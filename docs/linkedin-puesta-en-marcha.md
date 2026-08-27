# Borradores de LinkedIn — puesta en marcha

Qué hay que hacer una sola vez para que esto empiece a funcionar.

## Qué hace

Martes y viernes a las 8 de la mañana, un workflow de GitHub Actions le pide al
CRM dos borradores de post para `linkedin.com/company/scalerics`. Los dos salen
de la tabla `linkedin_temas`, con ángulos distintos, se redactan con
`claude-opus-5` y se validan contra las reglas de voz. El runner renderiza las
tarjetas con Playwright y se las devuelve; ahí el CRM manda el mail a
`scalerics@gmail.com` con los PNG adjuntos.

El mail programado **no habla de clientes ni de proyectos**. Esa rama existió y
se sacó el 26-8-2026: el CRM no guarda nada de los trabajos entregados (los 7
clientes cerrados tenían el `client_info` vacío y el nombre era el de la persona
que llenó el formulario de Meta), así que los posts salían sobre proyectos que
no existían. Para eso está el post a mano, más abajo.

**No publica nada.** Vos elegís, editás si querés, publicás a mano y marcás cuál
usaste con el link del pie del mail.

## 1. Secrets en Fly

```bash
flyctl secrets set ADMIN_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" -a scalerics-crm
flyctl secrets set LINKEDIN_MAIL_TO="scalerics@gmail.com" -a scalerics-crm
```

Guardá el valor de `ADMIN_TOKEN` que salga: lo necesitás en el paso 2.
`ANTHROPIC_API_KEY`, `RESEND_API_KEY` y `CRM_URL` ya están seteados.

Si `flyctl secrets set` rebota con *"We need your payment information to
continue"*, es el 403 de organización: la app vive en la org `scalerics` y hay
que cargar la tarjeta en `fly.io/dashboard/scalerics/billing`. No es un problema
de login, no pierdas tiempo con `flyctl auth login`.

## 2. Secrets en GitHub

En Settings → Secrets and variables → Actions del repo:

- `ADMIN_TOKEN` — el mismo valor que seteaste en Fly.
- `CRM_URL` — `https://scalerics-crm.fly.dev`

## 3. Primera corrida a mano

Actions → "Borradores de LinkedIn" → Run workflow. Tiene que llegar el mail a
`scalerics@gmail.com` en menos de 5 minutos.

## 4. Pedir un post sobre trabajo real

La información de qué se entregó no está en el CRM, así que la ponés vos. El
mismo endpoint acepta un contexto libre y arma un solo post con él:

```bash
TOKEN=$(grep "^ADMIN_TOKEN=" .env | cut -d= -f2-)
curl -X POST https://scalerics-crm.fly.dev/api/linkedin/generar   -H "x-admin-token: $TOKEN" -H "Content-Type: application/json"   -d '{"contexto_manual": "Salió online la tienda de Biciconde, una bicicletería de Ciudad de la Costa. Catálogo de bicicletas y repuestos, pago con Mercado Pago y envío por DAC.",
       "imagen_url": "https://biciconde.uy"}'
```

Devuelve `{"job_id": N, "lote": "..."}`. Después, para renderizar la imagen y
que salga el mail:

```bash
python scripts/render_linkedin.py --crm https://scalerics-crm.fly.dev   --token "$TOKEN" --job-id N
```

`imagen_url` es opcional: si va, la imagen es una captura de esa página; si no
va, el post sale sin imagen. Escribí en el contexto solo lo que es cierto: el
prompt le prohíbe agregar plazos, tecnologías o resultados que no le hayas dado.

Un post a mano **no consume** ningún tema educativo.

## Cuando deje de llegar el mail

En este orden:

1. **¿GitHub desactivó el cron?** Apaga los workflows programados tras 60 días
   sin commits en el repo, avisando por mail antes. Se ve en la pestaña Actions
   y se reactiva con un botón.
2. **¿El workflow falló?** GitHub manda el mail del fallo. El log dice en qué
   paso.
3. **¿401?** `ADMIN_TOKEN` no coincide entre Fly y GitHub, o quedó sin setear en
   Fly (con la variable vacía el bypass del header no aplica).
4. **¿Llegó el mail de "No salieron los borradores"?** El modelo no logró un
   texto que pasara la validación dos veces seguidas. Los logs dicen qué regla
   rompió: `flyctl logs -a scalerics-crm | grep -i "Borrador rechazado"`.
5. **¿Se acabaron los temas?** El mail avisa cuando reusa uno antes de los 180
   días. Sembrar más en `_LINKEDIN_TEMAS_SEMILLA` de `database.py`.

## Cuánto dura el banco de temas

Cada mail consume **dos** temas, y van dos mails por semana: **cuatro por
semana**. Con 42 sembrados, el banco alcanza para unas **10 semanas**. Después
empieza a reusar temas antes de los 180 días y el mail lo dice.

Un tema se marca como usado **cuando se genera el borrador, no cuando lo
publicás**. Así que un borrador que descartás igual gasta su tema.

Para estirarlo hay tres caminos: sembrar más temas, bajar a un borrador por
mail, o pasar a un mail por semana. Cualquiera de los dos últimos lleva el banco
a unas 21 semanas.

## Si la tabla de temas quedó sin tildes

Los títulos de la semilla se corrigieron el 26-8-2026 para que lleven tildes: se
imprimen tal cual en la imagen que se publica. `seed_linkedin_temas` usa
`INSERT OR IGNORE` por título, así que sobre una base sembrada con los títulos
viejos **no los actualiza, agrega los nuevos al lado**. Si en tu base hay temas
sin tilde, vaciá la tabla y resembrá:

```sql
DELETE FROM linkedin_temas;
```

y reiniciá la app (la semilla corre al arrancar, en `server.py`).
