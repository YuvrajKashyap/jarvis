import json
import logging
import re
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

_BEARER = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_NAMED_SECRET = re.compile(
    r"(?i)\b(authorization|token|secret|password|api[_-]?key)\s*([:=])\s*[^\s,;]+"
)


class _ManagedRotatingFileHandler(RotatingFileHandler):
    pass


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()[:16_000]
        redacted = _NAMED_SECRET.sub(r"\1\2[REDACTED]", message)
        redacted = _BEARER.sub("Bearer [REDACTED]", redacted)
        payload: dict[str, str] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.casefold(),
            "logger": record.name,
            "event": redacted,
        }
        if record.exc_info is not None:
            exception = self.formatException(record.exc_info)[:16_000]
            payload["exception"] = _BEARER.sub(
                "Bearer [REDACTED]",
                _NAMED_SECRET.sub(r"\1\2[REDACTED]", exception),
            )
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_local_logging(
    data_directory: Path,
    *,
    max_bytes: int = 2_000_000,
    backup_count: int = 5,
) -> Path:
    if max_bytes < 1_024 or max_bytes > 100_000_000:
        raise ValueError("log size must be between 1KB and 100MB")
    if backup_count < 1 or backup_count > 20:
        raise ValueError("log backup count must be between 1 and 20")
    log_directory = (data_directory / "logs").resolve()
    log_directory.mkdir(parents=True, exist_ok=True)
    path = log_directory / "jarvis.jsonl"
    root = logging.getLogger()
    for current in tuple(root.handlers):
        if isinstance(current, _ManagedRotatingFileHandler):
            root.removeHandler(current)
            current.close()
    handler = _ManagedRotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=False,
    )
    handler.setFormatter(RedactingJsonFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return path
