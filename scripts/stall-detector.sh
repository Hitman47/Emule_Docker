#!/bin/sh
# ╔══════════════════════════════════════════╗
# ║  Download Stall Detector                  ║
# ║  Switches ED2K server if no progress      ║
# ║  for 30 minutes on active downloads       ║
# ╚══════════════════════════════════════════╝

EC_HOST="${AMULE_EC_HOST:-localhost}"
EC_PORT="${AMULE_EC_PORT:-4712}"
EC_PASSWORD="${AMULE_EC_PASSWORD:-}"
EC_PASSWORD_HASH="${AMULE_EC_PASSWORD_HASH:-}"
AMULE_HOME="${AMULE_HOME:-/home/amule/.aMule}"
LOG_PREFIX="[STALL-DET]"

# State file to track progress over time
STATE_FILE="${AMULE_HOME}/.stall_state"
# How many consecutive stall checks before switching (5min interval x 6 = 30min)
STALL_THRESHOLD=6

# Load credentials
CRED_FILE="${AMULE_HOME}/.ec_credentials"
if [ -f "$CRED_FILE" ]; then
    . "$CRED_FILE"
fi

amulecmd_run() {
    OUTPUT=$(amulecmd -h "$EC_HOST" -p "$EC_PORT" -P "$EC_PASSWORD" -c "$1" 2>&1)
    if echo "$OUTPUT" | grep -qi "wrong password\|Authentication failed"; then
        if [ -n "$EC_PASSWORD_HASH" ]; then
            OUTPUT=$(amulecmd -h "$EC_HOST" -p "$EC_PORT" -P "$EC_PASSWORD_HASH" -c "$1" 2>&1)
        fi
    fi
    echo "$OUTPUT"
}

# Check aMule is running
if ! pgrep -x amuled >/dev/null 2>&1; then
    exit 0
fi

# Get current downloads
DL_RAW=$(amulecmd_run "show dl")

# Extract hashes of active (non-paused, non-complete) downloads
# Format: > HASH Filename
ACTIVE_HASHES=$(echo "$DL_RAW" | grep -oE '^[> ]*[0-9A-Fa-f]{32}' | tr -d '> ' | sort)

if [ -z "$ACTIVE_HASHES" ]; then
    # No downloads at all — reset state and exit
    rm -f "$STATE_FILE"
    exit 0
fi

# Check if any are actually downloading (have speed > 0 or status = downloading)
# For simplicity: get status to check download speed
STATUS=$(amulecmd_run "status")
DL_SPEED=$(echo "$STATUS" | grep -i "download" | grep -oE '[0-9]+' | head -1)
DL_SPEED=${DL_SPEED:-0}

# Check for paused state — if ALL are paused, don't trigger
# Look for lines after hash lines that contain "paused"
HAS_ACTIVE=0
PAUSE_COUNT=0
TOTAL_COUNT=0
for hash in $ACTIVE_HASHES; do
    TOTAL_COUNT=$((TOTAL_COUNT + 1))
    # Check if this specific download has activity
    # In amulecmd output, lines after the hash line contain status
    SECTION=$(echo "$DL_RAW" | sed -n "/$hash/,/^>/p" | head -20)
    if echo "$SECTION" | grep -qi "paused\|stopped\|complete"; then
        PAUSE_COUNT=$((PAUSE_COUNT + 1))
    else
        HAS_ACTIVE=1
    fi
done

if [ "$HAS_ACTIVE" -eq 0 ]; then
    # All downloads are paused or complete — not a stall
    rm -f "$STATE_FILE"
    exit 0
fi

printf "%s Vérification: %d DL actifs, %d en pause, vitesse=%s\n" "$LOG_PREFIX" "$TOTAL_COUNT" "$PAUSE_COUNT" "$DL_SPEED"

# Create fingerprint of current progress (hash of all download lines)
# If this fingerprint doesn't change, progress is stalled
FINGERPRINT=$(echo "$DL_RAW" | grep -iE '[0-9]+\s*%|bytes|source' | md5sum | cut -d' ' -f1)

# Read previous state
PREV_FINGERPRINT=""
STALL_COUNT=0
if [ -f "$STATE_FILE" ]; then
    PREV_FINGERPRINT=$(head -1 "$STATE_FILE" 2>/dev/null)
    STALL_COUNT=$(sed -n '2p' "$STATE_FILE" 2>/dev/null)
    STALL_COUNT=${STALL_COUNT:-0}
fi

if [ "$DL_SPEED" -gt 0 ]; then
    # There IS download activity — reset stall counter
    printf "%s Téléchargement actif (%s bytes/sec), reset compteur\n" "$LOG_PREFIX" "$DL_SPEED"
    printf "%s\n0\n" "$FINGERPRINT" > "$STATE_FILE"
    exit 0
fi

if [ "$FINGERPRINT" = "$PREV_FINGERPRINT" ]; then
    # No change — increment stall counter
    STALL_COUNT=$((STALL_COUNT + 1))
    printf "%s\n%d\n" "$FINGERPRINT" "$STALL_COUNT" > "$STATE_FILE"
    printf "%s Pas de progression depuis %d vérifications (%d min)\n" "$LOG_PREFIX" "$STALL_COUNT" "$((STALL_COUNT * 5))"

    if [ "$STALL_COUNT" -ge "$STALL_THRESHOLD" ]; then
        printf "%s === STALL DÉTECTÉ (30+ min sans progrès) ===\n" "$LOG_PREFIX"
        printf "%s Changement de serveur ED2K...\n" "$LOG_PREFIX"

        # Get current server
        CURRENT_SERVER=$(echo "$STATUS" | grep -i "ed2k.*connected" | head -1)
        printf "%s Serveur actuel: %s\n" "$LOG_PREFIX" "$CURRENT_SERVER"

        # Disconnect from current server
        amulecmd_run "disconnect" >/dev/null 2>&1
        sleep 2

        # Get server list and pick a different one
        SERVERS=$(amulecmd_run "show servers")
        CURRENT_ADDR=$(echo "$CURRENT_SERVER" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]+')

        # Find a different server to connect to
        NEW_ADDR=$(echo "$SERVERS" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]+' | grep -v "$CURRENT_ADDR" | head -1)

        if [ -n "$NEW_ADDR" ]; then
            printf "%s Connexion au nouveau serveur: %s\n" "$LOG_PREFIX" "$NEW_ADDR"
            amulecmd_run "connect $NEW_ADDR" >/dev/null 2>&1
        else
            printf "%s Pas d'autre serveur disponible, reconnexion générique\n" "$LOG_PREFIX"
            amulecmd_run "connect ed2k" >/dev/null 2>&1
        fi

        # Reset counter
        printf "%s\n0\n" "$FINGERPRINT" > "$STATE_FILE"

        # Wait and log result
        sleep 10
        NEW_STATUS=$(amulecmd_run "status")
        NEW_SERVER=$(echo "$NEW_STATUS" | grep -i "ed2k" | head -1)
        printf "%s Nouveau statut: %s\n" "$LOG_PREFIX" "$NEW_SERVER"
    fi
else
    # Fingerprint changed — something moved, reset
    printf "%s Progression détectée (fingerprint changé), reset compteur\n" "$LOG_PREFIX"
    printf "%s\n0\n" "$FINGERPRINT" > "$STATE_FILE"
fi
