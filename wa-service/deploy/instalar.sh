#!/usr/bin/env bash
#
# Prepara un servidor Hetzner recien creado para correr el wa-service.
# Se corre UNA sola vez, como root, en el servidor:
#
#   bash instalar.sh
#
# Despues de esto, cada actualizacion se hace desde la laptop con subir.sh.
set -euo pipefail

APP_DIR=/opt/scalerics-wa
USUARIO=scalerics

echo "── paquetes base ─────────────────────────────────────────────────────────"
apt-get update -qq
# build-essential y python3 los necesita better-sqlite3, que es un modulo nativo
# y se compila en el servidor. Por eso node_modules NO se copia desde Windows:
# el binario compilado ahi no corre en Linux.
apt-get install -y -qq curl ca-certificates build-essential python3 ufw

echo "── node 22 ───────────────────────────────────────────────────────────────"
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y -qq nodejs
fi
node -v

echo "── zona horaria ──────────────────────────────────────────────────────────"
# El bot decide horarios de envio y arma recordatorios con fechas locales. Un
# servidor en UTC manda el "te recuerdo que es manana" tres horas corrido.
timedatectl set-timezone America/Montevideo
date

echo "── usuario y directorios ─────────────────────────────────────────────────"
id -u "$USUARIO" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$USUARIO"
mkdir -p "$APP_DIR"/{data,auth}
chown -R "$USUARIO:$USUARIO" "$APP_DIR"
# La sesion de WhatsApp y la base son lo mas sensible que hay aca: con auth/
# alguien manda mensajes como si fuera el numero de la empresa.
chmod 700 "$APP_DIR/auth" "$APP_DIR/data"

echo "── firewall ──────────────────────────────────────────────────────────────"
ufw allow OpenSSH
ufw --force enable
ufw status verbose
# El puerto del servicio NO se abre: escucha en 127.0.0.1. Para ver el QR desde
# la laptop se hace un tunel SSH (esta en el README), que no expone nada.

echo "── systemd ───────────────────────────────────────────────────────────────"
cp "$(dirname "$0")/scalerics-wa.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable scalerics-wa

echo
echo "Listo. Falta subir el codigo y el .env desde la laptop:"
echo "  bash deploy/subir.sh <ip-del-servidor>"
