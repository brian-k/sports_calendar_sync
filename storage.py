from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS event_map (
    child TEXT NOT NULL,
    feed_id TEXT NOT NULL,
    ical_uid TEXT NOT NULL,
    google_event_id TEXT NOT NULL,
    last_seen_hash TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (child, feed_id, ical_uid)
);

CREATE INDEX IF NOT EXISTS idx_event_map_google_event_id
    ON event_map (google_event_id);

CREATE TABLE IF NOT EXISTS feed_status (
    child TEXT NOT NULL,
    feed_id TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    first_failure_at TEXT,
    last_error TEXT,
    alerted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (child, feed_id)
);
"""


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def get_mapping(self, child: str, feed_id: str, ical_uid: str) -> Optional[sqlite3.Row]:
        cur = self.conn.execute(
            """
            SELECT * FROM event_map
            WHERE child = ? AND feed_id = ? AND ical_uid = ?
            """,
            (child, feed_id, ical_uid),
        )
        return cur.fetchone()

    def upsert_mapping(
        self,
        child: str,
        feed_id: str,
        ical_uid: str,
        google_event_id: str,
        last_seen_hash: str,
        last_seen_at: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO event_map (
                child, feed_id, ical_uid, google_event_id, last_seen_hash, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(child, feed_id, ical_uid) DO UPDATE SET
                google_event_id = excluded.google_event_id,
                last_seen_hash = excluded.last_seen_hash,
                last_seen_at = excluded.last_seen_at
            """,
            (child, feed_id, ical_uid, google_event_id, last_seen_hash, last_seen_at),
        )
        self.conn.commit()

    def delete_mapping(self, child: str, feed_id: str, ical_uid: str) -> None:
        self.conn.execute(
            "DELETE FROM event_map WHERE child = ? AND feed_id = ? AND ical_uid = ?",
            (child, feed_id, ical_uid),
        )
        self.conn.commit()

    def _get_feed_status(self, child: str, feed_id: str) -> Optional[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM feed_status WHERE child = ? AND feed_id = ?",
            (child, feed_id),
        )
        return cur.fetchone()

    def record_feed_failure(self, child: str, feed_id: str, error: str, now_str: str) -> sqlite3.Row:
        """Count one more consecutive failure and return the updated status row."""
        prev = self._get_feed_status(child, feed_id)
        streak = prev["consecutive_failures"] if prev else 0
        first_failure_at = prev["first_failure_at"] if streak else now_str
        self.conn.execute(
            """
            INSERT INTO feed_status (child, feed_id, consecutive_failures, first_failure_at, last_error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(child, feed_id) DO UPDATE SET
                consecutive_failures = excluded.consecutive_failures,
                first_failure_at = excluded.first_failure_at,
                last_error = excluded.last_error
            """,
            (child, feed_id, streak + 1, first_failure_at, error),
        )
        self.conn.commit()
        return self._get_feed_status(child, feed_id)

    def record_feed_success(self, child: str, feed_id: str) -> Optional[sqlite3.Row]:
        """Reset the failure streak. Returns the prior status if the feed was failing."""
        prev = self._get_feed_status(child, feed_id)
        if prev is None or prev["consecutive_failures"] == 0:
            return None
        self.conn.execute(
            """
            UPDATE feed_status
            SET consecutive_failures = 0, first_failure_at = NULL, last_error = NULL, alerted = 0
            WHERE child = ? AND feed_id = ?
            """,
            (child, feed_id),
        )
        self.conn.commit()
        return prev

    def mark_feed_alerted(self, child: str, feed_id: str) -> None:
        self.conn.execute(
            "UPDATE feed_status SET alerted = 1 WHERE child = ? AND feed_id = ?",
            (child, feed_id),
        )
        self.conn.commit()

    def list_feed_mappings(self, child: str, feed_id: str) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM event_map WHERE child = ? AND feed_id = ?",
            (child, feed_id),
        )
        return list(cur.fetchall())
