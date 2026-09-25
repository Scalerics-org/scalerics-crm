"""Mail con borrador desde la lista de Fidelidad (pedido de Juan, 25/9).

- el botón abre el mail ya escrito, distinto para restaurantes y peluquerías
- sale de la casilla de quien lo manda (su Gmail, conectado una vez)
- queda anotado y deja una llamada de seguimiento a los dos días hábiles
- no se le manda a quien pidió la baja

Google no se llama nunca: `requests` está roto en conftest y acá se reemplaza.
Hoy está fijo en el martes 23/9/2026, 10:30 de Montevideo.
"""

import base64
import email
import sqlite3
from datetime import datetime

import pytest
from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash

import dashboard
import routes.fidelidad as rutas
from database import create_user, init_db
from services import fidelidad as fid
from services import gmail_usuario as gmail
from services.mails_vedados import vedar

AHORA = datetime(2026, 9, 23, 10, 30)          # martes


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDENCIALES_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("GMAIL_WEB_CLIENT_ID", "cliente")
    monkeypatch.setenv("GMAIL_WEB_CLIENT_SECRET", "secreto")
    ruta = str(tmp_path / "mail.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def uid(db):
    return create_user(db, name="Lucas Pérez", email="lucas@gmail.com", phone="099 555 111",
                       password_hash=generate_password_hash("x" * 10))


@pytest.fixture
def cli(db, uid, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "lucas@gmail.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(rutas, "_ahora", lambda: AHORA)
    app = dashboard.create_app(db)
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Lucas Pérez"
    return c


def _p(db, nombre="Pizzería La Esquina", **extra):
    datos = {"nombre": nombre, "zona": "Municipio CH", "barrio": "Pocitos", "tipo": "Pizzería",
             "telefono": "099 123 456", "rating": 4.4, "resenas": 423}
    datos.update(extra)
    pid, que = fid.crear_prospecto(db, datos)
    assert que == "creado"
    return pid


class _Resp:
    def __init__(self, status, datos):
        self.status_code, self._d, self.content = status, datos, b"x"

    def json(self):
        return self._d


def _google(monkeypatch, enviados, token_ok=True):
    """Google de mentira: token, userinfo y el envío."""
    def post(url, data=None, json=None, headers=None, timeout=None):
        if url == gmail.TOKEN_URL:
            if not token_ok:
                return _Resp(400, {"error": "invalid_grant"})
            return _Resp(200, {"access_token": "acceso", "refresh_token": "refresco"})
        enviados.append({"url": url, "raw": json["raw"], "auth": headers["Authorization"]})
        return _Resp(200, {"id": "gm-1"})

    def get(url, headers=None, timeout=None):
        return _Resp(200, {"email": "Lucas@Gmail.com"})

    monkeypatch.setattr(gmail.requests, "post", post)
    monkeypatch.setattr(gmail.requests, "get", get)


# ── el borrador ──────────────────────────────────────────────────────────────

def test_el_borrador_viene_escrito_y_firmado(db):
    p = fid.get_prospecto(db, _p(db))
    b = fid.borrador(p, "Lucas Pérez", "099 555 111")
    assert b["asunto"] == "Que tus clientes de Pocitos vuelvan más seguido"
    assert "Soy Lucas, de Scalerics" in b["cuerpo"] and "más de 420 reseñas" in b["cuerpo"]
    assert fid.DEMO_URL in b["cuerpo"] and "carta digital" in b["cuerpo"]
    assert b["cuerpo"].endswith("Lucas Pérez\nScalerics Fidelidad\n099 555 111")
    pelu = fid.get_prospecto(db, _p(db, "Barbería Don Pepe", tipo="Barbería", telefono="098 111 222"))
    b = fid.borrador(pelu, "Lucas Pérez")
    assert b["asunto"] == "Que tus clientes vuelvan más seguido a Barbería Don Pepe"
    assert "cada visita suma puntos" in b["cuerpo"] and "carta digital" not in b["cuerpo"]


# ── conectar el Gmail ────────────────────────────────────────────────────────

def test_conectar_guarda_el_token_cifrado(db, uid, monkeypatch):
    _google(monkeypatch, [])
    assert gmail.conectar(db, uid, "codigo", "https://crm/oauth/gmail/callback") == "lucas@gmail.com"
    fila = sqlite3.connect(db).execute("SELECT email, refresh_token FROM usuarios_gmail").fetchone()
    assert fila[0] == "lucas@gmail.com" and fila[1] != "refresco"   # nunca en texto plano
    assert gmail.estado(db, uid)["conectado"]


def test_la_url_de_google_pide_solo_mandar(db):
    url = gmail.url_autorizacion("https://crm/oauth/gmail/callback", "st", "lucas@gmail.com")
    assert "gmail.send" in url and "gmail.readonly" not in url
    assert "access_type=offline" in url and "prompt=consent" in url and "state=st" in url


def test_el_callback_valida_el_state(cli, db, uid, monkeypatch):
    _google(monkeypatch, [])
    r = cli.get("/oauth/gmail/conectar")
    assert r.status_code == 302 and r.location.startswith(gmail.AUTH_URL)
    with cli.session_transaction() as s:
        estado = s["gmail_state"]
    assert "gmail=error" in cli.get("/oauth/gmail/callback?code=x&state=otro").location
    assert not gmail.estado(db, uid)["conectado"]
    cli.get("/oauth/gmail/conectar")
    with cli.session_transaction() as s:
        estado = s["gmail_state"]
    assert "gmail=ok" in cli.get(f"/oauth/gmail/callback?code=x&state={estado}").location
    assert gmail.estado(db, uid)["email"] == "lucas@gmail.com"


# ── mandar ───────────────────────────────────────────────────────────────────

def test_sale_de_la_casilla_de_quien_lo_manda(cli, db, uid, monkeypatch):
    enviados = []
    _google(monkeypatch, enviados)
    gmail.conectar(db, uid, "codigo", "https://crm/cb")
    pid = _p(db)
    b = cli.get(f"/api/fidelidad/prospectos/{pid}/borrador").get_json()
    assert b["gmail"]["conectado"] and b["gmail"]["email"] == "lucas@gmail.com"
    r = cli.post(f"/api/fidelidad/prospectos/{pid}/mail",
                 json={"para": "dueno@laesquina.com", "asunto": b["asunto"], "cuerpo": b["cuerpo"]})
    assert r.status_code == 201 and r.get_json()["de"] == "lucas@gmail.com"
    msj = email.message_from_bytes(base64.urlsafe_b64decode(enviados[0]["raw"]))
    assert "lucas@gmail.com" in msj["From"] and "Lucas" in str(email.header.make_header(email.header.decode_header(msj["From"])))
    assert msj["To"] == "dueno@laesquina.com"
    p = fid.get_prospecto(db, pid)
    assert p["email"] == "dueno@laesquina.com"          # queda guardado
    assert p["proxima_llamada"] == "2026-09-25 11:00"   # seguimiento a 2 días hábiles
    assert p["mails"][0]["de"] == "lucas@gmail.com" and p["llamadas"] == []   # no es una llamada
    item = fid.armar_lista(db, "Montevideo", "restaurante", cuando=AHORA)["items"][0]
    assert item["ultimo_mail"] == "2026-09-23 10:30"


def test_sin_gmail_conectado_pide_conectar(cli, db):
    pid = _p(db)
    r = cli.post(f"/api/fidelidad/prospectos/{pid}/mail", json={"para": "a@b.com", "asunto": "x", "cuerpo": "y"})
    assert r.status_code == 409 and r.get_json()["reconectar"]


def test_si_google_revoco_el_permiso_se_desconecta(cli, db, uid, monkeypatch):
    _google(monkeypatch, [])
    gmail.conectar(db, uid, "codigo", "https://crm/cb")
    _google(monkeypatch, [], token_ok=False)
    r = cli.post(f"/api/fidelidad/prospectos/{_p(db)}/mail", json={"para": "a@b.com", "asunto": "x", "cuerpo": "y"})
    assert r.status_code == 409 and "volvé a conectarlo" in r.get_json()["error"]
    assert not gmail.estado(db, uid)["conectado"]


def test_no_se_manda_a_quien_pidio_la_baja_ni_a_un_mail_roto(cli, db, uid, monkeypatch):
    enviados = []
    _google(monkeypatch, enviados)
    gmail.conectar(db, uid, "codigo", "https://crm/cb")
    pid = _p(db)
    vedar(db, "baja@local.com", "baja_pedida")
    assert cli.post(f"/api/fidelidad/prospectos/{pid}/mail",
                    json={"para": "baja@local.com", "asunto": "x", "cuerpo": "y"}).status_code == 400
    assert cli.post(f"/api/fidelidad/prospectos/{pid}/mail",
                    json={"para": "sin-arroba", "asunto": "x", "cuerpo": "y"}).status_code == 400
    assert enviados == []


def test_la_pantalla_tiene_el_boton_de_mail():
    html = dashboard.DASHBOARD_HTML
    assert "function fidMailAbrir" in html and "function fidMailMandar" in html
    assert 'href="/oauth/gmail/conectar"' in html


def test_busca_el_mail_en_la_web_del_comercio(db):
    con_web = _p(db, "Pizzería La Esquina", web="https://laesquina.com.uy")
    sin_mail = _p(db, "Burger B", telefono="099 222 333", web="https://burgerb.uy")
    caido = _p(db, "Burger C", telefono="099 333 444", web="https://caido.uy")
    _p(db, "Sin web", telefono="099 444 555")
    paginas = {"https://laesquina.com.uy": '<a href="mailto:hola@laesquina.com.uy">escribinos</a>',
               "https://burgerb.uy": "<p>sin datos</p>"}
    res = fid.buscar_mails(db, lambda url: paginas.get(url.rstrip("/")), limite=10)
    assert res == {"revisados": 3, "encontrados": 1, "sin_mail": 1, "no_abrio": 1}
    assert fid.get_prospecto(db, con_web)["email"] == "hola@laesquina.com.uy"
    assert fid.get_prospecto(db, sin_mail)["mail_buscado_en"]          # no se vuelve a abrir
    assert fid.get_prospecto(db, caido)["mail_buscado_en"] is None     # se reintenta
    assert fid.buscar_mails(db, lambda url: None, limite=10)["revisados"] == 1
