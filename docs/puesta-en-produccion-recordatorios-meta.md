# Puesta en producción — recordatorios por mail a leads de Meta

**Se hace con Juan, no solo.** Es el único paso de todo esto que le manda correo
real a personas reales. Todo lo anterior es reversible; esto no.

App: `scalerics-crm` en Fly. Base: `/data/leads.db` dentro del volumen
`leads_data`.

Este runbook se escribió para el primer encendido, hecho desde la rama
`recordatorios-meta` (mergeada a `main` el 17-8-2026, encendida en producción
el 18-8-2026 y mandando mails reales desde entonces). Esta versión lo
actualiza para el deploy que reemplaza ese mail único por la secuencia de 7
contactos, hecho en la rama `secuencia-recordatorios`. El interruptor está
confirmado en `on` en producción (verificado el 19-8-2026, junto con que
`META_NOTIFY_OVERRIDE` no está seteado), así que este deploy no es un primer
encendido: el paso 0.c lo apaga temporalmente antes de deployar, para que la
migración y los pasos de verificación (3b y 4 a 7) corran con la máquina
quieta, y el paso 8 lo vuelve a prender al final.

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

La garantía de "no mandarle el mismo contacto dos veces a la misma persona"
vive en el `UNIQUE(business_id, numero)` de `meta_reminders`, así que esa
sobrevive aunque dos corridas se pisen. Lo que **no** cubre ese `UNIQUE` es el
volumen: el tope de 15 mails por día se calcula una sola vez al arrancar la
tanda (`enviados_ultimas_24h`, en `services/meta_reminders.py`) y no se
reserva. Dos corridas solapadas —dos máquinas, o un reinicio de Fly mientras
la tanda anterior todavía sigue corriendo— pueden calcular 15 de cupo cada
una y mandar hasta 30 en la ventana de 24 horas, sin que nada lo impida ni lo
avise. Por eso una sola máquina corriendo **es parte de la garantía del tope
diario, no un detalle de costos**. Y si dos máquinas llegan a tener dos
volúmenes distintos —el caso ya conocido: Fly creó una segunda máquina sola
antes en otro proyecto, no es hipotético— ahí se pierde también la garantía
de "no repetir contacto", porque cada volumen tiene su propia tabla
`meta_reminders` sin que la otra se entere.

**c) Apagar el interruptor antes de deployar.**

```bash
flyctl secrets list -a scalerics-crm | grep META_RECORDATORIOS
```

Confirmado en producción: desde el 18-8-2026 está en `on`, mandando mails
reales todos los días (`META_NOTIFY_OVERRIDE` no está seteado). Por eso el
deploy del paso 3 no se puede hacer con el interruptor prendido: 180
segundos después de ese boot sale una tanda real con la secuencia nueva,
antes de que corra un solo paso de verificación (3b, 6, 7), y esos pasos
necesitan la máquina quieta para funcionar (ver el aviso en cada uno). La
única mitigación es apagarlo ahora, dejarlo así durante toda la
verificación, y volver a prenderlo recién en el paso 8:

```bash
flyctl secrets unset META_RECORDATORIOS -a scalerics-crm
```

Esto también reinicia la máquina, igual que `secrets set` — nada sale
mientras quede así. Si el comando de arriba ya mostraba que estaba apagado
(otro ambiente, o alguien lo bajó a mano), saltear este `unset`: fallaría
porque no hay nada que sacar.

**d) La secuencia son 7 contactos repartidos en un año, no uno.**

| Contacto | Días desde el primer envío | Nota |
|----------|-----------------------------|------|
| 1        | 0                           | dispara la cuenta del resto |
| 2        | 10                          | |
| 3        | 25                          | |
| 4        | 115                         | |
| 5        | 205                         | |
| 6        | 295                         | |
| 7        | 365                         | último: después de este el lead no vuelve a entrar nunca, aunque siga `sin_contactar` |

Los días se cuentan desde el **primer** envío de cada lead, no desde el
anterior, para que el atraso de una tanda no se acumule sobre las que siguen
(`_DIAS_DE_CADA_CONTACTO` en `services/meta_reminders.py`). Cada contacto
tiene su propio texto (`services/email_service.py`; el 7 dice explícitamente
que es el último) y su propio token de baja, pero la baja es sobre la
persona: quien se da de baja en cualquier contacto no recibe ninguno de los
que faltan.

El tope de 15 mails por día es compartido entre seguimientos (leads que ya
están en la secuencia) y contactos nuevos, y los seguimientos van primero.
Un día con backlog de leads nuevos puede terminar sin mandar ningún contacto
1 si el cupo se lo llevan los seguimientos.

**e) Si un lead contesta y nadie lo mueve de `sin_contactar`, sigue en la
secuencia.** El único corte por respuesta es el `crm_status`: mientras siga
en `sin_contactar` va a seguir recibiendo los 7 contactos aunque haya
contestado el primero. Es el modo de falla más probable de todo esto y la
única mitigación es la disciplina de mover el lead en el CRM apenas contesta.

---

## 1. Verificar que el arranque no vaya a pisar la base

```bash
flyctl ssh console -a scalerics-crm -C "ls -la /data/.prod_imported /data/leads.db"
```

**Tiene que existir `/data/.prod_imported`.** Si no está, `start.sh` copia
`/app/leads_backup.db` —la base congelada del repo, con `meta_reminders`
vacía— encima de `/data/leads.db` en el próximo arranque. Con la secuencia de
7 contactos esto es más grave que un simple reenvío: **todos los leads
vuelven a recibir el contacto 1**, sin importar en qué contacto de la
secuencia estaban, y los tokens de baja ya publicados en mails reales dejan
de funcionar (la fila que los tenía se borró junto con el resto de la tabla).

Si alguna vez hay que recrear el volumen: restaurar del backup del paso 2, nunca
dejar que el arranque haga su copia.

## 2. Backup de la base

```bash
MSYS_NO_PATHCONV=1 flyctl ssh sftp get /data/leads.db ./leads-prod-$(date +%Y%m%d).db -a scalerics-crm
```

(`MSYS_NO_PATHCONV=1` es para Git Bash: sin eso, `/data/leads.db` se
interpreta como una ruta de Windows y el comando falla.)

Guardarlo fuera del repo. Es la única vuelta atrás real — también es lo que
salva si la migración del paso 3b sale mal.

De paso, contá cuántas filas tiene `meta_reminders` antes de deployar (la
migración corre recién en el próximo arranque): es el número contra el que se
compara en el paso 3b.

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3;c=sqlite3.connect('/data/leads.db');print(c.execute('SELECT COUNT(*) FROM meta_reminders').fetchone()[0])\""
```

## 3. Deploy

```bash
flyctl deploy -a scalerics-crm
```

Al arrancar, `init_db` (`database.py`) migra `meta_reminders` sola: agrega la
columna `numero`, cambia el `UNIQUE` a `(business_id, numero)` y, si la tabla
vieja no tenía esa columna, reconstruye la tabla completa poniendo `numero=1`
en cada fila existente (conservan su `id`, su `token` y su `sent_at`
originales). No hay forma de correrla a mano ni de saltearla — el deploy la
dispara sola, una sola vez.

El interruptor quedó apagado en el paso 0.c, así que el job no arranca
todavía — recién lo hace en el paso 8. Comparar la imagen del log propio
contra `flyctl status`: si no coinciden, otra sesión deployó encima — no
seguir.

## 3b. Verificar que la migración de `meta_reminders` sobrevivió

El día del deploy:

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3;c=sqlite3.connect('/data/leads.db');print('filas:',c.execute('SELECT COUNT(*) FROM meta_reminders').fetchone()[0]);print('numeros:',c.execute('SELECT numero,COUNT(*) FROM meta_reminders GROUP BY numero').fetchall())\""
```

Esperado: la misma cantidad de filas que contaste en el paso 2, y todas con
`numero=1`. Con el interruptor apagado desde el paso 0.c no debería haber
salido ninguna tanda entre el deploy y este chequeo, así que el número tiene
que cerrar exacto. Si por algún motivo el interruptor seguía encendido en
este punto, puede haber hasta 15 filas de más —la tanda automática de 180
segundos después del boot— y ahí conviene entender de dónde salieron antes
de seguir, no asumir que la migración duplicó datos.

Unos días después, con la secuencia ya corriendo, el mismo comando tiene que
mostrar varios números: el 1 sigue siendo mayoría (es el que reciben los
leads nuevos, hasta 15 por día) pero van a empezar a aparecer filas con
`numero=2` a partir del décimo día desde el deploy (contacto 1 + 10 días, ver
la tabla del paso 0.d). Un número que no debería estar todavía —por ejemplo
un `numero=2` al día siguiente del deploy, o un `numero=4` antes de que pasen
115 días desde el primer contacto de ese lead puntual— es señal de un reloj
desincronizado en la máquina, de una fila migrada con un `sent_at` corrido, o
de que alguien corrió el backfill o una prueba contra la base viva en vez de
una copia (paso 7). No se autoarregla: hay que mirar el `sent_at` de esa fila
puntual y decidir a mano.

## 4. Contar antes del backfill

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3;c=sqlite3.connect('/data/leads.db');print(c.execute('SELECT COUNT(*) FROM businesses WHERE source=?', ('meta',)).fetchone()[0])\""
```

Al 14-08 eran **216**. Y los mails repetidos, que es lo que decide si la dedup
por dirección alcanza:

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3;c=sqlite3.connect('/data/leads.db');print(c.execute('SELECT LOWER(TRIM(email)) e, COUNT(*) n FROM businesses WHERE source=? AND email IS NOT NULL AND TRIM(email)<>? GROUP BY e HAVING n>1', ('meta','')).fetchall())\""
```

En el snapshot de 110 leads daba 0. Si la base viva devuelve filas, no es
bloqueante —la dedup por mail las cubre— pero conviene saberlo antes.

(El binario `sqlite3` no está instalado en la imagen — el `apt-get install`
del `Dockerfile` no lo trae, solo `curl wget gnupg libffi-dev libxml2
libxslt1.1` — así que estas dos consultas van con `python -c`, igual que las
del paso 2 y 3b.)

## 5. Backfill de los mails enterrados

Ya corrió una vez, el 18-8-2026, con la automatización original. Volver a
correrlo ahora es seguro: es idempotente. `scripts/backfill_meta_emails.py`
(líneas 36-38) cuenta un lead que ya tiene `email` cargado como `ya_tenian` y
no lo toca; solo pasa a `actualizados` el que todavía tenía el mail enterrado
en `form_data`.

Primero en seco:

```bash
flyctl ssh console -a scalerics-crm -C "cd /app && python -m scripts.backfill_meta_emails /data/leads.db --dry-run"
```

**Si es la primera vez:** del orden de **174 actualizados y 42 sin mail**
sobre 216 leads, con `ya_tenian` en 0.

**Si ya corrió antes** (el caso normal desde el 18-8-2026): `actualizados`
va a rondar 0 —salvo los leads de Meta que entraron después del último
backfill y todavía tienen el mail enterrado— y la mayoría cae en
`ya_tenian`, no en `actualizados`.

En los dos casos la cuenta que tiene que cerrar es
`actualizados + sin_email + ya_tenian == total del paso 4` (no
`actualizados + sin_email` solo: esa alcanzaba la primera vez porque
`ya_tenian` daba 0). Si no cierra, sí es una señal real de error.

Después de verdad, sin `--dry-run`, con la misma lectura de arriba.

## 6. Dry-run: ver a quién le tocaría hoy

Corré esto con el interruptor todavía apagado (paso 0.c). `enviar_recordatorios`
calcula el cupo disponible **antes** de mirar `--dry-run`
(`services/meta_reminders.py:295-302`): si `enviados_ultimas_24h` ya llegó a
15, devuelve `{'candidatos': 0, ...}` sin listar nada, con o sin dry-run. Con
el interruptor encendido, la tanda automática que dispara el paso 3 (180
segundos después del boot) se come ese cupo antes de que este paso llegue a
correr — por eso hace falta la máquina quieta.

```bash
flyctl ssh console -a scalerics-crm -C "cd /app && python -m services.meta_reminders /data/leads.db --dry-run"
```

No escribe ni manda nada. Lista los candidatos de hoy (hasta 15), cada uno
con su `numero` de contacto — los seguimientos van primero, así que puede
haber alguno con `numero` mayor a 1 mezclado con los contactos nuevos. Mirar
que sean leads plausibles y que los mails tengan cara de mails.

Si aun así imprime `{'candidatos': 0, ...}` con el interruptor apagado, es
porque ya salió una tanda real más temprano ese mismo día (antes de empezar
este procedimiento) y la ventana de 24 horas todavía la cuenta — no es una
falla de este paso, pero sí conviene entender de dónde salió antes de
seguir.

## 7. Mail de prueba — **contra una copia, no contra la base viva**

Corré esto también con el interruptor apagado (paso 0.c). No es solo por
usar una copia de la base: si el interruptor siguiera encendido, la tanda
automática del paso 3 podría salir en paralelo mientras corrés esto a mano,
y las dos corridas leen `enviados_ultimas_24h` sin coordinarse entre sí — el
mismo riesgo de cupo no reservado del paso 0.b, aplicado a este momento
puntual.

`META_NOTIFY_OVERRIDE` redirige el destinatario, pero **igual registra el
envío** en `meta_reminders` con su `numero` correspondiente. Si se corre
contra `/data/leads.db`, cada lead de la prueba queda con una fila puesta
para ese contacto sin haberlo recibido: no vuelve a aparecer en
`leads_a_recordar` (que solo mira leads sin ninguna fila) y ese contacto en
particular queda salteado para siempre — el lead va a seguir recibiendo los
contactos siguientes en la fecha que le toque, contada desde ese envío falso,
pero nunca el que se probó. Con hasta 15 leads por corrida, son hasta 15
leads reales con un hueco permanente en su secuencia. Por eso va sobre una
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

**Esto reinicia la máquina y la primera tanda de 15 mails reales sale ~3
minutos después, ya con la secuencia nueva.** Es el punto de no retorno de
este deploy: hasta acá, con el interruptor apagado desde el paso 0.c, no
salió nada; a partir de acá vuelve a haber correo saliente a terceros.

## 9. El día que sale una tanda, no tocar nada más

Cada reinicio dispara una tanda: cada `flyctl deploy`, cada `secrets set/unset`,
y cada OOM de los 256 MB de RAM. El tope diario se calcula sobre las filas de
las últimas 24 horas (`enviados_ultimas_24h`), así que dos tandas normales,
una después de la otra, no se pasan de 15. Lo que **no** está cubierto es un
reinicio que ocurra mientras la tanda anterior todavía sigue corriendo — el
riesgo descrito en el paso 0.b. Por eso, además de no tocar nada más, conviene
mirar `flyctl status` si algo se movió ese día.

## 10. Mirar rebotes, no solo envíos

En el panel de Resend, después de la primera tanda. Los mails vienen de un
formulario de Meta de hasta 5 meses de antigüedad: una tasa de rebote alta en un
dominio nuevo hace más daño a la reputación que las quejas.

Con 174 elegibles a 15 por día, el backfill del contacto 1 se drena en unos 12
días **si nada más compite por el cupo**. En la práctica no va a ser así: a
partir del décimo día empiezan a aparecer seguimientos (contacto 2), y como
`leads_a_seguir` va primero (paso 0.d), cada seguimiento le come un lugar al
backfill. El backlog real tarda más de 12 días en vaciarse — es esperado, no
hay que salir a apurarlo subiendo el tope.

---

## Si algo sale mal

**Apagar es una línea y tiene efecto en el próximo arranque:**

```bash
flyctl secrets unset META_RECORDATORIOS -a scalerics-crm
```

**Alguien recibió un mail y no debía:** no hay forma de retirarlo. Buscar la
fila puntual en `meta_reminders` por `business_id` **y `numero`** y confirmar
que sigue puesta — mientras esté, no se le vuelve a mandar ese mismo
contacto. No hace falta tocar el resto de sus filas: cada contacto es
independiente.

**Un lead quedó marcado sin haber recibido nada** (aparece en el log como
"envío incierto" o como fallo de limpieza): borrar esa fila puntual de
`meta_reminders` (`business_id` + `numero`) a mano y ese contacto vuelve a
entrar en la selección al otro día. **No borrar todas las filas del lead**:
se pierden los tokens de baja de los contactos anteriores que ya se
mandaron de verdad, y esos links quedan rotos en mails que la gente ya tiene
en su bandeja.

**Un lead que ya contestó sigue recibiendo la secuencia:** ver el paso 0.e —
no es un bug, es que nadie lo movió de `sin_contactar` en el CRM.
