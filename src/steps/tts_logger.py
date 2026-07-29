import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from .tts_cache import get_resource_path


LOG_DIR = get_resource_path(os.path.join("resources", "logs"))
os.makedirs(LOG_DIR, exist_ok=True)


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
