#!/usr/bin/env bash
set -euo pipefail

ROOT="/data0/hewenwen/Werewolf"
LOGS="$ROOT/logs"
WEB="$ROOT/web"
STATE_FILE="$ROOT/.monitor_state"

snapshot_hash() {
  if [ ! -d "$LOGS" ]; then
    echo "missing_logs"
    return
  fi
  find "$LOGS" -type f -print0 2>/dev/null \
    | sort -z \
    | xargs -0 stat --printf '%n|%s|%Y\n' 2>/dev/null \
    | sha256sum \
    | awk '{print $1}'
}

while true; do
  new_hash=$(snapshot_hash)
  old_hash=""
  if [ -f "$STATE_FILE" ]; then
    old_hash=$(cat "$STATE_FILE" || true)
  fi

  if [ "$new_hash" != "$old_hash" ]; then
    echo "$new_hash" > "$STATE_FILE"

    "$WEB/update_logs.sh"

    cd "$ROOT"
    git add web

    if ! git diff --cached --quiet; then
      ts=$(date "+%Y-%m-%d %H:%M:%S")
      git commit -m "Update web logs ${ts}"
      git push
    fi
  fi

  sleep 60
done
