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

    def list_feed_mappings(self, child: str, feed_id: str) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM event_map WHERE child = ? AND feed_id = ?",
            (child, feed_id),
        )
        return list(cur.fetchall())
