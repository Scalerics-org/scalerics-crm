#!/bin/sh
set -e

mkdir -p /data

if [ ! -f /data/.prod_imported ]; then
  echo "Importando DB de produccion..."
  cp /app/leads_backup.db /data/leads.db
  touch /data/.prod_imported
fi

# Restauracion pendiente (docs/BACKUPS.md): si hay /data/restore.db y pasa el
# integrity_check, reemplaza a la base ANTES de levantar la app, que es el unico
# momento en que nadie la tiene abierta. Nunca frena el arranque.
python -m services.backup_db --aplicar-restauracion "${DB_PATH:-/data/leads.db}" \
  || echo "restauracion: el chequeo fallo, se arranca con la base de siempre"

exec gunicorn server:app --bind 0.0.0.0:$PORT --workers 1 --threads 4
