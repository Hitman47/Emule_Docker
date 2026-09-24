#!/bin/sh
# ╔══════════════════════════════════════════════════════╗
# ║  Smart Stall Detector & Auto-Reconnect               ║
# ║  OPT-IN — off by default (STALL_DETECTOR_ENABLED)    ║
# ║  Reads timeout from dashboard-settings.json           ║
# ║  On stall: changes ED2K server + refreshes nodes.dat  ║
# ╚══════════════════════════════════════════════════════╝
#
# Why this is off by default: on a Low ID connection, "no progress for N
# minutes" is the normal state, not a fault. Hopping servers then costs the
# queue positions the client spent hours earning, which is worse than waiting.
# Enable it only if downloads genuinely wedge on a High ID setup.

. /opt/scripts/lib.sh

STALL_DETECTOR_ENABLED="${STALL_DETECTOR_ENABLED:-false}"
SETTINGS_FILE="${SETTINGS_FILE:-${AMULE_HOME}/dashboard-settings.json}"
LOG_PREFIX="[STALL-DET]"
LOG_FILE="/var/log/amule-diag/stall-detector.log"
KAD_NODES_URL="http://upd.emule-security.org/nodes.dat"

STATE_FILE="${AMULE_HOME}/.stall_state"

mkdir -p /var/log/amule-diag

log() {
    printf "[%s] %s %s\n" "$(date '+%H:%M:%S')" "$LOG_PREFIX" "$1" >> "$LOG_FILE"
    printf "%s %s\n" "$LOG_PREFIX" "$1"
}

if ! is_true "$STALL_DETECTOR_ENABLED"; then
    exit 0
fi

# ── Read configurable timeout from settings ──
# Floor at 60 min: below that we react to ordinary Low ID queueing.
TIMEOUT_MIN=60
if [ -f "$SETTINGS_FILE" ] && command -v jq >/dev/null 2>&1; then
    RAW_TIMEOUT=$(jq -r '.stall_timeout_minutes // 60' "$SETTINGS_FILE" 2>/dev/null)
    if [ -n "$RAW_TIMEOUT" ] && [ "$RAW_TIMEOUT" -ge 60 ] 2>/dev/null; then
        TIMEOUT_MIN=$RAW_TIMEOUT
    fi
fi
# Convert to 5-minute intervals (cron runs every 5 min)
STALL_THRESHOLD=$((TIMEOUT_MIN / 5))
[ "$STALL_THRESHOLD" -lt 12 ] && STALL_THRESHOLD=12

amulecmd_run() { amule_ec "$1"; }

# ── Check amuled is running ──
if ! amuled_running; then
    exit 0
fi

# ── Get downloads and status ──
DL_RAW=$(amulecmd_run "show dl")
STATUS=$(amulecmd_run "status")

# Count active downloads
ACTIVE_HASHES=$(echo "$DL_RAW" | grep -oE '^[> ]*[0-9A-Fa-f]{32}' | tr -d '> ' | sort)
if [ -z "$ACTIVE_HASHES" ]; then
    rm -f "$STATE_FILE"
    exit 0
fi

# Skip if all paused
HAS_ACTIVE=0
echo "$DL_RAW" | grep -qi "downloading\|waiting\|getting sources\|connecting" && HAS_ACTIVE=1
if [ "$HAS_ACTIVE" -eq 0 ]; then
    PAUSE_ONLY=1
    for hash in $ACTIVE_HASHES; do
        SECTION=$(echo "$DL_RAW" | sed -n "/$hash/,/^>/p" | head -5)
        echo "$SECTION" | grep -qi "paused\|stopped\|complete" || { PAUSE_ONLY=0; break; }
    done
    if [ "$PAUSE_ONLY" -eq 1 ]; then
        rm -f "$STATE_FILE"
        exit 0
    fi
fi

# ── Check download speed ──
DL_SPEED=$(echo "$STATUS" | grep -ioE 'dl:\s*[0-9.]+' | grep -oE '[0-9.]+' | head -1)
DL_SPEED=${DL_SPEED:-0}

TOTAL_DL=$(echo "$ACTIVE_HASHES" | wc -w)
log "Check: ${TOTAL_DL} DLs, speed=${DL_SPEED} kB/s, timeout=${TIMEOUT_MIN}min (threshold=${STALL_THRESHOLD})"

# Create fingerprint from ONLY percentage + source counts (stable between checks)
# Extract just "[X.X%]    N/   N" patterns — these only change when actual progress happens
FINGERPRINT=$(echo "$DL_RAW" | grep -oE '\[[0-9.,]+%\]\s+[0-9]+/\s*[0-9]+' | sort | md5sum | cut -d' ' -f1)

# Read previous state
PREV_FINGERPRINT=""
STALL_COUNT=0
if [ -f "$STATE_FILE" ]; then
    PREV_FINGERPRINT=$(head -1 "$STATE_FILE" 2>/dev/null)
    STALL_COUNT=$(sed -n '2p' "$STATE_FILE" 2>/dev/null)
    STALL_COUNT=${STALL_COUNT:-0}
fi

# Speed > 0 → reset
DL_SPEED_INT=$(printf '%.0f' "$DL_SPEED" 2>/dev/null || echo 0)
if [ "$DL_SPEED_INT" -gt 0 ] 2>/dev/null; then
    log "Actif (${DL_SPEED} kB/s), reset"
    printf "%s\n0\n" "$FINGERPRINT" > "$STATE_FILE"
    exit 0
fi

if [ "$FINGERPRINT" = "$PREV_FINGERPRINT" ]; then
    STALL_COUNT=$((STALL_COUNT + 1))
    printf "%s\n%d\n" "$FINGERPRINT" "$STALL_COUNT" > "$STATE_FILE"
    STALL_MINUTES=$((STALL_COUNT * 5))
    log "Pas de progrès: ${STALL_COUNT}/${STALL_THRESHOLD} (${STALL_MINUTES}/${TIMEOUT_MIN} min)"

    if [ "$STALL_COUNT" -ge "$STALL_THRESHOLD" ]; then
        log "═══ STALL DÉTECTÉ (${STALL_MINUTES} min sans progrès) ═══"

        CURRENT_SERVER=$(echo "$STATUS" | grep -i "ed2k.*connected" | head -1)
        log "Serveur actuel: $CURRENT_SERVER"

        # Step 1: leave the eD2k server only. A bare "disconnect" also drops
        # Kad, which is the one network that still finds sources on Low ID.
        amulecmd_run "disconnect ed2k" >/dev/null 2>&1
        sleep 2

        # Step 2: Refresh nodes.dat (stops Kad, swaps, restarts Kad)
        log "Rafraîchissement nodes.dat..."
        if refresh_nodes_dat "$KAD_NODES_URL"; then
            log "nodes.dat mis à jour"
        else
            log "nodes.dat non récupéré"
        fi

        # Step 3: Import server lists
        log "Import listes de serveurs..."
        refresh_server_met "http://upd.emule-security.org/server.met" >/dev/null 2>&1
        refresh_server_met "http://edk.peerates.net/servers/best/server.met" >/dev/null 2>&1
        sleep 2

        # Step 4: Connect to a DIFFERENT server
        SERVERS=$(amulecmd_run "show servers")
        CURRENT_ADDR=$(echo "$CURRENT_SERVER" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]+')
        ALL_ADDRS=$(echo "$SERVERS" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]+' | grep -v "$CURRENT_ADDR")

        CONNECTED=0
        for NEW_ADDR in $(echo "$ALL_ADDRS" | head -3); do
            log "Tentative: $NEW_ADDR"
            amulecmd_run "connect $NEW_ADDR" >/dev/null 2>&1
            sleep 5
            NEW_STATUS=$(amulecmd_run "status")
            if echo "$NEW_STATUS" | grep -qi "ed2k.*connected to"; then
                CONNECTED=1
                log "✓ Connecté à $NEW_ADDR"
                break
            fi
        done

        if [ "$CONNECTED" -eq 0 ]; then
            log "Fallback: connect ed2k"
            amulecmd_run "connect ed2k" >/dev/null 2>&1
        fi

        # Step 5: make sure Kad came back up
        if ! amulecmd_run "status" | grep -qi "kad: *connected"; then
            log "Reconnexion Kad..."
            amulecmd_run "connect kad" >/dev/null 2>&1
        fi

        # Reset
        printf "%s\n0\n" "$FINGERPRINT" > "$STATE_FILE"

        sleep 8
        FINAL=$(amulecmd_run "status")
        log "Résultat: $(echo "$FINAL" | grep -i "ed2k" | head -1 | sed 's/^[> ]*//')"
        log "Résultat: $(echo "$FINAL" | grep -i "kad" | head -1 | sed 's/^[> ]*//')"
        log "═══ FIN RECONNEXION ═══"
    fi
else
    log "Progression détectée, reset"
    printf "%s\n0\n" "$FINGERPRINT" > "$STATE_FILE"
fi
