# Recordatorios por mail a los leads de Meta

**Fecha:** 2026-08-17
**Repo:** `Scalerics-org/scalerics-crm` (rama `recordatorios-meta`)
**Estado:** diseño aprobado, pendiente de plan de implementación

## Contexto

Los leads de Meta Ads vuelven a entrar al CRM desde el 12-08. Los números al
14-08 son incómodos: de **216 leads de Meta, 204 están `sin_contactar`** y
**176 tienen más de un mes**. El 94% no se tocó nunca.

Esta automatización no es un recordatorio a alguien con quien ya se habló: en
la práctica es el **primer y único contacto** que esa persona va a tener. Vale
decirlo en voz alta porque condiciona el tono del mail.

## Dos bloqueos que hay que resolver antes

**1. El webhook descarta el mail.** `routes/meta.py` extrae el mail del
formulario en la línea 211:

```python
email = fields.get("email") or fields.get("correo") or ""
```

…y el `insert_business` que viene después **nunca lo pasa**. La variable se
calcula y se tira. Por eso los 216 leads tienen la columna `email` vacía
mientras el dato está guardado dentro de `form_data`. Es una línea; la columna
`email` ya está en `ALLOWED_COLUMNS`, así que no hace falta migración.

**2. Hay 174 mails enterrados.** De los 216 leads, **174 tienen mail dentro de
`form_data`** y **42 no tienen ninguno**. Hace falta un backfill que los suba a
la columna, con dry-run, igual que el script de restauración.

Los 42 sin mail quedan fuera de esta automatización para siempre: tienen
teléfono, no dirección. El alcance real es el 80% de los leads, no el 100%. Si
el formulario de Meta no pide el mail como obligatorio, conviene hacerlo.

## Alcance

Disparadores **uno a uno**: el CRM encuentra un lead que cumple una condición y
le manda **un** mail. Transaccional, sin estado de secuencia.

### Fuera de alcance

- **Secuencias de varios mails** con corte al responder. Detectar que un lead
  contestó exige leer una casilla por IMAP o la API de Gmail: es un subsistema
  entero, no una variante. Sin esa condición de corte, una secuencia le sigue
  escribiendo a alguien que ya respondió, que es peor que no mandar nada.
- **Campañas masivas** a todo el backlog de una. El goteo diario cubre el
  backlog sin el riesgo de reputación.
- Tocar los 42 leads sin mail.

## Arquitectura

Un job diario dentro del CRM. Cada corrida:

1. Selecciona leads con `source='meta'`, `crm_status='sin_contactar'`, con mail,
   sin recordatorio previo y sin desuscripción.
2. Toma los que entraron hace **3 días o más** (los nuevos) y completa hasta
   **15 por día** con los más antiguos del backlog.
3. Manda el mail desde `contacto@scalerics.com` y registra el envío.

Con 174 leads elegibles, el backlog se drena en unos 12 días sin picos.

Los dos números — 3 días y 15 por día — van en constantes con nombre, no
incrustados en la query.

## Las guardas

Importan más que el criterio de selección, porque los errores acá son visibles
para terceros:

- **Una sola vez por lead, para siempre.** Sin esto, un bug de fechas manda tres
  mails a la misma persona. Es la guarda más importante.
- **Solo `sin_contactar`.** Si alguien lo movió de estado, el mail sobra.
- **Nunca a un lead sin mail.**
- **Nunca a alguien que se desuscribió.**

## El mail

**Objetivo único: agendar en el Calendly**
(`https://calendly.com/scalerics/consultoriagratuita`). Un solo botón, sin
links que compitan.

**Personalizado con lo que cada uno pidió.** `form_data` guarda el rubro
(`¿que_es_lo_que_buscás_para_tu_negocio?`), el presupuesto, el objetivo y el
nombre del negocio. El mail puede decir *"nos dejaste tus datos por una nueva
página web para RP Estudio Notarial"* en vez de *"¿seguís interesado?"*. Es la
diferencia entre un mail que parece escrito y uno que parece un envío masivo —
que es justo lo que decide si Gmail lo manda a spam.

Todo valor que venga del formulario se escapa con `html.escape` antes de
interpolarlo: lo llena cualquiera en internet. Ya hay precedente en
`send_new_meta_lead_notification`.

**Firma institucional**, sin persona: logo
(`raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full_alt.png`,
el mismo que ya usan los mails del CRM), Scalerics, sitio, **+598 97 250 713** y
el link de agendar. Sin emojis.

**Desuscripción obligatoria.** Es correo saliente a terceros; sin eso las quejas
van directo a reputación de dominio. Dos piezas:

- Un link propio con token por lead, que marca la desuscripción en la base.
- La cabecera `List-Unsubscribe`, para que Gmail muestre su botón nativo.

## Envío

Resend, plan gratuito: **3.000 mails por mes** y **límite de 2 por segundo**. El
15 de agosto una ráfaga sin control se comió 429s masivos. El envío tiene que
espaciar los mails; a 15 por día no es una restricción real, pero el código no
debe asumirlo.

`scalerics.com` está verificado en Resend, así que `contacto@scalerics.com`
envía sin configurar nada nuevo.

**A confirmar antes de implementar:** que `contacto@scalerics.com` sea una
casilla real que alguien lee. El sitio la publica como contacto y el dominio usa
Zoho, pero no está verificado que alguien la atienda. Si no lo es, hay que poner
un `Reply-To` a una casilla que sí se lea — si no, se pierden las respuestas,
que son el resultado que la automatización busca.

## Operación

- **Dry-run** que lista a quién le mandaría, sin mandar nada.
- **Interruptor** por variable de entorno para apagarlo sin deployar.
- **Registro** de a quién, cuándo y con qué contenido, para auditar y para que
  el "una sola vez" sea verificable, no una promesa.
- Reusa `META_NOTIFY_OVERRIDE` para probar contra una casilla propia sin
  escribirle a nadie.

## Verificación

El criterio de aceptación no es que el job corra: es que llegue un mail
correcto a una dirección de prueba y que un segundo pase no lo repita.

1. El backfill sube 174 mails y deja 42 leads sin dirección. Conteo antes y
   después.
2. Un lead nuevo con mail recibe el recordatorio a los 3 días, no antes.
3. **Correr el job dos veces seguidas manda un solo mail.**
4. Un lead en cualquier estado distinto de `sin_contactar` no recibe nada.
5. El link de desuscripción marca la baja y el lead deja de ser elegible.
6. El mail se ve bien en Gmail: firma, logo, botón, y sin `&amp;lt;` visible por
   doble escapado.
7. El dry-run no escribe ni manda.

## Riesgos

| Riesgo | Mitigación |
|---|---|
| Mandar dos veces al mismo lead | Registro de envío + test de doble corrida |
| Quemar la reputación de `scalerics.com` | Goteo de 15/día, desuscripción, personalización |
| Que las respuestas se pierdan | Confirmar que alguien lee `contacto@` o poner `Reply-To` |
| Escapar de más y mostrar `&amp;lt;` | Verificar el render real en Gmail, no solo el test |
| Agotar la cuota de Resend | 15/día contra 3.000/mes deja margen de sobra |
