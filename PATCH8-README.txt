═══════════════════════════════════════════════════
  PATCH 8 — aMule ZimaBoard Dashboard
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
   - Endpoint /api/organize retiré de server.py
   - Bouton "Organiser fichiers" retiré de l'UI
   - scripts/file-organizer.sh à supprimer

2. MIGRATION unique (marqueur .migrated)
   - La migration /incoming → /downloads/incoming ne tourne
     qu'une seule fois grâce au fichier /downloads/.migrated

3. ONGLET RECHERCHE supprimé
   - Tab, panneau HTML, CSS dédié et raccourci Ctrl+K retirés
   - Les fonctions JS restent (dead code inoffensif)
   - panel-dashboard INTACT (corrigé v2)

4. UNICODE FIX (noms de fichiers français)
   - send_json encode maintenant avec errors='replace'
   - Plus de crash sur é, è, ê, etc.

5. TIMEOUT amulecmd augmenté
   - 15s → 30s (exec + run_amulecmd)
   - Évite les faux timeouts quand aMule est chargé
