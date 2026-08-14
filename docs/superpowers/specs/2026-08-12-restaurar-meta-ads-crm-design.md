# Restaurar Meta Ads en el CRM

**Fecha:** 2026-08-12
**Repo:** `Scalerics-org/scalerics-crm` (rama `restaurar-meta-ads`)
**Estado:** diseño aprobado, pendiente de plan de implementación

## Contexto

El 30-07-2026 se sacó todo lo de Meta Ads del CRM porque entraba un
programador nuevo (MatiDom) que no podía ver nada de eso. El borrado fue
completo: código, datos y rastros de texto. Como reemplazo se levantó
`scalerics-meta-hook` en Vercel, que recibe el webhook y solo manda un mail.

Esa restricción ya no aplica. Meta vuelve al CRM sin cuidados especiales:
código en el repo de la org, historia de git normal.

## Alcance

Vuelta completa al estado previo al 30-07: datos históricos, ingesta en vivo
y panel en la UI.

### Fuera de alcance

- Rehacer la política de privacidad. La página `/privacidad` se reescribió en
  términos genéricos y así se queda: sigue cumpliendo el requisito de Meta.
- Reconciliar la divergencia entre `scalerics-crm` y el repo viejo
  `lead-gen-uy` (el fix de `crm@scalerics.com` y el alta de usuarios con
  aprobación quedaron solo en el viejo). Es un problema real pero aparte.
- Tocar los cambios sin commitear que ya están en el árbol de trabajo
  (`Dockerfile`, `start.sh`, `services/calendly_gcal.py`, `.gitattributes`).

## Punto de partida

El borrado está entero en la historia de `juantomasetti1/scalerics-lead-gen`.
El último commit con Meta vivo es **`82d82ae`**; el borrado va de `72cb53e` a
`37b0f3c` y suma 1126 líneas eliminadas:

| Archivo | Líneas | Qué es |
|---|---|---|
| `routes/meta.py` | 544 | webhook, import histórico, monitor de token, sync diario |
| `dashboard.py` | 231 | panel Meta y métricas |
| `routes/leads.py` | 118 | columna `source`, exclusión de la Cola |
| `import_meta_leads.py` | 107 | import batch |
| `setup_meta.py` | 73 | alta de token de página |
| `services/email_service.py` | 46 | mail de lead nuevo |
| `database.py` | 26 | columnas `form_data` y `source` |

`scalerics-crm` se creó sin historia compartida, así que no hay `git revert`
posible entre repos: el código vuelve como parche extraído de `82d82ae`.

La rama base es `feat/calendly-empresa-servicio-telefono`, **no `main`** — es
la que está deployada como v71. `main` solo tiene el commit inicial.

## Arquitectura: el webhook vuelve directo al CRM

Meta apunta de nuevo a `https://scalerics-crm.fly.dev/api/meta/webhook`. El
CRM valida la firma, busca el lead en Graph, lo guarda y manda el mail. El
`scalerics-meta-hook` de Vercel se jubila.

```
Meta (form leadgen)
      │  POST firmado
      ▼
CRM /api/meta/webhook  ──►  Graph API (fetch del lead)
      │                            │
      ▼                            ▼
  businesses (source=meta)   mail a NOTIFY_EMAILS
```

Se descartó dejar el hook de Vercel adelante reenviando al CRM: agrega un
salto de red y un secreto compartido para cubrir el caso "CRM caído", que ya
existía antes y nunca molestó. El corte de agosto fue Meta no entregando
nada, cosa que el hook adelante tampoco habría evitado.

### Consecuencia de infraestructura

`fly.toml` tiene `min_machines_running = 0` y `auto_stop_machines = "stop"`.
Con el webhook directo, Meta pega contra una máquina dormida y paga el cold
start. Hay que subir `min_machines_running` a 1. Cuesta unos pocos USD por
mes; es el precio de tener la ingesta en el CRM.

### Jubilación del meta-hook

Recién **después** de verificar que el CRM recibe leads de verdad. El proyecto
de Vercel se apaga pero no se borra en el mismo día: si algo sale mal, volver
a apuntar la URL de callback tiene que seguir siendo un cambio de un campo.

El hook tiene un cron semanal que avisa antes de que venza el token. Esa
función no se pierde: `routes/meta.py` trae `start_meta_token_monitor`, que
hace lo mismo dentro del CRM. Hay que confirmar que arranca, si no la
jubilación del hook deja el vencimiento del token sin vigilancia.

## Secrets

Los cinco `META_*` **no están en Fly** — se rotaron y se sacaron durante la
separación. Hoy viven en el proyecto de Vercel del meta-hook y hay copia en
`~/lead-gen-uy/.env`. Hay que cargarlos en Fly:

`META_APP_ID`, `META_APP_SECRET`, `META_PAGE_ID`, `META_PAGE_TOKEN`,
`META_VERIFY_TOKEN`.

La copia autoritativa es la de Vercel, no la del `.env` local: el `.env` se
tocó después de la rotación y puede tener un valor viejo. Antes de cargar el
`META_PAGE_TOKEN` hay que validarlo contra `debug_token` de Graph.

## Datos

El backup está en `~/lead-gen-uy/backups/meta-2026-07-30/` (`meta_leads.json`
y `meta_leads.db`): 199 businesses, 13 `lead_events`, 110 `activity_log`,
3 `lead_attachments`, 1 `budget`, 1 `demo`, 2 `meetings`.

**Se reimportan 192, no 199.** Los 7 restantes (`598 Plan Arq`,
`626 Wili Conde`, `655 Jose Avila`, `694 Gianni Lupano`, `20149 Angela
Serratto`, `26740 Adriana`, `32095 Cecilia Aloy Fierro`) son clientes reales
que nunca se borraron: siguen vivos en producción con `source=NULL`. A esos
hay que devolverles `source` y `form_data` con un UPDATE, no insertarlos.
Insertarlos duplicaría clientes activos.

Las tablas relacionadas se reinsertan por `business_id` después de los
businesses, y solo para los ids que efectivamente se restauraron.

### Esquema

`form_data` se dropeó de la tabla. El `ALTER TABLE` corre sobre el volumen de
Fly **antes** de deployar el código que la lee, para no dejar la app rota
entre el deploy y la migración.

## Tres arreglos que van con la restauración

No son mejoras opcionales: el código que vuelve tiene tres fallas conocidas.

**1. La falla silenciosa.** `_fetch_and_store_lead` corre en un thread y
termina en `except Exception as e: logger.error(...)`. El webhook ya devolvió
200, así que Meta no reintenta: si Graph falla, el lead se pierde y lo único
que queda es una línea de log que Fly no guarda para siempre. Es la misma
clase de falla que dejó 8 leads sin notificar entre el 28-07 y el 05-08.

El fallo tiene que ser ruidoso: cuando el fetch o el insert fallan, mandar el
mail de alerta a `ADMIN_EMAIL` con el `leadgen_id`, que es suficiente para
recuperar el lead a mano desde el panel de Meta.

**2. La versión de Graph.** El código llama a `graph.facebook.com/v20.0`. Hoy
responde — el meta-hook usa la misma y funciona — pero está vieja. Se sube a
`v26.0`, la misma en la que ya está la suscripción del campo `leadgen`, y
**se verifica en el momento con una llamada real**, no de fe. La versión
queda en una constante única, no repetida por llamada.

**3. La firma falla abierta.** `_verify_signature` devuelve `True` cuando
`META_APP_SECRET` está vacío — un `skip in dev if not configured` que en un
endpoint público significa que cualquiera puede POSTear leads falsos si el
secret no está cargado. Y el secret hoy **no está en Fly**, así que el primer
deploy con la ruta viva y sin los secrets cargados dejaría el webhook abierto.

Pasa a fallar cerrado: sin `META_APP_SECRET` se rechaza el webhook. El
salteo queda detrás de un `META_ALLOW_UNSIGNED=true` explícito para
desarrollo local.

## Verificación

El criterio de aceptación es un lead de prueba que llegue a la base, no que
el código deployee.

1. `flyctl secrets list` muestra los cinco `META_*`.
2. La app arranca y `/api/meta/webhook` responde el GET de verificación.
3. La columna `form_data` existe en el volumen.
4. Los 192 leads están en `businesses` con `source='meta'`, y los 7 clientes
   siguen siendo 7 (no 14). Conteo antes y después.
5. El panel de Meta dibuja y la Cola sigue excluyendo los leads de Meta.
6. **Lead Ads Testing Tool** (`developers.facebook.com/tools/lead-ads-testing`)
   para probar el camino real. El botón "Test" del panel de webhooks solo
   prueba el transporte y llega igual aunque la entrega esté rota.
7. Después de cambiar la URL de callback, confirmar que el campo `leadgen`
   sigue en la versión que entrega (v26.0) y la suscripción activa. Cambiar
   la config del webhook es exactamente lo que se rompió en silencio antes.

Dos avisos operativos: un test exitoso **manda el mail a los destinatarios
reales** (Javier y Juan Pereyra), y los logs de runtime de Vercel en Hobby
duran 30 minutos, así que la prueba se dispara y se mira en el momento.

## Riesgos

| Riesgo | Mitigación |
|---|---|
| El `META_PAGE_TOKEN` está vencido o rotado | Validar con `debug_token` antes de cargarlo |
| Cambiar la callback URL rompe la entrega en silencio | Verificar con la Testing Tool, no con el botón Test |
| Reimportar duplica los 7 clientes vivos | UPDATE por id para esos, INSERT solo para los 192 |
| La máquina dormida hace timeout el webhook | `min_machines_running = 1` |
| El deploy adelanta a la migración y rompe la app | `ALTER TABLE` primero, deploy después |
