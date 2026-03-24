#!/bin/sh
# ╔══════════════════════════════════════════╗
# ║  Config Backup                            ║
# ╚══════════════════════════════════════════╝

AMULE_HOME="${AMULE_HOME:-/home/amule/.aMule}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_KEEP=${BACKUP_KEEP:-4}
LOG_PREFIX="[BACKUP]"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
BACKUP_FILE="${BACKUP_DIR}/amule-config-${TIMESTAMP}.tar.gz"

printf "%s Création backup — %s\n" "$LOG_PREFIX" "$TIMESTAMP"

mkdir -p "${BACKUP_DIR}"

# Sauvegarder les fichiers de config importants
tar czf "$BACKUP_FILE" \
    -C "$(dirname "$AMULE_HOME")" \
    "$(basename "$AMULE_HOME")/amule.conf" \
    "$(basename "$AMULE_HOME")/remote.conf" \
    "$(basename "$AMULE_HOME")/addresses.dat" \
    "$(basename "$AMULE_HOME")/shareddir.dat" \
    "$(basename "$AMULE_HOME")/server.met" \
    "$(basename "$AMULE_HOME")/nodes.dat" \
    "$(basename "$AMULE_HOME")/clients.met" \
    "$(basename "$AMULE_HOME")/preferences.dat" \
    "$(basename "$AMULE_HOME")/statistics.dat" \
    2>/dev/null

if [ -f "$BACKUP_FILE" ]; then
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    printf "%s Backup créé: %s (%s)\n" "$LOG_PREFIX" "$BACKUP_FILE" "$SIZE"
else
    printf "%s ERREUR: backup échoué\n" "$LOG_PREFIX"
    exit 1
fi

# Rotation: garder uniquement les N derniers backups
BACKUP_COUNT=$(ls -1 "${BACKUP_DIR}"/amule-config-*.tar.gz 2>/dev/null | wc -l)
if [ "$BACKUP_COUNT" -gt "$BACKUP_KEEP" ]; then
    REMOVE_COUNT=$((BACKUP_COUNT - BACKUP_KEEP))
    ls -1t "${BACKUP_DIR}"/amule-config-*.tar.gz | tail -n "$REMOVE_COUNT" | while read -r old; do
        printf "%s Suppression ancien backup: %s\n" "$LOG_PREFIX" "$(basename "$old")"
        rm -f "$old"
    done
fi

printf "%s Terminé (%s backups conservés)\n" "$LOG_PREFIX" "$BACKUP_KEEP"
