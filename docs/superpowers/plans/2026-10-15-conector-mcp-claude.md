# Conector MCP del CRM para Claude — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el equipo agregue el CRM en claude.ai como conector personalizado y Claude pueda buscar leads, ver una ficha y leer el pipeline, entrando cada persona con su usuario del CRM.

**Architecture:** Todo nativo en Flask, sin cambiar el runtime. Un blueprint `oauth_bp` hace de servidor de autorización OAuth 2.1 (PKCE, CIMD de Claude, DCR) reutilizando el login y los usuarios del CRM; un blueprint `mcp_bp` atiende `POST /mcp` como transporte Streamable HTTP stateless con respuesta JSON y despacha a tres herramientas de solo lectura. Tokens opacos hasheados en SQLite.

**Tech Stack:** Python 3.11, Flask 3.1, SQLite, `requests` (ya en `requirements.txt`), pytest con el `test_client` de Flask. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-10-15-conector-mcp-claude-design.md`. Leerlo antes de empezar.

**Dónde se trabaja:** el worktree `C:\Users\gonza\Documents\GitHub\crm-mcp`, rama `feat/conector-mcp`. Correr todo desde ahí. No deployar nada: eso lo pide Juan.

**Cómo se corren los tests:** `python -m pytest tests/<archivo> -q` desde la raíz del worktree. La suite entera tarda minutos; correr solo el archivo que se toca y, al final de cada tarea, `python -m pytest tests/test_oauth_clientes.py tests/test_oauth_tokens.py tests/test_oauth_flujo.py tests/test_mcp.py -q`.

**Convenciones del repo que hay que respetar:**
- Comentarios y docstrings en español, explicando el *por qué*, como el resto del código.
- Las tablas nuevas se crean con `CREATE TABLE IF NOT EXISTS` desde una función `init_<modulo>(conn)` que `database.init_db` llama con su conexión (patrón de `services/fidelidad.init_fidelidad`).
- Los servicios abren la base con `from database import _connect` y cierran en `finally`.
- Commits con prefijo `feat:`, `test:`, `docs:`, en español.

---

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `services/oauth_tokens.py` (nuevo) | Tablas `oauth_clientes`, `oauth_codigos`, `oauth_tokens`. PKCE. Emitir, canjear, refrescar, revocar y validar tokens. `base_url()` y `recurso_mcp()`. |
| `services/oauth_clientes.py` (nuevo) | Resolver un `client_id`: CIMD de `claude.ai`/`claude.com` con cache, o cliente DCR. Registrar clientes. Comparar `redirect_uri`. |
| `services/mcp_tools.py` (nuevo) | Tabla `mcp_llamadas`. Registro de herramientas con schema, validación de argumentos, las tres herramientas, registro de cada llamada. |
| `routes/oauth.py` (nuevo) | Metadata `/.well-known/*`, `/oauth/register`, `/oauth/authorize` (consentimiento), `/oauth/token`, `/oauth/revoke`. |
| `routes/mcp.py` (nuevo) | `POST /mcp`: Bearer, `401` con `WWW-Authenticate`, JSON-RPC, despacho. |
| `database.py` (modificar) | Llamar `init_oauth(conn)` e `init_mcp(conn)` desde `init_db`. |
| `dashboard.py` (modificar) | Registrar los dos blueprints, eximir `/mcp`, `/oauth/`, `/.well-known/` del candado de sesión, `next` en `/login`. |
| `tests/test_oauth_tokens.py`, `tests/test_oauth_clientes.py`, `tests/test_oauth_flujo.py`, `tests/test_mcp.py` (nuevos) | Tests. |
| `docs/puesta-en-produccion-conector-claude.md` (nuevo) | Pasos para el Owner del plan Team. |
| `COORDINACION.md` (modificar) | Bitácora de esta sesión. |

---

### Task 1: Anotarse en la bitácora

**Files:**
- Modify: `COORDINACION.md` (tabla "Quién está en qué")

- [ ] **Step 1: Agregar la fila**

Abrir `COORDINACION.md`, ubicar la tabla con encabezado `| Sesión | Territorio | Archivos que está tocando | Desde |` y agregar al final de esa tabla:

```markdown
| M (conector MCP para Claude) | servidor MCP + OAuth propio para que el equipo use el CRM desde claude.ai. Solo lectura: leads y pipeline. Rama `feat/conector-mcp`, worktree `crm-mcp`. Spec en `docs/superpowers/specs/2026-10-15-conector-mcp-claude-design.md`. **No deployo sin que Juan lo pida.** | nuevos: `routes/oauth.py`, `routes/mcp.py`, `services/oauth_tokens.py`, `services/oauth_clientes.py`, `services/mcp_tools.py`, `tests/test_oauth_*.py`, `tests/test_mcp.py`. Zona compartida, cambios chicos: `database.py` (dos `init_*` en `init_db`), `dashboard.py` (dos blueprints, tres prefijos exentos en `require_login`, `next` en `/login`). | 15/10 |
```

- [ ] **Step 2: Commit**

```bash
git add COORDINACION.md
git commit -m "docs: sesión M se anota para el conector MCP"
```

---

### Task 2: Tokens OAuth — tablas, PKCE, emitir y canjear códigos

**Files:**
- Create: `services/oauth_tokens.py`
- Modify: `database.py` (dentro de `init_db`, justo después de `init_plexo(conn)`)
- Test: `tests/test_oauth_tokens.py`

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_oauth_tokens.py`:

```python
"""Códigos y tokens del OAuth del conector MCP.

Prueban `services/oauth_tokens.py` directo, sin HTTP. El flujo entero por
HTTP vive en test_oauth_flujo.py.
"""

import base64
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.security import generate_password_hash

from database import create_user, init_db
from services import oauth_tokens as ot


def _pkce():
    verifier = secrets.token_urlsafe(40)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "a.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def uid(db):
    return create_user(db, name="Ana", email="ana@scalerics.com", phone="099",
                       password_hash=generate_password_hash("x" * 10))


def _tabla_existe(db, nombre):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                            (nombre,)).fetchone() is not None
    finally:
        conn.close()


def test_init_db_crea_las_tablas(db):
    for t in ("oauth_clientes", "oauth_codigos", "oauth_tokens"):
        assert _tabla_existe(db, t), t


def test_pkce_s256():
    verifier, challenge = _pkce()
    assert ot.verificar_pkce(verifier, challenge)
    assert not ot.verificar_pkce(verifier + "x", challenge)


def test_canjear_codigo_devuelve_tokens(db, uid):
    verifier, challenge = _pkce()
    code = ot.emitir_codigo(db, uid, "cli", "https://claude.ai/api/mcp/auth_callback",
                            challenge, "crm:leer", "https://crm.test/mcp")
    tokens = ot.canjear_codigo(db, code, "cli", "https://claude.ai/api/mcp/auth_callback", verifier)
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["expires_in"] == 3600
    assert tokens["scope"] == "crm:leer"
    # El token no se guarda en claro.
    conn = sqlite3.connect(db)
    fila = conn.execute("SELECT access_hash, refresh_hash, user_id FROM oauth_tokens").fetchone()
    conn.close()
    assert fila[0] != tokens["access_token"] and fila[1] != tokens["refresh_token"]
    assert fila[2] == uid


def test_canjear_falla_con_verifier_incorrecto(db, uid):
    _, challenge = _pkce()
    code = ot.emitir_codigo(db, uid, "cli", "https://r/cb", challenge, "crm:leer", None)
    assert ot.canjear_codigo(db, code, "cli", "https://r/cb", "otro-verifier") is None


def test_canjear_falla_si_cliente_o_redirect_no_coinciden(db, uid):
    verifier, challenge = _pkce()
    code = ot.emitir_codigo(db, uid, "cli", "https://r/cb", challenge, "crm:leer", None)
    assert ot.canjear_codigo(db, code, "otro", "https://r/cb", verifier) is None
    assert ot.canjear_codigo(db, code, "cli", "https://r/otro", verifier) is None


def test_reusar_codigo_falla_y_revoca_lo_emitido(db, uid):
    verifier, challenge = _pkce()
    code = ot.emitir_codigo(db, uid, "cli", "https://r/cb", challenge, "crm:leer", None)
    tokens = ot.canjear_codigo(db, code, "cli", "https://r/cb", verifier)
    assert ot.validar_access(db, tokens["access_token"]) is not None
    assert ot.canjear_codigo(db, code, "cli", "https://r/cb", verifier) is None
    assert ot.validar_access(db, tokens["access_token"]) is None


def test_codigo_vencido_no_se_canjea(db, uid, monkeypatch):
    verifier, challenge = _pkce()
    code = ot.emitir_codigo(db, uid, "cli", "https://r/cb", challenge, "crm:leer", None)
    futuro = datetime.now(timezone.utc) + timedelta(minutes=11)
    monkeypatch.setattr(ot, "_ahora", lambda: futuro)
    assert ot.canjear_codigo(db, code, "cli", "https://r/cb", verifier) is None
```

- [ ] **Step 2: Correr los tests y ver que fallan**

Run: `python -m pytest tests/test_oauth_tokens.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'services.oauth_tokens'`.

- [ ] **Step 3: Crear `services/oauth_tokens.py`**

```python
"""Códigos y tokens del OAuth del conector MCP para Claude.

El CRM es a la vez servidor de autorización y de recurso: emite los tokens que
Claude le presenta después en `/mcp`. Son opacos (bytes al azar), y en la base
vive solo su SHA-256: un volcado de `leads.db` no alcanza para entrar.

Duraciones: el código de autorización vence a los 10 minutos, el access a la
hora, el refresh a los 30 días. El refresh se rota en cada uso (OAuth 2.1 lo
exige para clientes públicos, y Claude se registra como público).

Un código reusado no solo falla: revoca lo que se emitió con él. Es la defensa
de OAuth 2.1 §4.1.3 contra un código robado que llega segundo.
"""

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from database import _connect

SCOPE_LECTURA = "crm:leer"
CODIGO_DURA = timedelta(minutes=10)
ACCESS_DURA = timedelta(hours=1)
REFRESH_DURA = timedelta(days=30)
# Tokens cuyo refresh venció hace más de esto se borran en el barrido.
BASURA_DESPUES_DE = timedelta(days=30)
# `ultimo_uso` se escribe como mucho una vez por minuto, para no escribir en
# SQLite en cada llamada de Claude.
ULTIMO_USO_CADA = timedelta(minutes=1)


def base_url() -> str:
    """La URL pública del CRM, sin barra final. Es el emisor OAuth y la base de
    todos los endpoints anunciados. Nunca se arma desde el `Host` del request:
    un header forjado no puede cambiar lo que anunciamos."""
    return os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev").rstrip("/")


def recurso_mcp() -> str:
    """El identificador del recurso protegido. Tiene que ser exactamente la URL
    que la persona escribe en claude.ai."""
    return base_url() + "/mcp"


def init_oauth(conn: sqlite3.Connection) -> None:
    """Tablas del OAuth. Lo llama `database.init_db` con su conexión."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_clientes (
            client_id      TEXT PRIMARY KEY,
            client_name    TEXT,
            redirect_uris  TEXT NOT NULL,
            created_at     TEXT NOT NULL
        )
    """)
    conn.execute("""
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
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            client_id        TEXT NOT NULL,
            codigo_hash      TEXT,
            access_hash      TEXT NOT NULL UNIQUE,
            refresh_hash     TEXT NOT NULL UNIQUE,
            scope            TEXT NOT NULL,
            resource         TEXT,
            access_expira    TEXT NOT NULL,
            refresh_expira   TEXT NOT NULL,
            ultimo_uso       TEXT,
            revocado_en      TEXT,
            created_at       TEXT NOT NULL
        )
    """)


# ── utilidades ───────────────────────────────────────────────────────────────

def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(momento: datetime) -> str:
    return momento.isoformat(timespec="seconds")


def _hash(valor: str) -> str:
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()


def _secreto() -> str:
    return secrets.token_urlsafe(32)


def verificar_pkce(code_verifier: str, code_challenge: str) -> bool:
    """S256: BASE64URL(SHA256(verifier)) sin relleno tiene que ser el challenge."""
    if not code_verifier or not code_challenge:
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii", "ignore")).digest()
    calculado = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(calculado, code_challenge)


# ── códigos ──────────────────────────────────────────────────────────────────

def emitir_codigo(db: str, user_id: int, client_id: str, redirect_uri: str,
                  code_challenge: str, scope: str, resource: Optional[str]) -> str:
    """Devuelve el código en claro para mandárselo a Claude; guarda el hash."""
    code = _secreto()
    ahora = _ahora()
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO oauth_codigos (code_hash, user_id, client_id, redirect_uri, "
            "code_challenge, scope, resource, expires_at, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (_hash(code), user_id, client_id, redirect_uri, code_challenge, scope, resource,
             _iso(ahora + CODIGO_DURA), _iso(ahora)))
        _barrer(conn, ahora)
        conn.commit()
    finally:
        conn.close()
    return code


def canjear_codigo(db: str, code: str, client_id: str, redirect_uri: str,
                   code_verifier: str) -> Optional[dict]:
    """Cambia un código por access + refresh. None es `invalid_grant`."""
    code_hash = _hash(code or "")
    ahora = _ahora()
    conn = _connect(db)
    try:
        fila = conn.execute("SELECT * FROM oauth_codigos WHERE code_hash = ?",
                            (code_hash,)).fetchone()
        if not fila:
            return None
        if fila["used_at"]:
            # Segundo canje del mismo código: alguien lo robó, o Claude reintentó.
            # En los dos casos lo emitido con él deja de valer.
            conn.execute("UPDATE oauth_tokens SET revocado_en = ? WHERE codigo_hash = ? "
                         "AND revocado_en IS NULL", (_iso(ahora), code_hash))
            conn.commit()
            return None
        if fila["expires_at"] < _iso(ahora):
            return None
        if not hmac.compare_digest(fila["client_id"], client_id or ""):
            return None
        if fila["redirect_uri"] != (redirect_uri or ""):
            return None
        if not verificar_pkce(code_verifier, fila["code_challenge"]):
            return None
        marcado = conn.execute(
            "UPDATE oauth_codigos SET used_at = ? WHERE code_hash = ? AND used_at IS NULL",
            (_iso(ahora), code_hash))
        if marcado.rowcount != 1:
            return None
        tokens = _emitir_tokens(conn, fila["user_id"], fila["client_id"], fila["scope"],
                                fila["resource"], code_hash, ahora)
        conn.commit()
        return tokens
    finally:
        conn.close()


def _emitir_tokens(conn: sqlite3.Connection, user_id: int, client_id: str, scope: str,
                   resource: Optional[str], codigo_hash: Optional[str], ahora: datetime) -> dict:
    access, refresh = _secreto(), _secreto()
    conn.execute(
        "INSERT INTO oauth_tokens (user_id, client_id, codigo_hash, access_hash, refresh_hash, "
        "scope, resource, access_expira, refresh_expira, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (user_id, client_id, codigo_hash, _hash(access), _hash(refresh), scope, resource,
         _iso(ahora + ACCESS_DURA), _iso(ahora + REFRESH_DURA), _iso(ahora)))
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": int(ACCESS_DURA.total_seconds()),
        "scope": scope,
    }


def _barrer(conn: sqlite3.Connection, ahora: datetime) -> None:
    """Borra códigos vencidos y tokens muertos hace rato. Corre al emitir, no en
    un hilo: COORDINACION.md regla 3, nada arranca solo en el boot."""
    conn.execute("DELETE FROM oauth_codigos WHERE expires_at < ?", (_iso(ahora),))
    conn.execute("DELETE FROM oauth_tokens WHERE refresh_expira < ?",
                 (_iso(ahora - BASURA_DESPUES_DE),))
```

Nota: `validar_access` lo usa un test de arriba pero se escribe en la Task 3; ese test (`test_reusar_codigo_falla_y_revoca_lo_emitido`) va a fallar con `AttributeError` hasta entonces. Es esperado.

- [ ] **Step 4: Llamar `init_oauth` desde `init_db`**

En `database.py`, dentro de `init_db`, buscar estas líneas (están cerca del final de la función):

```python
        # Cobro automático con tarjeta (services/plexo.py, 24/9).
        from services.plexo import init_plexo
        init_plexo(conn)
        conn.commit()
```

y dejarlas así:

```python
        # Cobro automático con tarjeta (services/plexo.py, 24/9).
        from services.plexo import init_plexo
        init_plexo(conn)
        # Conector MCP para Claude (15/10): tokens OAuth del equipo y registro
        # de lo que Claude llama. Ver docs/superpowers/specs/2026-10-15-conector-mcp-claude-design.md.
        from services.oauth_tokens import init_oauth
        init_oauth(conn)
        conn.commit()
```

- [ ] **Step 5: Correr los tests**

Run: `python -m pytest tests/test_oauth_tokens.py -q`
Expected: 6 passed, 1 failed (`test_reusar_codigo_falla_y_revoca_lo_emitido`, por `validar_access` que todavía no existe).

- [ ] **Step 6: Commit**

```bash
git add services/oauth_tokens.py database.py tests/test_oauth_tokens.py
git commit -m "feat(mcp): tablas OAuth, PKCE y canje de códigos"
```

---

### Task 3: Tokens OAuth — refrescar, revocar, validar

**Files:**
- Modify: `services/oauth_tokens.py` (agregar al final)
- Test: `tests/test_oauth_tokens.py` (agregar al final)

- [ ] **Step 1: Agregar los tests**

Al final de `tests/test_oauth_tokens.py`:

```python
def _tokens(db, uid, client_id="cli"):
    verifier, challenge = _pkce()
    code = ot.emitir_codigo(db, uid, client_id, "https://r/cb", challenge, "crm:leer", None)
    return ot.canjear_codigo(db, code, client_id, "https://r/cb", verifier)


def test_validar_access_devuelve_usuario_y_cliente(db, uid):
    tokens = _tokens(db, uid)
    info = ot.validar_access(db, tokens["access_token"])
    assert info["user_id"] == uid and info["client_id"] == "cli" and info["scope"] == "crm:leer"
    assert ot.validar_access(db, "no-existe") is None


def test_access_vencido_no_vale(db, uid, monkeypatch):
    tokens = _tokens(db, uid)
    futuro = datetime.now(timezone.utc) + timedelta(hours=2)
    monkeypatch.setattr(ot, "_ahora", lambda: futuro)
    assert ot.validar_access(db, tokens["access_token"]) is None


def test_refrescar_rota_y_el_viejo_deja_de_servir(db, uid):
    viejo = _tokens(db, uid)
    nuevo = ot.refrescar(db, viejo["refresh_token"], "cli")
    assert nuevo["access_token"] != viejo["access_token"]
    assert nuevo["refresh_token"] != viejo["refresh_token"]
    assert ot.validar_access(db, nuevo["access_token"]) is not None
    assert ot.validar_access(db, viejo["access_token"]) is None
    assert ot.refrescar(db, viejo["refresh_token"], "cli") is None


def test_refrescar_exige_el_mismo_cliente(db, uid):
    tokens = _tokens(db, uid)
    assert ot.refrescar(db, tokens["refresh_token"], "otro") is None


def test_revocar_mata_access_y_refresh(db, uid):
    tokens = _tokens(db, uid)
    ot.revocar(db, tokens["access_token"])
    assert ot.validar_access(db, tokens["access_token"]) is None
    assert ot.refrescar(db, tokens["refresh_token"], "cli") is None
    # Revocar algo que no existe no rompe.
    ot.revocar(db, "nada")


def test_borrar_usuario_borra_sus_tokens(db, uid):
    _tokens(db, uid)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM users WHERE id = ?", (uid,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM oauth_tokens").fetchone()[0] == 0
    conn.close()
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_oauth_tokens.py -q`
Expected: los nuevos fallan con `AttributeError: module 'services.oauth_tokens' has no attribute 'validar_access'` (o `refrescar` / `revocar`).

- [ ] **Step 3: Agregar las funciones al final de `services/oauth_tokens.py`**

```python
# ── access / refresh ─────────────────────────────────────────────────────────

def validar_access(db: str, token: str) -> Optional[dict]:
    """El usuario y cliente detrás de un access token vivo, o None.

    Actualiza `ultimo_uso` como mucho una vez por minuto: sirve para el panel
    de tokens que vendrá, y no vale la pena una escritura por llamada."""
    if not token:
        return None
    ahora = _ahora()
    conn = _connect(db)
    try:
        fila = conn.execute(
            "SELECT id, user_id, client_id, scope, access_expira, ultimo_uso FROM oauth_tokens "
            "WHERE access_hash = ? AND revocado_en IS NULL", (_hash(token),)).fetchone()
        if not fila or fila["access_expira"] < _iso(ahora):
            return None
        if not fila["ultimo_uso"] or fila["ultimo_uso"] < _iso(ahora - ULTIMO_USO_CADA):
            conn.execute("UPDATE oauth_tokens SET ultimo_uso = ? WHERE id = ?",
                         (_iso(ahora), fila["id"]))
            conn.commit()
        return {"token_id": fila["id"], "user_id": fila["user_id"],
                "client_id": fila["client_id"], "scope": fila["scope"]}
    finally:
        conn.close()


def refrescar(db: str, refresh_token: str, client_id: str) -> Optional[dict]:
    """Nuevo par access + refresh; el refresh usado queda revocado en la misma
    transacción. None es `invalid_grant`."""
    if not refresh_token:
        return None
    ahora = _ahora()
    conn = _connect(db)
    try:
        fila = conn.execute(
            "SELECT * FROM oauth_tokens WHERE refresh_hash = ? AND revocado_en IS NULL",
            (_hash(refresh_token),)).fetchone()
        if not fila or fila["refresh_expira"] < _iso(ahora):
            return None
        if not hmac.compare_digest(fila["client_id"], client_id or ""):
            return None
        conn.execute("UPDATE oauth_tokens SET revocado_en = ? WHERE id = ?",
                     (_iso(ahora), fila["id"]))
        tokens = _emitir_tokens(conn, fila["user_id"], fila["client_id"], fila["scope"],
                                fila["resource"], fila["codigo_hash"], ahora)
        _barrer(conn, ahora)
        conn.commit()
        return tokens
    finally:
        conn.close()


def revocar(db: str, token: str) -> None:
    """RFC 7009: revoca el access o refresh que coincida, y con él su pareja.
    No dice si existía: la respuesta es 200 siempre."""
    if not token:
        return
    h = _hash(token)
    conn = _connect(db)
    try:
        conn.execute(
            "UPDATE oauth_tokens SET revocado_en = ? WHERE revocado_en IS NULL "
            "AND (access_hash = ? OR refresh_hash = ?)", (_iso(_ahora()), h, h))
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_oauth_tokens.py -q`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add services/oauth_tokens.py tests/test_oauth_tokens.py
git commit -m "feat(mcp): validar, refrescar y revocar tokens OAuth"
```

---

### Task 4: Clientes OAuth — CIMD de Claude y registro dinámico

**Files:**
- Create: `services/oauth_clientes.py`
- Test: `tests/test_oauth_clientes.py`

- [ ] **Step 1: Escribir los tests**

Crear `tests/test_oauth_clientes.py`:

```python
"""Quién puede pedir un token: el CIMD publicado de Claude o un cliente
registrado por DCR. Y contra qué `redirect_uri` se compara."""

import json
import sqlite3

import pytest

from database import init_db
from services import oauth_clientes as oc

CIMD_CLAUDE = "https://claude.ai/oauth/mcp-oauth-client-metadata"
CALLBACK_CLAUDE = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "a.db")
    init_db(ruta)
    oc.limpiar_cache()
    return ruta


class _Resp:
    def __init__(self, datos, status=200):
        self._datos, self.status_code = datos, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._datos


def _mock_get(monkeypatch, datos, llamadas=None):
    def fake(url, timeout):
        if llamadas is not None:
            llamadas.append(url)
        assert timeout == 5
        return _Resp(datos)
    monkeypatch.setattr(oc.requests, "get", fake)


def test_cimd_de_claude_se_acepta_y_se_cachea(db, monkeypatch):
    llamadas = []
    _mock_get(monkeypatch, {"client_id": CIMD_CLAUDE, "redirect_uris": [CALLBACK_CLAUDE]}, llamadas)
    assert oc.redirect_uris_de(db, CIMD_CLAUDE) == [CALLBACK_CLAUDE]
    assert oc.redirect_uris_de(db, CIMD_CLAUDE) == [CALLBACK_CLAUDE]
    assert len(llamadas) == 1


def test_cimd_de_otro_host_se_rechaza_sin_descargar(db, monkeypatch):
    llamadas = []
    _mock_get(monkeypatch, {"client_id": "https://evil.test/c", "redirect_uris": ["https://evil.test/cb"]}, llamadas)
    assert oc.redirect_uris_de(db, "https://evil.test/c") is None
    assert llamadas == []


def test_cimd_cuyo_client_id_no_coincide_se_rechaza(db, monkeypatch):
    _mock_get(monkeypatch, {"client_id": "https://claude.ai/otro", "redirect_uris": [CALLBACK_CLAUDE]})
    assert oc.redirect_uris_de(db, CIMD_CLAUDE) is None


def test_cimd_que_no_responde_se_rechaza(db, monkeypatch):
    def fake(url, timeout):
        raise oc.requests.ConnectionError("sin red")
    monkeypatch.setattr(oc.requests, "get", fake)
    assert oc.redirect_uris_de(db, CIMD_CLAUDE) is None


def test_registrar_cliente_dcr(db):
    cliente = oc.registrar_cliente(db, {"client_name": "Claude", "redirect_uris": [CALLBACK_CLAUDE]})
    assert cliente["client_id"] and cliente["token_endpoint_auth_method"] == "none"
    assert cliente["redirect_uris"] == [CALLBACK_CLAUDE]
    assert oc.redirect_uris_de(db, cliente["client_id"]) == [CALLBACK_CLAUDE]


@pytest.mark.parametrize("datos", [
    {},
    {"redirect_uris": []},
    {"redirect_uris": ["ftp://x/cb"]},
    {"redirect_uris": ["http://evil.test/cb"]},
    {"redirect_uris": "https://x/cb"},
])
def test_registrar_rechaza_redirects_invalidos(db, datos):
    assert oc.registrar_cliente(db, datos) is None


def test_registrar_acepta_loopback_http(db):
    c = oc.registrar_cliente(db, {"redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"]})
    assert c is not None


def test_cliente_desconocido_es_none(db):
    assert oc.redirect_uris_de(db, "no-existe") is None
    assert oc.redirect_uris_de(db, "") is None


def test_tope_de_clientes_poda_los_viejos_sin_tokens(db):
    for i in range(oc.TOPE_CLIENTES):
        oc.registrar_cliente(db, {"client_name": f"c{i}", "redirect_uris": [CALLBACK_CLAUDE]})
    oc.registrar_cliente(db, {"client_name": "nuevo", "redirect_uris": [CALLBACK_CLAUDE]})
    conn = sqlite3.connect(db)
    total = conn.execute("SELECT COUNT(*) FROM oauth_clientes").fetchone()[0]
    conn.close()
    assert total == oc.TOPE_CLIENTES


def test_redirect_exacto_salvo_loopback_sin_puerto():
    reg = [CALLBACK_CLAUDE, "http://localhost/callback", "http://127.0.0.1/callback"]
    assert oc.redirect_permitido(CALLBACK_CLAUDE, reg)
    assert not oc.redirect_permitido("https://claude.ai/api/mcp/auth_callback/", reg)
    assert not oc.redirect_permitido("https://claude.ai/api/mcp/auth_callback?x=1", reg)
    assert oc.redirect_permitido("http://localhost:3118/callback", reg)
    assert oc.redirect_permitido("http://127.0.0.1:52000/callback", reg)
    assert not oc.redirect_permitido("http://localhost:3118/otro", reg)
    assert not oc.redirect_permitido("http://localhost:3118/callback", [CALLBACK_CLAUDE])
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_oauth_clientes.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'services.oauth_clientes'`.

- [ ] **Step 3: Crear `services/oauth_clientes.py`**

```python
"""Qué clientes OAuth acepta el CRM para el conector MCP.

Dos caminos, en este orden:

1. **CIMD** (Client ID Metadata Document): Claude se identifica con una URL
   `https://` que sirve un JSON con sus `redirect_uris`. Solo se aceptan
   documentos alojados en claude.ai o claude.com: es un CRM privado, no hace
   falta confiar en cualquier cliente de internet. Se descarga con timeout
   corto (claude.ai espera 10 s por el flujo entero) y se cachea un día.

2. **DCR** (registro dinámico, RFC 7591): si la metadata no anunciara CIMD, o
   un cliente no lo usara, Claude se registra en `/oauth/register` y queda en
   `oauth_clientes`. Hay un tope porque cada conexión nueva registra un cliente
   nuevo y la tabla crecería sin fin.

La comparación de `redirect_uri` es exacta. Única excepción: las loopback de
Claude Code (`http://localhost/callback`, `http://127.0.0.1/callback`), que
llegan con un puerto distinto cada vez y se comparan sin él (RFC 8252 §7.3).
"""

import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

import requests

from database import _connect

HOSTS_CIMD = ("claude.ai", "claude.com")
REDIRECTS_LOOPBACK = ("http://localhost/callback", "http://127.0.0.1/callback")
HOSTS_LOOPBACK = ("localhost", "127.0.0.1")
CACHE_SEGUNDOS = 24 * 3600
TIMEOUT_CIMD = 5
TOPE_CLIENTES = 200

_cache_cimd: dict[str, tuple[float, list[str]]] = {}


def limpiar_cache() -> None:
    """Para los tests."""
    _cache_cimd.clear()


def es_cimd(client_id: str) -> bool:
    partes = urlsplit(client_id or "")
    return partes.scheme == "https" and partes.hostname in HOSTS_CIMD


def _descargar_cimd(client_id: str) -> Optional[list[str]]:
    try:
        resp = requests.get(client_id, timeout=TIMEOUT_CIMD)
        resp.raise_for_status()
        doc = resp.json()
    except Exception:
        return None
    if not isinstance(doc, dict) or doc.get("client_id") != client_id:
        return None
    uris = doc.get("redirect_uris")
    if not isinstance(uris, list) or not all(isinstance(u, str) for u in uris) or not uris:
        return None
    return uris


def _cliente_dcr(db: str, client_id: str) -> Optional[list[str]]:
    conn = _connect(db)
    try:
        fila = conn.execute("SELECT redirect_uris FROM oauth_clientes WHERE client_id = ?",
                            (client_id,)).fetchone()
    finally:
        conn.close()
    if not fila:
        return None
    try:
        uris = json.loads(fila["redirect_uris"])
    except (TypeError, ValueError):
        return None
    return uris if isinstance(uris, list) else None


def redirect_uris_de(db: str, client_id: str) -> Optional[list[str]]:
    """Las `redirect_uris` registradas para ese cliente, o None si no es un
    cliente que aceptemos."""
    if not client_id:
        return None
    if es_cimd(client_id):
        entrada = _cache_cimd.get(client_id)
        if entrada and entrada[0] > time.monotonic():
            return entrada[1]
        uris = _descargar_cimd(client_id)
        if uris is not None:
            _cache_cimd[client_id] = (time.monotonic() + CACHE_SEGUNDOS, uris)
        return uris
    return _cliente_dcr(db, client_id)


def _redirect_valida(uri: str) -> bool:
    partes = urlsplit(uri)
    if partes.scheme == "https" and partes.netloc:
        return True
    return partes.scheme == "http" and partes.hostname in HOSTS_LOOPBACK


def registrar_cliente(db: str, datos: dict) -> Optional[dict]:
    """RFC 7591. None si el pedido no sirve (el endpoint responde 400)."""
    if not isinstance(datos, dict):
        return None
    uris = datos.get("redirect_uris")
    if not isinstance(uris, list) or not uris:
        return None
    if not all(isinstance(u, str) and _redirect_valida(u) for u in uris):
        return None
    nombre = datos.get("client_name")
    nombre = nombre[:120] if isinstance(nombre, str) else None
    client_id = secrets.token_urlsafe(24)
    ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = _connect(db)
    try:
        _podar(conn, ahora)
        conn.execute(
            "INSERT INTO oauth_clientes (client_id, client_name, redirect_uris, created_at) "
            "VALUES (?,?,?,?)", (client_id, nombre, json.dumps(uris), ahora))
        conn.commit()
    finally:
        conn.close()
    return {"client_id": client_id, "client_name": nombre, "redirect_uris": uris,
            "token_endpoint_auth_method": "none"}


def _podar(conn: sqlite3.Connection, ahora: str) -> None:
    """Si ya hay TOPE_CLIENTES, borra los más viejos que no tengan tokens vivos,
    los justos para que entre uno más."""
    total = conn.execute("SELECT COUNT(*) FROM oauth_clientes").fetchone()[0]
    sobran = total - TOPE_CLIENTES + 1
    if sobran <= 0:
        return
    conn.execute(
        "DELETE FROM oauth_clientes WHERE client_id IN ("
        "  SELECT c.client_id FROM oauth_clientes c"
        "  WHERE NOT EXISTS (SELECT 1 FROM oauth_tokens t WHERE t.client_id = c.client_id"
        "                    AND t.revocado_en IS NULL AND t.refresh_expira > ?)"
        "  ORDER BY c.created_at LIMIT ?)", (ahora, sobran))


def redirect_permitido(redirect_uri: str, registradas: list[str]) -> bool:
    if not redirect_uri:
        return False
    if redirect_uri in registradas:
        return True
    pedida = urlsplit(redirect_uri)
    if pedida.scheme != "http" or pedida.hostname not in HOSTS_LOOPBACK:
        return False
    for reg in registradas:
        if reg not in REDIRECTS_LOOPBACK:
            continue
        r = urlsplit(reg)
        if (pedida.hostname == r.hostname and pedida.path == r.path
                and not pedida.query and not pedida.fragment):
            return True
    return False
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_oauth_clientes.py -q`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add services/oauth_clientes.py tests/test_oauth_clientes.py
git commit -m "feat(mcp): clientes OAuth por CIMD de Claude y registro dinámico"
```

---

### Task 5: Metadata OAuth y `/oauth/register` por HTTP

**Files:**
- Create: `routes/oauth.py`
- Modify: `dashboard.py` (imports de blueprints, tupla de `register_blueprint`, `require_login`)
- Test: `tests/test_oauth_flujo.py`

- [ ] **Step 1: Escribir los tests**

Crear `tests/test_oauth_flujo.py`:

```python
"""El OAuth del conector MCP, por HTTP, como lo ve Claude.

Las URLs anunciadas salen de CRM_URL, nunca del Host del request. Los tests la
fijan en https://crm.test.
"""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import oauth_clientes as oc

BASE = "https://crm.test"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"


def _pkce():
    verifier = secrets.token_urlsafe(40)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@pytest.fixture
def ruta(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_URL", BASE)
    monkeypatch.setenv("ADMIN_EMAIL", "nadie@scalerics.invalid")
    r = str(tmp_path / "leads.db")
    init_db(r)
    oc.limpiar_cache()
    return r


@pytest.fixture
def app(ruta):
    a = dashboard.create_app(ruta)
    a.config["TESTING"] = True
    return a


@pytest.fixture
def uid(ruta):
    return create_user(ruta, name="Ana", email="ana@scalerics.com", phone="099",
                       password_hash=generate_password_hash("x" * 10))


@pytest.fixture
def anonimo(app):
    return app.test_client()


@pytest.fixture
def logueado(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Ana"
    return c


def _registrar(c):
    r = c.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [CALLBACK]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["client_id"]


# ── metadata ─────────────────────────────────────────────────────────────────

def test_metadata_del_servidor_de_autorizacion(anonimo):
    r = anonimo.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    m = r.get_json()
    assert m["issuer"] == BASE
    assert m["authorization_endpoint"] == BASE + "/oauth/authorize"
    assert m["token_endpoint"] == BASE + "/oauth/token"
    assert m["registration_endpoint"] == BASE + "/oauth/register"
    assert m["revocation_endpoint"] == BASE + "/oauth/revoke"
    assert m["code_challenge_methods_supported"] == ["S256"]
    assert m["token_endpoint_auth_methods_supported"] == ["none"]
    assert m["client_id_metadata_document_supported"] is True
    assert m["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert m["response_types_supported"] == ["code"]
    assert m["scopes_supported"] == ["crm:leer"]
    assert r.headers["Access-Control-Allow-Origin"] == "*"
    assert "max-age" in r.headers["Cache-Control"]


@pytest.mark.parametrize("path", ["/.well-known/oauth-protected-resource",
                                  "/.well-known/oauth-protected-resource/mcp"])
def test_metadata_del_recurso_protegido(anonimo, path):
    r = anonimo.get(path)
    assert r.status_code == 200
    m = r.get_json()
    assert m["resource"] == BASE + "/mcp"
    assert m["authorization_servers"] == [BASE]
    assert m["bearer_methods_supported"] == ["header"]
    assert m["scopes_supported"] == ["crm:leer"]


def test_metadata_no_pide_sesion_ni_redirige(anonimo):
    r = anonimo.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200 and r.is_json


# ── registro ─────────────────────────────────────────────────────────────────

def test_register_crea_cliente_publico(anonimo):
    r = anonimo.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [CALLBACK]})
    assert r.status_code == 201
    c = r.get_json()
    assert c["client_id"] and c["token_endpoint_auth_method"] == "none"
    assert c["redirect_uris"] == [CALLBACK]


def test_register_sin_redirects_es_400(anonimo):
    r = anonimo.post("/oauth/register", json={"client_name": "x"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_redirect_uri"


def test_register_sin_json_es_400(anonimo):
    r = anonimo.post("/oauth/register", data="no es json", content_type="text/plain")
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_client_metadata"
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_oauth_flujo.py -q`
Expected: todos fallan. Los de metadata y registro reciben `302` al login (la ruta no existe y `require_login` redirige) o `404`.

- [ ] **Step 3: Crear `routes/oauth.py` con metadata y registro**

```python
"""Servidor de autorización OAuth 2.1 del conector MCP para Claude.

Es el lado HTTP: valida y delega en `services/oauth_clientes.py` (quién es el
cliente) y `services/oauth_tokens.py` (códigos y tokens). Las reglas que
claude.ai exige están en el spec y en
https://claude.com/docs/connectors/building/authentication.

Estas rutas están exentas del candado de sesión de `dashboard.require_login`:
cada una aplica su propia autenticación. `/oauth/authorize` es la única que
pide la sesión del CRM, porque ahí la persona autoriza a Claude.
"""

import hmac
import secrets
from urllib.parse import urlencode

from flask import (Blueprint, current_app, jsonify, redirect, render_template_string,
                   request, session, url_for)

from services import oauth_clientes, oauth_tokens
from services.oauth_tokens import SCOPE_LECTURA, base_url, recurso_mcp

oauth_bp = Blueprint("oauth", __name__)

CACHE_METADATA = "public, max-age=3600"


def _db() -> str:
    return current_app.config["DB_PATH"]


def _json_publico(datos: dict):
    resp = jsonify(datos)
    resp.headers["Cache-Control"] = CACHE_METADATA
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return resp


def _error_json(codigo: str, descripcion: str, estado: int = 400):
    resp = jsonify({"error": codigo, "error_description": descripcion})
    resp.status_code = estado
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Pragma"] = "no-cache"
    return resp


# ── metadata ─────────────────────────────────────────────────────────────────

@oauth_bp.route("/.well-known/oauth-authorization-server")
def metadata_servidor():
    base = base_url()
    return _json_publico({
        "issuer": base,
        "authorization_endpoint": base + "/oauth/authorize",
        "token_endpoint": base + "/oauth/token",
        "registration_endpoint": base + "/oauth/register",
        "revocation_endpoint": base + "/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        # Claude usa su identidad publicada (CIMD) solo si ve estas dos.
        "token_endpoint_auth_methods_supported": ["none"],
        "client_id_metadata_document_supported": True,
        "scopes_supported": [SCOPE_LECTURA],
    })


@oauth_bp.route("/.well-known/oauth-protected-resource")
@oauth_bp.route("/.well-known/oauth-protected-resource/mcp")
def metadata_recurso():
    return _json_publico({
        "resource": recurso_mcp(),
        "authorization_servers": [base_url()],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [SCOPE_LECTURA],
    })


# ── registro dinámico ────────────────────────────────────────────────────────

@oauth_bp.route("/oauth/register", methods=["POST"])
def registrar():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        return _error_json("invalid_client_metadata", "El cuerpo tiene que ser JSON")
    cliente = oauth_clientes.registrar_cliente(_db(), datos)
    if cliente is None:
        return _error_json("invalid_redirect_uri",
                           "redirect_uris tiene que ser una lista de URLs https (o loopback)")
    resp = jsonify(cliente)
    resp.status_code = 201
    resp.headers["Cache-Control"] = "no-store"
    return resp
```

- [ ] **Step 4: Registrar el blueprint y eximir las rutas en `dashboard.py`**

Tres cambios:

**(a)** Después de la última línea `from routes.<x> import <x>_bp` (buscarla con `grep -n "^from routes" dashboard.py | tail -1`), agregar:

```python
from routes.oauth import oauth_bp
```

**(b)** En la tupla del `for bp in (leads_bp, ...)` dentro de `create_app`, agregar `oauth_bp` al final. La última línea de la tupla hoy es:

```python
                credenciales_bp, fidelidad_bp, plexo_bp, buscar_bp):
```

y queda:

```python
                credenciales_bp, fidelidad_bp, plexo_bp, buscar_bp, oauth_bp):
```

**(c)** En `require_login` (el `@app.before_request` de `create_app`), justo después de la primera comprobación

```python
        if request.endpoint in ("login", "logout", "register", "forgot_password", "reset_password", "static", "privacidad", "baja_recordatorios"):
            return
```

agregar:

```python
        # Conector MCP para Claude: metadata pública, OAuth y el endpoint MCP.
        # Ninguna usa la sesión del CRM salvo /oauth/authorize, que la pide
        # adentro; /mcp valida su propio Bearer (routes/mcp.py).
        if request.path.startswith(("/mcp", "/oauth/", "/.well-known/")):
            return
```

- [ ] **Step 5: Correr los tests**

Run: `python -m pytest tests/test_oauth_flujo.py -q`
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add routes/oauth.py dashboard.py tests/test_oauth_flujo.py
git commit -m "feat(mcp): metadata OAuth y registro dinámico de clientes"
```

---

### Task 6: `/oauth/authorize` con consentimiento, y `next` en `/login`

**Files:**
- Modify: `routes/oauth.py` (agregar al final)
- Modify: `dashboard.py` (`LOGIN_HTML` y la función `login`)
- Test: `tests/test_oauth_flujo.py` (agregar)

- [ ] **Step 1: Agregar los tests**

Al final de `tests/test_oauth_flujo.py`:

```python
# ── autorización ─────────────────────────────────────────────────────────────

def _params_authorize(client_id, challenge, **extra):
    p = {"response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
         "code_challenge": challenge, "code_challenge_method": "S256",
         "state": "abc123", "scope": "crm:leer", "resource": BASE + "/mcp"}
    p.update(extra)
    return p


def _query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_authorize_sin_sesion_manda_al_login_con_next(anonimo):
    cid = _registrar(anonimo)
    _, ch = _pkce()
    r = anonimo.get("/oauth/authorize?" + urlencode(_params_authorize(cid, ch)))
    assert r.status_code == 302
    assert r.headers["Location"].startswith("/login?next=")
    assert "%2Foauth%2Fauthorize" in r.headers["Location"]


def test_authorize_con_sesion_muestra_consentimiento(logueado):
    cid = _registrar(logueado)
    _, ch = _pkce()
    r = logueado.get("/oauth/authorize?" + urlencode(_params_authorize(cid, ch)))
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Ana" in html
    assert "claude.ai" in html          # el host de destino, visible
    assert 'name="nonce"' in html
    assert "leer leads" in html.lower()


def test_cliente_desconocido_no_redirige(logueado):
    _, ch = _pkce()
    r = logueado.get("/oauth/authorize?" + urlencode(_params_authorize("no-existe", ch)))
    assert r.status_code == 400
    assert "Location" not in r.headers


def test_redirect_ajena_no_redirige(logueado):
    cid = _registrar(logueado)
    _, ch = _pkce()
    r = logueado.get("/oauth/authorize?" + urlencode(
        _params_authorize(cid, ch, redirect_uri="https://evil.test/cb")))
    assert r.status_code == 400
    assert "Location" not in r.headers


@pytest.mark.parametrize("cambio, error", [
    ({"response_type": "token"}, "unsupported_response_type"),
    ({"code_challenge": ""}, "invalid_request"),
    ({"code_challenge_method": "plain"}, "invalid_request"),
    ({"scope": "crm:escribir"}, "invalid_scope"),
    ({"resource": "https://otro.test/mcp"}, "invalid_target"),
])
def test_errores_de_parametros_vuelven_al_redirect_con_state(logueado, cambio, error):
    cid = _registrar(logueado)
    _, ch = _pkce()
    r = logueado.get("/oauth/authorize?" + urlencode(_params_authorize(cid, ch, **cambio)))
    assert r.status_code == 302
    assert r.headers["Location"].startswith(CALLBACK + "?")
    q = _query(r.headers["Location"])
    assert q["error"] == error and q["state"] == "abc123"


def _autorizar(c, cid, challenge, accion="autorizar"):
    r = c.get("/oauth/authorize?" + urlencode(_params_authorize(cid, challenge)))
    html = r.get_data(as_text=True)
    nonce = html.split('name="nonce" value="')[1].split('"')[0]
    form = _params_authorize(cid, challenge)
    form.update({"nonce": nonce, "accion": accion})
    return c.post("/oauth/authorize", data=form)


def test_autorizar_devuelve_code_y_state(logueado):
    cid = _registrar(logueado)
    _, ch = _pkce()
    r = _autorizar(logueado, cid, ch)
    assert r.status_code == 302
    assert r.headers["Location"].startswith(CALLBACK + "?")
    q = _query(r.headers["Location"])
    assert q["code"] and q["state"] == "abc123"


def test_cancelar_devuelve_access_denied(logueado):
    cid = _registrar(logueado)
    _, ch = _pkce()
    r = _autorizar(logueado, cid, ch, accion="cancelar")
    q = _query(r.headers["Location"])
    assert q["error"] == "access_denied" and q["state"] == "abc123"


def test_post_sin_nonce_valido_es_403(logueado):
    cid = _registrar(logueado)
    _, ch = _pkce()
    logueado.get("/oauth/authorize?" + urlencode(_params_authorize(cid, ch)))
    form = _params_authorize(cid, ch)
    form.update({"nonce": "inventado", "accion": "autorizar"})
    r = logueado.post("/oauth/authorize", data=form)
    assert r.status_code == 403


def test_post_sin_sesion_es_302_al_login(anonimo):
    cid = _registrar(anonimo)
    _, ch = _pkce()
    form = _params_authorize(cid, ch)
    form.update({"nonce": "x", "accion": "autorizar"})
    r = anonimo.post("/oauth/authorize", data=form)
    assert r.status_code == 302 and r.headers["Location"].startswith("/login")


# ── next en /login ───────────────────────────────────────────────────────────

def test_login_vuelve_a_authorize_despues_de_entrar(anonimo, uid):
    destino = "/oauth/authorize?client_id=x&state=1"
    r = anonimo.post("/login?next=" + destino.replace("?", "%3F").replace("&", "%26"),
                     data={"email": "ana@scalerics.com", "password": "x" * 10})
    assert r.status_code == 302
    assert r.headers["Location"] == destino


def test_login_ignora_next_que_no_sea_authorize(anonimo, uid):
    for malo in ("https://evil.test/", "//evil.test/", "/admin/users"):
        r = anonimo.post("/login?next=" + malo, data={"email": "ana@scalerics.com", "password": "x" * 10})
        assert r.status_code == 302
        assert r.headers["Location"] in ("/", "http://localhost/")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_oauth_flujo.py -q`
Expected: los nuevos fallan con `404`/`405` en `/oauth/authorize` y, los de login, con `Location == "/"`.

- [ ] **Step 3: Agregar `authorize` al final de `routes/oauth.py`**

```python
# ── autorización ─────────────────────────────────────────────────────────────

class _ErrorPagina(Exception):
    """No se puede confiar en la redirect_uri: se muestra la página, no se redirige."""


class _ErrorRedirect(Exception):
    def __init__(self, codigo: str, descripcion: str):
        super().__init__(descripcion)
        self.codigo, self.descripcion = codigo, descripcion


def _validar_pedido(params) -> dict:
    """Valida un pedido de autorización (GET o el form del POST, que repite los
    mismos campos). Primero cliente y redirect, que deciden si se puede
    redirigir; después el resto, que vuelve al cliente como error OAuth."""
    client_id = (params.get("client_id") or "").strip()
    redirect_uri = (params.get("redirect_uri") or "").strip()
    registradas = oauth_clientes.redirect_uris_de(_db(), client_id)
    if registradas is None:
        raise _ErrorPagina("Cliente desconocido. Volvé a agregar el conector en Claude.")
    if not oauth_clientes.redirect_permitido(redirect_uri, registradas):
        raise _ErrorPagina("La dirección de vuelta no es una de las registradas por el cliente.")
    if params.get("response_type") != "code":
        raise _ErrorRedirect("unsupported_response_type", "Solo response_type=code")
    challenge = (params.get("code_challenge") or "").strip()
    if not challenge or params.get("code_challenge_method") != "S256":
        raise _ErrorRedirect("invalid_request", "PKCE S256 es obligatorio")
    scope = (params.get("scope") or SCOPE_LECTURA).strip()
    if set(scope.split()) != {SCOPE_LECTURA}:
        raise _ErrorRedirect("invalid_scope", f"El único scope es {SCOPE_LECTURA}")
    resource = (params.get("resource") or recurso_mcp()).strip()
    if resource != recurso_mcp():
        raise _ErrorRedirect("invalid_target", f"El recurso es {recurso_mcp()}")
    return {"client_id": client_id, "redirect_uri": redirect_uri, "challenge": challenge,
            "scope": SCOPE_LECTURA, "resource": resource, "state": params.get("state") or ""}


def _volver(redirect_uri: str, **campos):
    sep = "&" if "?" in redirect_uri else "?"
    return redirect(redirect_uri + sep + urlencode({k: v for k, v in campos.items() if v}))


@oauth_bp.route("/oauth/authorize", methods=["GET", "POST"])
def autorizar():
    params = request.form if request.method == "POST" else request.args
    try:
        pedido = _validar_pedido(params)
    except _ErrorPagina as e:
        return render_template_string(ERROR_HTML, mensaje=str(e)), 400
    except _ErrorRedirect as e:
        redirect_uri = params.get("redirect_uri")
        return _volver(redirect_uri, error=e.codigo, error_description=e.descripcion,
                       state=params.get("state"))

    if not session.get("logged_in") or not session.get("user_id"):
        # Volver acá después de entrar. /login solo honra next si empieza con
        # /oauth/authorize, así que no sirve para mandar a nadie a otro lado.
        vuelta = request.full_path if request.method == "GET" else url_for("oauth.autorizar")
        return redirect(url_for("login", next=vuelta))

    if request.method == "GET":
        nonce = secrets.token_urlsafe(16)
        session["oauth_nonce"] = nonce
        return render_template_string(
            CONSENTIMIENTO_HTML, pedido=pedido, nonce=nonce,
            usuario=session.get("user_name") or "",
            host_destino=urlsplit(pedido["redirect_uri"]).netloc)

    esperado = session.pop("oauth_nonce", None)
    if not esperado or not hmac.compare_digest(esperado, request.form.get("nonce", "")):
        return render_template_string(ERROR_HTML, mensaje="El formulario venció. Volvé a intentar desde Claude."), 403
    if request.form.get("accion") != "autorizar":
        return _volver(pedido["redirect_uri"], error="access_denied",
                       error_description="La persona canceló", state=pedido["state"])
    code = oauth_tokens.emitir_codigo(_db(), session["user_id"], pedido["client_id"],
                                      pedido["redirect_uri"], pedido["challenge"],
                                      pedido["scope"], pedido["resource"])
    return _volver(pedido["redirect_uri"], code=code, state=pedido["state"])


_ESTILO = """
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#111827;border:1px solid #1e293b;border-radius:16px;padding:40px 36px;width:420px;max-width:92vw}
.logo-wrap{margin-bottom:24px;display:flex;flex-direction:column;align-items:flex-start;gap:10px}
.logo-wrap img{height:44px;object-fit:contain}
.logo-sub{font-size:.75rem;color:#475569}
h1{font-size:1.1rem;margin-bottom:12px}
p{font-size:.9rem;color:#94a3b8;line-height:1.5;margin-bottom:12px}
ul{margin:0 0 18px 18px;font-size:.9rem;color:#cbd5e1}
.host{font-family:monospace;color:#e2e8f0}
.botones{display:flex;gap:10px;margin-top:8px}
button{flex:1;font-size:.88rem;font-weight:700;padding:12px;border-radius:8px;border:none;cursor:pointer;font-family:'Inter',sans-serif}
.ok{background:linear-gradient(135deg,#0088cc,#3db648);color:#fff}
.no{background:#1e293b;color:#cbd5e1}
"""

CONSENTIMIENTO_HTML = """<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Scalerics — Autorizar a Claude</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<style>""" + _ESTILO + """</style></head>
<body><div class="card">
  <div class="logo-wrap">
    <img src="https://raw.githubusercontent.com/Scalerics-org/scalerics-assets/main/logo_full_alt.png" alt="Scalerics">
    <span class="logo-sub">CRM interno</span>
  </div>
  <h1>Claude quiere entrar al CRM como {{ usuario }}</h1>
  <p>Si autorizás, Claude va a poder, en tu nombre:</p>
  <ul>
    <li>Leer leads y el pipeline (buscar, ver fichas, historial)</li>
  </ul>
  <p>No va a poder modificar nada. Cada consulta queda registrada con tu usuario.</p>
  <p>Después de autorizar vas a volver a <span class="host">{{ host_destino }}</span>.</p>
  <form method="POST" action="/oauth/authorize">
    {% for k in ("response_type", "client_id", "redirect_uri", "code_challenge", "code_challenge_method", "state", "scope", "resource") %}
    <input type="hidden" name="{{ k }}" value="{{ request.values.get(k, '') }}">
    {% endfor %}
    <input type="hidden" name="nonce" value="{{ nonce }}">
    <div class="botones">
      <button class="no" type="submit" name="accion" value="cancelar">Cancelar</button>
      <button class="ok" type="submit" name="accion" value="autorizar">Autorizar</button>
    </div>
  </form>
</div></body></html>"""

ERROR_HTML = """<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Scalerics — No se pudo autorizar</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<style>""" + _ESTILO + """</style></head>
<body><div class="card">
  <h1>No se pudo autorizar a Claude</h1>
  <p>{{ mensaje }}</p>
</div></body></html>"""
```

Y agregar `urlsplit` al import de `urllib.parse` al principio del archivo:

```python
from urllib.parse import urlencode, urlsplit
```

- [ ] **Step 4: `next` en `/login` (`dashboard.py`)**

**(a)** En `LOGIN_HTML`, el formulario empieza con:

```html
  <form method="POST">
    <label>Email</label>
```

Verificar que ese par de líneas es único en `LOGIN_HTML` (`grep -n '<form method="POST">' dashboard.py`: la primera coincidencia después de la línea `LOGIN_HTML = """` es la del login). Reemplazarlo por:

```html
  <form method="POST">
    <input type="hidden" name="next" value="{{ next or '' }}">
    <label>Email</label>
```

**(b)** La función `login` dentro de `create_app` hoy es:

```python
    def login():
        from werkzeug.security import check_password_hash
        from database import get_user_by_email
        error = None
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = get_user_by_email(db_path, email)
            if user and check_password_hash(user["password"], password):
                session["logged_in"] = True
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                return redirect(url_for("index"))
            error = "Email o contraseña incorrectos"
        return render_template_string(LOGIN_HTML, error=error)
```

Dejarla así:

```python
    def login():
        from werkzeug.security import check_password_hash
        from database import get_user_by_email
        error = None
        # Solo la pantalla de autorización del conector MCP puede pedir volver
        # a algún lado después de entrar. Cualquier otro `next` se ignora: así
        # el login no sirve para mandar a nadie a un sitio ajeno.
        destino = request.args.get("next") or request.form.get("next") or ""
        if not destino.startswith("/oauth/authorize"):
            destino = ""
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = get_user_by_email(db_path, email)
            if user and check_password_hash(user["password"], password):
                session["logged_in"] = True
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                return redirect(destino or url_for("index"))
            error = "Email o contraseña incorrectos"
        return render_template_string(LOGIN_HTML, error=error, next=destino)
```

- [ ] **Step 5: Correr los tests**

Run: `python -m pytest tests/test_oauth_flujo.py -q`
Expected: 22 passed. Si `test_login_ignora_next_que_no_sea_authorize` falla por el valor exacto de `Location`, mirar qué devuelve `url_for("index")` en el test y ajustar la tupla aceptada; lo que importa es que no sea el `next` malicioso.

También: `python -m pytest tests/test_registro.py tests/test_auth_db.py -q` para confirmar que el login sigue andando para el resto.
Expected: passed.

- [ ] **Step 6: Commit**

```bash
git add routes/oauth.py dashboard.py tests/test_oauth_flujo.py
git commit -m "feat(mcp): pantalla de autorización OAuth con consentimiento"
```

---

### Task 7: `/oauth/token` y `/oauth/revoke`

**Files:**
- Modify: `routes/oauth.py` (agregar al final)
- Test: `tests/test_oauth_flujo.py` (agregar)

- [ ] **Step 1: Agregar los tests**

Al final de `tests/test_oauth_flujo.py`:

```python
# ── token ────────────────────────────────────────────────────────────────────

def _code(c, cid, challenge):
    r = _autorizar(c, cid, challenge)
    return _query(r.headers["Location"])["code"]


def _canjear(c, cid, code, verifier, **extra):
    form = {"grant_type": "authorization_code", "code": code, "client_id": cid,
            "redirect_uri": CALLBACK, "code_verifier": verifier}
    form.update(extra)
    return c.post("/oauth/token", data=form)


def test_flujo_completo_hasta_el_token(logueado):
    cid = _registrar(logueado)
    verifier, challenge = _pkce()
    code = _code(logueado, cid, challenge)
    r = _canjear(logueado, cid, code, verifier)
    assert r.status_code == 200, r.get_json()
    t = r.get_json()
    assert t["token_type"] == "Bearer" and t["expires_in"] == 3600
    assert t["access_token"] and t["refresh_token"] and t["scope"] == "crm:leer"
    assert r.headers["Cache-Control"] == "no-store"


def test_token_con_verifier_incorrecto_es_invalid_grant(logueado):
    cid = _registrar(logueado)
    _, challenge = _pkce()
    code = _code(logueado, cid, challenge)
    r = _canjear(logueado, cid, code, "otro")
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"


def test_reusar_el_codigo_es_invalid_grant(logueado):
    cid = _registrar(logueado)
    verifier, challenge = _pkce()
    code = _code(logueado, cid, challenge)
    assert _canjear(logueado, cid, code, verifier).status_code == 200
    r = _canjear(logueado, cid, code, verifier)
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"


def test_refresh_rota(logueado):
    cid = _registrar(logueado)
    verifier, challenge = _pkce()
    t = _canjear(logueado, cid, _code(logueado, cid, challenge), verifier).get_json()
    r = logueado.post("/oauth/token", data={"grant_type": "refresh_token",
                                            "refresh_token": t["refresh_token"], "client_id": cid})
    assert r.status_code == 200
    t2 = r.get_json()
    assert t2["refresh_token"] != t["refresh_token"]
    r = logueado.post("/oauth/token", data={"grant_type": "refresh_token",
                                            "refresh_token": t["refresh_token"], "client_id": cid})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"


def test_token_con_json_es_invalid_request(logueado):
    r = logueado.post("/oauth/token", json={"grant_type": "authorization_code"})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_request"


def test_grant_desconocido(logueado):
    r = logueado.post("/oauth/token", data={"grant_type": "client_credentials"})
    assert r.status_code == 400 and r.get_json()["error"] == "unsupported_grant_type"


def test_revoke_invalida(logueado):
    cid = _registrar(logueado)
    verifier, challenge = _pkce()
    t = _canjear(logueado, cid, _code(logueado, cid, challenge), verifier).get_json()
    r = logueado.post("/oauth/revoke", data={"token": t["access_token"]})
    assert r.status_code == 200
    r = logueado.post("/oauth/token", data={"grant_type": "refresh_token",
                                            "refresh_token": t["refresh_token"], "client_id": cid})
    assert r.get_json()["error"] == "invalid_grant"
    assert logueado.post("/oauth/revoke", data={"token": "nada"}).status_code == 200
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_oauth_flujo.py -q`
Expected: los nuevos fallan con `404`/`405`.

- [ ] **Step 3: Agregar `token` y `revoke` al final de `routes/oauth.py`**

```python
# ── token ────────────────────────────────────────────────────────────────────

@oauth_bp.route("/oauth/token", methods=["POST"])
def token():
    # RFC 6749 §4.1.3: form-urlencoded. Claude manda eso; un JSON acá es un
    # cliente mal armado, y la respuesta correcta es 400, no 415.
    if request.mimetype != "application/x-www-form-urlencoded":
        return _error_json("invalid_request", "El cuerpo tiene que ser application/x-www-form-urlencoded")
    form = request.form
    grant = form.get("grant_type", "")
    if grant == "authorization_code":
        tokens = oauth_tokens.canjear_codigo(_db(), form.get("code", ""), form.get("client_id", ""),
                                             form.get("redirect_uri", ""), form.get("code_verifier", ""))
    elif grant == "refresh_token":
        tokens = oauth_tokens.refrescar(_db(), form.get("refresh_token", ""), form.get("client_id", ""))
    else:
        return _error_json("unsupported_grant_type", "Solo authorization_code y refresh_token")
    if tokens is None:
        return _error_json("invalid_grant", "Código o refresh inválido, vencido o ya usado")
    resp = jsonify({**tokens, "token_type": "Bearer"})
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Pragma"] = "no-cache"
    return resp


@oauth_bp.route("/oauth/revoke", methods=["POST"])
def revocar():
    oauth_tokens.revocar(_db(), request.form.get("token", ""))
    resp = jsonify({})
    resp.headers["Cache-Control"] = "no-store"
    return resp
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_oauth_flujo.py -q`
Expected: 29 passed.

- [ ] **Step 5: Commit**

```bash
git add routes/oauth.py tests/test_oauth_flujo.py
git commit -m "feat(mcp): endpoints de token y revocación OAuth"
```

---

### Task 8: Herramientas MCP — registro, validación de argumentos y tabla de llamadas

**Files:**
- Create: `services/mcp_tools.py`
- Modify: `database.py` (al lado de `init_oauth`)
- Test: `tests/test_mcp.py`

- [ ] **Step 1: Escribir los tests**

Crear `tests/test_mcp.py`:

```python
"""Las herramientas del conector MCP y el endpoint /mcp.

La base se siembra a mano con leads en distintas etapas. Los tests de las
herramientas llaman a `services.mcp_tools` directo; los del transporte pasan
por el test_client de Flask con un Bearer real.
"""

import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import mcp_tools as mt
from services import oauth_tokens as ot


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    monkeypatch.setenv("ADMIN_EMAIL", "nadie@scalerics.invalid")
    r = str(tmp_path / "leads.db")
    init_db(r)
    return r


@pytest.fixture
def ana(db):
    return create_user(db, name="Ana", email="ana@scalerics.com", phone="099",
                       password_hash=generate_password_hash("x" * 10))


@pytest.fixture
def juan(db):
    return create_user(db, name="Juan", email="juan@scalerics.com", phone="098",
                       password_hash=generate_password_hash("x" * 10))


@pytest.fixture
def sembrada(db, ana, juan):
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO businesses (id, name, phone, email, city, category, crm_status, source, "
        "encargado_id, last_event_at, archivado_en, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "Panadería La Espiga", "099111222", "espiga@mail.com", "Montevideo", "panaderia",
             "demo_1", "meta", ana, "2026-10-01 10:00:00", None, "Quiere web con pedidos"),
            (2, "Ferretería Norte", "099333444", None, "Salto", "ferreteria",
             "presupuesto_enviado", None, juan, "2026-09-20 10:00:00", None, None),
            (3, "Bar Espiga", "099555666", "bar@mail.com", "Montevideo", "bar",
             "sin_contactar", None, None, None, None, None),
            (4, "Archivado Viejo", "099777888", "viejo@mail.com", "Rivera", "bar",
             "demo_1", None, None, None, "2026-09-23", None),
            (5, "Cliente Cerrado", "099999000", "c@mail.com", "Rivera", "bar",
             "cerrado", None, ana, "2026-08-01 10:00:00", None, None),
        ])
    conn.execute("INSERT INTO client_info (client_id, lead_name, rubro, needs) VALUES (1,'Lucía','panadería','pedidos online')")
    # created_at explícito: get_lead_events ordena por esa columna y dos
    # inserts en el mismo segundo dejarían el orden librado a SQLite.
    conn.executemany("INSERT INTO lead_events (lead_id, new_status, note, created_by, created_at) VALUES (?,?,?,?,?)",
                     [(1, "demo_agendada", "agendada para el martes", "Ana", "2026-09-25 10:00:00"),
                      (1, "demo_1", "demo dada", "Ana", "2026-10-01 10:00:00")])
    conn.execute("INSERT INTO call_logs (lead_id, outcome, notes, created_by) VALUES (1,'contestó','habló Lucía','Ana')")
    conn.commit()
    conn.close()
    return db


# ── registro de herramientas ─────────────────────────────────────────────────

def test_init_db_crea_mcp_llamadas(db):
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='mcp_llamadas'").fetchone()
    conn.close()


def test_listar_herramientas_tiene_las_tres_con_schema():
    hs = {h["name"]: h for h in mt.listar_herramientas()}
    assert set(hs) == {"buscar_leads", "ver_lead", "pipeline"}
    for h in hs.values():
        assert h["description"]
        assert h["inputSchema"]["type"] == "object"
        assert h["inputSchema"]["additionalProperties"] is False
        assert "fn" not in h
    assert hs["buscar_leads"]["inputSchema"]["required"] == ["texto"]
    assert hs["ver_lead"]["inputSchema"]["required"] == ["lead_id"]
    assert "demo_agendada" in hs["pipeline"]["description"]


@pytest.mark.parametrize("args", [
    {},                                  # falta texto
    {"texto": "a"},                      # muy corto
    {"texto": "ab", "limite": 0},        # fuera de rango
    {"texto": "ab", "limite": 51},
    {"texto": "ab", "limite": "5"},      # tipo
    {"texto": 5},
    {"texto": "ab", "otro": 1},          # additionalProperties
])
def test_validar_argumentos_rechaza(args):
    schema = {h["name"]: h for h in mt.listar_herramientas()}["buscar_leads"]["inputSchema"]
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.validar_argumentos(schema, args)


def test_validar_argumentos_completa_defaults():
    schema = {h["name"]: h for h in mt.listar_herramientas()}["buscar_leads"]["inputSchema"]
    assert mt.validar_argumentos(schema, {"texto": "espiga"}) == {"texto": "espiga", "limite": 10}


def test_validar_enum_de_etapa():
    schema = {h["name"]: h for h in mt.listar_herramientas()}["pipeline"]["inputSchema"]
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.validar_argumentos(schema, {"etapa": "inventada"})


def test_ejecutar_herramienta_desconocida(sembrada, ana):
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "borrar_todo", {})


def test_ejecutar_registra_la_llamada(sembrada, ana):
    r = mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads", {"texto": "espiga"})
    assert r["isError"] is False
    conn = sqlite3.connect(sembrada)
    fila = conn.execute("SELECT user_id, user_name, herramienta, argumentos, ok, ms FROM mcp_llamadas").fetchone()
    conn.close()
    assert fila[0] == ana and fila[1] == "Ana" and fila[2] == "buscar_leads"
    assert json.loads(fila[3]) == {"texto": "espiga", "limite": 10}
    assert fila[4] == 1 and fila[5] >= 0
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_mcp.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'services.mcp_tools'`.

- [ ] **Step 3: Crear `services/mcp_tools.py`** (sin las tres herramientas todavía: se agregan en las tareas 9 a 11; acá quedan como funciones que devuelven `{}` para que el registro exista)

```python
"""Las herramientas que el CRM le ofrece a Claude por MCP.

Solo lectura, leads y pipeline. Cada herramienta es un dict con `name`,
`description`, `inputSchema` (JSON Schema) y `fn`. La validación de argumentos
es propia y chica: tipos básicos, requeridos, rangos, enum y
`additionalProperties: false`. No vale la pena una librería para tres schemas.

Dos clases de error, y la diferencia importa para Claude:

- `ArgumentosInvalidos`: el pedido está mal armado (argumento que falta, tipo
  equivocado, herramienta que no existe). Vuelve como error JSON-RPC -32602.
- `ErrorHerramienta`: el pedido estaba bien pero no hay resultado (lead que no
  existe). Vuelve como `isError: true` con texto, que Claude lee y explica.

Cada llamada deja una fila en `mcp_llamadas`: quién, qué, con qué, si salió,
cuánto tardó. Si registrar falla, la llamada sale igual (mismo criterio que
`database.log_activity`).
"""

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from database import (ETAPAS_CLIENTE, ETAPAS_PRECLIENTE, _connect, get_business,
                      get_client_info, get_lead_events)

logger = logging.getLogger(__name__)

TOPE_LEADS = 200
TOPE_HISTORIAL = 20


class ArgumentosInvalidos(Exception):
    pass


class ErrorHerramienta(Exception):
    pass


def init_mcp(conn: sqlite3.Connection) -> None:
    """Tabla de llamadas. La llama `database.init_db` con su conexión."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mcp_llamadas (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER,
            user_name    TEXT NOT NULL,
            herramienta  TEXT NOT NULL,
            argumentos   TEXT,
            ok           INTEGER NOT NULL,
            error        TEXT,
            ms           INTEGER,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


# ── validación ───────────────────────────────────────────────────────────────

_TIPOS = {"string": str, "integer": int, "boolean": bool}


def validar_argumentos(schema: dict, args) -> dict:
    """Devuelve los argumentos con los defaults completados, o levanta
    ArgumentosInvalidos con un mensaje que dice qué está mal."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ArgumentosInvalidos("arguments tiene que ser un objeto")
    props = schema.get("properties", {})
    sobran = set(args) - set(props)
    if sobran:
        raise ArgumentosInvalidos(f"Argumentos desconocidos: {', '.join(sorted(sobran))}")
    for req in schema.get("required", []):
        if req not in args:
            raise ArgumentosInvalidos(f"Falta el argumento obligatorio '{req}'")
    salida = {}
    for nombre, prop in props.items():
        if nombre not in args:
            if "default" in prop:
                salida[nombre] = prop["default"]
            continue
        valor = args[nombre]
        if valor is None:
            # Un null explícito cuenta como ausente: error si era obligatorio,
            # default si lo tenía, y si no, se omite.
            if nombre in schema.get("required", []):
                raise ArgumentosInvalidos(f"'{nombre}' no puede ser null")
            if "default" in prop:
                salida[nombre] = prop["default"]
            continue
        tipo = _TIPOS[prop["type"]]
        # bool es subclase de int en Python: un True no es un integer válido.
        if not isinstance(valor, tipo) or (tipo is int and isinstance(valor, bool)):
            raise ArgumentosInvalidos(f"'{nombre}' tiene que ser {prop['type']}")
        if tipo is str and len(valor) < prop.get("minLength", 0):
            raise ArgumentosInvalidos(f"'{nombre}' tiene que tener al menos {prop['minLength']} caracteres")
        if tipo is int and ("minimum" in prop and valor < prop["minimum"]
                            or "maximum" in prop and valor > prop["maximum"]):
            raise ArgumentosInvalidos(f"'{nombre}' tiene que estar entre {prop.get('minimum')} y {prop.get('maximum')}")
        if "enum" in prop and valor not in prop["enum"]:
            raise ArgumentosInvalidos(f"'{nombre}' tiene que ser una de: {', '.join(prop['enum'])}")
        salida[nombre] = valor
    return salida


# ── herramientas ─────────────────────────────────────────────────────────────

ETAPAS_DESCRIPCION = {
    "demo_agendada": "demo pactada, todavía no se dio",
    "no_se_conecto": "estaba agendada y el lead no se conectó, hay que reagendar",
    "demo_1": "primera demo dada",
    "demo_2": "segunda demo",
    "demo_3": "tercera demo",
    "presupuesto_enviado": "se mandó el presupuesto",
    "follow_up_1": "primer seguimiento después del presupuesto",
    "follow_up_2": "segundo seguimiento",
    "acepto": "dijo que sí, falta firma o pago",
    "en_espera": "frenado por el cliente, sin cerrar",
    "rechazo": "dijo que no después de haber avanzado",
    "cerrado": "cliente: el acuerdo se concretó",
    "en_desarrollo": "cliente: el proyecto está en desarrollo",
    "finalizado": "cliente: proyecto entregado",
}

_ETAPAS_TODAS = tuple(ETAPAS_PRECLIENTE) + tuple(ETAPAS_CLIENTE)


def _descripcion_pipeline() -> str:
    lineas = "; ".join(f"{e} ({ETAPAS_DESCRIPCION.get(e, '')})" for e in _ETAPAS_TODAS)
    return ("Los leads en seguimiento comercial, agrupados por etapa, en el orden en que avanza "
            "una venta. Filtra por etapa y por nombre del responsable. Por defecto muestra solo "
            "pre-clientes; con incluir_clientes=true agrega los cerrados y en desarrollo. "
            f"Etapas válidas: {lineas}.")


def _buscar_leads(db: str, texto: str, limite: int) -> dict:
    return {}


def _ver_lead(db: str, lead_id: int) -> dict:
    return {}


def _pipeline(db: str, etapa: Optional[str], responsable: Optional[str], incluir_clientes: bool) -> dict:
    return {}


HERRAMIENTAS: list[dict] = [
    {
        "name": "buscar_leads",
        "description": ("Busca leads del CRM por nombre del negocio, email o teléfono. Devuelve "
                        "hasta `limite` resultados con id, nombre, etapa y contacto. Usá el id "
                        "con ver_lead para la ficha completa."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "minLength": 2,
                          "description": "Nombre, parte del nombre, email o teléfono"},
                "limite": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["texto"],
            "additionalProperties": False,
        },
        "fn": lambda db, a: _buscar_leads(db, a["texto"], a["limite"]),
    },
    {
        "name": "ver_lead",
        "description": ("La ficha completa de un lead: datos de contacto, etapa, origen, notas, "
                        "responsables, lo relevado en la reunión, los últimos eventos del "
                        "pipeline y las últimas llamadas."),
        "inputSchema": {
            "type": "object",
            "properties": {"lead_id": {"type": "integer", "minimum": 1}},
            "required": ["lead_id"],
            "additionalProperties": False,
        },
        "fn": lambda db, a: _ver_lead(db, a["lead_id"]),
    },
    {
        "name": "pipeline",
        "description": _descripcion_pipeline(),
        "inputSchema": {
            "type": "object",
            "properties": {
                "etapa": {"type": "string", "enum": list(_ETAPAS_TODAS),
                          "description": "Una sola etapa; sin este argumento trae todas"},
                "responsable": {"type": "string", "minLength": 2,
                                "description": "Nombre (o parte) del responsable del lead"},
                "incluir_clientes": {"type": "boolean", "default": False},
            },
            "required": [],
            "additionalProperties": False,
        },
        "fn": lambda db, a: _pipeline(db, a.get("etapa"), a.get("responsable"), a["incluir_clientes"]),
    },
]

_POR_NOMBRE = {h["name"]: h for h in HERRAMIENTAS}


def listar_herramientas() -> list[dict]:
    """Lo que va en tools/list: todo menos la función."""
    return [{k: v for k, v in h.items() if k != "fn"} for h in HERRAMIENTAS]


def ejecutar(db: str, usuario: dict, nombre: str, argumentos) -> dict:
    """Corre una herramienta y devuelve el `result` de tools/call:
    {"content": [{"type": "text", "text": ...}], "isError": bool}."""
    h = _POR_NOMBRE.get(nombre)
    if not h:
        raise ArgumentosInvalidos(f"No existe la herramienta '{nombre}'")
    args = validar_argumentos(h["inputSchema"], argumentos)
    inicio = time.monotonic()
    ok, error, texto = True, None, ""
    try:
        resultado = h["fn"](db, args)
        texto = json.dumps(resultado, ensure_ascii=False, default=str)
    except ErrorHerramienta as e:
        ok, error, texto = False, str(e), str(e)
    except Exception:
        logger.exception("Falló la herramienta MCP %s con %s", nombre, args)
        ok, error = False, "excepción"
        texto = "La herramienta falló por un error interno del CRM. Quedó registrado."
    ms = int((time.monotonic() - inicio) * 1000)
    _registrar(db, usuario, nombre, args, ok, error, ms)
    return {"content": [{"type": "text", "text": texto}], "isError": not ok}


def _registrar(db, usuario, nombre, args, ok, error, ms) -> None:
    try:
        conn = _connect(db)
        try:
            conn.execute(
                "INSERT INTO mcp_llamadas (user_id, user_name, herramienta, argumentos, ok, error, ms) "
                "VALUES (?,?,?,?,?,?,?)",
                (usuario.get("id"), usuario.get("name") or "sistema", nombre,
                 json.dumps(args, ensure_ascii=False), 1 if ok else 0, error, ms))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("No se pudo registrar la llamada MCP %s", nombre)
```

- [ ] **Step 4: Llamar `init_mcp` desde `init_db`**

En `database.py`, donde la Task 2 dejó:

```python
        from services.oauth_tokens import init_oauth
        init_oauth(conn)
        conn.commit()
```

dejar:

```python
        from services.oauth_tokens import init_oauth
        from services.mcp_tools import init_mcp
        init_oauth(conn)
        init_mcp(conn)
        conn.commit()
```

- [ ] **Step 5: Correr los tests**

Run: `python -m pytest tests/test_mcp.py -q`
Expected: 13 passed (`test_ejecutar_registra_la_llamada` pasa porque `_buscar_leads` devuelve `{}` sin error).

- [ ] **Step 6: Commit**

```bash
git add services/mcp_tools.py database.py tests/test_mcp.py
git commit -m "feat(mcp): registro de herramientas, validación de argumentos y tabla de llamadas"
```

---

### Task 9: Herramienta `buscar_leads`

**Files:**
- Modify: `services/mcp_tools.py` (reemplazar `_buscar_leads`)
- Test: `tests/test_mcp.py` (agregar)

- [ ] **Step 1: Agregar los tests**

Al final de `tests/test_mcp.py`:

```python
# ── buscar_leads ─────────────────────────────────────────────────────────────

def _texto(r):
    return json.loads(r["content"][0]["text"])


def test_buscar_por_nombre_sin_archivados(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads", {"texto": "espiga"}))
    assert [l["id"] for l in r["leads"]] == [1, 3]
    assert r["leads"][0] == {"id": 1, "nombre": "Panadería La Espiga", "etapa": "demo_1",
                             "telefono": "099111222", "email": "espiga@mail.com",
                             "ciudad": "Montevideo", "rubro": "panaderia", "origen": "meta",
                             "ultimo_evento_at": "2026-10-01 10:00:00"}


def test_buscar_por_mail_y_telefono(sembrada, ana):
    u = {"id": ana, "name": "Ana"}
    assert [l["id"] for l in _texto(mt.ejecutar(sembrada, u, "buscar_leads", {"texto": "BAR@mail"}))["leads"]] == [3]
    assert [l["id"] for l in _texto(mt.ejecutar(sembrada, u, "buscar_leads", {"texto": "099 333-444"}))["leads"]] == [2]


def test_buscar_respeta_limite_y_lo_dice(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads", {"texto": "espiga", "limite": 1}))
    assert len(r["leads"]) == 1 and r["hay_mas"] is True


def test_buscar_sin_resultados(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads", {"texto": "zzz"}))
    assert r == {"leads": [], "hay_mas": False}
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_mcp.py -q -k buscar`
Expected: FAIL con `KeyError: 'leads'`.

- [ ] **Step 3: Reemplazar `_buscar_leads` en `services/mcp_tools.py`**

```python
_SQL_BUSCAR = (
    "SELECT id, name, crm_status, phone, email, city, category, source, last_event_at "
    "FROM businesses "
    "WHERE archivado_en IS NULL AND ("
    "  lower(name) LIKE ? OR lower(email) LIKE ? "
    "  OR replace(replace(replace(coalesce(phone, ''), ' ', ''), '-', ''), '+', '') LIKE ?) "
    "ORDER BY CASE WHEN score IS NULL THEN 1 ELSE 0 END, score DESC, scraped_at DESC, id "
    "LIMIT ?"
)


def _fila_lead(f) -> dict:
    return {"id": f["id"], "nombre": f["name"], "etapa": f["crm_status"], "telefono": f["phone"],
            "email": f["email"], "ciudad": f["city"], "rubro": f["category"],
            "origen": f["source"], "ultimo_evento_at": f["last_event_at"]}


def _buscar_leads(db: str, texto: str, limite: int) -> dict:
    """LIKE sobre nombre, mail y teléfono, con LIMIT en SQL. No se usa
    `get_all_businesses` porque trae la tabla entera a memoria (incidente del
    28-8-2026). Se pide uno de más para saber si hay más."""
    patron = f"%{texto.strip().lower()}%"
    solo_digitos = "".join(c for c in texto if c.isdigit())
    # Sin dígitos no se busca por teléfono: LIKE NULL nunca matchea. (Un " "
    # como patrón no sirve: SQLite lo corta ahí, queda vacío y matchea los
    # teléfonos NULL.)
    patron_tel = f"%{solo_digitos}%" if solo_digitos else None
    conn = _connect(db)
    try:
        filas = conn.execute(_SQL_BUSCAR, (patron, patron, patron_tel, limite + 1)).fetchall()
    finally:
        conn.close()
    return {"leads": [_fila_lead(f) for f in filas[:limite]], "hay_mas": len(filas) > limite}
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_mcp.py -q`
Expected: 17 passed.

- [ ] **Step 5: Commit**

```bash
git add services/mcp_tools.py tests/test_mcp.py
git commit -m "feat(mcp): herramienta buscar_leads"
```

---

### Task 10: Herramienta `ver_lead`

**Files:**
- Modify: `services/mcp_tools.py` (reemplazar `_ver_lead`)
- Test: `tests/test_mcp.py` (agregar)

- [ ] **Step 1: Agregar los tests**

Al final de `tests/test_mcp.py`:

```python
# ── ver_lead ─────────────────────────────────────────────────────────────────

def test_ver_lead_trae_ficha_completa(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "ver_lead", {"lead_id": 1}))
    assert r["id"] == 1 and r["nombre"] == "Panadería La Espiga" and r["etapa"] == "demo_1"
    assert r["notas"] == "Quiere web con pedidos" and r["origen"] == "meta"
    assert r["responsables"] == {"encargado": "Ana", "mantenimiento": None, "cobros": None}
    assert r["relevamiento"]["lead_name"] == "Lucía" and r["relevamiento"]["needs"] == "pedidos online"
    assert [e["new_status"] for e in r["eventos"]] == ["demo_1", "demo_agendada"]
    assert r["llamadas"][0]["outcome"] == "contestó"
    # Nada interno.
    for interno in ("form_data", "meta_lead_id", "semaforo_origen", "demo_html_path", "encargado_id"):
        assert interno not in r


def test_ver_lead_sin_relevamiento(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "ver_lead", {"lead_id": 2}))
    assert r["relevamiento"] == {} and r["eventos"] == [] and r["llamadas"] == []
    assert r["responsables"]["encargado"] == "Juan"


def test_ver_lead_inexistente_es_error_de_herramienta(sembrada, ana):
    r = mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "ver_lead", {"lead_id": 999})
    assert r["isError"] is True
    assert "999" in r["content"][0]["text"]
    conn = sqlite3.connect(sembrada)
    assert conn.execute("SELECT ok, error FROM mcp_llamadas ORDER BY id DESC LIMIT 1").fetchone() == (0, "No existe el lead 999")
    conn.close()
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_mcp.py -q -k ver_lead`
Expected: FAIL con `KeyError: 'id'`.

- [ ] **Step 3: Reemplazar `_ver_lead` en `services/mcp_tools.py`**

```python
# Qué columnas de `businesses` ve Claude, y con qué nombre. Lo que no está acá
# no sale: ids de Meta, form_data crudo, semáforo, rutas de archivos.
_CAMPOS_LEAD = (
    ("id", "id"), ("name", "nombre"), ("crm_status", "etapa"), ("category", "rubro"),
    ("city", "ciudad"), ("address", "direccion"), ("phone", "telefono"), ("email", "email"),
    ("website", "web"), ("instagram_url", "instagram"), ("facebook_url", "facebook"),
    ("source", "origen"), ("meta_campaign_name", "campania_meta"), ("interest", "interes"),
    ("notes", "notas"), ("pitch_text", "pitch"), ("callback_date", "volver_a_llamar"),
    ("score", "score"), ("last_event_at", "ultimo_evento_at"), ("scraped_at", "creado_at"),
    ("monto_pagado", "monto_pagado"), ("moneda_pagado", "moneda_pagado"),
)
_RESPONSABLES = (("encargado_id", "encargado"), ("mantenimiento_id", "mantenimiento"),
                 ("cobros_id", "cobros"))


def _nombres_usuarios(conn, ids) -> dict:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    marcas = ",".join("?" * len(ids))
    return {f["id"]: f["name"] for f in
            conn.execute(f"SELECT id, name FROM users WHERE id IN ({marcas})", ids).fetchall()}


def _ver_lead(db: str, lead_id: int) -> dict:
    biz = get_business(db, lead_id)
    if not biz:
        raise ErrorHerramienta(f"No existe el lead {lead_id}")
    ficha = {salida: biz.get(col) for col, salida in _CAMPOS_LEAD}
    conn = _connect(db)
    try:
        nombres = _nombres_usuarios(conn, [biz.get(col) for col, _ in _RESPONSABLES])
        llamadas = conn.execute(
            "SELECT called_at, outcome, notes, created_by FROM call_logs "
            "WHERE lead_id = ? ORDER BY called_at DESC LIMIT ?", (lead_id, TOPE_HISTORIAL)).fetchall()
    finally:
        conn.close()
    ficha["responsables"] = {salida: nombres.get(biz.get(col)) for col, salida in _RESPONSABLES}
    relev = get_client_info(db, lead_id) or {}
    ficha["relevamiento"] = {k: v for k, v in relev.items() if k not in ("id", "client_id")}
    ficha["eventos"] = get_lead_events(db, lead_id)[:TOPE_HISTORIAL]
    ficha["llamadas"] = [dict(f) for f in llamadas]
    return ficha
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_mcp.py -q`
Expected: 20 passed.

- [ ] **Step 5: Commit**

```bash
git add services/mcp_tools.py tests/test_mcp.py
git commit -m "feat(mcp): herramienta ver_lead"
```

---

### Task 11: Herramienta `pipeline`

**Files:**
- Modify: `services/mcp_tools.py` (reemplazar `_pipeline`)
- Test: `tests/test_mcp.py` (agregar)

- [ ] **Step 1: Agregar los tests**

Al final de `tests/test_mcp.py`:

```python
# ── pipeline ─────────────────────────────────────────────────────────────────

def test_pipeline_agrupa_en_orden_y_excluye_archivados_y_clientes(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {}))
    assert [e["etapa"] for e in r["etapas"]] == ["demo_1", "presupuesto_enviado"]
    assert r["etapas"][0]["total"] == 1
    lead = r["etapas"][0]["leads"][0]
    assert lead["id"] == 1 and lead["encargado"] == "Ana" and lead["nombre"] == "Panadería La Espiga"
    assert isinstance(lead["dias_sin_movimiento"], int)
    assert r["total"] == 2 and r["truncado"] is False


def test_pipeline_filtra_por_etapa(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"etapa": "presupuesto_enviado"}))
    assert [e["etapa"] for e in r["etapas"]] == ["presupuesto_enviado"]
    assert r["etapas"][0]["leads"][0]["id"] == 2


def test_pipeline_filtra_por_responsable(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"responsable": "jua"}))
    assert r["total"] == 1 and r["etapas"][0]["leads"][0]["encargado"] == "Juan"


def test_pipeline_incluye_clientes(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"incluir_clientes": True}))
    assert [e["etapa"] for e in r["etapas"]] == ["demo_1", "presupuesto_enviado", "cerrado"]


def test_pipeline_etapa_de_cliente_sin_incluir_clientes_igual_la_trae(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"etapa": "cerrado"}))
    assert r["total"] == 1


def test_pipeline_vacio(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"etapa": "rechazo"}))
    assert r == {"etapas": [], "total": 0, "truncado": False}


def test_pipeline_trunca_a_200(sembrada, ana):
    conn = sqlite3.connect(sembrada)
    conn.executemany("INSERT INTO businesses (name, phone, crm_status) VALUES (?,?,?)",
                     [(f"L{i}", f"09{i:07d}", "follow_up_1") for i in range(205)])
    conn.commit()
    conn.close()
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"etapa": "follow_up_1"}))
    assert r["truncado"] is True and r["total"] == 200
    assert "Filtr" in r["aviso"]
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_mcp.py -q -k pipeline`
Expected: FAIL con `KeyError: 'etapas'`.

- [ ] **Step 3: Reemplazar `_pipeline` en `services/mcp_tools.py`**

```python
def _dias_desde(valor) -> Optional[int]:
    """`last_event_at` viene como 'YYYY-MM-DD HH:MM:SS' (CURRENT_TIMESTAMP de
    SQLite, en UTC). None si no hay fecha o no se puede leer."""
    if not valor:
        return None
    try:
        momento = datetime.fromisoformat(str(valor).replace("T", " ")[:19]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, (datetime.now(timezone.utc) - momento).days)


def _pipeline(db: str, etapa: Optional[str], responsable: Optional[str], incluir_clientes: bool) -> dict:
    if etapa:
        etapas = [etapa]
    else:
        etapas = list(ETAPAS_PRECLIENTE) + (list(ETAPAS_CLIENTE) if incluir_clientes else [])
    marcas = ",".join("?" * len(etapas))
    sql = (
        "SELECT b.id, b.name, b.crm_status, b.phone, b.email, b.last_event_at, u.name AS encargado "
        "FROM businesses b LEFT JOIN users u ON u.id = b.encargado_id "
        f"WHERE b.archivado_en IS NULL AND b.crm_status IN ({marcas}) "
    )
    params: list = list(etapas)
    if responsable:
        sql += "AND lower(u.name) LIKE ? "
        params.append(f"%{responsable.strip().lower()}%")
    # Los que hace más tiempo no se mueven, primero: son los que hay que empujar.
    sql += ("ORDER BY CASE WHEN b.last_event_at IS NULL THEN 0 ELSE 1 END, b.last_event_at ASC "
            "LIMIT ?")
    params.append(TOPE_LEADS + 1)
    conn = _connect(db)
    try:
        filas = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    truncado = len(filas) > TOPE_LEADS
    filas = filas[:TOPE_LEADS]
    grupos = {e: [] for e in etapas}
    for f in filas:
        grupos[f["crm_status"]].append({
            "id": f["id"], "nombre": f["name"], "telefono": f["phone"], "email": f["email"],
            "encargado": f["encargado"], "ultimo_evento_at": f["last_event_at"],
            "dias_sin_movimiento": _dias_desde(f["last_event_at"]),
        })
    salida = {
        "etapas": [{"etapa": e, "total": len(grupos[e]), "leads": grupos[e]} for e in etapas if grupos[e]],
        "total": len(filas),
        "truncado": truncado,
    }
    if truncado:
        salida["aviso"] = f"Hay más de {TOPE_LEADS} leads; se muestran {TOPE_LEADS}. Filtrá por etapa o responsable."
    return salida
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_mcp.py -q`
Expected: 27 passed.

- [ ] **Step 5: Commit**

```bash
git add services/mcp_tools.py tests/test_mcp.py
git commit -m "feat(mcp): herramienta pipeline"
```

---

### Task 12: Endpoint `POST /mcp`

**Files:**
- Create: `routes/mcp.py`
- Modify: `dashboard.py` (import y tupla de blueprints)
- Test: `tests/test_mcp.py` (agregar)

- [ ] **Step 1: Agregar los tests**

Al final de `tests/test_mcp.py`:

```python
# ── POST /mcp ────────────────────────────────────────────────────────────────

WWW_AUTH = ('Bearer resource_metadata="https://crm.test/.well-known/oauth-protected-resource/mcp", '
            'scope="crm:leer"')


@pytest.fixture
def app(sembrada):
    a = dashboard.create_app(sembrada)
    a.config["TESTING"] = True
    return a


def _token_de(db, uid):
    import base64, hashlib, secrets
    verifier = secrets.token_urlsafe(40)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    code = ot.emitir_codigo(db, uid, "cli", "https://r/cb", challenge, "crm:leer", "https://crm.test/mcp")
    return ot.canjear_codigo(db, code, "cli", "https://r/cb", verifier)["access_token"]


@pytest.fixture
def claude(app, sembrada, ana):
    """Un test_client que manda el Bearer de Ana en cada request."""
    token = _token_de(sembrada, ana)
    c = app.test_client()
    def post(cuerpo, raw=None):
        return c.post("/mcp", data=raw if raw is not None else json.dumps(cuerpo),
                      content_type="application/json",
                      headers={"Authorization": f"Bearer {token}",
                               "Accept": "application/json, text/event-stream"})
    return post


def _rpc(metodo, params=None, id_=1):
    cuerpo = {"jsonrpc": "2.0", "method": metodo, "id": id_}
    if params is not None:
        cuerpo["params"] = params
    return cuerpo


def test_sin_bearer_es_401_con_www_authenticate(app):
    r = app.test_client().post("/mcp", json=_rpc("initialize"))
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == WWW_AUTH
    assert r.get_json()["error"] == "invalid_token"


def test_bearer_invalido_es_401(app):
    r = app.test_client().post("/mcp", json=_rpc("ping"), headers={"Authorization": "Bearer nada"})
    assert r.status_code == 401 and r.headers["WWW-Authenticate"] == WWW_AUTH


def test_get_y_delete_son_405(app):
    assert app.test_client().get("/mcp").status_code == 405
    assert app.test_client().delete("/mcp").status_code == 405


def test_vendedor_de_fidelidad_es_403(app, sembrada, juan):
    from services.fidelidad import ROL_VENDEDOR
    conn = sqlite3.connect(sembrada)
    # init_fidelidad ya crea el rol: se busca, no se inserta.
    rid = conn.execute("SELECT id FROM roles WHERE name = ?", (ROL_VENDEDOR,)).fetchone()[0]
    conn.execute("UPDATE users SET role_id = ? WHERE id = ?", (rid, juan))
    conn.commit()
    conn.close()
    token = _token_de(sembrada, juan)
    r = app.test_client().post("/mcp", json=_rpc("ping"), headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_initialize(claude):
    r = claude(_rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "claude", "version": "1"}}))
    assert r.status_code == 200
    res = r.get_json()["result"]
    assert res["protocolVersion"] == "2025-06-18"
    assert res["capabilities"] == {"tools": {}}
    assert res["serverInfo"]["name"] == "scalerics-crm"
    assert "instructions" in res


def test_initialize_version_desconocida_cae_a_la_nuestra(claude):
    r = claude(_rpc("initialize", {"protocolVersion": "2099-01-01", "capabilities": {},
                                   "clientInfo": {"name": "x", "version": "1"}}))
    assert r.get_json()["result"]["protocolVersion"] == "2025-06-18"


def test_notificacion_es_202_sin_cuerpo(claude):
    r = claude({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202 and r.get_data() == b""


def test_ping(claude):
    r = claude(_rpc("ping", id_="p1"))
    assert r.get_json() == {"jsonrpc": "2.0", "id": "p1", "result": {}}


def test_tools_list(claude):
    r = claude(_rpc("tools/list"))
    nombres = [t["name"] for t in r.get_json()["result"]["tools"]]
    assert nombres == ["buscar_leads", "ver_lead", "pipeline"]


def test_tools_call(claude):
    r = claude(_rpc("tools/call", {"name": "ver_lead", "arguments": {"lead_id": 1}}))
    res = r.get_json()["result"]
    assert res["isError"] is False
    assert json.loads(res["content"][0]["text"])["nombre"] == "Panadería La Espiga"


def test_tools_call_argumentos_invalidos_es_32602(claude):
    r = claude(_rpc("tools/call", {"name": "ver_lead", "arguments": {"lead_id": "1"}}))
    assert r.status_code == 200
    err = r.get_json()["error"]
    assert err["code"] == -32602 and "lead_id" in err["message"]


def test_tools_call_herramienta_desconocida_es_32602(claude):
    r = claude(_rpc("tools/call", {"name": "borrar", "arguments": {}}))
    assert r.get_json()["error"]["code"] == -32602


def test_metodo_desconocido_es_32601(claude):
    r = claude(_rpc("resources/list"))
    assert r.get_json()["error"]["code"] == -32601


def test_batch_es_400(claude):
    r = claude(None, raw=json.dumps([_rpc("ping")]))
    assert r.status_code == 400 and r.get_json()["error"]["code"] == -32600


def test_cuerpo_no_json_es_400(claude):
    r = claude(None, raw="{no es json")
    assert r.status_code == 400 and r.get_json()["error"]["code"] == -32700


def test_cada_call_queda_registrada_con_el_usuario(claude, sembrada, ana):
    claude(_rpc("tools/call", {"name": "buscar_leads", "arguments": {"texto": "norte"}}))
    conn = sqlite3.connect(sembrada)
    fila = conn.execute("SELECT user_id, user_name, herramienta FROM mcp_llamadas ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert fila == (ana, "Ana", "buscar_leads")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_mcp.py -q -k "mcp or 401 or 405 or initialize or ping or tools or batch or json or registrada or vendedor"`
Expected: fallan con `404`/`302` en `/mcp`.

- [ ] **Step 3: Crear `routes/mcp.py`**

```python
"""El endpoint MCP del CRM: `POST /mcp`.

Transporte Streamable HTTP en modo stateless con respuesta JSON: cada POST es
un mensaje JSON-RPC y se contesta con un único JSON. No hay SSE, no hay
`Mcp-Session-Id`, no hay batch (la versión 2025-06-18 lo sacó). Para un
servidor de solo herramientas alcanza y es lo que mejor convive con gunicorn
sync.

Autenticación: Bearer emitido por `/oauth/token`. Sin token, o con uno malo,
`401` con el `WWW-Authenticate` que claude.ai necesita para arrancar el OAuth
(y lo necesita en un 401: en un 200 lo ignora). Con token, las mismas reglas
que la API web: el usuario existe y no es vendedor de Fidelidad.
"""

import logging

from flask import Blueprint, current_app, jsonify, request

from database import get_user_by_id
from services import mcp_tools, oauth_tokens
from services.fidelidad import es_vendedor
from services.oauth_tokens import SCOPE_LECTURA, base_url

logger = logging.getLogger(__name__)

mcp_bp = Blueprint("mcp", __name__)

VERSIONES = ("2025-11-25", "2025-06-18", "2025-03-26")
VERSION_DEFECTO = "2025-06-18"
INSTRUCCIONES = (
    "CRM de Scalerics, una agencia de Montevideo que vende sitios web y sistemas a comercios. "
    "Las herramientas son de solo lectura: buscar_leads para encontrar un negocio, ver_lead "
    "para su ficha e historial, pipeline para ver qué hay en cada etapa de venta."
)


class _ErrorRpc(Exception):
    def __init__(self, code: int, message: str, http: int = 200):
        super().__init__(message)
        self.code, self.message, self.http = code, message, http


def _db() -> str:
    return current_app.config["DB_PATH"]


def _rechazo_401():
    resp = jsonify({"error": "invalid_token"})
    resp.status_code = 401
    resp.headers["WWW-Authenticate"] = (
        f'Bearer resource_metadata="{base_url()}/.well-known/oauth-protected-resource/mcp", '
        f'scope="{SCOPE_LECTURA}"')
    return resp


def _autenticar():
    """(usuario, None) o (None, respuesta de error)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, _rechazo_401()
    info = oauth_tokens.validar_access(_db(), auth[len("Bearer "):].strip())
    if not info:
        return None, _rechazo_401()
    usuario = get_user_by_id(_db(), info["user_id"])
    if not usuario or es_vendedor(_db(), usuario["id"]):
        return None, (jsonify({"error": "forbidden"}), 403)
    return usuario, None


def _respuesta_error(id_, code, message, http=200):
    resp = jsonify({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})
    resp.status_code = http
    return resp


@mcp_bp.route("/mcp", methods=["POST"])
def mcp():
    usuario, err = _autenticar()
    if err is not None:
        return err
    cuerpo = request.get_json(silent=True, force=True)
    if cuerpo is None:
        return _respuesta_error(None, -32700, "El cuerpo no es JSON", 400)
    if isinstance(cuerpo, list):
        return _respuesta_error(None, -32600, "Batch no soportado", 400)
    if not isinstance(cuerpo, dict) or cuerpo.get("jsonrpc") != "2.0" or not isinstance(cuerpo.get("method"), str):
        return _respuesta_error(cuerpo.get("id") if isinstance(cuerpo, dict) else None,
                                -32600, "No es un pedido JSON-RPC 2.0", 400)
    if cuerpo.get("id") is None:
        # Notificación: no se responde. Claude manda notifications/initialized.
        return "", 202
    id_ = cuerpo["id"]
    try:
        resultado = _despachar(cuerpo["method"], cuerpo.get("params") or {}, usuario)
    except _ErrorRpc as e:
        return _respuesta_error(id_, e.code, e.message, e.http)
    return jsonify({"jsonrpc": "2.0", "id": id_, "result": resultado})


def _despachar(metodo: str, params, usuario: dict):
    if not isinstance(params, dict):
        raise _ErrorRpc(-32602, "params tiene que ser un objeto")
    if metodo == "initialize":
        pedida = params.get("protocolVersion")
        return {
            "protocolVersion": pedida if pedida in VERSIONES else VERSION_DEFECTO,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "scalerics-crm", "version": "1"},
            "instructions": INSTRUCCIONES,
        }
    if metodo == "ping":
        return {}
    if metodo == "tools/list":
        return {"tools": mcp_tools.listar_herramientas()}
    if metodo == "tools/call":
        nombre = params.get("name")
        if not isinstance(nombre, str):
            raise _ErrorRpc(-32602, "Falta name")
        try:
            return mcp_tools.ejecutar(_db(), usuario, nombre, params.get("arguments"))
        except mcp_tools.ArgumentosInvalidos as e:
            raise _ErrorRpc(-32602, str(e))
    raise _ErrorRpc(-32601, f"Método no soportado: {metodo}")
```

- [ ] **Step 4: Registrar el blueprint en `dashboard.py`**

Debajo de `from routes.oauth import oauth_bp` agregar:

```python
from routes.mcp import mcp_bp
```

y en la tupla del `for bp in (...)`, donde la Task 5 dejó `buscar_bp, oauth_bp):`, dejar `buscar_bp, oauth_bp, mcp_bp):`.

- [ ] **Step 5: Correr los tests**

Run: `python -m pytest tests/test_mcp.py -q`
Expected: 43 passed.

Después, todo lo nuevo junto y los tests de auth existentes:

Run: `python -m pytest tests/test_oauth_tokens.py tests/test_oauth_clientes.py tests/test_oauth_flujo.py tests/test_mcp.py tests/test_auth_panel.py tests/test_admin_por_rol.py tests/test_api_cohorte.py -q`
Expected: todos passed.

- [ ] **Step 6: Commit**

```bash
git add routes/mcp.py dashboard.py tests/test_mcp.py
git commit -m "feat(mcp): endpoint POST /mcp con JSON-RPC y Bearer"
```

---

### Task 13: Documento de puesta en producción

**Files:**
- Create: `docs/puesta-en-produccion-conector-claude.md`

- [ ] **Step 1: Escribir el documento**

```markdown
# Puesta en producción — conector del CRM para Claude

El CRM expone un servidor MCP en `https://scalerics-crm.fly.dev/mcp`. Cada
persona del equipo lo agrega en claude.ai y entra con su usuario del CRM.
Claude solo puede **leer** leads y pipeline; cada consulta queda registrada
con el nombre de quien la hizo.

## Qué tiene que estar configurado en Fly

Una sola variable, que ya existe para los mails: `CRM_URL`. Tiene que valer
exactamente `https://scalerics-crm.fly.dev`, sin barra final. Es la URL que
el CRM anuncia como emisor OAuth y como recurso protegido; si no coincide con
la que la gente escribe en claude.ai, claude.ai rechaza la conexión.

Comprobar:

```bash
flyctl secrets list -a scalerics-crm | grep CRM_URL
```

Si no está, `flyctl secrets set CRM_URL=https://scalerics-crm.fly.dev -a scalerics-crm`.

Después del deploy, estas dos URLs tienen que devolver JSON sin pedir login:

- https://scalerics-crm.fly.dev/.well-known/oauth-authorization-server
- https://scalerics-crm.fly.dev/.well-known/oauth-protected-resource/mcp

## Cómo se agrega en claude.ai (plan Team)

Solo un **Owner** de la organización puede agregar conectores personalizados.

1. Configuración → Conectores → **Agregar conector personalizado**.
2. Nombre: `CRM Scalerics`. URL: `https://scalerics-crm.fly.dev/mcp`.
3. Identidad OAuth: dejar la opción recomendada ("identidad publicada de
   Claude"). No hace falta client ID ni secret.
4. Guardar. Elegir "Iniciar sesión ahora".
5. Se abre el login del CRM. Entrar con el usuario propio. En la pantalla
   siguiente, **Autorizar**.

Una vez que el Owner lo agregó, cada miembro del equipo lo ve en Conectores y
conecta con su propio usuario del CRM: la autorización es por persona.

## Quién puede usarlo

Cualquier usuario del CRM, salvo el rol "Vendedor Fidelidad" (igual que en la
API web). No hace falta ningún panel en el rol.

## Qué puede hacer Claude

| Herramienta | Qué hace |
|---|---|
| `buscar_leads` | Busca por nombre, email o teléfono |
| `ver_lead` | Ficha completa, relevamiento, responsables, últimos eventos y llamadas |
| `pipeline` | Leads en seguimiento agrupados por etapa, filtrables por etapa y responsable |

Nada de escritura. Las finanzas, tareas y agenda no están expuestas.

## Revocar el acceso de alguien

Hoy no hay pantalla. Desde la consola de Fly:

```bash
flyctl ssh console -a scalerics-crm -C "sqlite3 /data/leads.db \"UPDATE oauth_tokens SET revocado_en = datetime('now') WHERE user_id = (SELECT id FROM users WHERE email = 'persona@scalerics.com') AND revocado_en IS NULL\""
```

Si la persona se borra del CRM, sus tokens se borran solos.

## Ver qué consultó Claude

```bash
flyctl ssh console -a scalerics-crm -C "sqlite3 -header /data/leads.db \"SELECT created_at, user_name, herramienta, argumentos, ok, ms FROM mcp_llamadas ORDER BY id DESC LIMIT 50\""
```

## Si algo falla

- "Couldn't reach the MCP server" al agregarlo: las URLs de `/.well-known/`
  no responden o `CRM_URL` no coincide.
- Pide login y después de autorizar vuelve a pedirlo: el reloj de la máquina o
  un `CRM_URL` con barra final. Mirar los logs de Fly en el momento del
  `POST /oauth/token`.
- Claude dice que no está conectado aunque autorizó: desconectar y volver a
  conectar desde Conectores. Si sigue, mirar `oauth_tokens` por `user_id`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/puesta-en-produccion-conector-claude.md
git commit -m "docs: puesta en producción del conector de Claude"
```

---

### Task 14: Verificación de integración con Claude Code contra el CRM local

Esta tarea es manual. No se commitea nada salvo arreglos que salgan de ella.

**Precondición:** no tener un `.env` con credenciales de producción en el worktree. Si `C:\Users\gonza\Documents\GitHub\scalerics-crm-wa-a-main\.env` existe, `load_dotenv()` **no** lo encuentra desde `crm-mcp` (son hermanos, no padre e hijo), pero conviene confirmar: `python -c "from dotenv import find_dotenv; print(find_dotenv())"` desde `crm-mcp` tiene que imprimir vacío o un `.env` propio.

- [ ] **Step 1: Levantar el CRM local con la URL local como emisor**

En una terminal, desde `crm-mcp`:

```bash
CRM_URL=http://localhost:5000 SECRET_KEY=local-dev DB_PATH=local.db REGISTER_CODE=abc CRM_SIN_PROCESOS_DE_FONDO=true python server.py
```

Entrar a http://localhost:5000/register y crear un usuario de prueba (código `abc`). Cargar dos o tres leads desde el panel, o insertarlos con `sqlite3 local.db`.

- [ ] **Step 2: Comprobar la metadata a mano**

```bash
curl -s http://localhost:5000/.well-known/oauth-protected-resource/mcp
curl -s -i -X POST http://localhost:5000/mcp -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"initialize"}' | head -5
```

Expected: el primero devuelve JSON con `"resource": "http://localhost:5000/mcp"`; el segundo, `401` con `WWW-Authenticate: Bearer resource_metadata="http://localhost:5000/.well-known/oauth-protected-resource/mcp", scope="crm:leer"`.

- [ ] **Step 3: Conectar Claude Code**

En otra terminal:

```bash
claude mcp add --transport http crm-local http://localhost:5000/mcp
claude
```

Dentro de Claude Code, `/mcp` → elegir `crm-local` → **Authenticate**. Se abre el navegador en el login del CRM, después la pantalla de consentimiento. Autorizar.

Expected: Claude Code muestra el servidor como conectado con 3 herramientas. Pedirle "buscá el lead X" y "mostrame el pipeline": tiene que llamar `buscar_leads` y `pipeline` y responder con datos del `local.db`. En el log del servidor se ven `POST /oauth/token` y `POST /mcp`.

- [ ] **Step 4: Comprobar el registro**

```bash
sqlite3 -header local.db "SELECT created_at, user_name, herramienta, argumentos, ok, ms FROM mcp_llamadas"
```

Expected: una fila por llamada, con el nombre del usuario de prueba.

- [ ] **Step 5: Limpiar**

```bash
claude mcp remove crm-local
```

Borrar `local.db` (y `local.db-wal`, `local.db-shm` si quedaron). Confirmar `git status --short` limpio.

- [ ] **Step 6: Si hubo arreglos, commitear**

```bash
git add -A
git commit -m "fix(mcp): ajustes tras la prueba con Claude Code"
```

---

### Task 15: Cierre de la rama

- [ ] **Step 1: Suite completa**

Run: `python -m pytest -q`
Expected: todo passed (si algún test preexistente falla, verificar que también falla en `origin/main` antes de tocarlo: `git stash` NO; usar `git worktree` o mirar el test en el otro clon).

- [ ] **Step 2: Revisión de código**

Usar el agente `code-reviewer` sobre `git diff origin/main...HEAD` y, como toca autenticación, el agente `security-reviewer`. Atender CRITICAL y HIGH.

- [ ] **Step 3: Actualizar la bitácora**

En `COORDINACION.md`, en la fila de la sesión M, cambiar el territorio a: "**Listo en `feat/conector-mcp`, probado con Claude Code contra el CRM local. Sin PR, sin merge y sin deploy hasta que Juan lo pida.** Para deployar: `CRM_URL` en Fly y seguir `docs/puesta-en-produccion-conector-claude.md`."

```bash
git add COORDINACION.md
git commit -m "docs: bitácora de la sesión M, conector listo sin deployar"
```

- [ ] **Step 4: Push de la rama**

```bash
git push -u origin feat/conector-mcp
```

El PR y el deploy los decide Juan.

---

## Auto-revisión del plan contra el spec

- **Metadata, 401, CIMD, DCR, PKCE, redirect exacta y loopback:** Tasks 4, 5, 6, 12.
- **Consentimiento con host visible, nonce, cancelar, `next` en login:** Task 6.
- **Token: form-urlencoded, rotación, `invalid_grant`, reuso de código revoca, revoke:** Tasks 2, 3, 7.
- **Hash en reposo, duraciones, barrido sin hilo, cascade al borrar usuario:** Tasks 2, 3.
- **Tope de clientes DCR:** Task 4.
- **`/mcp` stateless, 405, batch 400, -32700/-32600/-32601/-32602, versiones, instructions, 202 en notificaciones, 403 vendedor:** Task 12.
- **Tres herramientas, exclusión de archivados, columnas internas fuera, topes de 200/20, etapas en la descripción:** Tasks 8–11.
- **`mcp_llamadas` por llamada, sin romper si falla:** Tasks 8, 12.
- **Doc de producción, revocación manual, consulta de llamadas:** Task 13.
- **Prueba con Claude Code antes de claude.ai:** Task 14.
- **Desvíos respecto del spec, deliberados:** las tablas se crean desde `init_oauth` / `init_mcp` en los servicios (patrón más nuevo del repo, `init_fidelidad`) y no inline en `database.py`; `oauth_tokens` tiene una columna extra `codigo_hash` para poder revocar lo emitido por un código reusado. `ultimo_uso` se actualiza como mucho una vez por minuto, como dice el spec.
