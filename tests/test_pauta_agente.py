"""Agente de pauta: ensayo sin tocar Meta, deshacer, tope del mes, piezas y permisos."""

import json
import sqlite3
from datetime import datetime, timezone

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import pauta_agente as pa
from services import sombra_meta as sm

MIE_11 = datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)     # 11:00 en Montevideo
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def _f(gasto, leads, **extra):
    d = {"spend": str(gasto), "impressions": "4000", "inline_link_clicks": "40",
         "frequency": "1.5", "account_currency": "USD",
         "actions": [{"action_type": "lead", "value": str(leads)}]}
    d.update(extra)
    return d


def _datos(anuncios_7d=(), campanas_7d=(), presupuesto="2000"):
    return {
        "referencia": _f(612.0, 40),
        "anuncios_7d": list(anuncios_7d),
        "campanas_7d": list(campanas_7d),
        "anuncios": [{"id": f["ad_id"], "effective_status": "ACTIVE", "campaign_id": f["campaign_id"],
                      "adset_id": "S1", "created_time": "2026-08-01T00:00:00-0300"} for f in anuncios_7d],
        "campanas": [{"id": "C1", "name": "Leads - UY", "objective": "OUTCOME_LEADS",
                      "effective_status": "ACTIVE", "daily_budget": presupuesto}],
        "conjuntos": [],
    }


def _ad(ad_id, gasto, leads):
    return _f(gasto, leads, ad_id=ad_id, ad_name=f"Anuncio {ad_id}", campaign_id="C1",
              campaign_name="Leads - UY")


def _camp(gasto, leads):
    return _f(gasto, leads, campaign_id="C1", campaign_name="Leads - UY")


class Meta:
    """Graba lo que se le escribiria a Meta."""

    def __init__(self):
        self.escrito = []

    def post(self, ruta, archivos=None, **params):
        self.escrito.append((ruta, params))
        if ruta.endswith("/adimages"):
            return {"images": {"x": {"hash": "HASH1"}}}
        if ruta.endswith("/adcreatives"):
            return {"id": "CR1"}
        if ruta.endswith("/ads"):
            return {"id": "AD_NUEVO"}
        return {"success": True}

    def get(self, ruta, **params):
        return {"adset_id": "S1", "creative": {"object_story_spec": {
            "page_id": "P1", "link_data": {"message": "viejo", "picture": "http://x",
                                           "call_to_action": {"type": "SIGN_UP",
                                                              "value": {"lead_gen_form_id": "F1"}}}}}}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.delenv("PAUTA_ESCRITURA", raising=False)
    monkeypatch.setattr(pa, "_avisar", lambda *a: None)
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    sm.fijar_tope_cpl(ruta, 25.0, "test")
    return ruta


def _escribe(monkeypatch):
    monkeypatch.setenv("PAUTA_ESCRITURA", "on")


# ── llaves ───────────────────────────────────────────────────────────────────

def test_arranca_apagado_y_en_ensayo(monkeypatch):
    monkeypatch.delenv("PAUTA_AGENTE", raising=False)
    monkeypatch.delenv("PAUTA_ESCRITURA", raising=False)
    assert not pa.activo() and not pa.escritura()


def test_sin_la_llave_no_hay_escritura_posible(db):
    with pytest.raises(RuntimeError, match="PAUTA_ESCRITURA"):
        pa._post("123", status="PAUSED")


def test_en_ensayo_registra_pero_no_toca_meta(db):
    meta = Meta()
    r = pa.operador(db, MIE_11, traer=lambda hoy: _datos([_ad("A1", 60, 0)]), post=meta.post)
    assert r["estado"] == "ok" and r["ensayo"]
    [a] = pa.listar(db)
    assert a["tipo"] == "pausar" and a["estado"] == "ensayo"
    assert meta.escrito == []


# ── operador ─────────────────────────────────────────────────────────────────

def test_pausa_el_anuncio_que_gasta_sin_leads(db, monkeypatch):
    _escribe(monkeypatch)
    meta = Meta()
    pa.operador(db, MIE_11, traer=lambda hoy: _datos([_ad("A1", 60, 0)]), post=meta.post)
    [a] = pa.listar(db)
    assert a["estado"] == "aplicada"
    assert meta.escrito == [("A1", {"status": "PAUSED"})]


def test_bajar_presupuesto_es_automatico_y_subir_espera_ok(db, monkeypatch):
    _escribe(monkeypatch)
    meta = Meta()
    pa.operador(db, MIE_11, traer=lambda hoy: _datos(campanas_7d=[_camp(200, 2)]), post=meta.post)
    [a] = pa.listar(db)
    assert a["tipo"] == "bajar" and a["estado"] == "aplicada"
    assert meta.escrito == [("C1", {"daily_budget": "1600"})]


def test_subir_presupuesto_queda_pendiente_de_juan(db, monkeypatch):
    _escribe(monkeypatch)
    meta = Meta()
    pa.operador(db, MIE_11, traer=lambda hoy: _datos(campanas_7d=[_camp(40, 5)]), post=meta.post)
    [a] = pa.listar(db)
    assert a["tipo"] == "escalar" and a["estado"] == "pendiente_ok"
    assert meta.escrito == []
    pa.aprobar(db, a["id"], "Juan", post=meta.post)
    assert meta.escrito == [("C1", {"daily_budget": "2400"})]
    assert pa.obtener(db, a["id"])["estado"] == "aplicada"


def test_no_propone_subir_si_pasaria_el_tope(db):
    pa.fijar_tope_mes(db, 300, "Juan")
    planes = pa.planificar(_datos(campanas_7d=[_camp(40, 5)]), 25.0,
                           MIE_11.date(), tope=300, gasto_mes=250)
    assert planes == []


def test_frenado_no_hace_nada(db, monkeypatch):
    _escribe(monkeypatch)
    pa.frenar(db, "Andrés")
    meta = Meta()
    r = pa.operador(db, MIE_11, traer=lambda hoy: _datos([_ad("A1", 60, 0)]), post=meta.post)
    assert r["estado"] == "frenado" and pa.listar(db) == [] and meta.escrito == []
    pa.reactivar(db, "Andrés")
    assert pa.estado(db)["estado"] == "activo"


def test_no_repite_ni_pisa_lo_que_se_deshizo(db, monkeypatch):
    _escribe(monkeypatch)
    meta = Meta()
    pa.operador(db, MIE_11, traer=lambda hoy: _datos([_ad("A1", 60, 0)]), post=meta.post)
    [a] = pa.listar(db)
    pa.deshacer(db, a["id"], "Andrés", es_admin=False, post=meta.post)
    assert meta.escrito[-1] == ("A1", {"status": "ACTIVE"})
    assert pa.obtener(db, a["id"])["estado"] == "deshecha"
    assert pa.obtener(db, a["id"])["resuelta_por"] == "Andrés"
    otro_dia = datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)
    pa.operador(db, otro_dia, traer=lambda hoy: _datos([_ad("A1", 60, 0)]), post=meta.post)
    assert len(pa.listar(db)) == 1


def test_anuncio_gastado_pide_pieza_sin_tocar_nada(db):
    datos = _datos([_f(30, 2, ad_id="A1", ad_name="Viejo", campaign_id="C1",
                       campaign_name="Leads - UY", frequency="4.2")])
    [p] = pa.planificar(datos, 25.0, MIE_11.date())
    assert p["tipo"] == "pedir_pieza" and p["cambios"] == []


def test_si_meta_rechaza_queda_el_error(db, monkeypatch):
    _escribe(monkeypatch)

    def falla(ruta, **p):
        raise RuntimeError("code=100 sin permiso")
    pa.operador(db, MIE_11, traer=lambda hoy: _datos([_ad("A1", 60, 0)]), post=falla)
    [a] = pa.listar(db)
    assert a["estado"] == "error" and "sin permiso" in a["error"]


# ── supervisor ───────────────────────────────────────────────────────────────

def _resumen(gasto):
    return lambda hoy: {"mes": _f(gasto, 20), "campanas": [
        {"id": "C1", "name": "Leads - UY", "objective": "OUTCOME_LEADS", "effective_status": "ACTIVE"}]}


def test_guarda_el_gasto_del_mes_para_el_panel(db):
    pa.supervisor(db, MIE_11, traer=_resumen(312), forzar=True)
    r = pa.resumen(db)
    assert r["gasto_mes"] == 312 and r["leads_mes"] == 20 and r["cpl_mes"] == 15.6
    assert r["campanas"] == [{"id": "C1", "nombre": "Leads - UY"}]


def test_al_llegar_al_tope_pausa_todo_una_vez(db, monkeypatch):
    _escribe(monkeypatch)
    pa.fijar_tope_mes(db, 500, "Juan")
    meta = Meta()
    pa.supervisor(db, MIE_11, traer=_resumen(505), forzar=True, post=meta.post)
    pa.supervisor(db, MIE_11, traer=_resumen(510), forzar=True, post=meta.post)
    [a] = pa.listar(db)
    assert a["tipo"] == "tope" and a["estado"] == "aplicada"
    assert meta.escrito == [("C1", {"status": "PAUSED"})]
    with pytest.raises(pa.NoSePuede, match="administrador"):
        pa.deshacer(db, a["id"], "Andrés", es_admin=False, post=meta.post)
    pa.deshacer(db, a["id"], "Juan", es_admin=True, post=meta.post)
    assert meta.escrito[-1] == ("C1", {"status": "ACTIVE"})


# ── piezas ───────────────────────────────────────────────────────────────────

def test_valida_la_pieza(db):
    with pytest.raises(pa.NoSePuede, match="imagen"):
        pa.guardar_pieza(db, "x.gif", PNG, "hola", "", "Andrés")
    with pytest.raises(pa.NoSePuede, match="texto"):
        pa.guardar_pieza(db, "x.png", PNG, "  ", "", "Andrés")


def test_en_ensayo_la_pieza_no_se_sube(db):
    pa.guardar_pieza(db, "caso.png", PNG, "Texto nuevo", "", "Andrés")
    meta = Meta()
    pa.operador(db, MIE_11, traer=lambda hoy: _datos([_ad("A1", 30, 3)]), post=meta.post, get=meta.get)
    [p] = pa.listar_piezas(db)
    assert p["estado"] == "ensayo" and p["campana_id"] == "C1" and meta.escrito == []


def test_sube_la_pieza_copiando_el_formato_del_mejor_anuncio(db, monkeypatch):
    _escribe(monkeypatch)
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "act_1")
    pa.guardar_pieza(db, "caso.png", PNG, "Texto nuevo", "", "Andrés")
    meta = Meta()
    pa.operador(db, MIE_11, traer=lambda hoy: _datos([_ad("A1", 30, 3)]), post=meta.post, get=meta.get)
    [p] = pa.listar_piezas(db)
    assert p["estado"] == "en_prueba" and p["ad_id"] == "AD_NUEVO"
    rutas = [r for r, _ in meta.escrito]
    assert rutas == ["act_1/adimages", "act_1/adcreatives", "act_1/ads"]
    spec = json.loads(meta.escrito[1][1]["object_story_spec"])
    assert spec["link_data"]["image_hash"] == "HASH1" and spec["link_data"]["message"] == "Texto nuevo"
    assert "picture" not in spec["link_data"]
    assert spec["link_data"]["call_to_action"]["value"]["lead_gen_form_id"] == "F1"
    assert meta.escrito[2][1]["adset_id"] == "S1"
    subida = [a for a in pa.listar(db) if a["tipo"] == "subir_pieza"]
    assert subida and subida[0]["objeto_id"] == "AD_NUEVO"


def test_el_video_queda_para_subir_a_mano(db, monkeypatch):
    _escribe(monkeypatch)
    pa.guardar_pieza(db, "caso.mp4", b"video", "Texto", "", "Andrés")
    meta = Meta()
    pa.operador(db, MIE_11, traer=lambda hoy: _datos([_ad("A1", 30, 3)]), post=meta.post, get=meta.get)
    [p] = pa.listar_piezas(db)
    assert p["estado"] == "manual" and meta.escrito == []


# ── panel ────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.delenv("PAUTA_ESCRITURA", raising=False)
    monkeypatch.setattr(pa, "_avisar", lambda *a: None)
    ruta = str(tmp_path / "app.db")
    init_db(ruta)
    a = dashboard.create_app(ruta)
    a.config["TESTING"] = True
    return a


def _cli(app, email, paneles=None):
    db = app.config["DB_PATH"]
    uid = create_user(db, name="Andrés", email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if paneles is not None:
        c = sqlite3.connect(db)
        rol = c.execute("INSERT INTO roles (name, panel_access) VALUES (?, ?)",
                        (f"rol-{email}", json.dumps(paneles))).lastrowid
        c.execute("UPDATE users SET role_id = ? WHERE id = ?", (rol, uid))
        c.commit()
        c.close()
    cli = app.test_client()
    with cli.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Andrés"
    return cli


def test_permisos_del_panel(app):
    jefe = _cli(app, "jefe@scalerics.com")
    mkt = _cli(app, "mkt@scalerics.com", ["pauta"])
    otro = _cli(app, "otro@scalerics.com", ["sombra"])
    assert otro.get("/api/pauta/estado").status_code == 403
    d = mkt.get("/api/pauta/estado").get_json()
    assert d["ok"] and not d["es_admin"] and not d["escritura"] and d["agente"]["estado"] == "activo"
    assert mkt.post("/api/pauta/tope", json={"valor": 900}).status_code == 403
    assert mkt.post("/api/pauta/correr").status_code == 403
    assert mkt.post("/api/pauta/frenar").get_json()["agente"]["estado"] == "frenado"
    assert jefe.post("/api/pauta/tope", json={"valor": 500}).get_json()["tope_mes"] == 500


def test_andres_sube_una_pieza_desde_el_panel(app):
    import io
    mkt = _cli(app, "mkt@scalerics.com", ["pauta"])
    r = mkt.post("/api/pauta/piezas", data={"archivo": (io.BytesIO(PNG), "caso.png"),
                                           "texto": "Hola", "campana_id": ""},
                 content_type="multipart/form-data")
    assert r.get_json()["ok"]
    [p] = mkt.get("/api/pauta/estado").get_json()["piezas"]
    assert p["subida_por"] == "Andrés" and p["estado"] == "recibida"
    assert mkt.get(f"/api/pauta/piezas/{p['id']}/archivo").data == PNG


def test_no_se_reparte_a_ningun_rol_todavia():
    """Juan (30/9): no se lanza hasta hablarlo con el de marketing."""
    fuente = open("database.py", encoding="utf-8").read()
    assert '_grant_panel_to_existing_roles(conn, "pauta"' not in fuente.replace("# ", "")
