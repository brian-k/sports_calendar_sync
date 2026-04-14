# Family Sports Calendar Sync

This starter project syncs external iCal sports feeds into Google Calendar so each child can have one editable Google calendar that combines:
- sports events managed by the script
- normal family events added manually in Google Calendar

## What it does
- fetches one or more ICS feeds
- parses VEVENT items
- creates or updates matching Google Calendar events
- deletes missing feed events if enabled
- leaves manually created Google events alone
- tracks event mappings in SQLite

## Key design rule
The script tags every synced event with Google Calendar `extendedProperties.private`:
- `source = sports_sync`
- `child = <child key>`
- `feed_id = <feed key>`
- `ical_uid = <ICS UID>`

Only events with `source = sports_sync` are eligible for update or delete.

## Files
- `config.example.yaml` - sample config
- `sync_calendars.py` - main entry point
- `google_client.py` - Google Calendar auth/client
- `ics_parser.py` - ICS fetch and parse logic
- `event_mapper.py` - converts parsed ICS events into Google event payloads
- `storage.py` - SQLite mapping layer

## Setup
1. Copy `config.example.yaml` to `config.yaml`
2. Put your Google OAuth credentials JSON at the configured path
3. Put your OAuth token JSON at the configured path, or let the script create it on first local run
4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
5. Run one child first:
   ```bash
   python sync_calendars.py --config config.yaml --child luke
   ```

## Recommended first test
Use a single feed for Luke first.
Confirm these four behaviors:
1. new sports event is created
2. changed sports event updates
3. removed sports event deletes
4. manual event on Luke's Google calendar stays untouched

## Cron example
Run every 15 minutes:
```cron
*/15 * * * * /usr/bin/python3 /path/to/calendar-sync/sync_calendars.py --config /path/to/calendar-sync/config.yaml >> /path/to/calendar-sync/logs/cron.log 2>&1
```

## Notes
- This starter does not expand recurring ICS rules into separate events beyond what the `icalendar` library exposes from the feed. Many sports feeds already publish concrete VEVENT instances, which is usually fine.
- For very large feeds, you may later want to restrict by date window before creating or updating events.
- If your shared hosting blocks the local OAuth callback flow, generate the token on another machine first, then upload `token.json`.
