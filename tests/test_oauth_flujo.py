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
