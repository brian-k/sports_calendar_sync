from __future__ import annotations

import hashlib
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


def fetch_and_parse_ics(url: str, timeout_seconds: int = 30) -> list[ParsedEvent]:
    candidate_urls = normalize_calendar_urls(url)

    last_exception = None

    for candidate in candidate_urls:
        try:
            response = requests.get(candidate, timeout=timeout_seconds)
            response.raise_for_status()
            calendar = Calendar.from_ical(response.content)
            break
        except Exception as e:
            last_exception = e
    else:
        raise last_exception

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
