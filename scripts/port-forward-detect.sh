#!/bin/sh
# ╔══════════════════════════════════════════════════════════════╗
# ║  VPN Port Forwarding Auto-Detect & aMule Reconfigure         ║
# ║  Queries Gluetun for forwarded port, updates aMule config    ║
# ╚══════════════════════════════════════════════════════════════╝

EC_HOST="${AMULE_EC_HOST:-localhost}"
EC_PORT="${AMULE_EC_PORT:-4712}"
EC_PASSWORD="${AMULE_EC_PASSWORD:-}"
EC_PASSWORD_HASH="${AMULE_EC_PASSWORD_HASH:-}"
AMULE_HOME="${AMULE_HOME:-/home/amule/.aMule}"
AMULE_CONF="${AMULE_HOME}/amule.conf"
LOG_PREFIX="[PORT-FWD]"
STATE_FILE="${AMULE_HOME}/.last_fwd_port"

CRED_FILE="${AMULE_HOME}/.ec_credentials"
if [ -f "$CRED_FILE" ]; then
    . "$CRED_FILE"
fi

# Try multiple Gluetun endpoints
FWD_PORT=""
for endpoint in \
    "http://127.0.0.1:9999/v1/openvpn/portforwarded" \
    "http://127.0.0.1:9999/v1/portforwarded" \
    "http://gluetun:9999/v1/openvpn/portforwarded"; do
    RAW=$(curl -s --max-time 3 "$endpoint" 2>/dev/null)
    PORT=$(echo "$RAW" | grep -oE '"port"\s*:\s*[0-9]+' | grep -oE '[0-9]+' | head -1)
    if [ -n "$PORT" ] && [ "$PORT" -gt 0 ] 2>/dev/null; then
        FWD_PORT="$PORT"
        break
    fi
done

if [ -z "$FWD_PORT" ] || [ "$FWD_PORT" = "0" ]; then
    printf "%s No forwarded port detected from VPN\n" "$LOG_PREFIX"
    printf "%s Tip: Enable VPN_PORT_FORWARDING=on in Gluetun\n" "$LOG_PREFIX"
    printf "%s      Not all VPN providers support port forwarding\n" "$LOG_PREFIX"
    exit 0
fi

# Check if port changed
LAST_PORT=""
[ -f "$STATE_FILE" ] && LAST_PORT=$(cat "$STATE_FILE" 2>/dev/null)

if [ "$FWD_PORT" = "$LAST_PORT" ]; then
    printf "%s Port unchanged (%s), skipping\n" "$LOG_PREFIX" "$FWD_PORT"
    exit 0
fi

printf "%s VPN forwarded port: %s (was: %s)\n" "$LOG_PREFIX" "$FWD_PORT" "${LAST_PORT:-none}"

# Update amule.conf Port= to use forwarded port
if [ -f "$AMULE_CONF" ]; then
    CURRENT_PORT=$(grep '^Port=' "$AMULE_CONF" | head -1 | cut -d= -f2)
    if [ "$CURRENT_PORT" != "$FWD_PORT" ]; then
        printf "%s Updating aMule TCP port: %s → %s\n" "$LOG_PREFIX" "$CURRENT_PORT" "$FWD_PORT"
        sed -i "s/^Port=.*/Port=${FWD_PORT}/" "$AMULE_CONF"
        # Signal aMule to restart to pick up new port
        printf "%s aMule needs restart to use new port\n" "$LOG_PREFIX"
        # Don't auto-restart, just log it — the user or auto-restart cron will handle it
    fi
fi

echo "$FWD_PORT" > "$STATE_FILE"
printf "%s Done\n" "$LOG_PREFIX"
