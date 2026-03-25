#!/bin/sh
# ╔══════════════════════════════════════════╗
# ║  Kad Health Monitor & Auto-Reconnect     ║
# ╚══════════════════════════════════════════╝

EC_HOST="${AMULE_EC_HOST:-localhost}"
EC_PORT="${AMULE_EC_PORT:-4712}"
EC_PASSWORD="${AMULE_EC_PASSWORD:-}"
AMULE_HOME="${AMULE_HOME:-/home/amule/.aMule}"
LOG_PREFIX="[KAD-MON]"
KAD_NODES_URL="http://upd.emule-security.org/nodes.dat"

amulecmd_run() {
    amulecmd -h "$EC_HOST" -p "$EC_PORT" -P "$EC_PASSWORD" -c "$1" 2>&1
}

printf "%s Vérification Kad — %s\n" "$LOG_PREFIX" "$(date '+%Y-%m-%d %H:%M')"

# Check aMule is running
if ! pgrep -x amuled >/dev/null 2>&1; then
    printf "%s amuled n'est pas en cours d'exécution, skip\n" "$LOG_PREFIX"
    exit 0
fi

# Get status
STATUS=$(amulecmd_run "status")

# Check Kad connection
KAD_OK=0
echo "$STATUS" | grep -qi "kad.*running" && KAD_OK=1
echo "$STATUS" | grep -qi "kad.*connected" && KAD_OK=1
echo "$STATUS" | grep -qi "kad.*firewalled" && KAD_OK=1

if [ "$KAD_OK" -eq 1 ]; then
    printf "%s Kad est connecté, tout va bien\n" "$LOG_PREFIX"
else
    printf "%s Kad semble déconnecté, tentative de reconnexion...\n" "$LOG_PREFIX"

    # Refresh nodes.dat
    printf "%s Rafraîchissement de nodes.dat...\n" "$LOG_PREFIX"
    if curl -fsSL --retry 2 --max-time 30 -o "${AMULE_HOME}/nodes.dat.tmp" "$KAD_NODES_URL"; then
        if [ -s "${AMULE_HOME}/nodes.dat.tmp" ]; then
            mv "${AMULE_HOME}/nodes.dat.tmp" "${AMULE_HOME}/nodes.dat"
            printf "%s nodes.dat mis à jour\n" "$LOG_PREFIX"
        else
            rm -f "${AMULE_HOME}/nodes.dat.tmp"
        fi
    fi

    # Try to connect Kad
    amulecmd_run "connect kad" >/dev/null 2>&1
    printf "%s Commande connect kad envoyée\n" "$LOG_PREFIX"

    # Wait and re-check
    sleep 15
    STATUS2=$(amulecmd_run "status")
    if echo "$STATUS2" | grep -qi "kad.*running\|kad.*connected\|kad.*firewalled"; then
        printf "%s Kad reconnecté avec succès !\n" "$LOG_PREFIX"
    else
        printf "%s Kad toujours déconnecté. Vérifiez les logs.\n" "$LOG_PREFIX"
    fi
fi

# Also check ED2K
ED2K_OK=0
echo "$STATUS" | grep -qi "ed2k.*connected" && ED2K_OK=1

if [ "$ED2K_OK" -eq 0 ]; then
    printf "%s ED2K déconnecté, tentative de reconnexion...\n" "$LOG_PREFIX"
    amulecmd_run "connect ed2k" >/dev/null 2>&1
fi

printf "%s Terminé\n" "$LOG_PREFIX"
