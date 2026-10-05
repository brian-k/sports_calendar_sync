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
   python sync_calendars.py --config config.yaml --child john
   ```

## Recommended first test
Use a single feed for John first.
Confirm these four behaviors:
1. new sports event is created
2. changed sports event updates
3. removed sports event deletes
4. manual event on John's Google calendar stays untouched

## Cron example
Run every 15 minutes:
```cron
*/15 * * * * /usr/bin/python3 /path/to/calendar-sync/sync_calendars.py --config /path/to/calendar-sync/config.yaml >> /path/to/calendar-sync/logs/cron.log 2>&1
```

## Reliability and alerts
- Feed fetches retry transient failures (timeouts, connection errors, 429, 5xx) with
  exponential backoff; `defaults.fetch_max_attempts` sets the total tries.
- Feed and Google URLs are redacted from logs (host kept, path and query removed).
- With a `notify` section in `config.yaml`, you get one email when a feed has failed
  `failure_threshold` runs in a row (default 6) and one when it recovers. Failure
  streaks are tracked in the SQLite DB. Without `notify`, the log gets a
  `FEED FAILING` error line instead.
- Past events are never deleted, even if their feed drops them or shuts down.

## Deploying to a server
Code goes through git; secrets go over SSH and never touch git.

1. Copy `.env.example` to `.env` (git-ignored) and set:
   - `DEPLOY_HOST` - SSH host or alias for the server
   - `DEPLOY_DIR` - absolute path of the project on the server
2. Push code, then pull it on the server:
   ```bash
   git push origin main
   ssh "$DEPLOY_HOST" "cd $DEPLOY_DIR && git pull"
   ```
3. Push the git-ignored secrets (`config.yaml`, `data/service_account.json`):
   ```bash
   ./deploy_secrets.sh --dry-run   # preview
   ./deploy_secrets.sh
   ```
   Files are set to mode 600. `sync.db` is not copied unless you pass
   `--with-db`, because the server's database is live; overwriting it with a
   stale copy can duplicate events.

## Notes
- This starter does not expand recurring ICS rules into separate events beyond what the `icalendar` library exposes from the feed. Many sports feeds already publish concrete VEVENT instances, which is usually fine.
- For very large feeds, you may later want to restrict by date window before creating or updating events.
- If your shared hosting blocks the local OAuth callback flow, generate the token on another machine first, then upload `token.json`.
