#!/usr/bin/env bash
# Push git-ignored secrets from this machine to the server over SSH.
#
# Code goes through git (push here, `git pull` on the server). This script
# handles the files git must never carry: config.yaml and
# data/service_account.json.
#
# Settings come from the environment or a git-ignored .env file (see
# .env.example):
#   DEPLOY_HOST  SSH host or alias, e.g. an entry from ~/.ssh/config
#   DEPLOY_DIR   absolute path of the project directory on the server
#
# Usage:
#   ./deploy_secrets.sh              copy config.yaml + service_account.json
#   ./deploy_secrets.sh --dry-run    show what would happen, change nothing
#   ./deploy_secrets.sh --with-db    ALSO overwrite the server's sync.db
#
# sync.db is NOT copied by default. The server's copy is live (cron updates it
# regularly) and maps feed events to Google event IDs; overwriting it with a
# stale copy can cause duplicate or orphaned events. --with-db backs up the
# remote DB first and asks for confirmation.

set -euo pipefail

LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -f "$LOCAL_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$LOCAL_DIR/.env"
  set +a
fi

: "${DEPLOY_HOST:?Set DEPLOY_HOST in the environment or .env (see .env.example)}"
: "${DEPLOY_DIR:?Set DEPLOY_DIR in the environment or .env (see .env.example)}"

DRY_RUN=0
WITH_DB=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --with-db) WITH_DB=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

FILES=("config.yaml" "data/service_account.json")
[[ $WITH_DB -eq 1 ]] && FILES+=("data/sync.db")

for f in "${FILES[@]}"; do
  [[ -f "$LOCAL_DIR/$f" ]] || { echo "Missing local file: $f" >&2; exit 1; }
done

run() {
  if [[ $DRY_RUN -eq 1 ]]; then echo "[dry-run] $*"; else "$@"; fi
}

if [[ $WITH_DB -eq 1 ]]; then
  echo "WARNING: this overwrites the server's live sync.db with your local copy."
  echo "Stop local syncing first, and make sure the server's cron is not mid-run."
  if [[ $DRY_RUN -eq 0 ]]; then
    read -r -p "Type 'overwrite' to continue: " answer
    [[ "$answer" == "overwrite" ]] || { echo "Aborted."; exit 1; }
    stamp="$(date +%Y%m%d-%H%M%S)"
    ssh "$DEPLOY_HOST" "cp -p '$DEPLOY_DIR/data/sync.db' '$DEPLOY_DIR/data/sync.db.bak-$stamp'"
    echo "Remote backup: data/sync.db.bak-$stamp"
  fi
fi

echo "Deploying to $DEPLOY_HOST:$DEPLOY_DIR"
run ssh "$DEPLOY_HOST" "mkdir -p '$DEPLOY_DIR/data' '$DEPLOY_DIR/logs' && chmod 700 '$DEPLOY_DIR/data'"

for f in "${FILES[@]}"; do
  run scp -q "$LOCAL_DIR/$f" "$DEPLOY_HOST:$DEPLOY_DIR/$f"
  run ssh "$DEPLOY_HOST" "chmod 600 '$DEPLOY_DIR/$f'"
  echo "  ok $f"
done

echo "Done. To deploy code: git push here, then on the server: cd \$DEPLOY_DIR && git pull"
