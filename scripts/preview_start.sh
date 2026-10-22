#!/bin/sh
# Arranque de la vista previa de una PR (fly.preview.toml). No es start.sh:
# no copia la base de producción ni usa el volumen. Siembra datos inventados
# en /tmp y levanta la app sin procesos de fondo, así que no manda mails, no
# importa leads ni cobra nada.
set -e

export DB_PATH=/tmp/preview.db
export CRM_SIN_PROCESOS_DE_FONDO=true
export ADMIN_EMAIL="${PREVIEW_EMAIL:-preview@scalerics.com}"

python -m scripts.preview_seed "$DB_PATH"

exec gunicorn server:app --bind 0.0.0.0:$PORT --workers 1 --threads 4
