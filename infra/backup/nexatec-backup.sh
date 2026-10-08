#!/usr/bin/env bash
# Backup diario de las bases de NEXATEC: nexatec_control + cada base de
# tenant nxt_*. NUNCA incluye hesed_ot ni otra base que no sea de NEXATEC
# (lista explicita por nombre, no "todas las bases").
#
# Formato: pg_dump -Fc (comprimido) cifrado con AES-256 (openssl, clave en
# /etc/nexatec/backup.key, solo root). Destino /var/backups/nexatec/<fecha>/,
# solo root. Retencion: NEXATEC_BACKUP_KEEP_DAYS (default 14).
# Ver docs/BACKUPS.md.
set -euo pipefail
umask 077

KEY=/etc/nexatec/backup.key
DEST_ROOT=${NEXATEC_BACKUP_DIR:-/var/backups/nexatec}
KEEP_DAYS=${NEXATEC_BACKUP_KEEP_DAYS:-14}
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
