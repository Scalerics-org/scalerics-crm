# Secuencia de recordatorios a leads de Meta — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que cada lead de Meta reciba hasta 7 recordatorios repartidos en un año en vez de uno solo, sin perder la garantía de que nadie recibe dos veces el mismo contacto.

**Architecture:** `meta_reminders` pasa de una fila por lead a una fila por *contacto*, con `UNIQUE(business_id, numero)` en lugar de `UNIQUE(business_id)`. La elegibilidad se calcula contra el **primer** envío del lead, no contra el anterior, para que el atraso de una tanda no se acumule. `enviar_recordatorios` arma la tanda con los seguimientos primero y completa con contactos nuevos.

**Tech Stack:** Python 3, SQLite, Flask, Resend. Tests con pytest.

## Global Constraints

- Días desde el **primer** envío de cada lead: `[0, 10, 25, 115, 205, 295, 365]`. Son 7 contactos y el séptimo es el último de la vida de ese lead.
- Tope de **15 mails por 24 horas rodantes**, compartido entre seguimientos y contactos nuevos.
- **Los seguimientos van antes** que los contactos nuevos cuando compiten por el cupo.
- La baja aplica a **toda la secuencia**: cualquier fila con `unsubscribed_at` saca al lead para siempre.
- Salir de `crm_status='sin_contactar'` saca al lead.
- Fechas en `"%Y-%m-%d %H:%M:%S"` UTC naive (`_ahora()` en `services/meta_reminders.py`). Nunca `isoformat()`.
- Todo valor que venga del formulario de Meta se escapa con `html.escape` antes de ir al HTML.
- Sin emojis. Los textos van en español rioplatense, con voseo y con tildes.
- Cada número manda un texto distinto. Repetir el mismo párrafo comercial cada trimestre es lo que hace que alguien marque spam.
- La suite completa se corre con `python -m pytest tests/ -q --ignore=tests/test_main.py` y hoy da **271 passed**. No puede bajar.

---

### Task 1: Migrar `meta_reminders` a una fila por contacto

**Files:**
- Modify: `database.py:378-390` (bloque `meta_reminders` dentro de `init_db`)
- Test: `tests/test_database.py`

**Interfaces:**
- Produces: tabla `meta_reminders` con columnas `id, business_id, numero, token, sent_at, unsubscribed_at` y `UNIQUE(business_id, numero)`.

SQLite no permite quitar un `UNIQUE` declarado en la tabla: hay que reconstruirla. En producción esa tabla ya tiene 15 filas reales (la primera tanda del 18-8-2026) y **cada una tiene un token que está publicado dentro de un mail que ya recibió una persona**. Si se pierde el token, el link de baja de ese mail deja de funcionar.

- [ ] **Step 1: Escribir el test que falla**

En `tests/test_database.py`:

```python
def test_migracion_de_meta_reminders_conserva_las_filas_viejas(tmp_path):
    """La tabla vieja tenia UNIQUE(business_id) y una fila por lead. Las 15
    filas de produccion tienen tokens publicados dentro de mails ya enviados:
    si se pierden, el link de baja de esos mails deja de funcionar."""
    import sqlite3
    from database import init_db

    ruta = str(tmp_path / "viejo.db")
    conn = sqlite3.connect(ruta)
    conn.execute("""
        CREATE TABLE meta_reminders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id     INTEGER NOT NULL UNIQUE,
            token           TEXT NOT NULL UNIQUE,
            sent_at         TEXT NOT NULL,
            unsubscribed_at TEXT
        )""")
    conn.execute(
        "INSERT INTO meta_reminders (business_id, token, sent_at, unsubscribed_at) "
        "VALUES (?,?,?,?)", (650, "tok-uno", "2026-08-18 14:41:18", None))
    conn.execute(
        "INSERT INTO meta_reminders (business_id, token, sent_at, unsubscribed_at) "
        "VALUES (?,?,?,?)", (651, "tok-dos", "2026-08-18 14:41:19", "2026-08-18 15:00:00"))
    conn.commit()
    conn.close()

    init_db(ruta)

    conn = sqlite3.connect(ruta)
    try:
        filas = conn.execute(
            "SELECT business_id, numero, token, sent_at, unsubscribed_at "
            "FROM meta_reminders ORDER BY business_id").fetchall()
        cols = [c[1] for c in conn.execute("PRAGMA table_info(meta_reminders)")]
    finally:
        conn.close()

    assert filas == [
        (650, 1, "tok-uno", "2026-08-18 14:41:18", None),
        (651, 1, "tok-dos", "2026-08-18 14:41:19", "2026-08-18 15:00:00"),
    ], "las filas viejas son el contacto 1 y conservan su token"
    assert "numero" in cols


def test_el_mismo_lead_puede_tener_varios_contactos(tmp_path):
    import sqlite3
    import pytest
    from database import init_db

    ruta = str(tmp_path / "nuevo.db")
    init_db(ruta)

    conn = sqlite3.connect(ruta)
    try:
        conn.execute("INSERT INTO meta_reminders (business_id, numero, token, sent_at) "
                     "VALUES (?,?,?,?)", (10, 1, "t1", "2026-08-01 10:00:00"))
        conn.execute("INSERT INTO meta_reminders (business_id, numero, token, sent_at) "
                     "VALUES (?,?,?,?)", (10, 2, "t2", "2026-08-11 10:00:00"))
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO meta_reminders (business_id, numero, token, sent_at) "
                         "VALUES (?,?,?,?)", (10, 2, "t3", "2026-08-12 10:00:00"))
    finally:
        conn.close()
```

- [ ] **Step 2: Correr los tests y ver que fallan por la razón correcta**

Run: `python -m pytest tests/test_database.py -k "meta_reminders or varios_contactos" -v`
Expected: FAIL con `sqlite3.OperationalError: no such column: numero` en el primero, y en el segundo que el segundo INSERT falle por el `UNIQUE(business_id)` viejo.

- [ ] **Step 3: Implementar la migración**

En `database.py`, reemplazar el bloque que hoy crea `meta_reminders` por:

```python
        # ── meta_reminders ────────────────────────────────────────────────────
        # Una fila por CONTACTO, no por lead. El UNIQUE es (business_id, numero):
        # sigue siendo la base la que impide mandar dos veces el mismo contacto,
        # no una condicion en el codigo.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_reminders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id     INTEGER NOT NULL,
                numero          INTEGER NOT NULL DEFAULT 1,
                token           TEXT NOT NULL UNIQUE,
                sent_at         TEXT NOT NULL,
                unsubscribed_at TEXT,
                UNIQUE (business_id, numero)
            )
        """)

        # Migracion de la tabla vieja, que tenia UNIQUE(business_id) y una sola
        # fila por lead. SQLite no deja quitar un UNIQUE: hay que reconstruir.
        # Las filas viejas son el contacto 1 y conservan su token, que esta
        # publicado dentro de mails que la gente ya recibio.
        columnas = [c[1] for c in conn.execute("PRAGMA table_info(meta_reminders)")]
        if "numero" not in columnas:
            conn.execute("""
                CREATE TABLE meta_reminders_nueva (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_id     INTEGER NOT NULL,
                    numero          INTEGER NOT NULL DEFAULT 1,
                    token           TEXT NOT NULL UNIQUE,
                    sent_at         TEXT NOT NULL,
                    unsubscribed_at TEXT,
                    UNIQUE (business_id, numero)
                )
            """)
            conn.execute("""
                INSERT INTO meta_reminders_nueva
                       (id, business_id, numero, token, sent_at, unsubscribed_at)
                SELECT  id, business_id, 1,      token, sent_at, unsubscribed_at
                  FROM meta_reminders
            """)
            conn.execute("DROP TABLE meta_reminders")
            conn.execute("ALTER TABLE meta_reminders_nueva RENAME TO meta_reminders")
```

- [ ] **Step 4: Correr los tests y ver que pasan**

Run: `python -m pytest tests/test_database.py -q`
Expected: PASS.

- [ ] **Step 5: Correr la suite completa**

Run: `python -m pytest tests/ -q --ignore=tests/test_main.py`
Expected: 271 passed o más. Si algún test existente de `meta_reminders` falla, es porque asumía el `UNIQUE(business_id)`: arreglarlo forma parte de esta tarea, no de la siguiente.

- [ ] **Step 6: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat(recordatorios): meta_reminders pasa a una fila por contacto"
```

---

### Task 2: `registrar_envio` con número, y la baja sobre toda la secuencia

**Files:**
- Modify: `services/meta_reminders.py:50-105`
- Test: `tests/test_meta_reminders.py`

**Interfaces:**
- Consumes: la tabla de la Task 1.
- Produces:
  - `registrar_envio(db_path: str, business_id: int, numero: int) -> str`
  - `esta_dado_de_baja(db_path: str, business_id: int) -> bool` — True si **cualquier** fila de ese lead tiene `unsubscribed_at`.
  - `contactos_enviados(db_path: str, business_id: int) -> int`

- [ ] **Step 1: Escribir los tests que fallan**

En `tests/test_meta_reminders.py`:

```python
def test_registrar_envio_numera_los_contactos(db):
    from services.meta_reminders import registrar_envio, contactos_enviados

    t1 = registrar_envio(db, 30, 1)
    t2 = registrar_envio(db, 30, 2)

    assert t1 != t2, "cada contacto lleva su propio token"
    assert contactos_enviados(db, 30) == 2


def test_no_se_puede_mandar_dos_veces_el_mismo_contacto(db):
    import sqlite3
    import pytest
    from services.meta_reminders import registrar_envio

    registrar_envio(db, 31, 1)

    with pytest.raises(sqlite3.IntegrityError):
        registrar_envio(db, 31, 1)


def test_la_baja_en_un_contacto_vale_para_toda_la_secuencia(db):
    from services.meta_reminders import registrar_envio, dar_de_baja, esta_dado_de_baja

    registrar_envio(db, 32, 1)
    token2 = registrar_envio(db, 32, 2)

    assert esta_dado_de_baja(db, 32) is False
    assert dar_de_baja(db, token2) is True
    assert esta_dado_de_baja(db, 32) is True, (
        "se dio de baja en el contacto 2: no puede recibir el 3 ni los trimestrales")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_meta_reminders.py -k "numera or dos_veces_el_mismo or toda_la_secuencia" -v`
Expected: FAIL con `TypeError` (a `registrar_envio` le sobra un argumento) y `ImportError` de `contactos_enviados`.

- [ ] **Step 3: Implementar**

En `services/meta_reminders.py`, reemplazar `registrar_envio` y `esta_dado_de_baja`:

```python
def registrar_envio(db_path: str, business_id: int, numero: int) -> str:
    """Deja constancia del contacto `numero` y devuelve su token de baja.

    Lanza sqlite3.IntegrityError si ese lead ya recibio ese contacto: es la red
    que impide mandar dos veces, y tiene que fallar ruidosamente. Cada contacto
    lleva su propio token, asi que el link de baja de cada mail funciona por
    separado.
    """
    token = secrets.token_urlsafe(24)
    ahora = _ahora()
    conn = _conn(db_path)
    try:
        conn.execute(
            "INSERT INTO meta_reminders (business_id, numero, token, sent_at) "
            "VALUES (?, ?, ?, ?)",
            (business_id, int(numero), token, ahora),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def contactos_enviados(db_path: str, business_id: int) -> int:
    """Cuantos contactos de la secuencia ya recibio este lead."""
    conn = _conn(db_path)
    try:
        (cuantos,) = conn.execute(
            "SELECT COUNT(*) FROM meta_reminders WHERE business_id = ?",
            (business_id,),
        ).fetchone()
    finally:
        conn.close()
    return int(cuantos or 0)


def esta_dado_de_baja(db_path: str, business_id: int) -> bool:
    """True si el lead pidio no recibir mas, en CUALQUIER contacto.

    La baja es sobre la persona, no sobre el mail que la disparo: quien se da de
    baja en el contacto 2 no puede recibir el 3 ni ningun trimestral.
    """
    conn = _conn(db_path)
    try:
        fila = conn.execute(
            "SELECT 1 FROM meta_reminders "
            "WHERE business_id = ? AND unsubscribed_at IS NOT NULL LIMIT 1",
            (business_id,),
        ).fetchone()
    finally:
        conn.close()
    return bool(fila)
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_meta_reminders.py -q`
Expected: los tres nuevos pasan. Los que llaman `registrar_envio(db, id)` con dos argumentos ahora fallan: actualizarlos a `registrar_envio(db, id, 1)` forma parte de esta tarea.

- [ ] **Step 5: Correr la suite completa y commitear**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
git add services/meta_reminders.py tests/test_meta_reminders.py
git commit -m "feat(recordatorios): registrar_envio numera el contacto y la baja vale para toda la secuencia"
```

---

### Task 3: Elegir a quién le toca un seguimiento

**Files:**
- Modify: `services/meta_reminders.py` (constantes arriba del archivo, y función nueva junto a `leads_a_recordar`)
- Test: `tests/test_meta_reminders.py`

**Interfaces:**
- Consumes: `contactos_enviados`, la tabla con `numero`.
- Produces: `leads_a_seguir(db_path: str, limite: int = _TOPE_DIARIO) -> list[dict]`, cada dict con las mismas claves que devuelve `leads_a_recordar` (`id`, `name`, `email`, `negocio`, `rubro`) **más** `numero: int`, que es el contacto que corresponde mandar ahora.

- [ ] **Step 1: Escribir los tests que fallan**

```python
def _envio(conn, bid, numero, dias_atras):
    from datetime import datetime, timedelta, timezone
    cuando = (datetime.now(timezone.utc) - timedelta(days=dias_atras)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO meta_reminders (business_id, numero, token, sent_at) "
                 "VALUES (?,?,?,?)", (bid, numero, f"tok-{bid}-{numero}", cuando))


def test_el_seguimiento_espera_los_dias_que_corresponden(db):
    """El contacto 2 va a los 10 dias del primero, no antes."""
    from services.meta_reminders import leads_a_seguir

    conn = sqlite3.connect(db)
    _lead(conn, 40, dias=30)
    _envio(conn, 40, 1, dias_atras=9)
    _lead(conn, 41, dias=30)
    _envio(conn, 41, 1, dias_atras=10)
    conn.commit()
    conn.close()

    elegidos = leads_a_seguir(db)

    assert [x["id"] for x in elegidos] == [41]
    assert elegidos[0]["numero"] == 2


def test_los_trimestrales_se_cuentan_desde_el_primer_envio(db):
    """El ancla es el primer contacto, no el anterior: asi el atraso de una
    tanda no se acumula sobre los siguientes."""
    from services.meta_reminders import leads_a_seguir

    conn = sqlite3.connect(db)
    _lead(conn, 42, dias=200)
    _envio(conn, 42, 1, dias_atras=120)
    _envio(conn, 42, 2, dias_atras=110)
    _envio(conn, 42, 3, dias_atras=95)
    conn.commit()
    conn.close()

    elegidos = leads_a_seguir(db)

    assert [x["id"] for x in elegidos] == [42], "el 4 va a los 115 dias del primero"
    assert elegidos[0]["numero"] == 4


def test_el_septimo_es_el_ultimo_de_la_vida(db):
    from services.meta_reminders import leads_a_seguir

    conn = sqlite3.connect(db)
    _lead(conn, 43, dias=500)
    for n, dias in ((1, 400), (2, 390), (3, 375), (4, 285), (5, 195), (6, 105), (7, 35)):
        _envio(conn, 43, n, dias_atras=dias)
    conn.commit()
    conn.close()

    assert leads_a_seguir(db) == [], "despues del 7 no vuelve a entrar nunca"


def test_el_que_se_dio_de_baja_no_recibe_el_siguiente(db):
    from services.meta_reminders import leads_a_seguir

    conn = sqlite3.connect(db)
    _lead(conn, 44, dias=30)
    _envio(conn, 44, 1, dias_atras=20)
    conn.execute("UPDATE meta_reminders SET unsubscribed_at = ? WHERE business_id = ?",
                 ("2026-08-18 10:00:00", 44))
    conn.commit()
    conn.close()

    assert leads_a_seguir(db) == []


def test_el_que_dejo_de_estar_sin_contactar_no_recibe_el_siguiente(db):
    """Es la unica senal de corte cuando alguien contesta el mail."""
    from services.meta_reminders import leads_a_seguir

    conn = sqlite3.connect(db)
    _lead(conn, 45, dias=30, crm_status="interesado")
    _envio(conn, 45, 1, dias_atras=20)
    conn.commit()
    conn.close()

    assert leads_a_seguir(db) == []
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_meta_reminders.py -k seguir -v`
Expected: FAIL con `ImportError: cannot import name 'leads_a_seguir'`.

- [ ] **Step 3: Implementar**

Arriba del archivo, junto a las otras constantes:

```python
# Dias desde el PRIMER envio de cada lead. El ancla es el primer contacto y no
# el anterior a proposito: asi el atraso de una tanda no se acumula sobre los
# que siguen. Son 7 y el septimo es el ultimo de la vida de ese lead.
_DIAS_DE_CADA_CONTACTO = [0, 10, 25, 115, 205, 295, 365]
_TOTAL_CONTACTOS = len(_DIAS_DE_CADA_CONTACTO)
```

Y la función, junto a `leads_a_recordar`:

```python
def leads_a_seguir(db_path: str, limite: int = _TOPE_DIARIO) -> list[dict]:
    """Leads que ya recibieron algun contacto y a los que hoy les toca el siguiente.

    No hace falta deduplicar por mail como en `leads_a_recordar`: para tener
    fila, el lead ya paso por ese filtro, asi que hay una sola por direccion.

    El salto que corresponde depende de cuantos contactos lleva, asi que el CASE
    se arma desde `_DIAS_DE_CADA_CONTACTO` para que la tabla de dias tenga un
    solo lugar de verdad.
    """
    casos = " ".join(
        f"WHEN {n} THEN {_DIAS_DE_CADA_CONTACTO[n]}"
        for n in range(1, _TOTAL_CONTACTOS)
    )
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        filas = conn.execute(
            f"""
            SELECT b.id, b.name, TRIM(b.email) AS email, b.form_data,
                   COUNT(r.id) AS enviados,
                   MIN(r.sent_at) AS primer_envio
              FROM businesses b
              JOIN meta_reminders r ON r.business_id = b.id
             WHERE b.source = 'meta'
               AND b.crm_status = 'sin_contactar'
               AND b.email IS NOT NULL AND LENGTH(TRIM(b.email)) > 3
               AND NOT EXISTS (
                     SELECT 1 FROM meta_reminders u
                      WHERE u.business_id = b.id AND u.unsubscribed_at IS NOT NULL
                   )
          GROUP BY b.id
            HAVING enviados < {_TOTAL_CONTACTOS}
               AND primer_envio <= datetime('now', '-' || (CASE enviados {casos} END) || ' days')
          ORDER BY primer_envio ASC
             LIMIT ?
            """,
            (int(limite),),
        ).fetchall()
    finally:
        conn.close()

    salida = []
    for f in filas:
        try:
            campos = json.loads(f["form_data"] or "{}")
        except (ValueError, TypeError):
            campos = {}
        if not isinstance(campos, dict):
            campos = {}
        salida.append({
            "id": f["id"],
            "name": f["name"] or "",
            "email": f["email"],
            "negocio": _texto(campos, CLAVE_NEGOCIO),
            "rubro": _texto(campos, CLAVE_RUBRO),
            "numero": int(f["enviados"]) + 1,
        })
    return salida
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_meta_reminders.py -q`
Expected: PASS.

Si el helper `_lead` que ya existe en ese archivo no acepta `crm_status` como kwarg, revisar: lo acepta vía `**kw`. No agregar otro helper.

- [ ] **Step 5: Suite completa y commit**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
git add services/meta_reminders.py tests/test_meta_reminders.py
git commit -m "feat(recordatorios): elegir a quien le toca cada contacto de la secuencia"
```

---

### Task 4: Un texto por contacto

**Files:**
- Modify: `services/email_service.py` (función `send_meta_lead_reminder` y constantes de textos)
- Test: `tests/test_meta_reminder_email.py`

**Interfaces:**
- Produces: `send_meta_lead_reminder(to_email: str, negocio: str, rubro: str, unsub_url: str, numero: int = 1) -> str` — devuelve el tri-estado `"ok"`/`"fallo"`/`"desconocido"`.

**Ojo con la firma:** hoy es `send_meta_lead_reminder(to_email, lead_name, negocio, rubro, unsub_url)` y `lead_name` **ya no se usa** (el campo `name` de Meta trae basura). Esta tarea lo saca y agrega `numero` al final. Hay que actualizar la llamada en `services/meta_reminders.py` y los helpers de los tests.

- [ ] **Step 1: Escribir los tests que fallan**

```python
@pytest.mark.parametrize("numero,esperado", [
    (2, "hace unos días"),
    (3, "por ahora lo dejo acá"),
    (4, "Pasó un tiempo"),
    (7, "el último mail"),
])
def test_cada_contacto_dice_algo_distinto(numero, esperado):
    with _capturar() as enviar:
        send_meta_lead_reminder("lead@ejemplo.com", "RP Estudio",
                                "una_nueva_página_web", "https://crm/baja/x",
                                numero)

    assert esperado in enviar.call_args.args[2]


def test_el_primer_contacto_sigue_siendo_el_comercial():
    with _capturar() as enviar:
        send_meta_lead_reminder("lead@ejemplo.com", "RP Estudio",
                                "crear_mi_ecommerce", "https://crm/baja/x", 1)

    assert "Mercado Pago" in enviar.call_args.args[2]


def test_el_septimo_avisa_que_es_el_ultimo():
    """Tiene que cumplir lo que dice: despues de este el lead no vuelve a entrar."""
    with _capturar() as enviar:
        send_meta_lead_reminder("lead@ejemplo.com", "RP Estudio",
                                "automatizaciones", "https://crm/baja/x", 7)

    html = enviar.call_args.args[2]
    assert "el último mail" in html
    assert "no te escribimos más" in html.lower()


def test_todos_los_contactos_llevan_baja_y_firma():
    for numero in range(1, 8):
        with _capturar() as enviar:
            send_meta_lead_reminder("lead@ejemplo.com", "RP Estudio",
                                    "automatizaciones", "https://crm/baja/tok", numero)
        html = enviar.call_args.args[2]
        texto = enviar.call_args.kwargs["text"]
        assert "https://crm/baja/tok" in html, f"contacto {numero} sin link de baja"
        assert "https://crm/baja/tok" in texto, f"contacto {numero} sin baja en el texto"
        assert "+598 97 250 713" in html, f"contacto {numero} sin firma"
        assert enviar.call_args.kwargs["headers"]["List-Unsubscribe-Post"] == \
            "List-Unsubscribe=One-Click"
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_meta_reminder_email.py -q`
Expected: FAIL — la firma actual no acepta `numero` y los textos 2 a 7 no existen.

- [ ] **Step 3: Implementar los textos**

En `services/email_service.py`, junto a `_PARRAFOS_VALOR`:

```python
# Un texto por contacto. Repetir el mismo parrafo comercial cada trimestre es
# exactamente lo que hace que alguien marque spam, asi que del 2 en adelante los
# mails son cortos y no vuelven a vender.
def _cuerpo_por_contacto(numero: int, apertura: str, valor: str, negocio: str) -> tuple[str, list[str]]:
    """Devuelve (asunto, [parrafos]) para el contacto `numero`.

    `apertura` y `valor` ya vienen escapados por el llamador.
    """
    donde = f" para {negocio}" if negocio else ""
    if numero <= 1:
        return (f"Sobre tu consulta{donde}" if negocio else "Sobre tu consulta a Scalerics", [
            "Hola,",
            apertura,
            valor,
            "Agendá 30 minutos y salís de la llamada con precio y plazo cerrados.",
        ])
    if numero == 2:
        return (f"Sobre tu consulta{donde}" if negocio else "Sobre tu consulta a Scalerics", [
            "Hola,",
            f"Te escribimos hace unos días{donde}. Te dejo el link de vuelta por si "
            f"te quedó pendiente.",
            "Si preferís, respondé este mail y coordinamos por acá.",
        ])
    if numero == 3:
        return (f"Sobre tu consulta{donde}" if negocio else "Sobre tu consulta a Scalerics", [
            "Hola,",
            "No tuvimos novedades tuyas, así que por ahora lo dejamos acá.",
            f"Si más adelante retomás el tema{donde}, escribinos y lo vemos.",
        ])
    if numero >= _CONTACTO_FINAL:
        return (f"Último mail{donde}" if negocio else "Último mail de Scalerics", [
            "Hola,",
            "Este es el último mail que te mandamos.",
            f"Si en algún momento retomás el tema{donde}, el link para agendar queda "
            f"acá abajo y podés escribirnos cuando quieras.",
            "Gracias por el tiempo.",
        ])
    return (f"¿Retomamos lo{donde}?" if negocio else "¿Retomamos tu consulta?", [
        "Hola,",
        f"Pasó un tiempo desde que nos dejaste tus datos{donde}.",
        "Si el tema volvió a estar sobre la mesa, en 30 minutos te decimos qué se "
        "puede hacer, cuánto sale y en cuánto tiempo.",
    ])
```

Y arriba, junto a `_CALENDLY`:

```python
_CONTACTO_FINAL = 7
```

Después reescribir `send_meta_lead_reminder` para que use esa función. Mantener intacto todo lo demás: el membrete con `_LOGO_FIRMA`, la firma en texto con `<strong>Scalerics</strong>`, el link de baja, la parte en texto plano, y las cabeceras `List-Unsubscribe` + `List-Unsubscribe-Post`. La firma nueva es:

```python
def send_meta_lead_reminder(to_email: str, negocio: str, rubro: str,
                            unsub_url: str, numero: int = 1) -> str:
```

El cuerpo arma `apertura_esc` / `apertura_txt` y `valor` como hoy, llama a `_cuerpo_por_contacto` una vez para el HTML (con los valores escapados) y otra para el texto plano (con los crudos), y renderiza los párrafos con el mismo `estilo_p`. El link "Agendar una llamada" va después del último párrafo en todos los contactos.

- [ ] **Step 4: Actualizar el llamador**

En `services/meta_reminders.py`, dentro de `enviar_recordatorios`, la llamada pasa a:

```python
        estado = send_meta_lead_reminder(
            destino, lead["negocio"], lead["rubro"],
            f"{base_url.rstrip('/')}/baja/{token}",
            lead.get("numero", 1),
        )
```

- [ ] **Step 5: Correr los tests**

Run: `python -m pytest tests/test_meta_reminder_email.py tests/test_meta_reminders.py -q`
Expected: PASS. Los tests viejos que pasaban `lead_name` posicionalmente hay que actualizarlos.

- [ ] **Step 6: Suite completa y commit**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
git add services/email_service.py services/meta_reminders.py tests/test_meta_reminder_email.py tests/test_meta_reminders.py
git commit -m "feat(recordatorios): un texto por contacto de la secuencia"
```

---

### Task 5: Armar la tanda con los seguimientos primero

**Files:**
- Modify: `services/meta_reminders.py:194-...` (`enviar_recordatorios`)
- Test: `tests/test_meta_reminders.py`

**Interfaces:**
- Consumes: `leads_a_seguir`, `leads_a_recordar`, `registrar_envio(db, id, numero)`, `enviados_ultimas_24h`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
def test_los_seguimientos_van_antes_que_los_contactos_nuevos(db):
    """Un seguimiento a destiempo pierde sentido; un primer contacto aguanta."""
    from services.meta_reminders import enviar_recordatorios

    conn = sqlite3.connect(db)
    for i in range(50, 50 + 14):
        _lead(conn, i, dias=30)
    _lead(conn, 90, dias=60)
    _envio(conn, 90, 1, dias_atras=20)
    conn.commit()
    conn.close()

    mandados = []
    def fake(to, negocio, rubro, url, numero=1):
        mandados.append((to, numero))
        return "ok"

    with patch("services.meta_reminders.send_meta_lead_reminder", side_effect=fake):
        res = enviar_recordatorios(db, "https://crm")

    assert res["enviados"] == 15
    assert (f"lead90@ejemplo.com", 2) in mandados, "el seguimiento entra en la tanda"


def test_el_tope_diario_cuenta_juntos_seguimientos_y_nuevos(db):
    from services.meta_reminders import enviar_recordatorios

    conn = sqlite3.connect(db)
    for i in range(100, 120):
        _lead(conn, i, dias=60)
        _envio(conn, i, 1, dias_atras=20)
    for i in range(200, 210):
        _lead(conn, i, dias=30)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok"):
        res = enviar_recordatorios(db, "https://crm")

    assert res["enviados"] == 15, "15 en total, no 15 de cada tipo"


def test_el_numero_que_se_registra_es_el_que_se_mando(db):
    from services.meta_reminders import enviar_recordatorios

    conn = sqlite3.connect(db)
    _lead(conn, 300, dias=60)
    _envio(conn, 300, 1, dias_atras=20)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok"):
        enviar_recordatorios(db, "https://crm")

    conn = sqlite3.connect(db)
    try:
        numeros = [r[0] for r in conn.execute(
            "SELECT numero FROM meta_reminders WHERE business_id = 300 ORDER BY numero")]
    finally:
        conn.close()

    assert numeros == [1, 2]
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_meta_reminders.py -k "seguimientos_van_antes or tope_diario_cuenta_juntos or numero_que_se_registra" -v`
Expected: FAIL — hoy `enviar_recordatorios` solo mira `leads_a_recordar`.

- [ ] **Step 3: Implementar**

Dentro de `enviar_recordatorios`, reemplazar el armado de candidatos:

```python
    # Los seguimientos primero: uno a destiempo pierde sentido, mientras que un
    # primer contacto puede esperar un dia sin costo.
    seguimientos = leads_a_seguir(db_path, limite=cupo)
    faltan = cupo - len(seguimientos)
    nuevos = leads_a_recordar(db_path, limite=faltan) if faltan > 0 else []
    candidatos = seguimientos + nuevos
```

Y donde hoy llama a `registrar_envio(db_path, lead["id"])`:

```python
        numero = lead.get("numero", 1)
        try:
            token = registrar_envio(db_path, lead["id"], numero)
        except sqlite3.IntegrityError:
            continue
```

El `DELETE` de limpieza ante `"fallo"` tiene que borrar **solo ese contacto**, no todos los del lead:

```python
                    conn.execute(
                        "DELETE FROM meta_reminders WHERE business_id = ? AND numero = ?",
                        (lead["id"], numero),
                    )
```

El log de dry-run pasa a decir qué número mandaría:

```python
            logger.info(f"[dry-run] contacto {lead.get('numero', 1)} a {lead['email']} (lead {lead['id']})")
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_meta_reminders.py -q`
Expected: PASS.

- [ ] **Step 5: Suite completa y commit**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
git add services/meta_reminders.py tests/test_meta_reminders.py
git commit -m "feat(recordatorios): la tanda diaria manda seguimientos y despues contactos nuevos"
```

---

### Task 6: Actualizar el runbook de producción

**Files:**
- Modify: `docs/puesta-en-produccion-recordatorios-meta.md`

Esta tarea no toca código. El runbook actual describe una automatización de un mail por lead, y ahora hay una secuencia y una migración de tabla.

- [ ] **Step 1: Reescribir las partes que cambian**

Agregar al principio, antes del paso de deploy:

- **La migración de `meta_reminders` corre sola en el arranque** (`init_db`). Antes de deployar hay que bajarse el backup: `flyctl ssh sftp get /data/leads.db -a scalerics-crm` (con `MSYS_NO_PATHCONV=1` en Git Bash, si no la ruta remota se convierte en una de Windows).
- **Después de deployar, verificar que las filas sobrevivieron**, con el conteo de antes:

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3;c=sqlite3.connect('/data/leads.db');print('filas:',c.execute('SELECT COUNT(*) FROM meta_reminders').fetchone()[0]);print('numeros:',c.execute('SELECT numero,COUNT(*) FROM meta_reminders GROUP BY numero').fetchall())\""
```

Esperado: la misma cantidad de filas que antes del deploy, todas con `numero=1`.

- Cambiar la sección que dice "cada lead recibe uno solo" por la tabla de los 7 contactos y sus días.
- Agregar a los riesgos: **si alguien contesta el mail y no se lo mueve de `sin_contactar`, va a seguir recibiendo la secuencia.** Es el modo de falla más probable y la única mitigación es la disciplina de mover el lead en el CRM.

- [ ] **Step 2: Commit**

```bash
git add docs/puesta-en-produccion-recordatorios-meta.md
git commit -m "docs: actualizar el runbook para la secuencia de recordatorios"
```

---

## Self-Review

**Cobertura del spec:** los 7 contactos y sus días (Task 3), el `UNIQUE(business_id, numero)` y la migración con las 15 filas (Task 1), la baja sobre toda la secuencia (Task 2), el corte por `crm_status` (Task 3), el tope compartido de 15 (Task 5), la prioridad de los seguimientos (Task 5), un texto por contacto y el séptimo final (Task 4), y el runbook (Task 6). Los 8 criterios de verificación del spec tienen test salvo el 1 (migración), que tiene el suyo en la Task 1.

**Sin placeholders:** cada paso tiene el código o el comando concreto.

**Consistencia de tipos:** `registrar_envio(db, id, numero)`, `leads_a_seguir(db, limite)` devolviendo dicts con `numero`, y `send_meta_lead_reminder(to, negocio, rubro, unsub_url, numero)` se usan con esas mismas firmas en las tareas que las consumen.
