#!/bin/sh
set -e

mkdir -p /data

# Antes esto hacia `cp /app/leads_backup.db /data/leads.db` cuando no existia el
# marcador /data/.prod_imported. Dos problemas:
#   1. Obligaba a versionar la base de produccion (6 cuentas con hash + PII de
#      421 negocios) para que la imagen la pudiera copiar.
#   2. El guard miraba el marcador, no la base. Un volumen nuevo (reemplazo de
#      host, scale, cambio de region) no tiene el marcador, asi que pisaba
#      produccion con un snapshot viejo sin emitir un solo error.
#
# Ahora: si la base no existe se crea VACIA con el esquema. Restaurar datos
# reales es una operacion explicita y fuera de banda (ver scripts/ y el backup).
if [ ! -s /data/leads.db ]; then
  echo "No hay base en /data/leads.db — creando uno nuevo con el esquema vacio."
  echo "Si esperabas datos, PARA y restaura el backup antes de seguir."
  python -c "from database import init_db; init_db('/data/leads.db')"
fi

exec gunicorn server:app \
  --bind 0.0.0.0:$PORT \
  --workers 1 --threads 4 \
  --access-logfile - --error-logfile -
