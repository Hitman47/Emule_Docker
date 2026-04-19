#!/bin/sh
# ╔══════════════════════════════════════════════════════════╗
# ║  Source Boost — Low ID Download Optimizer                 ║
# ║  Maximizes download chances without port forwarding       ║
# ║                                                           ║
# ║  Phase 1: Refresh stalled — pause/resume "waiting" DLs   ║
# ║  Phase 2: Server rotation — new ED2K server = new peers   ║
# ║  Phase 3: Kad search — trigger DHT lookup per file        ║
# ║  Phase 4: Smart focus — pause 0-source, boost active      ║
# ║  Phase 5: Kad health — reconnect if needed                ║
# ╚══════════════════════════════════════════════════════════╝

EC_HOST="${AMULE_EC_HOST:-localhost}"
EC_PORT="${AMULE_EC_PORT:-4712}"
EC_PASSWORD="${AMULE_EC_PASSWORD:-}"
EC_PASSWORD_HASH="${AMULE_EC_PASSWORD_HASH:-}"
AMULE_HOME="${AMULE_HOME:-/home/amule/.aMule}"
LOG_PREFIX="[SRC-BOOST]"
LOG_FILE="/var/log/amule-diag/source-boost.log"
STATE_DIR="${AMULE_HOME}/.source-boost"
SOURCE_BOOST_AUTO_PAUSE_ENABLED="${SOURCE_BOOST_AUTO_PAUSE_ENABLED:-false}"
SOURCE_BOOST_ZERO_SRC_TIMEOUT="${SOURCE_BOOST_ZERO_SRC_TIMEOUT:-3600}"

mkdir -p /var/log/amule-diag "$STATE_DIR"

is_true() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

log() {
    MSG="$(date '+%Y-%m-%d %H:%M:%S') $LOG_PREFIX $1"
    printf "%s\n" "$MSG" >> "$LOG_FILE"
    printf "%s\n" "$MSG"
}

# Load credentials
CRED_FILE="${AMULE_HOME}/.ec_credentials"
[ -f "$CRED_FILE" ] && . "$CRED_FILE"

amule_cmd() {
    OUT=$(amulecmd -h "$EC_HOST" -p "$EC_PORT" -P "$EC_PASSWORD" -c "$1" 2>&1)
    if echo "$OUT" | grep -qi "wrong password\|Authentication failed"; then
        [ -n "$EC_PASSWORD_HASH" ] && OUT=$(amulecmd -h "$EC_HOST" -p "$EC_PORT" -P "$EC_PASSWORD_HASH" -c "$1" 2>&1)
    fi
    echo "$OUT"
}

# Check amuled
pgrep -x amuled >/dev/null 2>&1 || { log "amuled not running, skip"; exit 0; }

log "=== Source Boost START ==="

# ── Get current state ──
DL_RAW=$(amule_cmd "show dl")
STATUS_RAW=$(amule_cmd "status")

# Parse downloads into temp file: HASH|NAME|PROGRESS|SOURCES|STATUS
PARSE_FILE=$(mktemp)
CURRENT_HASH=""
CURRENT_NAME=""

echo "$DL_RAW" | while IFS= read -r line; do
    STRIPPED=$(echo "$line" | sed 's/^[> ]*//')
    # Hash line: 32 hex + filename
    HASH=$(echo "$STRIPPED" | grep -oE '^[0-9A-Fa-f]{32}')
    if [ -n "$HASH" ]; then
        # Flush previous
        if [ -n "$CURRENT_HASH" ]; then
            echo "${CURRENT_HASH}|${CURRENT_NAME}|${CURRENT_PCT}|${CURRENT_SRC}|${CURRENT_STATUS}" >> "$PARSE_FILE"
        fi
        CURRENT_HASH="$HASH"
        CURRENT_NAME=$(echo "$STRIPPED" | sed "s/^${HASH}[[:space:]]*//" | head -c 120)
        CURRENT_PCT="0"
        CURRENT_SRC="0"
        CURRENT_STATUS="unknown"
        continue
    fi
    [ -z "$CURRENT_HASH" ] && continue
    LOW=$(echo "$STRIPPED" | tr '[:upper:]' '[:lower:]')
    # Extract progress
    PCT=$(echo "$STRIPPED" | grep -oE '\[?[0-9.,]+%' | head -1 | tr -d '[%' | tr ',' '.')
    [ -n "$PCT" ] && CURRENT_PCT="$PCT"
    # Extract sources (N/ N pattern without unit = sources)
    SRC=$(echo "$STRIPPED" | grep -oE '[0-9]+/[[:space:]]*[0-9]+' | head -1)
    if [ -n "$SRC" ]; then
        # Only count as sources if no MB/GB unit follows
        HAS_UNIT=$(echo "$STRIPPED" | grep -oE '[0-9]+/[[:space:]]*[0-9]+[[:space:]]*[KMGTkmgt]')
        if [ -z "$HAS_UNIT" ]; then
            CURRENT_SRC=$(echo "$SRC" | cut -d'/' -f2 | tr -d ' ')
        fi
    fi
    # Extract status
    case "$LOW" in
        *downloading*|*/s*) CURRENT_STATUS="downloading" ;;
        *waiting*) CURRENT_STATUS="waiting" ;;
        *paused*|*stopped*) CURRENT_STATUS="paused" ;;
        *getting\ source*) CURRENT_STATUS="getting_sources" ;;
        *connecting*) CURRENT_STATUS="connecting" ;;
        *complet*) CURRENT_STATUS="complete" ;;
        *error*|*failed*) CURRENT_STATUS="error" ;;
    esac
done
# Flush last
if [ -n "$CURRENT_HASH" ]; then
    echo "${CURRENT_HASH}|${CURRENT_NAME}|${CURRENT_PCT}|${CURRENT_SRC}|${CURRENT_STATUS}" >> "$PARSE_FILE"
fi

TOTAL_DL=$(wc -l < "$PARSE_FILE" 2>/dev/null || echo 0)
if [ "$TOTAL_DL" -eq 0 ]; then
    log "No active downloads, nothing to boost"
    rm -f "$PARSE_FILE"
    log "=== Source Boost END ==="
    exit 0
fi

log "Found $TOTAL_DL download(s)"

# Count categories
STALLED_COUNT=0
ZERO_SRC_COUNT=0
ACTIVE_COUNT=0
while IFS='|' read -r hash name pct src status; do
    case "$status" in
        waiting|getting_sources|connecting) 
            if [ "${src:-0}" -gt 0 ]; then
                STALLED_COUNT=$((STALLED_COUNT + 1))
            else
                ZERO_SRC_COUNT=$((ZERO_SRC_COUNT + 1))
            fi
            ;;
        downloading) ACTIVE_COUNT=$((ACTIVE_COUNT + 1)) ;;
    esac
done < "$PARSE_FILE"

log "Status: $ACTIVE_COUNT active, $STALLED_COUNT stalled (with sources), $ZERO_SRC_COUNT zero-source"


# ═══════════════════════════════════════
# PHASE 1: Refresh stalled downloads
# Pause/resume forces aMule to re-request
# source slots from connected clients
# ═══════════════════════════════════════
CYCLE_MARKER="${STATE_DIR}/last-cycle"
CYCLE_INTERVAL=1800  # 30 min

DO_CYCLE=0
if [ ! -f "$CYCLE_MARKER" ]; then
    DO_CYCLE=1
else
    LAST_CYCLE=$(stat -c %Y "$CYCLE_MARKER" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    [ $((NOW - LAST_CYCLE)) -gt $CYCLE_INTERVAL ] && DO_CYCLE=1
fi

if [ "$DO_CYCLE" -eq 1 ] && [ "$STALLED_COUNT" -gt 0 ]; then
    log "Phase 1: Pause/Resume cycle on $STALLED_COUNT stalled download(s)..."
    CYCLED=0
    while IFS='|' read -r hash name pct src status; do
        case "$status" in
            waiting|getting_sources|connecting)
                [ "${src:-0}" -gt 0 ] || continue
                amule_cmd "pause $hash" >/dev/null 2>&1
                CYCLED=$((CYCLED + 1))
                ;;
        esac
    done < "$PARSE_FILE"
    sleep 3
    while IFS='|' read -r hash name pct src status; do
        case "$status" in
            waiting|getting_sources|connecting)
                [ "${src:-0}" -gt 0 ] || continue
                amule_cmd "resume $hash" >/dev/null 2>&1
                ;;
        esac
    done < "$PARSE_FILE"
    touch "$CYCLE_MARKER"
    log "Phase 1: Cycled $CYCLED download(s)"
else
    log "Phase 1: Skip (not due or no stalled DLs)"
fi


# ═══════════════════════════════════════
# PHASE 2: Server rotation
# Each ED2K server has a different pool
# of clients — rotating = more visibility
# ═══════════════════════════════════════
ROTATION_MARKER="${STATE_DIR}/last-rotation"
ROTATION_INTERVAL=3600  # 60 min

DO_ROTATION=0
if [ ! -f "$ROTATION_MARKER" ]; then
    DO_ROTATION=1
else
    LAST_ROT=$(stat -c %Y "$ROTATION_MARKER" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    [ $((NOW - LAST_ROT)) -gt $ROTATION_INTERVAL ] && DO_ROTATION=1
fi

if [ "$DO_ROTATION" -eq 1 ]; then
    log "Phase 2: Server rotation..."
    SERVERS_RAW=$(amule_cmd "show servers")
    CURRENT_ADDR=$(echo "$STATUS_RAW" | grep -i "ed2k.*connected" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]+' | head -1)
    ALL_ADDRS=$(echo "$SERVERS_RAW" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]+' | sort -u)

    # Pick one we haven't used recently
    LAST_SERVERS_FILE="${STATE_DIR}/recent-servers"
    touch "$LAST_SERVERS_FILE"
    RECENT=$(cat "$LAST_SERVERS_FILE" 2>/dev/null)

    NEXT_SERVER=""
    for SRV in $ALL_ADDRS; do
        [ "$SRV" = "$CURRENT_ADDR" ] && continue
        # Skip if used recently
        echo "$RECENT" | grep -qF "$SRV" && continue
        NEXT_SERVER="$SRV"
        break
    done

    # If all servers used recently, clear history and pick first non-current
    if [ -z "$NEXT_SERVER" ]; then
        : > "$LAST_SERVERS_FILE"
        for SRV in $ALL_ADDRS; do
            [ "$SRV" = "$CURRENT_ADDR" ] && continue
            NEXT_SERVER="$SRV"
            break
        done
    fi

    if [ -n "$NEXT_SERVER" ]; then
        log "Phase 2: Rotating $CURRENT_ADDR → $NEXT_SERVER"
        amule_cmd "connect $NEXT_SERVER" >/dev/null 2>&1
        echo "$NEXT_SERVER" >> "$LAST_SERVERS_FILE"
        # Keep only last 5 entries
        tail -5 "$LAST_SERVERS_FILE" > "${LAST_SERVERS_FILE}.tmp" && mv "${LAST_SERVERS_FILE}.tmp" "$LAST_SERVERS_FILE"
        touch "$ROTATION_MARKER"
        sleep 5
        NEW_STATUS=$(amule_cmd "status")
        if echo "$NEW_STATUS" | grep -qi "ed2k.*connected to"; then
            log "Phase 2: Connected to new server OK"
        else
            log "Phase 2: New server failed, reconnecting default..."
            amule_cmd "connect ed2k" >/dev/null 2>&1
        fi
    else
        log "Phase 2: No alternate server available"
    fi
else
    log "Phase 2: Skip (not due yet)"
fi


# ═══════════════════════════════════════
# PHASE 3: Kad search for stalled files
# Searching Kad triggers DHT lookups that
# help discover new sources for the file
# ═══════════════════════════════════════
KAD_MARKER="${STATE_DIR}/last-kad-search"
KAD_INTERVAL=900  # 15 min

DO_KAD=0
if [ ! -f "$KAD_MARKER" ]; then
    DO_KAD=1
else
    LAST_KAD=$(stat -c %Y "$KAD_MARKER" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    [ $((NOW - LAST_KAD)) -gt $KAD_INTERVAL ] && DO_KAD=1
fi

if [ "$DO_KAD" -eq 1 ] && [ $((STALLED_COUNT + ZERO_SRC_COUNT)) -gt 0 ]; then
    log "Phase 3: Kad source search for stalled downloads..."
    SEARCHED=0
    while IFS='|' read -r hash name pct src status; do
        case "$status" in
            waiting|getting_sources|connecting|unknown)
                # Extract first 3-4 meaningful words from filename for Kad search
                CLEAN_NAME=$(echo "$name" | sed 's/\.[a-zA-Z0-9]*$//' | sed 's/[_.\-\[\](){}]/ /g' | sed 's/  */ /g')
                KEYWORDS=$(echo "$CLEAN_NAME" | awk '{for(i=1;i<=4&&i<=NF;i++) printf "%s ", $i}' | sed 's/ *$//')
                if [ -n "$KEYWORDS" ] && [ ${#KEYWORDS} -gt 3 ]; then
                    amule_cmd "search kad $KEYWORDS" >/dev/null 2>&1
                    SEARCHED=$((SEARCHED + 1))
                    sleep 2  # Don't flood Kad
                fi
                ;;
        esac
        [ "$SEARCHED" -ge 5 ] && break  # Max 5 searches per run
    done < "$PARSE_FILE"
    touch "$KAD_MARKER"
    log "Phase 3: Triggered $SEARCHED Kad search(es)"
else
    log "Phase 3: Skip (not due or no stalled DLs)"
fi


# ═══════════════════════════════════════
# PHASE 4: Smart focus
# Auto-pause downloads with 0 sources only
# if explicitly enabled. Default = OFF,
# because this is too aggressive for eD2k.
# ═══════════════════════════════════════
ZERO_SRC_TIMEOUT="${SOURCE_BOOST_ZERO_SRC_TIMEOUT}"

PAUSED_BY_BOOST=0
RESUMED_BY_BOOST=0
if is_true "$SOURCE_BOOST_AUTO_PAUSE_ENABLED"; then
    while IFS='|' read -r hash name pct src status; do
        TRACKER="${STATE_DIR}/zero-src-${hash}"
        case "$status" in
            waiting|getting_sources|connecting|unknown)
                if [ "${src:-0}" -eq 0 ]; then
                    # Track how long this has had 0 sources
                    if [ ! -f "$TRACKER" ]; then
                        date +%s > "$TRACKER"
                    else
                        FIRST_SEEN=$(cat "$TRACKER" 2>/dev/null || echo 0)
                        NOW=$(date +%s)
                        ELAPSED=$((NOW - FIRST_SEEN))
                        if [ "$ELAPSED" -gt "$ZERO_SRC_TIMEOUT" ]; then
                            log "Phase 4: Auto-pause $hash (0 sources for ${ELAPSED}s) — $(echo "$name" | head -c 50)"
                            amule_cmd "pause $hash" >/dev/null 2>&1
                            PAUSED_BY_BOOST=$((PAUSED_BY_BOOST + 1))
                        fi
                    fi
                else
                    # Has sources now — clear tracker
                    rm -f "$TRACKER"
                fi
                ;;
            paused)
                # If this was paused by us and now has sources, resume it
                if [ -f "$TRACKER" ] && [ "${src:-0}" -gt 0 ]; then
                    log "Phase 4: Auto-resume $hash (sources found!) — $(echo "$name" | head -c 50)"
                    amule_cmd "resume $hash" >/dev/null 2>&1
                    rm -f "$TRACKER"
                    RESUMED_BY_BOOST=$((RESUMED_BY_BOOST + 1))
                fi
                ;;
        esac
    done < "$PARSE_FILE"

    if [ "$PAUSED_BY_BOOST" -gt 0 ] || [ "$RESUMED_BY_BOOST" -gt 0 ]; then
        log "Phase 4: Paused $PAUSED_BY_BOOST (no sources), Resumed $RESUMED_BY_BOOST (sources found)"
    else
        log "Phase 4: No focus changes needed"
    fi
else
    rm -f "${STATE_DIR}"/zero-src-*
    log "Phase 4: Auto-pause disabled (SOURCE_BOOST_AUTO_PAUSE_ENABLED=$SOURCE_BOOST_AUTO_PAUSE_ENABLED)"
fi

# Cleanup old trackers for downloads that no longer exist
for TRACKER_FILE in "${STATE_DIR}"/zero-src-*; do
    [ -f "$TRACKER_FILE" ] || continue
    TRACKER_HASH=$(basename "$TRACKER_FILE" | sed 's/^zero-src-//')
    if ! grep -qF "$TRACKER_HASH" "$PARSE_FILE" 2>/dev/null; then
        rm -f "$TRACKER_FILE"
    fi
done


# ═══════════════════════════════════════
# PHASE 5: Kad health
# ═══════════════════════════════════════
KAD_LINE=$(echo "$STATUS_RAW" | grep -i "kad")
if echo "$KAD_LINE" | grep -qi "not connected\|not running\|disconnected"; then
    log "Phase 5: Kad disconnected — reconnecting..."
    amule_cmd "connect kad" >/dev/null 2>&1
else
    log "Phase 5: Kad OK"
fi


# ── Write status file for dashboard ──
cat > "${STATE_DIR}/last-run.json" << STATUSEOF
{
  "timestamp": "$(date '+%Y-%m-%d %H:%M:%S')",
  "ts": $(date +%s),
  "total_downloads": $TOTAL_DL,
  "active": $ACTIVE_COUNT,
  "stalled": $STALLED_COUNT,
  "zero_sources": $ZERO_SRC_COUNT,
  "actions": {
    "cycle_done": $DO_CYCLE,
    "rotation_done": $DO_ROTATION,
    "kad_searches": $DO_KAD,
    "auto_paused": $PAUSED_BY_BOOST,
    "auto_resumed": $RESUMED_BY_BOOST
  }
}
STATUSEOF

rm -f "$PARSE_FILE"

# Keep log under 500 lines
if [ -f "$LOG_FILE" ] && [ "$(wc -l < "$LOG_FILE")" -gt 500 ]; then
    tail -300 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi

log "=== Source Boost END ==="
