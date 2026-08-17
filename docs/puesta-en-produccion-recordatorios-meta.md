# Puesta en producción — recordatorios por mail a leads de Meta

**Se hace con Juan, no solo.** Es el único paso de todo esto que le manda correo
real a personas reales. Todo lo anterior es reversible; esto no.

App: `scalerics-crm` en Fly. Base: `/data/leads.db` dentro del volumen
`leads_data`. Rama: `recordatorios-meta`.

Los pasos van en orden y cada uno tiene una condición de corte. Si alguno no da
lo esperado, **parar ahí** — ninguno de los siguientes lo arregla.

---

## 0. Antes de tocar nada

**a) ¿Alguien lee `contacto@scalerics.com`?** El mail sale de esa dirección y su
único objetivo es que la persona conteste o agende. Si nadie la atiende, las
respuestas se pierden — que es exactamente el resultado que la automatización
busca. Si no se lee, agregar un `Reply-To` a una casilla que sí, antes de seguir.

**b) Una sola máquina.**

```bash
flyctl status -a scalerics-crm
```

La garantía de "una sola vez" vive en un archivo SQLite dentro del volumen. Dos
máquinas con dos volúmenes son dos tablas `meta_reminders` distintas, o sea doble
mail sin que nada lo detecte. Fly ya creó una segunda máquina sola antes en otro
proyecto; no es hipotético.

**c) El interruptor está apagado y tiene que quedar así hasta el paso 7.**

```bash
flyctl secrets list -a scalerics-crm | grep META_RECORDATORIOS
```

No debería aparecer. El job **arranca apagado por diseño**: requiere
`META_RECORDATORIOS=on` para levantar. Si aparece con valor `on`, sacarlo antes
de deployar.

---

## 1. Verificar que el arranque no vaya a pisar la base

```bash
flyctl ssh console -a scalerics-crm -C "ls -la /data/.prod_imported /data/leads.db"
```

**Tiene que existir `/data/.prod_imported`.** Si no está, `start.sh` copia
`/app/leads_backup.db` —la base congelada del repo, con `meta_reminders` vacía—
encima de `/data/leads.db` en el próximo arranque. Después del backfill eso
significa remandarle el mail a todos los que ya lo recibieron.

Si alguna vez hay que recrear el volumen: restaurar del backup del paso 2, nunca
dejar que el arranque haga su copia.

## 2. Backup de la base

```bash
flyctl ssh sftp get /data/leads.db ./leads-prod-$(date +%Y%m%d).db -a scalerics-crm
```

Guardarlo fuera del repo. Es la única vuelta atrás real.

## 3. Deploy

```bash
flyctl deploy -a scalerics-crm
```

El job no arranca (paso 0c). Comparar la imagen del log propio contra
`flyctl status`: si no coinciden, otra sesión deployó encima — no seguir.

## 4. Contar antes del backfill

```bash
flyctl ssh console -a scalerics-crm -C "sqlite3 /data/leads.db \
  \"SELECT COUNT(*) FROM businesses WHERE source='meta';\""
```

Al 14-08 eran **216**. Y los mails repetidos, que es lo que decide si la dedup
por dirección alcanza:

```sql
SELECT LOWER(TRIM(email)) e, COUNT(*) n FROM businesses
 WHERE source='meta' AND email IS NOT NULL AND TRIM(email)<>''
 GROUP BY e HAVING n>1;
```

En el snapshot de 110 leads daba 0. Si la base viva devuelve filas, no es
bloqueante —la dedup por mail las cubre— pero conviene saberlo antes.

## 5. Backfill de los mails enterrados

Primero en seco:

```bash
flyctl ssh console -a scalerics-crm -C "cd /app && python -m scripts.backfill_meta_emails /data/leads.db --dry-run"
```

Después de verdad, sin `--dry-run`. Se espera del orden de **174 actualizados y
42 sin mail** sobre 216 leads. Si el número no da exacto no es necesariamente un
error: depende de cuántos leads entraron desde entonces. Lo que importa es que
`actualizados + sin_email` sea igual al total del paso 4.

## 6. Dry-run: ver a quién le tocaría hoy

```bash
flyctl ssh console -a scalerics-crm -C "cd /app && python -m services.meta_reminders /data/leads.db --dry-run"
```

No escribe ni manda nada. Lista los 15 de hoy. Mirar que sean leads plausibles y
que los mails tengan cara de mails.

## 7. Mail de prueba — **contra una copia, no contra la base viva**

`META_NOTIFY_OVERRIDE` redirige el destinatario, pero **igual registra el envío**.
Si se corre contra `/data/leads.db`, hasta 15 leads reales quedan marcados como
recordados para siempre y nunca reciben el mail de verdad. Por eso va sobre una
copia:

```bash
flyctl ssh console -a scalerics-crm
cp /data/leads.db /data/prueba.db
cd /app && META_NOTIFY_OVERRIDE=juantomasetti240@gmail.com \
  python -m services.meta_reminders /data/prueba.db
rm /data/prueba.db
```

En Gmail, revisar **en este orden**:

1. ¿Cayó en **Principal** o en Promociones/Spam? Es el dato más importante de
   todos y el único que no se puede testear con código.
2. Firma, logo y botón de agendar se ven bien.
3. No aparece ningún `&lt;` ni `&amp;` visible (doble escapado).
4. Gmail muestra su **botón nativo de "Cancelar suscripción"** arriba. Si no
   aparece, el par de cabeceras RFC 8058 no está llegando.
5. El texto personalizado dice algo que se entiende, no `crear_mi_ecommerce`.
6. Clickear el link de baja del pie: tiene que mostrar una página que diga que
   listo, sin error.

El link apunta a `scalerics-crm.fly.dev` mientras el remitente es
`scalerics.com`. Esa disparidad de dominios suma señal de spam en un dominio
verificado hace días. Si el mail cae en Promociones, es el primer sospechoso.

## 8. Encender

```bash
flyctl secrets set META_RECORDATORIOS=on -a scalerics-crm
```

**Esto reinicia la máquina y la primera tanda de 15 mails reales sale ~3 minutos
después.** Es el punto de no retorno: a partir de acá hay correo saliente a
terceros.

## 9. El día que se enciende, no tocar nada más

Cada reinicio dispara una tanda: cada `flyctl deploy`, cada `secrets set/unset`,
y cada OOM de los 256 MB de RAM. El tope diario ahora se calcula sobre las filas
de las últimas 24 horas, así que **los envíos no pueden pasar de 15 por día ni
con reinicios** — pero igual conviene no deployar nada más ese día.

## 10. Mirar rebotes, no solo envíos

En el panel de Resend, después de la primera tanda. Los mails vienen de un
formulario de Meta de hasta 5 meses de antigüedad: una tasa de rebote alta en un
dominio nuevo hace más daño a la reputación que las quejas.

Con 174 elegibles a 15 por día, el backlog se drena en unos 12 días.

---

## Si algo sale mal

**Apagar es una línea y tiene efecto en el próximo arranque:**

```bash
flyctl secrets unset META_RECORDATORIOS -a scalerics-crm
```

**Alguien recibió el mail y no debía:** no hay forma de retirarlo. Buscar la fila
en `meta_reminders` por `business_id` y confirmar que sigue puesta — mientras
esté, no se le vuelve a escribir.

**Un lead quedó marcado sin haber recibido nada** (aparece en el log como
"envío incierto" o como fallo de limpieza): borrar su fila de `meta_reminders` a
mano y al día siguiente vuelve a entrar en la selección.
