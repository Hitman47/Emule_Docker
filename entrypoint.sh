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
DOWNLOADS_DIR=${DOWNLOADS_DIR:-"/downloads"}
AMULE_INCOMING=${INCOMING_DIR:-"${DOWNLOADS_DIR}"}
AMULE_TEMP=${TEMP_DIR:-"/temp"}
AMULE_HOME=/home/amule/.aMule
AMULE_CONF=${AMULE_HOME}/amule.conf
REMOTE_CONF=${AMULE_HOME}/remote.conf
KAD_NODES_DAT_URL="http://upd.emule-security.org/nodes.dat"
SERVER_MET_URL="http://upd.emule-security.org/server.met"
IPFILTER_URL="http://upd.emule-security.org/ipfilter.zip"
CRON_FILE="/etc/cron.d/amule"
CRON_HAS_JOBS=0

path_starts_with() {
    parent="$1"
    child="$2"
    case "${child}/" in
        "${parent}/"*) return 0 ;;
        *) return 1 ;;
    esac
}

validate_download_paths() {
    if [ "$AMULE_INCOMING" = "$AMULE_TEMP" ]; then
        printf "[PATHS] IncomingDir et TempDir identiques (%s) — TempDir forcé vers /temp\n" "$AMULE_TEMP"
        AMULE_TEMP="/temp"
    fi

    if path_starts_with "$AMULE_INCOMING" "$AMULE_TEMP"; then
        printf "[PATHS] TempDir (%s) est imbriqué dans IncomingDir (%s) — TempDir forcé vers /temp\n" "$AMULE_TEMP" "$AMULE_INCOMING"
        AMULE_TEMP="/temp"
    fi
}

update_ini_value() {
    file="$1"
    section="$2"
    key="$3"
    value="$4"
    tmp="${file}.tmp"

    awk -v section="$section" -v key="$key" -v value="$value" '
        BEGIN { in_section=0; done=0 }
        /^\[/ {
            if (in_section && !done) {
                print key "=" value
                done=1
            }
            in_section = ($0 == "[" section "]")
        }
        {
            if (in_section && $0 ~ ("^" key "=")) {
                if (!done) {
                    print key "=" value
                    done=1
                }
                next
            }
            print
        }
        END {
            if (in_section && !done) {
                print key "=" value
            }
        }
    ' "$file" > "$tmp" && mv "$tmp" "$file"
}

# ── Performance tuning (Low ID optimized) ──
MAX_CONNECTIONS=${AMULE_MAX_CONNECTIONS:-800}
MAX_SOURCES=${AMULE_MAX_SOURCES_PER_FILE:-800}
MAX_CONN_5SEC=${AMULE_MAX_CONN_PER_5SEC:-60}
DL_CAPACITY=${AMULE_DOWNLOAD_CAPACITY:-300}
UL_CAPACITY=${AMULE_UPLOAD_CAPACITY:-80}
SLOT_ALLOC=${AMULE_SLOT_ALLOCATION:-20}

validate_download_paths

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
    MOD_AUTO_SHARE_DIRECTORIES=${MOD_AUTO_SHARE_DIRECTORIES:-"${DOWNLOADS_DIR}"}
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
# NEW: Stall detector — changes server if no DL progress for 30min
# ═══════════════════════════════════════════
mod_stall_detector() {
    printf "[MOD] Stall detector activé (toutes les 5 min)\n"
    add_cron_job "*/5 * * * *" "stall-detector" "/opt/scripts/stall-detector.sh >> /var/log/amule-diag/stall-detector.log 2>&1"
}

# ═══════════════════════════════════════════
# NEW: Source Hunter — aggressive source finding for Low ID
# ═══════════════════════════════════════════
mod_source_hunter() {
    printf "[MOD] Source Hunter activé (toutes les 10 min)\n"
    add_cron_job "*/10 * * * *" "source-hunter" "/opt/scripts/source-hunter.sh >> /var/log/amule-diag/source-hunter.log 2>&1"
}

# ═══════════════════════════════════════════
# NEW: Connectivity diagnostic logger (every 3min)
# ═══════════════════════════════════════════
mod_connectivity_diag() {
    printf "[MOD] Connectivity diagnostic activé (toutes les 3 min)\n"
    mkdir -p /var/log/amule-diag
    add_cron_job "*/3 * * * *" "connectivity-diag" "/opt/scripts/connectivity-diag.sh 2>&1"
}

# ═══════════════════════════════════════════
# NEW: VPN port forwarding auto-detect (every 10min)
# ═══════════════════════════════════════════
mod_port_forward() {
    printf "[MOD] VPN port forward detection activé (toutes les 10 min)\n"
    add_cron_job "*/10 * * * *" "port-forward" "/opt/scripts/port-forward-detect.sh >> /var/log/amule-diag/port-forward.log 2>&1"
}

# ═══════════════════════════════════════════
# NEW: Source Boost — Low ID download optimizer
# ═══════════════════════════════════════════
mod_source_boost() {
    printf "[MOD] Source Boost activé (toutes les 10 min)\n"
    add_cron_job "*/10 * * * *" "source-boost" "/opt/scripts/source-boost.sh 2>&1"
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
        printf 'DOWNLOADS_DIR=%s\n' "${DOWNLOADS_DIR}"
        printf 'INCOMING_DIR=%s\n' "${AMULE_INCOMING}"
        printf 'TEMP_DIR=%s\n' "${AMULE_TEMP}"
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

for dir in "$DOWNLOADS_DIR" "$AMULE_INCOMING" "$AMULE_TEMP" "$AMULE_HOME" "/backups"; do
    [ ! -d "$dir" ] && mkdir -p "$dir"
done


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
ServerKeepAliveTimeout=300
Reconnect=1
Scoresystem=1
Serverlist=1
AddServerListFromServer=1
AddServerListFromClient=1
SafeServerConnect=0
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
FileBufferSizePref=524288
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
CreateSparseFiles=0
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
UseSrcSeeds=1
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
IsCryptLayerRequested=0
IsClientCryptLayerRequired=0
CryptoPaddingLenght=128
CryptoKadUDPKey=$(od -An -tu4 -N4 /dev/urandom | tr -d ' ')
[PowerManagement]
PreventSleepWhileDownloading=0
[UserEvents]
[UserEvents/DownloadCompleted]
CoreEnabled=1
CoreCommand=/opt/scripts/on-download-complete.sh "%FILE" "%NAME" "%HASH" "%SIZE"
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

# ═══════════════════════════════════════════
# FORCE password update (always, even if conf existed)
# ═══════════════════════════════════════════
printf "\n━━━ Password synchronization ━━━\n"
printf "  GUI_PWD env set: %s\n" "$([ -n "${GUI_PWD:-}" ] && echo 'yes' || echo 'no (auto-generated)')"
printf "  Plain password:  %s***%s\n" "$(echo "$AMULE_GUI_PWD" | head -c2)" "$(echo "$AMULE_GUI_PWD" | tail -c3)"
printf "  MD5 hash:        %s\n" "$AMULE_GUI_ENCODED_PWD"

# Read what's currently in amule.conf
CURRENT_EC_HASH=$(grep '^ECPassword=' "$AMULE_CONF" 2>/dev/null | head -1 | cut -d= -f2)
printf "  Conf ECPassword: %s\n" "${CURRENT_EC_HASH:-'(none)'}"

if [ "$CURRENT_EC_HASH" != "$AMULE_GUI_ENCODED_PWD" ]; then
    printf "  [!] MISMATCH — updating amule.conf and remote.conf\n"

    # Force update ECPassword in amule.conf [ExternalConnect] section
    awk -v hash="$AMULE_GUI_ENCODED_PWD" '
        /^\[ExternalConnect\]/{s=1}
        /^\[/{if(!/^\[ExternalConnect\]/)s=0}
        s && /^ECPassword=/{$0="ECPassword="hash}
        {print}
    ' "$AMULE_CONF" > "${AMULE_CONF}.tmp" && mv "${AMULE_CONF}.tmp" "$AMULE_CONF"

    # Force update Password in remote.conf [EC] section
    awk -v hash="$AMULE_GUI_ENCODED_PWD" '
        /^\[EC\]/{s=1}
        /^\[/{if(!/^\[EC\]/)s=0}
        s && /^Password=/{$0="Password="hash}
        {print}
    ' "$REMOTE_CONF" > "${REMOTE_CONF}.tmp" && mv "${REMOTE_CONF}.tmp" "$REMOTE_CONF"

    # Verify it took
    NEW_HASH=$(grep '^ECPassword=' "$AMULE_CONF" 2>/dev/null | head -1 | cut -d= -f2)
    if [ "$NEW_HASH" = "$AMULE_GUI_ENCODED_PWD" ]; then
        printf "  [✓] ECPassword updated successfully\n"
    else
        printf "  [✗] UPDATE FAILED — got '%s', expected '%s'\n" "$NEW_HASH" "$AMULE_GUI_ENCODED_PWD"
        printf "  [!] Brute-force rewrite...\n"
        sed -i "s/^ECPassword=.*/ECPassword=${AMULE_GUI_ENCODED_PWD}/" "$AMULE_CONF"
    fi
else
    printf "  [✓] ECPassword already matches\n"
fi

# Update WebUI password in amule.conf [WebServer] section
if [ -n "${WEBUI_PWD:-}" ]; then
    awk -v pwd="${AMULE_WEBUI_ENCODED_PWD}" '
        /^\[WebServer\]/{s=1} /^\[/{if(!/^\[WebServer\]/)s=0}
        s && /^Password=/{$0="Password="pwd} {print}
    ' "$AMULE_CONF" > "${AMULE_CONF}.tmp" && mv "${AMULE_CONF}.tmp" "$AMULE_CONF"
    sed -i "s|^AdminPassword=.*|AdminPassword=${AMULE_WEBUI_ENCODED_PWD}|" "$REMOTE_CONF"
fi
printf "  IncomingDir:     %s\n" "$AMULE_INCOMING"
printf "  TempDir:         %s\n" "$AMULE_TEMP"
update_ini_value "$AMULE_CONF" "eMule" "IncomingDir" "$AMULE_INCOMING"
update_ini_value "$AMULE_CONF" "eMule" "TempDir" "$AMULE_TEMP"
printf "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

# ═══════════════════════════════════════════
# FORCE-FIX: Enable server list in existing configs
# (critical for ED2K auto-connect)
# ═══════════════════════════════════════════
printf "━━━ Server list fix ━━━\n"
sed -i 's/^Serverlist=0/Serverlist=1/' "$AMULE_CONF" 2>/dev/null
sed -i 's/^AddServerListFromServer=0/AddServerListFromServer=1/' "$AMULE_CONF" 2>/dev/null
sed -i 's/^AddServerListFromClient=0/AddServerListFromClient=1/' "$AMULE_CONF" 2>/dev/null
sed -i 's/^SafeServerConnect=1/SafeServerConnect=0/' "$AMULE_CONF" 2>/dev/null

# ── FORCE-FIX: Obfuscation too restrictive kills peer discovery ──
printf "━━━ Obfuscation fix ━━━\n"
sed -i 's/^IsClientCryptLayerRequired=1/IsClientCryptLayerRequired=0/' "$AMULE_CONF" 2>/dev/null
sed -i 's/^IsCryptLayerRequested=1/IsCryptLayerRequested=0/' "$AMULE_CONF" 2>/dev/null
CRYPT_REQ=$(grep '^IsClientCryptLayerRequired=' "$AMULE_CONF" | head -1 | cut -d= -f2)
printf "  IsClientCryptLayerRequired=%s (should be 0)\n" "${CRYPT_REQ:-?}"

# ── FORCE-FIX: Connection limits for better source discovery ──
printf "━━━ Connection limits fix ━━━\n"
sed -i "s/^MaxSourcesPerFile=.*/MaxSourcesPerFile=${MAX_SOURCES}/" "$AMULE_CONF" 2>/dev/null
sed -i "s/^MaxConnections=.*/MaxConnections=${MAX_CONNECTIONS}/" "$AMULE_CONF" 2>/dev/null
sed -i "s/^MaxConnectionsPerFiveSeconds=.*/MaxConnectionsPerFiveSeconds=${MAX_CONN_5SEC}/" "$AMULE_CONF" 2>/dev/null
printf "  MaxSourcesPerFile=%s MaxConnections=%s MaxConn5s=%s\n" "$MAX_SOURCES" "$MAX_CONNECTIONS" "$MAX_CONN_5SEC"

# ── FORCE-FIX: Buffer and source persistence ──
printf "━━━ Buffer & source fixes ━━━\n"
sed -i 's/^FileBufferSizePref=.*/FileBufferSizePref=524288/' "$AMULE_CONF" 2>/dev/null
sed -i 's/^ServerKeepAliveTimeout=0/ServerKeepAliveTimeout=300/' "$AMULE_CONF" 2>/dev/null
sed -i 's/^UseSrcSeeds=0/UseSrcSeeds=1/' "$AMULE_CONF" 2>/dev/null
printf "  FileBufferSizePref=524288 ServerKeepAlive=300 UseSrcSeeds=1\n"

# ── FORCE-FIX: Sparse files break on Docker overlay2 ──
sed -i 's/^CreateSparseFiles=1/CreateSparseFiles=0/' "$AMULE_CONF" 2>/dev/null
printf "  CreateSparseFiles=0 (overlay2 fix)\n"

# ── FORCE-FIX: Low ID optimization — maximize source exchange ──
printf "━━━ Low ID source optimization ━━━\n"
# Reconnect aggressively when disconnected
sed -i 's/^Reconnect=0/Reconnect=1/' "$AMULE_CONF" 2>/dev/null
# Accept sources from servers and other clients
sed -i 's/^AddServerListFromServer=0/AddServerListFromServer=1/' "$AMULE_CONF" 2>/dev/null
sed -i 's/^AddServerListFromClient=0/AddServerListFromClient=1/' "$AMULE_CONF" 2>/dev/null
# Smart Low ID — let aMule handle Low ID reconnect logic
sed -i 's/^SmartIdCheck=0/SmartIdCheck=1/' "$AMULE_CONF" 2>/dev/null
# Source exchange: accept sources from downloading clients (critical for Low ID)
# ICH = Intelligent Corruption Handling
sed -i 's/^ICH=0/ICH=1/' "$AMULE_CONF" 2>/dev/null
sed -i 's/^AICHTrust=0/AICHTrust=1/' "$AMULE_CONF" 2>/dev/null
# Start next file when one completes (keep connections busy)
sed -i 's/^StartNextFile=0/StartNextFile=1/' "$AMULE_CONF" 2>/dev/null
# Use DAP (Download Auto-Priority) — focuses on files with most sources
sed -i 's/^DAPPref=0/DAPPref=1/' "$AMULE_CONF" 2>/dev/null
# Upload Auto-Priority — share more of what others need = better queue position
sed -i 's/^UAPPref=0/UAPPref=1/' "$AMULE_CONF" 2>/dev/null
# Keep online signature updated (helps with source exchange)
sed -i 's/^OnlineSignature=0/OnlineSignature=1/' "$AMULE_CONF" 2>/dev/null
# Higher connection rate for Low ID (need to try more peers)
sed -i "s/^MaxConnectionsPerFiveSeconds=.*/MaxConnectionsPerFiveSeconds=${MAX_CONN_5SEC}/" "$AMULE_CONF" 2>/dev/null
printf "  Reconnect=1 SmartIdCheck=1 ICH=1 AICH=1 StartNextFile=1\n"
printf "  DAPPref=1 UAPPref=1 OnlineSignature=1 MaxConn5s=%s\n" "$MAX_CONN_5SEC"

# ── FORCE-FIX: Enable download completion event ──
if grep -q '^CoreEnabled=0' "$AMULE_CONF" 2>/dev/null; then
    # Replace the DownloadCompleted section
    awk '
        /^\[UserEvents\/DownloadCompleted\]/{sect=1}
        sect && /^CoreEnabled=/{$0="CoreEnabled=1"; sect_done=1}
        sect && /^CoreCommand=/{$0="CoreCommand=/opt/scripts/on-download-complete.sh \"%FILE\" \"%NAME\" \"%HASH\" \"%SIZE\""}
        sect && /^\[/ && !/^\[UserEvents\/DownloadCompleted\]/{sect=0}
        {print}
    ' "$AMULE_CONF" > "${AMULE_CONF}.tmp" && mv "${AMULE_CONF}.tmp" "$AMULE_CONF"
    printf "  UserEvents/DownloadCompleted enabled\n"
fi

# ── FORCE-FIX: TempDir and IncomingDir MUST be on same mount (cross-device rename fix) ──
printf "━━━ Cross-device rename fix ━━━\n"
CURRENT_TEMP=$(grep '^TempDir=' "$AMULE_CONF" 2>/dev/null | head -1 | cut -d= -f2)
CURRENT_INC=$(grep '^IncomingDir=' "$AMULE_CONF" 2>/dev/null | head -1 | cut -d= -f2)
printf "  Current: TempDir=%s IncomingDir=%s\n" "$CURRENT_TEMP" "$CURRENT_INC"

NEED_FIX=0
if [ "$CURRENT_TEMP" != "$AMULE_TEMP" ]; then
    sed -i "s|^TempDir=.*|TempDir=${AMULE_TEMP}|" "$AMULE_CONF"
    NEED_FIX=1
fi
if [ "$CURRENT_INC" != "$AMULE_INCOMING" ]; then
    sed -i "s|^IncomingDir=.*|IncomingDir=${AMULE_INCOMING}|" "$AMULE_CONF"
    NEED_FIX=1
fi
if [ "$NEED_FIX" -eq 1 ]; then
    printf "  [!] Updated: TempDir=%s IncomingDir=%s\n" "$AMULE_TEMP" "$AMULE_INCOMING"
    printf "  [!] Both are now under same mount point to prevent file loss\n"
else
    printf "  [✓] Paths already correct\n"
fi

# Migrate existing files from old paths if they exist (once only)
MIGRATE_MARKER="/downloads/.migrated"
if [ ! -f "$MIGRATE_MARKER" ]; then
  for OLD_DIR in /incoming /temp; do
    if [ -d "$OLD_DIR" ] && [ "$(ls -A "$OLD_DIR" 2>/dev/null)" ]; then
        case "$OLD_DIR" in
            /incoming) TARGET="$AMULE_INCOMING" ;;
            /temp)     TARGET="$AMULE_TEMP" ;;
        esac
        if [ "$OLD_DIR" != "$TARGET" ]; then
            printf "  Migrating %s → %s\n" "$OLD_DIR" "$TARGET"
            mkdir -p "$TARGET"
            cp -an "$OLD_DIR"/* "$TARGET"/ 2>/dev/null || true
        fi
    fi
  done
  touch "$MIGRATE_MARKER"
  printf "  [✓] Migration marker set\n"
else
  printf "  [✓] Already migrated (marker found)\n"
fi

printf "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
SRVLIST_VAL=$(grep '^Serverlist=' "$AMULE_CONF" | head -1 | cut -d= -f2)
printf "  Serverlist=%s\n" "$SRVLIST_VAL"
ADDSRV_VAL=$(grep '^AddServerListFromServer=' "$AMULE_CONF" | head -1 | cut -d= -f2)
printf "  AddServerListFromServer=%s\n" "$ADDSRV_VAL"

# Download server.met BEFORE amuled starts — this is critical
printf "  Téléchargement server.met...\n"
if curl -fsSL --retry 3 --max-time 30 -o "${AMULE_HOME}/server.met.tmp" "http://upd.emule-security.org/server.met" 2>/dev/null; then
    if [ -s "${AMULE_HOME}/server.met.tmp" ]; then
        mv "${AMULE_HOME}/server.met.tmp" "${AMULE_HOME}/server.met"
        chown "${AMULE_UID}:${AMULE_GID}" "${AMULE_HOME}/server.met"
        printf "  [✓] server.met téléchargé (%s octets)\n" "$(wc -c < "${AMULE_HOME}/server.met")"
    else
        rm -f "${AMULE_HOME}/server.met.tmp"
        printf "  [!] server.met vide, ignoré\n"
    fi
else
    rm -f "${AMULE_HOME}/server.met.tmp"
    printf "  [!] Échec téléchargement server.met\n"
fi

# Also ensure Ed2kServersUrl is set
if ! grep -q "^Ed2kServersUrl=" "$AMULE_CONF"; then
    printf "Ed2kServersUrl=http://upd.emule-security.org/server.met\n" >> "$AMULE_CONF"
fi
printf "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

chown -R "${AMULE_UID}:${AMULE_GID}" "$AMULE_INCOMING"
chown -R "${AMULE_UID}:${AMULE_GID}" "$AMULE_TEMP"
chown -R "${AMULE_UID}:${AMULE_GID}" "$AMULE_HOME"
chown -R "${AMULE_UID}:${AMULE_GID}" "/backups" 2>/dev/null || true

reset_cron_file
mod_auto_restart
mod_fix_kad_graph
mod_fix_kad_bootstrap
mod_server_update
mod_backup
mod_kad_monitor
mod_source_scanner
mod_stall_detector
mod_source_hunter
mod_connectivity_diag
mod_port_forward
mod_source_boost
init_settings

if [ "$CRON_HAS_JOBS" -eq 1 ]; then
    chmod 0644 "$CRON_FILE"
    cron
fi

start_dashboard

printf "\n[AMULE] Démarrage d'aMule...\n\n"

# ── Network buffer tuning (applied at runtime for VPN throughput) ──
sysctl -w net.core.rmem_max=4194304 2>/dev/null || true
sysctl -w net.core.wmem_max=4194304 2>/dev/null || true
sysctl -w net.core.rmem_default=262144 2>/dev/null || true
sysctl -w net.core.wmem_default=262144 2>/dev/null || true
sysctl -w net.core.somaxconn=1024 2>/dev/null || true

# ── Start file watcher (tracks all file events in /incoming and /temp) ──
chmod +x /opt/scripts/file-watcher.sh 2>/dev/null || true
/opt/scripts/file-watcher.sh &
printf "[WATCHER] File event watcher started (PID: $!)\n"

# Auto-connect verification (background)
# With Serverlist=1 and server.met downloaded, amuled should auto-connect.
# This just verifies and imports extra servers.
(
    _try_cmd() {
        OUT=$(amulecmd -h localhost -p 4712 -P "${AMULE_GUI_PWD}" -c "$1" 2>&1)
        if echo "$OUT" | grep -qi "wrong password\|Authentication failed"; then
            OUT=$(amulecmd -h localhost -p 4712 -P "${AMULE_GUI_ENCODED_PWD}" -c "$1" 2>&1)
        fi
        echo "$OUT"
    }

    # Wait for EC port
    printf "[AUTO-CONNECT] Attente du port EC...\n"
    for i in $(seq 1 60); do
        if nc -z localhost 4712 2>/dev/null; then
            printf "[AUTO-CONNECT] Port EC prêt (%ds)\n" "$i"
            break
        fi
        sleep 1
    done
    sleep 5

    # Import additional server lists via amulecmd
    printf "[AUTO-CONNECT] Import listes de serveurs supplementaires...\n"
    _try_cmd "add ed2k://|serverlist|http://upd.emule-security.org/server.met|/" >/dev/null 2>&1
    _try_cmd "add ed2k://|serverlist|http://edk.peerates.net/servers/best/server.met|/" >/dev/null 2>&1

    # Check status every 15s for 3 minutes
    for attempt in $(seq 1 12); do
        sleep 15
        STATUS=$(_try_cmd "status")
        ED2K_LINE=$(echo "$STATUS" | grep -i "ed2k\|edonkey" | head -1)
        KAD_LINE=$(echo "$STATUS" | grep -i "kad" | head -1)

        ED2K_OK=0
        echo "$ED2K_LINE" | grep -qi "connected to" && ED2K_OK=1

        KAD_OK=0
        echo "$KAD_LINE" | grep -qi "connected\|running\|firewalled" && KAD_OK=1

        printf "[AUTO-CONNECT] [%d/12] ED2K=%s Kad=%s\n" "$attempt" \
            "$([ $ED2K_OK -eq 1 ] && echo 'OK' || echo 'NO')" \
            "$([ $KAD_OK -eq 1 ] && echo 'OK' || echo 'NO')"

        # Both connected? Done.
        if [ $ED2K_OK -eq 1 ] && [ $KAD_OK -eq 1 ]; then
            printf "[AUTO-CONNECT] Tout est connecte !\n"
            printf "[AUTO-CONNECT] %s\n" "$(echo "$ED2K_LINE" | sed 's/^[> ]*//')"
            printf "[AUTO-CONNECT] %s\n" "$(echo "$KAD_LINE" | sed 's/^[> ]*//')"
            break
        fi

        # If ED2K still not connected after 1 minute, send explicit connect
        if [ $ED2K_OK -eq 0 ] && [ $attempt -eq 4 ]; then
            printf "[AUTO-CONNECT] ED2K toujours deconnecte, envoi connect...\n"
            _try_cmd "connect ed2k" >/dev/null 2>&1
        fi

        # If ED2K still not connected after 2 minutes, try specific server
        if [ $ED2K_OK -eq 0 ] && [ $attempt -eq 8 ]; then
            printf "[AUTO-CONNECT] Tentative serveur specifique...\n"
            _try_cmd "connect 45.82.80.155:5687" >/dev/null 2>&1
        fi
    done

    printf "[AUTO-CONNECT] Termine.\n"

    # Run initial diagnostics
    printf "[AUTO-CONNECT] Lancement diagnostic initial...\n"
    /opt/scripts/port-forward-detect.sh >> /var/log/amule-diag/port-forward.log 2>&1 || true
    /opt/scripts/connectivity-diag.sh 2>&1 || true
    printf "[AUTO-CONNECT] Diagnostic initial terminé.\n"
) &

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
