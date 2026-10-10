import os
import time
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from .tts_cache import get_resource_path


LOG_DIR = get_resource_path(os.path.join("resources", "logs"))
os.makedirs(LOG_DIR, exist_ok=True)

# 历史日志保留天数：日志按天命名，RotatingFileHandler 只能限制单日文件大小，
# 跨天的旧文件需要按保留期主动清理，否则会一直累积。
LOG_RETENTION_DAYS = 30


def purge_old_logs(days=LOG_RETENTION_DAYS, log_dir=LOG_DIR):
    """删除保留期之前的历史日志，返回删除数量。

    只处理本程序生成的 tts_*.log 与 crash_*.log，按修改时间判断；
    任何异常都不向外抛出，避免影响启动。
    """
    if days <= 0 or not os.path.isdir(log_dir):
        return 0
    removed = 0
    cutoff = time.time() - days * 86400
    names = []
    try:
        names = os.listdir(log_dir)
    except OSError:
        return 0
    for name in names:
        if not name.endswith(".log"):
            continue
        if not (name.startswith("tts_") or name.startswith("crash_")):
            continue
        path = os.path.join(log_dir, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.unlink(path)
                removed += 1
        except OSError:
            continue
    return removed


def setup_logger(name="tts_audio"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    console_handler.setFormatter(console_format)

    log_file = os.path.join(LOG_DIR, f"tts_{datetime.now().strftime('%Y%m%d')}.log")
    file_handler = RotatingFileHandler(log_file, encoding="utf-8", maxBytes=5*1024*1024, backupCount=3)
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter("%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(message)s")
    file_handler.setFormatter(file_format)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()
