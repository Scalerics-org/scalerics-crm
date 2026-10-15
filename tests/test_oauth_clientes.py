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
