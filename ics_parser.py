from __future__ import annotations

import hashlib
import logging
import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from urllib.parse import urlparse
import requests
from dateutil import tz
from icalendar import Calendar


@dataclass
class ParsedEvent:
    ical_uid: str
    summary: str
    description: str
    location: str
    start: date | datetime
    end: date | datetime
    all_day: bool
    status: str
    event_hash: str


def _to_native(value: Any) -> date | datetime:
    if hasattr(value, "dt"):
        value = value.dt
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, date):
        return value
    raise TypeError(f"Unsupported date type: {type(value)!r}")


def _hash_event(parts: list[str]) -> str:
    joined = "\n".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def normalize_calendar_urls(url: str):
    parsed = urlparse(url)

    if parsed.scheme == "webcal":
        base = url[len("webcal://"):]
        return [
            f"https://{base}",
            f"http://{base}",
        ]

    return [url]


RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = 60


def _is_retryable(exc: Exception) -> bool:
    """Transient failures only; a 404 or a malformed calendar will not fix itself."""
    if isinstance(exc, requests.exceptions.HTTPError):
        return exc.response is not None and exc.response.status_code in RETRYABLE_STATUS
    # requests' ConnectionError/Timeout are OSErrors too; raw socket errors can leak through.
    return isinstance(exc, OSError)


def _describe(exc: Exception) -> str:
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def _backoff_delay(attempt: int, base_seconds: float, exc: Exception) -> float:
    """Exponential delay with jitter; honors a numeric Retry-After header."""
    delay = base_seconds * (2 ** (attempt - 1))
    delay += random.uniform(0, delay * 0.25)
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        retry_after = exc.response.headers.get("Retry-After", "")
        if retry_after.isdigit():
            delay = max(delay, float(retry_after))
    return min(delay, MAX_BACKOFF_SECONDS)


def _download_calendar(url: str, timeout_seconds: int) -> Calendar:
    last_exception = None
    for candidate in normalize_calendar_urls(url):
        try:
            response = requests.get(candidate, timeout=timeout_seconds)
            response.raise_for_status()
            return Calendar.from_ical(response.content)
        except Exception as e:
            last_exception = e
    raise last_exception


def fetch_and_parse_ics(
    url: str,
    timeout_seconds: int = 30,
    max_attempts: int = 3,
    backoff_base_seconds: float = 2.0,
) -> list[ParsedEvent]:
    for attempt in range(1, max_attempts + 1):
        try:
            calendar = _download_calendar(url, timeout_seconds)
            break
        except Exception as e:
            if attempt == max_attempts or not _is_retryable(e):
                raise
            delay = _backoff_delay(attempt, backoff_base_seconds, e)
            logging.warning(
                "Feed fetch failed (%s); retry %s/%s in %.0fs",
                _describe(e),
                attempt,
                max_attempts - 1,
                delay,
            )
            time.sleep(delay)

    events: list[ParsedEvent] = []
    for component in calendar.walk():
        if component.name != "VEVENT":
            continue

        uid = str(component.get("uid", "")).strip()
        if not uid:
            continue

        summary = str(component.get("summary", "")).strip()
        description = str(component.get("description", "")).strip()
        location = str(component.get("location", "")).strip()
        status = str(component.get("status", "confirmed")).strip().lower() or "confirmed"

        dtstart = _to_native(component.decoded("dtstart"))
        dtend_raw = component.decoded("dtend") if component.get("dtend") else None
        if dtend_raw is None:
            dtend = dtstart
        else:
            dtend = _to_native(dtend_raw)

        all_day = isinstance(dtstart, date) and not isinstance(dtstart, datetime)

        event_hash = _hash_event(
            [
                uid,
                summary,
                description,
                location,
                status,
                dtstart.isoformat(),
                dtend.isoformat(),
                str(all_day),
            ]
        )

        events.append(
            ParsedEvent(
                ical_uid=uid,
                summary=summary,
                description=description,
                location=location,
                start=dtstart,
                end=dtend,
                all_day=all_day,
                status=status,
                event_hash=event_hash,
            )
        )

    return events
