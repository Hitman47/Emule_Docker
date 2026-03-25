#!/usr/bin/env sh
set -e

printf "\n"
printf "╔══════════════════════════════════════════╗\n"
printf "║   aMule ZimaBoard Edition                ║\n"
printf "║   Starting up...                         ║\n"
printf "╚══════════════════════════════════════════╝\n"
printf "\n"

# ── Variables ──
AMULE_UID=${PUID:-1000}
AMULE_GID=${PGID:-1000}
AMULE_INCOMING=${INCOMING_DIR:-"/incoming"}
AMULE_TEMP=${TEMP_DIR:-"/temp"}
AMULE_HOME=/home/amule/.aMule
AMULE_CONF=${AMULE_HOME}/amule.conf
REMOTE_CONF=${AMULE_HOME}/remote.conf
KAD_NODES_DAT_URL="http://upd.emule-security.org/nodes.dat"
SERVER_MET_URL="http://upd.emule-security.org/server.met"
IPFILTER_URL="http://upd.emule-security.org/ipfilter.zip"
CRON_FILE="/etc/cron.d/amule"
CRON_HAS_JOBS=0

# ── Performance tuning (ZimaBoard 832 optimized) ──
MAX_CONNECTIONS=${AMULE_MAX_CONNECTIONS:-300}
MAX_SOURCES=${AMULE_MAX_SOURCES_PER_FILE:-200}
MAX_CONN_5SEC=${AMULE_MAX_CONN_PER_5SEC:-15}
DL_CAPACITY=${AMULE_DOWNLOAD_CAPACITY:-300}
UL_CAPACITY=${AMULE_UPLOAD_CAPACITY:-80}
SLOT_ALLOC=${AMULE_SLOT_ALLOCATION:-30}

reset_cron_file() {
    cat > "$CRON_FILE" <<'CRONEOF'
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
CRONEOF
}

add_cron_job() {
    schedule="$1"
    name="$2"
    command="$3"
    printf "%s root . /etc/environment; %s # %s\n" "$schedule" "$command" "$name" >> "$CRON_FILE"
    CRON_HAS_JOBS=1
}

mod_auto_restart() {
    MOD_AUTO_RESTART_ENABLED=${MOD_AUTO_RESTART_ENABLED:-"false"}
    MOD_AUTO_RESTART_CRON=${MOD_AUTO_RESTART_CRON:-"0 6 * * *"}
    if [ "$MOD_AUTO_RESTART_ENABLED" = "true" ]; then
        printf "[MOD] Auto-restart activé (cron: %s)\n" "$MOD_AUTO_RESTART_CRON"
        add_cron_job "$MOD_AUTO_RESTART_CRON" "MOD_AUTO_RESTART" "/bin/sh -c 'echo \"[MOD] Redémarrage aMule...\" && pkill -x amuled || true'"
    fi
}

mod_fix_kad_graph() {
    MOD_FIX_KAD_GRAPH_ENABLED=${MOD_FIX_KAD_GRAPH_ENABLED:-"false"}
    if [ "$MOD_FIX_KAD_GRAPH_ENABLED" = "true" ]; then
        printf "[MOD] Fix Kad graph activé\n"
        sed -i 's/amule_stats_kad.png//g' /usr/share/amule/webserver/default/amuleweb-main-kad.php 2>/dev/null || true
        sed -i 's/amule_stats_kad.png//g' /usr/share/amule/webserver/AmuleWebUI-Reloaded/amuleweb-main-kad.php 2>/dev/null || true
        sed -i 's/amule_stats_kad.png//g' /usr/share/amule/webserver/AmuleWebUI-Reloaded/amuleweb-main-stats.php 2>/dev/null || true
    fi
}

mod_fix_kad_bootstrap() {
    MOD_FIX_KAD_BOOTSTRAP_ENABLED=${MOD_FIX_KAD_BOOTSTRAP_ENABLED:-"true"}
    if [ "$MOD_FIX_KAD_BOOTSTRAP_ENABLED" = "true" ] && [ ! -f "${AMULE_HOME}/nodes.dat" ]; then
        printf "[MOD] Téléchargement nodes.dat...\n"
        curl -fsSL --retry 3 --max-time 30 -o "${AMULE_HOME}/nodes.dat" "$KAD_NODES_DAT_URL" \
            && printf "[MOD] nodes.dat téléchargé avec succès\n" \
            || printf "[MOD] ERREUR: impossible de télécharger nodes.dat\n"
        chown "${AMULE_UID}:${AMULE_GID}" "${AMULE_HOME}/nodes.dat" 2>/dev/null || true
    fi
}

mod_auto_share() {
    MOD_AUTO_SHARE_ENABLED=${MOD_AUTO_SHARE_ENABLED:-"false"}
    MOD_AUTO_SHARE_DIRECTORIES=${MOD_AUTO_SHARE_DIRECTORIES:-"/incoming"}
    if [ "$MOD_AUTO_SHARE_ENABLED" = "true" ]; then
        printf "[MOD] Auto-share activé: %s\n" "$MOD_AUTO_SHARE_DIRECTORIES"
        SHAREDDIR_CONF="${AMULE_HOME}/shareddir.dat"
        SHAREDDIR_TMP="${SHAREDDIR_CONF}.tmp"
        printf "%s\n" "${AMULE_INCOMING}" > "$SHAREDDIR_TMP"
        OLDIFS=$IFS
        IFS=';'
        set -- $MOD_AUTO_SHARE_DIRECTORIES
        IFS=$OLDIFS
        for raw_dir in "$@"; do
            dir=$(printf '%s' "$raw_dir" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
            [ -z "$dir" ] && continue
            if [ -d "$dir" ]; then
                find "$dir" -type d >> "$SHAREDDIR_TMP"
            fi
        done
        sort -u "$SHAREDDIR_TMP" > "$SHAREDDIR_CONF"
        rm -f "$SHAREDDIR_TMP"
        chown "${AMULE_UID}:${AMULE_GID}" "$SHAREDDIR_CONF"
        chmod 444 "$SHAREDDIR_CONF"
    fi
}

mod_file_organizer() {
    FILE_ORGANIZER_ENABLED=${FILE_ORGANIZER_ENABLED:-"false"}
    FILE_ORGANIZER_CRON=${FILE_ORGANIZER_CRON:-"*/10 * * * *"}
    if [ "$FILE_ORGANIZER_ENABLED" = "true" ]; then
        printf "[MOD] Organisateur de fichiers activé (cron: %s)\n" "$FILE_ORGANIZER_CRON"
        add_cron_job "$FILE_ORGANIZER_CRON" "file-organizer" "/opt/scripts/file-organizer.sh >> /var/log/file-organizer.log 2>&1"
    fi
}

mod_server_update() {
    SERVER_UPDATE_ENABLED=${SERVER_UPDATE_ENABLED:-"false"}
    SERVER_UPDATE_CRON=${SERVER_UPDATE_CRON:-"0 4 * * *"}
    if [ "$SERVER_UPDATE_ENABLED" = "true" ]; then
        printf "[MOD] Mise à jour auto serveurs activée (cron: %s)\n" "$SERVER_UPDATE_CRON"
        add_cron_job "$SERVER_UPDATE_CRON" "update-servers" "/opt/scripts/update-servers.sh >> /var/log/server-update.log 2>&1"
        /opt/scripts/update-servers.sh || true
    fi
}

mod_backup() {
    BACKUP_ENABLED=${BACKUP_ENABLED:-"false"}
    BACKUP_CRON=${BACKUP_CRON:-"0 3 * * 0"}
    if [ "$BACKUP_ENABLED" = "true" ]; then
        printf "[MOD] Backup config activé (cron: %s)\n" "$BACKUP_CRON"
        add_cron_job "$BACKUP_CRON" "backup-config" "/opt/scripts/backup-config.sh >> /var/log/backup.log 2>&1"
    fi
}

# ═══════════════════════════════════════════
# NEW: Kad health monitor — reconnects Kad if it drops
# ═══════════════════════════════════════════
mod_kad_monitor() {
    printf "[MOD] Kad monitor activé (toutes les 15 min)\n"
    add_cron_job "*/15 * * * *" "kad-monitor" "/opt/scripts/kad-monitor.sh >> /var/log/kad-monitor.log 2>&1"
}

# ═══════════════════════════════════════════
# NEW: Periodic server source scanner (every 24h)
# ═══════════════════════════════════════════
mod_source_scanner() {
    printf "[MOD] Server source scanner activé (toutes les 24h)\n"
    add_cron_job "0 */24 * * *" "source-scanner" "/opt/scripts/source-scanner.sh >> /var/log/source-scanner.log 2>&1"
}

# ═══════════════════════════════════════════
# NEW: Init persistent settings file
# ═══════════════════════════════════════════
init_settings() {
    SETTINGS_FILE="${AMULE_HOME}/dashboard-settings.json"
    if [ ! -f "$SETTINGS_FILE" ]; then
        cat > "$SETTINGS_FILE" << 'SETTINGS_EOF'
{
  "server_sources": [
    {"key":"official","label":"eMule Security (officiel)","kind":"serverlist","url":"http://upd.emule-security.org/server.met","priority":300,"enabled":true},
    {"key":"peerates","label":"Peerates","kind":"serverlist","url":"http://edk.peerates.net/servers/best/server.met","priority":200,"enabled":true},
    {"key":"flyernet","label":"FlyerNet","kind":"html","url":"http://flyernet.fr.st.free.fr/ip_serveurs.php","priority":100,"enabled":true}
  ],
  "nodes_sources": [
    {"key":"emule-security","url":"http://upd.emule-security.org/nodes.dat","enabled":true}
  ],
  "ipfilter_url": "http://upd.emule-security.org/ipfilter.zip",
  "scan_interval_hours": 24,
  "kad_auto_reconnect": true,
  "auto_organize_enabled": true,
  "last_scan": null
}
SETTINGS_EOF
        chown "${AMULE_UID}:${AMULE_GID}" "$SETTINGS_FILE"
        printf "[SETTINGS] Fichier de paramètres initialisé\n"
    else
        printf "[SETTINGS] Fichier de paramètres existant\n"
    fi
    export SETTINGS_FILE
}

start_dashboard() {
    DASHBOARD_ENABLED=${DASHBOARD_ENABLED:-"false"}
    DASHBOARD_PORT=${DASHBOARD_PORT:-8078}

    # ── Write EC credentials file (used by dashboard + cron scripts) ──
    EC_CRED_FILE="${AMULE_HOME}/.ec_credentials"
    cat > "$EC_CRED_FILE" << CREDEOF
EC_HOST=localhost
EC_PORT=4712
EC_PASSWORD=${AMULE_GUI_PWD}
EC_PASSWORD_HASH=${AMULE_GUI_ENCODED_PWD}
CREDEOF
    chmod 600 "$EC_CRED_FILE"
    chown "${AMULE_UID}:${AMULE_GID}" "$EC_CRED_FILE" 2>/dev/null || true
    printf "[CREDENTIALS] EC password written to %s\n" "$EC_CRED_FILE"

    if [ "$DASHBOARD_ENABLED" = "true" ]; then
        printf "[DASHBOARD] Démarrage du dashboard sur le port %s...\n" "$DASHBOARD_PORT"
        export AMULE_HOME
        export INCOMING_DIR="$AMULE_INCOMING"
        export TEMP_DIR="$AMULE_TEMP"
        export AMULE_EC_HOST="localhost"
        export AMULE_EC_PORT="4712"
        export AMULE_EC_PASSWORD="${AMULE_GUI_PWD}"
        export AMULE_EC_PASSWORD_HASH="${AMULE_GUI_ENCODED_PWD}"
        export DASHBOARD_PORT
        export DASHBOARD_PWD="${DASHBOARD_PWD:-${WEBUI_PWD:-admin}}"
        export SETTINGS_FILE="${AMULE_HOME}/dashboard-settings.json"
        python3 /opt/dashboard/server.py &
        DASHBOARD_PID=$!
        printf "[DASHBOARD] PID: %s\n" "$DASHBOARD_PID"
    fi

    # ── /etc/environment for cron scripts ──
    {
        printf 'AMULE_EC_HOST=localhost\n'
        printf 'AMULE_EC_PORT=4712\n'
        printf 'AMULE_EC_PASSWORD=%s\n' "${AMULE_GUI_PWD}"
        printf 'AMULE_EC_PASSWORD_HASH=%s\n' "${AMULE_GUI_ENCODED_PWD}"
        printf 'AMULE_HOME=%s\n' "${AMULE_HOME}"
        printf 'INCOMING_DIR=%s\n' "${AMULE_INCOMING}"
        printf 'SETTINGS_FILE=%s\n' "${AMULE_HOME}/dashboard-settings.json"
    } > /etc/environment
}

AMULE_GROUP="amule"
if getent group "$AMULE_GID" >/dev/null 2>&1; then
    AMULE_GROUP=$(getent group "$AMULE_GID" | cut -d: -f1)
else
    groupadd -o -g "$AMULE_GID" "$AMULE_GROUP"
fi

AMULE_USER="amule"
if getent passwd "$AMULE_UID" >/dev/null 2>&1; then
    AMULE_USER=$(getent passwd "$AMULE_UID" | cut -d: -f1)
else
    useradd -o -u "$AMULE_UID" -g "$AMULE_GROUP" -d /home/amule -M -N -s /usr/sbin/nologin "$AMULE_USER"
fi

mkdir -p /home/amule

for dir in "$AMULE_INCOMING" "$AMULE_TEMP" "$AMULE_HOME" "/backups"; do
    [ ! -d "$dir" ] && mkdir -p "$dir"
done

if [ "${FILE_ORGANIZER_ENABLED:-false}" = "true" ]; then
    for subdir in Video Audio Images Documents Archives Software Other; do
        mkdir -p "${AMULE_INCOMING}/${subdir}"
    done
fi

if [ -z "${GUI_PWD:-}" ]; then
    AMULE_GUI_PWD=$(pwgen -s 14)
else
    AMULE_GUI_PWD="$GUI_PWD"
fi
AMULE_GUI_ENCODED_PWD=$(printf "%s" "$AMULE_GUI_PWD" | md5sum | cut -d ' ' -f 1)

if [ -z "${WEBUI_PWD:-}" ]; then
    AMULE_WEBUI_PWD=$(pwgen -s 14)
else
    AMULE_WEBUI_PWD="$WEBUI_PWD"
fi
AMULE_WEBUI_ENCODED_PWD=$(printf "%s" "$AMULE_WEBUI_PWD" | md5sum | cut -d ' ' -f 1)

if [ ! -f "$AMULE_CONF" ]; then
    printf "━━━ Mots de passe générés ━━━\n"
    printf "  GUI:    %s\n" "$AMULE_GUI_PWD"
    printf "  WebUI:  %s\n" "$AMULE_WEBUI_PWD"
    printf "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

    cat > "$AMULE_CONF" << CONFEOF
[eMule]
AppVersion=2.3.3
Nick=aMule-ZimaBoard
QueueSizePref=50
MaxUpload=0
MaxDownload=0
SlotAllocation=${SLOT_ALLOC}
Port=4662
UDPPort=4672
UDPEnable=1
Address=
Autoconnect=1
MaxSourcesPerFile=${MAX_SOURCES}
MaxConnections=${MAX_CONNECTIONS}
MaxConnectionsPerFiveSeconds=${MAX_CONN_5SEC}
RemoveDeadServer=1
DeadServerRetry=2
ServerKeepAliveTimeout=0
Reconnect=1
Scoresystem=1
Serverlist=0
AddServerListFromServer=0
AddServerListFromClient=0
SafeServerConnect=1
AutoConnectStaticOnly=0
UPnPEnabled=0
SmartIdCheck=1
ConnectToKad=1
ConnectToED2K=1
TempDir=${AMULE_TEMP}
IncomingDir=${AMULE_INCOMING}
ICH=1
AICHTrust=1
CheckDiskspace=1
MinFreeDiskSpace=1
AddNewFilesPaused=0
PreviewPrio=0
ManualHighPrio=0
StartNextFile=1
StartNextFileSameCat=0
StartNextFileAlpha=0
FileBufferSizePref=1400
DAPPref=1
UAPPref=1
AllocateFullFile=0
OSDirectory=${AMULE_HOME}
OnlineSignature=1
OnlineSignatureUpdate=5
EnableTrayIcon=0
MinToTray=0
ConfirmExit=1
StartupMinimized=0
3DDepth=10
ToolTipDelay=1
ShowOverhead=0
ShowInfoOnCatTabs=1
VerticalToolbar=0
GeoIPEnabled=1
VideoPlayer=
StatGraphsInterval=3
statsInterval=30
DownloadCapacity=${DL_CAPACITY}
UploadCapacity=${UL_CAPACITY}
StatsAverageMinutes=5
VariousStatisticsMaxValue=100
SeeShare=2
FilterLanIPs=1
ParanoidFiltering=1
IPFilterAutoLoad=1
IPFilterURL=${IPFILTER_URL}
FilterLevel=127
IPFilterSystem=1
FilterMessages=1
FilterAllMessages=0
MessagesFromFriendsOnly=0
MessageFromValidSourcesOnly=1
FilterWordMessages=1
MessageFilter=
ShowMessagesInLog=1
FilterComments=0
CommentFilter=
ShareHiddenFiles=0
AutoSortDownloads=0
NewVersionCheck=0
AdvancedSpamFilter=1
MessageUseCaptchas=1
Language=fr_FR.UTF-8
DateTimeFormat=%A, %x, %X
KadNodesUrl=${KAD_NODES_DAT_URL}
Ed2kServersUrl=${SERVER_MET_URL}
CreateSparseFiles=1
[Browser]
OpenPageInTab=1
CustomBrowserString=
[Proxy]
ProxyEnableProxy=0
ProxyType=0
ProxyName=
ProxyPort=1080
ProxyEnablePassword=0
ProxyUser=
ProxyPassword=
[ExternalConnect]
UseSrcSeeds=0
AcceptExternalConnections=1
ECAddress=
ECPort=4712
ECPassword=${AMULE_GUI_ENCODED_PWD}
UPnPECEnabled=0
ShowProgressBar=1
ShowPercent=1
UseSecIdent=1
IpFilterClients=1
IpFilterServers=1
TransmitOnlyUploadingClients=0
[WebServer]
Enabled=1
Password=${AMULE_WEBUI_ENCODED_PWD}
PasswordLow=
Port=4711
UPnPWebServerEnabled=0
UseGzip=1
UseLowRightsUser=0
PageRefreshTime=120
Template=AmuleWebUI-Reloaded
Path=amuleweb
[GUI]
HideOnClose=0
[Razor_Preferences]
FastED2KLinksHandler=1
[SkinGUIOptions]
Skin=
[Statistics]
MaxClientVersions=0
[Obfuscation]
IsClientCryptLayerSupported=1
IsCryptLayerRequested=1
IsClientCryptLayerRequired=1
CryptoPaddingLenght=254
CryptoKadUDPKey=$(od -An -tu4 -N4 /dev/urandom | tr -d ' ')
[PowerManagement]
PreventSleepWhileDownloading=0
[UserEvents]
[UserEvents/DownloadCompleted]
CoreEnabled=0
CoreCommand=
GUIEnabled=0
GUICommand=
[UserEvents/NewChatSession]
CoreEnabled=0
CoreCommand=
GUIEnabled=0
GUICommand=
[UserEvents/OutOfDiskSpace]
CoreEnabled=0
CoreCommand=
GUIEnabled=0
GUICommand=
[UserEvents/ErrorOnCompletion]
CoreEnabled=0
CoreCommand=
GUIEnabled=0
GUICommand=
[HTTPDownload]
URL_1=${IPFILTER_URL}
CONFEOF

    printf "[CONFIG] amule.conf généré\n"
else
    printf "[CONFIG] amule.conf existant, utilisation de la config actuelle\n"
fi

if [ ! -f "$REMOTE_CONF" ]; then
    cat > "$REMOTE_CONF" << REMEOF
Locale=
[EC]
Host=localhost
Port=4712
Password=${AMULE_GUI_ENCODED_PWD}
[Webserver]
Port=4711
UPnPWebServerEnabled=0
UPnPTCPPort=50001
Template=AmuleWebUI-Reloaded
UseGzip=1
AllowGuest=0
AdminPassword=${AMULE_WEBUI_ENCODED_PWD}
GuestPassword=
REMEOF
    printf "[CONFIG] remote.conf généré\n"
else
    printf "[CONFIG] remote.conf existant\n"
fi

# Replace passwords using awk to target the correct sections
if [ -n "${GUI_PWD:-}" ]; then
    sed -i "s/^ECPassword=.*/ECPassword=${AMULE_GUI_ENCODED_PWD}/" "$AMULE_CONF"
    # In remote.conf [EC] section
    sed -i "s/^Password=.*/Password=${AMULE_GUI_ENCODED_PWD}/" "$REMOTE_CONF"
fi
if [ -n "${WEBUI_PWD:-}" ]; then
    # Only replace Password= inside [WebServer] section of amule.conf
    awk -v pwd="${AMULE_WEBUI_ENCODED_PWD}" '
        /^\[WebServer\]/{s=1} /^\[/{if(!/^\[WebServer\]/)s=0}
        s && /^Password=/{$0="Password="pwd} {print}
    ' "$AMULE_CONF" > "${AMULE_CONF}.tmp" && mv "${AMULE_CONF}.tmp" "$AMULE_CONF"
    sed -i "s|^AdminPassword=.*|AdminPassword=${AMULE_WEBUI_ENCODED_PWD}|" "$REMOTE_CONF"
fi

chown -R "${AMULE_UID}:${AMULE_GID}" "$AMULE_INCOMING"
chown -R "${AMULE_UID}:${AMULE_GID}" "$AMULE_TEMP"
chown -R "${AMULE_UID}:${AMULE_GID}" "$AMULE_HOME"
chown -R "${AMULE_UID}:${AMULE_GID}" "/backups" 2>/dev/null || true

reset_cron_file
mod_auto_restart
mod_fix_kad_graph
mod_fix_kad_bootstrap
mod_file_organizer
mod_server_update
mod_backup
mod_kad_monitor
mod_source_scanner
init_settings

if [ "$CRON_HAS_JOBS" -eq 1 ]; then
    chmod 0644 "$CRON_FILE"
    cron
fi

start_dashboard

printf "\n[AMULE] Démarrage d'aMule...\n\n"

while true; do
    mod_auto_share
    gosu "${AMULE_UID}:${AMULE_GID}" amuled -c "${AMULE_HOME}" -o
    EXIT_CODE=$?
    if [ "$EXIT_CODE" -eq 0 ]; then
        printf "[MOD] Redémarrage d'aMule...\n"
    else
        printf "[AMULE] Arrêt avec code: %d\n" "$EXIT_CODE"
        break
    fi
done
exit "$EXIT_CODE"
