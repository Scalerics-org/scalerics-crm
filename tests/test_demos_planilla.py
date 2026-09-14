# -*- coding: utf-8 -*-
"""El Registro de demos se llena solo desde la planilla de semaforo.

Pedido de Juan (14/9): que las demos entren solas con los colores de la planilla,
mes por mes, cada una marcada con su color. Decisiones suyas que estos tests
fijan: entran los cuatro colores de demo (verde, celeste, violeta y verde
oscuro), el mes de la demo es el de la pestana, y las demos cargadas a mano no
se tocan.
"""
import re
import shutil
import sqlite3
import subprocess
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

import dashboard
import services.planilla_semaforo as ps
from database import (create_user, crear_demo_realizada, guardar_presupuesto_demo,
                      init_db, listar_demos_realizadas)
from services.planilla_semaforo import mes_de_pestana, sincronizar_demos

VERDE = "#00ff00"
CELESTE = "#00ffff"
VIOLETA = "#ff00ff"
VENTA = "#274e13"
VENTA_2 = "#38761d"
NEGRO = "#000000"
ROJO = "#ff0000"
AMARILLO = "#ffff00"

HOY = date(2026, 9, 14)
TEL = "+59899913326"
HTML = dashboard.DASHBOARD_HTML

node = pytest.mark.skipif(shutil.which("node") is None, reason="node no esta instalado")


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


def _lead(db, bid, phone=TEL, source="meta", email=None, estado="sin_contactar"):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO businesses (id, name, phone, email, crm_status, source) "
                 "VALUES (?,?,?,?,?,?)", (bid, f"Negocio {bid}", phone, email, estado, source))
    conn.commit()
    conn.close()


def _demos(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, client_id, numero, fecha, origen, estado_planilla, mes_planilla, "
            "actualizacion FROM demos_realizadas ORDER BY id")]
    finally:
        conn.close()


def _sync(db, *filas, dry=False, hoy=HOY):
    return sincronizar_demos(db, list(filas), dry_run=dry, hoy=hoy)


def _fila(color, mes="Agosto", tel=TEL, mail=""):
    return {"tel": tel, "mail": mail, "color": color, "mes": mes}


# ── cada color ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("color,estado", [
    (VERDE, "agendada"), (CELESTE, "realizada"), (VIOLETA, "no_cerro"),
    (VENTA, "venta"), (VENTA_2, "venta"),
])
def test_cada_color_de_demo_crea_su_demo(db, color, estado):
    _lead(db, 1)
    r = _sync(db, _fila(color))
    assert r["creadas"] == 1
    [d] = _demos(db)
    assert (d["client_id"], d["origen"], d["estado_planilla"]) == (1, "planilla", estado)
    assert d["mes_planilla"] == "2026-08" and d["fecha"] == "2026-08-01"
    assert d["numero"] == 1


@pytest.mark.parametrize("color", [NEGRO, ROJO, "#f4cccc", AMARILLO, "#fff2cc", "#ffffff", "#123456"])
def test_los_colores_que_no_son_demo_no_crean_nada(db, color):
    _lead(db, 1)
    r = _sync(db, _fila(color))
    assert _demos(db) == []
    assert r["creadas"] == 0 and r["sin_match"] == 0


# ── el mes y el año de la pestaña ────────────────────────────────────────────

@pytest.mark.parametrize("nombre,esperado", [
    ("Marzo", "2026-03"), ("Agosto", "2026-08"), ("  agosto ", "2026-08"),
    ("Setiembre", "2026-09"), ("Septiembre", "2026-09"), ("SETIEMBRE", "2026-09"),
    ("Setiémbre", "2026-09"), ("Octubre", "2025-10"), ("Diciembre", "2025-12"),
    ("Agosto 2024", "2024-08"),
])
def test_el_mes_de_la_pestana(nombre, esperado):
    assert mes_de_pestana(nombre, HOY) == esperado


def test_un_mes_posterior_al_actual_es_del_ano_anterior():
    """En febrero, la pestaña 'Diciembre' es el diciembre pasado."""
    febrero = date(2027, 2, 10)
    assert mes_de_pestana("Diciembre", febrero) == "2026-12"
    assert mes_de_pestana("Febrero", febrero) == "2027-02"
    assert mes_de_pestana("Enero", febrero) == "2027-01"
    assert mes_de_pestana("Marzo", febrero) == "2026-03"


@pytest.mark.parametrize("nombre", ["Analisis", "Análisis", "Semaforo", "Semáforo",
                                    "Cuenta Corriente", "", None, "Agosto viejo", "Hoja 1"])
def test_una_pestana_que_no_es_mes_se_ignora(db, nombre):
    assert mes_de_pestana(nombre, HOY) is None
    _lead(db, 1)
    r = _sync(db, _fila(CELESTE, mes=nombre))
    assert _demos(db) == [] and r["mes_ignorado"] == 1


def test_una_fila_sin_mes_no_crea_demo(db):
    """Las corridas a mano sin `mes` siguen aplicando estados y no inventan demos."""
    _lead(db, 1)
    r = _sync(db, {"tel": TEL, "color": CELESTE})
    assert _demos(db) == [] and r["mes_ignorado"] == 1


def test_el_cambio_de_ano_llega_a_la_fecha(db):
    _lead(db, 1)
    _sync(db, _fila(CELESTE, mes="Diciembre"), hoy=date(2027, 1, 5))
    assert _demos(db)[0]["fecha"] == "2026-12-01"


def test_la_fecha_por_defecto_es_la_de_uruguay(monkeypatch):
    """Sin `hoy`, toma el día de Uruguay (UTC-3), no el de la máquina."""
    monkeypatch.setattr(ps, "_hoy_uruguay", lambda: date(2026, 1, 20))
    assert mes_de_pestana("Diciembre") == "2025-12"


# ── idempotencia y cambios de color ──────────────────────────────────────────

def test_correrla_dos_veces_no_escribe_la_segunda(db):
    _lead(db, 1)
    _lead(db, 2, phone="+59899000002")
    filas = [_fila(VERDE), _fila(VIOLETA, tel="+59899000002", mes="Julio")]
    assert _sync(db, *filas)["creadas"] == 2
    antes = _demos(db)

    # `data_version` cambia en esta conexion si OTRA conexion commitea algo:
    # prueba que la segunda corrida no escribio, no solo que dejo lo mismo.
    testigo = sqlite3.connect(db)
    try:
        version = testigo.execute("PRAGMA data_version").fetchone()[0]
        segunda = _sync(db, *filas)
        assert testigo.execute("PRAGMA data_version").fetchone()[0] == version, "escribio"
    finally:
        testigo.close()
    assert (segunda["creadas"], segunda["actualizadas"], segunda["borradas"]) == (0, 0, 0)
    assert segunda["sin_cambio"] == 2
    assert _demos(db) == antes

    # Control del testigo: una corrida que si escribe tiene que moverlo.
    testigo = sqlite3.connect(db)
    try:
        version = testigo.execute("PRAGMA data_version").fetchone()[0]
        _sync(db, _fila(CELESTE), filas[1])
        assert testigo.execute("PRAGMA data_version").fetchone()[0] != version
    finally:
        testigo.close()


def test_un_cambio_de_color_actualiza_y_no_duplica(db):
    _lead(db, 1)
    _sync(db, _fila(VERDE))
    [original] = _demos(db)
    for color, estado in ((CELESTE, "realizada"), (VIOLETA, "no_cerro"), (VENTA, "venta")):
        r = _sync(db, _fila(color))
        assert r["actualizadas"] == 1 and r["creadas"] == 0
        [d] = _demos(db)
        assert d["id"] == original["id"] and d["estado_planilla"] == estado


def test_la_demo_refleja_la_planilla_aunque_vuelva_atras(db):
    """Si alguien corrige un violeta a celeste, el registro dice celeste."""
    _lead(db, 1)
    _sync(db, _fila(VIOLETA))
    _sync(db, _fila(CELESTE))
    assert _demos(db)[0]["estado_planilla"] == "realizada"


def test_el_mismo_lead_en_dos_meses_son_dos_demos(db):
    _lead(db, 1)
    r = _sync(db, _fila(VIOLETA, mes="Setiembre"), _fila(CELESTE, mes="Julio"))
    assert r["creadas"] == 2
    demos = {d["mes_planilla"]: d for d in _demos(db)}
    assert demos["2026-07"]["estado_planilla"] == "realizada"
    assert demos["2026-09"]["estado_planilla"] == "no_cerro"
    assert demos["2026-07"]["numero"] == 1 and demos["2026-09"]["numero"] == 2, \
        "el numero sigue el orden de los meses"


@pytest.mark.parametrize("orden", [0, 1])
def test_mismo_telefono_dos_veces_en_la_pestana_gana_el_mas_avanzado(db, orden):
    _lead(db, 1)
    filas = [_fila(VERDE), _fila(VIOLETA), _fila(ROJO)]
    if orden:
        filas.reverse()
    _sync(db, *filas)
    [d] = _demos(db)
    assert d["estado_planilla"] == "no_cerro"


def test_una_fila_repetida_con_color_de_demo_le_gana_al_negro(db):
    _lead(db, 1)
    _sync(db, _fila(CELESTE))
    r = _sync(db, _fila(NEGRO), _fila(CELESTE))
    assert r["borradas"] == 0 and len(_demos(db)) == 1


def test_casa_por_mail_y_por_los_ultimos_ocho_digitos(db):
    _lead(db, 1, email="lead@ejemplo.com")
    _lead(db, 2, phone="+59899000002")
    _sync(db, _fila(CELESTE, tel="0000", mail="LEAD@ejemplo.com"),
          _fila(VERDE, tel="99000002"))
    assert sorted(d["client_id"] for d in _demos(db)) == [1, 2]


# ── borrado ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("color", [NEGRO, ROJO, AMARILLO])
def test_si_la_fila_deja_de_ser_demo_se_borra(db, color):
    _lead(db, 1)
    _sync(db, _fila(CELESTE))
    r = _sync(db, _fila(color))
    assert r["borradas"] == 1 and _demos(db) == []


def test_con_presupuesto_adjunto_no_se_borra(db):
    _lead(db, 1)
    _sync(db, _fila(CELESTE))
    [d] = _demos(db)
    guardar_presupuesto_demo(db, d["id"], 1, "p.pdf", b"%PDF-1.4 x", "application/pdf")
    r = _sync(db, _fila(NEGRO))
    assert r["borradas"] == 0 and r["con_presupuesto_no_se_borra"] == 1
    assert [x["id"] for x in _demos(db)] == [d["id"]]


def test_una_fila_que_deja_de_venir_no_borra(db):
    """Pintada de blanco el script no la manda: la ausencia no es una marca."""
    _lead(db, 1)
    _sync(db, _fila(CELESTE))
    r = _sync(db)
    assert r["borradas"] == 0 and len(_demos(db)) == 1


def test_el_negro_en_otro_mes_no_borra_la_demo_de_este(db):
    _lead(db, 1)
    _sync(db, _fila(CELESTE, mes="Julio"))
    _sync(db, _fila(NEGRO, mes="Agosto"))
    assert [d["mes_planilla"] for d in _demos(db)] == ["2026-07"]


# ── lo que no toca ───────────────────────────────────────────────────────────

def test_no_toca_las_demos_cargadas_a_mano(db):
    _lead(db, 1)
    mano = crear_demo_realizada(db, 1, fecha="2026-08-10", actualizacion="la di yo")
    _sync(db, _fila(CELESTE))
    por_origen = {d["origen"]: d for d in _demos(db)}
    assert por_origen["planilla"]["numero"] == 2, "sigue la numeracion del cliente"

    _sync(db, _fila(NEGRO))
    [queda] = _demos(db)
    assert queda["id"] == mano and queda["origen"] is None
    assert queda["actualizacion"] == "la di yo" and queda["numero"] == 1


def test_una_demo_a_mano_del_mismo_mes_no_frena_la_de_planilla(db):
    """El indice unico es solo para las de planilla."""
    _lead(db, 1)
    crear_demo_realizada(db, 1, fecha="2026-08-10")
    crear_demo_realizada(db, 1, fecha="2026-08-20")
    assert _sync(db, _fila(VERDE))["creadas"] == 1
    assert len(_demos(db)) == 3


@pytest.mark.parametrize("source", ["discovery", None, "padron"])
def test_no_toca_leads_que_no_son_de_meta(db, source):
    _lead(db, 1, source=source)
    r = _sync(db, _fila(CELESTE))
    assert _demos(db) == [] and r["sin_match"] == 1


def test_el_dry_run_no_escribe(db):
    _lead(db, 1)
    r = _sync(db, _fila(CELESTE), dry=True)
    assert r["creadas"] == 1 and _demos(db) == []

    _sync(db, _fila(CELESTE))
    assert _sync(db, _fila(VIOLETA), dry=True)["actualizadas"] == 1
    assert _demos(db)[0]["estado_planilla"] == "realizada"
    assert _sync(db, _fila(NEGRO), dry=True)["borradas"] == 1
    assert len(_demos(db)) == 1


def test_el_indice_unico_frena_un_duplicado(db):
    _lead(db, 1)
    _sync(db, _fila(CELESTE))
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO demos_realizadas (client_id, origen, mes_planilla) "
                         "VALUES (1, 'planilla', '2026-08')")
    finally:
        conn.close()


def test_el_listado_trae_origen_y_estado(db):
    _lead(db, 1)
    _sync(db, _fila(VIOLETA))
    [d] = listar_demos_realizadas(db)
    assert (d["origen"], d["estado_planilla"], d["mes_planilla"]) == ("planilla", "no_cerro", "2026-08")


def test_la_migracion_agrega_las_columnas_a_una_base_vieja(tmp_path):
    ruta = str(tmp_path / "vieja.db")
    conn = sqlite3.connect(ruta)
    conn.execute("""CREATE TABLE demos_realizadas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER NOT NULL, numero INTEGER,
        realizada_por INTEGER, fecha TIMESTAMP, actualizacion TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, created_by INTEGER)""")
    conn.execute("INSERT INTO demos_realizadas (client_id, numero, fecha) VALUES (1, 1, '2026-08-02')")
    conn.commit()
    conn.close()

    init_db(ruta)
    init_db(ruta)

    conn = sqlite3.connect(ruta)
    try:
        cols = [f[1] for f in conn.execute("PRAGMA table_info(demos_realizadas)")]
        indices = [f[1] for f in conn.execute("PRAGMA index_list(demos_realizadas)")]
        vieja = conn.execute("SELECT origen, estado_planilla, mes_planilla FROM demos_realizadas").fetchall()
    finally:
        conn.close()
    assert {"origen", "estado_planilla", "mes_planilla"} <= set(cols)
    assert "idx_demos_planilla_cliente_mes" in indices
    assert vieja == [(None, None, None)]


# ── la ruta del sync ─────────────────────────────────────────────────────────

AUTH = {"x-admin-token": "token-de-test"}


@pytest.fixture
def cliente(db, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-test")
    monkeypatch.setenv("DB_PATH", db)
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    return app.test_client()


def test_la_ruta_carga_las_demos_y_lo_suma_a_la_respuesta(cliente, db):
    _lead(db, 1)
    r = cliente.post("/api/meta/sync-planilla", headers=AUTH,
                     json={"filas": [_fila(CELESTE, mes="Setiembre")]})
    cuerpo = r.get_json()
    assert r.status_code == 200 and cuerpo["actualizados"] == 1
    assert cuerpo["demos"]["creadas"] == 1
    assert _demos(db)[0]["mes_planilla"].endswith("-09")


def test_la_ruta_respeta_el_dry_para_las_demos(cliente, db):
    _lead(db, 1)
    r = cliente.post("/api/meta/sync-planilla?dry=1", headers=AUTH,
                     json={"filas": [_fila(CELESTE)]})
    assert r.get_json()["demos"]["creadas"] == 1
    assert _demos(db) == []


def test_si_las_demos_fallan_los_estados_igual_se_aplican(cliente, db, monkeypatch):
    def rompe(*a, **k):
        raise RuntimeError("se rompio la base de demos")

    monkeypatch.setattr(ps, "sincronizar_demos", rompe)
    _lead(db, 1)
    r = cliente.post("/api/meta/sync-planilla", headers=AUTH,
                     json={"filas": [_fila(VIOLETA)]})
    cuerpo = r.get_json()
    assert r.status_code == 200 and cuerpo["ok"] is True
    assert cuerpo["actualizados"] == 1
    assert cuerpo["demos"]["ok"] is False
    assert "se rompio" not in r.get_data(as_text=True), "el detalle va al log, no a la planilla"
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT crm_status FROM businesses WHERE id=1").fetchone()[0] == "presupuesto_enviado"
    conn.close()


def test_la_primera_corrida_carga_todas_las_pestanas_en_un_solo_post(cliente, db, monkeypatch):
    """Lo que pidió Juan: al publicar, aparecen TODAS las demos del Excel sin
    cargar nada. El Apps Script manda todas las filas de todas las pestañas en
    un solo POST; eso tiene que crear las demos de todos los meses de una vez."""
    monkeypatch.setattr(ps, "_hoy_uruguay", lambda: HOY)
    pestanas = [("Marzo", VERDE), ("Abril", CELESTE), ("Mayo", VIOLETA), ("Junio", VENTA),
                ("Julio", VENTA_2), ("Agosto", CELESTE), ("Setiembre", VERDE)]
    filas = []
    for i, (pestana, color) in enumerate(pestanas, start=1):
        _lead(db, i, phone=f"+5989900000{i}")
        filas.append(_fila(color, mes=pestana, tel=f"+5989900000{i}"))
        filas.append(_fila(ROJO, mes=pestana, tel="+59891111111"))  # no es demo ni casa
    _lead(db, 20, phone="+59899000020")
    filas.append(_fila(CELESTE, mes="Julio", tel="+59899000020"))   # un lead en dos meses
    filas.append(_fila(VIOLETA, mes="Agosto", tel="+59899000020"))
    filas.append(_fila(CELESTE, mes="Analisis", tel="+59899000001"))  # no es mes
    filas.append(_fila(CELESTE, mes="Semaforo", tel="+59899000001"))

    r = cliente.post("/api/meta/sync-planilla", headers=AUTH, json={"filas": filas})
    demos = r.get_json()["demos"]
    assert r.status_code == 200
    assert demos["creadas"] == 9 and demos["mes_ignorado"] == 2

    por_mes = {}
    for d in listar_demos_realizadas(db):
        por_mes.setdefault(d["mes_planilla"], []).append(d["estado_planilla"])
    assert sorted(por_mes) == ["2026-03", "2026-04", "2026-05", "2026-06",
                               "2026-07", "2026-08", "2026-09"]
    assert por_mes["2026-06"] == ["venta"] and por_mes["2026-09"] == ["agendada"]
    assert sorted(por_mes["2026-07"]) == ["realizada", "venta"]
    assert sorted(por_mes["2026-08"]) == ["no_cerro", "realizada"]

    otra = cliente.post("/api/meta/sync-planilla", headers=AUTH, json={"filas": filas})
    assert otra.get_json()["demos"]["creadas"] == 0, "la corrida siguiente no duplica"


# ── la pantalla ──────────────────────────────────────────────────────────────

def test_get_raiz_da_200_con_el_filtro_de_estado(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    app = dashboard.create_app(db)
    uid = create_user(db, name="jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    r = c.get("/")
    assert r.status_code == 200
    for fragmento in (b'id="demos-estado-filtro"', b'id="demos-presu-filtro"',
                      b'value="no_cerro"', "Hubo demo y no cerró".encode()):
        assert fragmento in r.data, fragmento


def test_ningun_marcador_de_jinja_en_el_html():
    """`{#` tumbó producción el 14/9: Jinja lo toma como comentario."""
    assert "{#" not in HTML


def test_los_colores_del_semaforo_son_tokens_en_los_dos_temas():
    osc = re.search(r":root\{([^}]*)\}", HTML).group(1)
    cla = re.search(r"body\.light\{([^}]*)\}", HTML).group(1)
    for token, valor in (("--semaforo-verde", "#00ff00"), ("--semaforo-celeste", "#00ffff"),
                         ("--semaforo-violeta", "#ff00ff"), ("--semaforo-venta", "#38761d")):
        assert f"{token}:{valor};" in osc and f"{token}:{valor};" in cla, token
    for clave, token in (("agendada", "verde"), ("realizada", "celeste"),
                         ("no_cerro", "violeta"), ("venta", "venta")):
        assert f".demo-punto-{clave}{{background:var(--semaforo-{token})}}" in HTML


def _funcion(nombre):
    m = re.search(r"\n(?:async )?function " + nombre + r"\(.*?\n\}", HTML, re.S)
    assert m, f"no encontré la función {nombre}"
    return m.group(0)


def _correr(cuerpo, tmp_path):
    constantes = [re.search(r"const " + n + r" = \[.*?\];", HTML, re.S).group(0)
                  for n in ("_DEMOS_MESES", "_DEMOS_ESTADOS")]
    fuente = "\n".join([
        dashboard.ESC_JS, *constantes,
        *(_funcion(n) for n in ("_demosMesDe", "_demosEtiquetaMes", "_demosMesClave",
                                "_demosMesSumar", "_demosMesMasViejo", "_demosMesMover",
                                "_demosDelMes", "_demosMesVisible", "_demosPintarNavegador",
                                "_demosResumenHtml", "_demosFecha", "_demosEstadoDe",
                                "_demosEstadoHtml", "_demosConteoEstados", "_demosFilaHtml",
                                "renderDemos")),
        """
        function assert(c, m) { if (!c) { throw new Error(m); } }
        const body = {innerHTML: ''};
        const els = {};
        const document = {getElementById: id => id === 'demos-body' ? body : (els[id] = els[id] || {id: id})};
        let _demosFiltro = '', _demosFiltroPresu = '', _demosFiltroEstado = '';
        let _demosMesVista = '2026-09';
        const P = (id, cliente, estado, mes, extra) => Object.assign({id: id, numero: 1, client_id: id,
          cliente_nombre: cliente, origen: 'planilla', estado_planilla: estado, mes_planilla: mes,
          fecha: mes + '-01', presupuesto_id: null}, extra || {});
        let _demos = [
          P(1, 'Optica Luz', 'realizada', '2026-09'),
          P(2, 'Vinoteca Sur', 'realizada', '2026-09'),
          P(3, 'Taller Norte', 'no_cerro', '2026-09', {presupuesto_id: 5, presupuesto_nombre: 'p.pdf'}),
          P(4, 'Panaderia', 'venta', '2026-09'),
          P(5, 'Gimnasio', 'agendada', '2026-09'),
          {id: 6, numero: 2, client_id: 6, cliente_nombre: 'Kiosco Mano', fecha: '2026-09-03',
           origen: null, estado_planilla: null, actualizacion: 'la di yo', presupuesto_id: null},
          P(7, 'Heladeria', 'agendada', '2026-08'),
        ];
        const pos = s => body.innerHTML.indexOf(s);
        """,
        cuerpo,
    ])
    archivo = tmp_path / "demos_planilla.js"
    archivo.write_text(fuente, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr


@node
def test_cada_demo_muestra_su_color_y_su_etiqueta(tmp_path):
    _correr("""
      renderDemos();
      for (const [clase, etiqueta] of [['realizada', 'Demo realizada'], ['no_cerro', 'Hubo demo y no cerró'],
                                        ['venta', 'Venta concretada'], ['agendada', 'Demo agendada']]) {
        assert(pos('demo-punto demo-punto-' + clase) !== -1, 'puntito ' + clase);
        assert(pos(etiqueta) !== -1, 'etiqueta ' + etiqueta);
      }
      assert(pos('Cargada a mano') !== -1, 'la de mano');
      assert(pos('la di yo') !== -1, 'la nota de la de mano');
      assert(pos('Sin notas') === -1, 'las de planilla no muestran Sin notas');
      assert(pos('planilla de leads') !== -1, 'dice de donde vino');
    """, tmp_path)


@node
def test_el_resumen_del_mes_cuenta_por_estado_con_su_color(tmp_path):
    _correr("""
      renderDemos();
      const piezas = ['demo-punto-realizada"></span>2 realizadas', 'demo-punto-no_cerro"></span>1 no cerró',
                      'demo-punto-venta"></span>1 venta', 'demo-punto-agendada"></span>1 agendada',
                      'demo-resumen-estado">1 a mano'];
      let antes = -1;
      for (const p of piezas) {
        assert(pos(p) > antes, 'falta o fuera de orden: ' + p);
        antes = pos(p);
      }
      assert(pos('6 demos') !== -1, 'total del mes');
      assert(pos('1 de 6 con presupuesto') !== -1, 'presupuestos del mes');
      assert(pos('aria-label="2 realizadas · 1 no cerró · 1 venta · 1 agendada · 1 a mano"') !== -1, 'se lee entero');
      assert(pos('Heladeria') === -1, 'la de agosto no esta en septiembre');
      assert(_demosConteoEstados([P(1, 'a', 'venta', '2026-09'), P(2, 'b', 'venta', '2026-09')]) === '2 ventas');
      assert(_demosConteoEstados([P(1, 'a', 'no_cerro', '2026-09'), P(2, 'b', 'no_cerro', '2026-09')]) === '2 no cerraron');
    """, tmp_path)


@node
def test_filtrar_por_estado_dentro_del_mes(tmp_path):
    _correr("""
      _demosFiltroEstado = 'venta'; renderDemos();
      assert(pos('Panaderia') !== -1 && pos('Optica Luz') === -1 && pos('Kiosco Mano') === -1, 'solo ventas');
      assert(pos('6 demos') !== -1, 'el resumen sigue siendo del mes entero');
      _demosFiltroEstado = 'mano'; renderDemos();
      assert(pos('Kiosco Mano') !== -1 && pos('Panaderia') === -1, 'solo a mano');
      _demosFiltroEstado = 'agendada'; renderDemos();
      assert(pos('Gimnasio') !== -1 && pos('Heladeria') === -1, 'solo las agendadas del mes mirado');
      _demosMesVista = '2026-08'; renderDemos();
      assert(pos('Heladeria') !== -1 && pos('Gimnasio') === -1, 'y en agosto, la de agosto');
      _demosMesVista = '2026-09'; _demosFiltroEstado = 'no_cerro'; _demosFiltroPresu = 'sin'; renderDemos();
      assert(pos('Ninguna demo coincide con el filtro en Septiembre 2026') !== -1, 'se combina con el de presupuesto');
      _demosFiltroPresu = 'con'; renderDemos();
      assert(pos('Taller Norte') !== -1, 'no cerro con presupuesto');
    """, tmp_path)


@node
def test_el_recorte_por_mes_respeta_los_bordes(tmp_path):
    """Una ventana (septiembre) que no contiene todos los datos, con demos
    pegadas a los dos bordes del mes."""
    _correr("""
      const lista = [
        {id: 1, fecha: '2026-08-31T23:59:59'},
        {id: 2, fecha: '2026-09-01T00:00:00'},
        {id: 3, fecha: '2026-09-01', origen: 'planilla', estado_planilla: 'realizada'},
        {id: 4, fecha: null, created_at: '2026-09-15 10:00:00'},
        {id: 5, fecha: '2026-09-30T23:59:59'},
        {id: 6, fecha: '2026-10-01T00:00:00'},
        {id: 7, fecha: '2025-09-12'},
        {id: 8},
      ];
      const ids = m => _demosDelMes(lista, m).map(d => d.id).join(',');
      assert(ids('2026-09') === '2,3,4,5', 'septiembre: ' + ids('2026-09'));
      assert(ids('2026-08') === '1', 'el 31 de agosto a ultima hora es agosto');
      assert(ids('2026-10') === '6', 'el 1 de octubre es octubre');
      assert(ids('2025-09') === '7', 'mismo mes de otro año no se mezcla');
      assert(_demosMesMasViejo(lista) === '2025-09', 'la sin fecha no cuenta para el limite');

      _demos = lista.map(d => Object.assign({cliente_nombre: 'Cliente ' + d.id, numero: 1, client_id: d.id}, d));
      _demosMesVista = '2026-09'; renderDemos();
      assert(pos('Cliente 2<') !== -1 && pos('Cliente 5<') !== -1, 'los bordes de adentro se ven');
      assert(pos('Cliente 1<') === -1 && pos('Cliente 6<') === -1, 'los de afuera no');
      assert(pos('4 demos') !== -1, 'el resumen cuenta solo el mes');
    """, tmp_path)


@node
def test_navegar_meses_sin_ir_al_futuro_ni_antes_de_la_mas_vieja(tmp_path):
    _correr("""
      assert(_demosMesSumar('2026-12', 1) === '2027-01', 'cambio de año hacia adelante');
      assert(_demosMesSumar('2026-01', -1) === '2025-12', 'cambio de año hacia atras');
      assert(_demosMesMover('2026-09', 1, '2026-03', '2026-09') === '2026-09', 'no pasa al futuro');
      assert(_demosMesMover('2026-09', -1, '2026-03', '2026-09') === '2026-08', 'va para atras');
      assert(_demosMesMover('2026-03', -1, '2026-03', '2026-09') === '2026-03', 'frena en la mas vieja');
      assert(_demosMesMover('2026-01', -1, '2025-11', '2026-09') === '2025-12', 'cruza el año');
      assert(_demosMesMover('2026-09', -1, '', '2026-09') === '2026-09', 'sin demos no se mueve');

      _demosMesVista = '2026-08'; renderDemos();
      assert(els['demos-mes-label'].textContent === 'Agosto 2026', 'label');
      assert(els['demos-mes-ant'].disabled === true, 'agosto es la mas vieja: no hay flecha para atras');
      assert(els['demos-mes-sig'].disabled === false, 'agosto 2026 no es el futuro');
    """, tmp_path)


@node
def test_un_mes_sin_demos_lo_dice_sin_romper(tmp_path):
    _correr("""
      _demosMesVista = '2026-07'; _demosFiltroEstado = 'venta'; _demosFiltroPresu = 'con'; renderDemos();
      assert(pos('No hay demos en Julio 2026') !== -1, body.innerHTML);
      assert(els['demos-mes-label'].textContent === 'Julio 2026', 'el navegador sigue andando');
    """, tmp_path)
