═══════════════════════════════════════════════════
  PATCH 9 — Source Boost (Low ID Optimizer)
═══════════════════════════════════════════════════

Fichiers modifiés :
  docker-compose.yml        — FILE_ORGANIZER retiré
  entrypoint.sh             — source-boost cron, config tweaks
  dashboard/server.py       — /api/clients, /api/source_boost
  dashboard/static/index.html — panel clients, bouton boost, Low ID warning

Fichier AJOUTÉ :
  scripts/source-boost.sh   — LE script d'optimisation (chmod +x)

Fichier à SUPPRIMER manuellement :
  scripts/file-organizer.sh

═══════════════════════════════════════════════════
  Ce que fait Source Boost (toutes les 10 min)
═══════════════════════════════════════════════════

PHASE 1 — Refresh stalled (toutes les 30 min)
  Pause/Resume les DLs bloqués en "waiting" qui ont des sources.
  Force aMule à re-demander un slot aux sources connectées.

PHASE 2 — Rotation serveur ED2K (toutes les 60 min)
  Chaque serveur a un pool de clients différent.
  En changeant de serveur, tu deviens visible par d'autres
  clients High ID qui n'étaient pas joignables avant.

PHASE 3 — Recherche Kad ciblée (toutes les 15 min)
  Lance une recherche Kad avec les mots-clés de chaque
  fichier bloqué. Ça déclenche des lookups DHT qui aident
  à découvrir de nouvelles sources.

PHASE 4 — Focus intelligent
  Auto-pause les DLs avec 0 sources depuis 60+ min pour
  libérer des slots de connexion. Auto-resume dès que
  des sources apparaissent.

PHASE 5 — Santé Kad
  Reconnecte Kad automatiquement si déconnecté.

═══════════════════════════════════════════════════
  Tweaks amule.conf appliqués
═══════════════════════════════════════════════════
  MaxSourcesPerFile=800  (was 500)
  MaxConnections=800     (was 500)
  MaxConn5sec=60         (was 40)
  + Reconnect=1, SmartIdCheck=1, DAPPref=1, UAPPref=1,
    ICH=1, AICHTrust=1, StartNextFile=1, UseSrcSeeds=1

═══════════════════════════════════════════════════
  Nouveautés UI
═══════════════════════════════════════════════════
  - Bouton "🚀 Boost sources" dans l'onglet Transferts
  - Panel "Clients connectés" avec upload queue
  - Alerte Low ID avec explication et solution
  - Log "Source Boost" dans les paramètres

Inclut aussi tous les correctifs du patch 8 :
  file-organizer supprimé, migration unique, onglet
  recherche supprimé, unicode fix, timeout 30s.
