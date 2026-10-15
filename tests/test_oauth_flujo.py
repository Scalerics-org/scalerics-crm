"""El OAuth del conector MCP, por HTTP, como lo ve Claude.

Las URLs anunciadas salen de CRM_URL, nunca del Host del request. Los tests la
fijan en https://crm.test.
"""

import base64
import hashlib
import secrets
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from routes import oauth as oauth_ruta
from services import oauth_clientes as oc

BASE = "https://crm.test"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"


def _pkce():
    verifier = secrets.token_urlsafe(40)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@pytest.fixture(autouse=True)
def _sin_registros_previos():
    """El tope de registros por IP vive en memoria del proceso: que un test no
    herede las altas de otro."""
    oauth_ruta._REGISTROS_POR_IP.clear()
    yield
    oauth_ruta._REGISTROS_POR_IP.clear()


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


def test_register_omite_client_name_si_no_vino(anonimo):
    r = anonimo.post("/oauth/register", json={"redirect_uris": [CALLBACK]})
    assert r.status_code == 201
    assert "client_name" not in r.get_json()
    assert r.headers["Pragma"] == "no-cache" and r.headers["Cache-Control"] == "no-store"


def test_register_rechaza_cuerpo_demasiado_grande(anonimo):
    grande = {"client_name": "x" * (oauth_ruta.TOPE_CUERPO_REGISTRO + 1),
              "redirect_uris": [CALLBACK]}
    r = anonimo.post("/oauth/register", json=grande)
    assert r.status_code == 413
    assert r.get_json()["error"] == "invalid_client_metadata"


def test_register_tope_por_ip_y_se_libera_con_la_ventana(anonimo, monkeypatch):
    ahora = [1_000_000.0]
    monkeypatch.setattr(oauth_ruta, "time", SimpleNamespace(time=lambda: ahora[0]))
    cuerpo = {"client_name": "Claude", "redirect_uris": [CALLBACK]}
    for _ in range(oauth_ruta.TOPE_REGISTROS_POR_IP):
        assert anonimo.post("/oauth/register", json=cuerpo).status_code == 201
    r = anonimo.post("/oauth/register", json=cuerpo)
    assert r.status_code == 429
    assert r.get_json()["error"] == "invalid_client_metadata"
    # Otra IP no se ve afectada.
    otra = anonimo.post("/oauth/register", json=cuerpo, headers={"Fly-Client-IP": "203.0.113.9"})
    assert otra.status_code == 201
    # Pasada la ventana, la primera IP vuelve a poder registrar.
    ahora[0] += oauth_ruta.VENTANA_REGISTROS_SEG + 1
    assert anonimo.post("/oauth/register", json=cuerpo).status_code == 201


def test_register_el_mapa_de_ips_no_crece_sin_limite(anonimo):
    cuerpo = {"client_name": "Claude", "redirect_uris": [CALLBACK]}
    for i in range(oauth_ruta.MAX_IPS_REGISTRO + 5):
        r = anonimo.post("/oauth/register", json=cuerpo,
                         headers={"Fly-Client-IP": f"10.{i // 250}.{i % 250}.1"})
        assert r.status_code == 201
    assert len(oauth_ruta._REGISTROS_POR_IP) <= oauth_ruta.MAX_IPS_REGISTRO


# ── el candado de sesión sigue en pie fuera de lo nuestro ────────────────────

def test_oauth_gmail_de_fidelidad_sigue_pidiendo_sesion(anonimo):
    r = anonimo.get("/oauth/gmail/conectar")
    assert r.status_code in (302, 403)
    assert "google" not in r.headers.get("Location", "")


def test_issuer_sin_barra_final_aunque_crm_url_la_tenga(anonimo, monkeypatch):
    monkeypatch.setenv("CRM_URL", BASE + "/")
    m = anonimo.get("/.well-known/oauth-authorization-server").get_json()
    assert m["issuer"] == BASE
    assert m["token_endpoint"] == BASE + "/oauth/token"
