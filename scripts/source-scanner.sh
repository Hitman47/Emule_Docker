#!/bin/sh
# ╔══════════════════════════════════════════╗
# ║  Server Source Scanner (24h periodic)     ║
# ║  Reads dashboard-settings.json and       ║
# ║  imports all enabled server sources      ║
# ╚══════════════════════════════════════════╝

EC_HOST="${AMULE_EC_HOST:-localhost}"
EC_PORT="${AMULE_EC_PORT:-4712}"
EC_PASSWORD="${AMULE_EC_PASSWORD:-}"
EC_PASSWORD_HASH="${AMULE_EC_PASSWORD_HASH:-}"
AMULE_HOME="${AMULE_HOME:-/home/amule/.aMule}"
SETTINGS_FILE="${SETTINGS_FILE:-${AMULE_HOME}/dashboard-settings.json}"
LOG_PREFIX="[SRC-SCAN]"

# Try loading credentials from file
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

printf "%s Scan périodique des sources serveurs — %s\n" "$LOG_PREFIX" "$(date '+%Y-%m-%d %H:%M')"

# Check aMule is running
if ! pgrep -x amuled >/dev/null 2>&1; then
    printf "%s amuled n'est pas en cours d'exécution, skip\n" "$LOG_PREFIX"
    exit 0
fi

# Read settings
if [ ! -f "$SETTINGS_FILE" ]; then
    printf "%s Fichier settings introuvable: %s\n" "$LOG_PREFIX" "$SETTINGS_FILE"
    exit 0
fi

# Parse server sources from JSON using jq
SOURCES=$(jq -r '.server_sources[] | select(.enabled==true) | .kind + "|" + .url + "|" + .key' "$SETTINGS_FILE" 2>/dev/null)

if [ -z "$SOURCES" ]; then
    printf "%s Aucune source serveur activée\n" "$LOG_PREFIX"
    exit 0
fi

echo "$SOURCES" | while IFS='|' read -r kind url key; do
    [ -z "$url" ] && continue
    printf "%s [%s] %s — %s\n" "$LOG_PREFIX" "$kind" "$key" "$url"

    if [ "$kind" = "serverlist" ]; then
        # refresh_server_met merges into the live list when amuled runs, and
        # only replaces server.met when it does not.
        if refresh_server_met "$url"; then
            printf "%s   → Importé avec succès\n" "$LOG_PREFIX"
        else
            printf "%s   → Échec\n" "$LOG_PREFIX"
        fi

    elif [ "$kind" = "html" ]; then
        # Scrape HTML page for IP:port pairs
        printf "%s   → Scraping page HTML...\n" "$LOG_PREFIX"
        HTML=$(curl -fsSL --max-time 20 "$url" 2>/dev/null || echo "")
        if [ -z "$HTML" ]; then
            printf "%s   → Échec téléchargement\n" "$LOG_PREFIX"
            continue
        fi

        # Extract IP:port from HTML
        SERVERS=$(echo "$HTML" | sed 's/<[^>]*>//g' | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}[: ][0-9]{2,5}' | sed 's/ /:/g' | sort -u)
        echo "$SERVERS" | while IFS=':' read -r ip port; do
            { [ -z "$ip" ] || [ -z "$port" ]; } && continue
            LINK="ed2k://|server|${ip}|${port}|/"
            amulecmd_run "add $LINK" >/dev/null 2>&1
        done
        COUNT=$(echo "$SERVERS" | grep -c '.')
        printf "%s   → %s serveurs trouvés et importés\n" "$LOG_PREFIX" "$COUNT"
    fi
done

# Also update nodes.dat for Kad
NODES_URLS=$(jq -r '.nodes_sources[] | select(.enabled==true) | .url' "$SETTINGS_FILE" 2>/dev/null)
if [ -n "$NODES_URLS" ]; then
    NURL_FILE=$(mktemp)
    printf '%s\n' "$NODES_URLS" > "$NURL_FILE"
    while read -r nurl; do
        [ -z "$nurl" ] && continue
        printf "%s Mise à jour nodes.dat depuis %s\n" "$LOG_PREFIX" "$nurl"
        if refresh_nodes_dat "$nurl"; then
            printf "%s   → nodes.dat mis à jour\n" "$LOG_PREFIX"
            break
        fi
    done < "$NURL_FILE"
    rm -f "$NURL_FILE"
fi

# Update IP filter
IPFILTER_URL=$(jq -r '.ipfilter_url // empty' "$SETTINGS_FILE" 2>/dev/null)
if [ -n "$IPFILTER_URL" ]; then
    printf "%s Mise à jour IP filter...\n" "$LOG_PREFIX"
    if fetch_to "$IPFILTER_URL" "${AMULE_HOME}/ipfilter.zip" 100; then
        printf "%s   → IP filter mis à jour\n" "$LOG_PREFIX"
        amule_ec "reload ipfilter" >/dev/null 2>&1
    else
        printf "%s   → Échec IP filter\n" "$LOG_PREFIX"
    fi
fi

# Only reconnect when actually disconnected: re-establishing an eD2k session
# costs the queue positions the client spent hours earning.
if ! amulecmd_run "status" | grep -qi "ed2k: *connected to\|ed2k: *now connecting"; then
    printf "%s ED2K déconnecté, reconnexion...\n" "$LOG_PREFIX"
    amulecmd_run "connect ed2k" >/dev/null 2>&1
fi

# Update last_scan timestamp in settings
TMPFILE=$(mktemp)
jq --arg ts "$(date -Iseconds)" '.last_scan = $ts' "$SETTINGS_FILE" > "$TMPFILE" 2>/dev/null && \
    mv "$TMPFILE" "$SETTINGS_FILE" || rm -f "$TMPFILE"

printf "%s Scan terminé\n" "$LOG_PREFIX"
