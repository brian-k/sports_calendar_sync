from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

# Feed URLs often embed private tokens, and Google API URLs embed calendar IDs.
# Keep scheme and host (useful for debugging) and drop everything after.
_URL_RE = re.compile(r"(?:https?|webcal)://[^\s'\")>]+")
# requests reports the path+query without a scheme: "... with url: /path?token=..."
_WITH_URL_RE = re.compile(r"(with url:\s*)\S+")


def _redact_url(match: re.Match[str]) -> str:
    parsed = urlparse(match.group(0))
    return f"{parsed.scheme}://{parsed.netloc}/<redacted>"


def redact(text: str) -> str:
    text = _URL_RE.sub(_redact_url, text)
    return _WITH_URL_RE.sub(r"\1<redacted>", text)


class RedactingFormatter(logging.Formatter):
    """Redacts URLs from the final log text, including formatted tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))
