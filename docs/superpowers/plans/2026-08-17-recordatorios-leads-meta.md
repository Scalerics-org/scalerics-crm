# Recordatorios por mail a leads de Meta — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que cada lead de Meta sin contactar reciba, una sola vez, un mail desde `contacto@scalerics.com` invitándolo a agendar una llamada.

**Architecture:** Un job diario dentro del CRM selecciona leads elegibles, arma un mail personalizado con los datos del formulario y lo manda por Resend. Una tabla registra el envío y el token de desuscripción, de modo que "una sola vez por lead" sea una restricción de la base y no una promesa del código.

**Tech Stack:** Python 3, Flask, SQLite, pytest, Resend, Fly.io.

**Spec:** `docs/superpowers/specs/2026-08-17-recordatorios-leads-meta-design.md`

## Global Constraints

- **Repo y rama:** `C:\Users\juant\crm-limpio`, rama `recordatorios-meta`.
- **Finales de línea:** los `.py` están en **CRLF** y `dashboard.py` tiene **BOM UTF-8**. Lo que se cree o edite queda en CRLF. No normalizar a LF.
- **Nunca `git add -A`.** Agregar por ruta explícita.
- **Baseline:** `python -m pytest tests/ -q --ignore=tests/test_main.py` → **115 passed, 0 failed**. `tests/test_main.py` aborta la corrida (`main.py:13` hace `sys.exit(1)` sin `ANTHROPIC_API_KEY`), de ahí el `--ignore`. `tests/conftest.py` ya apaga los procesos de fondo.
- **Remitente:** `Scalerics <contacto@scalerics.com>`. El dominio ya está verificado en Resend.
- **Teléfono de la firma:** `+598 97 250 713`. **Es el único número**: no usar `FACTORY_PHONE`, que en el `.env` es el placeholder `+598 XX XXX XXX`.
- **Calendly:** `https://calendly.com/scalerics/consultoriagratuita`.
- **Logo:** `https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full_alt.png` (constante `_LOGO` en `services/email_service.py`).
- **Sin emojis** en el mail.
- Resend free: **3.000/mes** y **2 envíos por segundo**. El envío espacia; no asumir volumen bajo.

---

### Task 1: Que el webhook guarde el mail, y recuperar los 174 enterrados

`routes/meta.py` extrae el mail del formulario y después no lo pasa al insert. Por eso los 216 leads tienen la columna vacía mientras el dato está en `form_data`.

**Files:**
- Modify: `routes/meta.py` (el `insert_business` de `_fetch_and_store_lead`)
- Create: `scripts/backfill_meta_emails.py`
- Test: `tests/test_backfill_meta_emails.py`

**Interfaces:**
- Produces: `scripts.backfill_meta_emails.backfill(db_path, dry_run=False) -> dict` con claves `actualizados`, `sin_email`, `ya_tenian`.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_backfill_meta_emails.py`:

```python
import json
import sqlite3

import pytest

from scripts.backfill_meta_emails import backfill


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    conn = sqlite3.connect(ruta)
    conn.execute("""
        CREATE TABLE businesses (
            id INTEGER PRIMARY KEY, name TEXT, email TEXT,
            source TEXT, form_data TEXT
        )
    """)
    filas = [
        (1, "Con mail", None, "meta", json.dumps({"email": "uno@ejemplo.com", "full_name": "Con mail"})),
        (2, "Sin mail", None, "meta", json.dumps({"full_name": "Sin mail"})),
        (3, "Ya tenia", "viejo@ejemplo.com", "meta", json.dumps({"email": "nuevo@ejemplo.com"})),
        (4, "No es de meta", None, "google", json.dumps({"email": "ajeno@ejemplo.com"})),
    ]
    conn.executemany("INSERT INTO businesses VALUES (?,?,?,?,?)", filas)
    conn.commit()
    conn.close()
    return ruta


def test_sube_el_mail_desde_form_data(db):
    res = backfill(db)

    conn = sqlite3.connect(db)
    por_id = dict(conn.execute("SELECT id, email FROM businesses").fetchall())
    conn.close()

    assert por_id[1] == "uno@ejemplo.com"
    assert por_id[2] is None, "sin email en form_data no se inventa nada"
    assert por_id[3] == "viejo@ejemplo.com", "no pisa un mail que ya estaba"
    assert por_id[4] is None, "no toca leads de otra fuente"
    assert res == {"actualizados": 1, "sin_email": 1, "ya_tenian": 1}


def test_dry_run_no_escribe(db):
    res = backfill(db, dry_run=True)

    conn = sqlite3.connect(db)
    email = conn.execute("SELECT email FROM businesses WHERE id=1").fetchone()[0]
    conn.close()

    assert email is None, "dry-run no escribe"
    assert res["actualizados"] == 1, "pero si reporta lo que haria"
```

- [ ] **Step 2: Correr y verificar que falla**

```bash
python -m pytest tests/test_backfill_meta_emails.py -v
```

Esperado: FAIL con `ModuleNotFoundError: No module named 'scripts.backfill_meta_emails'`.

- [ ] **Step 3: Escribir el script**

Crear `scripts/backfill_meta_emails.py`:

```python
"""Sube a la columna `email` los mails que quedaron dentro de form_data.

El webhook los extraia y no los guardaba (ver routes/meta.py). Este script
recupera los que ya entraron; el arreglo del webhook evita que vuelva a pasar.
"""

import json
import sqlite3
import sys

CLAVES = ("email", "correo")


def _email_de(form_data: str) -> str:
    try:
        campos = json.loads(form_data or "{}")
    except (ValueError, TypeError):
        return ""
    if not isinstance(campos, dict):
        return ""
    for clave in CLAVES:
        valor = (campos.get(clave) or "").strip()
        if "@" in valor:
            return valor
    return ""


def backfill(db_path: str, dry_run: bool = False) -> dict:
    conn = sqlite3.connect(db_path)
    res = {"actualizados": 0, "sin_email": 0, "ya_tenian": 0}

    filas = conn.execute(
        "SELECT id, email, form_data FROM businesses WHERE source = ?", ("meta",)
    ).fetchall()

    for bid, email_actual, form_data in filas:
        if email_actual and email_actual.strip():
            res["ya_tenian"] += 1
            continue
        email = _email_de(form_data)
        if not email:
            res["sin_email"] += 1
            continue
        if not dry_run:
            conn.execute("UPDATE businesses SET email = ? WHERE id = ?", (email, bid))
        res["actualizados"] += 1

    if not dry_run:
        conn.commit()
    conn.close()
    return res


if __name__ == "__main__":
    seco = "--dry-run" in sys.argv
    argumentos = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(argumentos) != 1:
        print("uso: python -m scripts.backfill_meta_emails <db> [--dry-run]")
        sys.exit(1)
    print(backfill(argumentos[0], dry_run=seco))
```

- [ ] **Step 4: Correr y verificar que pasa**

```bash
python -m pytest tests/test_backfill_meta_emails.py -v
```

Esperado: PASS los dos.

- [ ] **Step 5: Arreglar el webhook**

En `routes/meta.py`, dentro de `_fetch_and_store_lead`, el `insert_business` no incluye el mail. Agregar la clave, justo después de `"phone"`:

```python
                "email":      email or None,
```

La variable `email` ya existe unas líneas más arriba y `"email"` ya está en `ALLOWED_COLUMNS` de `database.py`, así que no hace falta migración.

- [ ] **Step 6: Correr el suite**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
```

Esperado: **117 passed, 0 failed** (115 + 2 nuevos).

- [ ] **Step 7: Commit**

```bash
git add routes/meta.py scripts/backfill_meta_emails.py tests/test_backfill_meta_emails.py
git commit -m "fix(meta): guardar el mail del lead y recuperar los que quedaron en form_data"
```

---

### Task 2: Tabla de recordatorios y baja de la lista

Una sola tabla resuelve dos cosas: que no se mande dos veces (por `UNIQUE` en `business_id`) y la desuscripción (token + fecha de baja).

**Files:**
- Modify: `database.py` (dentro de `init_db`, junto a `meta_token_alerts`)
- Create: `services/meta_reminders.py`
- Test: `tests/test_meta_reminders.py`

**Interfaces:**
- Produces: tabla `meta_reminders`; `services.meta_reminders.registrar_envio(db_path, business_id) -> str` (devuelve el token), `services.meta_reminders.dar_de_baja(db_path, token) -> bool`, `services.meta_reminders.esta_dado_de_baja(db_path, business_id) -> bool`.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_meta_reminders.py`:

```python
import sqlite3

import pytest

from database import init_db
from services.meta_reminders import dar_de_baja, esta_dado_de_baja, registrar_envio


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


def test_registrar_envio_es_una_sola_vez(db):
    token = registrar_envio(db, 42)
    assert token, "tiene que devolver un token"

    with pytest.raises(sqlite3.IntegrityError):
        registrar_envio(db, 42)


def test_la_baja_marca_al_lead(db):
    token = registrar_envio(db, 7)
    assert esta_dado_de_baja(db, 7) is False

    assert dar_de_baja(db, token) is True
    assert esta_dado_de_baja(db, 7) is True


def test_un_token_que_no_existe_no_rompe(db):
    assert dar_de_baja(db, "token-inventado") is False
```

- [ ] **Step 2: Correr y verificar que falla**

```bash
python -m pytest tests/test_meta_reminders.py -v
```

Esperado: FAIL con `ModuleNotFoundError: No module named 'services.meta_reminders'`.

- [ ] **Step 3: Crear la tabla**

En `database.py`, dentro de `init_db`, inmediatamente después del bloque `CREATE TABLE IF NOT EXISTS meta_token_alerts (...)`:

```python
        # ── meta_reminders ────────────────────────────────────────────────────
        # Un registro por lead al que se le mando el recordatorio. El UNIQUE en
        # business_id es lo que garantiza "una sola vez, para siempre": si el
        # job se corre dos veces, el segundo INSERT falla en vez de mandar otro
        # mail. Guarda tambien el token de baja, para no necesitar otra tabla.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_reminders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id     INTEGER NOT NULL UNIQUE,
                token           TEXT NOT NULL UNIQUE,
                sent_at         TEXT NOT NULL,
                unsubscribed_at TEXT
            )
        """)
```

- [ ] **Step 4: Escribir el módulo**

Crear `services/meta_reminders.py`:

```python
"""Registro de recordatorios enviados a leads de Meta y su baja de la lista."""

import secrets
import sqlite3
from datetime import datetime, timezone


def _conn(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def registrar_envio(db_path: str, business_id: int) -> str:
    """Deja constancia del envio y devuelve el token de baja.

    Lanza sqlite3.IntegrityError si ese lead ya tenia un recordatorio: es la
    red que impide mandar dos veces, y tiene que fallar ruidosamente.
    """
    token = secrets.token_urlsafe(24)
    ahora = datetime.now(timezone.utc).isoformat()
    conn = _conn(db_path)
    try:
        conn.execute(
            "INSERT INTO meta_reminders (business_id, token, sent_at) VALUES (?, ?, ?)",
            (business_id, token, ahora),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def dar_de_baja(db_path: str, token: str) -> bool:
    """Marca la baja. Devuelve False si el token no existe."""
    ahora = datetime.now(timezone.utc).isoformat()
    conn = _conn(db_path)
    try:
        cur = conn.execute(
            "UPDATE meta_reminders SET unsubscribed_at = ? WHERE token = ? AND unsubscribed_at IS NULL",
            (ahora, token),
        )
        conn.commit()
        if cur.rowcount:
            return True
        existe = conn.execute(
            "SELECT 1 FROM meta_reminders WHERE token = ?", (token,)
        ).fetchone()
        return bool(existe)
    finally:
        conn.close()


def esta_dado_de_baja(db_path: str, business_id: int) -> bool:
    """Solo lo usan los tests hoy, y esta bien que asi sea: ver la nota de la
    Task 5 sobre por que la baja ya queda cubierta por la seleccion."""
    conn = _conn(db_path)
    try:
        fila = conn.execute(
            "SELECT unsubscribed_at FROM meta_reminders WHERE business_id = ?",
            (business_id,),
        ).fetchone()
    finally:
        conn.close()
    return bool(fila and fila[0])
```

- [ ] **Step 5: Correr y verificar que pasa**

```bash
python -m pytest tests/test_meta_reminders.py -v
```

Esperado: PASS los tres.

- [ ] **Step 6: Commit**

```bash
git add database.py services/meta_reminders.py tests/test_meta_reminders.py
git commit -m "feat(meta): tabla de recordatorios con token de baja"
```

---

### Task 3: La página de baja

El link tiene que existir antes de mandar mails que lo incluyan.

**Files:**
- Modify: `dashboard.py` (la tupla de endpoints exentos en `require_login`, y una ruta nueva)
- Test: `tests/test_meta_reminders.py`

**Interfaces:**
- Consumes: `services.meta_reminders.dar_de_baja(db_path, token) -> bool`.
- Produces: ruta `GET /baja/<token>`, endpoint `baja_recordatorios`.

- [ ] **Step 1: Escribir el test que falla**

Agregar a `tests/test_meta_reminders.py`:

```python
def test_la_pagina_de_baja_funciona_sin_login(tmp_path):
    from dashboard import create_app
    from services.meta_reminders import esta_dado_de_baja, registrar_envio

    ruta = str(tmp_path / "baja.db")
    app = create_app(ruta)
    app.config["TESTING"] = True
    token = registrar_envio(ruta, 99)

    r = app.test_client().get(f"/baja/{token}")

    assert r.status_code == 200, "la pagina de baja no puede exigir login"
    assert esta_dado_de_baja(ruta, 99) is True


def test_la_pagina_de_baja_con_token_invalido_no_rompe(tmp_path):
    from dashboard import create_app

    app = create_app(str(tmp_path / "baja2.db"))
    app.config["TESTING"] = True

    r = app.test_client().get("/baja/no-existe")

    assert r.status_code == 200, "un token viejo o mal copiado muestra una pagina, no un error"
```

- [ ] **Step 2: Correr y verificar que falla**

```bash
python -m pytest tests/test_meta_reminders.py -v -k baja
```

Esperado: FAIL con 302 (redirección al login) o 404.

- [ ] **Step 3: Eximir el endpoint del login**

En `dashboard.py`, dentro de `require_login`, agregar `"baja_recordatorios"` a la tupla existente:

```python
        if request.endpoint in ("login", "logout", "register", "forgot_password", "reset_password", "static", "privacidad", "baja_recordatorios"):
            return
```

- [ ] **Step 4: Agregar la ruta**

En `dashboard.py`, junto a la ruta `/privacidad`:

```python
    @app.route("/baja/<token>")
    def baja_recordatorios(token):
        from services.meta_reminders import dar_de_baja
        dar_de_baja(app.config["DB_PATH"], token)
        # Se responde lo mismo exista o no el token: no tiene sentido decirle a
        # quien se da de baja que su token no servia, y evita sondear tokens.
        return render_template_string("""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Baja confirmada — Scalerics</title></head>
<body style="margin:0;font-family:'Segoe UI',Arial,sans-serif;background:#f1f5f9;color:#1c2b40">
  <div style="max-width:520px;margin:80px auto;background:#fff;border-radius:10px;padding:40px;text-align:center">
    <img src="https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full_alt.png"
         alt="Scalerics" style="height:28px;margin-bottom:24px">
    <h1 style="font-size:20px;margin:0 0 12px">Listo, no te escribimos mas</h1>
    <p style="font-size:15px;color:#64748b;margin:0">
      Te sacamos de la lista de recordatorios. Si algun dia queres retomar, escribinos a
      <a href="mailto:contacto@scalerics.com" style="color:#0088cc">contacto@scalerics.com</a>.
    </p>
  </div>
</body>
</html>""")
```

- [ ] **Step 5: Correr y verificar que pasa**

```bash
python -m pytest tests/test_meta_reminders.py -v
```

Esperado: PASS los cinco.

- [ ] **Step 6: Commit**

```bash
git add dashboard.py tests/test_meta_reminders.py
git commit -m "feat(meta): pagina publica de baja de los recordatorios"
```

---

### Task 4: El mail

**Files:**
- Modify: `services/email_service.py`
- Test: `tests/test_meta_reminder_email.py`

**Interfaces:**
- Produces: `services.email_service.send_meta_lead_reminder(to_email, lead_name, negocio, rubro, unsub_url) -> bool`.
- Modifica: `_send(to, subject, html, from_email=None, headers=None) -> bool` (los dos parámetros nuevos son opcionales; las llamadas existentes no cambian).

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_meta_reminder_email.py`:

```python
from unittest.mock import patch

from services.email_service import send_meta_lead_reminder


def _capturar():
    return patch("services.email_service._send", return_value=True)


def test_el_mail_sale_de_contacto_y_lleva_baja():
    with _capturar() as enviar:
        send_meta_lead_reminder(
            "lead@ejemplo.com", "Romyna", "RP Estudio Juridico",
            "una nueva pagina web", "https://crm/baja/abc123",
        )

    assert enviar.called
    kwargs = enviar.call_args.kwargs
    assert kwargs["from_email"] == "Scalerics <contacto@scalerics.com>"
    assert kwargs["headers"]["List-Unsubscribe"] == "<https://crm/baja/abc123>"

    html = enviar.call_args.args[2]
    assert "https://crm/baja/abc123" in html, "el link de baja va visible en el cuerpo"
    assert "calendly.com/scalerics/consultoriagratuita" in html
    assert "+598 97 250 713" in html


def test_personaliza_con_lo_que_pidio_el_lead():
    with _capturar() as enviar:
        send_meta_lead_reminder(
            "lead@ejemplo.com", "Romyna", "RP Estudio Juridico",
            "una nueva pagina web", "https://crm/baja/x",
        )

    html = enviar.call_args.args[2]
    assert "RP Estudio Juridico" in html
    assert "una nueva pagina web" in html


def test_escapa_la_entrada_del_formulario():
    with _capturar() as enviar:
        send_meta_lead_reminder(
            "lead@ejemplo.com", '<script>alert(1)</script>', "Neg<ocio>",
            "web", "https://crm/baja/x",
        )

    html = enviar.call_args.args[2]
    assert "<script>" not in html, "el nombre lo llena cualquiera en internet"
    assert "&lt;script&gt;" in html
```

- [ ] **Step 2: Correr y verificar que falla**

```bash
python -m pytest tests/test_meta_reminder_email.py -v
```

Esperado: FAIL con `ImportError: cannot import name 'send_meta_lead_reminder'`.

- [ ] **Step 3: Extender `_send`**

En `services/email_service.py`, cambiar la firma y el cuerpo de `_send`:

```python
def _send(to: str, subject: str, html: str, from_email: str | None = None, headers: dict | None = None) -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.info(f"[EMAIL STUB] {subject} → {to}")
        return True
    try:
        cuerpo = {
            "from": from_email or os.environ.get("RESEND_FROM_EMAIL", "Scalerics CRM <crm@noreply.scalerics.com>"),
            "to": [to],
            "subject": subject,
            "html": html,
        }
        if headers:
            cuerpo["headers"] = headers
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=cuerpo,
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send '{subject}' to {to}: {e}")
        return False
```

- [ ] **Step 4: Escribir la función del recordatorio**

Al final de `services/email_service.py`:

```python
_REMITENTE_LEADS = "Scalerics <contacto@scalerics.com>"
_CALENDLY = "https://calendly.com/scalerics/consultoriagratuita"
_TELEFONO = "+598 97 250 713"


def send_meta_lead_reminder(to_email: str, lead_name: str, negocio: str,
                            rubro: str, unsub_url: str) -> bool:
    """Invita al lead a agendar una llamada. Sale de contacto@, no de crm@."""
    # Nombre, negocio y rubro salen del formulario de Meta: los llena cualquiera.
    nombre_esc  = html.escape((lead_name or "").strip() or "Hola")
    negocio_esc = html.escape((negocio or "").strip())
    rubro_esc   = html.escape((rubro or "").strip())

    if negocio_esc and rubro_esc:
        apertura = (f"Nos dejaste tus datos porque buscabas {rubro_esc} "
                    f"para {negocio_esc}.")
    elif rubro_esc:
        apertura = f"Nos dejaste tus datos porque buscabas {rubro_esc}."
    else:
        apertura = "Nos dejaste tus datos para que hablemos de tu proyecto."

    body = (
        _muted(apertura)
        + _muted("Si te sigue interesando, agenda una llamada de 30 minutos "
                 "cuando te quede comodo. Sin compromiso.")
    )
    cuerpo_html = _layout(
        badge="Scalerics",
        title=f"{nombre_esc}, seguimos disponibles",
        body=body,
        cta_url=_CALENDLY,
        cta_label="Agendar una llamada",
    )
    firma = (
        f'<div style="text-align:center;font-size:12px;color:#94a3b8;'
        f'padding:0 24px 28px">'
        f'<img src="{_LOGO}" alt="Scalerics" style="height:22px;margin-bottom:10px"><br>'
        f'Scalerics &middot; {_TELEFONO} &middot; '
        f'<a href="https://scalerics.com" style="color:#94a3b8">scalerics.com</a><br>'
        f'<a href="{unsub_url}" style="color:#94a3b8;text-decoration:underline">'
        f'No quiero recibir mas estos mails</a>'
        f'</div>'
    )
    cuerpo_html = cuerpo_html.replace("</body>", f"{firma}</body>")

    asunto = f"{(lead_name or 'Hola').replace(chr(10), ' ')}, ¿agendamos una llamada?"
    return _send(
        to_email, asunto, cuerpo_html,
        from_email=_REMITENTE_LEADS,
        headers={"List-Unsubscribe": f"<{unsub_url}>"},
    )
```

`html` ya está importado en el módulo (lo usa `send_new_meta_lead_notification`).

- [ ] **Step 5: Correr y verificar que pasa**

```bash
python -m pytest tests/test_meta_reminder_email.py -v
```

Esperado: PASS los tres.

- [ ] **Step 6: Correr el suite**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
```

Esperado: **125 passed, 0 failed**. Si alguno de los tests viejos de mail falla, es porque `_send` cambió de firma: revisar que los parámetros nuevos sean opcionales.

- [ ] **Step 7: Commit**

```bash
git add services/email_service.py tests/test_meta_reminder_email.py
git commit -m "feat(meta): mail de recordatorio al lead, desde contacto@ y con baja"
```

---

### Task 5: La selección de a quién escribirle

**Files:**
- Modify: `services/meta_reminders.py`
- Test: `tests/test_meta_reminders.py`

**Interfaces:**
- Consumes: tabla `meta_reminders` de la Task 2.
- Produces: `services.meta_reminders.leads_a_recordar(db_path, dias_minimos=3, limite=15) -> list[dict]`, cada dict con `id`, `name`, `email`, `negocio`, `rubro`.

**Sobre la baja, para que nadie la crea sin implementar:** el `LEFT JOIN ... WHERE r.id IS NULL` deja fuera a **todo** el que ya tenga un registro en `meta_reminders`, y solo puede darse de baja quien recibio un mail — o sea, quien ya tiene registro. Asi que la desuscripcion queda cubierta por esta misma condicion, sin necesidad de un filtro aparte. Cuando exista el Sistema B (secuencias), donde a un lead se le escribe mas de una vez, va a hacer falta chequear `unsubscribed_at` explicitamente; hoy seria codigo muerto.

- [ ] **Step 1: Escribir el test que falla**

Agregar a `tests/test_meta_reminders.py`:

```python
import json
from datetime import datetime, timedelta, timezone


def _lead(conn, bid, dias, **kw):
    campos = {
        "crm_status": "sin_contactar",
        "email": f"lead{bid}@ejemplo.com",
        "source": "meta",
        "form_data": json.dumps({
            "¿cómo_se_llama_tu_negocio?": f"Negocio {bid}",
            "¿que_es_lo_que_buscás_para_tu_negocio?": "una_nueva_página_web",
        }),
    }
    campos.update(kw)
    cuando = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO businesses (id, name, email, source, crm_status, form_data, scraped_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (bid, f"Lead {bid}", campos["email"], campos["source"],
         campos["crm_status"], campos["form_data"], cuando),
    )


def test_elige_solo_a_los_que_corresponde(db):
    conn = sqlite3.connect(db)
    _lead(conn, 1, dias=5)                                  # elegible
    _lead(conn, 2, dias=1)                                  # muy nuevo
    _lead(conn, 3, dias=5, crm_status="reunion_hecha")      # ya lo contactaron
    _lead(conn, 4, dias=5, email=None)                      # sin mail
    _lead(conn, 5, dias=5, source="google")                 # no es de Meta
    conn.commit()
    conn.close()

    elegidos = [x["id"] for x in leads_a_recordar(db)]

    assert elegidos == [1]


def test_no_repite_a_quien_ya_recibio(db):
    conn = sqlite3.connect(db)
    _lead(conn, 10, dias=5)
    conn.commit()
    conn.close()

    assert [x["id"] for x in leads_a_recordar(db)] == [10]
    registrar_envio(db, 10)
    assert leads_a_recordar(db) == []


def test_respeta_el_limite_y_prioriza_a_los_mas_viejos(db):
    conn = sqlite3.connect(db)
    for i, dias in enumerate([5, 40, 20], start=20):
        _lead(conn, i, dias=dias)
    conn.commit()
    conn.close()

    elegidos = [x["id"] for x in leads_a_recordar(db, limite=2)]

    assert elegidos == [21, 22], "primero el de 40 dias, despues el de 20"


def test_trae_los_datos_para_personalizar(db):
    conn = sqlite3.connect(db)
    _lead(conn, 30, dias=5)
    conn.commit()
    conn.close()

    lead = leads_a_recordar(db)[0]

    assert lead["negocio"] == "Negocio 30"
    assert lead["rubro"] == "una nueva página web", "los guiones bajos se limpian"
```

Agregar `leads_a_recordar` al import del archivo.

- [ ] **Step 2: Correr y verificar que falla**

```bash
python -m pytest tests/test_meta_reminders.py -v -k "elige or repite or limite or personalizar"
```

Esperado: FAIL con `ImportError: cannot import name 'leads_a_recordar'`.

- [ ] **Step 3: Implementar**

Agregar a `services/meta_reminders.py`:

```python
import json

CLAVE_NEGOCIO = "¿cómo_se_llama_tu_negocio?"
CLAVE_RUBRO = "¿que_es_lo_que_buscás_para_tu_negocio?"


def _texto(campos: dict, clave: str) -> str:
    """Los valores de Meta vienen como 'una_nueva_página_web'."""
    return (campos.get(clave) or "").replace("_", " ").strip()


def leads_a_recordar(db_path: str, dias_minimos: int = 3, limite: int = 15) -> list[dict]:
    """Leads de Meta que corresponde recordar hoy, del mas viejo al mas nuevo.

    Las guardas viven todas en el WHERE a proposito: que un lead quede fuera
    no puede depender de que el llamador se acuerde de filtrarlo.
    """
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        filas = conn.execute(
            """
            SELECT b.id, b.name, b.email, b.form_data
              FROM businesses b
         LEFT JOIN meta_reminders r ON r.business_id = b.id
             WHERE b.source = 'meta'
               AND b.crm_status = 'sin_contactar'
               AND b.email IS NOT NULL AND LENGTH(TRIM(b.email)) > 3
               AND r.id IS NULL
               AND b.scraped_at IS NOT NULL
               AND b.scraped_at <= datetime('now', ?)
          ORDER BY b.scraped_at ASC
             LIMIT ?
            """,
            (f"-{int(dias_minimos)} days", int(limite)),
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
        })
    return salida
```

- [ ] **Step 4: Correr y verificar que pasa**

```bash
python -m pytest tests/test_meta_reminders.py -v
```

Esperado: PASS los nueve.

- [ ] **Step 5: Commit**

```bash
git add services/meta_reminders.py tests/test_meta_reminders.py
git commit -m "feat(meta): seleccion de leads a recordar, con las guardas en la query"
```

---

### Task 6: El job diario

Junta todo: selecciona, manda, registra. Con dry-run, interruptor y espaciado.

**Files:**
- Modify: `services/meta_reminders.py`, `dashboard.py`
- Test: `tests/test_meta_reminders.py`

**Interfaces:**
- Consumes: `leads_a_recordar`, `registrar_envio`, `services.email_service.send_meta_lead_reminder`.
- Produces: `services.meta_reminders.enviar_recordatorios(db_path, base_url, dry_run=False) -> dict` con claves `enviados`, `fallidos`, `candidatos`; `services.meta_reminders.start_meta_reminders(app)`.

- [ ] **Step 1: Escribir el test que falla**

Agregar a `tests/test_meta_reminders.py`:

```python
def test_correr_dos_veces_manda_un_solo_mail(db):
    conn = sqlite3.connect(db)
    _lead(conn, 50, dias=5)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value=True) as enviar:
        primera = enviar_recordatorios(db, "https://crm")
        segunda = enviar_recordatorios(db, "https://crm")

    assert primera["enviados"] == 1
    assert segunda["enviados"] == 0, "la segunda corrida no le escribe de nuevo"
    assert enviar.call_count == 1


def test_dry_run_no_manda_ni_registra(db):
    conn = sqlite3.connect(db)
    _lead(conn, 60, dias=5)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder") as enviar:
        res = enviar_recordatorios(db, "https://crm", dry_run=True)

    assert enviar.called is False
    assert res["candidatos"] == 1
    assert leads_a_recordar(db), "sigue elegible: el dry-run no registro nada"


def test_si_el_mail_falla_no_lo_da_por_enviado(db):
    conn = sqlite3.connect(db)
    _lead(conn, 70, dias=5)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value=False):
        res = enviar_recordatorios(db, "https://crm")

    assert res["enviados"] == 0
    assert res["fallidos"] == 1
    assert leads_a_recordar(db), "si no salio, tiene que poder reintentarse manana"
```

Agregar `enviar_recordatorios` al import y `from unittest.mock import patch` arriba.

- [ ] **Step 2: Correr y verificar que falla**

```bash
python -m pytest tests/test_meta_reminders.py -v -k "dos_veces or dry_run_no_manda or mail_falla"
```

Esperado: FAIL con `ImportError: cannot import name 'enviar_recordatorios'`.

- [ ] **Step 3: Implementar el envío**

Agregar a `services/meta_reminders.py`:

```python
import logging
import os
import threading
import time

from services.email_service import send_meta_lead_reminder

logger = logging.getLogger(__name__)

# Resend free permite 2 envios por segundo. A 15 por dia sobra, pero el codigo
# no tiene que depender de que el volumen sea bajo.
_PAUSA_ENTRE_ENVIOS = 0.6
_CADA_24_HORAS = 24 * 60 * 60


def enviar_recordatorios(db_path: str, base_url: str, dry_run: bool = False) -> dict:
    candidatos = leads_a_recordar(db_path)
    res = {"candidatos": len(candidatos), "enviados": 0, "fallidos": 0}

    for lead in candidatos:
        if dry_run:
            logger.info(f"[dry-run] recordatorio a {lead['email']} (lead {lead['id']})")
            continue
        try:
            token = registrar_envio(db_path, lead["id"])
        except sqlite3.IntegrityError:
            # Otra corrida se le adelanto. No es un error: es la guarda haciendo
            # su trabajo.
            continue
        ok = send_meta_lead_reminder(
            lead["email"], lead["name"], lead["negocio"], lead["rubro"],
            f"{base_url.rstrip('/')}/baja/{token}",
        )
        if ok:
            res["enviados"] += 1
        else:
            # Se borra el registro para que manana se reintente: dejarlo puesto
            # significaria que ese lead nunca recibe nada.
            conn = _conn(db_path)
            try:
                conn.execute("DELETE FROM meta_reminders WHERE business_id = ?", (lead["id"],))
                conn.commit()
            finally:
                conn.close()
            res["fallidos"] += 1
        time.sleep(_PAUSA_ENTRE_ENVIOS)

    logger.info(f"Recordatorios Meta: {res}")
    return res


def start_meta_reminders(app) -> None:
    """Corre una vez por dia. Se apaga con META_RECORDATORIOS=off."""
    if os.environ.get("META_RECORDATORIOS", "").lower() == "off":
        logger.info("Recordatorios de Meta apagados por META_RECORDATORIOS=off")
        return

    def _loop():
        time.sleep(180)  # dejar que la app termine de levantar
        while True:
            try:
                with app.app_context():
                    enviar_recordatorios(
                        app.config["DB_PATH"],
                        os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev"),
                    )
            except Exception as e:
                logger.warning(f"Recordatorios Meta: {e}")
            time.sleep(_CADA_24_HORAS)

    threading.Thread(target=_loop, daemon=True, name="meta-reminders").start()
    logger.info("Recordatorios de Meta activos (una corrida por dia)")
```

- [ ] **Step 4: Engancharlo al arranque**

En `dashboard.py`, dentro del `if os.environ.get("CRM_SIN_PROCESOS_DE_FONDO", "").lower() != "true":`, junto a `start_meta_daily_import(app)`:

```python
        from services.meta_reminders import start_meta_reminders
        start_meta_reminders(app)
```

Va dentro de ese `if` para que los tests no lo lancen, igual que los otros procesos de fondo.

- [ ] **Step 5: Correr y verificar que pasa**

```bash
python -m pytest tests/test_meta_reminders.py -v
```

Esperado: PASS los doce.

- [ ] **Step 6: Correr el suite**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
```

Esperado: **128 passed, 0 failed**.

- [ ] **Step 7: Documentar las variables**

En `.env.example`, después del bloque de Meta:

```
# Apaga los recordatorios por mail a los leads de Meta sin tocar el deploy.
# Cualquier valor distinto de "off" los deja andando.
META_RECORDATORIOS=
```

- [ ] **Step 8: Commit**

```bash
git add services/meta_reminders.py dashboard.py .env.example tests/test_meta_reminders.py
git commit -m "feat(meta): job diario de recordatorios con dry-run e interruptor"
```

---

### Task 7: Puesta en producción

Nada de esto se testea local: son pasos contra la base real. **Cada uno se verifica antes de pasar al siguiente.**

- [ ] **Step 1: Confirmar que `contacto@scalerics.com` se lee**

Es el pendiente que el spec dejó abierto. Mandar un mail a esa dirección desde otra cuenta y confirmar que llega a alguien. Si nadie la atiende, agregar `"reply_to"` al cuerpo que arma `_send` apuntando a una casilla que sí se lea, **antes** de seguir.

- [ ] **Step 2: Backup de la base**

```bash
flyctl ssh console -a scalerics-crm -C "cp /data/leads.db /data/leads_pre_recordatorios.db"
flyctl ssh console -a scalerics-crm -C "ls -la /data"
```

- [ ] **Step 3: Deployar**

```bash
flyctl deploy -a scalerics-crm > deploy.log 2>&1
```

Mandar la salida a un archivo, no pipearla: `fly deploy` se cuelga. Al terminar, **comparar la imagen del log contra la que corre**:

```bash
grep -i "^image:" deploy.log
flyctl status -a scalerics-crm | grep -i image
```

Si no coinciden, alguien deployó encima: no seguir.

- [ ] **Step 4: Verificar que el código llegó**

```bash
flyctl ssh console -a scalerics-crm -C "ls /app/services/meta_reminders.py /app/scripts/backfill_meta_emails.py"
```

- [ ] **Step 5: Backfill en seco, después de verdad**

```bash
flyctl ssh console -a scalerics-crm -C "python /app/scripts/backfill_meta_emails.py /data/leads.db --dry-run"
```

Esperado: `{'actualizados': 174, 'sin_email': 42, 'ya_tenian': 0}`. **Si `actualizados` no da 174, parar.** Después sin `--dry-run` y volver a contar.

- [ ] **Step 6: Probar el envío contra una casilla propia**

Cargar el override que ya existe y correr una sola vez:

```bash
flyctl secrets set META_NOTIFY_OVERRIDE=juantomasetti240@gmail.com -a scalerics-crm
```

Ojo: `META_NOTIFY_OVERRIDE` hoy solo afecta a los avisos de admin, **no** a los recordatorios, que van al mail del lead. Para esta prueba, correr `enviar_recordatorios` con `dry_run=True` y revisar la lista; y para ver el mail real, mandárselo a uno mismo con un lead de prueba creado a mano con la propia dirección.

- [ ] **Step 7: Mirar el mail en Gmail**

Que se vea la firma, el logo, el botón, el teléfono y el link de baja. Que **no** aparezca `&lt;` visible: eso sería doble escapado.

- [ ] **Step 8: Probar la baja**

Clickear el link del mail de prueba, confirmar que muestra la página y que el lead deja de ser elegible:

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3;c=sqlite3.connect('/data/leads.db');print(c.execute('SELECT business_id, unsubscribed_at FROM meta_reminders').fetchall())\""
```

- [ ] **Step 9: Sacar el override y dejarlo andando**

```bash
flyctl secrets unset META_NOTIFY_OVERRIDE -a scalerics-crm
```

Al día siguiente, verificar que salieron hasta 15 y que `meta_reminders` tiene un registro por lead, sin repetidos.
