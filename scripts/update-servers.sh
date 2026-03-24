#!/bin/sh
# ╔══════════════════════════════════════════╗
# ║  Server & Nodes Auto-Update              ║
# ╚══════════════════════════════════════════╝

AMULE_HOME="${AMULE_HOME:-/home/amule/.aMule}"
LOG_PREFIX="[SRV-UPDATE]"

# Sources fiables pour server.met
SERVER_URLS="
http://upd.emule-security.org/server.met
http://edk.peerates.net/servers/best/server.met
"

# Sources fiables pour nodes.dat (Kad)
NODES_URLS="
http://upd.emule-security.org/nodes.dat
"

# IP Filter
IPFILTER_URL="http://upd.emule-security.org/ipfilter.zip"

printf "%s Mise à jour des serveurs — %s\n" "$LOG_PREFIX" "$(date '+%Y-%m-%d %H:%M')"

# Update server.met
for url in $SERVER_URLS; do
    printf "%s Téléchargement server.met depuis %s...\n" "$LOG_PREFIX" "$url"
    if curl -s --retry 2 --max-time 30 -o "${AMULE_HOME}/server.met.tmp" "$url"; then
        # Vérifier que le fichier n'est pas vide
        if [ -s "${AMULE_HOME}/server.met.tmp" ]; then
            mv "${AMULE_HOME}/server.met.tmp" "${AMULE_HOME}/server.met"
            printf "%s server.met mis à jour avec succès\n" "$LOG_PREFIX"
            break
        fi
    fi
    rm -f "${AMULE_HOME}/server.met.tmp"
    printf "%s Échec pour %s, essai suivant...\n" "$LOG_PREFIX" "$url"
done

# Update nodes.dat
for url in $NODES_URLS; do
    printf "%s Téléchargement nodes.dat depuis %s...\n" "$LOG_PREFIX" "$url"
    if curl -s --retry 2 --max-time 30 -o "${AMULE_HOME}/nodes.dat.tmp" "$url"; then
        if [ -s "${AMULE_HOME}/nodes.dat.tmp" ]; then
            mv "${AMULE_HOME}/nodes.dat.tmp" "${AMULE_HOME}/nodes.dat"
            printf "%s nodes.dat mis à jour avec succès\n" "$LOG_PREFIX"
            break
        fi
    fi
    rm -f "${AMULE_HOME}/nodes.dat.tmp"
done

# Update IP filter
printf "%s Téléchargement IP filter...\n" "$LOG_PREFIX"
if curl -s --retry 2 --max-time 60 -o "${AMULE_HOME}/ipfilter.zip" "$IPFILTER_URL"; then
    printf "%s IP filter mis à jour\n" "$LOG_PREFIX"
else
    printf "%s Échec mise à jour IP filter\n" "$LOG_PREFIX"
fi

printf "%s Terminé\n" "$LOG_PREFIX"
