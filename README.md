# 🐴 aMule ZimaBoard Edition

Version sur mesure d'aMule en Docker, optimisée pour **ZimaBoard 832** sous ZimaOS avec stack VPN Gluetun/NordVPN.

## ✨ Fonctionnalités

### Héritées du projet original (ngosang/docker-amule)
- aMule avec Web UI Reloaded
- Auto-restart programmable (fix memory leak)
- Auto-share de répertoires
- Fix Kad graph crash 
- Bootstrap Kad automatique

### Nouvelles fonctionnalités
- **🔍 Moteur de recherche intégré** — Recherche Kad/ED2K avec interface moderne, ajout de liens ed2k en un clic
- **📊 Dashboard de monitoring** — Vitesses, connexions, espace disque en temps réel
- **📂 Organiseur automatique** — Tri des fichiers par type (vidéo, musique, images, docs, logiciels, archives)
- **🛡️ Sécurité renforcée** — Authentification dashboard, obfuscation forcée, filtrage IP/messages durci
- **🔄 Mise à jour auto des serveurs** — server.met, nodes.dat, ipfilter.zip
- **💾 Backup automatique** — Sauvegarde de la config avec rétention configurable
- **🏥 Healthcheck Docker** — Redémarrage auto si aMule plante
- **⚡ Limites ressources** — CPU/RAM plafonnés pour ne pas étouffer le ZimaBoard

## 🚀 Installation

### 1. Prérequis
- ZimaBoard avec ZimaOS (ou tout système Docker)
- Stack Gluetun/NordVPN fonctionnel
- Docker + Docker Compose

### 2. Cloner et configurer

```bash
# Copier le projet
cd /opt
git clone <ce-repo> amule-zima
cd amule-zima

# Créer les répertoires de données
mkdir -p data/{config,incoming,temp,backups}

# Éditer les mots de passe dans docker-compose.yml
nano docker-compose.yml
```

### 3. Configurer Gluetun

Ajoute ces ports dans ton conteneur Gluetun (docker-compose de Gluetun) :

```yaml
ports:
  # ... tes ports VPN existants ...
  - "4711:4711"      # aMule Web UI classique
  - "4713:4713"      # Dashboard custom
  - "4662:4662"      # ED2K TCP
  - "4665:4665/udp"  # ED2K global search
  - "4672:4672/udp"  # ED2K UDP
```

### 4. Build et lancement

```bash
docker compose build
docker compose up -d
```

### 5. Accès

| Service | URL | Description |
|---------|-----|-------------|
| **Dashboard** | `http://<IP-ZIMA>:4713` | Recherche, monitoring, fichiers |
| **Web UI classique** | `http://<IP-ZIMA>:4711` | Interface aMule Reloaded |

## ⚙️ Configuration

### Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `PUID` / `PGID` | `1000` | UID/GID de l'utilisateur |
| `TZ` | `Europe/Paris` | Fuseau horaire |
| `GUI_PWD` | auto-généré | Mot de passe Remote GUI |
| `WEBUI_PWD` | auto-généré | Mot de passe Web UI classique |
| `DASHBOARD_PWD` | `admin` | **Mot de passe du dashboard** |

#### Mods originaux

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MOD_AUTO_RESTART_ENABLED` | `true` | Redémarrage auto d'aMule |
| `MOD_AUTO_RESTART_CRON` | `0 5 * * *` | Cron du redémarrage (5h chaque jour) |
| `MOD_AUTO_SHARE_ENABLED` | `false` | Partage auto de répertoires |
| `MOD_AUTO_SHARE_DIRECTORIES` | `/incoming` | Répertoires à partager (séparés par `;`) |
| `MOD_FIX_KAD_GRAPH_ENABLED` | `true` | Fix crash stats Kad |
| `MOD_FIX_KAD_BOOTSTRAP_ENABLED` | `true` | Téléchargement auto nodes.dat |

#### Nouveaux mods

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MOD_DASHBOARD_ENABLED` | `true` | Active le dashboard custom |
| `MOD_DASHBOARD_PORT` | `4713` | Port du dashboard |
| `MOD_FILE_ORGANIZER_ENABLED` | `true` | Tri auto des fichiers |
| `MOD_FILE_ORGANIZER_CRON` | `*/10 * * * *` | Fréquence du tri (toutes les 10 min) |
| `MOD_SERVER_UPDATER_ENABLED` | `true` | MAJ auto des listes serveurs |
| `MOD_SERVER_UPDATER_CRON` | `0 3 * * 0` | Fréquence MAJ (dimanche 3h) |
| `MOD_BACKUP_ENABLED` | `true` | Backup auto config |
| `MOD_BACKUP_CRON` | `0 4 * * *` | Fréquence backup (4h chaque jour) |
| `MOD_BACKUP_KEEP_DAYS` | `7` | Jours de rétention |

#### Répertoires d'organisation

| Variable | Défaut | Extensions triées |
|----------|--------|-------------------|
| `ORGANIZE_VIDEO_DIR` | `/incoming/Videos` | mkv, avi, mp4, mov, wmv... |
| `ORGANIZE_MUSIC_DIR` | `/incoming/Musique` | mp3, flac, ogg, wav, aac... |
| `ORGANIZE_IMAGE_DIR` | `/incoming/Images` | jpg, png, gif, bmp, webp... |
| `ORGANIZE_DOC_DIR` | `/incoming/Documents` | pdf, doc, epub, srt, nfo... |
| `ORGANIZE_SOFTWARE_DIR` | `/incoming/Logiciels` | iso, exe, msi, deb, rpm... |
| `ORGANIZE_ARCHIVE_DIR` | `/incoming/Archives` | zip, rar, 7z, tar, gz... |

## 🔐 Sécurité

Cette version durcit la config aMule par défaut :

- **Obfuscation obligatoire** — `IsClientCryptLayerRequired=1`
- **Messages filtrés** — Spam, messages non-amis bloqués
- **Filtrage IP actif** — ipfilter.zip auto-chargé depuis emule-security.org
- **Serveurs sécurisés** — `SafeServerConnect=1`, pas d'ajout auto de serveurs
- **Dashboard protégé** — Authentification par mot de passe + cookie sécurisé
- **Tout le trafic ED2K passe par le VPN** (Gluetun)

## 📁 Structure des données

```
data/
├── config/          # Config aMule (persiste)
│   ├── amule.conf
│   ├── remote.conf
│   ├── nodes.dat
│   └── server.met
├── incoming/        # Fichiers terminés
│   ├── Videos/
│   ├── Musique/
│   ├── Images/
│   ├── Documents/
│   ├── Logiciels/
│   └── Archives/
├── temp/            # Fichiers en cours
└── backups/         # Sauvegardes config
    └── amule_config_20260325_040000.tar.gz
```

## 🔧 Commandes utiles

```bash
# Voir les logs
docker logs -f amule

# Forcer un tri des fichiers
docker exec amule /home/amule/scripts/file-organizer.sh

# Forcer une MAJ des serveurs
docker exec amule /home/amule/scripts/update-servers.sh /home/amule/.aMule

# Backup manuel
docker exec amule /home/amule/scripts/backup-config.sh /home/amule/.aMule /backups 7

# Accès amulecmd
docker exec -it amule amulecmd -h localhost -p 4712 -P <GUI_PWD>
```

## 🏗️ Architecture

```
┌─────────────────────────────────────┐
│           Gluetun (NordVPN)         │
│  ┌────────────────────────────────┐ │
│  │        Conteneur aMule         │ │
│  │  ┌──────────┐  ┌───────────┐  │ │
│  │  │  amuled   │  │ Dashboard │  │ │
│  │  │ :4662/72  │  │  :4713    │  │ │
│  │  └──────────┘  └───────────┘  │ │
│  │  ┌──────────┐  ┌───────────┐  │ │
│  │  │ amuleweb │  │ Cron jobs │  │ │
│  │  │  :4711   │  │ organizer │  │ │
│  │  └──────────┘  │  backup   │  │ │
│  │                │  servers  │  │ │
│  │                └───────────┘  │ │
│  └────────────────────────────────┘ │
└─────────────────────────────────────┘
```

## 📝 Basé sur

- [ngosang/docker-amule](https://github.com/ngosang/docker-amule) — Projet original
- [AmuleWebUI-Reloaded](https://github.com/MatteoRagni/AmuleWebUI-Reloaded) — Thème Web UI
- [aMule](https://github.com/amule-project/amule) — Client ED2K
