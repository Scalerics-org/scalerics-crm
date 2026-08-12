# Restaurar Meta Ads en el CRM — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Devolver al CRM la ingesta en vivo de leads de Meta Ads, los 192 leads borrados y el panel, con el webhook apuntando de nuevo a Fly.

**Architecture:** El código vuelve desde la historia de `juantomasetti1/scalerics-lead-gen` (commit `82d82ae`, el último con Meta vivo). Como `scalerics-crm` no comparte historia, cada archivo se trae con merge a tres puntas (`git merge-file`) usando el estado post-borrado como base. Meta vuelve a entregar directo en `scalerics-crm.fly.dev/api/meta/webhook` y el `scalerics-meta-hook` de Vercel se jubila.

**Tech Stack:** Python 3, Flask (blueprints), SQLite, pytest, Fly.io, Resend, Graph API de Meta.

**Spec:** `docs/superpowers/specs/2026-08-12-restaurar-meta-ads-crm-design.md`

## Global Constraints

- **Worktree:** trabajar en `C:\Users\juant\crm-limpio-meta`, rama `meta-restore`. **Otra sesión está commiteando en `C:\Users\juant\crm-limpio`** — no correr comandos git ahí.
- **Finales de línea:** los `.py` del repo están en **CRLF** y `dashboard.py` además tiene **BOM UTF-8**. El `.gitattributes` solo cubre `*.sh`, `Dockerfile` y `Procfile`. Todo archivo traído de `lead-gen-uy` (que está en LF) se convierte a CRLF con `sed -i 's/$/\r/'` **antes** de mezclarlo. No normalizar los `.py` a LF: eso genera un diff de 5900 líneas.
- **Commit fuente (pre-borrado):** `82d82ae`. **Base (post-borrado):** `37b0f3c`. Repo fuente: `/c/Users/juant/lead-gen-uy`.
- **Versión de Graph:** `v26.0` en una constante única. Nunca hardcodear la versión por llamada.
- **Baseline conocido:** `python -m pytest tests/ -q --ignore=tests/test_main.py` → **68 passed, 1 failed**. El fallo (`test_budget_ai.py::test_ai_edit_html_returns_modified_html`) es preexistente y ajeno. `tests/test_main.py` aborta la corrida entera (`main.py:13` hace `sys.exit(1)` sin `ANTHROPIC_API_KEY`); por eso siempre se corre con `--ignore`.
- **Nunca `git add -A`.** Agregar archivos por ruta explícita.
- **`SCRATCH`** en este plan es `/c/Users/juant/AppData/Local/Temp/claude/C--Users-juant/64ca45eb-4da4-444a-9fa0-f274da321eba/scratchpad`.

---

### Task 1: Traer el código de Meta al repo

Los 7 archivos del borrado. Tres son nuevos (copia directa), dos son idénticos al estado post-borrado (copia de la versión pre-borrado), y dos necesitan merge a tres puntas.

**Files:**
- Create: `routes/meta.py`, `import_meta_leads.py`, `setup_meta.py`
- Modify: `dashboard.py`, `database.py`, `routes/leads.py`, `services/email_service.py`

**Interfaces:**
- Produces: `routes.meta.meta_bp` (Blueprint), `routes.meta.start_meta_token_monitor(app)`, `routes.meta.start_meta_daily_import(app)`, `services.email_service.send_new_meta_lead_notification(email, lead_name, phone, campaign, city, lead_id)`, `services.email_service.send_meta_token_alert(...)`, y la columna `form_data` en la lista de campos permitidos de `database.py`.

- [ ] **Step 1: Extraer del repo fuente las tres versiones de cada archivo**

Cada comando por separado (el shell vuelve al worktree entre llamadas):

```bash
cd /c/Users/juant/lead-gen-uy && git show 82d82ae:routes/meta.py > $SCRATCH/meta_theirs.py
cd /c/Users/juant/lead-gen-uy && git show 82d82ae:import_meta_leads.py > $SCRATCH/import_theirs.py
cd /c/Users/juant/lead-gen-uy && git show 82d82ae:setup_meta.py > $SCRATCH/setup_theirs.py
cd /c/Users/juant/lead-gen-uy && git show 82d82ae:routes/leads.py > $SCRATCH/leads_theirs.py
cd /c/Users/juant/lead-gen-uy && git show 82d82ae:services/email_service.py > $SCRATCH/email_theirs.py
cd /c/Users/juant/lead-gen-uy && git show 82d82ae:dashboard.py > $SCRATCH/dash_theirs.py
cd /c/Users/juant/lead-gen-uy && git show 82d82ae:database.py > $SCRATCH/db_theirs.py
cd /c/Users/juant/lead-gen-uy && git show 37b0f3c:dashboard.py > $SCRATCH/dash_base.py
cd /c/Users/juant/lead-gen-uy && git show 37b0f3c:database.py > $SCRATCH/db_base.py
```

- [ ] **Step 2: Convertir todo a CRLF**

```bash
cd $SCRATCH && sed -i 's/$/\r/' meta_theirs.py import_theirs.py setup_theirs.py leads_theirs.py email_theirs.py dash_theirs.py db_theirs.py dash_base.py db_base.py
```

Verificar: `file $SCRATCH/meta_theirs.py` debe decir `CRLF line terminators`.

- [ ] **Step 3: Copiar los cinco archivos que no necesitan merge**

`routes/leads.py` y `services/email_service.py` están byte a byte idénticos (módulo CRLF) al estado post-borrado, así que la versión pre-borrado se copia entera sin riesgo.

```bash
cp $SCRATCH/meta_theirs.py   /c/Users/juant/crm-limpio-meta/routes/meta.py
cp $SCRATCH/import_theirs.py /c/Users/juant/crm-limpio-meta/import_meta_leads.py
cp $SCRATCH/setup_theirs.py  /c/Users/juant/crm-limpio-meta/setup_meta.py
cp $SCRATCH/leads_theirs.py  /c/Users/juant/crm-limpio-meta/routes/leads.py
cp $SCRATCH/email_theirs.py  /c/Users/juant/crm-limpio-meta/services/email_service.py
```

- [ ] **Step 4: Merge a tres puntas de `dashboard.py`**

Ya se verificó que mezcla **sin conflictos** (resultado: 6139 líneas).

```bash
cp /c/Users/juant/crm-limpio-meta/dashboard.py $SCRATCH/dash_ours.py
cd $SCRATCH && git merge-file -p dash_ours.py dash_base.py dash_theirs.py > dash_merged.py
```

Esperado: exit `0`, cero `<<<<<<<`. Después:

```bash
cp $SCRATCH/dash_merged.py /c/Users/juant/crm-limpio-meta/dashboard.py
```

- [ ] **Step 5: Merge a tres puntas de `database.py` y resolver el único conflicto**

```bash
cp /c/Users/juant/crm-limpio-meta/database.py $SCRATCH/db_ours.py
cd $SCRATCH && git merge-file -p db_ours.py db_base.py db_theirs.py > db_merged.py
```

Esperado: exit `1`, **un** conflicto en la lista de campos permitidos. `source` ya existe en el repo actual e `interest` es un campo nuevo posterior al borrado. La resolución es quedarse con los tres — reemplazar el bloque en conflicto por:

```python
    "has_whatsapp", "last_event_at", "score", "callback_date", "source",
    "interest", "form_data",
```

Copiar el resultado resuelto a `database.py`.

- [ ] **Step 6: Verificar que todo parsea**

```bash
python -c "import ast; [ast.parse(open(f, encoding='utf-8-sig').read()) for f in ['dashboard.py','database.py','routes/meta.py','routes/leads.py','services/email_service.py','import_meta_leads.py','setup_meta.py']]; print('sintaxis OK')"
```

Esperado: `sintaxis OK`. El `SyntaxWarning` por `"\d"` en `dashboard.py:2029` es preexistente, ignorar.

- [ ] **Step 7: Confirmar el enganche del blueprint**

`dashboard.py` debe tener las tres líneas que trajo el merge:

```bash
grep -n "from routes.meta import\|meta_bp," dashboard.py
grep -n "start_meta_token_monitor(app)\|start_meta_daily_import(app)" dashboard.py
```

Esperado: el import, `meta_bp` dentro de la tupla de blueprints registrados, y los dos `start_*` en el arranque. Si falta alguno, agregarlo a mano siguiendo el patrón de los blueprints vecinos.

- [ ] **Step 8: Correr el suite**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
```

Esperado: **68 passed, 1 failed** — el mismo baseline. Cualquier fallo nuevo es una regresión del merge, no seguir hasta entenderlo.

- [ ] **Step 9: Commit**

```bash
git add routes/meta.py import_meta_leads.py setup_meta.py dashboard.py database.py routes/leads.py services/email_service.py
git commit -m "feat(meta): devuelve el codigo de Meta Ads al CRM"
```

---

### Task 2: Que el webhook no falle en silencio

`_fetch_and_store_lead` corre en un thread y termina en `except Exception as e: logger.error(...)`. El webhook ya devolvió 200, así que Meta no reintenta: si Graph falla, el lead se pierde y solo queda una línea de log que Fly no guarda para siempre. Es la misma clase de falla que dejó 8 leads sin notificar entre el 28-07 y el 05-08.

**Files:**
- Modify: `routes/meta.py`, `services/email_service.py`
- Test: `tests/test_meta_webhook.py` (crear)

**Interfaces:**
- Consumes: `routes.meta._fetch_and_store_lead(app, lead_id, form_id)` de la Task 1.
- Produces: `services.email_service.send_meta_lead_failure_alert(email, lead_id, error)`.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_meta_webhook.py`:

```python
import json
from unittest.mock import patch

import pytest

from dashboard import create_app


@pytest.fixture
def app(tmp_path):
    # create_app(db_path) requiere la ruta de la base — no tiene default.
    application = create_app(str(tmp_path / "test.db"))
    application.config["TESTING"] = True
    return application


def test_fetch_failure_sends_alert(app):
    """Si Graph falla, tiene que avisar por mail con el leadgen_id, no comerse el error."""
    from routes import meta

    with patch.object(meta.requests, "get", side_effect=RuntimeError("graph caido")), \
         patch.object(meta, "_get_admin_emails", return_value=["juan@scalerics.com"]), \
         patch("services.email_service.send_meta_lead_failure_alert") as alert:
        meta.PAGE_TOKEN = "token-de-prueba"
        meta._fetch_and_store_lead(app, "LEAD-123", "FORM-456")

    assert alert.called, "un fallo de Graph tiene que disparar el mail de alerta"
    args = alert.call_args[0]
    assert "LEAD-123" in args, "la alerta tiene que traer el leadgen_id para recuperarlo a mano"
```

- [ ] **Step 2: Correr el test y verificar que falla**

```bash
python -m pytest tests/test_meta_webhook.py::test_fetch_failure_sends_alert -v
```

Esperado: FAIL — `alert.called` es `False`, porque hoy el `except` solo loguea.

- [ ] **Step 3: Agregar la función de alerta a `services/email_service.py`**

El helper de envío del archivo es `_send(to: str, subject: str, html: str) -> bool` (línea 89) y los cuerpos se arman con `_layout(...)`. Agregar, siguiendo el patrón de `send_meta_token_alert` que la Task 1 acaba de devolver al archivo:

```python
def send_meta_lead_failure_alert(email: str, lead_id: str, error: str) -> None:
    """Avisa que un lead de Meta llegó pero no se pudo guardar."""
    asunto = f"[CRM] No se pudo guardar un lead de Meta ({lead_id})"
    cuerpo = (
        f"<p>Llegó un lead de Meta y el CRM no lo pudo guardar.</p>"
        f"<p><b>leadgen_id:</b> {lead_id}</p>"
        f"<p><b>Error:</b> {error}</p>"
        f"<p>Se puede recuperar a mano desde el panel de formularios de Meta "
        f"buscando ese id.</p>"
    )
    _send(email, asunto, cuerpo)
```

Usar el helper de envío que ya use el archivo; si se llama distinto de `_send`, adaptar el nombre.

- [ ] **Step 4: Hacer ruidoso el `except` en `routes/meta.py`**

Reemplazar el bloque final de `_fetch_and_store_lead`:

```python
        except Exception as e:
            logger.error(f"Error processing Meta lead {lead_id}: {e}")
```

por:

```python
        except Exception as e:
            logger.error(f"Error processing Meta lead {lead_id}: {e}")
            from services.email_service import send_meta_lead_failure_alert
            for admin in _get_admin_emails(db):
                try:
                    send_meta_lead_failure_alert(admin, lead_id, str(e))
                except Exception as mail_err:
                    logger.error(f"Tampoco se pudo avisar del fallo: {mail_err}")
```

- [ ] **Step 5: Correr el test y verificar que pasa**

```bash
python -m pytest tests/test_meta_webhook.py -v
```

Esperado: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_meta_webhook.py routes/meta.py services/email_service.py
git commit -m "fix(meta): avisar por mail cuando un lead no se puede guardar"
```

---

### Task 3: Cerrar el fail-open de la firma y subir Graph a v26.0

Dos arreglos chicos en el mismo archivo.

`_verify_signature` devuelve `True` cuando `META_APP_SECRET` está vacío ("skip in dev if not configured"). En un endpoint público eso significa que cualquiera puede POSTear leads falsos si el secret no está cargado. Tiene que fallar cerrado en producción.

**Files:**
- Modify: `routes/meta.py`
- Test: `tests/test_meta_webhook.py`

**Interfaces:**
- Produces: `routes.meta.GRAPH_VERSION` (str), usada por todas las llamadas a Graph del módulo.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_meta_webhook.py`:

```python
def test_signature_fails_closed_without_secret(monkeypatch):
    """Sin APP_SECRET no se puede aceptar cualquier payload: eso es un webhook abierto."""
    from routes import meta

    monkeypatch.setattr(meta, "APP_SECRET", "")
    monkeypatch.setattr(meta, "ALLOW_UNSIGNED", False)
    assert meta._verify_signature(b'{"object":"page"}', "") is False


def test_signature_allows_unsigned_only_when_explicit(monkeypatch):
    """En dev se puede saltear, pero tiene que ser una decisión explícita."""
    from routes import meta

    monkeypatch.setattr(meta, "APP_SECRET", "")
    monkeypatch.setattr(meta, "ALLOW_UNSIGNED", True)
    assert meta._verify_signature(b'{"object":"page"}', "") is True


def test_graph_version_is_single_constant():
    """La versión de Graph vive en un solo lugar."""
    from routes import meta

    fuente = open(meta.__file__, encoding="utf-8-sig").read()
    assert meta.GRAPH_VERSION == "v26.0"
    assert "graph.facebook.com/v" not in fuente, \
        "no hardcodear la versión en las URLs, usar GRAPH_VERSION"
```

- [ ] **Step 2: Correr y verificar que fallan**

```bash
python -m pytest tests/test_meta_webhook.py -v -k "signature or graph_version"
```

Esperado: FAIL — `ALLOW_UNSIGNED` y `GRAPH_VERSION` no existen todavía.

- [ ] **Step 3: Implementar**

En el bloque de constantes de `routes/meta.py`, junto a `PAGE_TOKEN`:

```python
GRAPH_VERSION  = "v26.0"
GRAPH          = f"https://graph.facebook.com/{GRAPH_VERSION}"
ALLOW_UNSIGNED = os.environ.get("META_ALLOW_UNSIGNED", "").lower() == "true"
```

Cambiar el arranque de `_verify_signature`:

```python
def _verify_signature(payload: bytes, sig_header: str) -> bool:
    if not APP_SECRET:
        if ALLOW_UNSIGNED:
            logger.warning("META_APP_SECRET sin configurar y META_ALLOW_UNSIGNED=true — firma no verificada")
            return True
        logger.error("META_APP_SECRET sin configurar — se rechaza el webhook")
        return False
    if not sig_header:
        return False
    try:
        expected = "sha256=" + hmac.new(APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig_header)
    except Exception:
        return False
```

Después reemplazar **todas** las URLs `https://graph.facebook.com/v20.0` del archivo por la constante `GRAPH`. Buscarlas con:

```bash
grep -n "graph.facebook.com" routes/meta.py
```

- [ ] **Step 4: Correr y verificar que pasan**

```bash
python -m pytest tests/test_meta_webhook.py -v
```

Esperado: PASS, los cinco tests del archivo.

- [ ] **Step 5: Correr el suite completo**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
```

Esperado: **73 passed, 1 failed** (68 del baseline + 5 nuevos, con el fallo preexistente de `budget_ai`).

- [ ] **Step 6: Commit**

```bash
git add tests/test_meta_webhook.py routes/meta.py
git commit -m "fix(meta): firma que falla cerrado y version de Graph en una constante"
```

---

### Task 4: Migración de esquema y script de restauración de datos

`form_data` se dropeó de `businesses`. El backup tiene 199 businesses: **192 para insertar y 7 para actualizar**, porque esos 7 son clientes reales que nunca se borraron y siguen vivos en producción con `source=NULL`. Insertarlos duplicaría clientes activos.

Los ids a actualizar, no insertar: **598, 626, 655, 694, 20149, 26740, 32095**.

**Files:**
- Create: `scripts/restore_meta_leads.py`, `tests/test_restore_meta_leads.py`

**Interfaces:**
- Consumes: `~/lead-gen-uy/backups/meta-2026-07-30/meta_leads.json`.
- Produces: `scripts.restore_meta_leads.restore(db_path, backup_path, dry_run=False) -> dict` con las claves `inserted`, `updated`, `skipped`.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_restore_meta_leads.py`:

```python
import json
import sqlite3

import pytest

from scripts.restore_meta_leads import restore

CLIENTES_VIVOS = [598, 626]


@pytest.fixture
def db(tmp_path):
    ruta = tmp_path / "leads.db"
    conn = sqlite3.connect(ruta)
    conn.execute("""
        CREATE TABLE businesses (
            id INTEGER PRIMARY KEY, name TEXT, phone TEXT, city TEXT,
            category TEXT, status TEXT, notes TEXT, score INTEGER,
            source TEXT, form_data TEXT, scraped_at TEXT
        )
    """)
    # Un cliente real que sobrevivió al borrado, con source en NULL
    conn.execute(
        "INSERT INTO businesses (id, name, source, form_data) VALUES (598, 'Plan Arq', NULL, NULL)"
    )
    conn.commit()
    conn.close()
    return str(ruta)


@pytest.fixture
def backup(tmp_path):
    ruta = tmp_path / "meta_leads.json"
    datos = {
        "businesses": [
            {"id": 598, "name": "Plan Arq", "source": "meta", "form_data": '{"negocio":"arq"}'},
            {"id": 9001, "name": "Lead Borrado", "source": "meta", "form_data": '{"negocio":"x"}'},
        ],
        "lead_events": [], "call_logs": [], "lead_attachments": [],
        "budgets": [], "demos": [], "meetings": [], "activity_log": [],
    }
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    return str(ruta)


def test_actualiza_clientes_vivos_sin_duplicarlos(db, backup):
    res = restore(db, backup)

    conn = sqlite3.connect(db)
    filas = conn.execute("SELECT COUNT(*) FROM businesses WHERE id = 598").fetchone()[0]
    origen = conn.execute("SELECT source FROM businesses WHERE id = 598").fetchone()[0]
    conn.close()

    assert filas == 1, "el cliente vivo no se puede duplicar"
    assert origen == "meta", "al cliente vivo hay que devolverle el source"
    assert res["updated"] == 1
    assert res["inserted"] == 1


def test_dry_run_no_toca_la_base(db, backup):
    res = restore(db, backup, dry_run=True)

    conn = sqlite3.connect(db)
    total = conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0]
    conn.close()

    assert total == 1, "dry-run no escribe"
    assert res["inserted"] == 1, "pero sí reporta lo que haría"
```

- [ ] **Step 2: Correr y verificar que falla**

```bash
python -m pytest tests/test_restore_meta_leads.py -v
```

Esperado: FAIL con `ModuleNotFoundError: No module named 'scripts.restore_meta_leads'`.

- [ ] **Step 3: Implementar el script**

Crear `scripts/__init__.py` vacío si no existe. Crear `scripts/restore_meta_leads.py`:

```python
"""Restaura los leads de Meta borrados el 30-07-2026 desde el backup JSON.

Los 7 ids de CLIENTES_VIVOS nunca se borraron: a esos se les hace UPDATE de
source y form_data. Al resto se le hace INSERT.
"""

import json
import sqlite3
import sys

CLIENTES_VIVOS = {598, 626, 655, 694, 20149, 26740, 32095}

TABLAS_RELACIONADAS = [
    "lead_events", "call_logs", "lead_attachments",
    "budgets", "demos", "meetings", "activity_log",
]


def _columnas(conn, tabla: str) -> set:
    return {fila[1] for fila in conn.execute(f"PRAGMA table_info({tabla})")}


def _filtrar(fila: dict, columnas: set) -> dict:
    """Se queda solo con las claves que la tabla realmente tiene hoy."""
    return {k: v for k, v in fila.items() if k in columnas}


def restore(db_path: str, backup_path: str, dry_run: bool = False) -> dict:
    with open(backup_path, encoding="utf-8") as f:
        datos = json.load(f)

    conn = sqlite3.connect(db_path)
    res = {"inserted": 0, "updated": 0, "skipped": 0}

    cols_biz = _columnas(conn, "businesses")

    for fila in datos.get("businesses", []):
        bid = fila.get("id")
        if bid is None:
            res["skipped"] += 1
            continue

        if bid in CLIENTES_VIVOS:
            existe = conn.execute(
                "SELECT 1 FROM businesses WHERE id = ?", (bid,)
            ).fetchone()
            if existe:
                if not dry_run:
                    conn.execute(
                        "UPDATE businesses SET source = ?, form_data = ? WHERE id = ?",
                        (fila.get("source"), fila.get("form_data"), bid),
                    )
                res["updated"] += 1
                continue

        datos_fila = _filtrar(fila, cols_biz)
        campos = ", ".join(datos_fila)
        marcas = ", ".join("?" for _ in datos_fila)
        if not dry_run:
            conn.execute(
                f"INSERT OR IGNORE INTO businesses ({campos}) VALUES ({marcas})",
                list(datos_fila.values()),
            )
        res["inserted"] += 1

    ids_restaurados = {f["id"] for f in datos.get("businesses", []) if f.get("id")}
    for tabla in TABLAS_RELACIONADAS:
        filas = datos.get(tabla, [])
        if not filas:
            continue
        try:
            cols = _columnas(conn, tabla)
        except sqlite3.Error:
            continue
        if not cols:
            continue
        for fila in filas:
            if fila.get("business_id") not in ids_restaurados:
                continue
            datos_fila = _filtrar(fila, cols)
            if not datos_fila:
                continue
            campos = ", ".join(datos_fila)
            marcas = ", ".join("?" for _ in datos_fila)
            if not dry_run:
                conn.execute(
                    f"INSERT OR IGNORE INTO {tabla} ({campos}) VALUES ({marcas})",
                    list(datos_fila.values()),
                )

    if not dry_run:
        conn.commit()
    conn.close()
    return res


if __name__ == "__main__":
    seco = "--dry-run" in sys.argv
    argumentos = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(argumentos) != 2:
        print("uso: python -m scripts.restore_meta_leads <db> <backup.json> [--dry-run]")
        sys.exit(1)
    print(restore(argumentos[0], argumentos[1], dry_run=seco))
```

- [ ] **Step 4: Correr y verificar que pasa**

```bash
python -m pytest tests/test_restore_meta_leads.py -v
```

Esperado: PASS los dos.

- [ ] **Step 5: Escribir la migración de esquema**

Crear `scripts/add_form_data_column.py`:

```python
"""Agrega la columna form_data a businesses. Idempotente."""

import sqlite3
import sys


def migrate(db_path: str) -> bool:
    conn = sqlite3.connect(db_path)
    existentes = {fila[1] for fila in conn.execute("PRAGMA table_info(businesses)")}
    if "form_data" in existentes:
        conn.close()
        return False
    conn.execute("ALTER TABLE businesses ADD COLUMN form_data TEXT")
    conn.commit()
    conn.close()
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("uso: python -m scripts.add_form_data_column <db>")
        sys.exit(1)
    print("columna agregada" if migrate(sys.argv[1]) else "ya existía")
```

- [ ] **Step 6: Correr el suite completo**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
```

Esperado: **75 passed, 1 failed**.

- [ ] **Step 7: Commit**

```bash
git add scripts/__init__.py scripts/restore_meta_leads.py scripts/add_form_data_column.py tests/test_restore_meta_leads.py
git commit -m "feat(meta): script de restauracion de leads y migracion de form_data"
```

---

### Task 5: Preparar producción — secrets e infraestructura

Nada de esto se puede testear localmente; son cambios en Fly. **Ninguno rompe la app en su estado actual**, así que van antes del deploy.

**Files:**
- Modify: `fly.toml`

- [ ] **Step 1: Confirmar que los `META_*` no están**

```bash
flyctl secrets list -a scalerics-crm
```

Esperado: no aparece ningún `META_*` (se rotaron en la separación).

- [ ] **Step 2: Validar el `META_PAGE_TOKEN` antes de cargarlo**

La copia autoritativa es la del proyecto de Vercel `scalerics-meta-hook`, no la de `~/lead-gen-uy/.env` (ese `.env` se tocó después de la rotación y puede tener un valor viejo). Sacar el valor de Vercel y validarlo contra Graph:

```bash
curl -s "https://graph.facebook.com/debug_token?input_token=<PAGE_TOKEN>&access_token=<APP_ID>|<APP_SECRET>"
```

Esperado: `"is_valid": true`. Si dice `false` o el `expires_at` ya pasó, **parar**: hay que regenerar el token de página antes de seguir, no tiene sentido deployar contra un token muerto.

- [ ] **Step 3: Cargar los cinco secrets**

```bash
flyctl secrets set -a scalerics-crm META_APP_ID=<valor> META_APP_SECRET=<valor> META_PAGE_ID=<valor> META_PAGE_TOKEN=<valor> META_VERIFY_TOKEN=<valor>
```

Un solo comando con los cinco: cada `flyctl secrets set` dispara un redeploy, y así es uno solo.

Nota: `routes/meta.py` también lee `ADMIN_WA_PHONE`, pero está comentado en el código y tiene default `""`. No hace falta cargarlo.

- [ ] **Step 4: Que la máquina deje de dormirse**

En `fly.toml`, cambiar:

```toml
  min_machines_running = 0
```

por:

```toml
  min_machines_running = 1
```

Con el webhook directo, Meta pega contra la máquina y un cold start le hace timeout.

- [ ] **Step 5: Commit**

```bash
git add fly.toml
git commit -m "chore(fly): maquina siempre viva para recibir el webhook de Meta"
```

---

### Task 6: Deploy, migración y restauración de datos

**Orden obligatorio: migración de esquema primero, deploy después.** Si el código que lee `form_data` sube antes de que la columna exista, la app queda rota en el intervalo.

- [ ] **Step 1: Backup de la base de producción antes de tocar nada**

```bash
flyctl ssh console -a scalerics-crm -C "cp /data/leads.db /data/leads_pre_meta_restore.db"
```

Verificar que quedó: `flyctl ssh console -a scalerics-crm -C "ls -la /data/"`.

- [ ] **Step 2: Contar antes**

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3; c=sqlite3.connect('/data/leads.db'); print('businesses', c.execute('SELECT COUNT(*) FROM businesses').fetchone()[0])\""
```

Anotar el número. Post-borrado deberían ser ~1530.

- [ ] **Step 3: Correr la migración de esquema en producción**

Subir los **tres** archivos que hacen falta en el volumen (la migración, el script de restauración y el backup JSON) en una sola sesión de sftp, y correr la migración:

```bash
flyctl ssh sftp shell -a scalerics-crm
# put scripts/add_form_data_column.py /data/add_form_data_column.py
# put scripts/restore_meta_leads.py /data/restore_meta_leads.py
# put /c/Users/juant/lead-gen-uy/backups/meta-2026-07-30/meta_leads.json /data/meta_leads.json
flyctl ssh console -a scalerics-crm -C "python /data/add_form_data_column.py /data/leads.db"
```

Esperado: `columna agregada`. Si dice `ya existía`, seguir igual (es idempotente).

- [ ] **Step 4: Deploy**

```bash
flyctl deploy -a scalerics-crm > /c/Users/juant/AppData/Local/Temp/claude/C--Users-juant/64ca45eb-4da4-444a-9fa0-f274da321eba/scratchpad/deploy.log 2>&1
```

**Mandar la salida a un archivo, no pipearla a `grep`: `fly deploy` se cuelga.** Tarda ~15 min. Al terminar, revisar el log y confirmar que la app arrancó:

```bash
flyctl status -a scalerics-crm
```

- [ ] **Step 5: Restaurar los datos, primero en seco**

Subir el backup y el script, y correr el dry-run:

```bash
flyctl ssh console -a scalerics-crm -C "python /data/restore_meta_leads.py /data/leads.db /data/meta_leads.json --dry-run"
```

Esperado: `{'inserted': 192, 'updated': 7, 'skipped': 0}`. **Si `inserted` no da 192 o `updated` no da 7, parar** — algo no cuadra entre el backup y el estado de la base.

- [ ] **Step 6: Restaurar de verdad**

```bash
flyctl ssh console -a scalerics-crm -C "python /data/restore_meta_leads.py /data/leads.db /data/meta_leads.json"
```

- [ ] **Step 7: Contar después**

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3; c=sqlite3.connect('/data/leads.db'); print('total', c.execute('SELECT COUNT(*) FROM businesses').fetchone()[0]); print('meta', c.execute(\\\"SELECT COUNT(*) FROM businesses WHERE source='meta'\\\").fetchone()[0]); print('clientes', c.execute('SELECT COUNT(*) FROM businesses WHERE id IN (598,626,655,694,20149,26740,32095)').fetchone()[0])\""
```

Esperado: `total` = el de Step 2 **+ 192**, `meta` = 199, y `clientes` = **7** (no 14 — si da 14 se duplicaron y hay que restaurar el backup del Step 1).

- [ ] **Step 8: Mirar el panel en el browser**

Entrar a https://scalerics-crm.fly.dev/ y confirmar: el panel de Meta dibuja, muestra los leads con sus campos de formulario, y la Cola **no** los incluye.

---

### Task 7: Cortar la entrega al CRM y verificar de punta a punta

Esta es la única tarea que prueba lo que importa: que un lead de verdad llegue a la base.

- [ ] **Step 1: Cambiar la URL de callback en el panel de Meta**

App "Scalerics CRM" (id `1306976674838718`) → Casos de uso → Webhooks → producto **Page**.

Cambiar la URL de callback a `https://scalerics-crm.fly.dev/api/meta/webhook` con el `META_VERIFY_TOKEN` que se cargó en el Step 3 de la Task 5.

- [ ] **Step 2: Confirmar la versión del campo `leadgen`**

En la misma pantalla, fila `leadgen`, desplegable de **Versión**: tiene que decir **v26.0**, igual que el resto de los campos.

Esto es exactamente lo que se rompió en silencio entre el 28-07 y el 05-08: la suscripción quedó fijada en v25.0, Meta la mostraba activa y no entregaba nada. Mirarlo ahora, después de tocar la config.

- [ ] **Step 3: Probar el camino real con la Lead Ads Testing Tool**

`https://developers.facebook.com/tools/lead-ads-testing`

**No alcanza con el botón "Test" del panel de webhooks:** ese prueba solo el transporte (URL + firma + handler) y entrega igual aunque la entrega real esté rota. La Testing Tool crea un lead de prueba y dispara el webhook de verdad.

Es **un lead por formulario**: para repetir hay que borrar el anterior.

**Aviso:** un test exitoso manda el mail a los destinatarios reales (Javier y Juan Pereyra). Avisarles antes.

- [ ] **Step 4: Confirmar que el lead llegó a la base**

```bash
flyctl ssh console -a scalerics-crm -C "python -c \"import sqlite3; c=sqlite3.connect('/data/leads.db'); print(c.execute(\\\"SELECT id, name, phone, form_data FROM businesses WHERE source='meta' ORDER BY id DESC LIMIT 3\\\").fetchall())\""
```

Esperado: el lead de prueba arriba de todo, con `form_data` poblado.

Si no aparece, mirar los logs **en el momento**: `flyctl logs -a scalerics-crm`.

- [ ] **Step 5: Confirmar que el monitor de token arrancó**

El `scalerics-meta-hook` tenía un cron semanal que avisaba antes de que venciera el token. Esa función la retoma `start_meta_token_monitor` dentro del CRM. Confirmar en los logs de arranque que la línea del monitor aparece; si no arranca, jubilar el hook deja el vencimiento del token sin vigilancia.

- [ ] **Step 6: Jubilar el meta-hook de Vercel**

Recién ahora, y **sin borrarlo**: pausar el proyecto `scalerics-meta-hook` en Vercel. Si algo sale mal, volver a apuntar la URL de callback tiene que seguir siendo un cambio de un campo.

- [ ] **Step 7: Merge de la rama**

Usar la skill `superpowers:finishing-a-development-branch`. Ojo: `meta-restore` salió de `d500b14`, que incluye trabajo de Calendly de otra sesión. Coordinar antes de mergear para no pisarse.
