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
