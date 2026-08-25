from __future__ import annotations

import logging
import re


_AUTHORIZATION = re.compile(r"(?i)\bauthorization\s*[:=]\s*[^\r\n]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET = re.compile(r"(?i)\b(api[_-]?key|token|secret)(\s*[:=]\s*)([^\s,;]+)")


def redact(value: object) -> str:
    redacted = _AUTHORIZATION.sub("Authorization: [REDACTED]", str(value))
    redacted = _BEARER.sub("Bearer [REDACTED]", redacted)
    return _SECRET.sub(r"\1\2[REDACTED]", redacted)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
