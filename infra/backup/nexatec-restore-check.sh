#!/usr/bin/env bash
# Ejercicio de restauracion: toma el ULTIMO backup de nexatec_control, lo
# descifra, lo restaura en una base TEMPORAL (nexatec_restore_check),
# verifica que tenga el esquema y datos esperados, y borra la temporal.
# Nunca toca nexatec_control ni ninguna base en uso. Ver docs/BACKUPS.md.
set -euo pipefail
umask 077

KEY=/etc/nexatec/backup.key
DEST_ROOT=${NEXATEC_BACKUP_DIR:-/var/backups/nexatec}
TMPDB=nexatec_restore_check

latest=$(find "$DEST_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*T*Z' | sort | tail -1)
[ -n "$latest" ] || { echo "FALLO: no hay backups"; exit 1; }
file="$latest/nexatec_control.dump.enc"
(cd "$latest" && sha256sum -c --quiet SHA256SUMS) || { echo "FALLO: checksum invalido en $latest"; exit 1; }

cleanup() { runuser -u postgres -- dropdb --if-exists "$TMPDB" >/dev/null 2>&1 || true; rm -f "$work"; }
work=$(mktemp)
trap cleanup EXIT

openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$KEY" -in "$file" -out "$work"
chmod 644 "$work"
runuser -u postgres -- dropdb --if-exists "$TMPDB"
runuser -u postgres -- createdb "$TMPDB"
runuser -u postgres -- pg_restore --no-owner -d "$TMPDB" "$work"

tables=$(runuser -u postgres -- psql -d "$TMPDB" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
version=$(runuser -u postgres -- psql -d "$TMPDB" -tAc "SELECT version_num FROM alembic_version")
systems=$(runuser -u postgres -- psql -d "$TMPDB" -tAc "SELECT count(*) FROM systems")
if [ "$tables" -lt 20 ] || [ -z "$version" ]; then
  echo "FALLO: restauracion incompleta ($tables tablas, version '$version')"; exit 1
fi
echo "OK restauracion de $(basename "$latest"): $tables tablas, alembic $version, $systems sistemas"
