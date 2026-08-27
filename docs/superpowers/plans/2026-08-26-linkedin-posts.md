# Borradores automáticos de LinkedIn — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dos veces por semana el CRM redacta dos borradores de post para la página de LinkedIn de Scalerics y los manda por mail con las imágenes adjuntas, sin publicar nada.

**Architecture:** Un módulo nuevo dentro de `lead-gen-uy` recolecta material de la base (demos deployadas, clientes cerrados) y de una tabla de temas educativos con cooldown, redacta con `claude-opus-5`, valida el texto contra reglas de voz deterministas y manda el mail por Resend. El cron y el renderizado de imágenes con Playwright viven en GitHub Actions porque la máquina de Fly se duerme (`min_machines_running = 0`) y el Dockerfile instala el paquete pip de Playwright pero no los binarios de los navegadores.

**Tech Stack:** Python 3.11, Flask (blueprints), SQLite, `anthropic`, Resend HTTP API, Playwright (solo en el runner), pytest.

**Spec:** `docs/superpowers/specs/2026-08-26-linkedin-posts-design.md`

## Global Constraints

- Modelo: `claude-opus-5`. Thinking adaptive (`{"type": "adaptive"}`). Nunca `budget_tokens` — devuelve 400 en ese modelo.
- Todo el texto de cara al usuario va en español rioplatense.
- El texto de los posts no puede contener guion largo `—` (U+2014), guion medio `–` (U+2013) ni emojis. Esto vale también para el texto de las tarjetas.
- Paleta de las imágenes, copiada de `CLAUDE.md`: fondo `#09090f`, superficie `rgba(255,255,255,.04)`, borde `rgba(255,255,255,.07)`, texto `#e2e8f0`, apagado `#64748b`, azul `#0088CC`, verde `#10B981`. Títulos en Raleway, cuerpo en Inter.
- Imágenes de 1200x627 px.
- Destinatario del mail: `scalerics@gmail.com`, sobreescribible con la variable de entorno `LINKEDIN_MAIL_TO`.
- Cooldown de temas educativos: 180 días.
- Ventana de material propio: 10 días.
- Los tests corren con `pytest` desde la raíz del repo. No hay `conftest.py`; cada archivo de test define su propio fixture `db_path` con `tmp_path`.
- La llamada a Claude va siempre mockeada en los tests, con el patrón de `tests/test_budget_ai.py`.
- Commits en la rama `linkedin-posts`, que ya existe y ya tiene el spec commiteado.

---

### Task 1: Tablas, semilla de temas y acceso a datos

**Files:**
- Modify: `database.py` (dentro de `init_db`, después del bloque de `activity_log`; y funciones nuevas al final del archivo)
- Test: `tests/test_linkedin_db.py`

**Interfaces:**
- Consumes: `init_db(db_path)`, `_connect(db_path)`, `_add_column(conn, table, column, definition)` — todo ya existe en `database.py`.
- Produces:
  - `seed_linkedin_temas(db_path: str) -> None`
  - `create_linkedin_post(db_path: str, **fields) -> int`
  - `update_linkedin_post(db_path: str, post_id: int, **fields) -> None`
  - `get_linkedin_posts_by_job(db_path: str, job_id: int) -> list[dict]`
  - `get_linkedin_posts_by_lote(db_path: str, lote: str) -> list[dict]`
  - `get_linkedin_post_by_token(db_path: str, token: str) -> Optional[dict]`
  - `get_fuentes_usadas(db_path: str) -> set[tuple[str, int]]`
  - `get_temas_disponibles(db_path: str, limite_iso: str) -> list[dict]`
  - `marcar_tema_usado(db_path: str, tema_id: int, cuando_iso: str) -> None`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_linkedin_db.py`:

```python
import pytest

from database import (
    create_linkedin_post,
    get_fuentes_usadas,
    get_linkedin_post_by_token,
    get_linkedin_posts_by_job,
    get_linkedin_posts_by_lote,
    get_temas_disponibles,
    init_db,
    marcar_tema_usado,
    seed_linkedin_temas,
    update_linkedin_post,
)


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "test.db")
    init_db(p)
    return p


def test_init_crea_las_tablas_de_linkedin(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    nombres = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    conn.close()
    assert "linkedin_posts" in nombres
    assert "linkedin_temas" in nombres


def test_businesses_tiene_linkedin_ok_en_cero(db_path):
    import sqlite3
    from database import insert_business
    bid = insert_business(db_path, {"name": "Panadería La Nueva", "phone": "+598 99 111 222"})
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT linkedin_ok FROM businesses WHERE id = ?", (bid,)).fetchone()
    conn.close()
    assert row["linkedin_ok"] == 0


def test_seed_temas_es_idempotente(db_path):
    import sqlite3
    seed_linkedin_temas(db_path)
    seed_linkedin_temas(db_path)
    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM linkedin_temas").fetchone()[0]
    conn.close()
    assert total >= 40


def test_create_y_get_por_job(db_path):
    pid = create_linkedin_post(
        db_path,
        job_id=7,
        lote="lote-7",
        tipo="educativo",
        texto="Un texto cualquiera.",
        angulo="concreto",
        fuente_tipo="tema",
        fuente_id=3,
        imagen_tipo="tarjeta",
        imagen_spec='{"frase": "hola"}',
        marcar_token="tok-abc",
    )
    posts = get_linkedin_posts_by_job(db_path, 7)
    assert len(posts) == 1
    assert posts[0]["id"] == pid
    assert posts[0]["estado"] == "generado"
    assert posts[0]["texto"] == "Un texto cualquiera."


def test_get_por_lote(db_path):
    create_linkedin_post(
        db_path, job_id=1, lote="lote-a", tipo="educativo", texto="a" * 300,
        angulo="concreto", fuente_tipo="tema", fuente_id=1,
        imagen_tipo="ninguna", imagen_spec="{}", marcar_token="tok-a",
    )
    create_linkedin_post(
        db_path, job_id=2, lote="lote-b", tipo="educativo", texto="b" * 300,
        angulo="concreto", fuente_tipo="tema", fuente_id=2,
        imagen_tipo="ninguna", imagen_spec="{}", marcar_token="tok-b",
    )

    assert len(get_linkedin_posts_by_lote(db_path, "lote-a")) == 1
    assert get_linkedin_posts_by_lote(db_path, "lote-a")[0]["texto"] == "a" * 300
    assert get_linkedin_posts_by_lote(db_path, "inexistente") == []


def test_get_por_token_y_update(db_path):
    pid = create_linkedin_post(
        db_path, job_id=1, lote="l1", tipo="educativo", texto="x" * 300,
        angulo="a", fuente_tipo="tema", fuente_id=1, imagen_tipo="ninguna",
        imagen_spec="{}", marcar_token="tok-1",
    )
    assert get_linkedin_post_by_token(db_path, "tok-1")["id"] == pid
    update_linkedin_post(db_path, pid, estado="publicado", marcar_token=None)
    assert get_linkedin_post_by_token(db_path, "tok-1") is None


def test_update_rechaza_columna_inventada(db_path):
    pid = create_linkedin_post(
        db_path, job_id=1, lote="l2", tipo="educativo", texto="x" * 300,
        angulo="a", fuente_tipo="tema", fuente_id=1, imagen_tipo="ninguna",
        imagen_spec="{}", marcar_token="tok-2",
    )
    with pytest.raises(ValueError):
        update_linkedin_post(db_path, pid, columna_que_no_existe="x")


def test_fuentes_usadas_solo_cuenta_enviados_y_publicados(db_path):
    create_linkedin_post(
        db_path, job_id=1, lote="l3", tipo="trabajo", texto="a" * 300,
        angulo="a", fuente_tipo="demo", fuente_id=10, imagen_tipo="ninguna",
        imagen_spec="{}", marcar_token="t1",
    )
    descartado = create_linkedin_post(
        db_path, job_id=1, lote="l3", tipo="trabajo", texto="b" * 300,
        angulo="b", fuente_tipo="demo", fuente_id=11, imagen_tipo="ninguna",
        imagen_spec="{}", marcar_token="t2",
    )
    update_linkedin_post(db_path, descartado, estado="descartado")
    enviado = create_linkedin_post(
        db_path, job_id=1, lote="l3", tipo="trabajo", texto="c" * 300,
        angulo="c", fuente_tipo="demo", fuente_id=12, imagen_tipo="ninguna",
        imagen_spec="{}", marcar_token="t3",
    )
    update_linkedin_post(db_path, enviado, estado="enviado")

    usadas = get_fuentes_usadas(db_path)
    assert ("demo", 12) in usadas
    assert ("demo", 10) not in usadas   # sigue en 'generado'
    assert ("demo", 11) not in usadas   # descartado


def test_temas_disponibles_respeta_el_cooldown(db_path):
    seed_linkedin_temas(db_path)
    todos = get_temas_disponibles(db_path, "2026-03-01T00:00:00")
    primero = todos[0]["id"]

    marcar_tema_usado(db_path, primero, "2026-08-01T00:00:00")
    libres = get_temas_disponibles(db_path, "2026-03-01T00:00:00")
    assert primero not in {t["id"] for t in libres}

    # con un límite posterior al uso, el tema vuelve a estar disponible
    vueltos = get_temas_disponibles(db_path, "2026-09-01T00:00:00")
    assert primero in {t["id"] for t in vueltos}
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `pytest tests/test_linkedin_db.py -v`
Expected: FAIL con `ImportError: cannot import name 'create_linkedin_post' from 'database'`

- [ ] **Step 3: Agregar las tablas dentro de `init_db`**

En `database.py`, dentro de `init_db`, justo después del bloque `CREATE TABLE IF NOT EXISTS activity_log (...)` y antes del `conn.commit()` final:

```python
        # ── LinkedIn ──────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS linkedin_posts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          INTEGER,
                lote            TEXT NOT NULL,
                tipo            TEXT NOT NULL,
                texto           TEXT NOT NULL,
                angulo          TEXT,
                fuente_tipo     TEXT NOT NULL,
                fuente_id       INTEGER,
                imagen_tipo     TEXT NOT NULL DEFAULT 'ninguna',
                imagen_spec     TEXT,
                fuente_desc     TEXT,
                aviso           TEXT,
                estado          TEXT NOT NULL DEFAULT 'generado',
                marcar_token    TEXT,
                creado_en       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                publicado_en    TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS linkedin_temas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo      TEXT NOT NULL UNIQUE,
                angulo      TEXT NOT NULL,
                usado_en    TIMESTAMP
            )
        """)
        _add_column(conn, "businesses", "linkedin_ok", "INTEGER DEFAULT 0")
```

- [ ] **Step 4: Agregar las funciones de acceso al final de `database.py`**

```python
# ─── LinkedIn ─────────────────────────────────────────────────────────────────

_LINKEDIN_POST_COLUMNS = {
    "job_id", "lote", "tipo", "texto", "angulo", "fuente_tipo", "fuente_id",
    "imagen_tipo", "imagen_spec", "fuente_desc", "aviso", "estado",
    "marcar_token", "publicado_en",
}


def create_linkedin_post(db_path: str, **fields) -> int:
    invalid = set(fields) - _LINKEDIN_POST_COLUMNS
    if invalid:
        raise ValueError(f"Invalid linkedin_posts columns: {invalid}")
    cols = ", ".join(fields)
    marks = ", ".join(f":{k}" for k in fields)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            f"INSERT INTO linkedin_posts ({cols}) VALUES ({marks})", fields
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_linkedin_post(db_path: str, post_id: int, **fields) -> None:
    invalid = set(fields) - _LINKEDIN_POST_COLUMNS
    if invalid:
        raise ValueError(f"Invalid linkedin_posts columns: {invalid}")
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = post_id
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE linkedin_posts SET {set_clause} WHERE id = :id", fields)
        conn.commit()
    finally:
        conn.close()


def get_linkedin_posts_by_job(db_path: str, job_id: int) -> list[dict]:
    """Accesor de diagnostico: que borradores dejo una corrida del worker.
    El pipeline busca por lote, no por job."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM linkedin_posts WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_linkedin_posts_by_lote(db_path: str, lote: str) -> list[dict]:
    """Los borradores de una corrida. El lote lo genera el endpoint antes de
    encolar el job, asi que no hay ventana en la que el worker vea una fila
    sin identificar."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM linkedin_posts WHERE lote = ? ORDER BY id", (lote,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_linkedin_post_by_token(db_path: str, token: str) -> Optional[dict]:
    if not token:
        return None
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM linkedin_posts WHERE marcar_token = ?", (token,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_fuentes_usadas(db_path: str) -> set:
    """Pares (fuente_tipo, fuente_id) que ya salieron por mail o se publicaron.

    Un borrador en estado 'generado' todavia no salio a ningun lado y uno
    'descartado' no lo va a hacer nunca, asi que su fuente sigue libre.
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT fuente_tipo, fuente_id FROM linkedin_posts "
            "WHERE estado IN ('enviado', 'publicado')"
        ).fetchall()
        return {(r["fuente_tipo"], r["fuente_id"]) for r in rows}
    finally:
        conn.close()


def get_temas_disponibles(db_path: str, limite_iso: str) -> list[dict]:
    """Temas nunca usados o usados antes de `limite_iso`, mas viejos primero."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM linkedin_temas "
            "WHERE usado_en IS NULL OR usado_en < ? "
            "ORDER BY usado_en IS NOT NULL, usado_en, id",
            (limite_iso,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def marcar_tema_usado(db_path: str, tema_id: int, cuando_iso: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE linkedin_temas SET usado_en = ? WHERE id = ?", (cuando_iso, tema_id)
        )
        conn.commit()
    finally:
        conn.close()


def seed_linkedin_temas(db_path: str) -> None:
    """Inserta los temas educativos que falten. Idempotente por titulo."""
    temas = [
        ("Por que tu negocio no aparece en Google Maps", "la ficha existe pero esta incompleta, y eso decide quien te encuentra"),
        ("Que es un CRM y por que tu Excel no lo es", "el Excel no te avisa, no recuerda y no lo ve tu equipo"),
        ("Cuanto tarda de verdad una tienda online", "el desarrollo es la parte corta; cargar el catalogo es la larga"),
        ("El costo de no tener web cuando te buscan por el nombre", "si no aparecen, el cliente asume que cerraste"),
        ("WhatsApp Business no es lo mismo que WhatsApp", "catalogo, respuestas rapidas y etiquetas cambian el volumen que aguantas"),
        ("Por que el pago online sube el ticket promedio", "el que ya pago no negocia el envio"),
        ("Que mirar antes de contratar a alguien que te haga la web", "quien queda dueño del dominio y del hosting"),
        ("El dominio es tuyo, no de quien te hizo la web", "si esta a nombre de otro, tu negocio esta prestado"),
        ("Tres numeros que deberias saber de tu propio negocio", "cuanto vendes, cuanto te cuesta vender y cuanto vuelve"),
        ("Automatizar no es reemplazar gente", "es sacarle a la gente lo que hace una maquina mejor"),
        ("Cuando conviene una web y cuando alcanza con Instagram", "el limite lo pone el catalogo y el horario de atencion"),
        ("Por que los formularios de contacto no reciben nada", "van a una casilla que nadie abre"),
        ("El stock que no cuadra sale caro dos veces", "vendes lo que no tenes y no vendes lo que tenes"),
        ("Que hace un sistema de gestion que no hace una planilla", "el historial, los permisos y que dos personas escriban a la vez"),
        ("Facturacion electronica en Uruguay sin dolor de cabeza", "quien la emite y como se integra con lo que ya usas"),
        ("Como se ve tu negocio desde un celular", "la mitad de las visitas entra desde el telefono y ve otra cosa"),
        ("Cuando tu web tarda mas de tres segundos ya perdiste visitas", "la velocidad no es estetica, es plata"),
        ("Las resenas de Google se responden todas", "las malas tambien, sobre todo las malas"),
        ("Que pasa con tus datos si se rompe la computadora del mostrador", "sin backup no hay negocio, hay suerte"),
        ("Por que pedir presupuesto por rubro y no por horas", "las horas no te dicen que te llevas"),
        ("La diferencia entre una web y un catalogo online", "una informa, la otra vende"),
        ("Click and collect: vender online y entregar en el local", "sin costo de envio y con el cliente adentro del local"),
        ("Cuanto cuesta mantener una web al año", "dominio, hosting y las horas de quien la actualiza"),
        ("El error de tener el telefono solo en la foto de portada", "nadie transcribe un numero de una imagen"),
        ("Que datos pedir en un formulario y cuales sobran", "cada campo de mas te cuesta respuestas"),
        ("Como saber si tu publicidad esta funcionando", "si no podes decir cuantos clientes trajo, no lo sabes"),
        ("Integrar el sistema de tu proveedor con el tuyo", "cuando no hay API, el CSV sigue siendo una respuesta"),
        ("Por que separar la casilla del negocio de la personal", "el dia que te vas de vacaciones tu negocio sigue recibiendo"),
        ("Un correo con tu dominio cuesta menos de lo que pensas", "y cambia como te leen los proveedores"),
        ("Que es el SEO local y por que te importa mas que el otro", "compites contra los cinco negocios de tu barrio, no contra el mundo"),
        ("Digitalizar de a poco tambien es digitalizar", "el orden importa mas que la velocidad"),
        ("Las tres preguntas antes de comprar cualquier software", "quien lo usa, que reemplaza y como salgo si no me sirve"),
        ("El sistema que nadie usa es un gasto, no una inversion", "la adopcion se diseña, no se pide"),
        ("Que mirar en un contrato de desarrollo de software", "entregables, plazos y que pasa despues de la entrega"),
        ("Por que tu tienda online no vende aunque tenga visitas", "el problema esta entre el carrito y el pago"),
        ("Los costos de envio decididos tarde matan la venta", "el cliente los quiere ver antes de cargar la tarjeta"),
        ("Que informacion tiene que estar si o si en tu web", "que vendes, donde estas, como te contactan y a que hora abris"),
        ("Como se organiza un negocio con dos personas y cien pedidos", "primero el flujo, despues la herramienta"),
        ("Cuando conviene hacerlo a medida y cuando comprar hecho", "lo raro de tu negocio se hace a medida, el resto se compra"),
        ("La transformacion digital no empieza por la tecnologia", "empieza por escribir como trabajas hoy"),
        ("Por que el mismo producto tiene tres precios en tres lugares", "una fuente de verdad o ninguna"),
        ("Que hacer con los contactos que juntaste y nunca usaste", "una base vieja vale mas que una campaña nueva"),
    ]
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO linkedin_temas (titulo, angulo) VALUES (?, ?)",
            temas,
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 5: Sembrar los temas al arrancar**

En `server.py`, después de `seed_pitch_templates(db_path)`:

```python
from database import init_db, seed_linkedin_temas, seed_pitch_templates
```

y en la línea siguiente a `seed_pitch_templates(db_path)`:

```python
seed_linkedin_temas(db_path)
```

- [ ] **Step 6: Correr los tests**

Run: `pytest tests/test_linkedin_db.py -v`
Expected: PASS, 9 tests

Run también la suite entera para verificar que el `_add_column` nuevo no rompió nada:
Run: `pytest -q`
Expected: la misma cantidad de fallos que antes del cambio (idealmente 0)

- [ ] **Step 7: Commit**

```bash
git add database.py server.py tests/test_linkedin_db.py
git commit -m "feat(linkedin): tablas linkedin_posts y linkedin_temas con semilla de temas"
```

---

### Task 2: Recolección de material

**Files:**
- Create: `services/linkedin_posts.py`
- Test: `tests/test_linkedin_material.py`

**Interfaces:**
- Consumes: `get_fuentes_usadas`, `get_temas_disponibles`, `marcar_tema_usado` (Task 1); `demos`, `businesses`, `lead_events` (tablas existentes).
- Produces:
  - `VENTANA_DIAS = 10`
  - `COOLDOWN_DIAS = 180`
  - `recolectar_material(db_path: str, ahora: datetime) -> list[dict]` — cada dict tiene `fuente_tipo`, `fuente_id`, `nombre`, `rubro`, `ciudad`, `url`, `que_paso`, `linkedin_ok`.
  - `elegir_tema(db_path: str, ahora: datetime, excluir_ids: set) -> tuple[dict, bool]` — devuelve `(tema, en_cooldown)`; `en_cooldown=True` significa que se tuvo que reusar un tema que no cumplió los 180 días.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_linkedin_material.py`:

```python
import sqlite3
from datetime import datetime

import pytest

from database import init_db, insert_business, seed_linkedin_temas
from services.linkedin_posts import elegir_tema, recolectar_material


AHORA = datetime(2026, 8, 26, 9, 0, 0)


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "test.db")
    init_db(p)
    return p


def _demo(db, client_id, url, generated_at):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO demos (client_id, status, url, generated_at) VALUES (?, 'done', ?, ?)",
        (client_id, url, generated_at),
    )
    conn.commit()
    conn.close()


def _evento(db, lead_id, estado, cuando):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO lead_events (lead_id, new_status, created_at) VALUES (?, ?, ?)",
        (lead_id, estado, cuando),
    )
    conn.commit()
    conn.close()


def test_encuentra_una_demo_reciente(db_path):
    bid = insert_business(db_path, {
        "name": "Bloquera La Cadena", "category": "bloquera",
        "city": "Durazno", "phone": "+598 99 000 001",
    })
    _demo(db_path, bid, "https://lacadena.vercel.app", "2026-08-24 10:00:00")

    material = recolectar_material(db_path, AHORA)

    assert len(material) == 1
    assert material[0]["fuente_tipo"] == "demo"
    assert material[0]["nombre"] == "Bloquera La Cadena"
    assert material[0]["url"] == "https://lacadena.vercel.app"
    assert material[0]["linkedin_ok"] == 0


def test_ignora_demos_viejas_y_sin_url(db_path):
    viejo = insert_business(db_path, {"name": "Vieja", "phone": "+598 99 000 002"})
    _demo(db_path, viejo, "https://vieja.vercel.app", "2026-07-01 10:00:00")

    sin_url = insert_business(db_path, {"name": "Sin URL", "phone": "+598 99 000 003"})
    _demo(db_path, sin_url, "", "2026-08-25 10:00:00")

    assert recolectar_material(db_path, AHORA) == []


def test_encuentra_un_cliente_cerrado(db_path):
    bid = insert_business(db_path, {
        "name": "Marejada", "category": "surf", "city": "Punta Negra",
        "phone": "+598 99 000 004",
    })
    _evento(db_path, bid, "finalizado", "2026-08-22 12:00:00")

    material = recolectar_material(db_path, AHORA)

    assert len(material) == 1
    assert material[0]["fuente_tipo"] == "cliente"
    assert material[0]["fuente_id"] == bid
    assert "finaliz" in material[0]["que_paso"]


def test_no_repite_una_fuente_ya_enviada(db_path):
    from database import create_linkedin_post, update_linkedin_post

    bid = insert_business(db_path, {"name": "Repetida", "phone": "+598 99 000 005"})
    _demo(db_path, bid, "https://repetida.vercel.app", "2026-08-24 10:00:00")

    pid = create_linkedin_post(
        db_path, job_id=1, lote="lx", tipo="trabajo", texto="x" * 300,
        angulo="a", fuente_tipo="demo", fuente_id=bid, imagen_tipo="ninguna",
        imagen_spec="{}", marcar_token="t",
    )
    update_linkedin_post(db_path, pid, estado="enviado")

    assert recolectar_material(db_path, AHORA) == []


def test_respeta_linkedin_ok(db_path):
    bid = insert_business(db_path, {"name": "Autorizada", "phone": "+598 99 000 006"})
    _demo(db_path, bid, "https://autorizada.vercel.app", "2026-08-24 10:00:00")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE businesses SET linkedin_ok = 1 WHERE id = ?", (bid,))
    conn.commit()
    conn.close()

    assert recolectar_material(db_path, AHORA)[0]["linkedin_ok"] == 1


def test_elegir_tema_devuelve_uno_libre(db_path):
    seed_linkedin_temas(db_path)
    tema, en_cooldown = elegir_tema(db_path, AHORA, excluir_ids=set())
    assert tema["titulo"]
    assert en_cooldown is False


def test_elegir_tema_excluye_los_del_mismo_mail(db_path):
    seed_linkedin_temas(db_path)
    primero, _ = elegir_tema(db_path, AHORA, excluir_ids=set())
    segundo, _ = elegir_tema(db_path, AHORA, excluir_ids={primero["id"]})
    assert segundo["id"] != primero["id"]


def test_elegir_tema_reusa_el_mas_viejo_si_no_queda_ninguno(db_path):
    from database import marcar_tema_usado

    seed_linkedin_temas(db_path)
    conn = sqlite3.connect(db_path)
    ids = [r[0] for r in conn.execute("SELECT id FROM linkedin_temas")]
    conn.close()
    for i in ids:
        marcar_tema_usado(db_path, i, "2026-08-20T00:00:00")

    tema, en_cooldown = elegir_tema(db_path, AHORA, excluir_ids=set())

    assert tema is not None
    assert en_cooldown is True
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `pytest tests/test_linkedin_material.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'services.linkedin_posts'`

- [ ] **Step 3: Escribir la implementación**

Crear `services/linkedin_posts.py`:

```python
"""Borradores de post para la pagina de LinkedIn de Scalerics.

Recolecta material de la base, redacta con Claude y valida el texto contra
las reglas de voz. No publica nada: el resultado se manda por mail.
"""

import logging
from datetime import datetime, timedelta

from database import (
    _connect,
    get_fuentes_usadas,
    get_temas_disponibles,
)

logger = logging.getLogger(__name__)

VENTANA_DIAS = 10
COOLDOWN_DIAS = 180

_ESTADOS_DE_CIERRE = ("cliente_cerrado", "finalizado")


def recolectar_material(db_path: str, ahora: datetime) -> list[dict]:
    """Candidatos de tipo 'trabajo' de los ultimos VENTANA_DIAS dias.

    SQLite guarda estos timestamps como texto 'YYYY-MM-DD HH:MM:SS', asi que
    la comparacion es lexicografica y funciona sin convertir a fecha.
    """
    desde = (ahora - timedelta(days=VENTANA_DIAS)).strftime("%Y-%m-%d %H:%M:%S")
    usadas = get_fuentes_usadas(db_path)
    material: list[dict] = []

    conn = _connect(db_path)
    try:
        demos = conn.execute(
            """
            SELECT d.client_id, d.url, d.generated_at,
                   b.name, b.category, b.city, b.linkedin_ok
            FROM demos d
            JOIN businesses b ON b.id = d.client_id
            WHERE d.url IS NOT NULL AND d.url != ''
              AND d.generated_at IS NOT NULL AND d.generated_at >= ?
            ORDER BY d.generated_at DESC
            """,
            (desde,),
        ).fetchall()

        for d in demos:
            if ("demo", d["client_id"]) in usadas:
                continue
            material.append({
                "fuente_tipo": "demo",
                "fuente_id": d["client_id"],
                "nombre": d["name"],
                "rubro": d["category"] or "",
                "ciudad": d["city"] or "",
                "url": d["url"],
                "que_paso": "se le armo y publico una demo de sitio web",
                "linkedin_ok": d["linkedin_ok"] or 0,
                "cuando": d["generated_at"],
            })

        marcas = ", ".join("?" for _ in _ESTADOS_DE_CIERRE)
        cerrados = conn.execute(
            f"""
            SELECT e.lead_id, e.new_status, MAX(e.created_at) AS cuando,
                   b.name, b.category, b.city, b.demo_url, b.linkedin_ok
            FROM lead_events e
            JOIN businesses b ON b.id = e.lead_id
            WHERE e.new_status IN ({marcas}) AND e.created_at >= ?
            GROUP BY e.lead_id
            ORDER BY cuando DESC
            """,
            (*_ESTADOS_DE_CIERRE, desde),
        ).fetchall()

        for c in cerrados:
            if ("cliente", c["lead_id"]) in usadas:
                continue
            material.append({
                "fuente_tipo": "cliente",
                "fuente_id": c["lead_id"],
                "nombre": c["name"],
                "rubro": c["category"] or "",
                "ciudad": c["city"] or "",
                "url": c["demo_url"] or "",
                "que_paso": (
                    "el proyecto quedo finalizado y entregado"
                    if c["new_status"] == "finalizado"
                    else "se cerro como cliente y arranca el desarrollo"
                ),
                "linkedin_ok": c["linkedin_ok"] or 0,
                "cuando": c["cuando"],
            })
    finally:
        conn.close()

    material.sort(key=lambda m: m["cuando"], reverse=True)
    return material


def elegir_tema(db_path: str, ahora: datetime, excluir_ids: set) -> tuple:
    """Devuelve (tema, en_cooldown).

    en_cooldown=True significa que no quedaba ningun tema fuera del cooldown
    y se reuso el mas viejo. El mail lo dice, para que se note que hay que
    sembrar temas nuevos.
    """
    limite = (ahora - timedelta(days=COOLDOWN_DIAS)).isoformat()

    libres = [t for t in get_temas_disponibles(db_path, limite)
              if t["id"] not in excluir_ids]
    if libres:
        return libres[0], False

    # Fallback: el mas viejo de todos, aunque no haya cumplido el cooldown.
    todos = get_temas_disponibles(db_path, ahora.isoformat())
    todos = [t for t in todos if t["id"] not in excluir_ids]
    if not todos:
        raise RuntimeError("No hay temas de LinkedIn sembrados")
    return todos[0], True
```

- [ ] **Step 4: Correr los tests**

Run: `pytest tests/test_linkedin_material.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add services/linkedin_posts.py tests/test_linkedin_material.py
git commit -m "feat(linkedin): recoleccion de material y eleccion de tema con cooldown"
```

---

### Task 3: Validador de voz

**Files:**
- Modify: `services/linkedin_posts.py`
- Test: `tests/test_linkedin_validador.py`

**Interfaces:**
- Produces: `validar_borrador(texto: str) -> list[str]` — lista de violaciones en español; vacía significa que el texto pasa.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_linkedin_validador.py`:

```python
from services.linkedin_posts import validar_borrador


BUENO = (
    "Esta semana armamos la tienda online de una bloquera de Durazno. "
    "Tres dias de trabajo, catalogo con 40 productos y pago con Mercado Pago. "
    "Lo que mas costo no fue el codigo, fue ordenar los precios: tenian tres "
    "listas distintas segun el vendedor que atendiera. Eso pasa mas seguido "
    "de lo que parece, y es la parte que de verdad cambia el negocio. "
    "#Uruguay #PyMEs"
)


def test_un_texto_bien_escrito_pasa():
    assert validar_borrador(BUENO) == []


def test_rechaza_el_guion_largo():
    violaciones = validar_borrador(BUENO.replace("codigo, fue", "codigo — fue"))
    assert any("guion largo" in v for v in violaciones)


def test_rechaza_el_guion_medio():
    violaciones = validar_borrador(BUENO.replace("codigo, fue", "codigo – fue"))
    assert any("guion medio" in v for v in violaciones)


def test_rechaza_emojis():
    violaciones = validar_borrador(BUENO + " \U0001F680")
    assert any("emoji" in v for v in violaciones)


def test_rechaza_mas_de_dos_hashtags():
    violaciones = validar_borrador(BUENO + " #Software #Tecnologia")
    assert any("hashtag" in v for v in violaciones)


def test_rechaza_texto_largo():
    violaciones = validar_borrador("a" * 1301)
    assert any("largo" in v for v in violaciones)


def test_rechaza_texto_corto():
    violaciones = validar_borrador("Muy corto.")
    assert any("corto" in v for v in violaciones)


def test_acumula_varias_violaciones():
    violaciones = validar_borrador("Corto — \U0001F600")
    assert len(violaciones) >= 3
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `pytest tests/test_linkedin_validador.py -v`
Expected: FAIL con `ImportError: cannot import name 'validar_borrador'`

- [ ] **Step 3: Escribir la implementación**

Agregar a `services/linkedin_posts.py` (después de las constantes, antes de `recolectar_material`):

```python
import re

MAX_CARACTERES = 1300
MIN_CARACTERES = 200
MAX_HASHTAGS = 2

# Rangos de emoji. No es exhaustivo pero cubre todo lo que un modelo pone
# en un post: emoticones, simbolos, transporte, banderas, dingbats y las
# flechas decorativas.
_EMOJI = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002190-\U000021FF"
    "\U0000FE0F"
    "\U00002B00-\U00002BFF"
    "]"
)


def validar_borrador(texto: str) -> list[str]:
    """Violaciones de las reglas de voz. Lista vacia = el texto pasa."""
    violaciones = []
    texto = texto or ""

    if "—" in texto:
        violaciones.append("usa guion largo (—), que esta prohibido")
    if "–" in texto:
        violaciones.append("usa guion medio (–), que esta prohibido")
    if _EMOJI.search(texto):
        violaciones.append("tiene al menos un emoji, y no se permiten emojis")

    hashtags = re.findall(r"#\w+", texto)
    if len(hashtags) > MAX_HASHTAGS:
        violaciones.append(
            f"tiene {len(hashtags)} hashtags y el maximo es {MAX_HASHTAGS}"
        )

    largo = len(texto)
    if largo > MAX_CARACTERES:
        violaciones.append(
            f"es demasiado largo: {largo} caracteres, el maximo es {MAX_CARACTERES}"
        )
    if largo < MIN_CARACTERES:
        violaciones.append(
            f"es demasiado corto: {largo} caracteres, el minimo es {MIN_CARACTERES}"
        )

    return violaciones
```

- [ ] **Step 4: Correr los tests**

Run: `pytest tests/test_linkedin_validador.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add services/linkedin_posts.py tests/test_linkedin_validador.py
git commit -m "feat(linkedin): validador deterministico de las reglas de voz"
```

---

### Task 4: Redacción con Claude

**Files:**
- Modify: `services/linkedin_posts.py`
- Test: `tests/test_linkedin_redaccion.py`

**Interfaces:**
- Consumes: `validar_borrador` (Task 3).
- Produces:
  - `MODELO = "claude-opus-5"`
  - `SYSTEM_PROMPT: str`
  - `redactar(contexto: str, angulo: str) -> Optional[str]` — devuelve el texto validado, o `None` si falló las dos veces.
  - `contexto_trabajo(candidato: dict) -> str`
  - `contexto_educativo(tema: dict) -> str`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_linkedin_redaccion.py`:

```python
from unittest.mock import MagicMock, patch

from services.linkedin_posts import (
    contexto_educativo,
    contexto_trabajo,
    redactar,
)


VALIDO = (
    "Esta semana armamos la tienda online de una bloquera de Durazno. "
    "Tres dias de trabajo, catalogo con 40 productos y pago con Mercado Pago. "
    "Lo que mas costo no fue el codigo, fue ordenar los precios: tenian tres "
    "listas distintas segun el vendedor que atendiera. Eso pasa mas seguido "
    "de lo que parece, y es la parte que de verdad cambia el negocio."
)
INVALIDO = "Corto — con guion largo."


def _respuesta(texto):
    msg = MagicMock()
    bloque = MagicMock()
    bloque.type = "text"
    bloque.text = texto
    msg.content = [bloque]
    return msg


def test_redactar_devuelve_el_texto_cuando_valida():
    with patch("services.linkedin_posts.anthropic.Anthropic") as Cliente, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        Cliente.return_value.messages.create.return_value = _respuesta(VALIDO)
        resultado = redactar("contexto cualquiera", "concreto")

    assert resultado == VALIDO


def test_redactar_reintenta_una_vez_y_acepta_el_segundo():
    with patch("services.linkedin_posts.anthropic.Anthropic") as Cliente, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        Cliente.return_value.messages.create.side_effect = [
            _respuesta(INVALIDO), _respuesta(VALIDO),
        ]
        resultado = redactar("contexto cualquiera", "concreto")

    assert resultado == VALIDO
    assert Cliente.return_value.messages.create.call_count == 2


def test_redactar_devuelve_none_si_falla_dos_veces():
    with patch("services.linkedin_posts.anthropic.Anthropic") as Cliente, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        Cliente.return_value.messages.create.side_effect = [
            _respuesta(INVALIDO), _respuesta(INVALIDO),
        ]
        resultado = redactar("contexto cualquiera", "concreto")

    assert resultado is None
    assert Cliente.return_value.messages.create.call_count == 2


def test_redactar_devuelve_none_si_la_api_falla():
    with patch("services.linkedin_posts.anthropic.Anthropic") as Cliente, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        Cliente.return_value.messages.create.side_effect = RuntimeError("boom")
        assert redactar("contexto", "concreto") is None


def test_el_reintento_le_dice_al_modelo_que_rompio():
    with patch("services.linkedin_posts.anthropic.Anthropic") as Cliente, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        Cliente.return_value.messages.create.side_effect = [
            _respuesta(INVALIDO), _respuesta(VALIDO),
        ]
        redactar("contexto", "concreto")
        segunda = Cliente.return_value.messages.create.call_args_list[1]

    texto_enviado = str(segunda.kwargs["messages"])
    assert "guion largo" in texto_enviado


def test_contexto_trabajo_incluye_los_datos_reales():
    ctx = contexto_trabajo({
        "nombre": "Bloquera La Cadena", "rubro": "bloquera", "ciudad": "Durazno",
        "url": "https://lacadena.vercel.app",
        "que_paso": "se le armo y publico una demo de sitio web",
        "linkedin_ok": 1,
    })
    assert "Bloquera La Cadena" in ctx
    assert "Durazno" in ctx
    assert "demo" in ctx


def test_contexto_educativo_incluye_titulo_y_angulo():
    ctx = contexto_educativo({
        "titulo": "Que es un CRM y por que tu Excel no lo es",
        "angulo": "el Excel no te avisa, no recuerda y no lo ve tu equipo",
    })
    assert "CRM" in ctx
    assert "Excel no te avisa" in ctx
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `pytest tests/test_linkedin_redaccion.py -v`
Expected: FAIL con `ImportError: cannot import name 'contexto_educativo'`

- [ ] **Step 3: Escribir la implementación**

Agregar los imports arriba de `services/linkedin_posts.py`:

```python
import os

import anthropic
```

Y al final del archivo:

```python
MODELO = "claude-opus-5"

SYSTEM_PROMPT = """Escribis posts de LinkedIn para Scalerics, una software \
factory uruguaya que digitaliza PyMEs. Los firma Juan, que es el unico \
desarrollador. Escribis con su voz, y su voz tiene reglas duras.

Prohibido:
- El guion largo (—) y el guion medio (–). Usa coma, punto o dos puntos.
- Los emojis. Ninguno, en ningun lado.
- La estructura tipica del post de LinkedIn: parrafos todos del mismo largo, \
listas con flechas o vinetas, enumeraciones de tres, la formula "no es X, es Y", \
y los cierres del tipo "Excited for what's ahead" o "seguimos construyendo".
- Encuadrar el trabajo como esfuerzo o sacrificio. Nada de "agotador", "brutal" \
o "lo mas dificil que hice". A Juan le gusta programar; el encuadre es disfrute \
y ambicion.
- Las frases construidas para sonar bien pero que no dicen nada.
- Inventar datos. Solo podes usar lo que aparece en el contexto que te paso.

Obligatorio:
- Espanol rioplatense, voseo.
- Frases cortas y directas. Datos concretos: cantidades, tiempos, nombres, plazos.
- Tono seguro, sin falsa modestia y sin pedir permiso.
- Entre 400 y 1200 caracteres.
- Como maximo dos hashtags, al final, sin espacios raros.
- Arranca con algo concreto. La primera linea es lo unico que se ve antes del \
"ver mas", asi que tiene que valer sola.

Devolves solamente el texto del post. Sin titulo, sin comillas, sin explicaciones \
ni comentarios sobre lo que escribiste."""

_ANGULOS = {
    "concreto": (
        "Conta el hecho: que se hizo, para quien, cuanto llevo, que problema "
        "concreto resolvio. Que se note el detalle de alguien que estuvo ahi."
    ),
    "implicancia": (
        "Arranca del hecho pero el post es sobre lo que ese hecho revela de "
        "como trabajan los negocios chicos. Una idea, no una lista."
    ),
}


def contexto_trabajo(candidato: dict) -> str:
    autorizado = (
        "El cliente autorizo que se lo nombre."
        if candidato.get("linkedin_ok")
        else "OJO: no hay autorizacion explicita para nombrar al cliente, "
             "pero escribi el post con el nombre igual; se revisa antes de publicar."
    )
    return f"""Hecho real de esta semana, sacado de la base del CRM:

- Negocio: {candidato['nombre']}
- Rubro: {candidato.get('rubro') or 'sin especificar'}
- Ciudad: {candidato.get('ciudad') or 'sin especificar'}
- Que paso: {candidato['que_paso']}
- Link: {candidato.get('url') or 'sin link publico'}

{autorizado}

No agregues ningun dato que no este en esta lista. Si algo no esta, no lo menciones."""


def contexto_educativo(tema: dict) -> str:
    return f"""Post educativo para duenos de PyMEs uruguayas. No hay un cliente \
ni un proyecto detras: es Juan explicando algo que ve seguido.

- Tema: {tema['titulo']}
- Lo que hay que dejar dicho: {tema['angulo']}

Podes usar ejemplos genericos y verosimiles del mercado uruguayo (un almacen de \
barrio, una bloquera del interior), pero no inventes clientes de Scalerics ni \
cifras de resultados que no tenes."""


def redactar(contexto: str, angulo: str):
    """Un post validado, o None si el modelo no logro uno en dos intentos."""
    instruccion_angulo = _ANGULOS.get(angulo, _ANGULOS["concreto"])
    mensajes = [{
        "role": "user",
        "content": f"{contexto}\n\nAngulo del post: {instruccion_angulo}",
    }]

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    except KeyError:
        logger.error("Falta ANTHROPIC_API_KEY; no se puede redactar")
        return None

    for intento in (1, 2):
        try:
            respuesta = client.messages.create(
                model=MODELO,
                max_tokens=4000,
                thinking={"type": "adaptive"},
                system=SYSTEM_PROMPT,
                messages=mensajes,
            )
        except Exception:
            logger.exception("Fallo la llamada a Claude (intento %s)", intento)
            return None

        texto = "".join(
            b.text for b in respuesta.content if getattr(b, "type", None) == "text"
        ).strip()

        violaciones = validar_borrador(texto)
        if not violaciones:
            return texto

        logger.warning("Borrador rechazado (intento %s): %s", intento, violaciones)
        if intento == 2:
            return None

        # El reintento lleva el texto rechazado y la lista de lo que rompio.
        mensajes = mensajes + [
            {"role": "assistant", "content": texto},
            {"role": "user", "content":
                "Ese texto rompe las reglas. Problemas: "
                + "; ".join(violaciones)
                + ". Reescribilo entero corrigiendo eso y respetando todo lo demas."},
        ]

    return None
```

- [ ] **Step 4: Correr los tests**

Run: `pytest tests/test_linkedin_redaccion.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add services/linkedin_posts.py tests/test_linkedin_redaccion.py
git commit -m "feat(linkedin): redaccion con claude-opus-5 y reintento sobre las violaciones"
```

---

### Task 5: Mail con adjuntos

**Files:**
- Modify: `services/email_service.py`
- Test: `tests/test_linkedin_mail.py`

**Interfaces:**
- Consumes: `_layout(badge, title, body, cta_url, cta_label)`, `_send(to, subject, html)` — ambos ya existen.
- Produces:
  - `_send(to, subject, html, attachments=None) -> bool` — firma extendida, retrocompatible.
  - `send_linkedin_drafts(to: str, borradores: list[dict], base_url: str, aviso_cooldown: bool = False) -> bool`
  - `send_linkedin_failure(to: str, motivo: str) -> bool`

  Cada borrador es un dict con `id`, `tipo`, `texto`, `fuente_desc`, `aviso` (str o `""`), `marcar_token`, `png_b64` (str o `""`).

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_linkedin_mail.py`:

```python
from unittest.mock import patch

import services.email_service as es


BORRADOR = {
    "id": 1,
    "tipo": "trabajo",
    "texto": "Esta semana armamos la tienda de una bloquera de Durazno.",
    "fuente_desc": "Demo de Bloquera La Cadena (https://lacadena.vercel.app)",
    "aviso": "Confirma con el cliente antes de publicar.",
    "marcar_token": "tok-1",
    "png_b64": "iVBORw0KGgo=",
}


def _capturar():
    return patch.object(es, "_send", return_value=True)


def test_manda_un_mail_con_el_texto_del_borrador():
    with _capturar() as send:
        ok = es.send_linkedin_drafts("a@b.com", [BORRADOR], "https://crm.test")
    assert ok is True
    html = send.call_args.kwargs.get("html") or send.call_args.args[2]
    assert "bloquera de Durazno" in html


def test_el_asunto_dice_cuantos_borradores_van():
    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [BORRADOR], "https://crm.test")
    asunto = send.call_args.kwargs.get("subject") or send.call_args.args[1]
    assert "1 borrador" in asunto

    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [BORRADOR, dict(BORRADOR, id=2)], "https://crm.test")
    asunto = send.call_args.kwargs.get("subject") or send.call_args.args[1]
    assert "2 borradores" in asunto


def test_muestra_el_aviso_de_autorizacion():
    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [BORRADOR], "https://crm.test")
    html = send.call_args.kwargs.get("html") or send.call_args.args[2]
    assert "Confirma con el cliente" in html


def test_sin_aviso_no_aparece_el_bloque():
    sin_aviso = dict(BORRADOR, aviso="")
    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [sin_aviso], "https://crm.test")
    html = send.call_args.kwargs.get("html") or send.call_args.args[2]
    assert "Confirma con el cliente" not in html


def test_adjunta_el_png_cuando_hay():
    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [BORRADOR], "https://crm.test")
    adjuntos = send.call_args.kwargs["attachments"]
    assert len(adjuntos) == 1
    assert adjuntos[0]["filename"].endswith(".png")
    assert adjuntos[0]["content"] == "iVBORw0KGgo="


def test_sin_png_no_adjunta_nada():
    sin_png = dict(BORRADOR, png_b64="")
    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [sin_png], "https://crm.test")
    assert send.call_args.kwargs["attachments"] == []


def test_el_link_de_marcar_lleva_el_token():
    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [BORRADOR], "https://crm.test")
    html = send.call_args.kwargs.get("html") or send.call_args.args[2]
    assert "https://crm.test/api/linkedin/marcar?token=tok-1" in html


def test_aviso_de_cooldown_aparece_solo_si_se_pide():
    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [BORRADOR], "https://crm.test", aviso_cooldown=True)
    html = send.call_args.kwargs.get("html") or send.call_args.args[2]
    assert "temas" in html.lower()


def test_mail_de_fallo():
    with _capturar() as send:
        ok = es.send_linkedin_failure("a@b.com", "los dos borradores fallaron la validacion")
    assert ok is True
    html = send.call_args.kwargs.get("html") or send.call_args.args[2]
    assert "fallaron la validacion" in html


def test_send_sin_attachments_no_manda_la_clave():
    with patch.dict("os.environ", {"RESEND_API_KEY": "k"}), \
         patch("services.email_service.requests.post") as post:
        post.return_value.status_code = 200
        es._send("a@b.com", "asunto", "<p>hola</p>")
    cuerpo = post.call_args.kwargs["json"]
    assert "attachments" not in cuerpo


def test_send_con_attachments_los_pasa():
    with patch.dict("os.environ", {"RESEND_API_KEY": "k"}), \
         patch("services.email_service.requests.post") as post:
        post.return_value.status_code = 200
        es._send("a@b.com", "asunto", "<p>hola</p>",
                 attachments=[{"filename": "x.png", "content": "AAA"}])
    cuerpo = post.call_args.kwargs["json"]
    assert cuerpo["attachments"][0]["filename"] == "x.png"
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `pytest tests/test_linkedin_mail.py -v`
Expected: FAIL con `AttributeError: module 'services.email_service' has no attribute 'send_linkedin_drafts'`

- [ ] **Step 3: Extender `_send` con adjuntos**

En `services/email_service.py`, cambiar la firma y el cuerpo del payload:

```python
def _send(to: str, subject: str, html: str, attachments: list = None) -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")
```

y donde arma el `json=` del `requests.post`, agregar los adjuntos solo si vienen
(así ningún llamador existente cambia de comportamiento):

```python
    payload = {
        "from": os.environ.get("RESEND_FROM_EMAIL", "Scalerics CRM <crm@scalerics.com>"),
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if attachments:
        payload["attachments"] = attachments
```

y pasar `json=payload` en el `requests.post`, conservando los headers y el
manejo de errores que ya tiene.

- [ ] **Step 4: Agregar los dos mails nuevos**

Al final de `services/email_service.py`:

```python
# ─── LinkedIn ─────────────────────────────────────────────────────────────────

_DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def _bloque_borrador(b: dict, base_url: str) -> str:
    aviso = ""
    if b.get("aviso"):
        aviso = (
            '<div style="background:#fff7ed;border-left:3px solid #f97316;'
            'padding:10px 14px;margin:0 0 14px;font-size:13px;color:#7c2d12">'
            f'{b["aviso"]}</div>'
        )

    texto_html = (b.get("texto") or "").replace("\n", "<br>")
    marcar = f'{base_url}/api/linkedin/marcar?token={b["marcar_token"]}'
    etiqueta = "Trabajo propio" if b.get("tipo") == "trabajo" else "Educativo"

    return f"""
    <div style="border:1px solid #e2e8f0;border-radius:8px;padding:18px;margin:0 0 22px">
      <div style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;
                  color:#64748b;margin:0 0 12px">{etiqueta}</div>
      {aviso}
      <div style="background:#f8fafc;border-radius:6px;padding:16px;font-family:
                  ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;
                  line-height:1.7;color:#0f172a;white-space:pre-wrap">{texto_html}</div>
      <div style="font-size:12px;color:#64748b;margin:14px 0 0">
        Datos usados: {b.get('fuente_desc', '')}
      </div>
      <div style="margin:14px 0 0">
        <a href="{marcar}" style="font-size:13px;color:#0088cc;text-decoration:none">
          Publique este</a>
      </div>
    </div>"""


def send_linkedin_drafts(to: str, borradores: list, base_url: str,
                         aviso_cooldown: bool = False) -> bool:
    from datetime import datetime

    hoy = datetime.now()
    dia = _DIAS[hoy.weekday()]
    cuantos = len(borradores)
    palabra = "borrador" if cuantos == 1 else "borradores"
    subject = f"{cuantos} {palabra} para LinkedIn - {dia} {hoy.day}/{hoy.month}"

    cuerpo = "".join(_bloque_borrador(b, base_url) for b in borradores)
    if aviso_cooldown:
        cuerpo += _muted(
            "Se reuso un tema educativo antes de los 180 dias porque no quedaban "
            "temas libres. Conviene sembrar temas nuevos en linkedin_temas."
        )

    attachments = [
        {"filename": f"linkedin-{b['id']}.png", "content": b["png_b64"]}
        for b in borradores if b.get("png_b64")
    ]

    html = _layout(
        badge="LinkedIn",
        title=f"{cuantos} {palabra} listos",
        body=cuerpo,
    )
    return _send(to, subject, html, attachments=attachments)


def send_linkedin_failure(to: str, motivo: str) -> bool:
    html = _layout(
        badge="LinkedIn",
        title="No salieron los borradores",
        body=(
            '<p style="font-size:14px;color:#334155;line-height:1.6">'
            f"No se pudo generar ningun borrador esta vez. Motivo: {motivo}.</p>"
        ),
    )
    return _send(to, "No salieron los borradores de LinkedIn", html)
```

- [ ] **Step 5: Correr los tests**

Run: `pytest tests/test_linkedin_mail.py tests/test_email_service_tope.py -v`
Expected: PASS. `test_email_service_tope.py` tiene que seguir pasando: la firma de `_send` es retrocompatible.

- [ ] **Step 6: Commit**

```bash
git add services/email_service.py tests/test_linkedin_mail.py
git commit -m "feat(linkedin): mail de borradores con adjuntos y mail de fallo"
```

---

### Task 6: Job handler, endpoints y registro en el worker

**Files:**
- Modify: `services/linkedin_posts.py`
- Create: `routes/linkedin.py`
- Modify: `dashboard.py` (registro del blueprint en `create_app`, línea ~4889; registro del handler en el worker, línea ~5986)
- Test: `tests/test_linkedin_endpoints.py`

**Interfaces:**
- Consumes: `recolectar_material`, `elegir_tema`, `redactar`, `contexto_trabajo`, `contexto_educativo` (Tasks 2-4); `create_linkedin_post`, `update_linkedin_post`, `get_linkedin_posts_by_lote`, `get_linkedin_post_by_token`, `marcar_tema_usado` (Task 1); `send_linkedin_drafts`, `send_linkedin_failure` (Task 5); `create_job`, `get_job`, `JobWorker.register` (existentes).
- Produces:
  - `linkedin_job_handler(payload: dict) -> dict` — devuelve `{"lote": ..., "borradores": [...], "aviso_cooldown": ...}`, que el `JobWorker` guarda en `jobs.result`. El payload que recibe trae `db_path`, `lote`, `job_id` y `ahora`.
  - Blueprint `linkedin_bp` con `POST /api/linkedin/generar`, `POST /api/linkedin/enviar`, `GET /api/linkedin/marcar`.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_linkedin_endpoints.py`:

```python
import json
import sqlite3
from unittest.mock import patch

import pytest

import dashboard
from database import (
    create_linkedin_post,
    get_linkedin_post_by_token,
    init_db,
    insert_business,
    seed_linkedin_temas,
)


@pytest.fixture
def app_y_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secreto")
    monkeypatch.setenv("LINKEDIN_MAIL_TO", "destino@test.com")
    db = str(tmp_path / "t.db")
    init_db(db)
    seed_linkedin_temas(db)
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    return app, db


def test_generar_sin_token_devuelve_401(app_y_db):
    app, _ = app_y_db
    r = app.test_client().post("/api/linkedin/generar")
    assert r.status_code == 401


def test_generar_encola_un_job_con_lote(app_y_db):
    app, db = app_y_db
    # El worker arranca junto con create_app y podria levantar el job durante
    # el test; con redactar mockeado no sale ninguna llamada a la API.
    with patch("services.linkedin_posts.redactar", return_value="t" * 400):
        r = app.test_client().post(
            "/api/linkedin/generar", headers={"x-admin-token": "secreto"}
        )
        assert r.status_code == 202
        cuerpo = r.get_json()

    assert cuerpo["lote"]

    conn = sqlite3.connect(db)
    fila = conn.execute(
        "SELECT type, payload FROM jobs WHERE id = ?", (cuerpo["job_id"],)
    ).fetchone()
    conn.close()

    assert fila[0] == "linkedin"
    # El lote viaja en el payload desde el INSERT, no se agrega despues.
    assert json.loads(fila[1])["lote"] == cuerpo["lote"]


def test_enviar_manda_el_mail_y_marca_enviados(app_y_db):
    app, db = app_y_db
    pid = create_linkedin_post(
        db, job_id=99, lote="lote-x", tipo="educativo", texto="t" * 300,
        angulo="concreto", fuente_tipo="tema", fuente_id=1,
        imagen_tipo="tarjeta", imagen_spec=json.dumps({"frase": "hola"}),
        fuente_desc="Tema educativo: Que es un CRM", aviso="",
        marcar_token="tok-x",
    )

    with patch("routes.linkedin.send_linkedin_drafts", return_value=True) as mail:
        r = app.test_client().post(
            "/api/linkedin/enviar",
            headers={"x-admin-token": "secreto"},
            json={"lote": "lote-x", "imagenes": [{"id": pid, "png_b64": "AAA"}]},
        )

    assert r.status_code == 200
    assert mail.call_args.args[0] == "destino@test.com"
    assert mail.call_args.args[1][0]["png_b64"] == "AAA"
    assert mail.call_args.args[1][0]["fuente_desc"] == "Tema educativo: Que es un CRM"

    conn = sqlite3.connect(db)
    estado = conn.execute(
        "SELECT estado FROM linkedin_posts WHERE id = ?", (pid,)
    ).fetchone()[0]
    conn.close()
    assert estado == "enviado"


def test_enviar_sin_borradores_devuelve_404(app_y_db):
    app, _ = app_y_db
    r = app.test_client().post(
        "/api/linkedin/enviar",
        headers={"x-admin-token": "secreto"},
        json={"lote": "no-existe", "imagenes": []},
    )
    assert r.status_code == 404


def test_enviar_sin_lote_devuelve_400(app_y_db):
    app, _ = app_y_db
    r = app.test_client().post(
        "/api/linkedin/enviar",
        headers={"x-admin-token": "secreto"},
        json={"imagenes": []},
    )
    assert r.status_code == 400


def test_marcar_publica_y_quema_el_token(app_y_db):
    app, db = app_y_db
    pid = create_linkedin_post(
        db, job_id=1, lote="lote-m", tipo="educativo", texto="t" * 300,
        angulo="concreto", fuente_tipo="tema", fuente_id=1,
        imagen_tipo="ninguna", imagen_spec="{}", marcar_token="tok-m",
    )

    r = app.test_client().get("/api/linkedin/marcar?token=tok-m")
    assert r.status_code == 200

    conn = sqlite3.connect(db)
    fila = conn.execute(
        "SELECT estado, publicado_en FROM linkedin_posts WHERE id = ?", (pid,)
    ).fetchone()
    conn.close()
    assert fila[0] == "publicado"
    assert fila[1] is not None
    assert get_linkedin_post_by_token(db, "tok-m") is None


def test_marcar_con_token_quemado_devuelve_410(app_y_db):
    app, _ = app_y_db
    r = app.test_client().get("/api/linkedin/marcar?token=no-existe")
    assert r.status_code == 410
```

Y agregar al final de `tests/test_linkedin_redaccion.py` los tests del handler:

```python
def test_handler_arma_dos_borradores(tmp_path):
    from database import init_db, insert_business, seed_linkedin_temas
    from services.linkedin_posts import linkedin_job_handler

    db = str(tmp_path / "t.db")
    init_db(db)
    seed_linkedin_temas(db)
    bid = insert_business(db, {"name": "Cliente Uno", "phone": "+598 99 777 001"})
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO demos (client_id, status, url, generated_at) "
        "VALUES (?, 'done', 'https://uno.vercel.app', ?)",
        (bid, "2026-08-25 10:00:00"),
    )
    conn.commit()
    conn.close()

    with patch("services.linkedin_posts.redactar", return_value=VALIDO):
        resultado = linkedin_job_handler({"db_path": db, "lote": "l-test", "ahora": "2026-08-26T09:00:00"})

    tipos = [b["tipo"] for b in resultado["borradores"]]
    assert sorted(tipos) == ["educativo", "trabajo"]
    assert all(b["marcar_token"] for b in resultado["borradores"])


def test_handler_sin_material_hace_dos_educativos(tmp_path):
    from database import init_db, seed_linkedin_temas
    from services.linkedin_posts import linkedin_job_handler

    db = str(tmp_path / "t.db")
    init_db(db)
    seed_linkedin_temas(db)

    with patch("services.linkedin_posts.redactar", return_value=VALIDO):
        resultado = linkedin_job_handler({"db_path": db, "lote": "l-test", "ahora": "2026-08-26T09:00:00"})

    assert [b["tipo"] for b in resultado["borradores"]] == ["educativo", "educativo"]
    assert resultado["borradores"][0]["fuente_id"] != resultado["borradores"][1]["fuente_id"]


def test_handler_manda_mail_de_fallo_si_no_valida_ninguno(tmp_path):
    from database import init_db, seed_linkedin_temas
    from services.linkedin_posts import linkedin_job_handler

    db = str(tmp_path / "t.db")
    init_db(db)
    seed_linkedin_temas(db)

    with patch("services.linkedin_posts.redactar", return_value=None), \
         patch("services.linkedin_posts.send_linkedin_failure") as fallo:
        resultado = linkedin_job_handler({"db_path": db, "lote": "l-test", "ahora": "2026-08-26T09:00:00"})

    assert resultado["borradores"] == []
    assert fallo.called
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `pytest tests/test_linkedin_endpoints.py -v`
Expected: FAIL con 404 en `/api/linkedin/generar` (el blueprint no existe todavía)

- [ ] **Step 3: Escribir el job handler**

Al final de `services/linkedin_posts.py`:

```python
import json
import secrets

from services.email_service import send_linkedin_failure


def _borrador_de_trabajo(db_path: str, job_id, lote: str, candidato: dict, angulo: str):
    texto = redactar(contexto_trabajo(candidato), angulo)
    if texto is None:
        return None

    imagen_tipo = "screenshot" if candidato.get("url") else "ninguna"
    imagen_spec = json.dumps({"url": candidato.get("url", "")})
    token = secrets.token_urlsafe(16)
    fuente_desc = f"{candidato['nombre']} ({candidato['que_paso']})"
    aviso = "" if candidato.get("linkedin_ok") else (
        "Confirma con el cliente antes de publicar: no esta marcado como "
        "autorizado para nombrarlo."
    )

    post_id = create_linkedin_post(
        db_path,
        job_id=job_id,
        lote=lote,
        tipo="trabajo",
        texto=texto,
        angulo=angulo,
        fuente_tipo=candidato["fuente_tipo"],
        fuente_id=candidato["fuente_id"],
        imagen_tipo=imagen_tipo,
        imagen_spec=imagen_spec,
        fuente_desc=fuente_desc,
        aviso=aviso,
        marcar_token=token,
    )

    return {
        "id": post_id,
        "tipo": "trabajo",
        "texto": texto,
        "fuente_desc": fuente_desc,
        "aviso": aviso,
        "marcar_token": token,
        "imagen_tipo": imagen_tipo,
        "imagen_spec": json.loads(imagen_spec),
        "fuente_id": candidato["fuente_id"],
    }


def _borrador_educativo(db_path: str, job_id, lote: str, tema: dict, angulo: str):
    texto = redactar(contexto_educativo(tema), angulo)
    if texto is None:
        return None

    imagen_spec = json.dumps({"frase": tema["titulo"]})
    token = secrets.token_urlsafe(16)
    fuente_desc = f"Tema educativo: {tema['titulo']}"

    post_id = create_linkedin_post(
        db_path,
        job_id=job_id,
        lote=lote,
        tipo="educativo",
        texto=texto,
        angulo=angulo,
        fuente_tipo="tema",
        fuente_id=tema["id"],
        imagen_tipo="tarjeta",
        imagen_spec=imagen_spec,
        fuente_desc=fuente_desc,
        aviso="",
        marcar_token=token,
    )
    return {
        "id": post_id,
        "tipo": "educativo",
        "texto": texto,
        "fuente_desc": fuente_desc,
        "aviso": "",
        "marcar_token": token,
        "imagen_tipo": "tarjeta",
        "imagen_spec": json.loads(imagen_spec),
        "fuente_id": tema["id"],
    }


def linkedin_job_handler(payload: dict) -> dict:
    """Handler del JobWorker. Genera los borradores y los guarda.

    No manda el mail: eso pasa en /api/linkedin/enviar, despues de que el
    runner de Actions renderice las imagenes. El `lote` viene armado desde el
    endpoint, asi que las filas nacen ya identificadas.
    """
    db_path = payload["db_path"]
    lote = payload["lote"]
    job_id = payload.get("job_id")
    ahora = datetime.fromisoformat(payload["ahora"])

    material = recolectar_material(db_path, ahora)
    borradores = []
    usados_ids = set()
    aviso_cooldown = False

    if material:
        b = _borrador_de_trabajo(db_path, job_id, lote, material[0], "concreto")
        if b:
            borradores.append(b)

    # El primer educativo toma el angulo que quedo libre: si ya hay un borrador
    # de trabajo (que salio "concreto"), el educativo va por "implicancia".
    hubo_trabajo = bool(borradores)
    faltan = 2 - len(borradores)
    for i in range(faltan):
        tema, en_cooldown = elegir_tema(db_path, ahora, usados_ids)
        aviso_cooldown = aviso_cooldown or en_cooldown
        usados_ids.add(tema["id"])

        if hubo_trabajo:
            angulo = "implicancia"
        else:
            angulo = "concreto" if i == 0 else "implicancia"

        b = _borrador_educativo(db_path, job_id, lote, tema, angulo)
        if b:
            marcar_tema_usado(db_path, tema["id"], ahora.isoformat())
            borradores.append(b)

    if not borradores:
        destino = os.environ.get("LINKEDIN_MAIL_TO", "scalerics@gmail.com")
        send_linkedin_failure(
            destino, "ningun borrador paso la validacion de las reglas de voz"
        )

    return {"lote": lote, "borradores": borradores, "aviso_cooldown": aviso_cooldown}
```

Agregar a los imports de arriba del archivo lo que falte:

```python
from database import (
    _connect,
    create_linkedin_post,
    get_fuentes_usadas,
    get_temas_disponibles,
    marcar_tema_usado,
)
```

- [ ] **Step 4: Escribir el blueprint**

Crear `routes/linkedin.py`:

```python
"""Endpoints de los borradores de LinkedIn.

La autenticacion la resuelve el before_request de dashboard.py: cualquier
request a /api/ con el header x-admin-token correcto pasa sin sesion. La
excepcion es /marcar, que se abre desde un link de un mail y por eso lleva
su token de un solo uso en la query.
"""

import json
import os
import secrets
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from database import (
    create_job,
    get_linkedin_post_by_token,
    get_linkedin_posts_by_lote,
    update_linkedin_post,
)
from services.email_service import send_linkedin_drafts
from services.job_service import get_worker

linkedin_bp = Blueprint("linkedin", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@linkedin_bp.route("/api/linkedin/generar", methods=["POST"])
def api_linkedin_generar():
    db_path = _db()
    worker = get_worker()
    if worker is None:
        return jsonify({"ok": False, "error": "worker no inicializado"}), 503

    # El lote se genera aca y viaja dentro del payload desde el principio. Si
    # se lo agregara despues con un UPDATE, el worker podria levantar el job
    # entre el INSERT y el UPDATE y quedarse sin el.
    lote = secrets.token_urlsafe(12)
    payload = {
        "db_path": db_path,
        "lote": lote,
        "ahora": datetime.now().isoformat(),
    }
    job_id = create_job(db_path, "linkedin", json.dumps(payload))

    return jsonify({"ok": True, "job_id": job_id, "lote": lote}), 202


@linkedin_bp.route("/api/linkedin/enviar", methods=["POST"])
def api_linkedin_enviar():
    data = request.get_json() or {}
    lote = data.get("lote")
    imagenes = {i["id"]: i.get("png_b64", "") for i in data.get("imagenes", [])}

    if not lote:
        return jsonify({"ok": False, "error": "lote requerido"}), 400

    posts = [p for p in get_linkedin_posts_by_lote(_db(), lote)
             if p["estado"] == "generado"]
    if not posts:
        return jsonify({"ok": False, "error": "no hay borradores para ese lote"}), 404

    borradores = [{
        "id": p["id"],
        "tipo": p["tipo"],
        "texto": p["texto"],
        "fuente_desc": p["fuente_desc"] or f"{p['fuente_tipo']} #{p['fuente_id']}",
        "aviso": p["aviso"] or "",
        "marcar_token": p["marcar_token"],
        "png_b64": imagenes.get(p["id"], ""),
    } for p in posts]

    destino = os.environ.get("LINKEDIN_MAIL_TO", "scalerics@gmail.com")
    base_url = os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev")
    ok = send_linkedin_drafts(destino, borradores, base_url,
                              aviso_cooldown=bool(data.get("aviso_cooldown")))

    for p in posts:
        update_linkedin_post(_db(), p["id"], estado="enviado")

    return jsonify({"ok": ok, "enviados": len(borradores)}), 200


@linkedin_bp.route("/api/linkedin/marcar", methods=["GET"])
def api_linkedin_marcar():
    token = request.args.get("token", "")
    post = get_linkedin_post_by_token(_db(), token)
    if post is None:
        return "Ese link ya se uso o no existe.", 410

    update_linkedin_post(
        _db(), post["id"],
        estado="publicado",
        publicado_en=datetime.now().isoformat(),
        marcar_token=None,
    )
    return "Listo, quedo marcado como publicado.", 200
```

- [ ] **Step 5: Registrar el blueprint y el handler**

En `dashboard.py`, agregar el import junto a los otros blueprints:

```python
from routes.linkedin import linkedin_bp
```

y sumarlo a la tupla de `create_app` (línea ~4889):

```python
    for bp in (leads_bp, demos_bp, calendar_bp, wa_bp, pipeline_bp, tasks_bp,
               budgets_bp, tokens_bp, calendly_bp, linkedin_bp):
```

Agregar el import del handler junto a `demo_job_handler`:

```python
from services.linkedin_posts import linkedin_job_handler
```

y registrarlo en el worker (línea ~5986, justo después del `register("demo", ...)`):

```python
    worker.register("linkedin", linkedin_job_handler)
```

Además, agregar `/api/linkedin/marcar` a las excepciones del `before_request`,
justo debajo de la línea del webhook de Calendly:

```python
        if request.path.startswith("/api/linkedin/marcar"):
            return
```

- [ ] **Step 6: Correr los tests**

Run: `pytest tests/test_linkedin_endpoints.py tests/test_linkedin_redaccion.py -v`
Expected: PASS

Run: `pytest -q`
Expected: sin regresiones

- [ ] **Step 7: Commit**

```bash
git add services/linkedin_posts.py routes/linkedin.py dashboard.py tests/test_linkedin_endpoints.py tests/test_linkedin_redaccion.py
git commit -m "feat(linkedin): job handler y endpoints generar/enviar/marcar"
```

---

### Task 7: Tarjeta de marca y renderizador

**Files:**
- Create: `templates/linkedin_card.html`
- Create: `scripts/render_linkedin.py`
- Test: `tests/test_render_linkedin.py`

**Interfaces:**
- Produces:
  - `armar_tarjeta(plantilla_html: str, frase: str) -> str` — sustituye el marcador.
  - `renderizar(borradores: list[dict], plantilla_html: str, page) -> list[dict]` — devuelve `[{"id": ..., "png_b64": ...}]`. `page` es una página de Playwright ya abierta; en los tests va un doble.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_render_linkedin.py`:

```python
import base64

from scripts.render_linkedin import armar_tarjeta, renderizar


PLANTILLA = '<html><body><h1>{{FRASE}}</h1></body></html>'


class PageFalsa:
    """Doble de una pagina de Playwright: registra que se le pidio."""

    def __init__(self, falla_en=()):
        self.navegaciones = []
        self.contenidos = []
        self.falla_en = falla_en

    def goto(self, url, wait_until=None, timeout=None):
        if url in self.falla_en:
            raise RuntimeError("no cargo")
        self.navegaciones.append(url)

    def set_content(self, html, wait_until=None):
        self.contenidos.append(html)

    def screenshot(self):
        return b"PNGFALSO"


def test_armar_tarjeta_sustituye_la_frase():
    html = armar_tarjeta(PLANTILLA, "Que es un CRM")
    assert "Que es un CRM" in html
    assert "{{FRASE}}" not in html


def test_armar_tarjeta_escapa_el_html():
    html = armar_tarjeta(PLANTILLA, "Precios <script>alert(1)</script>")
    assert "<script>" not in html


def test_renderiza_una_tarjeta():
    page = PageFalsa()
    borradores = [{"id": 1, "imagen_tipo": "tarjeta", "imagen_spec": {"frase": "Hola"}}]

    resultado = renderizar(borradores, PLANTILLA, page)

    assert resultado == [{"id": 1, "png_b64": base64.b64encode(b"PNGFALSO").decode()}]
    assert "Hola" in page.contenidos[0]


def test_renderiza_un_screenshot():
    page = PageFalsa()
    borradores = [{
        "id": 2, "imagen_tipo": "screenshot",
        "imagen_spec": {"url": "https://demo.test"},
    }]

    resultado = renderizar(borradores, PLANTILLA, page)

    assert page.navegaciones == ["https://demo.test"]
    assert resultado[0]["id"] == 2


def test_imagen_ninguna_no_produce_png():
    page = PageFalsa()
    borradores = [{"id": 3, "imagen_tipo": "ninguna", "imagen_spec": {}}]
    assert renderizar(borradores, PLANTILLA, page) == []


def test_una_falla_de_render_no_tumba_al_resto():
    page = PageFalsa(falla_en={"https://rota.test"})
    borradores = [
        {"id": 4, "imagen_tipo": "screenshot", "imagen_spec": {"url": "https://rota.test"}},
        {"id": 5, "imagen_tipo": "tarjeta", "imagen_spec": {"frase": "Sigue"}},
    ]

    resultado = renderizar(borradores, PLANTILLA, page)

    assert [r["id"] for r in resultado] == [5]
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `pytest tests/test_render_linkedin.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'scripts.render_linkedin'`

- [ ] **Step 3: Escribir la plantilla de la tarjeta**

Crear `templates/linkedin_card.html`:

```html
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=Raleway:wght@700&display=swap" rel="stylesheet">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    width: 1200px; height: 627px;
    background: #09090f;
    font-family: 'Inter', sans-serif;
    color: #e2e8f0;
    display: flex; flex-direction: column; justify-content: center;
    padding: 0 90px;
    position: relative;
    overflow: hidden;
  }
  .glow {
    position: absolute; top: -180px; right: -120px;
    width: 620px; height: 620px; border-radius: 50%;
    background: radial-gradient(circle, rgba(0,136,204,.20) 0%, rgba(0,136,204,0) 68%);
  }
  .kicker {
    font-size: 15px; letter-spacing: .22em; text-transform: uppercase;
    color: #0088CC; margin-bottom: 30px; font-weight: 600;
  }
  h1 {
    font-family: 'Raleway', sans-serif; font-weight: 700;
    font-size: 62px; line-height: 1.13; letter-spacing: -.015em;
    max-width: 930px;
  }
  .rule { width: 84px; height: 3px; background: #0088CC; margin-top: 38px; }
  .pie {
    position: absolute; left: 90px; bottom: 54px;
    display: flex; align-items: center; gap: 14px;
    font-size: 17px; color: #64748b; letter-spacing: .04em;
  }
  .punto { width: 9px; height: 9px; border-radius: 50%; background: #10B981; }
</style>
</head>
<body>
  <div class="glow"></div>
  <div class="kicker">Scalerics</div>
  <h1>{{FRASE}}</h1>
  <div class="rule"></div>
  <div class="pie"><span class="punto"></span> scalerics.com</div>
</body>
</html>
```

- [ ] **Step 4: Escribir el renderizador**

Crear `scripts/render_linkedin.py`:

```python
"""Renderiza las imagenes de los borradores de LinkedIn.

Corre en el runner de GitHub Actions, no en Fly: la imagen de Fly instala el
paquete pip de Playwright pero no los binarios de los navegadores, y la VM
tiene 256 MB.

Uso:
    python scripts/render_linkedin.py --crm https://scalerics-crm.fly.dev \\
        --token "$ADMIN_TOKEN" --job-id 123
"""

import argparse
import base64
import html as html_mod
import json
import os
import sys
import time

import requests

ANCHO = 1200
ALTO = 627
PLANTILLA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates", "linkedin_card.html",
)


def armar_tarjeta(plantilla_html: str, frase: str) -> str:
    return plantilla_html.replace("{{FRASE}}", html_mod.escape(frase))


def renderizar(borradores: list, plantilla_html: str, page) -> list:
    """Un PNG en base64 por borrador que tenga imagen.

    Una falla de render deja a ese borrador sin imagen y sigue con el resto:
    un mail sin foto sirve, un mail que no llega no.
    """
    salida = []
    for b in borradores:
        tipo = b.get("imagen_tipo", "ninguna")
        spec = b.get("imagen_spec") or {}
        if tipo == "ninguna":
            continue

        try:
            if tipo == "screenshot":
                url = spec.get("url", "")
                if not url:
                    continue
                page.goto(url, wait_until="networkidle", timeout=30000)
            else:
                page.set_content(armar_tarjeta(plantilla_html, spec.get("frase", "")),
                                 wait_until="networkidle")
            png = page.screenshot()
        except Exception as e:  # noqa: BLE001 - queremos seguir con los demas
            print(f"no se pudo renderizar el borrador {b.get('id')}: {e}", file=sys.stderr)
            continue

        salida.append({"id": b["id"], "png_b64": base64.b64encode(png).decode()})

    return salida


def esperar_job(crm: str, token: str, job_id: int, timeout_s: int = 300) -> dict:
    headers = {"x-admin-token": token}
    limite = time.time() + timeout_s
    while time.time() < limite:
        r = requests.get(f"{crm}/api/jobs/{job_id}", headers=headers, timeout=30)
        r.raise_for_status()
        job = r.json()
        if job.get("status") == "completed":
            return json.loads(job.get("result") or "{}")
        if job.get("status") == "failed":
            raise RuntimeError(f"el job fallo: {job.get('error_message')}")
        time.sleep(10)
    raise TimeoutError(f"el job {job_id} no termino en {timeout_s}s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crm", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--job-id", type=int, required=True)
    args = parser.parse_args()

    resultado = esperar_job(args.crm, args.token, args.job_id)
    borradores = resultado.get("borradores", [])
    if not borradores:
        print("el job no dejo borradores; no hay nada que renderizar")
        return 0

    with open(PLANTILLA_PATH, encoding="utf-8") as f:
        plantilla = f.read()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": ANCHO, "height": ALTO},
                                device_scale_factor=1)
        imagenes = renderizar(borradores, plantilla, page)
        browser.close()

    r = requests.post(
        f"{args.crm}/api/linkedin/enviar",
        headers={"x-admin-token": args.token},
        json={
            "lote": resultado["lote"],
            "imagenes": imagenes,
            "aviso_cooldown": resultado.get("aviso_cooldown", False),
        },
        timeout=120,
    )
    r.raise_for_status()
    print(f"mail enviado: {r.json()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Verificar que `scripts/__init__.py` existe (ya está en el repo) para que el
import `from scripts.render_linkedin import ...` funcione desde los tests.

- [ ] **Step 5: Correr los tests**

Run: `pytest tests/test_render_linkedin.py -v`
Expected: PASS, 6 tests

- [ ] **Step 6: Verificar la tarjeta a ojo**

```bash
python -c "
from playwright.sync_api import sync_playwright
from scripts.render_linkedin import armar_tarjeta
html = armar_tarjeta(open('templates/linkedin_card.html', encoding='utf-8').read(),
                     'Por que tu negocio no aparece en Google Maps')
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width':1200,'height':627})
    pg.set_content(html, wait_until='networkidle')
    pg.screenshot(path='tarjeta-prueba.png')
    b.close()
print('escrita tarjeta-prueba.png')
"
```

Abrir `tarjeta-prueba.png` y confirmar que el texto entra sin cortarse con una
frase larga y con una corta. Borrar el archivo después: `rm tarjeta-prueba.png`.

- [ ] **Step 7: Commit**

```bash
git add templates/linkedin_card.html scripts/render_linkedin.py tests/test_render_linkedin.py
git commit -m "feat(linkedin): tarjeta de marca y renderizador de imagenes con Playwright"
```

---

### Task 8: Cron de GitHub Actions y puesta en marcha

**Files:**
- Create: `.github/workflows/linkedin.yml`
- Create: `docs/linkedin-puesta-en-marcha.md`

**Interfaces:**
- Consumes: `POST /api/linkedin/generar`, `scripts/render_linkedin.py` (Tasks 6-7).
- Produces: nada que consuma otro task. Es el último.

- [ ] **Step 1: Escribir el workflow**

Crear `.github/workflows/linkedin.yml`:

```yaml
name: Borradores de LinkedIn

on:
  schedule:
    # 11:00 UTC = 08:00 en Montevideo (UTC-3). Martes y viernes.
    - cron: "0 11 * * 2,5"
  workflow_dispatch:

jobs:
  borradores:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Instalar dependencias
        run: |
          pip install requests playwright
          playwright install --with-deps chromium

      - name: Pedirle los borradores al CRM
        id: generar
        env:
          CRM: ${{ secrets.CRM_URL }}
          TOKEN: ${{ secrets.ADMIN_TOKEN }}
        run: |
          set -euo pipefail
          RESP=$(curl -sS -f -X POST "$CRM/api/linkedin/generar" \
            -H "x-admin-token: $TOKEN")
          echo "respuesta: $RESP"
          JOB_ID=$(echo "$RESP" | python -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
          echo "job_id=$JOB_ID" >> "$GITHUB_OUTPUT"

      - name: Renderizar imagenes y mandar el mail
        env:
          CRM: ${{ secrets.CRM_URL }}
          TOKEN: ${{ secrets.ADMIN_TOKEN }}
        run: |
          python scripts/render_linkedin.py \
            --crm "$CRM" \
            --token "$TOKEN" \
            --job-id "${{ steps.generar.outputs.job_id }}"
```

- [ ] **Step 2: Escribir el documento de puesta en marcha**

Crear `docs/linkedin-puesta-en-marcha.md`:

```markdown
# Borradores de LinkedIn — puesta en marcha

Qué hay que hacer una sola vez para que esto empiece a funcionar.

## 1. Secrets en Fly

```bash
flyctl secrets set ADMIN_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" -a scalerics-crm
flyctl secrets set LINKEDIN_MAIL_TO="scalerics@gmail.com" -a scalerics-crm
```

`ANTHROPIC_API_KEY`, `RESEND_API_KEY` y `CRM_URL` ya están seteados.

Si `flyctl secrets set` rebota con *"We need your payment information to
continue"*, es el 403 de organización: la app vive en la org `scalerics` y hay
que cargar la tarjeta en `fly.io/dashboard/scalerics/billing`. No es un problema
de login.

## 2. Secrets en GitHub

En Settings → Secrets and variables → Actions del repo:

- `ADMIN_TOKEN` — el mismo valor que se seteó en Fly.
- `CRM_URL` — `https://scalerics-crm.fly.dev`

## 3. Primera corrida a mano

Actions → "Borradores de LinkedIn" → Run workflow. Tiene que llegar el mail a
`scalerics@gmail.com` en menos de 5 minutos.

## 4. Autorizar clientes

Mientras un cliente tenga `linkedin_ok = 0`, sus borradores llegan con el aviso
de "confirmá antes de publicar". Para autorizarlo:

```sql
UPDATE businesses SET linkedin_ok = 1 WHERE id = <id>;
```

## Cuando deje de llegar el mail

En este orden:

1. **¿GitHub desactivó el cron?** Apaga los workflows programados tras 60 días
   sin commits en el repo. Se ve en la pestaña Actions y se reactiva con un
   botón.
2. **¿El workflow falló?** GitHub manda el mail del fallo. El log dice en qué
   paso.
3. **¿401?** `ADMIN_TOKEN` no coincide entre Fly y GitHub.
4. **¿Llegó el mail de "No salieron los borradores"?** El modelo no logró un
   texto que pasara la validación dos veces seguidas. Mirar los logs de Fly:
   `flyctl logs -a scalerics-crm | grep -i "Borrador rechazado"`.
5. **¿Se acabaron los temas?** El mail avisa cuando reusa uno antes de los 180
   días. Sembrar más en `seed_linkedin_temas()` de `database.py`.
```

- [ ] **Step 3: Verificar que el YAML parsea**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/linkedin.yml')); print('ok')"`
Expected: `ok`

(Si `yaml` no está instalado: `pip install pyyaml`.)

- [ ] **Step 4: Correr la suite entera**

Run: `pytest -q`
Expected: PASS, sin regresiones

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/linkedin.yml docs/linkedin-puesta-en-marcha.md
git commit -m "feat(linkedin): cron de GitHub Actions y documento de puesta en marcha"
```

---

## Después del plan

El pipeline no se puede probar de punta a punta sin los secrets de la puesta en
marcha, y esos los carga Juan (nunca ingresar datos de facturación por él). El
orden es: mergear la rama, deployar a Fly, cargar los secrets, y recién ahí
correr el workflow a mano una vez y mirar el mail que llega.
