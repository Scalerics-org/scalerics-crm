# -*- coding: utf-8 -*-
"""Agregar a mano un lead a Meta Ads (pedido de Juan, 13/10).

"Que a los leads de Meta se les pueda agregar leads manual que nos llegan por
otro lado." Juan eligio que cuenten como leads de Meta en todo (numeros y
secuencia de mails); `origen_manual` solo dice por donde llego.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import ENVIOS_META_SQL, create_user, init_db

TEL = "+59899111222"


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def app(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    a = dashboard.create_app(db)
    a.config["TESTING"] = True
    return a


def _usuario(db, email, paneles=None):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if paneles is not None:
        conn = sqlite3.connect(db)
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                           (f"rol-{email}", json.dumps(paneles))).lastrowid
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
        conn.commit()
        conn.close()
    return uid


def _cliente(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Juan"
    return c


@pytest.fixture
def cli(app, db):
    return _cliente(app, _usuario(db, "jefe@scalerics.com"))


def _fila(db, bid):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM businesses WHERE id=?", (bid,)).fetchone())
    finally:
        conn.close()


def _alta(cli, **extra):
    cuerpo = {"name": "Ana Pérez", "phone": TEL, "origen": "whatsapp"}
    cuerpo.update(extra)
    return cli.post("/api/meta/leads", json=cuerpo)


def test_queda_como_lead_de_meta_con_su_origen(cli, db):
    r = _alta(cli, negocio="Óptica Luz", email="Ana@Optica.uy", city="Salto",
              busca="crear_mi_ecommerce", presupuesto="entre_usd_500_y_usd_1.000",
              nota="La pasó Pedro")
    assert r.status_code == 200, r.get_data(as_text=True)
    bid = r.get_json()["id"]
    f = _fila(db, bid)
    assert (f["source"], f["origen_manual"], f["crm_status"]) == ("meta", "whatsapp", "sin_contactar")
    assert (f["phone"], f["email"], f["city"]) == (TEL, "ana@optica.uy", "Salto")
    assert "WhatsApp" in f["notes"] and "La pasó Pedro" in f["notes"]
    # Las mismas claves del formulario de Meta: la lista y los mails leen de ahi.
    fd = json.loads(f["form_data"])
    assert fd["¿cómo_se_llama_tu_negocio?"] == "Óptica Luz"
    assert fd["¿que_es_lo_que_buscás_para_tu_negocio?"] == "crear_mi_ecommerce"
    assert fd["¿contás_con_un_presupuesto_para_este_proyecto?"] == "entre_usd_500_y_usd_1.000"

    lead = next(x for x in cli.get("/api/leads?crm_group=meta").get_json() if x["id"] == bid)
    assert lead["origen_manual"] == "whatsapp" and lead["envios"]


def test_cuenta_en_los_numeros_de_meta(cli, db):
    bid = _alta(cli, fecha="2026-08-20").get_json()["id"]
    conn = sqlite3.connect(db)
    filas = conn.execute(f"SELECT scraped_at, fecha_local FROM ({ENVIOS_META_SQL}) WHERE id=?",
                         (bid,)).fetchall()
    conn.close()
    # Al mediodia de Montevideo: no se corre de dia.
    assert filas == [("2026-08-20 15:00:00", "2026-08-20 12:00:00")]


def test_entra_en_la_secuencia_de_mails(cli, db):
    from services.meta_reminders import _FILTRO_LEAD_ELEGIBLE
    bid = _alta(cli, email="ana@optica.uy").get_json()["id"]
    conn = sqlite3.connect(db)
    ids = [r[0] for r in conn.execute(f"SELECT b.id FROM businesses b WHERE {_FILTRO_LEAD_ELEGIBLE}")]
    conn.close()
    assert bid in ids


def test_sin_fecha_es_ahora(cli, db):
    antes = datetime.now(timezone.utc).replace(microsecond=0)
    f = _fila(db, _alta(cli).get_json()["id"])
    hecho = datetime.strptime(f["scraped_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    assert antes - timedelta(seconds=1) <= hecho <= antes + timedelta(seconds=5)


@pytest.mark.parametrize("cuerpo,falta", [
    ({"name": ""}, "nombre"),
    ({"phone": "", "email": ""}, "teléfono"),
    ({"email": "no-es-un-mail", "phone": ""}, "mail"),
    ({"origen": ""}, "llegó"),
    ({"origen": "paloma"}, "llegó"),
    ({"busca": "cualquier_cosa"}, "busca"),
    ({"presupuesto": "un_millon"}, "presupuesto"),
    ({"fecha": "2099-01-01"}, "fecha"),
    ({"fecha": "ayer"}, "fecha"),
])
def test_datos_invalidos_dan_400_y_no_guardan(cli, db, cuerpo, falta):
    r = _alta(cli, **cuerpo)
    assert r.status_code == 400
    assert falta in r.get_json()["error"]
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0] == 0
    conn.close()


def test_con_mail_y_sin_telefono_alcanza(cli, db):
    r = _alta(cli, phone="", email="ana@optica.uy")
    assert r.status_code == 200
    assert _fila(db, r.get_json()["id"])["phone"] is None


def test_no_duplica_un_lead_de_meta(cli, db):
    primero = _alta(cli).get_json()["id"]
    r = _alta(cli, name="Otra Ana")
    assert r.status_code == 409 and r.get_json()["id"] == primero
    # Sin telefono, por el mail de una ficha de Meta.
    _alta(cli, phone="", email="b@b.uy", name="Bea")
    assert _alta(cli, phone="", email="B@b.uy").status_code == 409


@pytest.mark.parametrize("tipeado", ["099 111 222", "99111222", "+598 99 111 222",
                                     "59899111222", "(099) 111-222"])
def test_el_telefono_queda_con_la_grafia_de_meta(cli, db, tipeado):
    r = _alta(cli, phone=tipeado)
    assert _fila(db, r.get_json()["id"])["phone"] == TEL


def test_el_mismo_telefono_escrito_distinto_no_duplica(cli, db):
    _alta(cli, phone="099 111 222")
    assert _alta(cli, phone="099111222").status_code == 409
    # Ni un formulario de Meta que llegue despues con la grafia de Meta.
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0] == 1
    conn.close()
    from database import get_business_by_phone
    assert get_business_by_phone(db, TEL)["origen_manual"] == "whatsapp"


def test_un_telefono_de_afuera_queda_con_sus_digitos(cli, db):
    r = _alta(cli, phone="+54 9 11 5555-1234")
    assert _fila(db, r.get_json()["id"])["phone"] == "+5491155551234"


def test_una_ficha_de_otra_cohorte_pasa_a_meta(cli, db):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO businesses (id, name, phone, source, scraped_at) "
                 "VALUES (5, 'Taller Sur', ?, NULL, '2026-03-01 10:00:00')", (TEL,))
    conn.commit()
    conn.close()
    r = _alta(cli, fecha="2026-09-10")
    assert r.status_code == 200 and r.get_json() == {"ok": True, "id": 5, "pasada": True}
    f = _fila(db, 5)
    assert (f["source"], f["origen_manual"]) == ("meta", "whatsapp")
    # Aparece en el mes en que llego, no solo en el del scrapeo.
    lead = next(x for x in cli.get("/api/leads?crm_group=meta").get_json() if x["id"] == 5)
    assert "2026-09-10 15:00:00" in lead["envios"]


def test_pide_el_panel_meta(app, db):
    _usuario(db, "jefe@scalerics.com")   # el primero que se registra es admin
    sin = _cliente(app, _usuario(db, "finanzas@scalerics.com", ["finanzas"]))
    assert _alta(sin).status_code == 403
    assert app.test_client().post("/api/meta/leads", json={"name": "x"}).status_code == 401
    con = _cliente(app, _usuario(db, "ventas@scalerics.com", ["meta"]))
    assert _alta(con).status_code == 200


def test_el_panel_tiene_el_boton_y_el_formulario():
    html = dashboard.DASHBOARD_HTML
    assert 'onclick="mmAbrirAlta()"' in html and 'id="meta-alta-modal"' in html
    from routes.meta import BUSCA_MANUAL, ORIGENES_MANUALES, PRESUPUESTO_MANUAL
    for clave in (*ORIGENES_MANUALES, *BUSCA_MANUAL, *PRESUPUESTO_MANUAL):
        assert f'<option value="{clave}">' in html, clave
    for clave, etiqueta in ORIGENES_MANUALES.items():
        assert f"{clave}: '{etiqueta}'" in html, "MM_ORIGENES igual que en Python"
