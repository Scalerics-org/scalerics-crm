# Poner el bot en el servidor

El bot corría en la laptop y se moría solo: cerrabas la terminal, se suspendía
la máquina, y los clientes que escribían no recibían nada hasta que alguien lo
notara. Esto lo pone en un servidor con systemd, que lo reinicia si se cae y lo
levanta cuando arranca la máquina.

## Antes de empezar

Necesitás la IP del servidor y poder entrar por SSH. Si no tenés clave todavía:

```bash
ssh-keygen -t ed25519 -C scalerics
```

Y la subís al panel de Hetzner, o directo al servidor con `ssh-copy-id`.

---

## 1. Preparar el servidor (una sola vez)

```bash
scp -r deploy root@<IP>:/tmp/ && ssh root@<IP> 'bash /tmp/deploy/instalar.sh'
```

Instala Node 22, crea el usuario `scalerics`, pone la zona horaria en
Montevideo, prende el firewall y deja el servicio registrado en systemd.

**La zona horaria importa más de lo que parece.** El bot decide a qué hora puede
mandar y arma los recordatorios con fechas locales; un servidor en UTC manda el
"te recuerdo que la reunión es mañana" tres horas corrido.

## 2. El `.env`

Se copia a mano, una vez, y **nunca** por git — tiene la clave de OpenAI.

```bash
scp .env root@<IP>:/opt/scalerics-wa/.env
ssh root@<IP> 'chown scalerics:scalerics /opt/scalerics-wa/.env && chmod 600 /opt/scalerics-wa/.env'
```

Antes de copiarlo, cambiá estas dos líneas para producción:

```
NODE_ENV=production
BUSINESS_HOURS=09:00-19:00
```

`BUSINESS_HOURS` lo tenías en `00:00-23:59` para probar. En producción conviene
el horario real: mandarle un mensaje a un lead a las 3 de la mañana es de las
cosas que hacen que te reporten.

## 3. Mudar la sesión de WhatsApp

**Apagá el bot en la laptop antes de esto.** Dos procesos con las mismas
credenciales se pelean la sesión y WhatsApp termina desvinculando el número —
ahí hay que escanear el QR de nuevo, y re-vincular seguido es una señal
sospechosa para Meta.

```bash
bash deploy/subir.sh <IP> --con-estado
```

Sube el código, la carpeta `auth/` (la sesión) y `data/` (los leads y las
conversaciones), compila las dependencias nativas en el servidor, corre los
tests ahí y reinicia.

El `--con-estado` es **solo para esta primera vez**. Después la base viva es la
del servidor, y volver a usarlo la pisaría con la copia vieja de la laptop.

## 4. Verificar

```bash
ssh root@<IP> 'systemctl status scalerics-wa --no-pager && journalctl -u scalerics-wa -n 20 --no-pager'
```

Tenés que ver `WhatsApp conectado`. Si en vez de eso pide QR, la sesión no
viajó bien: revisá que `auth/` haya llegado y que el dueño sea `scalerics`.

Después, escribile al número desde tu celular.

---

## De ahí en más

```bash
bash deploy/subir.sh <IP>          # deploy de un cambio
ssh root@<IP> 'journalctl -u scalerics-wa -f'   # ver qué está haciendo
ssh root@<IP> 'systemctl restart scalerics-wa'  # reiniciar
```

### Ver el QR si alguna vez hay que re-vincular

El servicio escucha solo en `127.0.0.1`, así que no hay nada expuesto a
internet. Para llegarle desde la laptop se hace un túnel:

```bash
ssh -L 8080:127.0.0.1:8080 root@<IP>
```

Y con eso abrís `http://127.0.0.1:8080/session/qr?key=<WA_API_KEY>` en el
navegador.

### Backup

Lo único irreemplazable es `auth/` (la sesión: perderla obliga a re-escanear) y
`data/` (los leads y las conversaciones).

```bash
ssh root@<IP> 'tar czf - -C /opt/scalerics-wa auth data' > backup-$(date +%F).tar.gz
```

Conviene dejarlo en un cron semanal.

---

## Lo que falta decidir: dónde queda el CRM

Hoy el CRM está en Fly y le tiene que pegar al bot para dos cosas: avisarle
cuando alguien agenda en Calendly (`POST /meetings`) y el panel de WhatsApp
(`/api/*`). Como el bot escucha solo en localhost, hoy no puede.

Hay dos salidas, y cambian el trabajo:

**El CRM se muda al mismo servidor.** El bot sigue en localhost, no se abre
ningún puerto, no hace falta dominio ni certificado. Además resuelve que la app
de Fly no la ve tu cuenta, que viene bloqueando el deploy del CRM desde hace
rato. Es la más simple y la más segura.

**El CRM se queda en Fly.** Ahí el bot tiene que ser alcanzable desde internet:
un subdominio apuntando al servidor, nginx adelante con certificado, y el
`x-api-key` que ya está implementado como autenticación. Es más piezas y más
superficie expuesta, pero no toca el CRM.

---

# Backup automático

El backup manual no sirve: la copia que había era del 24 de julio, con 421
negocios, cuando producción tenía 1.752. La diferencia no se notó hasta que
alguien fue a buscarla.

`respaldar.sh` respalda las dos bases que no se pueden perder —la del bot con la
sesión de WhatsApp, y la del CRM que vive en el volumen de Fly— y corre por
systemd todos los días a las 04:30.

## Instalación

```bash
sudo cp deploy/scalerics-backup.{service,timer} /etc/systemd/system/
sudo cp deploy/scalerics-backup.env.example /etc/default/scalerics-backup
sudo chmod 600 /etc/default/scalerics-backup
```

Editá `/etc/default/scalerics-backup` y completá `FLY_API_TOKEN`. El token se
saca desde la laptop:

```bash
fly tokens create readonly --name backup-servidor
```

**De solo lectura a propósito.** Ese token va a vivir en un archivo del
servidor; si alguien lo saca, que como mucho pueda leer, no borrar la app.

Después:

```bash
sudo systemctl enable --now scalerics-backup.timer
sudo systemctl start scalerics-backup.service   # probarlo ya
sudo journalctl -u scalerics-backup -n 30 --no-pager
```

## Lo que hace, y por qué así

**No copia los archivos con `cp`.** Las dos bases están vivas; una copia cruda
tomada en medio de una escritura sale cortada, y eso no se nota hasta el día que
hay que restaurarla. Usa la API de backup de SQLite, que toma una foto
consistente.

**Verifica lo que guardó.** Corre `integrity_check` sobre la copia y confirma
que tenga tablas. Un archivo que no abre no es un backup.

**Rota recién cuando lo de hoy salió bien.** Si el backup falló y además borra
los viejos, quedás sin nada.

**Avisa por WhatsApp si algo falla.** Un backup que falla en silencio es igual
que no tener backup — y el equipo mira WhatsApp, no los logs del servidor. Sale
por el mismo bot, a `AVISAR_A`.

## Restaurar

```bash
tar xzf /var/backups/scalerics/bot-2026-08-18.tar.gz -C /tmp
systemctl stop scalerics-wa

# Borrar el -wal y el -shm ANTES de poner la base. No es opcional.
rm -f /opt/scalerics-wa/data/wa.db /opt/scalerics-wa/data/wa.db-wal /opt/scalerics-wa/data/wa.db-shm
cp /tmp/wa.db /opt/scalerics-wa/data/wa.db
cp -r /tmp/auth /opt/scalerics-wa/
chown -R scalerics:scalerics /opt/scalerics-wa/data /opt/scalerics-wa/auth
systemctl start scalerics-wa
```

**Lo del `-wal` es la parte que se olvida y arruina la restauración.** SQLite guarda
las escrituras recientes en un archivo `wa.db-wal` aparte, y al abrir la base lo
replaya encima. Si dejás el WAL de la base vieja y ponés una base nueva al lado,
SQLite le aplica encima las páginas de la vieja y te la vacía. Pasó en la mudanza
a Fly: la base llegó bien, quedó el WAL de un arranque anterior, y al reiniciar
el bot tenía cero leads con el archivo correcto en el disco.

Conviene probarlo una vez ahora, no el día que haga falta.

---

# Por qué el bot va en el VPS y no en Fly

Fly apaga las máquinas cuando no reciben tráfico. El CRM está configurado así
—`auto_stop_machines = "stop"`, `min_machines_running = 0`— y para una web está
perfecto: se despierta con el primer request.

Para el bot es fatal. Baileys mantiene un WebSocket abierto contra WhatsApp; si
la máquina se detiene, la conexión se cae. Y no hay nada que la despierte,
porque **un mensaje de WhatsApp no llega como un request HTTP a Fly**: llega por
esa conexión que ya no existe. El bot quedaría dormido hasta que alguien entre
al `/health`.

Se puede forzar `min_machines_running = 1`, pero entonces estás pagando una
máquina prendida 24/7 en Fly, que es más cara que el VPS que ya tenés. Y Fly
migra máquinas entre hosts por mantenimiento más seguido que un VPS, y cada
migración es una reconexión.

Un proceso con una conexión persistente y estado en disco es la forma que mejor
le queda a un VPS y peor le queda a Fly.

## Cómo le pega el CRM al bot, entonces

Sin abrir nada a internet: se mete el VPS en la red privada de Fly con
WireGuard, que es una función de Fly y no un invento.

En la laptop:

```bash
fly wireguard create personal gru scalerics-vps
```

Deja un archivo de configuración. Se copia al servidor:

```bash
sudo apt install wireguard
sudo cp scalerics-vps.conf /etc/wireguard/fly.conf
sudo systemctl enable --now wg-quick@fly
ping6 -c2 scalerics-crm.internal
```

A partir de ahí el VPS tiene una dirección `fdaa:...` dentro de la red de Fly, y
el CRM le puede pegar al bot por ahí. En el `.env` del CRM:

```
WA_SERVICE_URL=http://[fdaa:tu:direccion]:8080
WA_API_KEY=<el mismo del bot>
BOT_API_URL=http://[fdaa:tu:direccion]:8080
```

Y en el `.env` del bot, para que escuche también en la interfaz de WireGuard:

```
HOST=::
```

Con eso el bot sigue sin estar expuesto a internet —el firewall solo deja pasar
SSH— pero el CRM lo alcanza. Sin dominio, sin certificado, sin nginx.

Recién ahí funcionan las dos cosas que hoy no: que el bot se entere cuando
alguien agenda en Calendly y deje de insistirle, y el panel de WhatsApp del CRM.
