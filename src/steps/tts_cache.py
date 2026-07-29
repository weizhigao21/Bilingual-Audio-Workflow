import os
import json
import sys
import sqlite3
import shutil
import hashlib
import threading


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
    def __init__(self, db_path=CACHE_DB_PATH):
        self.db_path = db_path
        os.makedirs(CACHE_DIR, exist_ok=True)
        self._conn = None
        self._lock = threading.Lock()
        self.init_database()

    def _get_conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        return self._conn

    def close(self):
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_name TEXT NOT NULL UNIQUE,
                file_paths TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

    def get_text_hash(self, text, model_name="", source_version=""):
        content = f"{text}|{model_name}|{source_version}"
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    def get_cached_audio(self, text, model_name="", source_version=""):
        text_hash = self.get_text_hash(text, model_name, source_version)
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT audio_path FROM audio_cache WHERE text_hash = ?", (text_hash,)
            )
            result = cursor.fetchone()

            if result:
                audio_path = result[0]
                if os.path.exists(audio_path):
                    cursor.execute(
                        "UPDATE audio_cache SET use_count = use_count + 1 WHERE text_hash = ?",
                        (text_hash,),
                    )
                    conn.commit()
                    return audio_path
                else:
                    cursor.execute(
                        "DELETE FROM audio_cache WHERE text_hash = ?", (text_hash,)
                    )
                    conn.commit()

        return None

    def get_cache_file_path(self, text_hash, ext=".wav"):
        return os.path.join(CACHE_DIR, f"{text_hash}{ext}")

    def save_audio_cache(self, text, audio_path, model_name="", api_url="", ext=".wav", source_version=""):
        text_hash = self.get_text_hash(text, model_name, source_version)
        cache_file = self.get_cache_file_path(text_hash, ext)

        try:
            shutil.copy2(audio_path, cache_file)
        except Exception:
            pass

        file_size = os.path.getsize(cache_file) if os.path.exists(cache_file) else 0

        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO audio_cache 
                (text_hash, text_content, audio_path, model_name, api_url, file_size)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (text_hash, text, cache_file, model_name, api_url, file_size),
            )
            conn.commit()

    def get_cache_stats(self):
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*), SUM(file_size), SUM(use_count) FROM audio_cache"
            )
            result = cursor.fetchone()

        return {
            "total_count": result[0] or 0,
            "total_size": result[1] or 0,
            "total_reuse": result[2] or 0,
        }

    def clear_cache(self):
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT audio_path FROM audio_cache")
            files_to_delete = [row[0] for row in cursor.fetchall()]
            cursor.execute("DELETE FROM audio_cache")
            deleted_count = len(files_to_delete)
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

    def clear_unused_cache(self):
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT audio_path FROM audio_cache WHERE use_count = 1")
            files_to_delete = [row[0] for row in cursor.fetchall()]

            cursor.execute("DELETE FROM audio_cache WHERE use_count = 1")
            deleted_count = len(files_to_delete)
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

    def get_cache_details(self):
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT text_content, model_name, use_count, file_size, created_at 
                FROM audio_cache 
                ORDER BY use_count DESC, created_at DESC
            """)
            results = cursor.fetchall()

        details = []
        for row in results:
            details.append(
                {
                    "text": row[0],
                    "model": row[1] or "未知",
                    "use_count": row[2] or 0,
                    "file_size": row[3] or 0,
                    "created_at": row[4] or "未知",
                }
            )
        return details

    def save_task_list(self, task_name, file_paths):
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            file_paths_json = json.dumps(file_paths, ensure_ascii=False)
            cursor.execute(
                """
                INSERT OR REPLACE INTO task_list (task_name, file_paths, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
                (task_name, file_paths_json),
            )
            conn.commit()

    def get_all_task_lists(self):
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT task_name, file_paths, created_at, updated_at FROM task_list ORDER BY updated_at DESC"
            )
            results = cursor.fetchall()

        tasks = []
        for row in results:
            tasks.append(
                {
                    "name": row[0],
                    "files": json.loads(row[1]),
                    "created_at": row[2],
                    "updated_at": row[3],
                }
            )
        return tasks

    def delete_task_list(self, task_name):
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM task_list WHERE task_name = ?", (task_name,))
            conn.commit()
