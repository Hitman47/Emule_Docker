#!/bin/sh
# ╔══════════════════════════════════════════╗
# ║  Kad Health Monitor & Auto-Reconnect     ║
# ║  + memory watchdog (replaces the blind   ║
# ║    daily MOD_AUTO_RESTART)               ║
# ╚══════════════════════════════════════════╝

. /opt/scripts/lib.sh

LOG_PREFIX="[KAD-MON]"
KAD_NODES_URL="${KAD_NODES_URL:-http://upd.emule-security.org/nodes.dat}"
# Restart amuled when its resident memory passes this (MB). 0 disables.
RESTART_IF_RSS_MB="${RESTART_IF_RSS_MB:-1500}"

printf "%s Vérification Kad — %s\n" "$LOG_PREFIX" "$(date '+%Y-%m-%d %H:%M')"

if ! amuled_running; then
    printf "%s amuled n'est pas en cours d'exécution, skip\n" "$LOG_PREFIX"
    exit 0
fi

# ── Memory watchdog ──
# A fixed daily restart throws away a healthy session; restarting on actual
# memory growth only acts when there is something to act on. The supervisor in
# the entrypoint restarts amuled, so killing it is enough.
if [ "$RESTART_IF_RSS_MB" -gt 0 ] 2>/dev/null; then
    RSS_MB=$(amuled_rss_mb)
    if [ "$RSS_MB" -gt "$RESTART_IF_RSS_MB" ]; then
        printf "%s amuled à %s MB (> %s MB), redémarrage\n" "$LOG_PREFIX" "$RSS_MB" "$RESTART_IF_RSS_MB"
        pkill -TERM -x amuled
        exit 0
    fi
    printf "%s Mémoire amuled: %s MB (seuil %s MB)\n" "$LOG_PREFIX" "$RSS_MB" "$RESTART_IF_RSS_MB"
fi

STATUS=$(amule_ec "status")

# ── Kad ──
# amulecmd prints "Kad: Connected (ok)" / "Kad: Connected (firewalled)"
#                 "Kad: Not connected" / "Kad: Not running"
if echo "$STATUS" | grep -qi "kad: *connected"; then
    printf "%s Kad est connecté, tout va bien\n" "$LOG_PREFIX"
else
    printf "%s Kad semble déconnecté, tentative de reconnexion...\n" "$LOG_PREFIX"

    # refresh_nodes_dat stops Kad, swaps the file and starts Kad again — aMule
    # only reads nodes.dat when Kad starts, so a plain overwrite does nothing.
    if refresh_nodes_dat "$KAD_NODES_URL"; then
        printf "%s nodes.dat mis à jour et Kad relancé\n" "$LOG_PREFIX"
    else
        printf "%s nodes.dat non récupéré, simple reconnexion\n" "$LOG_PREFIX"
        amule_ec "connect kad" >/dev/null 2>&1
    fi

    sleep 15
    if echo "$(amule_ec "status")" | grep -qi "kad: *connected"; then
        printf "%s Kad reconnecté avec succès !\n" "$LOG_PREFIX"
    else
        printf "%s Kad toujours déconnecté. Vérifiez les logs.\n" "$LOG_PREFIX"
    fi
fi

# ── eD2k ──
# "eD2k: Connected to <name> <ip> with LowID|HighID" / "eD2k: Now connecting"
if ! echo "$STATUS" | grep -qi "ed2k: *connected to\|ed2k: *now connecting"; then
    printf "%s ED2K déconnecté, tentative de reconnexion...\n" "$LOG_PREFIX"
    amule_ec "connect ed2k" >/dev/null 2>&1
fi

printf "%s Terminé\n" "$LOG_PREFIX"
