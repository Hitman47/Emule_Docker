#!/bin/sh
# Called by aMule UserEvents/DownloadCompleted
# Args: %FILE %NAME %HASH %SIZE

FILE="$1"
NAME="$2"
HASH="$3"
SIZE="$4"
LOG="/var/log/amule-diag/completions.log"
TS=$(date '+%Y-%m-%d %H:%M:%S')

mkdir -p /var/log/amule-diag

log() { printf "[%s] %s\n" "$TS" "$1" >> "$LOG"; }

log "═══ DOWNLOAD COMPLETED EVENT ═══"
log "  Name: $NAME"
log "  Hash: $HASH"
log "  Size: $SIZE"
log "  File: $FILE"

# Check if the file actually exists
if [ -f "$FILE" ]; then
    ACTUAL_SIZE=$(stat -c%s "$FILE" 2>/dev/null || echo "?")
    log "  ✅ File EXISTS at: $FILE ($ACTUAL_SIZE bytes)"
else
    log "  🔴 File MISSING at: $FILE"
    # Search for it
    INCOMING="${INCOMING_DIR:-/incoming}"
    TEMP="${TEMP_DIR:-/temp}"
    log "  Searching in $INCOMING..."
    FOUND_INC=$(find "$INCOMING" -name "$NAME" -type f 2>/dev/null)
    if [ -n "$FOUND_INC" ]; then
        log "  Found in incoming: $FOUND_INC"
    else
        log "  NOT in incoming"
    fi
    log "  Searching in $TEMP..."
    FOUND_TMP=$(find "$TEMP" -name "*.part" -type f 2>/dev/null | head -5)
    if [ -n "$FOUND_TMP" ]; then
        log "  Part files in temp: $FOUND_TMP"
    else
        log "  NO part files in temp"
    fi
fi

# List incoming dir state
INCOMING="${INCOMING_DIR:-/incoming}"
log "  /incoming contents:"
ls -la "$INCOMING"/ 2>/dev/null | head -20 | while read -r line; do
    log "    $line"
done

# Check disk space
DF=$(df -h "$INCOMING" 2>/dev/null | tail -1)
log "  Disk: $DF"

log "═══ END COMPLETION EVENT ═══"
