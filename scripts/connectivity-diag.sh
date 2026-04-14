#!/bin/sh
# ╔══════════════════════════════════════════════════════════════╗
# ║  Connectivity & Source Diagnostic Logger                     ║
# ║  Runs every 3min — logs everything needed to debug           ║
# ║  peer discovery, source finding, and download issues         ║
# ╚══════════════════════════════════════════════════════════════╝

EC_HOST="${AMULE_EC_HOST:-localhost}"
EC_PORT="${AMULE_EC_PORT:-4712}"
EC_PASSWORD="${AMULE_EC_PASSWORD:-}"
EC_PASSWORD_HASH="${AMULE_EC_PASSWORD_HASH:-}"
AMULE_HOME="${AMULE_HOME:-/home/amule/.aMule}"
LOG_DIR="/var/log/amule-diag"
LOG_FILE="${LOG_DIR}/connectivity.log"
SNAPSHOT_FILE="${LOG_DIR}/last-snapshot.json"
HISTORY_FILE="${LOG_DIR}/diag-history.jsonl"
MAX_LOG_SIZE=2097152  # 2MB
MAX_HISTORY_LINES=2000
TS=$(date '+%Y-%m-%d %H:%M:%S')
TS_UNIX=$(date +%s)

mkdir -p "$LOG_DIR"

# Rotate logs if too large
if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)" -gt "$MAX_LOG_SIZE" ]; then
    mv "$LOG_FILE" "${LOG_FILE}.1"
fi
if [ -f "$HISTORY_FILE" ] && [ "$(wc -l < "$HISTORY_FILE" 2>/dev/null || echo 0)" -gt "$MAX_HISTORY_LINES" ]; then
    tail -n 1000 "$HISTORY_FILE" > "${HISTORY_FILE}.tmp" && mv "${HISTORY_FILE}.tmp" "$HISTORY_FILE"
fi

# Load credentials
CRED_FILE="${AMULE_HOME}/.ec_credentials"
if [ -f "$CRED_FILE" ]; then
    . "$CRED_FILE"
fi

log() {
    printf "[%s] %s\n" "$TS" "$1" >> "$LOG_FILE"
}

amulecmd_run() {
    OUTPUT=$(amulecmd -h "$EC_HOST" -p "$EC_PORT" -P "$EC_PASSWORD" -c "$1" 2>&1)
    if echo "$OUTPUT" | grep -qi "wrong password\|Authentication failed"; then
        if [ -n "$EC_PASSWORD_HASH" ]; then
            OUTPUT=$(amulecmd -h "$EC_HOST" -p "$EC_PORT" -P "$EC_PASSWORD_HASH" -c "$1" 2>&1)
        fi
    fi
    echo "$OUTPUT"
}

# ── Check amuled is running ──
if ! pgrep -x amuled >/dev/null 2>&1; then
    log "CRITICAL: amuled is NOT running"
    echo "{\"ts\":$TS_UNIX,\"status\":\"amuled_down\"}" >> "$HISTORY_FILE"
    exit 0
fi

log "═══ DIAGNOSTIC START ═══"

# ══════════════════════════════════════════
# 1. NETWORK CONNECTIVITY
# ══════════════════════════════════════════
log "── NETWORK ──"

# Check VPN / external IP
EXT_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || echo "FAILED")
log "  External IP: $EXT_IP"

# Check if VPN is up (Gluetun health)
GLUETUN_OK="unknown"
if curl -sf --max-time 3 http://127.0.0.1:9999/v1/openvpn/status >/dev/null 2>&1; then
    GLUETUN_OK="yes"
elif curl -sf --max-time 3 http://127.0.0.1:9999/v1/publicip/ip >/dev/null 2>&1; then
    GLUETUN_OK="yes"
fi
log "  Gluetun reachable: $GLUETUN_OK"

# Check VPN port forwarding
FWD_PORT=""
FWD_RAW=$(curl -s --max-time 3 http://127.0.0.1:9999/v1/openvpn/portforwarded 2>/dev/null)
if [ -n "$FWD_RAW" ]; then
    FWD_PORT=$(echo "$FWD_RAW" | grep -oE '[0-9]+' | head -1)
fi
if [ -z "$FWD_PORT" ]; then
    FWD_RAW=$(curl -s --max-time 3 http://127.0.0.1:9999/v1/portforwarded 2>/dev/null)
    if [ -n "$FWD_RAW" ]; then
        FWD_PORT=$(echo "$FWD_RAW" | grep -oE '[0-9]+' | head -1)
    fi
fi
log "  VPN forwarded port: ${FWD_PORT:-NONE (= Low ID!)}"

# Check if aMule ports are reachable
for port in 4662 4672 4665; do
    if nc -z -w2 localhost $port 2>/dev/null; then
        log "  Port $port: OPEN"
    else
        log "  Port $port: CLOSED"
    fi
done

# ══════════════════════════════════════════
# 2. AMULE CONNECTION STATUS
# ══════════════════════════════════════════
log "── AMULE STATUS ──"
STATUS_RAW=$(amulecmd_run "status")

# ED2K
ED2K_STATUS="disconnected"
ED2K_SERVER=""
ED2K_ID=""
echo "$STATUS_RAW" | grep -qi "ed2k.*not connected" && ED2K_STATUS="disconnected"
echo "$STATUS_RAW" | grep -qi "ed2k.*now connecting" && ED2K_STATUS="connecting"
if echo "$STATUS_RAW" | grep -qi "ed2k.*connected to"; then
    ED2K_STATUS="connected"
    ED2K_SERVER=$(echo "$STATUS_RAW" | grep -i "ed2k.*connected to" | head -1 | sed 's/.*connected to //i')
    echo "$STATUS_RAW" | grep -qi "high.*id\|highid" && ED2K_ID="HighID"
    echo "$STATUS_RAW" | grep -qi "low.*id\|lowid" && ED2K_ID="LowID"
    [ -z "$ED2K_ID" ] && ED2K_ID="LowID"  # Default behind VPN
fi
log "  ED2K: $ED2K_STATUS | Server: $ED2K_SERVER | ID: $ED2K_ID"

# Kad
KAD_STATUS="disconnected"
echo "$STATUS_RAW" | grep -qi "kad.*not connected\|kad.*not running\|kad.*disconnected" && KAD_STATUS="disconnected"
echo "$STATUS_RAW" | grep -qi "kad.*firewalled" && KAD_STATUS="firewalled"
echo "$STATUS_RAW" | grep -qi "kad.*running\|kad.*connected" && KAD_STATUS="connected"
echo "$STATUS_RAW" | grep -qi "kad.*connecting\|kad.*bootstrapping" && KAD_STATUS="connecting"
log "  Kad: $KAD_STATUS"

# Speeds
DL_SPEED=$(echo "$STATUS_RAW" | grep -ioE 'dl:\s*[0-9.]+' | grep -oE '[0-9.]+' | head -1)
UL_SPEED=$(echo "$STATUS_RAW" | grep -ioE 'ul:\s*[0-9.]+' | grep -oE '[0-9.]+' | head -1)
log "  Speed: DL=${DL_SPEED:-0} kB/s  UL=${UL_SPEED:-0} kB/s"

# ══════════════════════════════════════════
# 3. DOWNLOAD & SOURCE ANALYSIS
# ══════════════════════════════════════════
log "── DOWNLOADS & SOURCES ──"
DL_RAW=$(amulecmd_run "show dl")

TOTAL_DL=0
ACTIVE_DL=0
ZERO_SOURCE=0
STALLED=0
DL_NAMES=""

echo "$DL_RAW" | grep -oE '^>?\s*[0-9A-Fa-f]{32}' | tr -d '> ' | while read HASH; do
    TOTAL_DL=$((TOTAL_DL + 1))
done
TOTAL_DL=$(echo "$DL_RAW" | grep -cE '^>?\s*[0-9A-Fa-f]{32}')

# Parse each download for source count
CURRENT_NAME=""
CURRENT_HASH=""
while IFS= read -r line; do
    # New download entry
    HASH_MATCH=$(echo "$line" | grep -oE '^>?\s*[0-9A-Fa-f]{32}' | tr -d '> ')
    if [ -n "$HASH_MATCH" ]; then
        CURRENT_HASH="$HASH_MATCH"
        CURRENT_NAME=$(echo "$line" | sed "s/^>*\s*$HASH_MATCH\s*//")
        continue
    fi
    [ -z "$CURRENT_HASH" ] && continue

    # Check for sources
    SRC_NUM=$(echo "$line" | grep -ioE '[0-9]+\s*source' | grep -oE '[0-9]+' | head -1)
    if [ -n "$SRC_NUM" ]; then
        if [ "$SRC_NUM" -eq 0 ]; then
            ZERO_SOURCE=$((ZERO_SOURCE + 1))
            log "  ⚠ ZERO SOURCES: ${CURRENT_NAME:-$CURRENT_HASH}"
        else
            log "  ✓ ${CURRENT_NAME:-$CURRENT_HASH}: $SRC_NUM sources"
        fi
    fi

    # Check for paused/active
    echo "$line" | grep -qi "paused\|stopped" && continue
    echo "$line" | grep -qi "downloading\|waiting\|getting sources\|connecting" && ACTIVE_DL=$((ACTIVE_DL + 1))
done << EOF
$DL_RAW
EOF

log "  Total downloads: $TOTAL_DL | Active: $ACTIVE_DL | Zero sources: $ZERO_SOURCE"

# ══════════════════════════════════════════
# 4. SERVER LIST HEALTH
# ══════════════════════════════════════════
log "── SERVERS ──"
SRV_RAW=$(amulecmd_run "show servers")
SRV_COUNT=$(echo "$SRV_RAW" | grep -cE '([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]+')
log "  Known servers: $SRV_COUNT"

# Check server.met age
if [ -f "${AMULE_HOME}/server.met" ]; then
    SMET_AGE=$(( $(date +%s) - $(stat -c %Y "${AMULE_HOME}/server.met" 2>/dev/null || echo 0) ))
    SMET_HOURS=$(( SMET_AGE / 3600 ))
    log "  server.met age: ${SMET_HOURS}h"
    [ "$SMET_HOURS" -gt 48 ] && log "  ⚠ server.met is stale (>48h)"
fi

# Check nodes.dat
if [ -f "${AMULE_HOME}/nodes.dat" ]; then
    NDAT_AGE=$(( $(date +%s) - $(stat -c %Y "${AMULE_HOME}/nodes.dat" 2>/dev/null || echo 0) ))
    NDAT_HOURS=$(( NDAT_AGE / 3600 ))
    NDAT_SIZE=$(wc -c < "${AMULE_HOME}/nodes.dat" 2>/dev/null || echo 0)
    log "  nodes.dat: ${NDAT_SIZE} bytes, age ${NDAT_HOURS}h"
    [ "$NDAT_SIZE" -lt 1000 ] && log "  ⚠ nodes.dat looks too small"
else
    log "  ⚠ nodes.dat MISSING — Kad cannot bootstrap"
fi

# ══════════════════════════════════════════
# 5. CONFIG AUDIT (check for common mistakes)
# ══════════════════════════════════════════
log "── CONFIG AUDIT ──"
CONF="${AMULE_HOME}/amule.conf"
if [ -f "$CONF" ]; then
    CRYPT_REQ=$(grep '^IsClientCryptLayerRequired=' "$CONF" | cut -d= -f2)
    [ "$CRYPT_REQ" = "1" ] && log "  ⚠ PROBLEM: IsClientCryptLayerRequired=1 (blocks most peers!)"

    SAFE_CONN=$(grep '^SafeServerConnect=' "$CONF" | cut -d= -f2)
    [ "$SAFE_CONN" = "1" ] && log "  ⚠ WARN: SafeServerConnect=1 (limits connections)"

    MAX_SRC=$(grep '^MaxSourcesPerFile=' "$CONF" | cut -d= -f2)
    [ -n "$MAX_SRC" ] && [ "$MAX_SRC" -lt 300 ] && log "  ⚠ WARN: MaxSourcesPerFile=$MAX_SRC (too low, recommend 500)"

    MAX_CONN=$(grep '^MaxConnections=' "$CONF" | cut -d= -f2)
    [ -n "$MAX_CONN" ] && [ "$MAX_CONN" -lt 400 ] && log "  ⚠ WARN: MaxConnections=$MAX_CONN (too low, recommend 500)"

    CONN_5S=$(grep '^MaxConnectionsPerFiveSeconds=' "$CONF" | cut -d= -f2)
    [ -n "$CONN_5S" ] && [ "$CONN_5S" -lt 25 ] && log "  ⚠ WARN: MaxConnectionsPerFiveSeconds=$CONN_5S (too low, recommend 40)"

    ADD_CLI=$(grep '^AddServerListFromClient=' "$CONF" | cut -d= -f2)
    [ "$ADD_CLI" = "0" ] && log "  ⚠ INFO: AddServerListFromClient=0 (less server discovery)"

    UPNP=$(grep '^UPnPEnabled=' "$CONF" | cut -d= -f2)
    log "  UPnP: ${UPNP:-?} (should be 0 behind VPN)"

    log "  MaxSources=$MAX_SRC MaxConn=$MAX_CONN Conn5s=$CONN_5S CryptReq=$CRYPT_REQ"
fi

# ══════════════════════════════════════════
# 6. ISSUE SUMMARY & RECOMMENDATIONS
# ══════════════════════════════════════════
log "── DIAGNOSTIC SUMMARY ──"
ISSUES=0

if [ "$ED2K_STATUS" = "disconnected" ]; then
    log "  🔴 ED2K is DISCONNECTED — no server connection"
    ISSUES=$((ISSUES + 1))
fi

if [ "$KAD_STATUS" = "disconnected" ]; then
    log "  🔴 Kad is DISCONNECTED — no DHT peer discovery"
    ISSUES=$((ISSUES + 1))
fi

if [ "$KAD_STATUS" = "firewalled" ]; then
    log "  🟡 Kad is FIREWALLED — limited peer discovery (need port forwarding)"
    ISSUES=$((ISSUES + 1))
fi

if [ "$ED2K_ID" = "LowID" ]; then
    log "  🟡 ED2K Low ID — cannot connect to other Low ID peers (need port forwarding)"
    ISSUES=$((ISSUES + 1))
fi

if [ -z "$FWD_PORT" ] || [ "$FWD_PORT" = "0" ]; then
    log "  🔴 No VPN port forwarding — High ID impossible, sources severely limited"
    log "     FIX: Enable VPN_PORT_FORWARDING=on in Gluetun, or use a VPN that supports it"
    ISSUES=$((ISSUES + 1))
fi

if [ "$CRYPT_REQ" = "1" ]; then
    log "  🔴 Encryption REQUIRED — most peers rejected"
    log "     FIX: Set IsClientCryptLayerRequired=0 in amule.conf"
    ISSUES=$((ISSUES + 1))
fi

if [ "$SRV_COUNT" -lt 3 ]; then
    log "  🟡 Only $SRV_COUNT servers known — too few for good source discovery"
    ISSUES=$((ISSUES + 1))
fi

if [ "$ZERO_SOURCE" -gt 0 ]; then
    log "  🟡 $ZERO_SOURCE download(s) with ZERO sources"
    ISSUES=$((ISSUES + 1))
fi

if [ "$EXT_IP" = "FAILED" ]; then
    log "  🔴 Cannot reach internet — VPN may be down"
    ISSUES=$((ISSUES + 1))
fi

if [ "$ISSUES" -eq 0 ]; then
    log "  ✅ No issues detected"
else
    log "  ⚠ $ISSUES issue(s) found — see above"
fi

log "═══ DIAGNOSTIC END ═══"

# ══════════════════════════════════════════
# 7. WRITE JSON SNAPSHOT (for dashboard)
# ══════════════════════════════════════════
cat > "$SNAPSHOT_FILE" << SNAPEOF
{
  "timestamp": "$TS",
  "ts": $TS_UNIX,
  "network": {
    "external_ip": "$EXT_IP",
    "vpn_ok": "$GLUETUN_OK",
    "forwarded_port": "${FWD_PORT:-null}"
  },
  "amule": {
    "ed2k_status": "$ED2K_STATUS",
    "ed2k_server": "$(echo "$ED2K_SERVER" | sed 's/"/\\"/g')",
    "ed2k_id": "$ED2K_ID",
    "kad_status": "$KAD_STATUS",
    "dl_speed": "${DL_SPEED:-0}",
    "ul_speed": "${UL_SPEED:-0}"
  },
  "downloads": {
    "total": $TOTAL_DL,
    "active": $ACTIVE_DL,
    "zero_sources": $ZERO_SOURCE
  },
  "servers": {
    "count": $SRV_COUNT
  },
  "config": {
    "crypt_required": "${CRYPT_REQ:-?}",
    "max_sources": "${MAX_SRC:-?}",
    "max_connections": "${MAX_CONN:-?}",
    "conn_5sec": "${CONN_5S:-?}"
  },
  "issues_count": $ISSUES
}
SNAPEOF

# Append to history (JSONL)
echo "{\"ts\":$TS_UNIX,\"ed2k\":\"$ED2K_STATUS\",\"ed2k_id\":\"$ED2K_ID\",\"kad\":\"$KAD_STATUS\",\"dl\":\"${DL_SPEED:-0}\",\"ul\":\"${UL_SPEED:-0}\",\"srv\":$SRV_COUNT,\"zero_src\":$ZERO_SOURCE,\"issues\":$ISSUES,\"fwd_port\":\"${FWD_PORT:-}\"}" >> "$HISTORY_FILE"
