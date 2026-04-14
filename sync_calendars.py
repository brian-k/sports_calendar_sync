from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from googleapiclient.errors import HttpError

from event_mapper import SYNC_SOURCE, build_google_event
from google_client import get_calendar_service
from ics_parser import ParsedEvent, fetch_and_parse_ics
from storage import Storage

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)


class SyncEngine:
    def __init__(self, config: dict[str, Any]) -> None:
        google_cfg = config["google"]
        self.defaults = config.get("defaults", {})
        self.service = get_calendar_service(
            credentials_path=google_cfg["credentials_path"],
            token_path=google_cfg["token_path"],
        )
        db_path = self.defaults.get("db_path", "./data/sync.db")
        self.storage = Storage(db_path=db_path)
        self.calendars = config["calendars"]
        self.timeout_seconds = int(self.defaults.get("request_timeout_seconds", 30))
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
        logging.info("Syncing child=%s calendar=%s", child, calendar_id)

        for feed in child_cfg["feeds"]:
            try:
                self.sync_feed(child=child, calendar_id=calendar_id, feed=feed)
            except Exception as e:
                logging.exception("Feed sync failed child=%s feed_id=%s error=%s", child, feed.get("id"), e)  

    def sync_feed(self, child: str, calendar_id: str, feed: dict[str, Any]) -> None:
        feed_id = feed["id"]
        feed_name = feed.get("name", feed_id)
        title_prefix = feed.get("title_prefix") if self.defaults.get("event_title_prefix", True) else None
        logging.info("Fetching feed child=%s feed_id=%s url=%s", child, feed_id, feed["url"])
        parsed_events = fetch_and_parse_ics(feed["url"], timeout_seconds=self.timeout_seconds)
        logging.info("Parsed %s events for child=%s feed_id=%s", len(parsed_events), child, feed_id)

        seen_uids: set[str] = set()
        created = 0
        updated = 0
        skipped = 0
        deleted = 0
        now_str = datetime.now(timezone.utc).isoformat()

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

            if mapping is None:
                created_event = self.service.events().insert(
                    calendarId=calendar_id,
                    body=event_body,
                ).execute()
                self.storage.upsert_mapping(
                    child=child,
                    feed_id=feed_id,
                    ical_uid=parsed.ical_uid,
                    google_event_id=created_event["id"],
                    last_seen_hash=parsed.event_hash,
                    last_seen_at=now_str,
                )
                created += 1
                continue

            google_event_id = mapping["google_event_id"]
            if mapping["last_seen_hash"] == parsed.event_hash:
                self.storage.upsert_mapping(
                    child=child,
                    feed_id=feed_id,
                    ical_uid=parsed.ical_uid,
                    google_event_id=google_event_id,
                    last_seen_hash=parsed.event_hash,
                    last_seen_at=now_str,
                )
                skipped += 1
                continue

            existing = self.service.events().get(
                calendarId=calendar_id,
                eventId=google_event_id,
            ).execute()
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
                last_seen_hash=parsed.event_hash,
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
            "Finished child=%s feed_id=%s created=%s updated=%s deleted=%s skipped=%s",
            child,
            feed_id,
            created,
            updated,
            deleted,
            skipped,
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
