═══════════════════════════════════════════════════
  PATCH 8 v3 — aMule ZimaBoard Dashboard
═══════════════════════════════════════════════════

Fichiers modifiés (copier en respectant l'arborescence) :
  docker-compose.yml
  entrypoint.sh
  dashboard/server.py
  dashboard/static/index.html

Fichier à SUPPRIMER manuellement :
  scripts/file-organizer.sh

Résumé des correctifs :
──────────────────────
1. FILE-ORGANIZER supprimé
   - Fonction + cron retirés de entrypoint.sh
   - Variable env retirée de docker-compose.yml
   - Endpoint /api/organize + bouton retirés
   - scripts/file-organizer.sh à supprimer

2. MIGRATION unique (marqueur .migrated)
   - Ne tourne qu'une seule fois

3. ONGLET RECHERCHE supprimé
   - Tab, panneau HTML, CSS, raccourci Ctrl+K retirés
   - panel-dashboard INTACT (corrigé v2/v3)

4. UNICODE FIX — send_json errors='replace'

5. TIMEOUT amulecmd — 15s → 30s

6. NOUVEAU : Panel "Clients connectés"
   - Endpoint /api/clients (parse show ul + statistics)
   - Panel dans l'onglet Transferts avec :
     · Liste des clients upload actifs
     · File d'attente upload, sources trouvées
     · Alerte Low ID avec explication et solution
   - Rafraîchi automatiquement avec les transferts

NOTE sur le Low ID :
  Tes sources "waiting" sont liées au Low ID (pas de port forwarding VPN).
  Solution : passer à ProtonVPN ou AirVPN avec VPN_PORT_FORWARDING=on dans Gluetun.
  NordVPN ne supporte pas le port forwarding de manière fiable.
