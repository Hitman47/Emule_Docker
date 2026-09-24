#!/bin/sh
# ╔══════════════════════════════════════════════════════════╗
# ║  lib.sh — helpers shared by the maintenance scripts       ║
# ║  Source it:  . /opt/scripts/lib.sh                        ║
# ╚══════════════════════════════════════════════════════════╝
#
# Two things here matter more than the rest:
#
#  * refresh_nodes_dat / refresh_server_met — aMule keeps its Kad node list and
#    its server list IN MEMORY and rewrites the files when the corresponding
#    network stops. Overwriting nodes.dat or server.met under a running amuled
#    is at best a no-op (aMule never re-reads them) and at worst destroys the
#    download when aMule saves its own copy over ours. Both helpers go through
#    the daemon when it runs, and only touch the files when it does not.
#
#  * amule_ec — one EC wrapper instead of four near-identical copies.

AMULE_HOME="${AMULE_HOME:-/home/amule/.aMule}"
EC_HOST="${AMULE_EC_HOST:-localhost}"
EC_PORT="${AMULE_EC_PORT:-4712}"
EC_PASSWORD="${AMULE_EC_PASSWORD:-}"
EC_PASSWORD_HASH="${AMULE_EC_PASSWORD_HASH:-}"

# Credentials written by the entrypoint
if [ -f "${AMULE_HOME}/.ec_credentials" ]; then
    . "${AMULE_HOME}/.ec_credentials"
fi

is_true() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

amuled_running() {
    pgrep -x amuled >/dev/null 2>&1
}

# Resident memory of amuled, in MB (0 when not running)
amuled_rss_mb() {
    PID=$(pgrep -x amuled 2>/dev/null | head -1)
    [ -n "$PID" ] || { echo 0; return; }
    RSS_KB=$(awk '/^VmRSS:/ { print $2 }' "/proc/$PID/status" 2>/dev/null)
    echo $(( ${RSS_KB:-0} / 1024 ))
}

amule_ec() {
    OUT=$(amulecmd -h "$EC_HOST" -p "$EC_PORT" -P "$EC_PASSWORD" -c "$1" 2>&1)
    if echo "$OUT" | grep -qi "wrong password\|Authentication failed"; then
        if [ -n "$EC_PASSWORD_HASH" ]; then
            OUT=$(amulecmd -h "$EC_HOST" -p "$EC_PORT" -P "$EC_PASSWORD_HASH" -c "$1" 2>&1)
        fi
    fi
    echo "$OUT"
}

# Give a downloaded file the same owner as the aMule home directory. cron runs
# as root, amuled does not.
adopt_owner() {
    chown --reference="$AMULE_HOME" "$1" 2>/dev/null || true
}

# Download $1 to $2, refusing anything empty or implausibly small.
# NOTE: sh has no local variables. Every name here is prefixed _FT_ so that a
# caller's own _URL / _DEST survive the call.
fetch_to() {
    _FT_URL="$1"; _FT_DEST="$2"; _FT_MIN="${3:-64}"
    _FT_TMP="${_FT_DEST}.tmp.$$"
    if ! curl -fsSL --retry 2 --max-time 30 -o "$_FT_TMP" "$_FT_URL" 2>/dev/null; then
        rm -f "$_FT_TMP"; return 1
    fi
    if [ ! -s "$_FT_TMP" ] || [ "$(wc -c < "$_FT_TMP")" -lt "$_FT_MIN" ]; then
        rm -f "$_FT_TMP"; return 1
    fi
    mv "$_FT_TMP" "$_FT_DEST" || { rm -f "$_FT_TMP"; return 1; }
    adopt_owner "$_FT_DEST"
    return 0
}

# ── nodes.dat ───────────────────────────────────────────────
# Kad must be stopped for the swap: stopping it makes aMule flush its own
# nodes.dat first, so ours is not overwritten a moment later, and starting it
# again is what makes aMule read the new file.
refresh_nodes_dat() {
    _ND_URL="${1:-${KAD_NODES_URL:-http://upd.emule-security.org/nodes.dat}}"
    _ND_DEST="${AMULE_HOME}/nodes.dat"
    _ND_STAGE="${AMULE_HOME}/.nodes.dat.new"

    if ! fetch_to "$_ND_URL" "$_ND_STAGE" 100; then
        return 1
    fi

    if amuled_running; then
        amule_ec "disconnect kad" >/dev/null 2>&1
        sleep 2
        mv "$_ND_STAGE" "$_ND_DEST" && adopt_owner "$_ND_DEST"
        amule_ec "connect kad" >/dev/null 2>&1
    else
        mv "$_ND_STAGE" "$_ND_DEST" && adopt_owner "$_ND_DEST"
    fi
    return 0
}

# ── server.met ──────────────────────────────────────────────
# While amuled runs, the only supported way in is the ed2k serverlist link: it
# merges into the in-memory list. Replacing the file is reserved for pre-start.
refresh_server_met() {
    _SM_URL="$1"
    [ -n "$_SM_URL" ] || return 1

    if amuled_running; then
        _SM_OUT=$(amule_ec "add ed2k://|serverlist|${_SM_URL}|/")
        echo "$_SM_OUT" | grep -qi "error\|invalid\|failed" && return 1
        return 0
    fi
    fetch_to "$_SM_URL" "${AMULE_HOME}/server.met" 100
}
