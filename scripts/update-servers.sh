#!/bin/sh
# ╔══════════════════════════════════════════╗
# ║  Server & Nodes Auto-Update              ║
# ║  Reads settings file if available        ║
# ╚══════════════════════════════════════════╝

AMULE_HOME="${AMULE_HOME:-/home/amule/.aMule}"
SETTINGS_FILE="${SETTINGS_FILE:-${AMULE_HOME}/dashboard-settings.json}"
LOG_PREFIX="[SRV-UPDATE]"

printf "%s Mise a jour des serveurs — %s\n" "$LOG_PREFIX" "$(date '+%Y-%m-%d %H:%M')"

# Try to read from settings file
if [ -f "$SETTINGS_FILE" ] && command -v jq >/dev/null 2>&1; then
    printf "%s Lecture des sources depuis %s\n" "$LOG_PREFIX" "$SETTINGS_FILE"
    SERVER_URLS=$(jq -r '.server_sources[] | select(.enabled==true and .kind=="serverlist") | .url' "$SETTINGS_FILE" 2>/dev/null)
    NODES_URLS=$(jq -r '.nodes_sources[] | select(.enabled==true) | .url' "$SETTINGS_FILE" 2>/dev/null)
    IPFILTER_URL=$(jq -r '.ipfilter_url // empty' "$SETTINGS_FILE" 2>/dev/null)
else
    printf "%s Utilisation des sources par defaut\n" "$LOG_PREFIX"
    SERVER_URLS="http://upd.emule-security.org/server.met
http://edk.peerates.net/servers/best/server.met"
    NODES_URLS="http://upd.emule-security.org/nodes.dat"
    IPFILTER_URL="http://upd.emule-security.org/ipfilter.zip"
fi

# Update server.met
echo "$SERVER_URLS" | while read -r url; do
    [ -z "$url" ] && continue
    printf "%s Telechargement server.met depuis %s...\n" "$LOG_PREFIX" "$url"
    if curl -fsSL --retry 2 --max-time 30 -o "${AMULE_HOME}/server.met.tmp" "$url"; then
        if [ -s "${AMULE_HOME}/server.met.tmp" ]; then
            mv "${AMULE_HOME}/server.met.tmp" "${AMULE_HOME}/server.met"
            printf "%s server.met mis a jour\n" "$LOG_PREFIX"
            break
        fi
    fi
    rm -f "${AMULE_HOME}/server.met.tmp"
    printf "%s Echec pour %s\n" "$LOG_PREFIX" "$url"
done

# Update nodes.dat
echo "$NODES_URLS" | while read -r url; do
    [ -z "$url" ] && continue
    printf "%s Telechargement nodes.dat depuis %s...\n" "$LOG_PREFIX" "$url"
    if curl -fsSL --retry 2 --max-time 30 -o "${AMULE_HOME}/nodes.dat.tmp" "$url"; then
        if [ -s "${AMULE_HOME}/nodes.dat.tmp" ]; then
            mv "${AMULE_HOME}/nodes.dat.tmp" "${AMULE_HOME}/nodes.dat"
            printf "%s nodes.dat mis a jour\n" "$LOG_PREFIX"
            break
        fi
    fi
    rm -f "${AMULE_HOME}/nodes.dat.tmp"
done

# Update IP filter
if [ -n "$IPFILTER_URL" ]; then
    printf "%s Telechargement IP filter...\n" "$LOG_PREFIX"
    curl -fsSL --retry 2 --max-time 60 -o "${AMULE_HOME}/ipfilter.zip" "$IPFILTER_URL" 2>/dev/null && \
        printf "%s IP filter mis a jour\n" "$LOG_PREFIX" || \
        printf "%s Echec IP filter\n" "$LOG_PREFIX"
fi

printf "%s Termine\n" "$LOG_PREFIX"
