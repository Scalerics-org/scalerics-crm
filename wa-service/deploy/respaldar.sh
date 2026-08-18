#!/usr/bin/env bash
#
# Respalda las dos bases que no se pueden perder y avisa si algo sale mal.
#
#   bash respaldar.sh
#
# Pensado para correr por cron en el servidor, que es el unico que esta prendido
# siempre. En la laptop no sirve: el dia que no la abris, no hay backup — que es
# exactamente como se llego a tener una copia de tres semanas de antiguedad.
#
# Que respalda:
#   - la base del bot (leads, conversaciones, jobs) y la sesion de WhatsApp
#   - la base del CRM, que vive en el volumen de Fly
#
# Variables (van en /etc/default/scalerics-backup):
#   DESTINO           donde dejar los archivos           (default /var/backups/scalerics)
#   RETENER_DIAS      cuantos dias guardar               (default 30)
#   APP_DIR           instalacion del bot                (default /opt/scalerics-wa)
#   FLY_APP           app del CRM en Fly                 (vacio = no respalda el CRM)
#   FLY_API_TOKEN     token de Fly, para que corra solo
#   AVISAR_A          telefono del AM para el aviso de falla
set -uo pipefail

DESTINO="${DESTINO:-/var/backups/scalerics}"
RETENER_DIAS="${RETENER_DIAS:-30}"
APP_DIR="${APP_DIR:-/opt/scalerics-wa}"
FLY_APP="${FLY_APP:-}"
AVISAR_A="${AVISAR_A:-}"

HOY=$(date +%Y-%m-%d)
TRABAJO="$DESTINO/.tmp-$$"
problemas=()

mkdir -p "$DESTINO" "$TRABAJO"
chmod 700 "$DESTINO"
trap 'rm -rf "$TRABAJO"' EXIT

log() { echo "[$(date +%H:%M:%S)] $*"; }
fallo() { problemas+=("$1"); echo "[$(date +%H:%M:%S)] FALLO: $1" >&2; }

# Copia consistente de una base SQLite. No se usa cp: si alguien escribe en el
# medio, la copia sale cortada y no se nota hasta el dia que hay que restaurarla.
snapshot() {
  local origen="$1" destino="$2"
  python3 - "$origen" "$destino" <<'PY'
import sqlite3, sys
o = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
d = sqlite3.connect(sys.argv[2])
o.backup(d)
d.close(); o.close()
PY
}

# Una base que no abre no es un backup, es un archivo. Se verifica siempre.
verificar() {
  local f="$1"
  local r
  r=$(python3 -c "
import sqlite3, sys
c = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
print(c.execute('PRAGMA integrity_check').fetchone()[0])
print(c.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()[0])
" "$f" 2>&1) || { echo "no se pudo abrir"; return 1; }

  local estado tablas
  estado=$(echo "$r" | sed -n 1p)
  tablas=$(echo "$r" | sed -n 2p)
  [ "$estado" = "ok" ] || { echo "integrity_check dijo: $estado"; return 1; }
  [ "${tablas:-0}" -gt 0 ] || { echo "no tiene ninguna tabla"; return 1; }
  return 0
}

# ── el bot ───────────────────────────────────────────────────────────────────
log "bot: base y sesion de WhatsApp"
BOT_DB="$APP_DIR/data/wa.db"
if [ -f "$BOT_DB" ]; then
  if snapshot "$BOT_DB" "$TRABAJO/wa.db" 2>/dev/null; then
    if msg=$(verificar "$TRABAJO/wa.db"); then
      # auth/ va junto: sin esa carpeta hay que re-escanear el QR, y re-vincular
      # seguido es una senial sospechosa para Meta.
      tar czf "$DESTINO/bot-$HOY.tar.gz" -C "$TRABAJO" wa.db -C "$APP_DIR" auth \
        && log "  ok $(du -h "$DESTINO/bot-$HOY.tar.gz" | cut -f1)" \
        || fallo "no se pudo armar el tar del bot"
    else
      fallo "la base del bot no paso la verificacion: $msg"
    fi
  else
    fallo "no se pudo hacer el snapshot de la base del bot"
  fi
else
  fallo "no existe $BOT_DB"
fi

# ── el CRM ───────────────────────────────────────────────────────────────────
if [ -n "$FLY_APP" ]; then
  log "CRM: base en el volumen de Fly"
  if ! command -v fly >/dev/null 2>&1 && ! command -v flyctl >/dev/null 2>&1; then
    fallo "flyctl no esta instalado, no se respalda el CRM"
  else
    FLY=$(command -v fly || command -v flyctl)
    # El snapshot se arma DENTRO de la maquina, por lo mismo que arriba: la base
    # esta viva y un cat puede agarrarla a mitad de una escritura.
    if "$FLY" ssh console -a "$FLY_APP" -C "python3 -c \"
import sqlite3
o = sqlite3.connect('file:/data/leads.db?mode=ro', uri=True)
d = sqlite3.connect('/data/_backup_tmp.db')
o.backup(d); d.close(); o.close()
\"" >/dev/null 2>&1; then
      if "$FLY" ssh sftp get /data/_backup_tmp.db "$TRABAJO/leads.db" -a "$FLY_APP" >/dev/null 2>&1 \
         && [ -s "$TRABAJO/leads.db" ]; then
        if msg=$(verificar "$TRABAJO/leads.db"); then
          gzip -c "$TRABAJO/leads.db" > "$DESTINO/crm-$HOY.db.gz" \
            && log "  ok $(du -h "$DESTINO/crm-$HOY.db.gz" | cut -f1)" \
            || fallo "no se pudo comprimir la base del CRM"
        else
          fallo "la base del CRM no paso la verificacion: $msg"
        fi
      else
        fallo "no se pudo bajar la base del CRM"
      fi
      # El temporal se borra pase lo que pase: si queda, el volumen se llena.
      "$FLY" ssh console -a "$FLY_APP" -C "rm -f /data/_backup_tmp.db" >/dev/null 2>&1 \
        || fallo "quedo /data/_backup_tmp.db sin borrar en el servidor del CRM"
    else
      fallo "no se pudo hacer el snapshot en la maquina del CRM"
    fi
  fi
fi

# ── rotacion ─────────────────────────────────────────────────────────────────
# Solo despues de que lo de hoy salio bien: si el backup fallo, borrar los
# viejos deja sin nada.
if [ ${#problemas[@]} -eq 0 ]; then
  borrados=$(find "$DESTINO" -maxdepth 1 -name '*-????-??-??*' -mtime "+$RETENER_DIAS" -print -delete | wc -l)
  [ "$borrados" -gt 0 ] && log "rotacion: $borrados archivos de mas de $RETENER_DIAS dias"
fi

# ── aviso ────────────────────────────────────────────────────────────────────
if [ ${#problemas[@]} -gt 0 ]; then
  detalle=$(printf '  • %s\n' "${problemas[@]}")
  echo -e "\nEl backup termino con problemas:\n$detalle" >&2

  # Un backup que falla en silencio es igual que no tener backup. El aviso sale
  # por el mismo WhatsApp que ya esta andando, que es donde el equipo mira.
  if [ -n "$AVISAR_A" ] && [ -f "$APP_DIR/.env" ]; then
    CLAVE=$(grep -oP '(?<=^WA_API_KEY=).*' "$APP_DIR/.env" || true)
    PUERTO=$(grep -oP '(?<=^PORT=).*' "$APP_DIR/.env" || echo 8080)
    [ -n "$CLAVE" ] && curl -s --max-time 10 -X POST "http://127.0.0.1:$PUERTO/messages/send" \
      -H "x-api-key: $CLAVE" -H 'Content-Type: application/json' \
      -d "$(python3 -c "
import json,sys
print(json.dumps({'telefono': sys.argv[1],
                  'text': '⚠️ El backup de hoy falló:\n' + sys.argv[2] + '\nHay que mirarlo: journalctl -u scalerics-backup -n 50',
                  'skip_delay': True}))" "$AVISAR_A" "$detalle")" >/dev/null || true
  fi
  exit 1
fi

log "listo. Guardado en $DESTINO:"
ls -1t "$DESTINO" | head -4 | sed 's/^/  /'
