"""Outbound e Inteligencia comercial de Scalerics Fidelidad (pedido de Juan, 23/9).

Lo que se pidió, cada cosa con su test:
- cada llamada deja agendada la siguiente (`proxima_llamada`, `registrar_llamada`)
- la cola del día se ordena sola (`armar_hoy`)
- los prospectos del Excel entran, sin duplicarse, y los de fuera de zona quedan
  ocultos (`importar`)
- los comercios que mostraba la cola vieja se ocultan sin borrarse
- el vendedor entra con su rol al registrarse y no ve nada de la agencia
- SDR sale del menú

Hoy está fijo en el martes 23/9/2026, 10:30 de Montevideo.
"""

import io
import json
import sqlite3
import zipfile
from datetime import datetime

import pytest
from werkzeug.security import generate_password_hash

import dashboard
import routes.fidelidad as rutas
from database import create_user, init_db, listar_leads
from services import fidelidad as fid

AHORA = datetime(2026, 9, 23, 10, 30)          # martes
VIERNES = datetime(2026, 9, 25, 17, 0)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "fid.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def app(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(rutas, "_ahora", lambda: AHORA)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _exec(db, sql, params=()):
    c = sqlite3.connect(db)
    try:
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def _uno(db, sql, params=()):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql, params).fetchone()
    finally:
        c.close()


def _usuario(db, email, rol=None):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if rol:
        rid = _uno(db, "SELECT id FROM roles WHERE name = ?", (rol,))[0]
        _exec(db, "UPDATE users SET role_id = ? WHERE id = ?", (rid, uid))
    return uid


def _cli(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Lucas"
    return c


def _p(db, nombre="La Perdiz", **extra):
    datos = {"nombre": nombre, "zona": "Municipio CH", "barrio": "Punta Carretas",
             "tipo": "Parrilla", "telefono": "+598 2711 8963", "rating": 4.5, "resenas": 5489}
    datos.update(extra)
    pid, que = fid.crear_prospecto(db, datos)
    assert que == "creado", que
    return pid


# ── la próxima llamada ───────────────────────────────────────────────────────

def test_no_atendio_a_la_manana_vuelve_manana_de_tarde():
    assert fid.proxima_llamada("no_atendio", AHORA) == datetime(2026, 9, 24, 16, 0)


def test_no_atendio_de_tarde_vuelve_manana_a_la_manana_y_saltea_el_finde():
    assert fid.proxima_llamada("no_atendio", VIERNES) == datetime(2026, 9, 28, 11, 0)


def test_info_por_whatsapp_da_dos_dias_habiles():
    assert fid.proxima_llamada("info_whatsapp", VIERNES) == datetime(2026, 9, 29, 17, 0)


def test_no_le_interesa_vuelve_a_los_90_dias():
    assert fid.proxima_llamada("no_interesa", AHORA).date().isoformat() == "2026-12-22"


def test_primera_llamada_pasa_a_contactado_y_agenda(db):
    pid = _p(db)
    p, err = fid.registrar_llamada(db, pid, "no_atendio", "Lucas", cuando=AHORA)
    assert err is None
    assert p["estado"] == "contactado"
    assert p["proxima_llamada"] == "2026-09-24 16:00"
    assert p["primera_llamada"] == "2026-09-23 10:30"
    assert p["llamadas"][0]["efectivo"] == 0


def test_pidio_que_llame_exige_fecha_futura(db):
    pid = _p(db)
    _, err = fid.registrar_llamada(db, pid, "llamar_despues", "Lucas", cuando=AHORA)
    assert err == "elegí cuándo volver a llamar"
    _, err = fid.registrar_llamada(db, pid, "llamar_despues", "Lucas", fecha="2026-09-01T10:00", cuando=AHORA)
    assert "pasado" in err
    p, err = fid.registrar_llamada(db, pid, "llamar_despues", "Lucas", fecha="2026-09-25T16:00", cuando=AHORA)
    assert err is None and p["proxima_llamada"] == "2026-09-25 16:00"
    assert p["llamadas"][0]["efectivo"] == 1


def test_el_camino_completo_hasta_el_cierre(db):
    pid = _p(db)
    p, _ = fid.registrar_llamada(db, pid, "reunion", "Lucas", fecha_reunion="2026-09-24T10:00", cuando=AHORA)
    assert p["estado"] == "reunion_agendada" and p["fecha_reunion"] == "2026-09-24 10:00"
    assert p["proxima_llamada"] is None
    # Una vez agendada, los botones son los de la reunión, no los de la llamada.
    _, err = fid.registrar_llamada(db, pid, "no_atendio", "Lucas", cuando=AHORA)
    assert err == "ese resultado no corresponde a esta etapa"
    assert fid.get_prospecto(db, pid)["estado"] == "reunion_agendada"


def test_resultados_de_otra_etapa_se_rechazan(db):
    pid = _p(db)
    _, err = fid.registrar_llamada(db, pid, "cerro", "Lucas", cuando=AHORA)
    assert err == "ese resultado no corresponde a esta etapa"


def test_reunion_piloto_y_cierre(db):
    pid = _p(db)
    fid.registrar_llamada(db, pid, "reunion", "Lucas", fecha_reunion="2026-09-23T09:00", cuando=AHORA)
    p, _ = fid.registrar_llamada(db, pid, "piloto", "Lucas", cuando=AHORA)
    assert p["estado"] == "piloto" and p["piloto_inicio"] == "2026-09-23"
    assert p["proxima_llamada"] == "2026-10-07 15:00"
    p, _ = fid.registrar_llamada(db, pid, "cerro", "Lucas", cuando=AHORA)
    assert p["estado"] == "cerrado" and p["cerrado_en"] == "2026-09-23"
    assert p["mensual_usd"] == 150
    assert p["proxima_llamada"] is None
    assert [c["a"] for c in reversed(p["cambios"])] == ["reunion_agendada", "piloto", "cerrado"]


def test_no_le_interesa_pide_motivo_y_descarta(db):
    pid = _p(db)
    _, err = fid.registrar_llamada(db, pid, "no_interesa", "Lucas", cuando=AHORA)
    assert err == "elegí el motivo"
    p, _ = fid.registrar_llamada(db, pid, "no_interesa", "Lucas", motivo="Precio", cuando=AHORA)
    assert p["estado"] == "descartado" and p["motivo_descarte"] == "Precio"
    assert p["proxima_llamada"].startswith("2026-12-22")


# ── la cola del día ──────────────────────────────────────────────────────────

def test_la_cola_ordena_vencidas_hoy_y_sugeridos(db):
    venc = _p(db, "Bruta", telefono="091251252")
    hoy = _p(db, "Garcia", telefono="27120080")
    chico = _p(db, "Chiquito", telefono="099111222", rating=3.9, resenas=12)
    grande = _p(db, "Grandote", telefono="099333444", rating=4.8, resenas=3000)
    _exec(db, "UPDATE fid_prospectos SET estado='contactado', proxima_llamada='2026-09-22 11:00' WHERE id=?", (venc,))
    _exec(db, "UPDATE fid_prospectos SET estado='contactado', proxima_llamada='2026-09-23 15:00' WHERE id=?", (hoy,))
    _exec(db, "UPDATE fid_prospectos SET estado='contactado', proxima_llamada='2026-09-24 15:00' WHERE nombre='La Perdiz'")
    d = fid.armar_hoy(db, AHORA)
    g = d["grupos"]
    assert [p["id"] for p in g["vencidas"]] == [venc]
    assert [p["id"] for p in g["hoy"]] == [hoy]
    assert [p["id"] for p in g["sugeridos"]] == [grande, chico]
    assert d["kpis"]["vencidas"] == 1 and d["kpis"]["para_hoy"] == 1


def test_reunion_que_ya_paso_vuelve_a_la_cola(db):
    pid = _p(db)
    fid.registrar_llamada(db, pid, "reunion", "Lucas", fecha_reunion="2026-09-23T09:00", cuando=AHORA)
    d = fid.armar_hoy(db, AHORA)
    assert [p["id"] for p in d["grupos"]["post_reunion"]] == [pid]


def test_sin_telefono_queda_ultimo_en_sugeridos(db):
    sin = _p(db, "Sin Tel", telefono="—", rating=4.9, resenas=9000)
    con = _p(db, "Con Tel", telefono="099000111", rating=4.0, resenas=50)
    ids = [p["id"] for p in fid.armar_hoy(db, AHORA)["grupos"]["sugeridos"]]
    assert ids.index(con) < ids.index(sin)


# ── importar el Excel ────────────────────────────────────────────────────────

def _xlsx(filas, links=None):
    """Un .xlsx mínimo con strings inline y, opcional, hipervínculos."""
    def celda(ref, v):
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"><v>{v}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>'
    letras = "ABCDEFGHIJKLMNOP"
    rows = "".join(f'<row r="{i+1}">' + "".join(celda(f"{letras[j]}{i+1}", v) for j, v in enumerate(f) if v is not None) + "</row>"
                   for i, f in enumerate(filas))
    hl = ""
    rels = ""
    if links:
        hl = "<hyperlinks>" + "".join(f'<hyperlink ref="{ref}" r:id="rId{k}"/>' for k, ref in enumerate(links, 1)) + "</hyperlinks>"
        rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + "".join(f'<Relationship Id="rId{k}" Type="hyperlink" Target="{url}" TargetMode="External"/>'
                          for k, url in enumerate(links.values(), 1)) + "</Relationships>")
    hoja = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheetData>{rows}</sheetData>{hl}</worksheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", hoja)
        if rels:
            z.writestr("xl/worksheets/_rels/sheet1.xml.rels", rels)
    return buf.getvalue()


ENCABEZADO = ["#", "Restaurante", "Zona", "Barrio", "Tipo", "Dirección", "Teléfono", "Rating Google",
              "Reseñas", "Google Maps", "Notas", "Facilidad contacto", "Contacto dueño (nombre / IG / tel)", "Estado"]


def test_importa_el_excel_de_prospectos(db):
    contenido = _xlsx([
        ["Scalerics SAS — Prospectos"], ["Columnas amarillas = para completar vos"], ENCABEZADO,
        [1, "La Perdiz", "Municipio CH", "Punta Carretas", "Parrilla", "Guipúzcoa 350", "+598 2711 8963", 4.5, 5489, "Ver en Maps", "Muy concurrido", None, None, "Sin contactar"],
        [2, "Massey Familia", "Carrasco", "Carrasco", "Pastas", "Couture 6465", "+598 96 095 331", 4.7, 803, "Ver en Maps", None, "Alta", "Martín", "Contactado"],
        [3, "La Parrillita", "Barra de Carrasco (Canelones)", "Barra de Carrasco", "Parrilla", "", "—", 4.2, 391],
        [4, "El Otro Es Mercat", "Ciudad Vieja / Centro (Municipio B)", "Ciudad Vieja", "Tapas", "", "+598 2914 7078", 4.6, 285],
    ], links={"J4": "https://www.google.com/maps/place/?q=place_id:AAA"})
    r = fid.importar(db, contenido)
    assert r == {"ok": True, "leidos": 4, "creados": 3, "duplicados": 0, "fuera_de_zona": 1}
    perdiz = _uno(db, "SELECT maps_url, resenas, rating, zona, notas FROM fid_prospectos WHERE nombre='La Perdiz'")
    assert tuple(perdiz) == ("https://www.google.com/maps/place/?q=place_id:AAA", 5489, 4.5, "Municipio CH", "Muy concurrido")
    massey = _uno(db, "SELECT estado, facilidad, contacto, zona FROM fid_prospectos WHERE nombre='Massey Familia'")
    assert tuple(massey) == ("contactado", "Alta", "Martín", "Carrasco")
    assert _uno(db, "SELECT zona, telefono FROM fid_prospectos WHERE nombre='La Parrillita'")[0] == "Carrasco"
    # Ciudad Vieja no es territorio: queda guardado, oculto.
    assert _uno(db, "SELECT archivado FROM fid_prospectos WHERE nombre='El Otro Es Mercat'")[0] == 1
    assert "El Otro Es Mercat" not in [p["nombre"] for p in fid.listar(db)["items"]]
    # Volver a importar no duplica.
    assert fid.importar(db, contenido)["duplicados"] == 4


def test_duplicado_por_telefono_aunque_cambie_el_nombre(db):
    _p(db, "Garcia (Punta Carretas)", telefono="+598 2712 0080")
    pid, que = fid.crear_prospecto(db, {"nombre": "Garcia Parrilla", "zona": "Municipio CH", "telefono": "27120080"})
    assert que == "duplicado"


def test_importar_rechaza_un_archivo_que_no_es_xlsx(db):
    assert fid.importar(db, b"hola")["ok"] is False


def test_leer_el_excel_real_si_esta(db):
    """El archivo que mandó Juan, si está en la máquina. No viaja en el repo:
    el repo es público y la planilla es inteligencia comercial."""
    from pathlib import Path
    ruta = Path.home() / "Desktop" / "Scalerics" / "Scalerics_prospectos_180.xlsx"
    if not ruta.exists():
        pytest.skip("el Excel de prospectos no está en esta máquina")
    r = fid.importar(db, ruta.read_bytes())
    assert r["ok"] and r["leidos"] == 180
    assert r["creados"] + r["fuera_de_zona"] + r["duplicados"] == 180
    visibles = fid.listar(db, por_pagina=1000)["total"]
    assert visibles == 150  # 89 CH + 50 Carrasco + 5 Buceo + 6 Barra de Carrasco
    assert _uno(db, "SELECT COUNT(*) FROM fid_prospectos WHERE maps_url LIKE 'https://%'")[0] >= 150


# ── la cola vieja se oculta, no se borra ─────────────────────────────────────

def test_la_cola_vieja_queda_archivada_una_sola_vez(tmp_path):
    ruta = str(tmp_path / "vieja.db")
    init_db(ruta)
    # init_db ya corrió la marca sobre una base vacía: se simula una base de
    # antes del cambio sacando la marca.
    _exec(ruta, "DELETE FROM fid_marcas")
    ids = {}
    for nombre, source, estado in (("Padron", None, "sin_contactar"), ("Discovery", "discovery", None),
                                   ("NoInteresa", None, "no_interesa"), ("Meta", "meta", "sin_contactar"),
                                   ("Interesado", None, "interesado")):
        ids[nombre] = _exec(ruta, "INSERT INTO businesses (name, source, crm_status) VALUES (?,?,?)",
                            (nombre, source, estado))
    init_db(ruta)
    archivados = {f[0] for f in sqlite3.connect(ruta).execute(
        "SELECT name FROM businesses WHERE archivado_en IS NOT NULL")}
    assert archivados == {"Padron", "Discovery", "NoInteresa"}
    assert _uno(ruta, "SELECT COUNT(*) FROM businesses")[0] == 5   # nada borrado
    assert listar_leads(ruta, crm_status="sin_contactar")["total"] == 0
    # Un comercio nuevo después de la marca no se archiva en el próximo arranque.
    _exec(ruta, "INSERT INTO businesses (name, crm_status) VALUES ('Nuevo', 'sin_contactar')")
    init_db(ruta)
    assert _uno(ruta, "SELECT archivado_en FROM businesses WHERE name='Nuevo'")[0] is None


# ── el vendedor ──────────────────────────────────────────────────────────────

def test_el_rol_de_vendedor_existe_con_sus_dos_pantallas(db):
    fila = _uno(db, "SELECT panel_access FROM roles WHERE name = ?", (fid.ROL_VENDEDOR,))
    assert json.loads(fila[0]) == ["cola", "metrics"]


def test_al_registrarse_entra_como_vendedor(app, monkeypatch):
    monkeypatch.setenv("REGISTER_CODE", "clave")
    monkeypatch.setenv("VENDEDORES_FIDELIDAD", "Lucas@Ejemplo.com, otro@x.com")
    c = app.test_client()
    r = c.post("/register", data={"name": "Lucas", "email": "lucas@ejemplo.com", "phone": "099",
                                  "password": "12345678", "code": "clave"})
    assert r.status_code == 302
    fila = _uno(app.config["_DB"], "SELECT r.name FROM users u JOIN roles r ON u.role_id = r.id "
                                   "WHERE u.email = 'lucas@ejemplo.com'")
    assert fila[0] == fid.ROL_VENDEDOR


def test_otro_mail_se_registra_sin_rol(app, monkeypatch):
    monkeypatch.setenv("REGISTER_CODE", "clave")
    monkeypatch.setenv("VENDEDORES_FIDELIDAD", "lucas@ejemplo.com")
    app.test_client().post("/register", data={"name": "X", "email": "x@x.com", "phone": "099",
                                              "password": "12345678", "code": "clave"})
    assert _uno(app.config["_DB"], "SELECT role_id FROM users WHERE email='x@x.com'")[0] is None


def test_un_rol_que_juan_cambio_no_se_pisa(db):
    uid = _usuario(db, "lucas@ejemplo.com", rol="Marketing")
    c = sqlite3.connect(db)
    assert fid.asignar_vendedores(c, ["lucas@ejemplo.com"]) == 0
    c.close()
    assert _uno(db, "SELECT r.name FROM users u JOIN roles r ON u.role_id=r.id WHERE u.id=?", (uid,))[0] == "Marketing"


@pytest.mark.parametrize("ruta", ["/api/calendar/events", "/api/leads?page=1", "/api/finanzas/movimientos",
                                  "/api/metrics", "/api/sdr-stats", "/api/meta/leads", "/api/seg-leads"])
def test_el_vendedor_no_ve_nada_de_la_agencia(app, ruta):
    cli = _cli(app, _usuario(app.config["_DB"], "lucas@ejemplo.com", rol=fid.ROL_VENDEDOR))
    assert cli.get(ruta).status_code == 403


def test_el_vendedor_si_ve_lo_suyo(app):
    db = app.config["_DB"]
    cli = _cli(app, _usuario(db, "lucas@ejemplo.com", rol=fid.ROL_VENDEDOR))
    pid = _p(db)
    assert cli.get("/api/me").status_code == 200
    assert cli.get("/api/fidelidad/hoy").status_code == 200
    assert cli.get("/api/fidelidad/intel").status_code == 200
    r = cli.post(f"/api/fidelidad/prospectos/{pid}/llamadas",
                 json={"resultado": "reunion", "fecha_reunion": "2026-09-24T10:00"})
    assert r.status_code == 201
    assert r.get_json()["prospecto"]["llamadas"][0]["usuario"] == "Lucas"
    assert cli.get("/api/fidelidad/reuniones").get_json()["proximas"][0]["id"] == pid
    # Las metas las pone Scalerics.
    assert cli.put("/api/fidelidad/config", json={"comision_pct": 50}).status_code == 403


def test_sin_el_panel_no_entra(app):
    db = app.config["_DB"]
    _exec(db, "INSERT INTO roles (name, panel_access) VALUES ('Otro', '[\"cal\"]')")
    cli = _cli(app, _usuario(db, "otro@x.com", rol="Otro"))
    assert cli.get("/api/fidelidad/hoy").status_code == 403
    assert cli.get("/api/fidelidad/intel").status_code == 403


# ── endpoints y métricas ─────────────────────────────────────────────────────

@pytest.fixture
def admin(app):
    return _cli(app, _usuario(app.config["_DB"], "jefe@scalerics.com"))


def test_alta_manual_fuera_de_zona_se_rechaza(admin):
    r = admin.post("/api/fidelidad/prospectos", json={"nombre": "X", "zona": "Ciudad Vieja"})
    assert r.status_code == 400
    r = admin.post("/api/fidelidad/prospectos", json={"nombre": "Y", "zona": "Carrasco"})
    assert r.status_code == 201


def test_importar_por_la_pantalla(admin):
    contenido = _xlsx([ENCABEZADO, [1, "Bruta", "Municipio CH", "Pocitos", "Tapas", "", "091251252", 4.6, 1283]])
    r = admin.post("/api/fidelidad/importar", data={"archivo": (io.BytesIO(contenido), "p.xlsx")},
                   content_type="multipart/form-data")
    assert r.get_json()["creados"] == 1


def test_mover_en_el_tablero(admin, app):
    pid = _p(app.config["_DB"])
    r = admin.post(f"/api/fidelidad/prospectos/{pid}/estado", json={"estado": "cerrado"})
    assert r.get_json()["prospecto"]["estado"] == "cerrado"
    col = {c["estado"]: c for c in admin.get("/api/fidelidad/pipeline").get_json()["columnas"]}
    assert col["cerrado"]["total"] == 1 and col["cerrado"]["potencial_usd"] == 150


def test_intel_arma_embudo_y_mrr(db):
    a, b, c = _p(db, "A", telefono="091000001"), _p(db, "B", telefono="091000002"), _p(db, "C", telefono="091000003")
    fid.registrar_llamada(db, a, "no_atendio", "L", cuando=datetime(2026, 9, 22, 11, 0))
    fid.registrar_llamada(db, b, "reunion", "L", fecha_reunion="2026-09-22T15:00", cuando=datetime(2026, 9, 22, 11, 5))
    fid.registrar_llamada(db, b, "piloto", "L", cuando=datetime(2026, 9, 22, 16, 0))
    fid.registrar_llamada(db, b, "cerro", "L", cuando=AHORA)
    fid.registrar_llamada(db, c, "no_interesa", "L", motivo="Precio", cuando=datetime(2026, 9, 22, 12, 0))
    d = fid.armar_intel(db, "mes", cuando=AHORA)
    emb = {e["etapa"]: e["n"] for e in d["embudo"]}
    assert emb == {"Contactados": 3, "Habló con el dueño": 2, "Reunión agendada": 1,
                   "Reunión hecha": 1, "Piloto": 1, "Cerrado": 1}
    k = d["kpis"]
    assert k["mrr"] == 150 and k["cerrados"] == 1 and k["llamadas"] == 5 and k["efectivos"] == 4
    assert k["reuniones"] == 1 and k["ciclo_dias"] == 1
    assert d["serie_mrr"][-1] == {"mes": "2026-09", "mrr": 150}
    assert d["motivos"] == [{"motivo": "Precio", "n": 1}]
    assert d["cobertura"]["contactados"] == 3
    assert d["cierres"][0]["nombre"] == "B"


def test_comision_sale_de_la_config(db):
    fid.set_config(db, {"comision_pct": 10, "precio_usd": 200, "inventada": 3})
    cfg = fid.get_config(db)
    assert cfg["comision_pct"] == 10 and cfg["precio_usd"] == 200 and "inventada" not in cfg
    pid = _p(db)
    fid.mover_estado(db, pid, "cerrado", "L")
    assert fid.armar_intel(db, "todo", cuando=AHORA)["kpis"]["comision"] == 20


def test_export_csv(admin, app):
    _p(app.config["_DB"])
    r = admin.get("/api/fidelidad/export.csv")
    assert r.status_code == 200 and "La Perdiz" in r.get_data(as_text=True)


# ── la pantalla ──────────────────────────────────────────────────────────────

def test_sdr_sale_del_menu_y_outbound_es_fidelidad():
    html = dashboard.DASHBOARD_HTML
    assert 'id="nav-sdr"' not in html
    assert "Outbound · Scalerics Fidelidad" in html
    assert "Inteligencia comercial · Fidelidad" in html
    assert "function fidCargarHoy" in html
    assert "/*FID_JS*/" not in html


def test_la_pagina_renderiza(admin):
    r = admin.get("/")
    assert r.status_code == 200
    cuerpo = r.get_data(as_text=True)
    assert "fidCargarIntel" in cuerpo and "{% raw %}" not in cuerpo


# ── el scraper ───────────────────────────────────────────────────────────────

def test_el_scraper_solo_guarda_comida_en_el_territorio(db):
    import scraper
    assert fid.es_de_comida("Parrilla") and fid.es_de_comida("Pizzería") and fid.es_de_comida("")
    assert not fid.es_de_comida("Farmacia") and not fid.es_de_comida("Supermercado")
    ficha = {"name": "Parrilla Nueva", "category": "Parrilla", "address": "Av. Brasil 2800, Montevideo",
             "phone": "+598 2700 0000", "rating": 4.4, "review_count": 320,
             "maps_url": "https://www.google.com/maps/place/nueva"}
    assert scraper._guardar_fidelidad(ficha, "Pocitos", db) is True
    assert scraper._guardar_fidelidad(ficha, "Pocitos", db) is False     # ya estaba
    fila = _uno(db, "SELECT zona, barrio, fuente, resenas FROM fid_prospectos WHERE nombre='Parrilla Nueva'")
    assert tuple(fila) == ("Municipio CH", "Pocitos", "scraper", 320)
    # No toca el padron de las campañas.
    assert _uno(db, "SELECT COUNT(*) FROM businesses")[0] == 0


def test_todos_los_barrios_caen_en_su_zona():
    for zona, barrios in fid.BARRIOS.items():
        for b in barrios:
            assert fid.normalizar_zona(None, b) == zona, b


def test_el_comando_recorre_los_barrios_de_la_zona(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")   # main.py lo exige al importarse
    import main
    llamadas = []
    monkeypatch.setattr("scraper.run", lambda q, n, db, ya_vistos=None, fidelidad_barrio=None:
                        llamadas.append((q, n, fidelidad_barrio)) or 1)
    args = main.create_parser().parse_args(["scrape-fidelidad", "--zona", "Carrasco", "--max-por-barrio", "5"])
    assert main.cmd_scrape_fidelidad(args) == 3
    assert llamadas == [("restaurantes en Carrasco, Montevideo", 5, "Carrasco"),
                        ("restaurantes en Carrasco Norte, Montevideo", 5, "Carrasco Norte"),
                        ("restaurantes en Barra de Carrasco, Canelones", 5, "Barra de Carrasco")]


def test_ningun_reparto_de_paneles_le_suma_nada_al_vendedor(db):
    from database import _grant_panel_to_existing_roles
    c = sqlite3.connect(db)
    try:
        _grant_panel_to_existing_roles(c, "panel_nuevo", si_tiene=("cola", "metrics"))
    finally:
        c.close()
    fila = _uno(db, "SELECT panel_access FROM roles WHERE name = ?", (fid.ROL_VENDEDOR,))
    assert json.loads(fila[0]) == ["cola", "metrics"]
    assert "panel_nuevo" in json.loads(_uno(db, "SELECT panel_access FROM roles WHERE name='Admin'")[0])
