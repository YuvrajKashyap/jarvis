import json
import logging

from jarvis.platform.logging import RedactingJsonFormatter, configure_local_logging


def test_structured_formatter_redacts_credentials_and_bearer_tokens() -> None:
    record = logging.LogRecord(
        name="jarvis.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="authorization=top-secret Bearer abc.def token=hunter2",
        args=(),
        exc_info=None,
    )

    payload = json.loads(RedactingJsonFormatter().format(record))

    assert payload["event"] == "authorization=[REDACTED] Bearer [REDACTED] token=[REDACTED]"
    assert payload["level"] == "info"
    assert "top-secret" not in json.dumps(payload)
    assert "hunter2" not in json.dumps(payload)


def test_local_logging_rotates_inside_managed_data_directory(tmp_path) -> None:
    path = configure_local_logging(tmp_path, max_bytes=1_024, backup_count=2)
    logger = logging.getLogger("jarvis.test.local")

    logger.warning("model unavailable")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert path == (tmp_path / "logs" / "jarvis.jsonl").resolve()
    assert "model unavailable" in path.read_text(encoding="utf-8")
