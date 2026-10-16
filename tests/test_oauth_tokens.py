"""Códigos y tokens del OAuth del conector MCP.

Prueban `services/oauth_tokens.py` directo, sin HTTP. El flujo entero por
HTTP vive en test_oauth_flujo.py.
"""

import base64
import hashlib
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.security import generate_password_hash

from database import create_user, delete_user, init_db
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


def _tokens(db, uid, client_id="cli"):
    verifier, challenge = _pkce()
    code = ot.emitir_codigo(db, uid, client_id, "https://r/cb", challenge, "crm:leer", None)
    return ot.canjear_codigo(db, code, client_id, "https://r/cb", verifier)


def test_validar_access_devuelve_usuario_y_cliente(db, uid):
    tokens = _tokens(db, uid)
    info = ot.validar_access(db, tokens["access_token"])
    assert info["user_id"] == uid and info["client_id"] == "cli" and info["scope"] == "crm:leer"
    assert ot.validar_access(db, "no-existe") is None


def test_validar_access_devuelve_el_resource(db, uid):
    """/mcp lo compara con recurso_mcp(): un token emitido para otro recurso no
    tiene que valer acá (RFC 8707)."""
    verifier, challenge = _pkce()
    code = ot.emitir_codigo(db, uid, "cli", "https://r/cb", challenge, "crm:leer",
                            "https://crm.test/mcp")
    tokens = ot.canjear_codigo(db, code, "cli", "https://r/cb", verifier)
    assert ot.validar_access(db, tokens["access_token"])["resource"] == "https://crm.test/mcp"
    assert ot.validar_access(db, _tokens(db, uid)["access_token"])["resource"] is None


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


def test_usuario_borrado_no_puede_usar_sus_tokens(db, uid):
    # `_connect` no activa foreign_keys: lo que protege es el JOIN con users.
    tokens = _tokens(db, uid)
    delete_user(db, uid)
    assert ot.validar_access(db, tokens["access_token"]) is None
    assert ot.refrescar(db, tokens["refresh_token"], "cli") is None


def test_codigo_de_usuario_borrado_no_se_canjea(db, uid):
    verifier, challenge = _pkce()
    code = ot.emitir_codigo(db, uid, "cli", "https://r/cb", challenge, "crm:leer", None)
    delete_user(db, uid)
    assert ot.canjear_codigo(db, code, "cli", "https://r/cb", verifier) is None


def test_client_id_no_ascii_no_revienta(db, uid):
    verifier, challenge = _pkce()
    code = ot.emitir_codigo(db, uid, "cli", "https://r/cb", challenge, "crm:leer", None)
    assert ot.canjear_codigo(db, code, "clíente", "https://r/cb", verifier) is None
    assert ot.canjear_codigo(db, code, "cli", "https://r/cb", "ñ" * 50) is None
    tokens = _tokens(db, uid)
    assert ot.refrescar(db, tokens["refresh_token"], "clíente") is None


def _adelantar(monkeypatch, **delta):
    futuro = datetime.now(timezone.utc) + timedelta(**delta)
    monkeypatch.setattr(ot, "_ahora", lambda: futuro)


def test_refresh_vencido_no_se_puede_usar(db, uid, monkeypatch):
    tokens = _tokens(db, uid)
    _adelantar(monkeypatch, days=31)
    assert ot.refrescar(db, tokens["refresh_token"], "cli") is None


def test_barrido_borra_codigos_vencidos(db, uid, monkeypatch):
    _, challenge = _pkce()
    ot.emitir_codigo(db, uid, "cli", "https://r/cb", challenge, "crm:leer", None)
    _adelantar(monkeypatch, minutes=11)
    ot.emitir_codigo(db, uid, "cli", "https://r/cb", challenge, "crm:leer", None)
    conn = sqlite3.connect(db)
    cuantos = conn.execute("SELECT COUNT(*) FROM oauth_codigos").fetchone()[0]
    conn.close()
    assert cuantos == 1


def test_reusar_codigo_despues_de_refrescar_revoca_tambien_el_par_nuevo(db, uid):
    verifier, challenge = _pkce()
    code = ot.emitir_codigo(db, uid, "cli", "https://r/cb", challenge, "crm:leer", None)
    viejo = ot.canjear_codigo(db, code, "cli", "https://r/cb", verifier)
    nuevo = ot.refrescar(db, viejo["refresh_token"], "cli")
    assert ot.validar_access(db, nuevo["access_token"]) is not None
    assert ot.canjear_codigo(db, code, "cli", "https://r/cb", verifier) is None
    assert ot.validar_access(db, nuevo["access_token"]) is None


def test_reusar_refresh_rotado_revoca_la_familia(db, uid):
    viejo = _tokens(db, uid)
    nuevo = ot.refrescar(db, viejo["refresh_token"], "cli")
    assert ot.validar_access(db, nuevo["access_token"]) is not None
    assert ot.refrescar(db, viejo["refresh_token"], "cli") is None
    assert ot.validar_access(db, nuevo["access_token"]) is None
    assert ot.refrescar(db, nuevo["refresh_token"], "cli") is None


def test_revocar_con_el_refresh_mata_tambien_el_access(db, uid):
    tokens = _tokens(db, uid)
    ot.revocar(db, tokens["refresh_token"])
    assert ot.validar_access(db, tokens["access_token"]) is None


def _ultimo_uso(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT ultimo_uso FROM oauth_tokens").fetchone()[0]
    finally:
        conn.close()


def test_ultimo_uso_se_escribe_como_mucho_una_vez_por_minuto(db, uid, monkeypatch):
    tokens = _tokens(db, uid)
    assert _ultimo_uso(db) is None
    ot.validar_access(db, tokens["access_token"])
    primero = _ultimo_uso(db)
    assert primero
    ot.validar_access(db, tokens["access_token"])
    assert _ultimo_uso(db) == primero
    _adelantar(monkeypatch, minutes=2)
    ot.validar_access(db, tokens["access_token"])
    assert _ultimo_uso(db) > primero


def test_dos_refresh_simultaneos_emiten_un_solo_par(db, uid, monkeypatch):
    tokens = _tokens(db, uid)
    barrera = threading.Barrier(2, timeout=10)
    # Los dos hilos leen el refresh vivo y recién ahí se encuentran: la carrera.
    monkeypatch.setattr(ot, "_antes_de_revocar", barrera.wait)
    resultados = []

    def correr():
        resultados.append(ot.refrescar(db, tokens["refresh_token"], "cli"))

    hilos = [threading.Thread(target=correr) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=30)
    assert len(resultados) == 2
    assert sum(r is not None for r in resultados) == 1
    conn = sqlite3.connect(db)
    vivos = conn.execute("SELECT COUNT(*) FROM oauth_tokens WHERE revocado_en IS NULL").fetchone()[0]
    conn.close()
    assert vivos == 1


def test_cambiar_la_contrasena_revoca_los_tokens_y_borra_los_codigos(db, uid):
    import database
    tokens = _tokens(db, uid)
    _, challenge = _pkce()
    ot.emitir_codigo(db, uid, "cli", "https://r/cb", challenge, "crm:leer", None)
    database.update_user_password(db, uid, "otro-hash")
    assert ot.validar_access(db, tokens["access_token"]) is None
    assert ot.refrescar(db, tokens["refresh_token"], "cli") is None
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM oauth_codigos WHERE user_id = ?",
                            (uid,)).fetchone()[0] == 0
    finally:
        conn.close()
