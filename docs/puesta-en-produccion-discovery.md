# Puesta en producción — Discovery: correo en frío a comercios con web

Este documento es para encender la campaña de discovery. **Es correo en frío**: le
escribe a comercios que nunca pidieron nada y cuya dirección se sacó raspando su
propio sitio web. Eso cambia el nivel de cuidado respecto de los recordatorios de
Meta, donde el lead llenó un formulario pidiendo que lo contacten.

Si algún paso no da lo esperado, **parar ahí**. Y ojo con el paso 1: hay una espera
que no depende de nosotros y que conviene arrancar semanas antes que todo lo demás.

Runbook hermano, con las trampas de infraestructura que son las mismas (la misma
máquina, la misma base, el mismo volumen): `puesta-en-produccion-recordatorios-meta.md`.
**Leelo también.**

---

## 0. Las dos cosas que hay que entender antes de tocar nada

**a. Hay dos automatizaciones de correo y dos interruptores independientes.**

| Campaña | Interruptor | Público | Remitente |
|---|---|---|---|
| Recordatorios de Meta | `META_RECORDATORIOS` | leads que llenaron el formulario | `contacto@scalerics.com` |
| Discovery | `DISCOVERY_EMAILS` | comercios raspados de Maps | el subdominio, ver paso 1 |

Apagar uno **no** apaga el otro. Y como `flyctl secrets set` reinicia la máquina,
tocar cualquiera de los dos secrets dispara el hilo de **las dos** campañas 180
segundos después del arranque. Al operar una, acordate de la otra.

**b. El tope diario es por campaña y por ventana rodante de 24 horas.** Discovery
arranca en 10; Meta está en 15. Son independientes: no se pisan, se suman. Si las
dos están encendidas, el dominio de Scalerics manda hasta 25 mails por día en total,
aunque salgan de remitentes distintos.

---

## 1. El subdominio, y por qué va primero

**Empezá por acá aunque falten semanas para lo demás.** Es lo único de todo este
procedimiento que depende de esperar a un tercero.

El correo en frío genera quejas por bien hecho que esté. Si sale de
`scalerics.com`, esas quejas degradan la entrega de los recordatorios de Meta —que
sí son opt-in— y del correo con clientes. Por eso sale de un subdominio propio.

1. Crear los registros DNS del subdominio en Cloudflare (los que dicte Resend:
   DKIM, SPF y el de retorno).
2. Esperar propagación y que **Resend lo marque como verificado**. Esto puede
   tardar de minutos a horas y no se puede apurar.
3. Recién ahí:

```bash
flyctl secrets set DISCOVERY_FROM_EMAIL="Scalerics <hola@novedades.scalerics.com>" -a scalerics-crm
```

**Si esa variable no está, la campaña no manda nada**: `send_discovery_email`
devuelve `"fallo"` a propósito y lo registra en el log. Es deliberado — una
configuración a medias no puede terminar mandando en frío desde el dominio
principal. Así que el orden es imposible de invertir por accidente.

---

## 2. Antes de deployar: la guarda del volumen y el backup

Igual que en el runbook de Meta, y por las mismas razones:

```bash
flyctl status -a scalerics-crm
```

Si la máquina está detenida **no entres por SSH**: `flyctl ssh console` la arranca,
y `start.sh` corre antes de que llegues a leer nada.

```bash
flyctl ssh console -a scalerics-crm -C "ls -la /data/.prod_imported /data/leads.db"
MSYS_NO_PATHCONV=1 flyctl ssh sftp get /data/leads.db ./leads-prod-$(date +%Y%m%d).db -a scalerics-crm
```

Tiene que existir `/data/.prod_imported`. Si falta, `start.sh` copia la base
congelada del repo encima de la viva en el próximo arranque.

---

## 3. Deploy

La tabla `discovery_reminders` se crea sola en el arranque, dentro de `init_db`.
No hay migración de datos: la tabla nace con su forma final.

```bash
flyctl deploy -a scalerics-crm
```

**El interruptor de discovery arranca apagado**, así que el deploy no manda nada de
esta campaña. Pero **sí puede disparar la tanda de Meta**, que está encendida: si
pasaron más de 24 horas desde la última, salen hasta 15 mails reales 180 segundos
después del arranque. Eso es lo esperado, no una falla.

Verificar que la tabla quedó:

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3;c=sqlite3.connect('/data/leads.db');print(c.execute('SELECT COUNT(*) FROM discovery_reminders').fetchone()[0])\""
```

Esperado: `0` la primera vez.

---

## 4. Cuánto padrón hay, y de qué calidad

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3;c=sqlite3.connect('/data/leads.db');print('discovery:',c.execute(\\\"SELECT COUNT(*) FROM businesses WHERE source='discovery'\\\").fetchone()[0]);print('con mail:',c.execute(\\\"SELECT COUNT(*) FROM businesses WHERE source='discovery' AND email IS NOT NULL AND LENGTH(TRIM(email))>3\\\").fetchone()[0]);print('direcciones unicas:',c.execute(\\\"SELECT COUNT(DISTINCT LOWER(TRIM(email))) FROM businesses WHERE source='discovery' AND email IS NOT NULL\\\").fetchone()[0])\""
```

**Las direcciones únicas van a ser menos que los comercios con mail, y está bien:**
dos sucursales de la misma firma comparten sitio y casilla. La campaña deduplica
por dirección, así que el número que importa es el de únicas.

**Condición de corte:** si "con mail" da 0, no sigas — el padrón no llegó al CRM.
Lo más probable es que el scrape haya corrido contra el CRM deployado **antes** de
que este código estuviera arriba, en cuyo caso los sitios web se perdieron en
silencio. Ver la precondición en el plan de la parte 1.

---

## 5. Dry-run, y mirar las direcciones a mano

Este paso **no existe en el runbook de Meta**, y acá es obligatorio. Allá las
direcciones las tipeó su dueño en un formulario; acá las sacó un bot de un sitio
web, y una dirección de ejemplo que quedó en una plantilla es reputación quemada
sin ninguna chance de venta.

```bash
flyctl ssh console -a scalerics-crm -C "cd /app && python -c \"from services.discovery_emails import enviar_discovery; print(enviar_discovery('/data/leads.db','https://scalerics-crm.fly.dev',dry_run=True))\""
```

El dry-run **no escribe ni manda**, y por eso tampoco respeta el cupo: lista con el
cupo entero aunque la tanda de hace un rato ya se lo haya llevado.

Mirá las direcciones que imprime, de a una. Buscá:

- direcciones de ejemplo (`usuario@dominio.com`, cualquier cosa con `tudominio`)
- `noreply@` o similares
- direcciones que sean claramente de un proveedor y no del comercio
- cualquier cosa que no parezca una casilla a la que le escribirías vos

El filtro ya descarta esas familias, pero es la primera vez que le escribís a
direcciones raspadas: **mirá antes de encender.**

---

## 6. Encender

```bash
flyctl secrets set DISCOVERY_EMAILS=on -a scalerics-crm
```

Reinicia la máquina y 180 segundos después arranca el hilo. **Si hay cupo, manda
hasta 10 mails reales en ese momento.** Este es el punto de no retorno: hasta acá
no salía nada de esta campaña.

Confirmar en el log:

```bash
flyctl logs -a scalerics-crm | grep -i "Discovery"
```

Esperado: `Discovery ACTIVO por DISCOVERY_EMAILS=on` y, tres minutos después, la
línea con el resumen: `{'candidatos': N, 'enviados': N, 'fallidos': 0, ...}`.

---

## 7. Los primeros días: qué mirar, y en este orden

**a. ¿Cayó en Principal o en Promociones?** Es el dato que no se puede testear con
código. Mandate uno a vos mismo con `DISCOVERY_FROM_EMAIL` apuntando a una casilla
tuya antes de la primera tanda real, o revisá con alguien que haya recibido.

**b. Rebotes.** En una lista raspada parte de las direcciones están muertas.
Insistirle a direcciones muertas es la vía más rápida a que bloqueen el dominio.
**Hoy no hay manejo automático de rebotes** —necesita el webhook de Resend, que es
otro subsistema— así que hay que mirarlos en el panel de Resend a mano. Si ves una
tasa de rebote arriba del 5%, **apagá y revisá el padrón** antes de seguir.

**c. Quejas de spam.** Cualquier queja es señal de parar y releer el texto. En frío,
una queja pesa mucho más que en una lista opt-in.

**d. Que la baja funcione.** Clickeá el link de baja de un mail de prueba y
confirmá que la página responde y que la fila queda marcada:

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3;c=sqlite3.connect('/data/leads.db');print(c.execute('SELECT COUNT(*) FROM discovery_reminders WHERE unsubscribed_at IS NOT NULL').fetchone()[0])\""
```

---

## 8. Subir el tope

El tope arranca en 10 porque el subdominio nace sin reputación. **No lo subas hasta
tener varios días de entrega limpia**: sin rebotes altos, sin quejas, y con los
mails cayendo donde deberían.

Cuando corresponda, se cambia `_TOPE_DIARIO` en `services/discovery_emails.py` y se
deploya. Subí de a poco — de 10 a 20, no de 10 a 100.

---

## Si algo sale mal

**Frenar todo, ya:**

```bash
flyctl secrets unset DISCOVERY_EMAILS -a scalerics-crm
```

Eso reinicia la máquina y el hilo de discovery no vuelve a arrancar. **Ojo: ese
mismo reinicio dispara el hilo de Meta**, que sigue encendido, y si hay cupo manda
su tanda. Si querés frenar las dos, sacá también `META_RECORDATORIOS`.

**Un mail salió a quien no debía.** Buscá la fila y marcá la baja a mano, para que
no reciba el contacto 2:

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3;c=sqlite3.connect('/data/leads.db');c.execute(\\\"UPDATE discovery_reminders SET unsubscribed_at=datetime('now') WHERE business_id=(SELECT id FROM businesses WHERE LOWER(TRIM(email))=LOWER(TRIM('LA_DIRECCION')))\\\");c.commit();print('listo')\""
```

**Hay que reintentar un envío que falló.** Borrá **esa fila puntual**, nunca por
`business_id` solo:

```sql
DELETE FROM discovery_reminders WHERE business_id = X AND numero = N;
```

Borrar por `business_id` se lleva el contacto 1, cuyo token ya viaja dentro de un
mail que alguien recibió: ese link de baja dejaría de funcionar.
