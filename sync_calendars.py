from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from googleapiclient.errors import HttpError

from event_mapper import SYNC_SOURCE, build_google_event
from google_client import get_calendar_service
from ics_parser import ParsedEvent, fetch_and_parse_ics
from notify import send_email
from redaction import RedactingFormatter, redact
from storage import Storage

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    for handler in logging.getLogger().handlers:
        handler.setFormatter(RedactingFormatter(LOG_FORMAT))


def _http_status(exc: HttpError) -> int | None:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    return int(status) if status is not None else getattr(exc, "status_code", None)


def _error_summary(exc: Exception) -> str:
    return f"{type(exc).__name__}: {redact(str(exc))}"[:300]


def _is_past(google_event: dict[str, Any], now: datetime) -> bool:
    """True if the Google event started before `now` (all-day: before today, UTC)."""
    start = google_event.get("start", {})
    if "dateTime" in start:
        started = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return started < now
    if "date" in start:
        return date.fromisoformat(start["date"]) < now.date()
    return False


class SyncEngine:
    def __init__(self, config: dict[str, Any]) -> None:
        google_cfg = config["google"]
        self.defaults = config.get("defaults", {})
        self.service = get_calendar_service(
            credentials_path=google_cfg["credentials_path"],
            token_path=google_cfg.get("token_path"),
        )
        db_path = self.defaults.get("db_path", "./data/sync.db")
        self.storage = Storage(db_path=db_path)
        self.calendars = config["calendars"]
        self.timeout_seconds = int(self.defaults.get("request_timeout_seconds", 30))
        self.fetch_max_attempts = int(self.defaults.get("fetch_max_attempts", 3))
        self.notify_cfg = config.get("notify") or {}
        # Runs are ~30 min apart, so 6 consecutive failures is about 3 hours.
        self.failure_threshold = int(self.notify_cfg.get("failure_threshold", 6))
        self.delete_missing_events = bool(self.defaults.get("delete_missing_events", True))
        self.sync_past_days = int(self.defaults.get("sync_window_past_days", 30))
        self.sync_future_days = int(self.defaults.get("sync_window_future_days", 365))

    def sync_all(self) -> None:
        for child in self.calendars:
            self.sync_child(child)

    def sync_child(self, child: str) -> None:
        if child not in self.calendars:
            raise KeyError(f"Unknown child: {child}")

        child_cfg = self.calendars[child]
        calendar_id = child_cfg["google_calendar_id"]
        logging.info("Syncing child=%s", child)

        for feed in child_cfg["feeds"]:
            try:
                self.sync_feed(child=child, calendar_id=calendar_id, feed=feed)
            except Exception as e:
                logging.exception(
                    "Feed sync failed child=%s feed_id=%s error=%s", child, feed.get("id"), _error_summary(e)
                )
                self._record_failure(child, feed, e)
            else:
                self._record_success(child, feed)

    def _notify(self, subject: str, body: str) -> bool:
        """Email the configured recipient. Never raises; returns True if sent."""
        if not self.notify_cfg.get("email_to"):
            return False
        try:
            send_email(self.notify_cfg, subject, body)
            return True
        except Exception:
            logging.exception("Failed to send notification email")
            return False

    def _record_failure(self, child: str, feed: dict[str, Any], exc: Exception) -> None:
        feed_id = feed["id"]
        feed_name = feed.get("name", feed_id)
        status = self.storage.record_feed_failure(
            child, feed_id, _error_summary(exc), datetime.now(timezone.utc).isoformat()
        )
        failures = status["consecutive_failures"]
        if failures < self.failure_threshold or status["alerted"]:
            return
        logging.error("FEED FAILING child=%s feed_id=%s consecutive_failures=%s", child, feed_id, failures)
        sent = self._notify(
            subject=f"[sports-sync] Feed failing: {feed_name} ({child})",
            body=(
                f"Feed '{feed_name}' (child={child}, feed_id={feed_id}) has failed "
                f"{failures} runs in a row, since {status['first_failure_at']}.\n\n"
                f"Last error: {status['last_error']}\n\n"
                "If the feed was shut down for good, remove it from config.yaml. "
                "Events already on your calendar are kept."
            ),
        )
        if sent:
            self.storage.mark_feed_alerted(child, feed_id)

    def _record_success(self, child: str, feed: dict[str, Any]) -> None:
        feed_id = feed["id"]
        previous = self.storage.record_feed_success(child, feed_id)
        if previous is not None and previous["alerted"]:
            feed_name = feed.get("name", feed_id)
            self._notify(
                subject=f"[sports-sync] Feed recovered: {feed_name} ({child})",
                body=(
                    f"Feed '{feed_name}' (child={child}, feed_id={feed_id}) is syncing again "
                    f"after {previous['consecutive_failures']} failed runs."
                ),
            )

    def _create_event(
        self,
        child: str,
        feed_id: str,
        calendar_id: str,
        ical_uid: str,
        event_body: dict[str, Any],
        content_hash: str,
        now_str: str,
    ) -> str:
        """Insert the event in Google and record (or replace) its mapping. Returns the new event ID."""
        created_event = self.service.events().insert(
            calendarId=calendar_id,
            body=event_body,
        ).execute()
        self.storage.upsert_mapping(
            child=child,
            feed_id=feed_id,
            ical_uid=ical_uid,
            google_event_id=created_event["id"],
            last_seen_hash=content_hash,
            last_seen_at=now_str,
        )
        return created_event["id"]

    def sync_feed(self, child: str, calendar_id: str, feed: dict[str, Any]) -> None:
        feed_id = feed["id"]
        feed_name = feed.get("name", feed_id)
        title_prefix = feed.get("title_prefix") if self.defaults.get("event_title_prefix", True) else None
        logging.info("Fetching feed child=%s feed_id=%s", child, feed_id)
        parsed_events = fetch_and_parse_ics(
            feed["url"],
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.fetch_max_attempts,
        )
        logging.info("Parsed %s events for child=%s feed_id=%s", len(parsed_events), child, feed_id)

        seen_uids: set[str] = set()
        created = 0
        updated = 0
        skipped = 0
        deleted = 0
        kept_past = 0
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()

        for parsed in parsed_events:
            seen_uids.add(parsed.ical_uid)
            mapping = self.storage.get_mapping(child=child, feed_id=feed_id, ical_uid=parsed.ical_uid)
            event_body = build_google_event(
                parsed=parsed,
                child=child,
                feed_id=feed_id,
                feed_name=feed_name,
                title_prefix=title_prefix,
            )
            # Hash the event as it will appear in Google so mapping changes
            # (e.g. title_prefix) trigger updates, not just feed changes.
            content_hash = hashlib.sha256(
                json.dumps(event_body, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()

            if mapping is None:
                self._create_event(child, feed_id, calendar_id, parsed.ical_uid, event_body, content_hash, now_str)
                created += 1
                continue

            google_event_id = mapping["google_event_id"]
            if mapping["last_seen_hash"] == content_hash:
                self.storage.upsert_mapping(
                    child=child,
                    feed_id=feed_id,
                    ical_uid=parsed.ical_uid,
                    google_event_id=google_event_id,
                    last_seen_hash=content_hash,
                    last_seen_at=now_str,
                )
                skipped += 1
                continue

            try:
                existing = self.service.events().get(
                    calendarId=calendar_id,
                    eventId=google_event_id,
                ).execute()
            except HttpError as exc:
                if _http_status(exc) not in (404, 410):
                    raise
                # The event is gone from Google (e.g. deleted by hand) but still
                # mapped here. Recreate it so the calendar keeps mirroring the feed.
                new_id = self._create_event(
                    child, feed_id, calendar_id, parsed.ical_uid, event_body, content_hash, now_str
                )
                logging.info(
                    "Recreated event missing from Google child=%s feed_id=%s old_event_id=%s new_event_id=%s",
                    child,
                    feed_id,
                    google_event_id,
                    new_id,
                )
                created += 1
                continue
            private_props = existing.get("extendedProperties", {}).get("private", {})
            if private_props.get("source") != SYNC_SOURCE:
                logging.warning(
                    "Skipping update because event ownership mismatch child=%s feed_id=%s event_id=%s",
                    child,
                    feed_id,
                    google_event_id,
                )
                skipped += 1
                continue

            event_body["id"] = google_event_id
            self.service.events().update(
                calendarId=calendar_id,
                eventId=google_event_id,
                body=event_body,
            ).execute()
            self.storage.upsert_mapping(
                child=child,
                feed_id=feed_id,
                ical_uid=parsed.ical_uid,
                google_event_id=google_event_id,
                last_seen_hash=content_hash,
                last_seen_at=now_str,
            )
            updated += 1

        if self.delete_missing_events:
            for mapping in self.storage.list_feed_mappings(child=child, feed_id=feed_id):
                if mapping["ical_uid"] in seen_uids:
                    continue
                google_event_id = mapping["google_event_id"]
                try:
                    existing = self.service.events().get(
                        calendarId=calendar_id,
                        eventId=google_event_id,
                    ).execute()
                    private_props = existing.get("extendedProperties", {}).get("private", {})
                    if private_props.get("source") == SYNC_SOURCE and _is_past(existing, now):
                        # Keep history: feeds often drop past games or shut down.
                        # The mapping is kept so a restored feed updates in place.
                        kept_past += 1
                        continue
                    if private_props.get("source") == SYNC_SOURCE:
                        self.service.events().delete(
                            calendarId=calendar_id,
                            eventId=google_event_id,
                        ).execute()
                    else:
                        logging.warning(
                            "Skipping delete because event ownership mismatch child=%s feed_id=%s event_id=%s",
                            child,
                            feed_id,
                            google_event_id,
                        )
                except HttpError as exc:
                    if getattr(exc, "status_code", None) != 404:
                        raise
                self.storage.delete_mapping(child=child, feed_id=feed_id, ical_uid=mapping["ical_uid"])
                deleted += 1

        logging.info(
            "Finished child=%s feed_id=%s created=%s updated=%s deleted=%s skipped=%s kept_past=%s",
            child,
            feed_id,
            created,
            updated,
            deleted,
            skipped,
            kept_past,
        )


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync sports iCal feeds into Google Calendars")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    parser.add_argument("--child", help="Sync only one child key from config")
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    config = load_config(args.config)
    engine = SyncEngine(config)
    if args.child:
        engine.sync_child(args.child)
    else:
        engine.sync_all()


if __name__ == "__main__":
    main()
