"""Console + file logging. Secrets are never passed to the logger."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

LOGGER_NAME = "job_mailer"
LOG_DIR = Path(__file__).resolve().parent / "logs"
PLAIN = {"plain": True}  # pass as `extra=` to print a line without the [LEVEL] prefix


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if getattr(record, "plain", False):
            return record.getMessage()
        return f"[{record.levelname}] {record.getMessage()}"


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def setup_logger(log_dir: Path = LOG_DIR) -> logging.Logger:
    logger = get_logger()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(ConsoleFormatter())
    logger.addHandler(console)

    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_dir / "mailer.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(file_handler)
    return logger
