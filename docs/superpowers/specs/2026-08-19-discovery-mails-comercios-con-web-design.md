# Discovery por mail a comercios con sitio web — Diseño

**Fecha:** 2026-08-19
**Estado:** aprobado, pendiente de plan de implementación

## El problema

Scalerics tiene 1753 negocios en el CRM y sólo 211 direcciones de mail, casi todas
de gente que ya se contactó sola (172 leads de Meta, 36 de Calendly). De los 1467
comercios scrapeados —el público natural de una campaña de captación— hay **3
direcciones de mail entre todos**.

La causa no es un scraper incompleto: esos 1467 fueron scrapeados justamente
**porque no tienen sitio web**, que es el criterio con el que `scraper.py` los
selecciona hoy. Sin sitio no hay dónde esté publicada una dirección. El campo
`status` de la tabla lo registra sobre los 1467: `scraped: 1460`, `email_found: 3`,
`demo_deployed: 2`, `no_email: 1`, `contacted: 1`. La búsqueda de mail ya se
intentó y encontró tres.

O sea: el padrón actual no es alcanzable por mail, y ninguna automatización de
correo lo va a cambiar.

## La evidencia

Se corrieron dos sondeos sobre Google Maps midiendo, punta a punta, qué fracción
de los comercios vistos termina con una dirección de mail utilizable. El código
del sondeo fue descartable y no quedó en el repo.

| | Inmobiliarias | Peluquerías |
|---|---|---|
| Vistos en Maps | 20 | 21 |
| Con sitio web | 18 (90%) | 3 (14%) |
| Sitios que abrieron | 16 | 3 |
| Con mail | 14 (78% de los que tienen web) | 3 |
| — de dominio propio | 9 | 1 |
| — personales (gmail, hotmail) | 5 | 2 |
| **Tasa final (mail / visto)** | **70%** | **14%** |

Dos conclusiones:

1. **Extraer el mail de un sitio funciona.** De los 18 sitios, 16 abrieron y 14
   dieron dirección: 78% de los que tienen web, 87% de los que efectivamente
   abrieron. Más de la mitad son casillas de contacto de dominio propio
   (`info@imas.uy`, `hola@acsa.uy`, `info@urbankey.com.uy`), no el Gmail personal
   del dueño.
2. **El rubro decide el rendimiento, y por un factor de cinco.** Scrapear rubros
   con baja presencia web quema horas para sacar tres direcciones.

Ritmo medido: ~4,4 negocios por minuto de scraping. En inmobiliarias eso son unas
3 direcciones por minuto, o **500 direcciones en unas 3 horas**. En peluquerías,
las mismas 500 son más de 15 horas.

**Muestra falsa detectada:** uno de los sitios devolvió `usuario@dominio.com`, una
dirección de ejemplo que quedó en la plantilla de la web. Descontándola, la tasa
real es 13 de 16. El filtrado de direcciones basura es un requisito, no un pulido.

## Alcance

**Público:** comercios uruguayos **con** sitio web, scrapeados de Google Maps,
priorizando los rubros de alta presencia web. Confirmado: inmobiliarias (90%).
Candidatos por analogía, a confirmar al scrapear: automotoras, concesionarias,
odontología. Peluquerías queda **fuera** de este canal por su 14%.

**Oferta:** a estos comercios no se les vende una página web —ya la tienen— sino
automatizaciones, tienda online o software a medida.

**Fuera de alcance:** el padrón sin web (1467 comercios). Ese público es alcanzable
por WhatsApp, donde hay 1467 teléfonos y un `pitch_text` ya escrito para cada uno.
Es un proyecto aparte y probablemente de mayor retorno, pero no es éste.

## Las cuatro decisiones de fondo

### La extracción de mails va en un paso aparte, no dentro del scraper

El scraper guarda el negocio con su `website`; un segundo trabajo recorre los que
tienen web y busca la dirección.

El scraper contra Google Maps ya es lento y frágil. Colgarle la visita al sitio de
cada negocio hace que un timeout en una web rompa el scrape de Maps, que es la
parte cara. Separados, la extracción se puede volver a correr —cuando mejore el
extractor, por ejemplo— sin volver a scrapear Maps.

### Módulo nuevo, no generalizar el motor de Meta

`services/meta_reminders.py` se encendió en producción el 19-8-2026 y le manda
correo real a terceros todos los días. Convertirlo en un motor genérico para
meterle una segunda campaña con reglas distintas es tocar lo único que funciona
para agregarle un caso de uso que todavía no existe.

Se acepta duplicar unas 150 líneas del bucle de envío. Si aparece una tercera
campaña, ahí se unifica con tres casos reales a la vista en vez de dos.

### Subdominio de envío propio

`novedades.scalerics.com`, verificado en Resend con DKIM propio.

El correo en frío genera quejas por bien hecho que esté. Saliendo de
`scalerics.com` esas quejas degradan la entrega de los recordatorios de Meta —que
sí son opt-in— y del correo con clientes. El subdominio aísla la reputación; no es
aislamiento perfecto, pero es la práctica estándar y alcanza.

**`Reply-To` apunta a `contacto@scalerics.com`**, para que las respuestas caigan en
la casilla que ya se mira y no en uno nuevo que nadie abre.

### Dos contactos, no siete

Día 0 y día 7, y ahí termina la vida del lead en esta campaña.

La secuencia de 7 contactos de Meta es para gente que llenó un formulario pidiendo
ser contactada. Siete mails a lo largo de un año a alguien que nunca pidió nada es
el perfil que se marca como spam, y cada marca se paga con entrega. Dos toques
capturan casi todo lo que un frío rinde.

## Arquitectura

### 1. Scraper: modo "con web"

**Cambia:** `scraper.py`, el bloque que hoy descarta (`if data.get("maps_website_url")`).

Se agrega una bandera de modo que invierte el criterio: en modo discovery se queda
**solo** con los negocios que tienen sitio web y guarda el dominio. El modo por
defecto no cambia, para no alterar el scraping del padrón sin web.

**Cambia:** `businesses` gana una columna `website TEXT`. Hoy `maps_website_url` se
extrae y se descarta sin persistirse.

Los negocios de esta campaña entran con `source='discovery'`. Las tres cohortes
quedan separadas y ninguna automatización puede alcanzar al público de otra:

| Cohorte | `source` | Canal |
|---|---|---|
| Comercios sin web | `NULL` | WhatsApp (futuro) |
| Leads de Meta | `'meta'` | Secuencia de recordatorios (en producción) |
| Comercios con web | `'discovery'` | Esta campaña |

### 2. Extractor de mails

**Nuevo:** un job sobre los negocios con `website` no nulo y `email` nulo.

Para cada uno: entra a la home y a `/contacto` (y variantes), junta las direcciones
de los `href="mailto:"` y del HTML, filtra y guarda la primera buena en `email`.
Los `mailto:` explícitos tienen prioridad sobre una dirección suelta en el texto.

**Filtro de basura, obligatorio.** Se descartan:

- direcciones de ejemplo de plantillas: `usuario@dominio.com`, `@example`,
  `@tudominio`, `@sitio`
- `noreply@` y `no-reply@`
- direcciones de los proveedores de la plantilla (Wix, Squarespace, GoDaddy)
- falsos positivos del regex sobre nombres de archivo (`.png`, `.jpg`)

Mandarle a una dirección de ejemplo es daño puro a la reputación sin ninguna
chance de venta.

**Registra el intento fallido**, para no reintentar eternamente sobre sitios que no
publican dirección. Un sitio que no dio mail no se vuelve a visitar salvo que se
pida explícitamente.

### 3. Envío

**Nuevo:** `services/discovery_emails.py`, con la forma de `meta_reminders.py`.

**Nueva tabla `discovery_reminders`**, misma estructura que `meta_reminders`: una
fila por contacto, `UNIQUE(business_id, numero)`, token de baja único por fila,
`sent_at`, `unsubscribed_at`.

**Calendario:** contacto 1 el día 0, contacto 2 a los 7 días del primero. El
contacto 2 es el último de la vida del lead en esta campaña.

**Tope diario propio**, independiente del de Meta, arrancando en **10 por día**. El
subdominio nace sin reputación y una tanda grande el primer día es la peor forma de
estrenarlo. El tope sube a mano una vez que haya historial de entrega limpio.

**Textos:** generados por rubro, no uno para todos, con el mismo criterio que el
`pitch_text` existente pero con otro contenido — a estos se les ofrece automatizar
algo de su operación, no una página web. Cada número manda un texto distinto.

**Ruta de baja propia**, separada de la de Meta, con su propio token.

### 4. Rebotes

Los rebotes duros se registran y esa dirección no se vuelve a usar nunca, en
ninguna de las dos campañas.

En una lista armada raspando sitios web parte de las direcciones van a estar
muertas. Insistirle a direcciones muertas es la vía más rápida a que un dominio
quede bloqueado, y es el riesgo específico de este canal frente al de Meta, donde
las direcciones las tipeó su dueño hace poco.

### 5. Supresión

Un solo chequeo antes de cada envío. No se le escribe a un negocio que:

- tiene un `crm_status` distinto de `sin_contactar` — o sea, cualquiera que ya se tocó
- se dio de baja alguna vez, en esta campaña **o** en la de Meta
- comparte dirección de mail con un lead de Meta o con un cliente actual
- **comparte dirección de mail con otro comercio de la propia cohorte de discovery**
- rebotó antes

La comparación de direcciones es normalizada (minúsculas, sin espacios), igual que
la deduplicación que ya hace `leads_a_recordar`.

**Sobre la deduplicación dentro de la cohorte, que no es hipotética.** La primera
corrida real del padrón, el 20-8-2026, trajo 17 inmobiliarias y 13 direcciones, de
las cuales **12 son únicas**: "Inmobiliaria Mas Aguada" e "Inmobiliaria Mas Rodo"
son sucursales de la misma firma, con teléfonos y fichas de Google Maps distintas
—por eso entraron como dos filas, el `UNIQUE` de `phone` y `maps_url` no las
fusiona— pero con el mismo sitio y el mismo `info@imas.uy`. Sin esta regla, esa
persona recibe el mismo mail en frío dos veces, que es la forma más rápida de que
marque spam.

La garantía tiene que vivir en el `WHERE` de la consulta que elige a quién le toca,
igual que en `leads_a_recordar` (`GROUP BY LOWER(TRIM(email))` más un `NOT EXISTS`
sobre los ya enviados). No alcanza con deduplicar la lista en memoria después de
leerla: entre tandas distintas, la segunda sucursal volvería a aparecer.

## Lo que deliberadamente no lleva

- **Sin píxel de apertura.** Suma señal de spam y es un problema de privacidad.
- **Sin A/B testing.** Con 10 mails por día no hay volumen para que un test
  signifique algo.
- **Sin detección automática de respuestas.** Las respuestas llegan a
  `contacto@scalerics.com` y las lee una persona.

## Criterios de verificación

1. El scraper en modo discovery guarda negocios **con** web y ninguno sin web; el
   modo por defecto sigue guardando sólo los que no tienen.
2. La columna `website` queda poblada para los negocios de `source='discovery'`.
3. El extractor descarta `usuario@dominio.com`, `noreply@` y las direcciones de
   proveedores de plantillas, verificado con un test sobre HTML de muestra.
4. Un sitio que no publica mail queda marcado y no se reintenta.
5. Ningún negocio recibe dos veces el mismo número de contacto.
6. El contacto 2 sale a los 7 días del 1 y no antes; después del 2 el lead no
   vuelve a entrar nunca.
7. Una baja saca al negocio de esta campaña **y** de la de Meta.
8. Un negocio cuya dirección coincide con la de un lead de Meta no recibe nada.
9. Un rebote duro deja la dirección fuera para siempre.
10. La automatización de Meta no cambia su comportamiento en nada.

## Riesgos

**El subdominio tiene una espera que no depende de nosotros.** Crear los registros
DNS en Cloudflare, esperar propagación y que Resend verifique. Conviene arrancarlo
en paralelo desde el primer día: es lo único que puede frenar el lanzamiento cuando
todo lo demás esté listo.

**Un `mailto:` publicado no es un consentimiento.** Que la dirección esté en una web
pública no significa que hayan pedido que se les escriba. Esto es correo en frío de
verdad, y obliga a identificarse claramente como Scalerics, a un link de baja que
funcione desde el primer mail, y a respetar la baja para siempre.

**El rendimiento por rubro está medido sobre dos rubros.** Inmobiliarias al 90% y
peluquerías al 14% son datos; que automotoras u odontología estén arriba es una
apuesta razonable, no un hecho. Conviene medir cada rubro nuevo con una corrida
corta antes de dedicarle horas de scraping.

## Lo que confirmó la primera corrida real (20-8-2026)

17 inmobiliarias de Montevideo scrapeadas con `--con-web`, en 4,5 minutos, y después
`buscar-mails` sobre ellas:

```
{'revisados': 17, 'con_mail': 13, 'sin_mail': 2, 'no_abrio': 2}
```

**13 de 17 = 76%**, contra el 78% que había predicho el sondeo. La predicción se
sostuvo contra sitios reales, y ninguna dirección de plantilla se coló: las que
salieron son casillas de contacto de verdad (`info@imas.uy`, `hola@acsa.uy`,
`inmobiliaria@lars.com.uy`, `casacentral@sigaloavarela.com`).

Los tres caminos se comportaron distinto, como se diseñó: los 13 con mail quedaron
en `email_found`; los 2 que abrieron sin publicar dirección, en `no_email` y no se
reintentan; los 2 que **no abrieron** quedaron sin marcar, para que la corrida
siguiente los reintente.

**Detalle cosmético a resolver antes de que el texto del mail use la ciudad:** el
scraper guarda `city` como "Departamento de Montevideo", no "Montevideo".

## Orden de construcción

El orden de la lista no es el orden de construcción. Primero el scraper y el
extractor: hasta no tener 200 o 300 direcciones reales cargadas no hay a quién
mandarle nada, y es donde puede aparecer una sorpresa. El envío es la parte que ya
se sabe que funciona, porque es la forma del motor que ya corre en producción.

El subdominio arranca en paralelo desde el día uno.
