"""Registro de demos por mes, con el presupuesto adjunto de cada una.

Pedido de Juan (14/9): "una sección para ir viendo las demos hechas por mes,
donde se vean esas y los presupuestos".

El presupuesto va como BLOB en `lead_attachments` (con `demo_id`). Eso es
aceptable solo con tres cuidados, y estos tests los fijan: tope de 10 MB en el
backend, solo PDF o imagen, y que ningún listado lea `file_data` (la máquina
tiene 512 MB y ya hubo un OOM).
"""

import io
import re
import shutil
import sqlite3
import subprocess

import pytest
from werkzeug.security import generate_password_hash

import dashboard
import database
from database import (create_user, delete_business, get_attachments, init_db,
                      insert_business, listar_demos_realizadas,
                      obtener_presupuesto_demo)

HTML = dashboard.DASHBOARD_HTML
MB = 1024 * 1024
PDF = b"%PDF-1.4" + b"x" * 2000
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200

node = pytest.mark.skipif(shutil.which("node") is None, reason="node no esta instalado")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "demos.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


@pytest.fixture
def cli(app):
    uid = create_user(app.config["_DB"], name="jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    return c


_tel = iter(range(700000, 799999))


def _demo(app, cli, nombre="Optica Luz", fecha=None):
    lid = insert_business(app.config["_DB"], {"name": nombre, "phone": f"+598{next(_tel)}"})
    cuerpo = {"client_id": lid, "actualizacion": "mostramos la web"}
    if fecha:
        cuerpo["fecha"] = fecha
    r = cli.post("/api/demos-realizadas", json=cuerpo)
    assert r.status_code == 201, r.get_json()
    return lid, r.get_json()["id"]


def _subir(cli, demo_id, datos, nombre="presupuesto.pdf", tipo="application/pdf"):
    return cli.post(f"/api/demos-realizadas/{demo_id}/presupuesto",
                    data={"file": (io.BytesIO(datos), nombre, tipo)},
                    content_type="multipart/form-data")


def _adjuntos_de(db, demo_id):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM lead_attachments WHERE demo_id = ?",
                            (demo_id,)).fetchone()[0]
    finally:
        conn.close()


# ── diagnóstico: el backend sí devuelve todas ────────────────────────────────

def test_el_listado_sin_cliente_trae_las_demos_de_todos(app, cli):
    """El panel se veía vacío y no por el backend: sin client_id devuelve todas."""
    _demo(app, cli, "Optica Luz")
    _demo(app, cli, "Vinoteca Sur")
    demos = cli.get("/api/demos-realizadas?limite=5000").get_json()["demos"]
    assert sorted(d["cliente_nombre"] for d in demos) == ["Optica Luz", "Vinoteca Sur"]


# ── adjuntar y descargar ─────────────────────────────────────────────────────

def test_adjuntar_un_pdf_y_volver_a_descargarlo(app, cli):
    _, did = _demo(app, cli)
    r = _subir(cli, did, PDF, "Presupuesto Optica.pdf")
    assert r.status_code == 201, r.get_json()

    d = cli.get("/api/demos-realizadas").get_json()["demos"][0]
    assert d["presupuesto_id"] == r.get_json()["id"]
    assert d["presupuesto_nombre"] == "Presupuesto_Optica.pdf"

    bajada = cli.get(f"/api/demos-realizadas/{did}/presupuesto")
    assert bajada.status_code == 200
    assert bajada.data == PDF
    assert bajada.mimetype == "application/pdf"
    assert bajada.headers["Content-Disposition"].startswith("attachment")
    assert bajada.headers["X-Content-Type-Options"] == "nosniff"


def test_una_demo_sin_presupuesto_se_distingue(app, cli):
    _, con = _demo(app, cli, "Con")
    _demo(app, cli, "Sin")
    _subir(cli, con, PDF)
    por_nombre = {d["cliente_nombre"]: d for d in cli.get("/api/demos-realizadas").get_json()["demos"]}
    assert por_nombre["Con"]["presupuesto_id"]
    assert por_nombre["Sin"]["presupuesto_id"] is None
    assert por_nombre["Sin"]["presupuesto_nombre"] is None


def test_una_imagen_tambien_sirve(app, cli):
    _, did = _demo(app, cli)
    assert _subir(cli, did, PNG, "captura.png", "image/png").status_code == 201
    assert cli.get(f"/api/demos-realizadas/{did}/presupuesto").mimetype == "image/png"


def test_el_tipo_sale_del_archivo_y_no_del_nombre(app, cli):
    """Un PNG llamado .pdf se guarda como PNG: el nombre no manda."""
    _, did = _demo(app, cli)
    r = _subir(cli, did, PNG, "foto.pdf", "application/pdf")
    assert r.status_code == 201
    assert r.get_json()["nombre"] == "foto.png"


def test_un_html_disfrazado_de_pdf_se_rechaza(app, cli):
    """Servido desde el origen del CRM, un HTML correría con la sesión del usuario."""
    _, did = _demo(app, cli)
    r = _subir(cli, did, b"<html><script>alert(1)</script></html>", "presupuesto.pdf")
    assert r.status_code == 400
    assert _adjuntos_de(app.config["_DB"], did) == 0


def test_un_archivo_de_mas_de_10_mb_se_rechaza(app, cli):
    _, did = _demo(app, cli)
    r = _subir(cli, did, b"%PDF-" + b"0" * (10 * MB - 4))
    assert r.status_code == 413
    assert _adjuntos_de(app.config["_DB"], did) == 0


def test_uno_muy_grande_se_corta_antes_de_leerlo(app, cli):
    """Mirando el Content-Length, sin parsear el multipart."""
    _, did = _demo(app, cli)
    assert _subir(cli, did, b"%PDF-" + b"0" * (11 * MB)).status_code == 413


def test_10_mb_justos_entran(app, cli):
    _, did = _demo(app, cli)
    assert _subir(cli, did, b"%PDF-" + b"0" * (10 * MB - 5)).status_code == 201


def test_sin_archivo_o_vacio_da_400(app, cli):
    _, did = _demo(app, cli)
    assert cli.post(f"/api/demos-realizadas/{did}/presupuesto", data={},
                    content_type="multipart/form-data").status_code == 400
    assert _subir(cli, did, b"").status_code == 400


def test_subir_otro_reemplaza_al_anterior(app, cli):
    """Uno por demo: no se acumulan BLOBs que nadie ve."""
    _, did = _demo(app, cli)
    _subir(cli, did, PDF, "v1.pdf")
    _subir(cli, did, PDF, "v2.pdf")
    assert _adjuntos_de(app.config["_DB"], did) == 1
    assert cli.get("/api/demos-realizadas").get_json()["demos"][0]["presupuesto_nombre"] == "v2.pdf"


def test_quitar_el_presupuesto(app, cli):
    _, did = _demo(app, cli)
    _subir(cli, did, PDF)
    assert cli.delete(f"/api/demos-realizadas/{did}/presupuesto").get_json()["ok"]
    assert _adjuntos_de(app.config["_DB"], did) == 0
    assert cli.delete(f"/api/demos-realizadas/{did}/presupuesto").status_code == 404
    assert cli.get(f"/api/demos-realizadas/{did}/presupuesto").status_code == 404


def test_demo_inexistente_da_404(app, cli):
    assert _subir(cli, 99999, PDF).status_code == 404
    assert cli.get("/api/demos-realizadas/99999/presupuesto").status_code == 404
    assert cli.delete("/api/demos-realizadas/99999/presupuesto").status_code == 404


def test_el_presupuesto_aparece_en_la_ficha_del_cliente(app, cli):
    """Por eso `section` queda en 'budget' y la demo va en su propia columna."""
    lid, did = _demo(app, cli)
    _subir(cli, did, PDF, "p.pdf")
    assert [a["name"] for a in get_attachments(app.config["_DB"], lid, "budget")] == ["p.pdf"]


def test_borrar_la_demo_se_lleva_su_presupuesto(app, cli):
    _, did = _demo(app, cli)
    _subir(cli, did, PDF)
    cli.delete(f"/api/demos-realizadas/{did}")
    assert _adjuntos_de(app.config["_DB"], did) == 0


def test_borrar_el_cliente_se_lleva_los_presupuestos_de_sus_demos(app, cli):
    lid, did = _demo(app, cli)
    _subir(cli, did, PDF)
    delete_business(app.config["_DB"], lid)
    assert _adjuntos_de(app.config["_DB"], did) == 0


# ── peso: ningún listado lee el BLOB ─────────────────────────────────────────

@pytest.fixture
def lecturas(monkeypatch):
    """Cada columna que SQLite lee, vía el authorizer: mide lo que la consulta
    toca de verdad, no lo que termina en el JSON."""
    leidas = []
    real = database._connect

    def espia(*a, **k):
        conn = real(*a, **k)

        def autorizar(accion, tabla, columna, *_):
            if accion == sqlite3.SQLITE_READ:
                leidas.append((tabla, columna))
            return sqlite3.SQLITE_OK

        conn.set_authorizer(autorizar)
        return conn

    monkeypatch.setattr(database, "_connect", espia)
    return leidas


def test_el_listado_de_demos_no_lee_file_data(app, cli, lecturas):
    lid, did = _demo(app, cli)
    _subir(cli, did, PDF)
    lecturas.clear()

    listar_demos_realizadas(app.config["_DB"])
    listar_demos_realizadas(app.config["_DB"], client_id=lid)
    cuerpo = cli.get("/api/demos-realizadas?limite=5000").get_data(as_text=True)

    assert ("lead_attachments", "name") in lecturas, "el espía no vio el join"
    assert ("lead_attachments", "file_data") not in lecturas
    assert "file_data" not in cuerpo


def test_la_descarga_si_lee_file_data(app, cli, lecturas):
    """Control del espía: si esto no lo ve, el test de arriba no prueba nada."""
    _, did = _demo(app, cli)
    _subir(cli, did, PDF)
    lecturas.clear()
    assert obtener_presupuesto_demo(app.config["_DB"], did)["file_data"] == PDF
    assert ("lead_attachments", "file_data") in lecturas


# ── migración ────────────────────────────────────────────────────────────────

def test_la_migracion_agrega_demo_id_a_una_base_vieja(tmp_path):
    db = str(tmp_path / "vieja.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE lead_attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL,
        section TEXT NOT NULL, name TEXT NOT NULL, url TEXT, file_data BLOB,
        mime_type TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("INSERT INTO lead_attachments (lead_id, section, name) VALUES (1, 'budget', 'viejo')")
    conn.commit()
    conn.close()

    init_db(db)
    init_db(db)  # dos veces: tiene que ser idempotente

    conn = sqlite3.connect(db)
    try:
        columnas = [f[1] for f in conn.execute("PRAGMA table_info(lead_attachments)")]
        indices = [f[1] for f in conn.execute("PRAGMA index_list(lead_attachments)")]
        viejo = conn.execute("SELECT name, demo_id FROM lead_attachments").fetchall()
    finally:
        conn.close()
    assert "demo_id" in columnas
    assert "idx_lead_attachments_demo" in indices
    assert viejo == [("viejo", None)]


# ── la pantalla ──────────────────────────────────────────────────────────────

def test_la_pagina_se_renderiza_con_el_panel_nuevo(app, cli):
    r = cli.get("/")
    assert r.status_code == 200
    assert b'id="demos-presu-filtro"' in r.data
    assert b'id="demos-body"' in r.data


def test_el_css_de_demos_usa_solo_tokens():
    reglas = re.findall(r"^\.demo-[^{\n]*\{[^}]*\}", HTML, re.M)
    assert len(reglas) >= 15, f"el parser vio pocas reglas: {len(reglas)}"
    a_mano = [r[:50] for r in reglas if re.search("#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])", r)]
    assert not a_mano, f"reglas .demo- con colores a mano: {a_mano}"
    assert not re.search(r"^body\.light \.demo-", HTML, re.M), "los tokens ya cubren el claro"


def test_el_js_de_demos_no_pinta_colores_a_mano():
    i = HTML.index("// == Registro de demos")
    js = HTML[i:HTML.index("// == Responsables del cliente", i)]
    a_mano = re.findall("(?:color|background|border[a-z-]*)[:][^;\"`]*?#[0-9a-fA-F]{3,6}(?![0-9a-zA-Z])", js)
    assert not a_mano, a_mano


def _funcion(nombre):
    m = re.search(r"\n(?:async )?function " + nombre + r"\(.*?\n\}", HTML, re.S)
    assert m, f"no encontré la función {nombre}"
    return m.group(0)


def _correr(cuerpo, tmp_path):
    meses = re.search(r"const _DEMOS_MESES = \[.*?\];", HTML, re.S)
    assert meses, "no encontré _DEMOS_MESES"
    fuente = "\n".join([
        dashboard.ESC_JS, meses.group(0),
        *(_funcion(n) for n in ("_demosMesDe", "_demosEtiquetaMes", "_demosAgruparPorMes",
                                "_demosFecha", "_demosFilaHtml", "renderDemos")),
        """
        function assert(c, m) { if (!c) { throw new Error(m); } }
        const body = {innerHTML: ''};
        const document = {getElementById: () => body};
        let _demosFiltro = '', _demosFiltroPresu = '';
        let _demos = [
          {id: 1, numero: 1, client_id: 10, cliente_nombre: 'Optica Luz', fecha: '2026-08-20T15:00:00',
           presupuesto_id: 7, presupuesto_nombre: 'presu-optica.pdf', realizada_por_nombre: 'thomy'},
          {id: 2, numero: 2, client_id: 11, cliente_nombre: 'Vinoteca Sur', fecha: '2026-09-01',
           presupuesto_id: null, presupuesto_nombre: null},
          {id: 3, numero: 1, client_id: 12, cliente_nombre: 'Taller Norte', fecha: null,
           created_at: '2026-09-10 12:00:00', presupuesto_id: 9, presupuesto_nombre: 'p.pdf'},
          {id: 4, numero: 1, client_id: 13, cliente_nombre: 'Panaderia', fecha: '2025-01-05T10:00:00',
           presupuesto_id: null},
        ];
        const pos = s => body.innerHTML.indexOf(s);
        """,
        cuerpo,
    ])
    archivo = tmp_path / "demos.js"
    archivo.write_text(fuente, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr


@node
def test_agrupa_por_mes_del_mas_reciente_al_mas_viejo(tmp_path):
    _correr("""
      const g = _demosAgruparPorMes(_demos);
      assert(g.map(x => x.etiqueta).join('|') === 'Septiembre 2026|Agosto 2026|Enero 2025',
             g.map(x => x.etiqueta).join('|'));
      assert(g[0].demos.length === 2, 'septiembre tiene 2');
      assert(g[0].conPresupuesto === 1, 'una con presupuesto');
      assert(_demosMesDe({fecha: '2026-09-01'}) === '2026-09', 'el 1 no se corre a agosto');
      assert(_demosMesDe({}) === 'sin-fecha', 'sin fecha');
      assert(_demosFecha('2026-09-01') === '01/09/2026', _demosFecha('2026-09-01'));
    """, tmp_path)


@node
def test_la_pantalla_muestra_meses_cantidades_y_presupuestos(tmp_path):
    _correr("""
      renderDemos();
      assert(pos('Septiembre 2026') !== -1 && pos('Septiembre 2026') < pos('Agosto 2026'), 'orden');
      assert(pos('Agosto 2026') < pos('Enero 2025'), 'orden viejo');
      assert(pos('2 demos') !== -1, 'cantidad del mes');
      assert(pos('1 demo<') !== -1, 'singular');
      assert(pos('href="/api/demos-realizadas/1/presupuesto"') !== -1, 'link de descarga');
      assert(pos('presu-optica.pdf') !== -1, 'nombre del archivo');
      assert(pos('Sin presupuesto') !== -1, 'marca la que no tiene');
      assert(pos('demosSubirPresupuesto(2, this)') !== -1, 'se puede adjuntar');
    """, tmp_path)


@node
def test_filtrar_por_presupuesto_y_vacio(tmp_path):
    _correr("""
      _demosFiltroPresu = 'sin'; renderDemos();
      assert(pos('Vinoteca Sur') !== -1 && pos('Optica Luz') === -1, 'solo sin presupuesto');
      _demosFiltroPresu = ''; _demosFiltro = 'nadie'; renderDemos();
      assert(pos('Ninguna demo coincide') !== -1, body.innerHTML);
      _demosFiltro = ''; _demos = []; renderDemos();
      assert(pos('Todavía no hay demos registradas') !== -1, body.innerHTML);
    """, tmp_path)
