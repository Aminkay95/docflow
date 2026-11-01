from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# Attributes always present on a LogRecord; everything else is caller-supplied context.
_RESERVED = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}

# Content-bearing field names are dropped defensively. Logs never carry document text,
# page images, prompt payloads or raw file bytes.
_BLOCKED_KEYS = {
    "text",
    "page_text",
    "content",
    "body",
    "prompt",
    "response",
    "raw",
    "file_bytes",
    "image",
    "payload_body",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            if key in _BLOCKED_KEYS:
                payload[key] = "[redacted]"
                continue
            payload[key] = _coerce(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def _coerce(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if str(key) in _BLOCKED_KEYS else _coerce(item)
            for key, item in value.items()
        }
    return str(value)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
