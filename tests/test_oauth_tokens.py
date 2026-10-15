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
