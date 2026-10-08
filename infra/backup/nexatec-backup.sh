#!/usr/bin/env bash
# Backup diario de las bases de NEXATEC: nexatec_control + cada base de
# tenant nxt_*. NUNCA incluye hesed_ot ni otra base que no sea de NEXATEC
# (lista explicita por nombre, no "todas las bases").
#
# Formato: pg_dump -Fc (comprimido) cifrado con AES-256 (openssl, clave en
# /etc/nexatec/backup.key, solo root). Destino /var/backups/nexatec/<fecha>/,
# solo root. Retencion: NEXATEC_BACKUP_KEEP_DAYS (default 14).
# Copia offsite: si existe /etc/nexatec/rclone.conf con un remoto "onedrive",
# se sube la carpeta (solo archivos YA cifrados; la clave nunca sale del
# servidor) a onedrive:NEXATEC-backups/<fecha>/. Retencion remota:
# NEXATEC_BACKUP_REMOTE_KEEP_DAYS (default 60).
# Ver docs/BACKUPS.md.
set -euo pipefail
umask 077

KEY=/etc/nexatec/backup.key
DEST_ROOT=${NEXATEC_BACKUP_DIR:-/var/backups/nexatec}
KEEP_DAYS=${NEXATEC_BACKUP_KEEP_DAYS:-14}
REMOTE_KEEP_DAYS=${NEXATEC_BACKUP_REMOTE_KEEP_DAYS:-60}
RCLONE_CONF=/etc/nexatec/rclone.conf
REMOTE_DIR=onedrive:NEXATEC-backups
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="$DEST_ROOT/$STAMP"

[ -r "$KEY" ] || { echo "falta $KEY"; exit 1; }
mkdir -p "$DEST"

dbs=$(runuser -u postgres -- psql -tAc \
  "SELECT datname FROM pg_database WHERE datname = 'nexatec_control' OR datname LIKE 'nxt\_%' ORDER BY datname")

count=0
for db in $dbs; do
  case "$db" in nexatec_control|nxt_*) ;; *) echo "omitida (no es de NEXATEC): $db"; continue ;; esac
  runuser -u postgres -- pg_dump -Fc --no-owner "$db" \
    | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$KEY" -out "$DEST/$db.dump.enc"
  sha256sum "$DEST/$db.dump.enc" >> "$DEST/SHA256SUMS"
  count=$((count + 1))
done
echo "$count bases respaldadas en $DEST ($(du -sh "$DEST" | cut -f1))"

# Retencion: borra solo carpetas con formato de fecha de este script.
find "$DEST_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*T*Z' -mtime +"$KEEP_DAYS" -print -exec rm -rf {} +

# Offsite. Un fallo aca deja el servicio en "failed" (se ve en systemctl /
# journal) pero el backup local ya quedo hecho.
if [ -r "$RCLONE_CONF" ] && rclone --config "$RCLONE_CONF" listremotes | grep -qx 'onedrive:'; then
  rclone --config "$RCLONE_CONF" copy "$DEST" "$REMOTE_DIR/$STAMP" --checksum --retries 5 --low-level-retries 10
  rclone --config "$RCLONE_CONF" check "$DEST" "$REMOTE_DIR/$STAMP" --one-way --size-only
  echo "subido a $REMOTE_DIR/$STAMP"
  # Retencion remota: solo dentro de NEXATEC-backups.
  rclone --config "$RCLONE_CONF" delete "$REMOTE_DIR" --min-age "${REMOTE_KEEP_DAYS}d" --include '20*T*Z/**'
  rclone --config "$RCLONE_CONF" rmdirs "$REMOTE_DIR" --leave-root
else
  echo "AVISO: OneDrive no configurado ($RCLONE_CONF); backup solo local"
fi
