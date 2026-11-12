import os
import sys
import sqlite3
import shutil
import hashlib
import threading
import tempfile
import time

from . import perf


def get_resource_path(relative_path):
    if getattr(sys, "frozen", False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    full_path = os.path.join(base_path, relative_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    return full_path


OUTPUT_DIR = get_resource_path(os.path.join("resources", "batch_tts_output"))
CACHE_DIR = get_resource_path(os.path.join("resources", "tts_audio_cache"))
CACHE_DB_PATH = get_resource_path(
    os.path.join("resources", "configs", "tts_audio_cache.db")
)


class AudioCache:
    """SQLite + 文件双缓存。

    性能约定（2026-11-05 P0-1）：
    - ``use_count`` 只是界面展示用的统计字段，命中路径**不做 UPDATE/commit**，
      改为内存计数 + 批量刷盘；实测 300 次逐条 commit 775ms → 攒批 2.8ms。
    - 文件 stat / 复制等磁盘 I/O 一律移出 ``self._lock``，避免 8 个合成线程
      在写缓存时被串行化；只有 DB 写入需要持锁。
    - ``save_audio_cache`` 的 INSERT 仍然**立即 commit**（这是数据，不能只靠
      ``close()`` 兜底）；``close()`` 额外负责把未刷盘的 use_count 落库。
    """

    _FLUSH_THRESHOLD = 64      # 累计命中多少次刷一次
    _FLUSH_INTERVAL = 5.0      # 或距上次刷盘超过多少秒

    def __init__(self, db_path=CACHE_DB_PATH):
        self.db_path = db_path
        os.makedirs(CACHE_DIR, exist_ok=True)
        self._conn = None
        self._lock = threading.Lock()
        self._pending_uses = {}       # text_hash -> 未落盘的命中次数
        self._pending_total = 0
        self._last_flush = time.monotonic()
        self.init_database()

    def _get_conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            # 只读文件系统或老库无法切 WAL 时降级为默认日志模式，不影响功能。
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.Error:
                pass
        return self._conn

    def _flush_pending_locked(self):
        """把内存里的 use_count 增量一次性写入；调用方必须持有 _lock。"""
        if not self._pending_uses or self._conn is None:
            return
        try:
            self._conn.executemany(
                "UPDATE audio_cache SET use_count = use_count + ? WHERE text_hash = ?",
                [(count, key) for key, count in self._pending_uses.items()],
            )
            self._conn.commit()
        except sqlite3.Error:
            # 纯统计字段，写失败宁可少算也不能让合成主流程失败。
            try:
                self._conn.rollback()
            except sqlite3.Error:
                pass
        finally:
            self._pending_uses.clear()
            self._pending_total = 0
            self._last_flush = time.monotonic()

    def close(self):
        with self._lock:
            self._flush_pending_locked()
            if self._conn:
                self._conn.close()
                self._conn = None

    def init_database(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audio_cache (
                text_hash TEXT PRIMARY KEY,
                text_content TEXT NOT NULL,
                audio_path TEXT NOT NULL,
                model_name TEXT,
                api_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                file_size INTEGER,
                use_count INTEGER DEFAULT 1
            )
        """)
        conn.commit()

    def get_text_hash(self, text, model_name="", source_version=""):
        content = f"{text}|{model_name}|{source_version}"
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    def _note_use(self, text_hash):
        """记录一次缓存命中；达到阈值或超时才批量刷盘。"""
        with self._lock:
            self._pending_uses[text_hash] = self._pending_uses.get(text_hash, 0) + 1
            self._pending_total += 1
            if (self._pending_total >= self._FLUSH_THRESHOLD
                    or time.monotonic() - self._last_flush >= self._FLUSH_INTERVAL):
                self._flush_pending_locked()

    def get_cached_audio(self, text, model_name="", source_version=""):
        text_hash = self.get_text_hash(text, model_name, source_version)
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT audio_path FROM audio_cache WHERE text_hash = ?", (text_hash,)
            ).fetchone()
        if not row:
            perf.COUNTERS.incr("cache_miss")
            return None

        audio_path = row[0]
        # 文件校验是磁盘 I/O，移出锁外，命中路径因此完全没有写事务。
        if os.path.isfile(audio_path) and os.path.getsize(audio_path) > 0:
            perf.COUNTERS.incr("cache_hit")
            self._note_use(text_hash)
            return audio_path

        with self._lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM audio_cache WHERE text_hash = ?", (text_hash,))
            conn.commit()
        perf.COUNTERS.incr("cache_miss")
        return None

    def get_cache_file_path(self, text_hash, ext=".wav"):
        return os.path.join(CACHE_DIR, f"{text_hash}{ext}")

    def save_audio_cache(self, text, audio_path, model_name="", api_url="", ext=".wav", source_version=""):
        text_hash = self.get_text_hash(text, model_name, source_version)
        cache_file = self.get_cache_file_path(text_hash, ext)
        if not os.path.isfile(audio_path) or os.path.getsize(audio_path) == 0:
            return False
        temp_path = None
        try:
            # 临时文件同目录 + os.replace 是原子的；文件复制放在锁外，
            # 并发写入不再互相等待整块磁盘 I/O。
            with tempfile.NamedTemporaryFile(dir=CACHE_DIR, prefix=".cache-",
                                             suffix=ext, delete=False) as tmp:
                temp_path = tmp.name
                with open(audio_path, "rb") as source:
                    shutil.copyfileobj(source, tmp)
            with self._lock:
                # Windows 上并发 os.replace 覆盖同一目标会抛 WinError 5，
                # 这个瞬时操作必须串行；耗时的文件复制已在锁外并行完成。
                os.replace(temp_path, cache_file)
                temp_path = None
                file_size = os.path.getsize(cache_file)
                conn = self._get_conn()
                conn.execute(
                    """INSERT INTO audio_cache
                    (text_hash, text_content, audio_path, model_name, api_url, file_size)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(text_hash) DO UPDATE SET
                    audio_path=excluded.audio_path, file_size=excluded.file_size,
                    model_name=excluded.model_name, api_url=excluded.api_url""",
                    (text_hash, text, cache_file, model_name, api_url, file_size),
                )
                # 这是缓存索引数据，必须立即落盘，不能等 close()。
                conn.commit()
            perf.COUNTERS.incr("cache_write")
            return True
        except Exception:
            return False
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def get_cache_stats(self):
        with self._lock:
            conn = self._get_conn()
            result = conn.execute(
                "SELECT COUNT(*), SUM(file_size), SUM(use_count) FROM audio_cache"
            ).fetchone()
            # 尚未刷盘的命中数也要算进「累计复用」，显示才不会倒退。
            pending = self._pending_total

        return {
            "total_count": result[0] or 0,
            "total_size": result[1] or 0,
            "total_reuse": (result[2] or 0) + pending,
        }

    def clear_cache(self):
        with self._lock:
            conn = self._get_conn()
            files_to_delete = [
                row[0] for row in conn.execute("SELECT audio_path FROM audio_cache")
            ]
            conn.execute("DELETE FROM audio_cache")
            deleted_count = len(files_to_delete)
            self._pending_uses.clear()
            self._pending_total = 0
            conn.commit()

        deleted_files = 0
        for file_path in files_to_delete:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    deleted_files += 1
                except Exception:
                    pass

        return deleted_count, deleted_files
