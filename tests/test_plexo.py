"""Cobro automático con Plexo (services/plexo.py, routes/plexo.py).

Pedido de Juan (24/9): que el cliente cargue la tarjeta una vez y el CRM le
cobre solo cada mes, anotándolo en Finanzas. Plexo se reemplaza por un falso:
los tests no salen a la red.
"""

import json
import sqlite3
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (create_user, crear_recurrente, get_recurrente, init_db,
                      insert_business, listar_movimientos)
from services import plexo

HOY = date.today()
PERIODO = f"{HOY.year:04d}-{HOY.month:02d}"


class PlexoFalso:
    """Responde como la API REST de testing, con lo que vimos el 24/9."""

    def __init__(self):
        self.llamadas = []
        self.tarjetas = []
        self.estado_pago = "approved"
        self.pagos = {}
        self.caida = False

    def __call__(self, cfg, metodo, ruta, cuerpo=None):
        self.llamadas.append((metodo, ruta, cuerpo))
        if metodo == "POST" and ruta == "/v1/customers":
            return 201, {"id": "cus_1"}
        if metodo == "POST" and ruta == "/v1/sessions":
            return 201, {"expiresAt": "2026-09-24T19:00:00Z",
                         "actions": [{"rel": "REDIRECT", "href": "https://tokenizations.testing.plexo.com.uy/abc"}]}
        if metodo == "GET" and ruta.endswith("/payment-instruments"):
            return 200, self.tarjetas
        if metodo == "POST" and ruta == "/v1/payments":
            if self.caida:
                raise OSError("se cortó la conexión")
            pago = {"id": f"pay_{len(self.pagos) + 1}", "referenceId": cuerpo["referenceId"],
                    "status": self.estado_pago,
                    "transactions": [{"resultMessage": "Aprobada" if self.estado_pago == "approved" else "Fondos insuficientes"}]}
            self.pagos[cuerpo["referenceId"]] = pago
            return 201, pago
        if metodo == "GET" and ruta.startswith("/v1/payments/query"):
            return 200, {"items": list(self.pagos.values())}
        return 404, {"message": "no existe " + ruta}


TARJETA_MASTER = {"token": "tok_4444", "cardName": "555555XXXXXX4444", "bin": "555555",
                  "cardType": "credit", "expMonth": 12, "expYear": 2027, "status": "active",
                  "createdAt": "2026-09-24T18:37:00Z"}


@pytest.fixture
def falso(monkeypatch):
    f = PlexoFalso()
    monkeypatch.setattr(plexo, "_http", f)
    monkeypatch.setenv("PLEXO_ENV", "testing")
    monkeypatch.setenv("PLEXO_CLIENT_ID", "1372")
    monkeypatch.setenv("PLEXO_MERCHANT_ID", "15004")
    monkeypatch.setenv("PLEXO_API_KEY", "clave-de-prueba")
    return f


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "p.db")
    init_db(ruta)
    return ruta


def _fijo(db, **extra):
    cid = insert_business(db, {"name": "Diego Hinze", "phone": "099 123 456", "email": "diego@ejemplo.uy"})
    campos = {"tipo": "ingreso", "concepto": "Mantenimiento mensual", "categoria": "mantenimiento",
              "monto": 120, "moneda": "USD", "dia_del_mes": 1, "desde": PERIODO,
              "client_id": cid, "tarjeta": "visa_debito", **extra}
    return get_recurrente(db, crear_recurrente(db, **campos))


def _con_tarjeta(db, falso, **extra):
    fijo = _fijo(db, **extra)
    plexo.pedir_tarjeta(db, fijo, {"name": "Diego Hinze"}, "https://crm.test")
    falso.tarjetas = [TARJETA_MASTER]
    plexo.verificar_tarjeta(db, fijo["id"])
    return get_recurrente(db, fijo["id"])


# ── la key ───────────────────────────────────────────────────────────────────

def _guardar_clave(db, servicio, clave):
    from services.credenciales import cifrar
    c = sqlite3.connect(db)
    c.execute("INSERT INTO credenciales (servicio, clave_cifrada) VALUES (?, ?)", (servicio, cifrar(clave)))
    c.commit()
    c.close()


@pytest.fixture
def con_cifrado(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("CREDENCIALES_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("PLEXO_API_KEY", raising=False)
    monkeypatch.setenv("PLEXO_ENV", "testing")


def test_la_key_sale_de_contrasenas(db, con_cifrado):
    _guardar_clave(db, "Plexo PRODUCCIÓN", "la-real")
    _guardar_clave(db, "Plexo TESTING (API REST)", "la-de-prueba")
    assert plexo.configuracion(db)["api_key"] == "la-de-prueba"


def test_una_sola_de_plexo_con_otro_nombre_sirve(db, con_cifrado):
    _guardar_clave(db, "Plexo API", "la-unica")
    assert plexo.configuracion(db)["api_key"] == "la-unica"


def test_con_dos_de_plexo_ambiguas_no_adivina(db, con_cifrado):
    _guardar_clave(db, "Plexo API", "una")
    _guardar_clave(db, "Plexo dashboard", "otra")
    cfg = plexo.configuracion(db)
    assert cfg["api_key"] is None and "api_key" in cfg["faltan"]


# ── pedir y guardar la tarjeta ───────────────────────────────────────────────

def test_pedir_tarjeta_da_un_link_del_crm_que_no_vence(db, falso):
    fijo = _fijo(db)
    r = plexo.pedir_tarjeta(db, fijo, {"name": "Diego Hinze"}, "https://crm.test")
    assert r["link"].startswith("https://crm.test/plexo/tarjeta/")
    codigo = r["link"].rsplit("/", 1)[1]
    # Cada vez que lo abre, una página de Plexo nueva.
    assert plexo.abrir_link(db, codigo, "https://crm.test") == "https://tokenizations.testing.plexo.com.uy/abc"
    sesion = [c for c in falso.llamadas if c[1] == "/v1/sessions"][-1][2]
    assert sesion["type"] == "tokenization" and sesion["customerId"] == "cus_1"
    assert sesion["settings"]["redirects"]["defaultUrl"] == f"https://crm.test/plexo/listo/{codigo}"
    # Pedirlo de nuevo no crea otro cliente en Plexo ni cambia el link.
    r2 = plexo.pedir_tarjeta(db, fijo, {"name": "Diego Hinze"}, "https://crm.test")
    assert r2["link"] == r["link"]
    assert sum(1 for c in falso.llamadas if c[1] == "/v1/customers") == 1


def test_un_codigo_inventado_no_abre_nada(db, falso):
    assert plexo.abrir_link(db, "x" * 24, "https://crm.test") is None
    assert plexo.abrir_link(db, "corto", "https://crm.test") is None


def test_verificar_activa_la_tarjeta_y_corrige_la_comision(db, falso):
    fijo = _con_tarjeta(db, falso)
    s = plexo.suscripcion(db, fijo["id"])
    assert s["estado"] == "activa" and s["token"] == "tok_4444"
    assert s["tarjeta_texto"].endswith("4444") and s["vence"] == "12/27"
    # Juan puso Visa débito, pero cargó una Master de crédito: la comisión cambia.
    assert fijo["tarjeta"] == "master_credito"
    assert plexo.es_automatico(db, fijo["id"])


def test_sin_tarjeta_cargada_sigue_esperando(db, falso):
    fijo = _fijo(db)
    plexo.pedir_tarjeta(db, fijo, None, "https://crm.test")
    r = plexo.verificar_tarjeta(db, fijo["id"])
    assert r["estado"] == "esperando_tarjeta"
    assert not plexo.es_automatico(db, fijo["id"])


@pytest.mark.parametrize("bin_,tipo,esperado", [
    ("455555", "debit", "visa_debito"), ("555555", "credit", "master_credito"),
    ("222300", "debit", "master_debito"), ("589657", "credit", "oca_credito"),
    ("371234", "credit", None), ("455555", "prepaid", None)])
def test_marca_de_la_tarjeta_por_el_bin(bin_, tipo, esperado):
    assert plexo.tarjeta_de({"bin": bin_, "cardType": tipo}) == esperado


# ── cobrar ───────────────────────────────────────────────────────────────────

def test_cobro_aprobado_se_anota_en_finanzas_una_sola_vez(db, falso):
    fijo = _con_tarjeta(db, falso)
    r = plexo.cobrar_mes(db, fijo, HOY)
    assert r["resultado"] == "aprobado"
    pago = [c for c in falso.llamadas if c[1] == "/v1/payments"][0][2]
    assert pago["paymentMethod"] == {"source": "token", "paymentInstrument": {"token": "tok_4444"}}
    assert pago["amount"]["currency"] == "USD" and pago["amount"]["total"] == 146.4   # 120 + IVA

    movs = listar_movimientos(db)
    assert sorted(m["tipo"] for m in movs) == ["egreso", "egreso", "ingreso"]
    ingreso = next(m for m in movs if m["tipo"] == "ingreso")
    assert ingreso["recurrente_id"] == fijo["id"] and "Diego Hinze" in ingreso["concepto"]

    # Correrlo otra vez (el hilo corre cada hora) no cobra de nuevo.
    assert plexo.cobrar_mes(db, fijo, HOY)["resultado"] == "ya_cobrado"
    assert plexo.cobrar_mes(db, fijo, HOY, manual=True)["resultado"] == "ya_cobrado"
    assert sum(1 for c in falso.llamadas if c[1] == "/v1/payments") == 1
    assert len(listar_movimientos(db)) == 3


def test_un_mes_ya_anotado_no_se_le_cobra_a_la_tarjeta(db, falso):
    """El caso de Diego: septiembre ya lo cargó Juan a mano. Cobrarlo con la
    tarjeta sería cobrarle dos veces."""
    from services.finanzas import materializar_recurrentes
    fijo = _fijo(db)
    assert materializar_recurrentes(db) == 1      # el mes, supuesto pagado
    plexo.pedir_tarjeta(db, fijo, None, "https://crm.test")
    falso.tarjetas = [TARJETA_MASTER]
    plexo.verificar_tarjeta(db, fijo["id"])
    fijo = get_recurrente(db, fijo["id"])

    assert plexo.cobrar_mes(db, fijo, HOY)["resultado"] == "ya_anotado"
    assert plexo.cobrar_mes(db, fijo, HOY, manual=True)["resultado"] == "ya_anotado"
    assert not any(c[1] == "/v1/payments" for c in falso.llamadas)


def test_en_automatico_finanzas_no_supone_que_pago(db, falso):
    from services.finanzas import materializar_recurrentes
    fijo = _con_tarjeta(db, falso)
    assert materializar_recurrentes(db) == 0
    assert listar_movimientos(db) == []


def test_antes_del_dia_de_cobro_no_se_cobra(db, falso):
    fijo = _con_tarjeta(db, falso, dia_del_mes=20)
    assert plexo.cobrar_mes(db, fijo, HOY.replace(day=5))["resultado"] == "todavia_no"
    # El botón "Cobrar ahora" no espera.
    assert plexo.cobrar_mes(db, fijo, HOY.replace(day=5), manual=True)["resultado"] == "aprobado"


def test_rechazo_no_anota_nada_y_reintenta_a_los_3_dias(db, falso):
    fijo = _con_tarjeta(db, falso)
    falso.estado_pago = "rejected"
    dia1 = HOY.replace(day=1)
    r = plexo.cobrar_mes(db, fijo, dia1)
    assert r["resultado"] == "rechazado" and "Fondos" in r["detalle"]
    assert listar_movimientos(db) == []
    assert plexo.suscripcion(db, fijo["id"])["estado"] == "rechazada"

    # Al día siguiente todavía no. El intento cuenta la fecha real en que se
    # hizo, así que se simula moviéndola.
    c = sqlite3.connect(db)
    c.execute("UPDATE plexo_cobros SET creado_en = ?", (f"{dia1.isoformat()} 10:00:00",))
    c.commit()
    c.close()
    assert plexo.cobrar_mes(db, fijo, dia1.replace(day=2))["resultado"] == "esperando_reintento"
    falso.estado_pago = "approved"
    assert plexo.cobrar_mes(db, fijo, dia1.replace(day=4))["resultado"] == "aprobado"
    assert plexo.suscripcion(db, fijo["id"])["estado"] == "activa"
    assert len(listar_movimientos(db)) == 3


def test_sin_respuesta_no_se_reintenta_a_ciegas(db, falso):
    """Si se corta la conexión, Plexo puede haber cobrado igual: antes de
    volver a cobrar se le pregunta por la referencia."""
    fijo = _con_tarjeta(db, falso)
    falso.caida = True
    assert plexo.cobrar_mes(db, fijo, HOY)["resultado"] == "sin_respuesta"
    # Plexo sí lo había cobrado.
    ref = plexo._intentos(db, fijo["id"], PERIODO)[0]["referencia"]
    falso.pagos[ref] = {"id": "pay_x", "referenceId": ref, "status": "approved", "transactions": []}
    falso.caida = False
    assert plexo.cobrar_mes(db, fijo, HOY, manual=True)["resultado"] == "aprobado"
    assert sum(1 for c in falso.llamadas if c[1] == "/v1/payments") == 1, "no se volvió a cobrar"
    assert len(listar_movimientos(db)) == 3


def test_sin_respuesta_y_plexo_no_lo_tiene_se_puede_reintentar(db, falso):
    fijo = _con_tarjeta(db, falso)
    falso.caida = True
    plexo.cobrar_mes(db, fijo, HOY)
    falso.caida = False
    assert plexo.cobrar_mes(db, fijo, HOY, manual=True)["resultado"] == "reintentable"
    assert plexo.cobrar_mes(db, fijo, HOY, manual=True)["resultado"] == "aprobado"


def test_pausado_no_se_cobra(db, falso):
    fijo = _con_tarjeta(db, falso)
    plexo.pausar(db, fijo["id"], True)
    assert plexo.cobrar_mes(db, fijo, HOY)["resultado"] == "sin_tarjeta"
    assert not plexo.es_automatico(db, fijo["id"])
    plexo.pausar(db, fijo["id"], False)
    assert plexo.cobrar_mes(db, fijo, HOY)["resultado"] == "aprobado"


def test_una_tarjeta_de_testing_no_se_cobra_en_produccion(db, falso, monkeypatch):
    fijo = _con_tarjeta(db, falso)
    monkeypatch.setenv("PLEXO_ENV", "produccion")
    assert plexo.cobrar_mes(db, fijo, HOY)["resultado"] == "otro_entorno"


def test_cobrar_pendientes_sin_configurar_no_hace_nada(db, falso, monkeypatch):
    _con_tarjeta(db, falso)
    monkeypatch.delenv("PLEXO_MERCHANT_ID")
    assert plexo.cobrar_pendientes(db, HOY) == []


def test_cobrar_pendientes_cobra_los_que_tocan(db, falso):
    _con_tarjeta(db, falso)
    hechos = plexo.cobrar_pendientes(db, HOY)
    assert [h["resultado"] for h in hechos] == ["aprobado"]
    assert plexo.cobrar_pendientes(db, HOY) == []


# ── las rutas ────────────────────────────────────────────────────────────────

@pytest.fixture
def app(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _cliente_http(app, email, paneles):
    db = app.config["_DB"]
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    conn = sqlite3.connect(db)
    rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                       (email, json.dumps(paneles))).lastrowid
    conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
    conn.commit()
    conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = email.split("@")[0]
    return c


def test_la_pantalla_lista_los_fijos_con_tarjeta(app, falso):
    fijo = _fijo(app.config["_DB"])
    cli = _cliente_http(app, "socio@scalerics.com", ["finanzas"])
    d = cli.get("/api/plexo/estado").get_json()
    assert d["env"] == "testing" and d["listo"]
    assert [f["fijo_id"] for f in d["fijos"]] == [fijo["id"]]
    assert d["fijos"][0]["estado"] == "sin_tarjeta" and d["fijos"][0]["link"] is None

    r = cli.post(f"/api/plexo/fijos/{fijo['id']}/link", json={})
    assert r.status_code == 200 and "/plexo/tarjeta/" in r.get_json()["link"]


def test_sin_el_panel_de_finanzas_no_se_ve(app, falso):
    cli = _cliente_http(app, "otro@scalerics.com", ["leads"])
    assert cli.get("/api/plexo/estado").status_code in (302, 401, 403)


def test_cobrar_a_mano_es_solo_para_admin(app, falso):
    fijo = _con_tarjeta(app.config["_DB"], falso)
    cli = _cliente_http(app, "socio@scalerics.com", ["finanzas"])
    assert cli.post(f"/api/plexo/fijos/{fijo['id']}/cobrar").status_code == 403
    assert not any(c[1] == "/v1/payments" for c in falso.llamadas)


def test_el_link_publico_redirige_a_plexo_sin_sesion(app, falso):
    fijo = _fijo(app.config["_DB"])
    link = plexo.pedir_tarjeta(app.config["_DB"], fijo, None, "https://crm.test")["link"]
    anonimo = app.test_client()
    r = anonimo.get("/plexo/tarjeta/" + link.rsplit("/", 1)[1])
    assert r.status_code == 302 and r.headers["Location"].startswith("https://tokenizations.testing")
    assert anonimo.get("/plexo/tarjeta/" + "z" * 24).status_code == 404


def test_el_callback_no_le_cree_al_cuerpo(app, falso):
    """El aviso de Plexo no viene firmado: aunque diga que hay tarjeta, se le
    pregunta a Plexo, y si Plexo no la tiene no se activa nada."""
    db = app.config["_DB"]
    fijo = _fijo(db)
    plexo.pedir_tarjeta(db, fijo, None, "https://crm.test")
    anonimo = app.test_client()
    cuerpo = {"Data": {"Customer": {"Id": "cus_1"}, "PaymentInstrument": {"Token": "tok_trucho"}}}
    assert anonimo.post("/api/plexo/callback/tarjeta", json=cuerpo).status_code == 200
    assert plexo.suscripcion(db, fijo["id"])["token"] is None

    falso.tarjetas = [TARJETA_MASTER]
    anonimo.post("/api/plexo/callback/tarjeta", json=cuerpo)
    assert plexo.suscripcion(db, fijo["id"])["token"] == "tok_4444"


def test_la_pagina_de_listo_confirma_la_tarjeta(app, falso):
    db = app.config["_DB"]
    fijo = _fijo(db)
    link = plexo.pedir_tarjeta(db, fijo, None, "https://crm.test")["link"]
    falso.tarjetas = [TARJETA_MASTER]
    r = app.test_client().get("/plexo/listo/" + link.rsplit("/", 1)[1])
    assert r.status_code == 200 and "Listo" in r.get_data(as_text=True)
    assert plexo.suscripcion(db, fijo["id"])["estado"] == "activa"
