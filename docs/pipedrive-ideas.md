# Ideas para nuestro CRM a partir de Pipedrive

> Investigación a fondo de Pipedrive (pipeline, deals, leads, automatizaciones, IA, email, Smart Docs, insights, metas, actividades, LeadBooster, proyectos, integraciones, UX) mapeada a **nuestro** CRM de agencia lead-gen (Scalerics). Fecha: 2026-07-28. Fuentes: pipedrive.com, support.pipedrive.com, developers.pipedrive.com + reseñas reputadas.

## TL;DR — las 6 apuestas de mayor palanca

1. **Disciplina de "próxima acción" + rotting.** Ningún lead activo sin una próxima acción agendada; marcar en rojo los que llevan X días sin movimiento. Es *lo que hace valioso a Pipedrive* y en nuestro caso es casi gratis (ya tenemos `last_event_at`). **Impacto alto / Esfuerzo bajo.**
2. **Embudo de conversión + tiempo en cada etapa.** Ya guardamos cada cambio de estado en `lead_events` con timestamp → podemos calcular tasa de conversión por etapa y dónde se traban los leads sin agregar datos nuevos. **Alto / Bajo-Medio.**
3. **Links trackeables de demo/presupuesto con aviso de apertura.** Ya generamos demos y presupuestos con IA; falta saber *cuándo el cliente los abrió* para pegar el follow-up caliente (por WhatsApp). **Alto / Bajo-Medio.**
4. **Valor monetario en el lead → forecast.** Hoy la plata vive solo en los presupuestos. Un campo `valor` + probabilidad por etapa habilita "cuánto vamos a cerrar este mes". **Alto / Bajo.**
5. **Motivos de pérdida estructurados.** `no_interesa` sin razón no enseña nada; con motivos (precio/timing/no contesta/competencia) sabemos por qué se caen. **Medio / Bajo.**
6. **Motor mínimo de automatizaciones (trigger → acción), con acciones WhatsApp-first.** "Estado pasó a `presupuesto_enviado` → crear tarea de seguimiento a los 2 días". **Alto / Medio.**

---

## Contexto: qué tenemos hoy

Nuestro CRM ya cubre bastante del modelo de Pipedrive, con nombres propios:

| Concepto Pipedrive | Nuestro equivalente | Estado |
|---|---|---|
| Pipeline con etapas | `crm_status`: `sin_contactar → contactado → reunion_agendada → reunion_hecha → presupuesto_enviado → negociacion → cliente_cerrado → en_desarrollo → finalizado` (+ `no_interesa`) | ✅ (como status, no kanban con valor) |
| Activities (llamadas) | Panel **Cola** + logueo de llamada con resultado + callback | ✅ |
| Activities view / planner | **Seguimientos**, **Calendario** | ✅ parcial |
| Contact/deal timeline | **Ficha de cliente** (tabs Info/Conversación/Reuniones/Presupuesto/Demo/Tareas/Llamadas/Historial) | ✅ |
| Notes | notas por lead + `lead_events` | ✅ |
| Tasks + goals | **Tareas** con responsables y tipos de objetivo, `task_progress_events` | ✅ |
| Insights / reporting | **Métricas** (embudo + SDR) | ✅ parcial |
| Activity log | **Actividad** (`activity_log`) | ✅ |
| Rep leaderboard | **SDR** | ✅ parcial |
| Permission sets / visibility | roles + `panel_access` | ✅ |
| Smart Docs (proposal gen) | **generación de demos + presupuestos con IA** | ✅ (mejor que Pipedrive base) |
| Messaging | **WhatsApp** (conversación + templates) | ✅ (Pipedrive lo tiene solo vía integraciones) |
| Meetings + IA | reuniones con transcript + resumen IA | ✅ |
| **Stage-transition log** | **`lead_events`** (new_status + created_at + created_by) | ✅ **ya existe — subutilizado** |

**Lo que NO tenemos (los gaps que valen):** valor/forecast monetario en el pipeline, flag de rotting/inactividad, nudge de "sin próxima acción", motivos de pérdida, Lead Inbox separado, captación inbound (web forms), booking/scheduler, sync de email bidireccional (hoy solo `gmail.send`), detección de duplicados al crear, filtros guardados, atajos de teclado / command bar, metas org con barra de progreso, board de entrega para `en_desarrollo`.

---

## Tabla maestra priorizada

Leyenda esfuerzo adaptado a nuestro stack (Flask/SQLite, vanilla JS, ya con IA + WhatsApp + logueo de llamadas + transcripts).

### 🟢 Quick wins (impacto alto, esfuerzo bajo)

| Idea | Qué es | Cómo se ve acá |
|---|---|---|
| **Nudge "sin próxima acción"** | Todo lead activo debería tener una tarea/callback futuro; marcar los que no tienen | Badge/columna en Cola y Seguimientos: "⚠️ sin próximo paso" (query sobre `tasks`/callbacks abiertos por lead) |
| **Rotting / inactividad** | Marcar en rojo leads sin movimiento hace > N días (por etapa) | Ya tenemos `last_event_at`: comparar contra umbral por `crm_status` y pintar la fila |
| **Motivos de pérdida** | Al pasar a `no_interesa`, pedir motivo (lista fija) | Dropdown en el modal de resultado de llamada + columna en Métricas ("por qué perdemos") |
| **Valor del lead + valor ponderado** | Campo `valor` + probabilidad por etapa → `valor × prob` | Campo en la ficha; total y total ponderado en cabecera del pipeline (Proceso de venta) |
| **Ordenar Cola por próxima acción / prioridad** | Default de Pipedrive: el board se ordena por próxima actividad | Orden de la Cola: vencidos primero, luego por `last_event_at`/score |
| **Filtros guardados con nombre** | Guardar vistas (mis leads, sin contactar hace 7d, etc.) | Guardar combinaciones de filtros por usuario en Cola/Clientes |

### 🟡 Medio (alto valor, esfuerzo medio)

| Idea | Qué es | Cómo se ve acá |
|---|---|---|
| **Embudo de conversión + tiempo en etapa** | Tasa de paso entre etapas y días promedio por etapa (detecta cuellos de botella) | **Ya tenemos `lead_events`**: computar `count(llegaron a etapa)/total` y `avg(Δt entre transiciones)`. Nuevas tarjetas en Métricas |
| **Links trackeables de demo/presupuesto + aviso de apertura** | Cada demo/presupuesto compartido registra vistas (primera/cada apertura) | Página hosteada con pixel/redirect + evento en Actividad + aviso → follow-up por WhatsApp cuando abren |
| **Metas con barra de progreso** | Objetivo por usuario/equipo (llamadas, reuniones, cierres) vs. real | Extiende SDR/Métricas: `{scope, métrica, período, target}` + barra. Ya tenemos los datos crudos |
| **Motor mínimo de automatizaciones** | trigger (cambio de estado / tarea hecha) → acción (crear tarea, enviar WhatsApp/email, webhook) | Reglas simples sobre `lead_events`; acciones **WhatsApp-first** (ya tenemos templates) |
| **Lead Inbox + Web Forms** | Staging de inbound "pedí tu demo" antes de entrar al pipeline; form embebible | Form en el sitio → endpoint → bandeja de leads → "convertir" a `businesses`. Fuente (`source`) ya existe |
| **Board de entrega para `en_desarrollo` (Projects)** | Post-venta: fases + tareas + template por tipo de proyecto web | Reusar el patrón de etapas: al pasar a `en_desarrollo`, crear proyecto con fases estándar del build web |
| **Scheduler / booking** | Link tipo Calendly con disponibilidad, evita ida y vuelta | Post-WhatsApp: mandar link de reserva; el turno crea reunión en Calendario |
| **Calidad de datos** | Duplicados al crear + import con "revertir 48h" + campos importantes | Matching por teléfono/nombre al scrapear; import de listas con undo; marcar campos obligatorios antes de `cliente_cerrado` |
| **IA: scoring configurable (estilo Pulse)** | Feed de leads rankeado por intención con pesos configurables | Extiende el score que ya mostramos: fórmula de pesos → feed ordenado "a quién llamar primero" |

### 🔵 Grande / estratégico (evaluar aparte)

| Idea | Nota |
|---|---|
| **Custom fields engine (tipado + fórmulas)** | Potente pero caro; hoy no lo necesitamos. Empezar con ~5 campos fijos |
| **Sync de email bidireccional + tracking** | Somos WhatsApp-first; el email hoy es `gmail.send`. Alternativa barata: BCC-a-CRM en vez de sync completo |
| **Deal detail changelog completo** | Ya tenemos historial por `lead_events`; ampliar a más campos si hace falta |

### ⚪ Integrar terceros, no construir

- **VoIP real / Caller** (Twilio/CloudTalk) → integrar, no construir.
- **Firma electrónica legal** (DocuSign/Dropbox Sign) → integrar. MVP interno: "escribí tu nombre para aceptar" + IP/timestamp (no equivale a firma legal).
- **Enrichment / Prospector / Web Visitors** → dependen de un proveedor de datos (Surfe/Leadfeeder). Nuestro **scraping propio ya cubre gran parte** de la intención de Prospector.

### 🚫 Saltar por ahora

Recurring revenue / MRR-ARR / installments (vendemos builds a precio fijo), sync de calendario bidireccional multi-proveedor (arrancar con feed iCal de una vía), múltiples pipelines, mobile nativo (nearby/caller-ID/offline).

---

## Detalle por tema (con "cómo se vería acá")

### 1. Venta basada en actividad — la idea madre
Doctrina de Pipedrive: enfocarse en **acciones controlables** (llamadas, mensajes, reuniones), no en resultados. Operacionalmente: **ningún lead activo sin una próxima acción agendada**; al completar una acción, la UI te empuja a agendar la siguiente; el board se ordena por próxima actividad; lo vencido va en rojo. No es un checkbox, es *el* diferenciador.

**Acá:** en la Cola y Seguimientos, columna "próximo paso" y estado vencido/rojo. Al loguear una llamada, si el resultado es "llamar después" ya pedimos callback (bien) — extender a: si no hay callback ni tarea futura, sugerir crearla. Ordenar la Cola por vencidos → sin próximo paso → score.

### 2. Rotting / inactividad
Setting por etapa: leads sin movimiento hace > N días se pintan de rojo. El timer resetea con actividad (nota, llamada, cambio de estado). Cuidado con el matiz de Pipedrive: el rotting *ignora* la próxima actividad futura (un lead con reunión dentro de 2 meses igual "se pudre").

**Acá:** trivial — `businesses.last_event_at` ya existe. Umbral por `crm_status` (p.ej. `presupuesto_enviado` se pudre a los 5 días). Render en la fila.

### 3. Embudo de conversión + tiempo en etapa (¡datos ya capturados!)
`lead_events(new_status, created_at, created_by)` es un stage-transition log. Con eso:
- **Conversión por etapa:** `count(leads que alcanzaron etapa X) / count(total)` → dónde se caen.
- **Tiempo en etapa:** `avg(Δt entre transición a X y la siguiente)` → cuellos de botella (barra por etapa).
- **Edad del lead / ciclo de venta promedio.**

**Acá:** nuevas tarjetas en Métricas. Es sobre todo SQL de agregación sobre una tabla que ya llenamos.

### 4. Links trackeables de demo/presupuesto + aviso de apertura
Smart Docs de Pipedrive: cada compartido genera link read-only con versiones y **tracking de apertura** (primera/cada vez) con conteo y timestamp → sabés cuándo timear el follow-up.

**Acá (encaje perfecto):** ya generamos demos y presupuestos. Servir cada uno en una página hosteada con pixel/redirect; registrar apertura como evento en Actividad y disparar aviso → follow-up **por WhatsApp** ("vi que abriste la propuesta, ¿la charlamos?"). Este es probablemente el mayor "wow" por poco esfuerzo dado lo que ya tenemos.

### 5. Valor y forecast
Deal = valor + probabilidad; `ponderado = valor × prob/100`; la probabilidad del deal pisa la de la etapa. Vista Forecast = kanban por fecha de cierre esperada.

**Acá:** hoy la plata está solo en los presupuestos. Agregar `valor` (y `cierre_esperado`) al lead permite: total y ponderado en la cabecera del pipeline y un "vamos a cerrar $X este mes". Como vendemos builds a precio fijo, la probabilidad puede ser simple (por etapa).

### 6. Motivos de pérdida
Al marcar Lost, Pipedrive pide motivo (freeform o lista predefinida, hasta 100). Alimenta Insights ("por qué perdemos").

**Acá:** cuando un lead pasa a `no_interesa`, dropdown de motivo (precio / no contesta / ya tiene web / timing / competencia). Columna en Métricas. Bajísimo esfuerzo, alto aprendizaje.

### 7. Automatizaciones (trigger → condición → acción)
Modelo: evento (cambio de etapa, actividad hecha, fecha ±N días) → condiciones → acciones (crear tarea, enviar email/mensaje, webhook, nota). Pasos con delay para secuencias tipo drip.

**Acá:** motor mínimo sobre `lead_events`. Acciones **WhatsApp-first** (ya tenemos templates) + crear tarea + webhook. Ejemplos: `→ presupuesto_enviado` ⇒ tarea "seguir en 2 días"; `→ reunion_agendada` ⇒ mandar template de confirmación. El delay-scheduler (cola durable) es la parte de más laburo; arrancar solo con triggers de evento (sin delay) es Medio.

### 8. Lead Inbox + Web Forms
Leads (no calificados) viven en una bandeja separada del pipeline; se convierten a deal con un clic arrastrando persona/org/notas/actividades. Web Forms embebibles capturan inbound directo a esa bandeja.

**Acá:** hoy todo entra como `businesses` scrapeado. Un form "pedí tu demo" en el sitio → bandeja de inbound → "convertir" al pipeline. `source` ya lo trackeamos, así que la atribución (scraping vs inbound vs Meta Ads) sale gratis.

### 9. Proyectos / entrega para `en_desarrollo`
Add-on Projects: boards con **fases**, tareas/subtareas, milestones, **templates** reusables, y "deal ganado → crear proyecto" que arrastra el contexto. Encaja casi 1:1 con nuestro `en_desarrollo`.

**Acá:** al pasar a `en_desarrollo`, instanciar un proyecto con fases estándar del build web (brief → diseño → dev → revisión → publicación) desde un template. Reusa el patrón de etapas del pipeline. Da visibilidad de entrega y algo compartible con el cliente.

### 10. Scheduler / booking
Link tipo Calendly con disponibilidad y anti-doble-reserva; el turno reservado crea una actividad/reunión.

**Acá:** tras el WhatsApp, mandar link de reserva en vez de coordinar a mano; el turno cae en Calendario como reunión. La parte cara es el sync de calendario; un MVP con disponibilidad manual ya sirve.

### 11. Calidad de datos
- **Duplicados al crear:** personas = mismo nombre + (tel o email o org); orgs = nombre + dirección. Merge irreversible → dejar elegir maestro y campos protegidos (Pipedrive tiene el footgun de quedarse con el más viejo).
- **Import con revertir 48h:** red de seguridad para cargas masivas (import transaccional).
- **Campos importantes:** marcar campos que la UI exige/avisa si faltan, por etapa (p.ej. presupuesto antes de `cliente_cerrado`).

**Acá:** matching por teléfono al scrapear (evitar duplicar negocios); ya tenemos merge manual en la ficha — agregar detección proactiva. Import de listas con undo.

### 12. IA
- **Scoring configurable (Pulse):** feed rankeado por intención con **pesos configurables** → "a quién llamar primero". Extiende el score que ya mostramos.
- **Redactor / resumen de hilos + score de "readiness to buy":** LOW dado que ya hacemos gen con IA.
- **Brief de handoff SDR→cierre / delivery:** auto-draft del contexto del lead (notas, llamadas, reunión) para no perder info al pasar de SDR a quien cierra o entrega. Reusa nuestros transcripts + resúmenes.

### 13. Robos de UX (bajo esfuerzo, mucho pulido)
- **`/` para buscar desde cualquier lado**; atajos de una tecla (c=actividad, n=nota, etc.); `j/k` para moverse en listas.
- **Filtros guardados con nombre** (por usuario o compartidos).
- **Columnas configurables** en las listas.
- **Checklist de onboarding** con barra de progreso flotante.
- **Campos importantes / "falta dato"** como nudge de completitud.

---

## Roadmap sugerido

**Fase 1 — Disciplina y visibilidad (quick wins sobre datos que ya tenemos):**
nudge "sin próxima acción" · rotting con `last_event_at` · motivos de pérdida · embudo de conversión + tiempo en etapa (sobre `lead_events`) · ordenar Cola por prioridad.

**Fase 2 — Plata y seguimiento caliente:**
valor + forecast del lead · links trackeables de demo/presupuesto con aviso de apertura (→ WhatsApp) · metas con barra de progreso.

**Fase 3 — Captación y automatización:**
Lead Inbox + Web Forms · motor mínimo de automatizaciones (WhatsApp-first) · scoring configurable.

**Fase 4 — Entrega y datos:**
board de proyectos para `en_desarrollo` · calidad de datos (duplicados/import/campos importantes) · scheduler.

**Integrar (no construir):** VoIP, firma legal, enrichment. **Saltar:** recurring/MRR, multi-pipeline, mobile nativo.

---

## Nota metodológica
Investigación hecha con 4 pasadas paralelas sobre dominios oficiales de Pipedrive y reseñas reputadas; cada afirmación de las pasadas venía con URL de fuente. El mapeo a nuestro CRM y las estimaciones de esfuerzo son propios, calibrados contra el código actual (paneles, `crm_status`, `lead_events`, `last_event_at`, generación IA de demos/presupuestos, WhatsApp, transcripts).
