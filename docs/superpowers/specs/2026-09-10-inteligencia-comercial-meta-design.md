# Inteligencia comercial sobre Meta Ads

**Fecha:** 10/9/2026 · **Sesión:** G · **Estado:** diseño aprobado, sin implementar

Un módulo que mide la máquina de Meta Ads de punta a punta —del peso gastado en
un anuncio hasta la reunión que salió de ahí— y le pide a un modelo que lea esos
números y diga dónde conviene invertir el esfuerzo.

---

## 1. Por qué este módulo y no el que se pidió originalmente

El pedido original era un sistema de inteligencia comercial completo en 19
secciones: enriquecimiento de empresas por dominio, análisis automático de webs,
lectura de conversaciones, lead score, probabilidad de cierre entrenada con
resultados reales, detección de necesidades no declaradas y aprendizaje continuo.

Antes de diseñar nada se midió la base (`backups/leads_pre_preclientes_8sep.db`,
8.357 leads). Lo que apareció:

| Lo que el pedido asumía | Lo que hay |
|---|---|
| Leads cerrados para entrenar un modelo | **4** `finalizado`, 3 filas en `budgets` |
| Conversaciones para analizar | **0** transcripciones, 43 notas en `call_logs` |
| Leads entrantes que consultaron algo | ~311 (238 Meta + 73 Calendly) |
| Leads de discovery scrapeados en frío | 6.575, que nunca pidieron nada |

**Un modelo predictivo con 4 positivos no es un modelo.** Las secciones de
probabilidad de cierre entrenada y aprendizaje continuo no son "fase 3": son
imposibles hasta que haya decenas de cierres, y forzarlas ahora produciría
números con decimales y sin sustento.

El segundo hallazgo reordenó la prioridad: **los 4 cierres y los 3 presupuestos
son todos de Meta.** Discovery aportó 6.575 leads y cero cierres. El embudo
comercial de Scalerics *es* Meta Ads. Por eso el módulo apunta ahí: no es
recortar el alcance, es apuntarle al único lugar donde hay señal.

El trabajo se descompuso en tres proyectos independientes. Este spec cubre el
primero.

- **A — Radiografía comercial de Meta Ads.** Este documento.
- **B — Ficha de empresa enriquecida.** Enrichment por dominio, diagnóstico web
  automático, trazabilidad de fuentes. Aplica a los 6.116 leads con web.
- **C — Copiloto del vendedor.** Lead score explicable, próxima acción, resumen
  post-reunión. Depende de que A y B existan y de que empiecen a cargarse notas.

## 2. Qué contesta y qué no

**Contesta:** dónde conviene invertir el esfuerzo comercial y publicitario. Se
mide con contactabilidad, interés y avance en el embudo, no con ventas.

**No contesta:** cuál es la probabilidad de que un lead concreto cierre. Eso
necesita historia que todavía no existe, y aparece en el proyecto C.

## 3. Alcance

Dentro:

- Sincronizar gasto y performance de Meta Ads (Marketing API) por campaña y día.
- Unir esos datos con el destino real de cada lead dentro del CRM.
- Calcular un dossier determinista de métricas, con tamaños de muestra e
  intervalos de confianza.
- Que un modelo lea ese dossier y escriba hallazgos con evidencia y
  recomendaciones, sin poder inventar un número.
- Un panel nuevo con el nivel de gráficos de un dashboard de ads comercial.
- Una corrida semanal automática que guarda snapshot para comparar contra la
  semana anterior.

Fuera:

- Ejecutar cambios en Meta (pausar campañas, mover presupuestos). El módulo
  recomienda; la decisión es de una persona. Con 50-85 leads por campaña, una
  recomendación mal calibrada podría apagar la campaña que factura.
- Cohortes de discovery y Calendly. Se pueden sumar después; el eje es Meta.
- Cualquier scoring por lead individual.

## 4. Los datos que ya existen

Medido sobre el backup del 8/9. La cohorte `source='meta'` son 238 leads entre
el 11/3/2026 y el 4/9/2026.

**Campañas** (hoy embebidas como texto en `businesses.notes`, con el formato
`"Meta Lead Ad · <campaña>"`):

| Campaña | Leads |
|---|---|
| Leads - Form - 2026 | 85 |
| Leads - UY - 2026 | 83 |
| Leads - ARG - CH - 2026 | 51 |
| (sin campaña) | 13 |
| Leads - ARG - 2026 | 5 |
| Leads - Abril 2026 | 1 |

**Estados:** 27 `sin_contactar`, 54 `llamar_despues`, 57 `no_interesa`, 39
`interesado`, 12 `reunion_agendada`, 21 `reunion_hecha`, 20
`presupuesto_enviado`, 4 `en_desarrollo`, 4 `finalizado`. El 89% se trabajó de
verdad.

> **Ojo: esos son los nombres viejos.** El backup es anterior a la migración de
> estados, que ya corrió en producción. Hoy el vocabulario es
> `demo_agendada`, `demo_1`, `follow_up_1`, `cerrado`; el mapa vive en
> `database._MAPA_ESTADOS_VIEJOS`. **`lead_events` mezcla las dos épocas**
> —la migración reescribió `businesses.crm_status` pero no el historial— así
> que cualquier lectura de eventos tiene que normalizar antes de comparar.
> El módulo nunca escribe nombres de estado a mano: usa el orden y las
> funciones de §5.1.

**Actividad asociada:** 242 filas en `lead_events`, 46 en `meetings`, 255 en
`meta_reminders`. Cero filas en `call_logs` para esta cohorte —las llamadas a
leads de Meta se registran pintando la planilla de semáforo, no en el CRM.

**El formulario de Meta es la mina de oro sin explotar.** `form_data` guarda
respuestas declaradas por el propio lead:

| Campo | Distribución |
|---|---|
| Qué busca para su negocio (191) | automatizaciones 55 · nueva página web 48 · software a medida 44 · ecommerce 43 |
| Presupuesto para el proyecto (191) | aún no lo sé 104 · menos de USD 500 → 50 · USD 500-1.000 → 19 · **más de USD 1.000 → 17** |
| Objetivo del año (191) | crecer 53 · generar eficiencias 52 · lanzar mi negocio 51 · continuar vendiendo 34 |
| Ciudad (191) | Montevideo 50, Maldonado 7, Rosario 6, Buenos Aires 3, … |

Ningún reporte usa esto hoy. Es la variable de segmentación más valiosa que hay
y no cuesta traerla: ya está guardada.

**El semáforo de la planilla ya está integrado.** `services/planilla_semaforo.py`
importa los estados reales que los CEO pintan a mano en Google Sheets. Ese es el
"Excel" del pedido original y entra al dossier a través de `crm_status`, sin
integración nueva.

## 5. Convivencia con el módulo de Finanzas

**Descubierto el 10/9, con el spec ya escrito:** mientras se diseñaba esto, otra
sesión mergeó `services/finanzas.py` a `main`. Trae una vista «Pauta» que se
superpone con parte de lo especificado acá.

Lo que Finanzas **ya calcula** (`rendimiento_pauta`, `services/finanzas.py:505`),
mes a mes y sobre el total: inversión en pauta, leads, leads calificados, demos,
ventas, ingresos, CPL, costo por calificado, costo por demo, costo por venta y
ROI.

Esto obliga a dos correcciones del diseño, y las dos son innegociables.

### 5.1 El embudo se define en un solo lugar

Finanzas cuenta las etapas leyendo **`lead_events` acumulado**: un lead
«alcanzó» una etapa si pasó por ella *o por cualquiera posterior*, alguna vez,
aunque hoy figure en `no_interesa`. El borrador de este spec contaba desde
`crm_status`, que es el estado de hoy.

Son dos respuestas distintas a la misma pregunta, en dos paneles del mismo CRM.
**Eso es peor que no tener el módulo**: nadie sabría a cuál creerle.

Resolución: se extrae la lógica de etapas de `services/finanzas.py` a
**`services/embudo.py`**, y los dos módulos la importan de ahí. Se mueven tal
cual, sin cambiar comportamiento:

| Nombre | Qué es |
|---|---|
| `FUNNEL` | la lista ordenada de 14 etapas |
| `EXCLUIDOS_DEL_FUNNEL` | `en_espera` y `rechazo`, que no son avances |
| `normalizar_estado(estado)` | traduce un nombre viejo al nuevo |
| `alcanzo(eventos, etapa)` | si el lead pasó por esa etapa o una posterior |
| `dividir(num, den)` | división, o `None` si el denominador es cero |
| `costo(inversion, cantidad)` | costo unitario, o `None` — un mes sin ventas **no** tiene costo por venta de cero |

`services/finanzas.py` queda importando desde ahí y conserva alias privados
(`_dividir = dividir`) para no tocar sus llamadas internas ni sus tests.
**Es territorio de otra sesión: va anotado en `COORDINACION.md` y el criterio es
no cambiarle ni un comportamiento.** Los tests de Finanzas tienen que pasar
idénticos antes y después.

### 5.2 El gasto tiene dos fuentes y se concilian

Finanzas toma la inversión de movimientos cargados **a mano** (egresos con
`categoria = 'publicidad'`, mensuales, en USD). Este módulo la trae de la
**Marketing API** (diaria, por campaña, en la moneda de la cuenta).

No se unifican y no se pisan. Cada uno guarda lo suyo y el módulo expone la
**brecha**: `conciliacion.gasto_meta` contra `conciliacion.gasto_cargado`, por
mes. Esa comparación vale por sí sola —dice si se está registrando en la
contabilidad todo lo que Meta efectivamente cobró— y es un hallazgo que el
informe levanta cuando la diferencia supera el 10%.

Este módulo **no escribe nunca en `finanzas_movimientos`.**

### 5.3 Qué aporta este módulo que Finanzas no tiene

Para que el recorte quede claro: Finanzas responde «cuánto costó y cuánto
volvió», mensual y global. Este módulo responde «de dónde vino y a quién
apuntarle», y lo hace con lo que Finanzas no mira:

- Grano por **campaña** y por **día** (Finanzas es mensual y global).
- Las métricas del anuncio: impresiones, clics, alcance, CTR, CPM, CPC.
- La segmentación por lo que el lead **declaró en el formulario**.
- Los intervalos de confianza, para no decidir sobre ruido.
- La lectura con IA.

**Corrección al borrador:** este spec decía que los tiles de «valor del lead»
eran imposibles por falta de plata asignada. Es falso desde que existe Finanzas:
`finanzas_movimientos` tiene ingresos por cliente, y de ahí salen ROI y costo por
venta reales. El módulo no los recalcula —los toma de `rendimiento_pauta`— y los
muestra como contexto junto a sus propias métricas.

## 6. Arquitectura

```
Meta Marketing API (Insights)          CRM (SQLite en el volumen de Fly)
   gasto, impresiones, clics    ──┐    businesses · lead_events · meetings
                                  │    meta_reminders · budgets · form_data
                                  ▼
                        services/meta_insights.py
                          (sync idempotente)
                                  │
                                  ▼
                        services/radiografia.py
              Dossier determinista: cada métrica con id,
              valor, numerador, denominador, n e IC
                                  │
                                  ▼
                       services/radiografia_ia.py
              Opus 5 lee SOLO el dossier · escribe hallazgos
              citando ids · validador rechaza números ajenos
                                  │
                                  ▼
                        tabla radiografias (snapshot)
                                  │
                     ┌────────────┴────────────┐
                     ▼                         ▼
            routes/marketing.py          GitHub Actions
            panel + static/charts.js     cron semanal
```

Todo corre en la máquina de Fly. Ninguna pieza corre en la computadora de nadie.

**La propiedad que hace barato el enfoque:** el modelo nunca ve filas, solo el
dossier. El dossier pesa lo mismo con 238 leads que con 20.000, así que el costo
por corrida no crece con la base.

## 7. Modelo de datos

Todo aditivo. Ninguna migración altera una tabla o consulta existente.

### 7.1 Columnas nuevas en `businesses`

```sql
ALTER TABLE businesses ADD COLUMN meta_campaign_id   TEXT;
ALTER TABLE businesses ADD COLUMN meta_campaign_name TEXT;
ALTER TABLE businesses ADD COLUMN meta_adset_id      TEXT;
ALTER TABLE businesses ADD COLUMN meta_ad_id         TEXT;
ALTER TABLE businesses ADD COLUMN meta_ad_name       TEXT;
CREATE INDEX IF NOT EXISTS idx_biz_meta_campaign ON businesses(meta_campaign_id);
```

Hoy la campaña vive como texto dentro de `notes`. Se saca a columna por dos
motivos: un `LIKE` sobre `notes` no escala ni agrupa, y el nombre de una campaña
puede cambiar en Meta mientras el id no.

**Backfill de los 238 existentes:** parsear `notes` con
`^Meta Lead Ad\s*·\s*(.+)$` sobre la primera línea y escribir
`meta_campaign_name`. Los ids no se pueden recuperar del texto: quedan `NULL` y
el emparejamiento con Insights cae al nombre (ver riesgo R1).

**Ingesta futura:** `routes/meta.py` ya pide `campaign_name` y `ad_name` al
Graph. Hay que agregar `campaign_id,adset_id,ad_id` a los `fields` de las cuatro
llamadas que traen leads y escribir las columnas nuevas. `notes` se sigue
escribiendo igual que hoy — nada que ya lea `notes` se rompe.

### 7.2 Tabla `meta_insights`

```sql
CREATE TABLE IF NOT EXISTS meta_insights (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL,           -- YYYY-MM-DD, día de Meta
    campaign_id   TEXT NOT NULL,
    campaign_name TEXT,
    spend         REAL DEFAULT 0,          -- en la moneda de la cuenta
    currency      TEXT,
    impressions   INTEGER DEFAULT 0,
    clicks        INTEGER DEFAULT 0,
    reach         INTEGER DEFAULT 0,
    leads         INTEGER DEFAULT 0,       -- action_type lead / leadgen
    synced_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, campaign_id)
);
```

Grano: campaña × día. Alcanza para todo lo que pide el panel y mantiene la tabla
chica (6 campañas × 180 días ≈ 1.080 filas). Bajar a anuncio individual se puede
después sin romper nada: es agregar columnas al `UNIQUE`.

Solo se guardan métricas crudas y contables. **CPM, CPC, CTR y CPL no se
guardan: se derivan al calcular.** Una tasa guardada se desincroniza de sus
componentes y después nadie sabe cuál manda.

### 7.3 Tabla `radiografias`

```sql
CREATE TABLE IF NOT EXISTS radiografias (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    dossier_json  TEXT NOT NULL,   -- el dossier completo, tal cual lo vio el modelo
    report_json   TEXT,            -- los hallazgos validados
    model         TEXT,
    tokens_in     INTEGER,
    tokens_out    INTEGER,
    status        TEXT DEFAULT 'ok',  -- ok | sin_datos | error_ia | error_validacion
    error_message TEXT
);
```

Guardar el dossier íntegro, y no solo el informe, es lo que permite auditar
después por qué el modelo dijo lo que dijo, y comparar contra la semana anterior
sin recalcular el pasado.

## 8. Sincronización de Insights

`services/meta_insights.py`.

**Credencial nueva.** El `META_PAGE_TOKEN` actual no sirve: los Insights de ads
requieren `ads_read` sobre la cuenta publicitaria. Variables nuevas:
`META_ADS_TOKEN` y `META_AD_ACCOUNT_ID` (formato `act_<n>`), en los secrets de
Fly. Si falta cualquiera de las dos, el sync se salta con un log de warning y el
resto del módulo funciona sin gasto — el panel muestra las métricas de calidad y
oculta las de costo.

**Llamada:** `GET /{ad_account_id}/insights` con
`level=campaign`, `time_increment=1`, `fields=campaign_id,campaign_name,spend,impressions,clicks,reach,actions`,
`time_range={since,until}`. Los leads salen de `actions` filtrando el
`action_type` de leadgen; el nombre exacto se confirma contra una respuesta real
antes de fijarlo en código.

**Versión de la Graph API:** usar la misma constante `GRAPH` que
`routes/meta.py`, nunca una versión propia. El 2026 ya hubo un incidente por
esto: el webhook de leadgen quedó fijado en `v25.0`, Meta dejó de entregar en
silencio y se buscó el problema en el token durante horas. Una sola constante en
todo el repo.

**Idempotencia y tope.** `INSERT ... ON CONFLICT(date, campaign_id) DO UPDATE`.
Se resincronizan siempre los últimos 7 días (Meta ajusta cifras hacia atrás) y se
completa hacia atrás solo lo que falte. La regla 3 de `COORDINACION.md` aplica:
**el sync nace con su propio tope rodante** en la tabla `corridas`, para que un
deploy —que reinicia la máquina— no dispare una tanda de llamadas a la API.

## 9. El dossier

`services/radiografia.py`. Es la pieza central: todo el cálculo es determinista y
testeable, y es lo único que el modelo llega a ver.

### 9.1 Forma de cada métrica

```json
{
  "id": "campana.tasa_reunion.leads_uy_2026",
  "etiqueta": "Tasa de reunión — Leads - UY - 2026",
  "valor": 0.1084,
  "formato": "porcentaje",
  "numerador": 9,
  "denominador": 83,
  "n": 83,
  "ic95": [0.0581, 0.1934],
  "delta_periodo_anterior": 0.021,
  "fuente": "crm"
}
```

`fuente` es siempre `"meta_insights"`, `"crm"` o `"derivada"`. Nunca hay una
métrica sin fuente, y no existe la categoría "inferencia" en el dossier: las
inferencias son del modelo y viven en el informe, separadas.

El intervalo se calcula con **Wilson**, que se comporta bien con muestras chicas
y proporciones cerca de 0 o 1 —justo el caso acá— y no necesita ninguna
dependencia nueva. Toda métrica con `n < 30` se marca `muestra_chica: true`.

### 9.2 Qué se calcula

**Por campaña** (y el total como una campaña más, `id` = `todas`):

- De Insights: gasto, impresiones, clics, alcance, leads.
- Derivadas: CPM, CPC, CTR, CPL.
- Del CRM, **contadas con `alcanzo()` sobre `lead_events`, nunca con
  `crm_status`** (§5.1): leads registrados, y cuántos alcanzaron `contactado`,
  `interesado`, `demo_agendada`, `demo_1`, `presupuesto_enviado` y `cerrado`.
  Un lead que llegó a demo y hoy figura en `no_interesa` cuenta como demo,
  porque la demo pasó.
- Tasas entre etapas consecutivas, cada una con su IC.
- Conciliación de gasto: lo que dice Meta contra lo cargado en Finanzas (§5.2).
- **Costo por demo** = gasto ÷ leads que alcanzaron `demo_1`.
- **Costo por presupuesto** = gasto ÷ leads que alcanzaron
  `presupuesto_enviado`. Las dos usan `costo()` de §5.1, así que sin gasto o
  sin cantidad devuelven `None` y no un cero engañoso.
- **Discrepancia de leads**: leads según Insights vs. leads en el CRM. Si no
  cuadran, algo se está perdiendo en la ingesta, y eso vale como hallazgo propio.

**Por segmento declarado en el formulario** —qué busca, presupuesto declarado,
objetivo, ciudad— las mismas tasas de avance. Esta es la sección que responde
"hacia dónde apuntar": permite ver si los que declaran más de USD 1.000
efectivamente avanzan más que los 104 que dicen "aún no lo sé".

**Serie temporal semanal** (no diaria): gasto, leads, CPL, impresiones, clics.
238 leads en 178 días son 1,3 por día; un gráfico diario sería ruido con días en
cero. La semana es el grano donde se ve la señal.

**Tiempos**: mediana de lead → primer evento, y de lead → reunión. La mediana, no
el promedio, porque un lead contactado a los 60 días arrastra el promedio.

**Secuencia de recordatorios**: para cada uno de los 7, cuántos se enviaron,
cuántos derivaron en un cambio de estado dentro de los 7 días siguientes, y
cuántas bajas produjo. Cruce de `meta_reminders` con `lead_events`.

**Pipeline ponderado por presupuesto declarado**: suma del punto medio del rango
que cada lead activo declaró. Es una estimación, y el dossier la marca como tal
en la etiqueta —pero la fuente es lo que dijo el propio lead, no un supuesto
nuestro.

**Deltas**: cada métrica trae su valor del período anterior, leído del último
snapshot en `radiografias`, no recalculado.

### 9.3 Normalizaciones obligatorias

Dos trampas reales medidas en los datos:

1. **Encoding roto en las claves de `form_data`.** Las claves llegan como
   `¿que_es_lo_que_buscás_para_tu_negocio?` con mojibake. El dossier no puede
   depender de la clave literal: hay que normalizar (bajar a minúsculas, quitar
   acentos, colapsar no-alfanuméricos) y mapear contra una tabla de sinónimos
   versionada en el módulo.
2. **Dos versiones del formulario.** 191 leads responden un juego de preguntas y
   47 responden otro (`¿tenés una marca/negocio establecido...?`,
   `¿cuál es el objetivo que tenes en este 2026?`). Las preguntas equivalentes se
   unifican; las que no tienen equivalente se reportan solo sobre su propia
   cohorte, con el `n` visible. Nunca se mezclan dos preguntas distintas bajo una
   misma etiqueta.

Lo mismo aplica a `category`, donde ya conviven `Peluqueria`, `peluqueria` y
`Odontología` con encoding roto. Reusar `_normalize_category` de
`routes/leads.py` en vez de escribir otra.

## 10. El motor de IA

`services/radiografia_ia.py`.

> **Restricción vigente (10/9, decisión de Juan): nada de esto llama a la API
> todavía.** El 28/8 el cron de LinkedIn dejó el saldo de la cuenta en cero, y
> hasta nueva orden ninguna corrida gasta tokens. Consecuencias concretas:
>
> - El módulo se escribe completo y se testea **solo contra respuestas
>   simuladas**. Ningún test, ni ningún paso del desarrollo, toca la API.
> - Nace apagado detrás de `RADIOGRAFIA_IA_ACTIVA` (default `false`). Con la
>   bandera apagada, `POST /api/marketing/generar` calcula y guarda el dossier,
>   y deja `report_json` en `NULL` con `status='sin_ia'`.
> - **El panel funciona igual sin IA.** Los gráficos y los KPIs salen del
>   dossier, no del informe; lo único que falta es el texto de los hallazgos.
>   Esto no es una degradación de emergencia: es el orden en que se construye.
> - El workflow semanal se crea con `schedule` comentado. Se descomenta el día
>   que se prenda la bandera, y no antes.

**Modelo:** `claude-opus-5`, con `thinking: {"type": "adaptive"}`. El trabajo es
razonamiento analítico y corre una vez por semana; ahorrar centavos degradando el
modelo no tiene sentido acá.

**Import diferido.** `import anthropic` son 19,6 MB medidos dentro del contenedor
y la máquina de Fly tiene 256 MB. El import va adentro de la función, igual que
en `services/budget_ai.py`. Hay un test que lo verifica sobre el fuente.

**Entrada:** únicamente el `dossier_json`. Nada de filas, nombres de leads,
teléfonos ni mails. Además de ser la garantía contra la invención, evita mandar
datos personales de 238 personas a un servicio externo.

**Salida estructurada** vía `output_config.format`:

```json
{
  "resumen": "2-3 frases sobre cómo viene la máquina",
  "hallazgos": [{
    "titulo": "...",
    "tipo": "oportunidad | riesgo | anomalia | contexto",
    "cuerpo": "...",
    "metricas_citadas": ["campana.cpl.leads_uy_2026", "..."],
    "confianza": "alta | media | baja",
    "recomendacion": "...",
    "advertencia_muestra": true
  }],
  "cambios_desde_la_ultima": ["..."]
}
```

### 10.1 El contrato anti-invención

Cuatro reglas, y las cuatro son verificables por código —no promesas del prompt:

1. **Todo número del texto tiene que existir en el dossier.** El validador
   extrae con regex cada número del `resumen`, `cuerpo` y `recomendacion` y lo
   busca entre los valores del dossier (con tolerancia de redondeo). Uno que no
   aparezca es un fallo.
2. **`metricas_citadas` no puede estar vacío ni citar un id inexistente.**
3. **Un hallazgo que cite una métrica con `muestra_chica` tiene que traer
   `advertencia_muestra: true`.** Que la señal sea débil se dice, no se omite.
4. **Prohibido afirmar causalidad.** Lista de patrones vedados
   (`porque`, `se debe a`, `causa`, `genera que`) sobre relaciones entre métricas;
   se pide correlación explícita. Esta es la regla más blanda de las cuatro y se
   trata como advertencia, no como fallo.

Si falla 1, 2 o 3, se reintenta una vez con el error concreto en el mensaje. Si
vuelve a fallar, la radiografía se guarda con `status='error_validacion'` y el
panel muestra los gráficos con un aviso de que el análisis no pasó la
validación. **Nunca se publica un informe sin validar.**

### 10.2 Lo que el modelo tiene prohibido saber

No recibe nombres de campañas de la competencia, ni precios de Scalerics, ni
supuestos de negocio. Si una recomendación necesita un dato que no está en el
dossier, la instrucción es decir que falta ese dato. "No sé" es una respuesta
válida y esperada.

## 11. El panel

`routes/marketing.py` + `static/charts.js` + un panel `marketing` en
`dashboard.py`, al lado de "Métricas". No se toca el panel de Métricas existente.

**Los gráficos se construyen a mano en SVG**, en un módulo aparte. El CRM hoy no
tiene ninguna librería de gráficos: todo son barras de CSS (`_barList`,
`_monthBars`, `_funnelBars`). Se eligió SVG propio sobre Chart.js o ECharts para
no sumar dependencia externa, respetar el tema oscuro/claro que ya existe
(`body.light`) y la paleta Scalerics, y no engordar los 574 KB de `dashboard.py`.

### 11.1 Inventario

| Bloque | Forma | Fuente |
|---|---|---|
| 8 KPIs con delta | Stat tiles, sin gráfico | Insights + CRM |
| Embudo de 9 pasos | Embudo con % de caída entre etapas | Ambas |
| Gasto y CPL por semana | Dos gráficos apilados, eje de tiempo compartido | Ambas |
| Impresiones/CPM y Clics/CPC | Pares apilados | Insights |
| CPL por campaña | Barras horizontales ordenadas | Ambas |
| Tasa de reunión por campaña | Barras con intervalo de confianza dibujado | CRM |
| Costo por reunión por campaña | Barras horizontales | Ambas |
| Calidad por segmento declarado | Barras (qué busca · presupuesto · objetivo · ciudad) | CRM |
| Secuencia de recordatorios | Barras: cuánto movió cada uno de los 7 | CRM |

Los 8 KPIs: gasto, CPM, CPC, CTR, leads, CPL, **costo por reunión** y **costo por
presupuesto enviado**. Los dos últimos reemplazan a los tiles de "valor del lead"
de los dashboards comerciales: ese número necesita plata asignada por lead y hay
4 cierres en toda la base.

El embudo de 9 pasos es el módulo entero en una imagen, y es lo que ninguna
herramienta comprada puede mostrar: las tres primeras etapas las tiene cualquier
reporte de ads, las seis siguientes solo las tiene el CRM.

### 11.2 Reglas de los gráficos

- **Ningún gráfico de doble eje.** Superponer dos escalas deja elegir dónde se
  cruzan las líneas, o sea que se puede fabricar cualquier correlación moviendo
  un eje. Dos medidas de escalas distintas van como dos gráficos apilados que
  comparten el eje de tiempo.
- **Grano semanal** en toda serie temporal, por el motivo de §9.2.
- **La incertidumbre se dibuja.** Toda barra de tasa con `n < 30` lleva su
  intervalo y una marca visible. Con 51 leads en la campaña de ARG, cinco puntos
  de diferencia contra UY no significan nada, y el gráfico tiene que decirlo en
  vez de dejar decidir sobre ruido.
- **Color por entidad, nunca por ranking.** Una campaña conserva su color aunque
  cambie de posición al filtrar.
- **Tema oscuro y claro**, ambos definidos explícitamente. El panel se abre en
  los dos y se mira antes de darlo por hecho.
- Hover con tooltip en toda serie y toda barra; leyenda siempre que haya dos o
  más series.

### 11.3 Endpoints

| Método y ruta | Qué hace |
|---|---|
| `GET /api/marketing/radiografia` | Último snapshot: dossier + informe |
| `GET /api/marketing/radiografia/<id>` | Un snapshot puntual |
| `GET /api/marketing/series?desde=&hasta=&campana=` | Series para los gráficos, con filtros |
| `POST /api/marketing/sync-insights` | Fuerza el sync. `x-admin-token` |
| `POST /api/marketing/generar` | Genera radiografía. `x-admin-token`, asíncrono con `job_id` |

Los dos `POST` van protegidos por `x-admin-token`, igual que
`/api/linkedin/generar`. Los `GET` requieren sesión y rol admin: el panel muestra
gasto publicitario.

## 12. Cron y costo

Workflow nuevo `.github/workflows/radiografia.yml`, copiando el patrón probado de
`linkedin.yml`: `schedule` los lunes a las 11:00 UTC (8:00 en Montevideo),
`concurrency` para que dos corridas no se solapen, espera a que el CRM conteste
antes de pegarle, `--retry-all-errors` en los 5xx pero no en los 4xx, y aviso por
mail si falla. GitHub solo golpea la puerta; todo el trabajo lo hace Fly.

Secuencia de la corrida: sync de Insights → dossier → IA → validación →
snapshot.

**Costo por corrida:** el dossier ronda los 5.000 tokens de entrada y el informe
unos 6.000 de salida contando el razonamiento. Con Opus 5 a USD 5/USD 25 por
millón, son **~USD 0,18 por corrida, ~USD 0,75 por mes**. El CRM en Fly cuesta
USD 4,18 mensuales, así que es ruido. Y no crece con la base.

## 13. Riesgos

**R1 — Emparejar leads con campañas por nombre.** Los 238 leads históricos no
tienen `campaign_id` recuperable, así que se emparejan por
`meta_campaign_name` ↔ `meta_insights.campaign_name`. Si una campaña se renombró
en Meta, ese tramo de historia no empareja.
*Mitigación:* los leads nuevos guardan `campaign_id` y ese es el camino
preferido; el nombre es solo el respaldo. El dossier expone `emparejamiento.cobertura` —la proporción de leads que
encontraron su campaña en `meta_insights`— y **por debajo de 0,80 toda métrica de
costo se marca `cobertura_parcial: true`**, el informe lo levanta como anomalía y
el panel muestra el aviso sobre los tiles de costo. Un CPL calculado sobre gasto
completo y leads incompletos miente hacia abajo, que es la dirección peligrosa.

**R2 — 13 leads sin campaña.** Van a un bucket `(sin campaña)` explícito, nunca
repartidos ni escondidos.

**R3 — Moneda.** Insights devuelve el gasto en la moneda de la cuenta. Se guarda
`currency` y se muestra tal cual. **No se convierte a dólares**: una conversión
con la cotización de hoy sobre gasto de marzo produce un número que parece
preciso y no lo es.

**R4 — La máquina tiene 256 MB.** Imports diferidos, y el dossier se calcula con
agregaciones en SQL, sin traer 8.357 filas a memoria.

**R5 — Zona compartida.** `database.py`, `dashboard.py` y `routes/meta.py` los
tocan otras sesiones. Todos los cambios son aditivos y están anotados en
`COORDINACION.md`. El cambio en `routes/meta.py` cae en territorio de la sesión D.

**R6 — Atribución, no causalidad.** Que los leads de una campaña avancen más no
prueba que la campaña sea mejor: pueden diferir el público, el momento y quién
los llamó. El módulo reporta asociación y lo dice; la regla 4 del validador
existe por esto.

## 14. Testing

Sobre una copia del backup, nunca contra producción, y nunca con el `.env` de
producción (regla 4 de `COORDINACION.md`).

- **Dossier:** cada métrica contra un valor calculado a mano sobre un fixture
  chico. Casos borde: denominador cero, campaña sin leads, campaña sin gasto,
  un solo lead.
- **Wilson:** contra valores publicados conocidos.
- **Validador anti-invención:** el test que más importa. Un informe con un número
  que no está en el dossier tiene que ser rechazado; uno que cita una métrica
  inexistente también; uno que cita muestra chica sin advertencia también.
  Con respuestas de modelo mockeadas, sin tocar la API.
- **Sync de Insights:** idempotencia (correrlo dos veces no cambia nada), upsert
  sobre día ya sincronizado, y ausencia de credenciales (se saltea sin romper).
- **Backfill de campañas:** parseo de `notes` incluyendo las variantes reales y
  las 13 filas sin campaña.
- **Normalización de `form_data`:** claves con mojibake y las dos versiones del
  formulario.
- **Import diferido:** un test sobre el fuente verifica que el módulo no importe
  `anthropic` a nivel de archivo, igual que `test_linkedin_sin_api.py`.

## 15. Lo que viene después

En orden de valor, no de facilidad:

1. **Bajar Insights a nivel de anuncio.** Con más historia, saber qué creativo
   trae los leads que avanzan, no solo qué campaña.
2. **Sumar las cohortes de discovery y Calendly** al mismo dossier, para comparar
   canales entre sí con la misma vara.
3. **Preguntarle al dossier.** Un modo conversacional encima del mismo dossier
   —el enfoque 2 que se descartó como base, que como agregado es barato porque la
   infraestructura ya está.
4. **Proyecto B**, ficha de empresa enriquecida.
5. **Proyecto C**, copiloto del vendedor. Recién tiene sentido cuando haya
   decenas de cierres, no cuatro.
