#!/bin/sh
# Watches /incoming and /temp for file events (create, move, delete)
# Runs as background daemon started from entrypoint

LOG="/var/log/amule-diag/file-events.log"
mkdir -p /var/log/amule-diag

INCOMING="${INCOMING_DIR:-/incoming}"
TEMP="${TEMP_DIR:-/temp}"

# Rotate if too large (>2MB)
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG" 2>/dev/null || echo 0)" -gt 2097152 ]; then
    mv "$LOG" "${LOG}.1"
fi

printf "[%s] File watcher started on %s and %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$INCOMING" "$TEMP" >> "$LOG"

# Watch for file create, move, delete, close_write events
inotifywait -m -r \
    --format '%T %w%f %e' \
    --timefmt '%Y-%m-%d %H:%M:%S' \
    -e create -e moved_to -e moved_from -e delete -e close_write \
    "$INCOMING" "$TEMP" 2>/dev/null >> "$LOG" &

WATCHER_PID=$!
echo "$WATCHER_PID" > /var/run/file-watcher.pid
wait "$WATCHER_PID"
