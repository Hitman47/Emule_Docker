# aMule ZimaBoard Edition

Client aMule Docker pour ZimaBoard 832 / ZimaOS avec dashboard moderne, moteur de recherche ED2K intégré, gestion de serveurs multi-sources, tri automatique des fichiers, et intégration VPN Gluetun/NordVPN.

## Fonctionnalités

### Core
- aMule daemon headless avec Web UI (AmuleWebUI-Reloaded)
- Dashboard PWA moderne (port 8078) avec authentification
- Moteur de recherche ED2K/Kad intégré + support liens `ed2k://`
- Onglet Serveurs avec import multi-sources

### Gestion des serveurs ED2K (nouveau)
- **3 sources pré-configurées** : eMule Security (officiel, prioritaire), Peerates, FlyerNet
- **Scan automatique toutes les 24h** de toutes les sources activées
- **Panel Paramètres** pour ajouter/supprimer/activer/désactiver des sources
- Support des fichiers `server.met` ET des pages HTML (scraping IP:port)
- Import de sources personnalisées via URL
- Les paramètres sont persistants dans `dashboard-settings.json`

### Kad
- Bootstrap automatique (téléchargement `nodes.dat`)
- **Monitor Kad** : vérification toutes les 15 min, reconnexion auto si déconnecté
- Rafraîchissement périodique de `nodes.dat`
- Bouton de reconnexion manuelle dans les Paramètres

### Automatisation
- Tri auto des fichiers par type (Video, Audio, Images, Documents, Archives, Software)
- Auto-restart aMule (contourne les memory leaks)
- Backup auto de la config avec rotation
- Mise à jour auto de l'IP filter (emule-security.org)

### Sécurité
- Obfuscation renforcée (chiffrement ED2K obligatoire)
- IP Filter mis à jour automatiquement
- Filtrage messages/spam activé
- Dashboard protégé par mot de passe
- Tout le trafic passe par Gluetun VPN

### Monitoring
- Vitesses temps réel, état ED2K/Kad, espace disque
- Visualiseur de logs (Kad monitor, scanner, backup, etc.)
- Healthcheck Docker avec redémarrage auto

## Installation

### 1. Cloner
```bash
git clone <ce-repo>
cd Emule_Docker
```

### 2. Configurer
Édite `docker-compose.yml` et remplace les mots de passe et la clé NordVPN.
Ou utilise un fichier `.env` :
```bash
NORDVPN_PRIVATE_KEY=ta_cle_wireguard
AMULE_GUI_PWD=MonMotDePasse
AMULE_WEBUI_PWD=MonMotDePasse
DASHBOARD_PWD=MonMotDePasse
```

### 3. Lancer
```bash
docker compose up -d --build
```

**Si Gluetun tourne déjà séparément**, utilise `network_mode: "container:gluetun"` et ajoute les ports aMule dans ton Gluetun existant :
```yaml
ports:
  - "4662:4662"
  - "4665:4665/udp"
  - "4672:4672/udp"
  - "4711:4711"
  - "4712:4712"
  - "8078:8078"
```

## Accès

| Service | URL | Port |
|---------|-----|------|
| Dashboard | `http://<ip>:8078` | 8078 |
| Web UI aMule classique | `http://<ip>:4711` | 4711 |

## Structure

```
data/
├── amule-config/                  # Config aMule
│   └── dashboard-settings.json    # Paramètres du dashboard (sources serveurs, etc.)
├── downloads/                     # Téléchargements terminés (à plat)
│   └── .amule-temp/               # Fichiers temporaires aMule
├── backups/                       # Sauvegardes config
└── logs/                          # Logs du dashboard / scripts
```

## Variables d'environnement

| Variable | Description | Défaut |
|----------|-------------|--------|
| `GUI_PWD` | Mot de passe EC/GUI | auto-généré |
| `WEBUI_PWD` | Mot de passe Web UI | auto-généré |
| `DASHBOARD_PWD` | Mot de passe dashboard | = WEBUI_PWD |
| `DASHBOARD_ENABLED` | Activer le dashboard | `true` |
| `DASHBOARD_PORT` | Port du dashboard | `8078` |
| `SERVER_UPDATE_ENABLED` | MAJ auto serveurs | `true` |
| `BACKUP_ENABLED` | Backup auto config | `true` |
| `MOD_AUTO_RESTART_ENABLED` | Auto-restart aMule | `true` |
| `MOD_FIX_KAD_GRAPH_ENABLED` | Fix crash Kad graph | `true` |
| `MOD_FIX_KAD_BOOTSTRAP_ENABLED` | Bootstrap Kad auto | `true` |
| `AMULE_MAX_CONNECTIONS` | Connexions max | `300` |
| `AMULE_MAX_SOURCES_PER_FILE` | Sources max/fichier | `200` |
| `AMULE_DOWNLOAD_CAPACITY` | Capacité DL (Ko/s) | `300` |
| `AMULE_UPLOAD_CAPACITY` | Capacité UL (Ko/s) | `80` |

## Dépannage

### Vérifier que le VPN fonctionne
```bash
docker exec amule curl -s https://api.ipify.org
```

### Kad ne se connecte pas
```bash
docker exec amule /opt/scripts/kad-monitor.sh
```

### Forcer un scan des sources
```bash
docker exec amule /opt/scripts/source-scanner.sh
```

### Forcer un tri des fichiers
```bash
docker exec amule /opt/scripts/file-organizer.sh
```

### Voir les logs
Via le dashboard (onglet Paramètres > Logs) ou :
```bash
docker exec amule cat /var/log/kad-monitor.log
docker exec amule cat /var/log/source-scanner.log
```

### Restaurer un backup
```bash
docker exec amule ls /backups/
docker exec amule tar xzf /backups/amule-config-XXXXXXXX.tar.gz -C /home/amule/
docker restart amule
```

## Crédits

Basé sur [ngosang/docker-amule](https://github.com/ngosang/docker-amule).
Sources serveurs : [emule-security.org](https://www.emule-security.org/serverlist/), [peerates.net](https://edk.peerates.net/fr/), [FlyerNet](http://flyernet.fr.st.free.fr/ip_serveurs.php).

### Important : dossier hôte de destination

Si tu laisses `./data/downloads`, Docker créera ce dossier **dans le dossier du projet**.
Si tu veux écrire directement dans un dossier existant de ton NAS, définis `DOWNLOADS_HOST_DIR` vers ce chemin hôte au lieu d'utiliser le chemin relatif par défaut.
