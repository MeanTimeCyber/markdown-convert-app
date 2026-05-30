from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app import config


class JsonFormatter(logging.Formatter):
    """Render logs as JSON so logs are easy to parse and search."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field in (
            "request_id",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "client_ip",
            "event",
            "detail",
            "template_configured",
        ):
            if hasattr(record, field):
                payload[field] = getattr(record, field)

        return json.dumps(payload)


def configure_logging() -> logging.Logger:
    """Configure application logging with console and optional rotating file output."""
    logger = logging.getLogger("md_convert")
    logger.handlers.clear()
    logger.setLevel(config.LOG_LEVEL)
    logger.propagate = False

    formatter = JsonFormatter()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if config.LOG_TO_FILE:
        try:
            log_path = Path(config.LOG_FILE_PATH)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                filename=log_path,
                maxBytes=config.LOG_FILE_MAX_BYTES,
                backupCount=config.LOG_FILE_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as exc:
            logger.warning(
                "file_logging_disabled",
                extra={
                    "event": "file_logging_disabled",
                    "detail": f"Could not initialize file logging at {config.LOG_FILE_PATH}: {exc}",
                },
            )

    return logger