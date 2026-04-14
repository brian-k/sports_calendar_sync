from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from ics_parser import ParsedEvent

SYNC_SOURCE = "sports_sync"


def _format_google_datetime(value: date | datetime) -> dict[str, str]:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return {"dateTime": dt.isoformat()}
    return {"date": value.isoformat()}


def _build_description(feed_name: str, original_description: str) -> str:
    lines = []
    if original_description:
        lines.append(original_description)
    lines.append("")
    lines.append(f"Source feed: {feed_name}")
    return "\n".join(lines).strip()


def build_google_event(
    parsed: ParsedEvent,
    child: str,
    feed_id: str,
    feed_name: str,
    title_prefix: str | None,
) -> dict[str, Any]:
    summary = parsed.summary
    if title_prefix:
        summary = f"{title_prefix} {summary}".strip()

    event: dict[str, Any] = {
        "summary": summary,
        "description": _build_description(feed_name, parsed.description),
        "location": parsed.location,
        "status": "cancelled" if parsed.status == "cancelled" else "confirmed",
        "extendedProperties": {
            "private": {
                "source": SYNC_SOURCE,
                "child": child,
                "feed_id": feed_id,
                "ical_uid": parsed.ical_uid,
            }
        },
    }

    if parsed.all_day:
        end_date = parsed.end
        if isinstance(end_date, date) and not isinstance(end_date, datetime):
            if end_date <= parsed.start:
                end_date = parsed.start + timedelta(days=1)
        event["start"] = _format_google_datetime(parsed.start)
        event["end"] = _format_google_datetime(end_date)
    else:
        event["start"] = _format_google_datetime(parsed.start)
        event["end"] = _format_google_datetime(parsed.end)

    return event
