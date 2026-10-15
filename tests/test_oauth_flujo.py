"""El OAuth del conector MCP, por HTTP, como lo ve Claude.

Las URLs anunciadas salen de CRM_URL, nunca del Host del request. Los tests la
fijan en https://crm.test.
"""

import base64
import hashlib
import logging
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
    # Werkzeug deja "/" y "?" sin escapar en el valor; lo que importa es que
    # al decodificar vuelva el pedido completo.
    siguiente = _query(r.headers["Location"])["next"]
    assert siguiente.startswith("/oauth/authorize?")
    assert f"client_id={cid}" in siguiente and "state=abc123" in siguiente


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
    ({"code_challenge": "corto"}, "invalid_request"),
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


# ── endurecimiento: cabeceras, redirect saneada, cookie, nonce, XSS ──────────

def test_consentimiento_no_se_puede_embeber_ni_cachear(logueado):
    cid = _registrar(logueado)
    _, ch = _pkce()
    r = logueado.get("/oauth/authorize?" + urlencode(_params_authorize(cid, ch)))
    assert r.status_code == 200
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store"


def test_redirect_con_codigo_no_se_cachea(logueado):
    cid = _registrar(logueado)
    _, ch = _pkce()
    r = _autorizar(logueado, cid, ch)
    assert r.status_code == 302
    assert r.headers["Cache-Control"] == "no-store"


def test_metadata_sigue_cacheable(anonimo):
    r = anonimo.get("/.well-known/oauth-authorization-server")
    assert "max-age=3600" in r.headers["Cache-Control"]
    assert r.headers["X-Frame-Options"] == "DENY"


def test_error_con_redirect_uri_con_espacio_vuelve_a_la_registrada(logueado):
    cid = _registrar(logueado)
    _, ch = _pkce()
    r = logueado.get("/oauth/authorize?" + urlencode(
        _params_authorize(cid, ch, redirect_uri=" " + CALLBACK, response_type="token")))
    assert r.status_code == 302
    assert r.headers["Location"].startswith(CALLBACK + "?")
    assert _query(r.headers["Location"])["state"] == "abc123"


def test_cookie_de_sesion_lax_y_secure_con_crm_https(anonimo, uid):
    r = anonimo.post("/login", data={"email": "ana@scalerics.com", "password": "x" * 10})
    cookie = r.headers["Set-Cookie"]
    assert "SameSite=Lax" in cookie and "Secure" in cookie and "HttpOnly" in cookie


def test_nonce_no_se_puede_reusar(logueado):
    cid = _registrar(logueado)
    _, ch = _pkce()
    html = logueado.get("/oauth/authorize?" + urlencode(_params_authorize(cid, ch))
                        ).get_data(as_text=True)
    nonce = html.split('name="nonce" value="')[1].split('"')[0]
    form = _params_authorize(cid, ch)
    form.update({"nonce": nonce, "accion": "autorizar"})
    assert logueado.post("/oauth/authorize", data=form).status_code == 302
    assert logueado.post("/oauth/authorize", data=form).status_code == 403


def test_post_con_redirect_uri_adulterada_es_400_sin_location(logueado):
    cid = _registrar(logueado)
    _, ch = _pkce()
    html = logueado.get("/oauth/authorize?" + urlencode(_params_authorize(cid, ch))
                        ).get_data(as_text=True)
    nonce = html.split('name="nonce" value="')[1].split('"')[0]
    form = _params_authorize(cid, ch, redirect_uri="https://evil.test/cb")
    form.update({"nonce": nonce, "accion": "autorizar"})
    r = logueado.post("/oauth/authorize", data=form)
    assert r.status_code == 400
    assert "Location" not in r.headers


def test_nombre_de_usuario_se_escapa_en_el_consentimiento(app, ruta):
    uid_xss = create_user(ruta, name="<script>x</script>", email="xss@scalerics.com",
                          phone="098", password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid_xss
        s["user_name"] = "<script>x</script>"
    cid = _registrar(c)
    _, ch = _pkce()
    html = c.get("/oauth/authorize?" + urlencode(_params_authorize(cid, ch))
                 ).get_data(as_text=True)
    assert "&lt;script&gt;" in html
    assert "<script>x" not in html


def test_login_ignora_next_con_barra_invertida_o_prefijo_parecido(anonimo, uid):
    for malo in (r"\evil.test","/oauth/authorizeXYZ", "/oauth/authorize/../admin"):
        r = anonimo.post("/login", query_string={"next": malo},
                         data={"email": "ana@scalerics.com", "password": "x" * 10})
        assert r.status_code == 302
        assert r.headers["Location"] in ("/", "http://localhost/")


def test_429_de_registro_trae_retry_after(anonimo):
    for _ in range(oauth_ruta.TOPE_REGISTROS_POR_IP):
        assert anonimo.post("/oauth/register", json={"redirect_uris": [CALLBACK]}).status_code == 201
    r = anonimo.post("/oauth/register", json={"redirect_uris": [CALLBACK]})
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "3600"


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


@pytest.mark.parametrize("cambio", [{"client_id": "otro-cliente"},
                                    {"redirect_uri": "https://claude.ai/otra"}])
def test_canje_con_client_id_o_redirect_ajenos_es_invalid_grant(logueado, cambio):
    cid = _registrar(logueado)
    verifier, challenge = _pkce()
    code = _code(logueado, cid, challenge)
    r = _canjear(logueado, cid, code, verifier, **cambio)
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"


def test_refresh_con_client_id_ajeno_es_invalid_grant(logueado):
    cid = _registrar(logueado)
    verifier, challenge = _pkce()
    t = _canjear(logueado, cid, _code(logueado, cid, challenge), verifier).get_json()
    r = logueado.post("/oauth/token", data={"grant_type": "refresh_token",
                                            "refresh_token": t["refresh_token"],
                                            "client_id": "otro-cliente"})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"


def test_token_rechaza_cuerpo_demasiado_grande_con_json(logueado):
    r = logueado.post("/oauth/token", data={"grant_type": "authorization_code",
                                            "code": "x" * (oauth_ruta.TOPE_CUERPO_REGISTRO + 1)})
    assert r.status_code == 413 and r.get_json()["error"] == "invalid_request"


def test_canje_rechazado_deja_un_warning_sin_el_codigo(logueado, caplog):
    cid = _registrar(logueado)
    _, challenge = _pkce()
    code = _code(logueado, cid, challenge)
    caplog.set_level(logging.WARNING)
    assert _canjear(logueado, cid, code, "verifier-malo").status_code == 400
    avisos = [r.getMessage() for r in caplog.records if "rechazado" in r.getMessage()]
    assert avisos, "falta el warning del canje rechazado"
    assert cid in avisos[0]
    assert all(code not in a for a in avisos)


def test_revoke_lleva_no_store_y_pragma(logueado):
    r = logueado.post("/oauth/revoke", data={"token": "nada"})
    assert r.headers["Cache-Control"] == "no-store" and r.headers["Pragma"] == "no-cache"
