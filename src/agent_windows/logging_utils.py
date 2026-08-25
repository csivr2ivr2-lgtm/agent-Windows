from __future__ import annotations

import logging
import re


_SECRET = re.compile(r"(?i)(authorization|api[_-]?key|token|secret)(\s*[:=]\s*)([^\s,;]+)")


def redact(value: object) -> str:
    return _SECRET.sub(r"\1\2[REDACTED]", str(value))


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

