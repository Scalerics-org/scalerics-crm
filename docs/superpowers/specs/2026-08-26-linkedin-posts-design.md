# Borradores automáticos para el LinkedIn de Scalerics

Fecha: 2026-08-26
Estado: diseño aprobado, pendiente de plan de implementación

## Problema

La página de empresa `linkedin.com/company/scalerics` está creada desde el
2026-08-05 y no tiene posts. Publicar a mano no ocurre porque el costo no está
en escribir sino en decidir qué contar: hay que acordarse de qué se entregó esa
semana, elegir un ángulo y redactar. El sistema tiene que resolver esa parte y
dejar el texto listo para copiar.

No se automatiza la publicación. Los posts salen con la voz de Juan y con el
nombre de clientes reales; publicar sin leer es un riesgo que no compensa el
tiempo que ahorra, y además evita depender de la Community Management API de
LinkedIn, que requiere aprobación de LinkedIn.

## Alcance

Dos veces por semana (martes y viernes) llega un mail a `scalerics@gmail.com`
con **dos borradores** de post, cada uno con su imagen adjunta y con los datos
que se usaron para escribirlo. Juan elige uno, lo edita si quiere, lo publica a
mano y marca cuál usó.

Fuera de alcance: publicar por API, responder comentarios, métricas de alcance,
y el perfil personal de Juan (esto es solo la página de empresa).

## Arquitectura

Todo vive dentro del repo `lead-gen-uy`. El material de los posts sale de la
base del CRM, así que el módulo tiene que estar donde está la base.

```
services/linkedin_posts.py     recolección de material + redacción
routes/linkedin.py             endpoints /api/linkedin/*
templates/linkedin_card.html   plantilla de la tarjeta de marca
database.py                    + tablas linkedin_posts y linkedin_temas
scripts/render_linkedin.py     renderizador Playwright (corre en Actions)
.github/workflows/linkedin.yml el cron
tests/test_linkedin_posts.py   pruebas
```

### Por qué el cron es externo

`fly.toml` tiene `min_machines_running = 0` y `auto_stop_machines = "stop"`: la
máquina se duerme cuando no hay tráfico, así que un scheduler dentro del proceso
no dispara. El cron es un workflow de GitHub Actions que le pega al endpoint;
ese request además despierta la máquina.

Actions también es donde corre Playwright. La alternativa era meterlo en la
imagen de Fly, pero eso obliga a subir la VM de 256 MB a 512 MB. Se eligió
Actions porque el runner es gratis para este volumen (~40 min/mes contra 2.000
de cuota) y el cron iba a estar ahí de todos modos.

### Flujo

```
Actions (mar/vie 08:00 UY)
  │
  ├─1─► POST /api/linkedin/generar          → 202 {job_id}
  │        encola job "linkedin_draft" en el JobWorker que ya existe
  │
  ├─2─► GET /api/jobs/<id> (el endpoint que ya existe; polling hasta 5 min)
  │        → {borradores: [{id, texto, imagen: {...}, fuente: {...}}, ...]}
  │
  ├─3─► scripts/render_linkedin.py
  │        renderiza 2 PNG de 1200x627 según imagen.tipo
  │
  └─4─► POST /api/linkedin/enviar {job_id, imagenes: [{id, png_b64}]}
           → el CRM manda el mail con los PNG adjuntos y marca los
             borradores como enviados
```

El paso 1 devuelve 202 y no espera: la llamada a Claude tarda decenas de
segundos y el worker de gunicorn tiene timeout. El polling del paso 2 es del
runner, que no tiene apuro.

### Autenticación

No hace falta nada nuevo. `dashboard.py` (`require_login`, línea ~4899) ya deja
pasar cualquier request a `/api/` que traiga el header `x-admin-token` con el
valor de la variable `ADMIN_TOKEN`.

**Prerrequisito:** `ADMIN_TOKEN` tiene que estar seteado como secret en Fly y
como secret del repo en GitHub. Al 2026-08-26 no está seteado en Fly (quedó
pendiente del trabajo de discovery por mail). Si `ADMIN_TOKEN` está vacío el
bypass no aplica y todo el pipeline devuelve 401.

## Datos

### Tabla `linkedin_posts`

| Columna | Tipo | Nota |
|---|---|---|
| `id` | INTEGER PK | |
| `job_id` | INTEGER | job que lo generó |
| `tipo` | TEXT | `trabajo` \| `educativo` |
| `texto` | TEXT | el post redactado |
| `angulo` | TEXT | etiqueta corta del ángulo, para no repetirlo en el mismo mail |
| `fuente_tipo` | TEXT | `demo` \| `cliente` \| `tema` |
| `fuente_id` | INTEGER | id de la demo, del business o del tema |
| `imagen_tipo` | TEXT | `screenshot` \| `tarjeta` \| `ninguna` |
| `imagen_spec` | TEXT | JSON: URL a capturar, o frase de la tarjeta |
| `estado` | TEXT | `generado` \| `enviado` \| `publicado` \| `descartado` |
| `creado_en` | TEXT | ISO, hora de Montevideo |
| `marcar_token` | TEXT | token de un solo uso del link del mail; NULL una vez usado |
| `publicado_en` | TEXT | NULL hasta que Juan lo marca |

### Tabla `linkedin_temas`

| Columna | Tipo | Nota |
|---|---|---|
| `id` | INTEGER PK | |
| `titulo` | TEXT | "Por qué tu negocio no aparece en Google Maps" |
| `angulo` | TEXT | qué se quiere dejar dicho |
| `usado_en` | TEXT | ISO de la última vez, NULL si nunca |

Se siembra con unos 40 temas en `seed_linkedin_temas()`, siguiendo el patrón de
`seed_pitch_templates()` que ya existe. Un tema usado no vuelve a elegirse
hasta pasados **180 días**.

### Autorización para nombrar clientes

Se agrega la columna `linkedin_ok` (INTEGER, default 0) a `businesses`. Un
borrador de tipo `trabajo` sobre un cliente con `linkedin_ok = 0` **igual se
genera**, pero el mail lo marca con un aviso visible: *"confirmá con el cliente
antes de publicar"*. Nunca se omite el aviso por asumir que está bien.

La regla dura es la de no inventar: el redactor solo recibe datos que salieron
de la base. Si no hay material, no hay borrador de trabajo — se mandan dos
educativos.

## Selección del material

`recolectar_material(db_path, hoy)` devuelve los candidatos de tipo `trabajo`:

- Demos con `url` no vacía y `generated_at` dentro de los últimos
  **10 días**, cuyo `fuente_id` no aparezca ya en `linkedin_posts` con estado
  `publicado` o `enviado`.
- Negocios con un `lead_events.new_status` en (`cliente_cerrado`, `finalizado`)
  en los últimos 10 días.

Cada candidato viene con: nombre del negocio, rubro, ciudad, qué se hizo, URL, y
`linkedin_ok`.

**Composición del mail:**

- Hay ≥1 candidato de trabajo → un borrador de trabajo (el más reciente) + uno
  educativo.
- No hay candidatos → dos educativos, con temas distintos.

Los dos borradores de un mismo mail siempre llevan `angulo` distinto: uno
concreto sobre el hecho, otro sobre lo que ese hecho implica. La idea es que
Juan elija, no que edite.

## Redacción

Una llamada a `claude-opus-5` por borrador, con el SDK `anthropic` y el secret
`ANTHROPIC_API_KEY` que ya usa `services/budget_ai.py`. `thinking` en adaptive,
`max_tokens` 4000, sin streaming (la respuesta es corta).

### Reglas de voz en el system prompt

Escritas como prohibiciones, no como sugerencias:

- Prohibido el guion largo (`—`). Es el tell más claro de texto generado.
- Prohibidos los emojis.
- Nada de estructura de post de LinkedIn: párrafos parejos, listas con flechas,
  tricolon, "no es X, es Y", cierres tipo "Excited for what's ahead".
- Nada de encuadrar el trabajo como esfuerzo o sacrificio. El encuadre es
  disfrute y ambición.
- Frases cortas y directas, datos concretos (cantidades, tiempos, nombres),
  tono seguro sin falsa modestia.
- Máximo dos hashtags, al final.
- Español rioplatense, voseo.

### Validador determinista

`validar_borrador(texto) -> list[str]` corre en Python después de cada
respuesta y devuelve la lista de violaciones. Rechaza si encuentra:

- Cualquier `—` (U+2014) o `–` (U+2013).
- Cualquier codepoint en los rangos de emoji.
- Más de 2 hashtags.
- Más de 1.300 caracteres (LinkedIn corta con "ver más" a los ~210, pero 1.300
  es el techo de lo que alguien lee).
- Menos de 200 caracteres (respuesta trunca o vacía).

Si hay violaciones se reintenta **una vez**, pasándole al modelo la lista de lo
que rompió. Si el reintento también falla, ese borrador se descarta y el mail
sale con el otro. Si fallan los dos, se manda un mail avisando del fallo — nunca
un mail vacío ni silencio.

## Imágenes

`scripts/render_linkedin.py` corre en el runner con Playwright y produce PNG de
**1200x627** (la medida de imagen única de LinkedIn).

- `imagen_tipo = "screenshot"` — navega a la URL del `imagen_spec`, espera
  `networkidle`, captura el viewport. Se usa para los borradores de trabajo,
  donde hay una demo o un sitio real para mostrar.
- `imagen_tipo = "tarjeta"` — renderiza `templates/linkedin_card.html` con la
  frase del `imagen_spec` sustituida, y captura. Paleta de `CLAUDE.md`:
  fondo `#09090f`, acento `#0088CC`, títulos en Raleway, cuerpo en Inter, logo
  de Scalerics abajo a la izquierda. Se usa para los educativos.
- `imagen_tipo = "ninguna"` — si el render falla, el borrador va sin imagen. Una
  falla de Playwright no cancela el mail.

Los PNG viajan en base64 dentro del JSON del paso 4. Un PNG de 1200x627 pesa
unos 100-300 KB, que en base64 son ~400 KB; muy por debajo de cualquier límite
de request.

## El mail

Reusa `_layout()` de `services/email_service.py` para que se vea como el resto
de los mails del CRM. Se agrega `send_linkedin_drafts(to, borradores)`.

- Destinatario: `scalerics@gmail.com` (configurable con `LINKEDIN_MAIL_TO`).
- Asunto: `2 borradores para LinkedIn - martes 26/8`, con el número real de
  borradores que van (si uno se descartó, dice `1 borrador`).
- Por cada borrador: el texto en un bloque monoespaciado con fondo claro, listo
  para seleccionar y copiar; abajo, los datos que se usaron (qué cliente, qué
  demo, qué tema), para que Juan verifique que nada se inventó; y el aviso de
  autorización si el cliente tiene `linkedin_ok = 0`.
- Al pie de cada uno, un link `GET /api/linkedin/marcar?id=N&token=...` que lo
  pasa a `publicado`. El token va en la query porque un click desde el mail no
  puede mandar headers; es un token de un solo uso guardado en la fila.
- Las imágenes van como **adjuntos** de Resend (`attachments`, base64), no
  embebidas: Resend no soporta bien `cid:` y Juan necesita el archivo suelto
  para subirlo a LinkedIn igual.

`_send()` hoy no soporta adjuntos. Hay que extenderlo con un parámetro
`attachments` opcional que se pasa tal cual a la API de Resend; el resto de los
llamadores no cambia.

## Manejo de errores

| Falla | Qué pasa |
|---|---|
| Claude devuelve algo que no valida, dos veces | Ese borrador se descarta; el mail sale con el otro |
| Los dos borradores fallan | Mail de aviso a `scalerics@gmail.com`, sin borradores |
| Playwright falla en un render | Ese borrador va sin imagen |
| El job no termina en 5 min | Actions falla y GitHub manda el aviso; no se manda mail |
| Fly devuelve 401 | `ADMIN_TOKEN` no está seteado o no coincide; Actions falla ruidosamente |
| No hay temas educativos disponibles | Se usa el tema con `usado_en` más viejo aunque no haya cumplido los 180 días, y el mail lo dice |

## Pruebas

En `tests/test_linkedin_posts.py`, con base sembrada, siguiendo el patrón de los
tests que ya existen. La llamada a Claude va mockeada: no se testea la calidad
del texto, se testea que el pipeline no mande basura ni mails vacíos.

- `recolectar_material` encuentra una demo reciente y no devuelve una ya usada.
- Un cliente con `linkedin_ok = 0` produce el aviso en el mail.
- La elección de tema respeta el cooldown de 180 días, y el fallback cuando no
  queda ninguno libre.
- `validar_borrador` rechaza guion largo, emoji, tres hashtags, texto de 1.400
  caracteres y texto de 50.
- Un borrador inválido dos veces no llega al mail; los dos inválidos producen el
  mail de aviso.
- El endpoint `/api/linkedin/generar` sin `x-admin-token` devuelve 401.
- `marcar` con token válido pasa el post a `publicado` y quema el token; con
  token ya usado devuelve 410.

## Riesgos conocidos

- **GitHub apaga los workflows programados tras 60 días sin actividad en el
  repo.** Manda un aviso por mail antes. Al CRM se le hacen commits seguido, así
  que es poco probable, pero si el mail de borradores deja de llegar, mirar esto
  primero.
- **El cron de Actions no es puntual**: puede correr 5 a 30 minutos tarde. Para
  esto da igual.
- **Repetición a mediano plazo.** El cooldown de 180 días con 40 temas alcanza
  para unos 6 o 7 meses de posts educativos a este ritmo (entre 1 y 2 por mail,
  dos mails por semana). **Corregido el mismo dia:** al sacarse la rama de
  trabajo propio los dos borradores pasaron a ser educativos, o sea 4 temas
  por semana, y el banco dura unas 10 semanas. Después hay que sembrar
  más temas; conviene revisarlo a los 6 meses.
- **Costo de Claude**: 4 llamadas por semana a Opus con prompts cortos. Menos de
  un dólar al mes.
