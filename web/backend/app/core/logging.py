"""Structured logging with a correlation id per request.

Every log line is one JSON object. The correlation id travels in a ContextVar,
so background work started from a request keeps the same id and an operator can
follow one action from the browser through a job to an SSH call.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.core.redaction import redact

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# Attributes LogRecord always carries; everything else a caller attached with
# `extra=` is treated as structured context.
_RESERVED = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {
    "message",
    "asctime",
    "taskName",
}


def set_correlation_id(value: str | None) -> None:
    _correlation_id.set(value)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        correlation_id = get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Redaction happens here rather than at every call site: one place to
        # get right, and it also covers third-party libraries that log for us.
        return json.dumps(redact(payload), default=str, ensure_ascii=False)


class PlainFormatter(logging.Formatter):
    """Readable output for development terminals."""

    def format(self, record: logging.LogRecord) -> str:
        correlation_id = get_correlation_id()
        prefix = f"[{correlation_id[:8]}] " if correlation_id else ""
        base = f"{record.levelname:<8} {prefix}{record.name}: {record.getMessage()}"
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


def configure_logging(*, debug: bool = False, json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else PlainFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    # uvicorn installs its own handlers; route them through ours so every line
    # carries the correlation id.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # asyncssh logs the full protocol handshake at DEBUG, which is noise here
    # and can echo key material.
    logging.getLogger("asyncssh").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
