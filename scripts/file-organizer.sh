#!/bin/sh
# ╔══════════════════════════════════════════╗
# ║  File Organizer — Tri auto par type      ║
# ╚══════════════════════════════════════════╝

INCOMING="${INCOMING_DIR:-/incoming}"
LOG_PREFIX="[FILE-ORG]"

# Catégories et extensions
is_video() { echo "$1" | grep -qiE '\.(mkv|avi|mp4|mov|wmv|flv|m4v|ts|mpg|mpeg|webm|vob|3gp|ogv)$'; }
is_audio() { echo "$1" | grep -qiE '\.(mp3|flac|ogg|wav|aac|wma|m4a|opus|ape|alac)$'; }
is_image() { echo "$1" | grep -qiE '\.(jpg|jpeg|png|gif|bmp|svg|webp|tiff|tif|ico|raw)$'; }
is_document() { echo "$1" | grep -qiE '\.(pdf|doc|docx|xls|xlsx|ppt|pptx|txt|epub|mobi|odt|ods|rtf|csv)$'; }
is_archive() { echo "$1" | grep -qiE '\.(zip|rar|7z|tar|gz|bz2|xz|tgz|cab|ace)$'; }
is_software() { echo "$1" | grep -qiE '\.(iso|img|exe|msi|dmg|deb|rpm|apk|app|bin|nrg)$'; }

get_category() {
    filename="$1"
    if is_video "$filename"; then echo "Video"
    elif is_audio "$filename"; then echo "Audio"
    elif is_image "$filename"; then echo "Images"
    elif is_document "$filename"; then echo "Documents"
    elif is_archive "$filename"; then echo "Archives"
    elif is_software "$filename"; then echo "Software"
    else echo "Other"
    fi
}

# Création des sous-dossiers
for dir in Video Audio Images Documents Archives Software Other; do
    mkdir -p "${INCOMING}/${dir}"
done

# Trier les fichiers à la racine de /incoming
moved=0
find "${INCOMING}" -maxdepth 1 -type f | while read -r filepath; do
    filename=$(basename "$filepath")
    category=$(get_category "$filename")
    target="${INCOMING}/${category}/${filename}"

    if [ ! -f "$target" ]; then
        mv "$filepath" "$target"
        printf "%s %s → %s/\n" "$LOG_PREFIX" "$filename" "$category"
        moved=$((moved + 1))
    fi
done

printf "%s Terminé. Date: %s\n" "$LOG_PREFIX" "$(date '+%Y-%m-%d %H:%M')"
