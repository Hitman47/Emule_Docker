# aMule ZimaBoard Edition

Client aMule Docker pour ZimaBoard 832 / ZimaOS avec dashboard moderne, gestion de serveurs multi-sources, monitoring et intégration VPN Gluetun.

## Fonctionnalités

### Core
- aMule daemon headless avec Web UI (AmuleWebUI-Reloaded)
- Dashboard moderne (port 8078) avec authentification
- Ajout de liens `ed2k://` (fichier ou liste de serveurs), un ou plusieurs à la fois, avec confirmation d'état
- Onglet Serveurs avec import multi-sources
- Recherche : utilise la Web UI aMule classique (port 4711) — l'onglet Recherche du dashboard a été retiré

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
- Auto-restart aMule optionnel (cron)
- Backup auto de la config avec rotation
- Mise à jour auto de l'IP filter (emule-security.org)
- Mise à jour auto de la liste de serveurs par amuled lui-même (`addresses.dat` généré depuis les sources activées)

### Sécurité
- Obfuscation supportée mais **non obligatoire** (le chiffrement obligatoire coupe la majorité des pairs)
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
├── amule-config/                  # Config aMule (amule.conf, server.met, nodes.dat, addresses.dat…)
│   ├── dashboard-settings.json    # Paramètres du dashboard (sources serveurs, etc.)
│   └── dashboard-history.json     # Historique des actions du dashboard
├── downloads/
│   ├── incoming/                  # Téléchargements terminés
│   └── temp/                      # Téléchargements en cours (même volume : pas de copie cross-device)
├── backups/                       # Sauvegardes config
└── logs/                          # Logs de diagnostic (/var/log/amule-diag)
```

## Ajouter des liens ed2k depuis le navigateur

Chrome et Firefox refusent qu'une page web s'enregistre comme gestionnaire du protocole `ed2k://` (liste blanche de schémas). Quatre façons d'envoyer des liens au dashboard :

1. **Bookmarklet** (Paramètres → Bookmarklet) : glisse le bouton dans ta barre de favoris. Sur une page contenant des liens ed2k, clique dessus : le dashboard s'ouvre dans un nouvel onglet et ajoute les liens. Fonctionne depuis les sites HTTPS (le bookmarklet ne fait pas de requête depuis la page, il ouvre `http://<ip>:8078/#add=…`).
2. **Coller** : Ctrl+V n'importe où sur le dashboard (hors champ de saisie) avec des liens ed2k dans le presse-papiers.
3. **Glisser-déposer** : fais glisser un lien ed2k depuis un autre onglet vers le dashboard.
4. **Clic direct sur les liens ed2k (Windows)** : `tools/windows/` contient un gestionnaire de protocole. Copie `ed2k-to-amule.cmd` dans `C:\Tools\`, édite `DASHBOARD_URL`, puis double-clique `register-ed2k-handler.reg`. Le navigateur proposera ensuite « Ouvrir aMule ? » sur chaque lien ed2k (coche « Toujours autoriser »). Aucun mot de passe n'est stocké : c'est ta session navigateur qui est utilisée.

## Low ID / High ID

Derrière un VPN **sans port forwarding** (NordVPN par exemple), aMule obtient un **Low ID** et Kad est « firewalled ». C'est normal et ça fonctionne, mais tu n'es joignable que par les clients High ID. Pour un High ID il faut un VPN avec port forwarding (ProtonVPN, AirVPN, PIA) : voir les exemples commentés dans `docker-compose.yml`.

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
| `AMULE_MAX_CONNECTIONS` | Connexions max | `800` |
| `AMULE_MAX_SOURCES_PER_FILE` | Sources max/fichier | `800` |
| `AMULE_MAX_CONN_PER_5SEC` | Nouvelles connexions / 5 s | `60` |
| `SOURCE_BOOST_AUTO_PAUSE_ENABLED` | Auto-pause des DL sans source | `false` |
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

### Voir les logs
Via le dashboard (onglet Paramètres > Logs) ou :
```bash
docker exec amule cat /var/log/kad-monitor.log
docker exec amule cat /var/log/source-scanner.log
```

### Lancer les tests du dashboard
```bash
python -m unittest discover -s tests
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
