#!/bin/sh
set -e

mkdir -p /data

if [ ! -f /data/.prod_imported ]; then
  echo "Importando DB de produccion..."
  cp /app/leads_backup.db /data/leads.db
  touch /data/.prod_imported
fi

exec gunicorn server:app --bind 0.0.0.0:$PORT --workers 1 --threads 4
