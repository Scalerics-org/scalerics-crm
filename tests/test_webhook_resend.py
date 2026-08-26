"""El webhook de Resend: rebotes y quejas entran a la lista de vedados.

La verificacion de firma no es ceremonia. Sin ella cualquiera que conozca la
URL puede vedar direcciones —o vedarlas todas— y dejar las dos campanas mudas
sin que nadie entienda por que. El endpoint es publico por definicion: Resend
tiene que poder llamarlo sin credenciales nuestras.

Resend firma con Svix: HMAC-SHA256 sobre "{id}.{timestamp}.{cuerpo}" con el
secreto en base64 despues del prefijo "whsec_".
"""

import base64
import hashlib
import hmac
import json
import time

import pytest

import dashboard
from database import init_db
from services.mails_vedados import esta_vedado, listar_vedados

_SECRETO = "whsec_" + base64.b64encode(b"un secreto de prueba de 32 bytes!").decode()


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", _SECRETO)
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    app = dashboard.create_app(ruta)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def cliente(app):
    return app.test_client()


def _firmar(cuerpo: bytes, msg_id="msg_1", ts=None):
    ts = str(int(ts if ts is not None else time.time()))
    secreto = base64.b64decode(_SECRETO.split("_", 1)[1])
    firma = hmac.new(secreto, f"{msg_id}.{ts}.{cuerpo.decode()}".encode(),
                     hashlib.sha256).digest()
    return {"svix-id": msg_id, "svix-timestamp": ts,
            "svix-signature": "v1," + base64.b64encode(firma).decode()}


def _evento(tipo, email="rebota@x.uy", **extra):
    datos = {"to": [email], "subject": "Una idea para X"}
    datos.update(extra)
    return json.dumps({"type": tipo, "data": datos}).encode()


def _postear(cliente, cuerpo, cabeceras=None):
    return cliente.post("/api/resend/webhook", data=cuerpo,
                        headers={**(cabeceras or {}), "Content-Type": "application/json"})


# ─── La firma ─────────────────────────────────────────────────────────────────

def test_sin_firma_se_rechaza(app, cliente):
    cuerpo = _evento("email.bounced")
    assert _postear(cliente, cuerpo).status_code == 401
    assert listar_vedados(app.config["DB_PATH"]) == []


def test_con_firma_invalida_se_rechaza(app, cliente):
    cuerpo = _evento("email.bounced")
    cabeceras = _firmar(cuerpo)
    cabeceras["svix-signature"] = "v1," + base64.b64encode(b"cualquier cosa").decode()
    assert _postear(cliente, cuerpo, cabeceras).status_code == 401
    assert listar_vedados(app.config["DB_PATH"]) == []


def test_una_firma_de_otro_cuerpo_no_sirve(app, cliente):
    """Firmar A y mandar B es el ataque obvio si solo se compara "hay firma"."""
    cabeceras = _firmar(_evento("email.bounced", "inocente@x.uy"))
    otro = _evento("email.bounced", "victima@x.uy")
    assert _postear(cliente, otro, cabeceras).status_code == 401


def test_una_firma_vieja_se_rechaza(app, cliente):
    """Sin ventana de tiempo, una peticion capturada sirve para siempre."""
    cuerpo = _evento("email.bounced")
    viejo = time.time() - 60 * 60 * 24
    assert _postear(cliente, cuerpo, _firmar(cuerpo, ts=viejo)).status_code == 401


def test_con_firma_valida_se_acepta(app, cliente):
    cuerpo = _evento("email.bounced")
    assert _postear(cliente, cuerpo, _firmar(cuerpo)).status_code == 200


def test_sin_secreto_configurado_no_se_acepta_nada(tmp_path, monkeypatch):
    """Una config a medias no puede dejar el endpoint abierto."""
    monkeypatch.delenv("RESEND_WEBHOOK_SECRET", raising=False)
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    app = dashboard.create_app(ruta)
    app.config["TESTING"] = True
    cuerpo = _evento("email.bounced")
    r = app.test_client().post("/api/resend/webhook", data=cuerpo,
                               headers={**_firmar(cuerpo), "Content-Type": "application/json"})
    assert r.status_code == 401


# ─── Los eventos ──────────────────────────────────────────────────────────────

def test_un_rebote_duro_veda_la_direccion(app, cliente):
    cuerpo = _evento("email.bounced", "rebota@x.uy",
                     bounce={"type": "Permanent", "subType": "NoEmail"})
    _postear(cliente, cuerpo, _firmar(cuerpo))
    assert esta_vedado(app.config["DB_PATH"], "rebota@x.uy") is True
    assert listar_vedados(app.config["DB_PATH"])[0]["motivo"] == "rebote_duro"


def test_un_rebote_blando_NO_veda(app, cliente):
    """Una casilla llena o un servidor caido un rato no es una direccion muerta.
    Vedarla seria tirar un contacto bueno por un problema temporal."""
    cuerpo = _evento("email.bounced", "llena@x.uy",
                     bounce={"type": "Transient", "subType": "MailboxFull"})
    _postear(cliente, cuerpo, _firmar(cuerpo))
    assert esta_vedado(app.config["DB_PATH"], "llena@x.uy") is False


def test_una_queja_de_spam_veda(app, cliente):
    cuerpo = _evento("email.complained", "molesto@x.uy")
    _postear(cliente, cuerpo, _firmar(cuerpo))
    assert listar_vedados(app.config["DB_PATH"])[0]["motivo"] == "queja_spam"


def test_una_entrega_normal_no_veda_nada(app, cliente):
    cuerpo = _evento("email.delivered", "bien@x.uy")
    _postear(cliente, cuerpo, _firmar(cuerpo))
    assert listar_vedados(app.config["DB_PATH"]) == []


def test_un_evento_desconocido_no_explota(app, cliente):
    cuerpo = _evento("email.something.new", "raro@x.uy")
    assert _postear(cliente, cuerpo, _firmar(cuerpo)).status_code == 200


def test_varios_destinatarios_se_vedan_todos(app, cliente):
    cuerpo = json.dumps({"type": "email.complained",
                         "data": {"to": ["uno@x.uy", "dos@x.uy"]}}).encode()
    _postear(cliente, cuerpo, _firmar(cuerpo))
    assert esta_vedado(app.config["DB_PATH"], "uno@x.uy") is True
    assert esta_vedado(app.config["DB_PATH"], "dos@x.uy") is True


def test_el_mismo_evento_dos_veces_no_rompe(app, cliente):
    """Resend reintenta si no contestamos 200 rapido."""
    cuerpo = _evento("email.complained", "repetido@x.uy")
    cabeceras = _firmar(cuerpo)
    assert _postear(cliente, cuerpo, cabeceras).status_code == 200
    assert _postear(cliente, cuerpo, cabeceras).status_code == 200
    assert len(listar_vedados(app.config["DB_PATH"])) == 1


def test_un_cuerpo_que_no_es_json_no_tumba_el_endpoint(app, cliente):
    cuerpo = b"esto no es json"
    r = _postear(cliente, cuerpo, _firmar(cuerpo))
    assert r.status_code == 200
    assert listar_vedados(app.config["DB_PATH"]) == []


def test_un_evento_sin_destinatario_no_veda_vacio(app, cliente):
    """Vedar "" haria matchear a todo lead sin mail y dejaria las campanas mudas."""
    cuerpo = json.dumps({"type": "email.bounced",
                         "data": {"bounce": {"type": "Permanent"}}}).encode()
    _postear(cliente, cuerpo, _firmar(cuerpo))
    assert listar_vedados(app.config["DB_PATH"]) == []
