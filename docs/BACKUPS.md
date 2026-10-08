# Backups

## Qué se respalda

- `nexatec_control` y **cada base de cliente** `nxt_*` (lista explícita por
  nombre: **nunca** `hesed_ot` ni otra base del servidor).
- Diario a las 03:30 (`nexatec-backup.timer`), `pg_dump -Fc` (comprimido),
  **cifrado AES-256** con `openssl` (PBKDF2, 200k iteraciones) usando
  `/etc/nexatec/backup.key` (solo root).
- Destino: `/var/backups/nexatec/<fecha UTC>/` (carpeta solo de root) con
  `SHA256SUMS`. Retención: 14 días (`NEXATEC_BACKUP_KEEP_DAYS`).
- Corre con `CPUQuota=50%`, prioridad de disco "idle" y `Nice=10` para no
  competir con `hesed-ot-sistema`.

## Prueba de restauración (automática)

`nexatec-restore-check.timer` (domingos 05:00): verifica checksums,
descifra el último backup de `nexatec_control`, lo restaura en una base
**temporal** `nexatec_restore_check`, controla tablas, versión de Alembic y
datos, y la borra. Primer ejercicio (2026-10-08): `OK ... 24 tablas,
alembic 6089547cc3fe, 4 sistemas`. También se verificó el descifrado de un
backup de base de cliente.

```bash
sudo journalctl -u nexatec-backup.service -n 5 --no-pager
sudo journalctl -u nexatec-restore-check.service -n 5 --no-pager | grep -E "OK|FALLO"
sudo systemctl start nexatec-backup.service          # backup manual (p.ej. antes de migrar)
```

## Restaurar a mano (incidente)

Siempre primero en una base **nueva**; nunca encima de la que está en uso.

```bash
D=/var/backups/nexatec/<fecha>            # elegir la carpeta
sudo bash -c "cd $D && sha256sum -c SHA256SUMS"
sudo bash -c "openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -pass file:/etc/nexatec/backup.key -in $D/<base>.dump.enc -out /tmp/restore.dump"
sudo -u postgres createdb <base>_restaurada
sudo -u postgres pg_restore --no-owner -d <base>_restaurada /tmp/restore.dump
sudo shred -u /tmp/restore.dump
# revisar, y recién después decidir el reemplazo (con la app detenida).
```

Al restaurar una base de cliente hay que devolverle el dueño correcto
(`ALTER DATABASE ... OWNER TO <rol del tenant>` y reasignar objetos), porque
el dump se hace con `--no-owner`.

## Instalación / actualización de los scripts

Los scripts viven en el repo (`infra/backup/`) pero se **ejecutan desde
`/usr/local/sbin/` (propiedad de root)**: un script que corre como root no
puede estar en una carpeta que el usuario `opc` puede modificar (además
SELinux lo bloquea). Después de cambiarlos:

```bash
sudo install -m 755 -o root -g root infra/backup/nexatec-*.sh /usr/local/sbin/
```

## Pendientes (no inventar que ya está)

- **Copia fuera del servidor.** Hoy los backups están en el mismo disco que
  las bases: protegen contra errores y corrupción lógica, **no** contra la
  pérdida del servidor. Falta un destino externo (OCI Object Storage u otro)
  con credenciales que tiene que crear el propietario.
- **Guardar la clave** `/etc/nexatec/backup.key` en un lugar seguro fuera
  del servidor (gestor de contraseñas). Sin ella los backups no se pueden
  descifrar.
- RPO actual: hasta 24 h (backup diario). RTO: minutos para una base chica;
  no medido con volumen real.
