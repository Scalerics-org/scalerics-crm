"""Quién puede pedir un token: el CIMD publicado de Claude o un cliente
registrado por DCR. Y contra qué `redirect_uri` se compara."""

import base64
import hashlib
import secrets
import sqlite3
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

from database import create_user, init_db
from services import oauth_clientes as oc
from services import oauth_tokens as ot

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
    def fake(url, timeout, allow_redirects):
        if llamadas is not None:
            llamadas.append(url)
        assert timeout == 5
        assert allow_redirects is False
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
    def fake(url, timeout, allow_redirects):
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
    {"redirect_uris": ["https://claude.ai/api/mcp/auth_callback#frag"]},
])
def test_registrar_rechaza_redirects_invalidos(db, datos):
    assert oc.registrar_cliente(db, datos) is None


def test_registrar_solo_acepta_los_callbacks_de_claude(db):
    # Un https cualquiera permitiría phishing de consentimiento.
    assert oc.registrar_cliente(db, {"redirect_uris": ["https://evil.test/cb"]}) is None
    assert oc.registrar_cliente(db, {"redirect_uris": [CALLBACK_CLAUDE, "https://evil.test/cb"]}) is None
    assert oc.registrar_cliente(db, {"redirect_uris": ["https://claude.ai/api/mcp/otro"]}) is None
    assert oc.registrar_cliente(
        db, {"redirect_uris": ["https://claude.com/api/mcp/auth_callback"]}) is not None


def test_registrar_acepta_loopback_http(db):
    c = oc.registrar_cliente(db, {"redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"]})
    assert c is not None


def test_cliente_desconocido_es_none(db):
    assert oc.redirect_uris_de(db, "no-existe") is None
    assert oc.redirect_uris_de(db, "") is None


def test_tope_de_clientes_poda_los_viejos_sin_tokens(db, monkeypatch):
    monkeypatch.setattr(oc, "TOPE_CLIENTES", 5)
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


def _reloj(monkeypatch, inicio=1000.0):
    """Reloj controlable para `oc.time.monotonic`, sin tocar el `time` global."""
    ahora = {"t": inicio}
    monkeypatch.setattr(oc, "time", SimpleNamespace(monotonic=lambda: ahora["t"]))
    return ahora


def test_cache_del_cimd_vence_y_se_vuelve_a_descargar(db, monkeypatch):
    reloj = _reloj(monkeypatch)
    llamadas = []
    _mock_get(monkeypatch, {"client_id": CIMD_CLAUDE, "redirect_uris": [CALLBACK_CLAUDE]}, llamadas)
    oc.redirect_uris_de(db, CIMD_CLAUDE)
    reloj["t"] += oc.CACHE_SEGUNDOS - 1
    oc.redirect_uris_de(db, CIMD_CLAUDE)
    assert len(llamadas) == 1
    reloj["t"] += 2
    assert oc.redirect_uris_de(db, CIMD_CLAUDE) == [CALLBACK_CLAUDE]
    assert len(llamadas) == 2


def test_cimd_que_falla_no_se_reintenta_dentro_de_60_segundos(db, monkeypatch):
    reloj = _reloj(monkeypatch)
    llamadas = []

    def fake(url, timeout, allow_redirects):
        llamadas.append(url)
        raise oc.requests.ConnectionError("sin red")
    monkeypatch.setattr(oc.requests, "get", fake)

    assert oc.redirect_uris_de(db, CIMD_CLAUDE) is None
    reloj["t"] += 59
    assert oc.redirect_uris_de(db, CIMD_CLAUDE) is None
    assert len(llamadas) == 1
    reloj["t"] += 2  # 61 s desde el fallo
    assert oc.redirect_uris_de(db, CIMD_CLAUDE) is None
    assert len(llamadas) == 2


@pytest.mark.parametrize("client_id", [
    "https://claude.ai@evil.test/x",
    "https://claude.ai.evil.test/x",
])
def test_cimd_con_userinfo_o_subdominio_falso_se_rechaza_sin_descargar(db, monkeypatch, client_id):
    llamadas = []
    _mock_get(monkeypatch, {"client_id": client_id, "redirect_uris": ["https://evil.test/cb"]}, llamadas)
    assert oc.redirect_uris_de(db, client_id) is None
    assert llamadas == []


@pytest.mark.parametrize("uris", [
    [CALLBACK_CLAUDE, 5],
    [CALLBACK_CLAUDE, "http://evil.test/cb"],
    [CALLBACK_CLAUDE + "#frag"],
])
def test_cimd_con_redirect_invalida_se_rechaza(db, monkeypatch, uris):
    _mock_get(monkeypatch, {"client_id": CIMD_CLAUDE, "redirect_uris": uris})
    assert oc.redirect_uris_de(db, CIMD_CLAUDE) is None


def test_registrar_rechaza_mas_de_diez_redirects(db):
    uris = [CALLBACK_CLAUDE] * (oc.TOPE_REDIRECTS + 1)
    assert oc.registrar_cliente(db, {"redirect_uris": uris}) is None
    assert oc.registrar_cliente(db, {"redirect_uris": uris[:oc.TOPE_REDIRECTS]}) is not None


def test_redirect_valida_rechaza_demasiado_larga_y_fragmento():
    # Estas reglas siguen valiendo para las redirects de un CIMD.
    assert not oc._redirect_valida("https://x.test/" + "a" * oc.LARGO_MAX_REDIRECT)
    assert not oc._redirect_valida("https://x.test/cb#frag")
    assert oc._redirect_valida("https://x.test/cb")


@pytest.mark.parametrize("client_id", [
    "https://evil.com\\@claude.ai/x",
    "https://claude.ai:8443/x",
    "https://claude.ai/x?y=1",
    "https://claude.ai/x#f",
    "https://claude.ai/\nX",
])
def test_cimd_con_url_ambigua_se_rechaza_sin_descargar(db, monkeypatch, client_id):
    # urlsplit y requests no leen igual `\`, `@`, puerto, query ni fragmento.
    llamadas = []
    _mock_get(monkeypatch, {"client_id": client_id, "redirect_uris": [CALLBACK_CLAUDE]}, llamadas)
    assert not oc.es_cimd(client_id)
    assert oc.redirect_uris_de(db, client_id) is None
    assert llamadas == []


def _pkce():
    verifier = secrets.token_urlsafe(40)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _ids_por_antiguedad(db):
    conn = sqlite3.connect(db)
    try:
        return [f[0] for f in conn.execute(
            "SELECT client_id FROM oauth_clientes ORDER BY created_at, rowid")]
    finally:
        conn.close()


def _espaciar_created_at(db):
    """Los registros del test caen en el mismo segundo: se les da un orden fijo."""
    conn = sqlite3.connect(db)
    try:
        filas = conn.execute("SELECT client_id FROM oauth_clientes ORDER BY rowid").fetchall()
        for i, (cid,) in enumerate(filas):
            conn.execute("UPDATE oauth_clientes SET created_at = ? WHERE client_id = ?",
                         (f"2026-01-01T00:00:{i:02d}+00:00", cid))
        conn.commit()
    finally:
        conn.close()


def test_poda_borra_el_mas_viejo_cuando_no_tiene_tokens(db, monkeypatch):
    monkeypatch.setattr(oc, "TOPE_CLIENTES", 5)
    for i in range(5):
        oc.registrar_cliente(db, {"client_name": f"c{i}", "redirect_uris": [CALLBACK_CLAUDE]})
    _espaciar_created_at(db)
    viejo, segundo = _ids_por_antiguedad(db)[:2]
    oc.registrar_cliente(db, {"client_name": "nuevo", "redirect_uris": [CALLBACK_CLAUDE]})
    ids = _ids_por_antiguedad(db)
    assert len(ids) == 5
    assert viejo not in ids and segundo in ids


def test_cliente_con_token_vivo_sobrevive_a_la_poda(db, monkeypatch):
    monkeypatch.setattr(oc, "TOPE_CLIENTES", 5)
    for i in range(5):
        oc.registrar_cliente(db, {"client_name": f"c{i}", "redirect_uris": [CALLBACK_CLAUDE]})
    _espaciar_created_at(db)
    primero, segundo = _ids_por_antiguedad(db)[:2]
    uid = create_user(db, name="Ana", email="ana@scalerics.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    verifier, challenge = _pkce()
    code = ot.emitir_codigo(db, uid, primero, CALLBACK_CLAUDE, challenge, "crm:leer", None)
    assert ot.canjear_codigo(db, code, primero, CALLBACK_CLAUDE, verifier) is not None

    oc.registrar_cliente(db, {"client_name": "nuevo", "redirect_uris": [CALLBACK_CLAUDE]})
    ids = _ids_por_antiguedad(db)
    assert len(ids) == 5
    assert primero in ids and segundo not in ids


def test_cache_de_cimd_tiene_tope_y_purga_las_vencidas(db, monkeypatch):
    monkeypatch.setattr(oc, "TOPE_CACHE", 3)
    reloj = _reloj(monkeypatch)

    def fake(url, timeout, allow_redirects):
        raise oc.requests.ConnectionError("sin red")
    monkeypatch.setattr(oc.requests, "get", fake)

    # Lleno y sin nada vencido: no guarda más, pero igual responde None.
    for i in range(oc.TOPE_CACHE + 5):
        assert oc.redirect_uris_de(db, f"https://claude.ai/falso{i}") is None
        assert len(oc._cache_cimd) <= oc.TOPE_CACHE
    assert len(oc._cache_cimd) == oc.TOPE_CACHE

    # Vencidas las entradas, la purga deja lugar para las nuevas.
    reloj["t"] += oc.CACHE_FALLO_SEGUNDOS + 1
    assert oc.redirect_uris_de(db, "https://claude.ai/nuevo") is None
    assert list(oc._cache_cimd) == ["https://claude.ai/nuevo"]
