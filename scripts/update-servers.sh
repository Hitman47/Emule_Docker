#!/bin/sh
# ╔══════════════════════════════════════════╗
# ║  Server & Nodes Auto-Update              ║
# ║  Reads settings file if available        ║
# ╚══════════════════════════════════════════╝
#
# Both refresh_* helpers know whether amuled is running: server.met goes in
# through an ed2k serverlist link (merged into the live list) and nodes.dat is
# swapped with Kad stopped. Overwriting either file under a running daemon does
# nothing, because aMule holds them in memory and saves its own copy on exit.

. /opt/scripts/lib.sh

SETTINGS_FILE="${SETTINGS_FILE:-${AMULE_HOME}/dashboard-settings.json}"
LOG_PREFIX="[SRV-UPDATE]"

printf "%s Mise a jour des serveurs — %s\n" "$LOG_PREFIX" "$(date '+%Y-%m-%d %H:%M')"
if amuled_running; then
    printf "%s amuled tourne : import à chaud via EC\n" "$LOG_PREFIX"
else
    printf "%s amuled arrêté : remplacement direct des fichiers\n" "$LOG_PREFIX"
fi

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

# ── server.met ──
# With amuled up, every URL is merged; with amuled down the first success wins,
# since each download replaces the whole file.
URL_FILE=$(mktemp)
printf '%s\n' "$SERVER_URLS" > "$URL_FILE"
while read -r url; do
    [ -z "$url" ] && continue
    printf "%s server.met depuis %s...\n" "$LOG_PREFIX" "$url"
    if refresh_server_met "$url"; then
        printf "%s   → OK\n" "$LOG_PREFIX"
        amuled_running || break
    else
        printf "%s   → Echec\n" "$LOG_PREFIX"
    fi
done < "$URL_FILE"

# ── nodes.dat ──
printf '%s\n' "$NODES_URLS" > "$URL_FILE"
while read -r url; do
    [ -z "$url" ] && continue
    printf "%s nodes.dat depuis %s...\n" "$LOG_PREFIX" "$url"
    if refresh_nodes_dat "$url"; then
        printf "%s   → OK\n" "$LOG_PREFIX"
        break
    fi
    printf "%s   → Echec\n" "$LOG_PREFIX"
done < "$URL_FILE"
rm -f "$URL_FILE"

# ── IP filter ──
# aMule reloads this one on demand, so replacing the file is safe either way.
if [ -n "$IPFILTER_URL" ]; then
    printf "%s Telechargement IP filter...\n" "$LOG_PREFIX"
    if fetch_to "$IPFILTER_URL" "${AMULE_HOME}/ipfilter.zip" 100; then
        printf "%s IP filter mis a jour\n" "$LOG_PREFIX"
        amuled_running && amule_ec "reload ipfilter" >/dev/null 2>&1
    else
        printf "%s Echec IP filter\n" "$LOG_PREFIX"
    fi
fi

printf "%s Termine\n" "$LOG_PREFIX"
