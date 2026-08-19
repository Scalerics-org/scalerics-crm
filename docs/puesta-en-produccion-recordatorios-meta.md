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
encendido: el paso 2b lo apaga temporalmente — **después** de la guarda del
paso 1 y del backup del paso 2, nunca antes, para que ningún reinicio caiga
sobre esas dos protecciones sin haberlas corrido — para que los pasos que
necesitan que no salga una tanda en paralelo (3b y 7) corran con la máquina
quieta; el paso 8 lo vuelve a prender al final.

Los pasos van en orden y cada uno tiene una condición de corte. Si alguno no da
lo esperado, **parar ahí** — ninguno de los siguientes lo arregla. Si parás en
cualquier punto entre el paso 2b y el 8, la automatización queda **apagada**
en producción de forma silenciosa: no hay ningún aviso más que este párrafo.
Antes de dejarlo así por hoy, decidí si es intencional; si no lo es, dejá
anotado en algún lado que hace falta volver a este runbook para terminarlo —
si no, nadie se entera de que dejó de mandar recordatorios hasta que alguien
lo note por otro lado.

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
volumen. El tope de 15 mails por día se relee antes de cada envío
(`enviados_ultimas_24h`, en `services/meta_reminders.py`), así que dos
corridas solapadas —dos máquinas, o un reinicio de Fly mientras la tanda
anterior todavía sigue corriendo— ya no mandan 15 cada una: la segunda corta
apenas la ventana de 24 horas llega al tope, y en el log queda la línea "se
corta la tanda". Lo que puede pasar igual es que se pasen de 15 por un
puñado, porque el cupo se lee pero no se reserva: dos corridas pueden leer 14
al mismo tiempo y mandar una cada una. Por eso una sola máquina corriendo
**sigue siendo parte de la garantía del tope diario, no un detalle de
costos**. Y si dos máquinas llegan a tener dos volúmenes distintos —el caso ya
conocido: Fly creó una segunda máquina sola antes en otro proyecto, no es
hipotético— ahí se pierde también la garantía de "no repetir contacto", porque
cada volumen tiene su propia tabla `meta_reminders` sin que la otra se entere.

**c) La secuencia son 7 contactos repartidos en un año, no uno.**

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
(`DIAS_DE_CADA_CONTACTO` en `services/secuencia_contactos.py`). Además tienen
que haber pasado **7 días desde el último envío** de ese lead
(`_PISO_ENTRE_CONTACTOS_DIAS`), y eso es lo que hace que un lead atrasado —la
automatización apagada un tiempo, o backlog que tardó en drenar— no reciba los
contactos 2 a 7 uno atrás de otro al pasar todos los umbrales de golpe. Para
un lead al día el piso no cambia nada: sus saltos reales son 10, 15, 90, 90,
90 y 70 días. Para uno atrasado significa un contacto por semana hasta ponerse
al día.

Cada contacto tiene su propio texto (`services/email_service.py`; el 7 dice
explícitamente que es el último) y su propio token de baja, pero la baja es
sobre la persona: quien se da de baja en cualquier contacto no recibe ninguno
de los que faltan.

El tope de 15 mails por día es compartido entre seguimientos (leads que ya
están en la secuencia) y contactos nuevos, y los seguimientos van primero.
Un día con backlog de leads nuevos puede terminar sin mandar ningún contacto
1 si el cupo se lo llevan los seguimientos.

**d) Si un lead contesta y nadie lo mueve de `sin_contactar`, sigue en la
secuencia.** El único corte por respuesta es el `crm_status`: mientras siga
en `sin_contactar` va a seguir recibiendo los 7 contactos aunque haya
contestado el primero. Es el modo de falla más probable de todo esto y la
única mitigación es la disciplina de mover el lead en el CRM apenas contesta.

---

## 1. Verificar que el arranque no vaya a pisar la base

**Primero mirar si la máquina está corriendo**, porque `flyctl ssh console`
**la arranca si está detenida** — y ahí `start.sh` corre antes de que llegues a
leer la respuesta, o sea que el chequeo destruye justo lo que iba a chequear:

```bash
flyctl status -a scalerics-crm
```

Si la máquina está `started`, entrar. Si está detenida, **no entrar por SSH**:
hay que mirar el volumen por otro lado (o asumir lo peor y restaurar del backup
del paso 2 después del arranque) antes de dejar que ese arranque decida solo.

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

## 2b. Apagar el interruptor antes de deployar

Recién ahora, con la guarda del paso 1 confirmada y el backup del paso 2 ya
hecho, es seguro tocar el interruptor. Hacerlo antes reiniciaría la máquina
sin esas dos protecciones puestas: si `start.sh` fuera a pisar la base (paso
1) o si algo saliera mal antes de tener el backup (paso 2), un reinicio de
este paso no puede ser el primero en pasar.

```bash
flyctl secrets list -a scalerics-crm | grep META_RECORDATORIOS
```

Confirmado en producción: desde el 18-8-2026 está en `on`, mandando mails
reales todos los días (`META_NOTIFY_OVERRIDE` no está seteado). El deploy del
paso 3 no se puede hacer con el interruptor prendido: 180 segundos después de
ese boot sale una tanda real con la secuencia nueva, antes de que corra un
solo paso de verificación. De los pasos que siguen, **3b y 7** necesitan la
máquina quieta porque dependen de que no salga una tanda en paralelo; **4, 5 y
6 no** — no mandan mail, así que da igual si corren con el interruptor
prendido o apagado. La única mitigación para 3b y 7 es apagarlo ahora, dejarlo
así durante toda la verificación, y volver a prenderlo recién en el paso 8:

```bash
flyctl secrets unset META_RECORDATORIOS -a scalerics-crm
```

Esto también reinicia la máquina, igual que `secrets set` — nada sale
mientras quede así. Si el comando de arriba ya mostraba que estaba apagado
(otro ambiente, o alguien lo bajó a mano), saltear este `unset`: fallaría
porque no hay nada que sacar.

## 3. Deploy

```bash
flyctl deploy -a scalerics-crm
```

Al arrancar, `init_db` (`database.py`) migra `meta_reminders` sola: agrega la
columna `numero`, cambia el `UNIQUE` a `(business_id, numero)` y, si la tabla
vieja no tenía esa columna, reconstruye la tabla completa poniendo `numero=1`
en cada fila existente (conservan su `id`, su `token` y su `sent_at`
originales). El deploy la dispara solo, sin que haya que acordarse, y es
idempotente: correrla de nuevo sobre una base ya migrada no hace nada.

Si el arranque fallara antes de llegar a ella, el plan B es correrla a mano
sobre la base viva:

```bash
flyctl ssh console -a scalerics-crm -C "cd /app && python -c \"import database; database.init_db('/data/leads.db')\""
```

(Es el mismo camino que corre el arranque. Con el backup del paso 2 ya hecho,
que es la condición para tocar esto a mano.)

El interruptor quedó apagado en el paso 2b, así que el job no arranca
todavía — recién lo hace en el paso 8. Comparar la imagen del log propio
contra `flyctl status`: si no coinciden, otra sesión deployó encima — no
seguir.

## 3b. Verificar que la migración de `meta_reminders` sobrevivió

El día del deploy:

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3;c=sqlite3.connect('/data/leads.db');print('filas:',c.execute('SELECT COUNT(*) FROM meta_reminders').fetchone()[0]);print('numeros:',c.execute('SELECT numero,COUNT(*) FROM meta_reminders GROUP BY numero').fetchall())\""
```

Esperado: la misma cantidad de filas que contaste en el paso 2, y todas con
`numero=1`. Con el interruptor apagado desde el paso 2b no debería haber
salido ninguna tanda entre el deploy y este chequeo, así que el número tiene
que cerrar exacto. Si por algún motivo el interruptor seguía encendido en
este punto, puede haber hasta 15 filas de más —la tanda automática de 180
segundos después del boot— y ahí conviene entender de dónde salieron antes
de seguir, no asumir que la migración duplicó datos.

Unos días después, con la secuencia ya corriendo, el mismo comando tiene que
mostrar varios números: el 1 sigue siendo mayoría (es el que reciben los
leads nuevos, hasta 15 por día) pero van a empezar a aparecer filas con
`numero=2`.

**El reloj de cada lead es su propio `sent_at`, no la fecha del deploy.** Las
filas migradas vienen del 18-8-2026, o sea que son anteriores a este deploy: si
el deploy pasa diez días o más después de esa fecha, aparecen `numero=2` el
primer día, y eso es correcto, no una anomalía. El chequeo que sí sirve es por
fila, con esta consulta, que lista los contactos que salieron antes de lo que
les tocaba según la tabla del paso 0.c:

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3;c=sqlite3.connect('/data/leads.db');print(c.execute('SELECT r.business_id, r.numero, f.primero, r.sent_at FROM meta_reminders r JOIN (SELECT business_id, MIN(sent_at) primero FROM meta_reminders GROUP BY business_id) f ON f.business_id = r.business_id WHERE r.numero > 1 AND julianday(r.sent_at) - julianday(f.primero) < CASE r.numero WHEN 2 THEN 10 WHEN 3 THEN 25 WHEN 4 THEN 115 WHEN 5 THEN 205 WHEN 6 THEN 295 WHEN 7 THEN 365 END').fetchall())\""
```

Tiene que devolver una lista vacía. Si devuelve filas —un contacto que salió
antes de los días que le correspondían desde el **primer** envío de ese lead—
es señal de un reloj desincronizado en la máquina, de una fila migrada con un
`sent_at` corrido, o de que alguien corrió el backfill o una prueba contra la
base viva en vez de una copia (paso 7). No se autoarregla: hay que mirar el
`sent_at` de esa fila puntual y decidir a mano.

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

```bash
flyctl ssh console -a scalerics-crm -C "cd /app && python -m services.meta_reminders /data/leads.db --dry-run"
```

Va directo contra la base viva y no hace falta liberar nada: el `--dry-run` no
escribe ni manda, así que no gasta cupo, y por eso tampoco lo respeta — lista
con el cupo entero aunque la tanda de hace un rato ya se haya llevado los 15.
(Antes cortaba por cupo antes de mirar el flag, y como en producción el cupo
está en 0 buena parte del día, este paso obligaba a un `cp` y un `DELETE` a
mano dentro de la máquina de producción. Ya no.)

Lista los candidatos (hasta 15), cada uno con su `numero` de contacto — los
seguimientos van primero, así que puede haber alguno con `numero` mayor a 1
mezclado con los contactos nuevos. Ojo con lo que estás mirando: como ignora
el cupo, la lista es "a quién le tocaría si arrancara una tanda con los 15
libres", no el resultado exacto de la próxima tanda real.

Si querés saber cuánto cupo hay de verdad (informativo, no hace falta para
seguir):

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3;c=sqlite3.connect('/data/leads.db');print('ultimas_24h:',c.execute('SELECT COUNT(*) FROM meta_reminders WHERE sent_at >= datetime(?, ?)', ('now','-1 day')).fetchone()[0]);print('ultimo_envio:',c.execute('SELECT MAX(sent_at) FROM meta_reminders').fetchone()[0])\""
```

Si `ultimas_24h` da 15, el cupo real es 0 y recién se libera unas 24 horas
después de `ultimo_envio` (cada tanda manda sus hasta 15 mails en unos
segundos, así que esa hora alcanza como aproximación). Importa para el paso 7,
que sí manda de verdad, y para el paso 8.

**Condición de corte:** si la lista sale vacía, no sigas — revisá
`_FILTRO_LEAD_ELEGIBLE` (`crm_status`, `email`) contra la base real antes de
continuar; puede ser que hoy no queden leads elegibles, y ahí conviene decidir
con Juan si seguir igual.

## 7. Mail de prueba — **contra una copia, no contra la base viva**

Corré esto con el interruptor apagado (paso 2b). No es solo por usar una
copia de la base: si el interruptor siguiera encendido, la tanda automática
del paso 3 podría salir en paralelo mientras corrés esto a mano, y las dos
corridas leen `enviados_ultimas_24h` sin coordinarse entre sí — el mismo
riesgo de cupo no reservado del paso 0.b, aplicado a este momento puntual.

**Además, y aunque el interruptor esté apagado: sin cupo libre no sale
nada.** A diferencia del paso 6, este paso manda de verdad, así que sí
respeta el cupo — y la automatización viene mandando una tanda real cada 24
horas desde el 18-8-2026, con lo cual buena parte del día el cupo ya está en
0 (chequealo con el segundo comando del paso 6). El remedio es liberarlo **en
la copia**, nunca en `/data/leads.db`.

`META_NOTIFY_OVERRIDE` redirige el destinatario, pero **igual registra el
envío** en `meta_reminders` con su `numero` correspondiente. Si se corre
contra `/data/leads.db`, cada lead de la prueba queda con una fila puesta
para ese contacto sin haberlo recibido: no vuelve a aparecer en
`leads_a_recordar` (que solo mira leads sin ninguna fila) y ese contacto en
particular queda salteado para siempre — el lead va a seguir recibiendo los
contactos siguientes en la fecha que le toque, contada desde ese envío falso,
pero nunca el que se probó. Con hasta 15 leads por corrida, son hasta 15
leads reales con un hueco permanente en su secuencia. Por eso va sobre una
copia, con el cupo de las últimas 24 horas liberado en esa misma copia:

```bash
flyctl ssh console -a scalerics-crm
cp /data/leads.db /data/prueba.db
python -c "import sqlite3;c=sqlite3.connect('/data/prueba.db');c.execute('DELETE FROM meta_reminders WHERE sent_at >= datetime(?, ?)', ('now','-1 day'));c.commit()"
cd /app && META_NOTIFY_OVERRIDE=juantomasetti240@gmail.com \
  python -m services.meta_reminders /data/prueba.db
rm /data/prueba.db
```

El `DELETE` borra, solo en la copia, las filas con `sent_at` de las últimas 24
horas, para no depender de cuándo salió la última tanda real. Es legítimo
porque `prueba.db` se borra al final y nunca se escribe sobre
`/data/leads.db`: no libera cupo real, solo el de la copia descartable. Por eso
el lead que reciba el mail de prueba puede no ser el que le tocaría de verdad
hoy — lo que valida esto es que el camino completo (selección, armado del mail,
registro, envío) funciona, no cuál lead puntual sale.

**Condición de corte:** si con el `DELETE` ya aplicado el comando de arriba
sigue sin mandar nada (revisá el log en la consola), no sigas a la revisión
de Gmail — no hay nada que revisar.

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

Esto reinicia la máquina, y 180 segundos después arranca el hilo con la
secuencia nueva. **Si todavía queda cupo en la ventana de 24 horas, manda
hasta 15 mails reales en ese momento.** Si el cupo ya está en 0 —por ejemplo
si la última tanda real salió hace pocas horas, antes de empezar este
procedimiento; chequealo con el comando del paso 6— esa primera corrida no
manda nada y espera al día siguiente, que es el comportamiento normal, no
una falla. De cualquier manera, este es el punto de no retorno de este
deploy: hasta acá, con el interruptor apagado desde el paso 2b, no podía
salir nada; a partir de acá la automatización vuelve a estar en marcha y
puede mandar correo real a terceros en cualquier momento.

## 9. El día que sale una tanda, no tocar nada más

Cada reinicio dispara una tanda: cada `flyctl deploy`, cada `secrets set/unset`,
y cada OOM de los 256 MB de RAM. El tope diario se calcula sobre las filas de
las últimas 24 horas (`enviados_ultimas_24h`) y se relee antes de cada envío,
así que dos tandas —una después de la otra, o incluso solapadas— no se pasan
de 15 más que por un puñado: el riesgo del paso 0.b, acotado pero no
eliminado, porque el cupo se lee y no se reserva. Por eso, además de no tocar
nada más, conviene mirar `flyctl status` si algo se movió ese día.

## 10. Mirar rebotes, no solo envíos

En el panel de Resend, después de la primera tanda. Los mails vienen de un
formulario de Meta de hasta 5 meses de antigüedad: una tasa de rebote alta en un
dominio nuevo hace más daño a la reputación que las quejas.

Con 174 elegibles a 15 por día, el backlog de contactos 1 se drenaría en unos
12 días **si nada más compitiera por el cupo**. No es el caso: a partir del
décimo día empiezan a aparecer seguimientos (contacto 2), y como
`leads_a_seguir` va primero (paso 0.c), cada seguimiento le come un lugar al
backlog. **Puede tardar meses, no días.** Simulado sobre un backlog del orden
del real: con 144 leads y sin leads nuevos entrando, el último contacto 1 sale
el día 44; con 5 leads nuevos por día, el día 346. Es esperado y no hay que
salir a apurarlo subiendo el tope: el tope de 15 es lo que protege la
reputación del dominio, y el orden (seguimientos primero) es deliberado
porque un seguimiento a destiempo pierde sentido y un primer contacto no.

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
entrar en la selección. Si era un seguimiento (`numero` mayor a 1), no
necesariamente al otro día: con la fila borrada, el piso de 7 días pasa a
contarse desde el envío anterior que le quede al lead, así que puede tardar
hasta una semana en reaparecer. **No borrar todas las filas del lead**:
se pierden los tokens de baja de los contactos anteriores que ya se
mandaron de verdad, y esos links quedan rotos en mails que la gente ya tiene
en su bandeja.

**Un lead que ya contestó sigue recibiendo la secuencia:** ver el paso 0.d —
no es un bug, es que nadie lo movió de `sin_contactar` en el CRM.
