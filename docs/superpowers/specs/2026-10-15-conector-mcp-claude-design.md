# Conector MCP del CRM para Claude — diseño

**Fecha:** 2026-10-15
**Rama:** `feat/conector-mcp`
**Estado:** aprobado por Gonzalo, pendiente de plan de implementación

## Qué se construye

Un servidor MCP remoto dentro del CRM, en `https://scalerics-crm.fly.dev/mcp`,
para que el equipo lo agregue en claude.ai como conector personalizado. Cada
persona entra con su usuario del CRM; Claude ve lo que esa persona puede ver y
cada llamada queda registrada con su nombre.

Primera versión: **solo lectura, leads y pipeline.** Tres herramientas.

### Fuera de alcance (queda para después)

- Escritura (notas, cambio de etapa).
- Tareas, agenda, clientes activos, finanzas.
- Panel para ver y revocar tokens desde el CRM.
- Claude Code como cliente oficial. Se acepta su redirección loopback solo
  porque sirve para probar contra el CRM local antes de deployar.
- Token fijo en header (`static_headers`): es beta, para pocas
  organizaciones, y es una credencial compartida por todo el equipo. No
  identifica a la persona.

## Por qué nativo en Flask y no el SDK

El SDK oficial de MCP en Python es solo ASGI (Starlette). El CRM es Flask bajo
gunicorn con un worker y cuatro hilos, hilos de fondo que arrancan en cada
boot y un webhook de Meta que no tolera reinicios. Montar el SDK obligaba a
cambiar esa capa en una app de 22 mil líneas con historial de incidentes de
deploy (ver `COORDINACION.md`).

El OAuth hay que escribirlo igual en cualquier variante: la pantalla de
autorización reutiliza el login y los usuarios del CRM, y los tokens viven en
su SQLite. Lo que queda a mano es el JSON-RPC de MCP para herramientas, que
son cuatro mensajes.

## Lo que exige claude.ai

Según la doc oficial de conectores (`claude.com/docs/connectors/building/authentication`):

- La primera llamada sin token responde `401` con
  `WWW-Authenticate: Bearer resource_metadata="…"`. Claude ignora ese header
  en un `200`.
- Metadata de recurso protegido (RFC 9728) cuyo `resource` es exactamente la
  URL que la persona escribe en claude.ai: `https://scalerics-crm.fly.dev/mcp`.
  Claude usa solo la primera entrada de `authorization_servers`.
- Metadata de servidor de autorización (RFC 8414) en `/.well-known/`.
- PKCE S256 siempre, anunciado en `code_challenge_methods_supported`.
- Claude se identifica con su Client ID Metadata Document (CIMD) si la
  metadata anuncia `"client_id_metadata_document_supported": true` **y**
  `"none"` en `token_endpoint_auth_methods_supported`. Si falta alguno, cae a
  registro dinámico (DCR, RFC 7591) y necesita `registration_endpoint`.
- Redirección fija de las apps hospedadas: `https://claude.ai/api/mcp/auth_callback`.
  Claude Code usa `http://localhost/callback` y `http://127.0.0.1/callback`
  con puerto variable: se comparan sin puerto.
- `/token` acepta `application/x-www-form-urlencoded`; `/register` usa JSON.
- Refresh rotado en cada uso. Refresh inválido devuelve `invalid_grant`.
- Discovery, registro y token responden en menos de 10 segundos; refresh en
  menos de 30.
- El tráfico sale de `160.79.104.0/21`. Fly ya expone el CRM por HTTPS.

## Arquitectura

```
claude.ai ──POST /mcp (Bearer)──▶ routes/mcp.py ──▶ services/mcp_tools.py ──▶ database.py
                │                      │
                │ 401 + resource_metadata
                ▼
        /.well-known/*  ─┐
        /oauth/authorize ├─ routes/oauth.py ──▶ services/oauth_clientes.py (CIMD + DCR)
        /oauth/token     │                  └─▶ services/oauth_tokens.py   (códigos, tokens, PKCE)
        /oauth/register  │
        /oauth/revoke   ─┘
```

Archivos nuevos, cada uno con una responsabilidad:

| Archivo | Qué hace | Depende de |
|---|---|---|
| `routes/oauth.py` | Metadata, autorización con consentimiento, token, registro, revocación. Solo HTTP: valida y delega. | `services/oauth_clientes`, `services/oauth_tokens`, sesión de Flask |
| `routes/mcp.py` | `POST /mcp`: Bearer, `401` con el header correcto, JSON-RPC, despacho a herramientas, registro en `mcp_llamadas`. | `services/oauth_tokens`, `services/mcp_tools` |
| `services/oauth_clientes.py` | Resuelve un `client_id`: descarga y cachea el CIMD, o busca el cliente registrado por DCR. Compara `redirect_uri`. | `requests`, tabla `oauth_clientes` |
| `services/oauth_tokens.py` | Emite y valida códigos, access y refresh tokens. PKCE. Hash en reposo. Rotación. | tablas `oauth_codigos`, `oauth_tokens` |
| `services/mcp_tools.py` | Registro de herramientas: nombre, descripción, `inputSchema`, función. Las tres herramientas de v1. | `database.py` |
| `tests/test_oauth_clientes.py`, `tests/test_oauth_flujo.py`, `tests/test_mcp.py` | Ver sección Tests. | |
| `docs/puesta-en-produccion-conector-claude.md` | Pasos para el Owner del plan Team. | |

Cambios en lo existente:

- `database.py`: cuatro tablas nuevas en `init_db`, con el patrón
  `CREATE TABLE IF NOT EXISTS` que usa el resto.
- `dashboard.py`: registrar `oauth_bp` y `mcp_bp`; eximir del candado de
  sesión los prefijos `/mcp`, `/oauth/` y `/.well-known/` (cada ruta aplica
  su propia autenticación); `/login` acepta `next` y vuelve ahí tras entrar,
  solo si es una ruta relativa del CRM que empieza con `/oauth/authorize`.

Ninguna dependencia nueva: `requests`, `cryptography` y `werkzeug` ya están.

## Herramientas

Nombres y descripciones en español, porque así habla el equipo con Claude.
Cada herramienta devuelve un único `content` de tipo `text` con JSON. Los
errores esperables (lead inexistente, etapa inválida) vuelven con
`isError: true` y un mensaje en español, nunca como excepción.

### `buscar_leads`

Busca por nombre, email o teléfono, sin distinguir mayúsculas. Excluye los
archivados (`archivado_en IS NULL`).

- Entrada: `texto` (string, obligatorio, mínimo 2 caracteres), `limite`
  (entero, 1–50, por defecto 10).
- Salida: lista de `{id, nombre, etapa, telefono, email, ciudad, rubro,
  origen, ultimo_evento_at}` ordenada como la cola del CRM (score, fecha).
- SQL propio en `services/mcp_tools.py`, con `LIKE` sobre `name`, `email` y
  `phone` y `LIMIT`. No se reutiliza `get_all_businesses` porque trae la
  tabla entera a memoria (incidente del 28-8-2026).

### `ver_lead`

La ficha de un lead.

- Entrada: `lead_id` (entero, obligatorio).
- Salida: campos útiles de `businesses` (nombre, etapa, contacto, ciudad,
  rubro, dirección, web, redes, origen, campaña de Meta si la hay, notas,
  pitch, callback_date, score, monto pagado si es cliente), `client_info`,
  responsables por nombre (`encargado`, `mantenimiento`, `cobros`, resueltos
  contra `users`), últimos 20 eventos (`get_lead_events`) y últimas 20
  llamadas (`call_logs`).
- Se excluyen columnas internas: `form_data` crudo, ids de Meta, campos de
  semáforo, `demo_html_path`.

### `pipeline`

Los leads en seguimiento, agrupados por etapa.

- Entrada: `etapa` (string opcional, `enum` del schema con todas las etapas,
  pre-clientes y clientes: así Claude ve los valores válidos y una etapa
  inventada es un error de argumentos `-32602`, no `isError`), `responsable`
  (string opcional, se busca por nombre de usuario con `LIKE`, sobre
  `encargado_id`), `incluir_clientes` (bool, por defecto false, agrega
  `ETAPAS_CLIENTE`).
- Salida: `{etapas: [{etapa, total, leads: [{id, nombre, telefono, email,
  encargado, ultimo_evento_at, dias_sin_movimiento}]}], total}`. Orden de
  etapas igual a la constante; dentro de cada etapa, los más viejos sin
  movimiento primero.
- La descripción de la herramienta enumera las etapas válidas con su
  significado, copiado de los comentarios de `database.py`, para que Claude no
  invente nombres. Tope de 200 leads por respuesta; si hay más, el JSON lo
  dice y sugiere filtrar.

### Permisos

Se aplican las mismas reglas que `before_request` para `/api/`: el usuario del
token tiene que existir y no ser vendedor de Fidelidad
(`services.fidelidad.es_vendedor`). Los leads no piden panel en la API web,
así que acá tampoco. Un admin no ve nada distinto.

## OAuth

El CRM es a la vez servidor de autorización y de recurso. El emisor es
`CRM_URL` (ya existe, por defecto `https://scalerics-crm.fly.dev`). Las URLs
de abajo se arman desde esa variable, nunca desde el `Host` del request.

### Metadata

`GET /.well-known/oauth-authorization-server`:

```json
{
  "issuer": "https://scalerics-crm.fly.dev",
  "authorization_endpoint": "https://scalerics-crm.fly.dev/oauth/authorize",
  "token_endpoint": "https://scalerics-crm.fly.dev/oauth/token",
  "registration_endpoint": "https://scalerics-crm.fly.dev/oauth/register",
  "revocation_endpoint": "https://scalerics-crm.fly.dev/oauth/revoke",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["none"],
  "client_id_metadata_document_supported": true,
  "scopes_supported": ["crm:leer"]
}
```

`GET /.well-known/oauth-protected-resource/mcp` y
`GET /.well-known/oauth-protected-resource` (misma respuesta):

```json
{
  "resource": "https://scalerics-crm.fly.dev/mcp",
  "authorization_servers": ["https://scalerics-crm.fly.dev"],
  "bearer_methods_supported": ["header"],
  "scopes_supported": ["crm:leer"]
}
```

Las dos con `Cache-Control: public, max-age=3600` y CORS abierto solo para
`GET`.

### Clientes

Un `client_id` se resuelve así, en `services/oauth_clientes.py`:

1. **CIMD.** Si el `client_id` es una URL `https://` cuyo host es `claude.ai`
   o `claude.com`, se descarga con `requests` (timeout 5 s), se verifica que
   el `client_id` del documento sea idéntico a la URL y se toman sus
   `redirect_uris`. Se cachea en memoria un día. Hosts fuera de esa lista se
   rechazan con `invalid_client`: es un CRM privado, no hace falta aceptar
   cualquier cliente de internet.
2. **DCR.** Si no, se busca en `oauth_clientes`. `POST /oauth/register`
   recibe JSON (RFC 7591), exige `redirect_uris` no vacío con URLs `https://`
   o loopback, genera `client_id` aleatorio, guarda `client_name` y
   `redirect_uris`, y responde `201` con `token_endpoint_auth_method: "none"`.
   El DCR solo registra los callbacks conocidos de Claude
   (`https://claude.ai/api/mcp/auth_callback`, `https://claude.com/api/mcp/auth_callback`)
   y los loopback de Claude Code; cualquier otro cliente tiene que usar CIMD,
   que solo claude.ai/claude.com pueden publicar (si no, un atacante registraría
   su callback y haría phishing de consentimiento contra un empleado logueado).
   Tope: 200 clientes registrados; pasado eso se borran los que no tienen
   tokens vivos, del más viejo al más nuevo.

`redirect_uri` se compara **exacta** contra las registradas. Única excepción:
si la registrada es `http://localhost/callback` o `http://127.0.0.1/callback`,
se ignora el puerto de la recibida (RFC 8252 §7.3 y la doc de Claude Code).

### Autorización

`GET /oauth/authorize` con `response_type=code`, `client_id`, `redirect_uri`,
`code_challenge`, `code_challenge_method=S256`, `state`, `scope` (opcional) y
`resource` (opcional).

1. Se valida cliente y `redirect_uri` **antes** de cualquier redirección. Si
   fallan, se muestra una página de error en el CRM, no se redirige (OAuth 2.1
   §7.12.2: no redirigir a URIs no confiables).
2. Los demás errores (falta `code_challenge`, método distinto de S256, scope
   desconocido, `resource` distinto de `https://scalerics-crm.fly.dev/mcp`)
   redirigen a `redirect_uri` con `error=invalid_request` y el `state`.
3. Sin sesión del CRM: redirección a `/login?next=<esta URL>`. `/login`
   solo honra `next` si empieza con `/oauth/authorize`.
4. Con sesión: pantalla de consentimiento con el nombre del usuario, qué va a
   poder hacer Claude ("leer leads y pipeline"), y el **host** de la
   `redirect_uri` en texto visible. Botones Autorizar / Cancelar. El
   formulario lleva un nonce guardado en la sesión (CSRF). Cancelar redirige
   con `error=access_denied`.
5. Al autorizar se emite un código aleatorio (32 bytes, urlsafe) que vence a
   los 10 minutos y guarda `user_id`, `client_id`, `redirect_uri`,
   `code_challenge`, `scope` y `resource`. Redirección a `redirect_uri` con
   `code` y `state`.

La pantalla se renderiza con `render_template_string` y el CSS de `LOGIN_HTML`,
como las demás páginas sueltas del CRM.

### Token

`POST /oauth/token`, solo `application/x-www-form-urlencoded`. Otro
`Content-Type` devuelve `400 invalid_request` (no `415`).

- `grant_type=authorization_code`: exige `code`, `client_id`, `redirect_uri`,
  `code_verifier`. Verifica que el código exista, no esté usado ni vencido,
  que `client_id` y `redirect_uri` coincidan con los del código, y que
  `BASE64URL(SHA256(code_verifier)) == code_challenge`. Un código se marca
  usado en la misma transacción; reusarlo devuelve `invalid_grant` y revoca
  los tokens que nació de él (OAuth 2.1 §4.1.3).
- `grant_type=refresh_token`: exige `refresh_token` y `client_id`. Si el
  refresh no existe, venció o está revocado: `invalid_grant`. Si es válido:
  nuevo access y **nuevo refresh**, y el viejo queda revocado en la misma
  transacción.
- Respuesta: `{access_token, token_type: "Bearer", expires_in: 3600,
  refresh_token, scope}`. Access dura 1 hora, refresh 30 días.
- Vida máxima de una familia: todo lo que nace de un mismo código vive como
  mucho 90 días desde el canje (`familia_en`). Cada rotación hereda
  `familia_en`, y el refresh nuevo vence en
  `min(ahora + 30 días, familia_en + 90 días)`. Sin este tope el refresh era
  deslizante: un refresh robado que se siguiera rotando no vencía nunca.
  Pasados los 90 días la persona vuelve a autorizar a Claude.
- Tokens opacos: 32 bytes aleatorios en urlsafe. En la base se guarda solo
  `SHA-256` del token; la búsqueda es por hash. Un volcado de la base no sirve
  para entrar.
- Errores en JSON con `error` y `error_description` según RFC 6749, con
  `Cache-Control: no-store`.

`POST /oauth/revoke` (RFC 7009): recibe `token` y revoca el access o refresh
que coincida, y su pareja. Responde `200` siempre.

### Tablas

```sql
CREATE TABLE IF NOT EXISTS oauth_clientes (
    client_id      TEXT PRIMARY KEY,
    client_name    TEXT,
    redirect_uris  TEXT NOT NULL,        -- JSON, lista
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_codigos (
    code_hash       TEXT PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id       TEXT NOT NULL,
    redirect_uri    TEXT NOT NULL,
    code_challenge  TEXT NOT NULL,
    scope           TEXT NOT NULL,
    resource        TEXT,
    expires_at      TEXT NOT NULL,
    used_at         TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id        TEXT NOT NULL,
    codigo_hash      TEXT,                -- de qué código nació: un código
                                          -- reusado revoca toda su familia
    access_hash      TEXT NOT NULL UNIQUE,
    refresh_hash     TEXT NOT NULL UNIQUE,
    scope            TEXT NOT NULL,
    resource         TEXT,
    access_expira    TEXT NOT NULL,
    refresh_expira   TEXT NOT NULL,
    ultimo_uso       TEXT,
    revocado_en      TEXT,
    familia_en       TEXT,                -- canje del código de la familia:
                                          -- el refresh no pasa de +90 días
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_llamadas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER,
    user_name    TEXT NOT NULL,
    herramienta  TEXT NOT NULL,
    argumentos   TEXT,                   -- JSON
    ok           INTEGER NOT NULL,
    error        TEXT,
    ms           INTEGER,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

`database._connect` no activa `PRAGMA foreign_keys`, así que el `ON DELETE
CASCADE` no corre en producción. Lo que protege es que toda búsqueda de
código o token hace `JOIN users`: un usuario borrado no valida, no refresca
ni canjea. Un refresh ya rotado que vuelve a presentarse revoca su familia,
igual que un código reusado.

Fechas en ISO 8601 UTC, como `password_reset_tokens`. Los códigos vencidos y
los tokens con refresh vencido hace más de 30 días se borran en un barrido al
emitir uno nuevo (sin hilo de fondo: regla 3 de `COORDINACION.md`).

## Endpoint `/mcp`

Transporte Streamable HTTP en modo stateless con respuesta JSON, sin SSE.
Es una opción válida del transporte: el servidor puede contestar cada POST
con un único JSON.

- `POST /mcp` únicamente. `GET` y `DELETE` responden `405`.
- Sin header `Authorization: Bearer …`, o con token inválido, vencido o
  revocado: `401` con
  `WWW-Authenticate: Bearer resource_metadata="https://scalerics-crm.fly.dev/.well-known/oauth-protected-resource/mcp", scope="crm:leer"`
  y cuerpo JSON `{"error": "invalid_token"}`.
- Token válido pero usuario inexistente o vendedor de Fidelidad: `403`.
- Cada uso válido actualiza `ultimo_uso` del token (una vez por minuto como
  máximo, para no escribir en cada llamada).
- Cuerpo: un objeto JSON-RPC 2.0. Un array (batch) se rechaza con `400`:
  la versión 2025-06-18 del protocolo lo sacó. Cuerpo que no es JSON: `400`
  con error `-32700`.
- Métodos:
  - `initialize`: responde `protocolVersion` igual al pedido si es uno de
    `2025-11-25`, `2025-06-18` o `2025-03-26`, si no `2025-06-18`. Para un
    servidor de solo herramientas las tres versiones son equivalentes.
    `capabilities: {tools: {}}`;
    `serverInfo: {name: "scalerics-crm", version: "1"}`; `instructions`
    con dos líneas sobre qué es el CRM y qué hacen las herramientas.
  - `notifications/initialized` y cualquier otra notificación (sin `id`):
    `202` sin cuerpo.
  - `ping`: `{}`.
  - `tools/list`: las tres herramientas con `name`, `description`,
    `inputSchema` (JSON Schema con `required` y `additionalProperties: false`).
  - `tools/call`: valida `name` y `arguments` contra el schema (tipos,
    requeridos, rangos) sin librería extra: la validación es chica y se
    escribe en `services/mcp_tools.py`. Argumentos inválidos devuelven el
    error JSON-RPC `-32602`. La herramienta corre dentro de `try/except`:
    un error de negocio es `isError: true` con texto; una excepción
    inesperada también, con un mensaje genérico, y el detalle va al log del
    servidor.
  - Método desconocido: `-32601`.
- El header `Mcp-Session-Id` no se emite ni se exige (stateless).
- Cada `tools/call` deja una fila en `mcp_llamadas` con usuario, herramienta,
  argumentos, si salió bien, el error si no, y la duración. Si registrar
  falla, la llamada sale igual (mismo criterio que `log_activity`).

## Errores y límites

- Todas las respuestas de error de OAuth y de `/mcp` son JSON; nunca una
  página HTML del CRM, salvo las dos pantallas pensadas para una persona
  (consentimiento y error de autorización).
- Un lead inexistente en `ver_lead` es `isError: true`, no `-32602`: Claude
  lo lee y lo explica. Una etapa inválida en `pipeline`, en cambio, es
  `-32602`, porque la lista válida ya está en el schema de la herramienta.
- `texto` y `responsable` tienen `maxLength` 100 y se les escapan los
  comodines de `LIKE`; los argumentos se recortan al registrarlos.
- Toda respuesta JSON de `/mcp` se recorta a 200 leads o 20 eventos y lo dice.
- No hay límite de tasa en v1. Los tokens están atados a usuarios del CRM y
  el worker ya tiene tope de hilos. Queda anotado como posible seguimiento.

## Tests

Con el `test_client` de Flask y una base temporal, como el resto de la suite.
`CRM_SIN_PROCESOS_DE_FONDO` ya lo pone `conftest.py`. `CRM_URL` se fija por
test con `monkeypatch`.

`tests/test_oauth_clientes.py`:
- CIMD de `claude.ai` se acepta y sus `redirect_uris` se usan (con `requests`
  mockeado); un host fuera de la lista se rechaza; un documento cuyo
  `client_id` no coincide con la URL se rechaza.
- DCR: registro válido crea cliente; sin `redirect_uris` da `400`; la
  comparación de redirect es exacta salvo loopback sin puerto.

`tests/test_oauth_flujo.py`:
- Las dos metadata devuelven los campos que Claude necesita, con URLs
  armadas desde `CRM_URL`.
- Flujo completo: registrar, `authorize` sin sesión redirige a `/login` con
  `next`; con sesión muestra el consentimiento con el host; autorizar
  devuelve `code` y `state`; `token` con `code_verifier` correcto da access y
  refresh; con `code_verifier` incorrecto da `invalid_grant`; reusar el
  código da `invalid_grant` y revoca el token; refresh rota y el viejo deja
  de servir; `revoke` invalida; `redirect_uri` ajena no redirige.
- `token` con JSON en vez de form devuelve `400 invalid_request`.

`tests/test_mcp.py`:
- Sin Bearer: `401` con el `WWW-Authenticate` exacto.
- Token vencido: `401`. Vendedor de Fidelidad: `403`.
- `initialize`, `ping`, `tools/list` con las tres herramientas y sus schemas.
- `buscar_leads` encuentra por nombre, mail y teléfono y no devuelve
  archivados; `ver_lead` trae `client_info`, eventos y responsables por
  nombre; `ver_lead` de un id inexistente es `isError`; `pipeline` agrupa en
  el orden de `ETAPAS_PRECLIENTE`, filtra por etapa y por responsable, y
  rechaza una etapa inventada con `isError`.
- `tools/call` con argumentos inválidos da `-32602`; método desconocido,
  `-32601`; batch, `400`.
- Cada `tools/call` deja su fila en `mcp_llamadas`.

Verificación de integración, en este orden:

1. `python server.py` local y `claude mcp add --transport http crm http://localhost:5000/mcp`
   desde Claude Code: hace el flujo OAuth real contra el CRM local (de ahí la
   excepción loopback) y llama las herramientas.
2. Deploy a Fly siguiendo `COORDINACION.md` (árbol limpio, mirar
   `flyctl releases`).
3. Alta en claude.ai como conector personalizado por el Owner, con "Sign in
   now". Probar las tres herramientas desde un chat.

## Puesta en producción

`docs/puesta-en-produccion-conector-claude.md` con:

- Qué variable hace falta: `CRM_URL` (ya está en el `.env.example`; en Fly
  tiene que valer `https://scalerics-crm.fly.dev`, sin barra final).
- Pasos para el Owner del plan Team: Configuración → Conectores → Agregar
  conector personalizado → URL `https://scalerics-crm.fly.dev/mcp` →
  identidad publicada de Claude → cada miembro conecta con su usuario.
- Cómo revocar a alguien hoy: `UPDATE oauth_tokens SET revocado_en = … WHERE
  user_id = …` desde la consola de Fly, hasta que exista el panel.
- Qué registra `mcp_llamadas` y cómo consultarlo.

## Seguimientos propuestos

- Escritura: `agregar_nota` y `cambiar_etapa`, con `log_activity`.
- Panel "Conectores" en el CRM: tokens por usuario, último uso, revocar.
- Límite de tasa por token.
- Claude Code como cliente oficial, con su propio CIMD documentado.
