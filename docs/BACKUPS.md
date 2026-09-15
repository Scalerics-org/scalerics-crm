# Backups de la base del CRM

La base es un solo archivo SQLite: `/data/leads.db`, en el volumen de Fly
`leads_data` (1 GB) de la app `scalerics-crm`. Código: `services/backup_db.py`.

## Qué se guarda, dónde y cuánto tiempo

| Copia | Dónde | Cuánto tiempo | Quién la hace |
|---|---|---|---|
| Backup diario `crm-leads-AAAA-MM-DD.db.gz` | Cloudflare R2, bucket `R2_BUCKET`, prefijo `crm/` | 30 días | este código, una vez por día |
| El mismo `.gz` | el volumen, en `/data/backups/` | los últimos 3 | este código |
| Snapshot del volumen entero | Fly | 5 días | Fly, automático |

- La fecha del nombre es la de Montevideo. Si hay dos corridas el mismo día (la diaria y el botón), la segunda pisa a la primera.
- La copia sale de la API de backup de SQLite, no de copiar el archivo. Así es consistente aunque la app esté escribiendo.
- Antes de comprimir se corre `PRAGMA integrity_check`. Si no da `ok`, no se guarda ni se sube nada y se manda el aviso.
- La retención en R2 solo borra archivos `crm/crm-leads-AAAA-MM-DD.db.gz`. Lo que haya bajo `wa-service/` (el bot de WhatsApp) u otro nombre bajo `crm/` no se toca.

### Cuándo corre

- Una vez por día. La primera corrida es 300 s después de cada arranque.
- La última corrida queda en la tabla `corridas` (nombre `backup_db`). Si ya corrió en las últimas 20 horas, un deploy no repite el backup.
- `BACKUP_DB=off` lo apaga. Por defecto está prendido.
- Sin los secrets de R2 hace solo la copia local. En ese caso lo avisa una vez por arranque en el log: `backup a R2 desactivado: faltan R2_*`.

### Si falla

Si falla la integridad, la subida, la retención en R2 o salta una excepción, llega un mail a los admins. Son los mismos destinatarios que la alerta del token de Meta: rol "Admin" más `ADMIN_EMAIL`. Llega como mucho un mail cada 24 horas (marca `backup_db_aviso` en `corridas`).

## Secrets que hay que cargar

Son los mismos nombres que usa el bot de WhatsApp. Pueden apuntar al mismo bucket, porque el prefijo los separa.

- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET`

Cargar un secret reinicia la máquina. No es un problema: el backup trae su propia marca diaria y no manda mails a terceros.

## Cómo verificar que anda

1. **Log:**
   ```
   flyctl logs -a scalerics-crm | grep -i backup
   ```
   - Al arrancar: `Backup diario ACTIVO: ... (R2: si)`.
   - Unos 5 minutos después: `backup OK: crm-leads-AAAA-MM-DD.db.gz, N bytes ... subido a R2: si`.
   - En un arranque por deploy el mismo día: `Backup: ya corrio el ..., se saltea esta corrida (arranque por deploy)`.
2. **Pantalla:** en `/admin/users` (Administración), sección "Backups de la base".
   - El botón "Hacer backup ahora" corre uno en el momento y muestra el resultado.
   - Abajo se listan los backups de R2 y los locales.
3. **API** (sesión de admin; quien no es admin recibe 403):
   - `POST /api/admin/backup-ahora` devuelve el resumen: `ok`, `nombre`, `tamano`, `tamano_db`, `integridad`, `subido`, `borrados_r2`, `borrados_locales`, `error`.
   - `GET /api/admin/backups` devuelve `backups` (R2: nombre, tamaño, fecha) y `locales`.
4. **R2:** en el panel de Cloudflare, R2, el bucket y la carpeta `crm/`.

## Cómo restaurar

> Antes de restaurar nada, sacá un snapshot del estado actual. Así la restauración se puede deshacer:
> ```
> fly volumes list -a scalerics-crm                     # anotar el id del volumen leads_data
> fly volumes snapshots create <vol_id> -a scalerics-crm
> ```

### A. Desde un backup de R2 (lo normal)

1. **Bajar el `.gz`.** Desde el panel de Cloudflare (R2, el bucket, `crm/`, el archivo, Download), o con cualquier cliente S3 y las claves de R2:
   ```
   aws s3 cp s3://<R2_BUCKET>/crm/crm-leads-AAAA-MM-DD.db.gz . \
     --endpoint-url https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com
   ```
2. **Descomprimir y revisar en tu máquina:**
   ```
   gunzip -k crm-leads-AAAA-MM-DD.db.gz
   python -c "import sqlite3; c=sqlite3.connect('crm-leads-AAAA-MM-DD.db'); print(c.execute('PRAGMA integrity_check').fetchone(), c.execute('SELECT COUNT(*) FROM businesses').fetchone())"
   ```
   Tiene que dar `('ok',)` y una cantidad de leads razonable.
3. **Subirlo al volumen con otro nombre**, con la app andando:
   ```
   fly ssh sftp shell -a scalerics-crm
   » put crm-leads-AAAA-MM-DD.db /data/restore.db.part
   ```
   Se sube como `.part` a propósito: si la máquina se reinicia a mitad de la subida, un archivo incompleto no puede tomarse por la restauración.
4. **Dejarlo listo:**
   ```
   fly ssh console -a scalerics-crm -C "mv /data/restore.db.part /data/restore.db"
   ```
5. **Parar y arrancar la máquina:**
   ```
   fly machine list -a scalerics-crm
   fly machine restart <machine_id> -a scalerics-crm
   ```
   Al arrancar, `start.sh` corre `python -m services.backup_db --aplicar-restauracion` **antes** de levantar gunicorn. Es el único momento en que nadie tiene la base abierta. Ese paso:
   - corre `integrity_check` sobre `/data/restore.db`. Si falla, no toca nada y lo renombra a `restore.db.rechazado-<fecha>`;
   - vuelca el WAL de la base actual y la mueve a `/data/leads.db.antes-restore-<fecha>`. No la borra;
   - pone `restore.db` como `/data/leads.db`.

   No reemplaces `/data/leads.db` a mano con la app andando: el proceso viejo sigue escribiendo en el archivo desplazado y en su `-wal`, y esos cambios se pierden.
6. **Verificar:**
   - En el log tiene que aparecer `restauracion APLICADA` (o `RECHAZADA`).
   - Entrá al CRM y mirá que estén los datos esperados.
7. **Limpiar:** el volumen es de 1 GB. Cuando confirmes que todo está bien, borrá la base anterior:
   ```
   fly ssh console -a scalerics-crm -C "sh -c 'ls -la /data && rm /data/leads.db.antes-restore-*'"
   ```

### B. Desde un snapshot de Fly (últimos 5 días)

Un snapshot es el volumen entero, no un archivo. Se restaura creando un volumen nuevo desde el snapshot y levantando la máquina con ese volumen.

> Revisá los flags con `fly <comando> --help` antes de correrlos: cambian entre versiones de flyctl.

1. Listar los snapshots:
   ```
   fly volumes list -a scalerics-crm
   fly volumes snapshots list <vol_id> -a scalerics-crm
   ```
2. Crear un volumen desde el snapshot elegido, en la misma región (`iad`):
   ```
   fly volumes create leads_data --snapshot-id <snapshot_id> --region iad --size 1 -a scalerics-crm
   ```
3. Parar la máquina actual, así deja de escribir en el volumen viejo:
   ```
   fly machine stop <machine_id> -a scalerics-crm
   ```
4. Levantar una máquina igual con el volumen restaurado montado en `/data`:
   ```
   fly machine clone <machine_id> --attach-volume <vol_id_nuevo>:/data -a scalerics-crm
   ```
5. Verificar en el CRM y en el log. Recién ahí destruí la máquina vieja (`fly machine destroy <machine_id>`).
   - El volumen viejo queda suelto, como respaldo. Borralo cuando no haga falta.
   - Ojo: con dos volúmenes llamados `leads_data`, un deploy puede montar cualquiera de los dos si alguno queda suelto.

**Alternativa sin cambiar de volumen:** se puede sacar solo el archivo del snapshot.
- Montar el volumen restaurado en una máquina temporal.
- Bajar `/data/leads.db` con `fly ssh sftp get`.
- Seguir el camino A desde el paso 3.
