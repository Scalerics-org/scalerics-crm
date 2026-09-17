#!/usr/bin/env bash
#
# Sube el codigo al servidor y reinicia el servicio. Se corre desde la laptop,
# parado en wa-service/:
#
#   bash deploy/subir.sh 5.161.x.x            # codigo nada mas
#   bash deploy/subir.sh 5.161.x.x --con-estado   # ademas la sesion y la base
#
# --con-estado es para la PRIMERA vez, cuando se muda la sesion de WhatsApp y
# las conversaciones que ya existen. Despues no se usa mas: pisaria la base del
# servidor —que es la que esta viva— con la copia vieja de la laptop.
set -euo pipefail

SERVIDOR="${1:-}"
CON_ESTADO="${2:-}"
USUARIO_SSH="${SSH_USER:-root}"
APP_DIR=/opt/scalerics-wa

if [ -z "$SERVIDOR" ]; then
  echo "Falta la IP: bash deploy/subir.sh <ip> [--con-estado]" >&2
  exit 1
fi
[ -f package.json ] || { echo "Correlo parado en wa-service/" >&2; exit 1; }

ssh_() { ssh "$USUARIO_SSH@$SERVIDOR" "$@"; }

echo "── codigo ────────────────────────────────────────────────────────────────"
# node_modules queda afuera a proposito: better-sqlite3 es nativo y el binario
# compilado en Windows no corre en Linux. Se instala alla.
tar czf - --exclude=node_modules --exclude=auth --exclude=data --exclude=.env \
  src test package.json package-lock.json deploy \
  | ssh_ "tar xzf - -C $APP_DIR"

if [ "$CON_ESTADO" = "--con-estado" ]; then
  echo "── sesion de WhatsApp y base ─────────────────────────────────────────────"
  # El servicio local tiene que estar APAGADO. Dos procesos con las mismas
  # credenciales de WhatsApp se pelean la sesion y termina desvinculandose.
  echo "   (asegurate de que el bot NO este corriendo en la laptop)"
  tar czf - auth data | ssh_ "tar xzf - -C $APP_DIR"
  ssh_ "chown -R scalerics:scalerics $APP_DIR/auth $APP_DIR/data && chmod 700 $APP_DIR/auth $APP_DIR/data"
fi

echo "── dependencias ──────────────────────────────────────────────────────────"
ssh_ "cd $APP_DIR && npm ci --omit=dev --no-audit --no-fund"

echo "── tests ─────────────────────────────────────────────────────────────────"
# Corren en el servidor, contra el Node y los binarios que van a correr de
# verdad. Si fallan, no se reinicia nada.
ssh_ "cd $APP_DIR && npm ci --no-audit --no-fund >/dev/null && node --test 'test/*.test.js' | tail -5"

echo "── reiniciar ─────────────────────────────────────────────────────────────"
ssh_ "chown -R scalerics:scalerics $APP_DIR && systemctl restart scalerics-wa && sleep 4 && systemctl is-active scalerics-wa"
ssh_ "curl -s --max-time 5 http://127.0.0.1:\$(grep -oP '(?<=^PORT=).*' $APP_DIR/.env || echo 8080)/health || echo '(el /health todavia no responde: mirar journalctl -u scalerics-wa -n 50)'"
echo
echo "Listo."
