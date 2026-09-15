"""Cuándo llegan los leads, semana por semana (pedido de Juan, 14/9).

Juan, sobre el mapa de franjas de tres horas: "esto vamos a ir viendo más
detallado por semana". El bloque pasa a una semana a la vez, lunes a domingo en
hora de Montevideo, con una fila por día y una columna por hora.

**Los tests de recorte miran desde una ventana que NO contiene todos los
datos**, con leads justo en los dos bordes: el domingo 23:59 y el lunes 00:00
de Montevideo, que en UTC son el lunes 02:59 y el lunes 03:00. Un recorte que
mirara la fecha UTC en vez de la local pasaría con datos del medio de la semana
y fallaría exactamente ahí.
"""

import sqlite3
from datetime import date

import pytest

import dashboard
import services.dossier as dossier
from dashboard import create_app
from database import _connect, init_db
from services.dossier import leads_de_la_semana

_AUTH = {"x-admin-token": "token-de-prueba"}

# 2026-09-07 es lunes. La semana local va del lunes 7 al domingo 13.
LUNES = "2026-09-07"
HOY = "2026-09-14"


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _lead(db, cuando, source="meta"):
    conn = _connect(db)
    try:
        conn.execute("INSERT INTO businesses (name, phone, source, scraped_at) "
                     "VALUES (?,?,?,?)", (f"{cuando}-{source}", cuando, source, cuando))
        conn.commit()
    finally:
        conn.close()


def _cargar_bordes(db):
    _lead(db, "2026-08-17 01:00:00")        # domingo 16/8 22:00 local: el primero
    _lead(db, "2026-09-07 02:59:00")        # domingo 6/9 23:59 local: semana ANTERIOR
    _lead(db, "2026-09-07 03:00:00")        # lunes 7/9 00:00 local: ESTA semana
    _lead(db, "2026-09-10T14:30:00+0000")   # jueves 10/9 11:30 local, formato Meta
    _lead(db, "2026-09-14 02:59:00")        # domingo 13/9 23:59 local: ESTA semana
    _lead(db, "2026-09-14 03:00:00")        # lunes 14/9 00:00 local: semana SIGUIENTE
    _lead(db, "2026-09-09")                 # sin hora
    _lead(db, "2026-09-10 15:00:00", source="discovery")   # no es de Meta


def test_la_semana_recorta_en_hora_de_montevideo_con_los_bordes(db):
    _cargar_bordes(db)
    s = leads_de_la_semana(db, LUNES, hoy=HOY)
    horas = [d["horas"] for d in s["dias"]]
    assert horas[0][0] == 1, "el lunes 00:00 local (03:00 UTC) es de esta semana"
    assert horas[6][23] == 1, "el domingo 23:59 local (lunes 02:59 UTC) es de esta semana"
    assert horas[3][11] == 1, "14:30 UTC del jueves son las 11 en Montevideo"
    assert s["total"] == 3
    assert s["sin_hora"] == 1
    assert [d["total"] for d in s["dias"]] == [1, 0, 0, 1, 0, 0, 1]


def test_la_semana_anterior_se_queda_con_el_domingo_a_la_noche(db):
    _cargar_bordes(db)
    s = leads_de_la_semana(db, "2026-08-31", hoy=HOY)
    assert s["total"] == 1
    assert s["dias"][6]["horas"][23] == 1
    assert s["dias"][6]["fecha"] == "2026-09-06"


def test_la_semana_siguiente_arranca_el_lunes_a_las_cero(db):
    _cargar_bordes(db)
    s = leads_de_la_semana(db, "2026-09-14", hoy=HOY)
    assert s["total"] == 1
    assert s["dias"][0]["horas"][0] == 1


def test_la_grilla_es_de_siete_por_veinticuatro_con_sus_fechas(db):
    s = leads_de_la_semana(db, LUNES, hoy=HOY)
    assert len(s["dias"]) == 7
    assert all(len(d["horas"]) == 24 for d in s["dias"])
    assert [d["fecha"] for d in s["dias"]] == [
        "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10",
        "2026-09-11", "2026-09-12", "2026-09-13"]
    assert s["semana"] == LUNES and s["hasta"] == "2026-09-13"


def test_la_primera_semana_es_la_del_primer_lead_en_hora_local(db):
    """01:00 UTC del lunes 17/8 es el domingo 16/8 a la noche: su semana es la
    del 10/8, no la del 17."""
    _cargar_bordes(db)
    _lead(db, "basura")
    _lead(db, "")
    assert leads_de_la_semana(db, LUNES, hoy=HOY)["primera_semana"] == "2026-08-10"


def test_la_semana_actual_es_la_de_hoy_en_montevideo(db):
    assert leads_de_la_semana(db, LUNES, hoy="2026-09-14")["semana_actual"] == "2026-09-14"
    assert leads_de_la_semana(db, LUNES, hoy="2026-09-13")["semana_actual"] == "2026-09-07"


def test_de_utc_a_montevideo_cruza_la_medianoche():
    assert dossier._a_montevideo(date(2026, 9, 14), 2) == (date(2026, 9, 13), 23)
    assert dossier._a_montevideo(date(2026, 9, 14), 3) == (date(2026, 9, 14), 0)
    assert dossier.HORAS_UTC_A_MONTEVIDEO == -3


def test_sin_leads_no_hay_maximo_ni_primera_semana(db):
    s = leads_de_la_semana(db, LUNES, hoy=HOY)
    assert s["total"] == 0 and s["maximo"] is None and s["primera_semana"] is None


def test_trae_el_maximo_de_la_semana(db):
    _lead(db, "2026-09-08 13:05:00")
    _lead(db, "2026-09-08 13:55:00")
    _lead(db, "2026-09-09 13:00:00")
    _lead(db, "2026-08-20 13:00:00")        # otra semana: no cuenta para el maximo
    _lead(db, "2026-08-20 13:10:00")
    _lead(db, "2026-08-20 13:20:00")
    s = leads_de_la_semana(db, LUNES, hoy=HOY)
    assert s["maximo"] == 2
    assert s["dias"][1]["horas"][10] == 2


def test_un_dia_que_no_es_lunes_no_se_acepta(db):
    with pytest.raises(ValueError):
        leads_de_la_semana(db, "2026-09-08", hoy=HOY)


def test_no_lee_filas_completas(db, monkeypatch):
    """Agrupa en SQLite: de `businesses` solo se leen `source` y `scraped_at`."""
    _cargar_bordes(db)
    leidas = set()
    original = dossier._connect

    def conectar(ruta):
        conn = original(ruta)

        def autorizar(accion, arg1, arg2, *_):
            if accion == sqlite3.SQLITE_READ and arg1 == "businesses":
                leidas.add(arg2)
            return sqlite3.SQLITE_OK
        conn.set_authorizer(autorizar)
        return conn

    monkeypatch.setattr(dossier, "_connect", conectar)
    assert leads_de_la_semana(db, LUNES, hoy=HOY)["total"] == 3
    assert leidas and leidas <= {"source", "scraped_at"}, leidas


def test_el_mapa_de_franjas_del_dossier_sigue_igual(db):
    """El informe usa `llegada_de_leads`: el cambio es solo la vista."""
    _cargar_bordes(db)
    mapa = dossier.llegada_de_leads(db, "2026-09-01", "2026-09-30")
    assert len(mapa["celdas"]) == 7 * 8
    assert mapa["horas_por_franja"] == 3


# ── La ruta ──────────────────────────────────────────────────────────────

@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-prueba")
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    app = create_app(ruta)
    app.config["TESTING"] = True
    return ruta, app.test_client()


def test_la_ruta_devuelve_la_semana_pedida(cliente):
    ruta, c = cliente
    _cargar_bordes(ruta)
    r = c.get(f"/api/marketing/leads-semana?semana={LUNES}", headers=_AUTH)
    assert r.status_code == 200
    datos = r.get_json()
    assert datos["semana"] == LUNES and datos["total"] == 3
    assert datos["primera_semana"] == "2026-08-10"


def test_la_ruta_sin_semana_usa_la_de_hoy_en_montevideo(cliente, monkeypatch):
    _, c = cliente
    monkeypatch.setattr(dossier, "_hoy_en_montevideo", lambda: date(2026, 9, 13))
    datos = c.get("/api/marketing/leads-semana", headers=_AUTH).get_json()
    assert datos["semana"] == "2026-09-07"
    assert datos["semana_actual"] == "2026-09-07"


@pytest.mark.parametrize("semana", ["2026-09-08", "2026-9-7", "hola", "2026-02-30"])
def test_una_semana_invalida_da_400(cliente, semana):
    _, c = cliente
    r = c.get(f"/api/marketing/leads-semana?semana={semana}", headers=_AUTH)
    assert r.status_code == 400


def test_la_semana_no_se_ve_sin_permiso(cliente):
    _, c = cliente
    assert c.get(f"/api/marketing/leads-semana?semana={LUNES}").status_code in (302, 401, 403)


# ── El bloque en la página ───────────────────────────────────────────────

def test_el_bloque_tiene_su_navegador_de_semana():
    html = dashboard.DASHBOARD_HTML
    for ident in ("mk-llegada", "mk-llegada-semana", "mk-llegada-ant", "mk-llegada-sig"):
        assert f'id="{ident}"' in html, ident
    assert "mkLlegadaSemana(-1)" in html and "mkLlegadaSemana(1)" in html
    assert "mkLlegadaSemanaHoy()" in html and ">Esta semana<" in html
    assert "/api/marketing/leads-semana" in html


def test_el_subtitulo_del_bloque_hace_wrap():
    """Juan vio el subtítulo cortado a la derecha. La regla no tiene nowrap ni
    ancho fijo; esto la deja explícita para que nadie se lo agregue."""
    import re
    reglas = re.findall(r"^\.sc-bloque>\.sc-sub\{([^}]*)\}", dashboard.DASHBOARD_HTML, re.M)
    assert reglas
    for cuerpo in reglas:
        assert "nowrap" not in cuerpo and not re.search(r"(^|;)\s*width:", cuerpo)
        assert "white-space:normal" in cuerpo and "overflow-wrap:anywhere" in cuerpo
    # Y la tabla de 24 columnas scrollea adentro de su tarjeta, no la página.
    assert re.search(r"^\.sc-lleg-wrap\{[^}]*overflow-x:auto", dashboard.DASHBOARD_HTML, re.M)


def test_el_numero_de_cada_casillero_se_lee_en_los_dos_temas():
    """El relleno va de 14% a 70% de --azul sobre la tarjeta, y el número va en
    --texto encima. Arriba de 70% el oscuro baja de 4,5:1."""
    from tests.test_marketing_contraste import PISO_TEXTO, TEMAS, contraste

    assert "Math.round(14 + 56 *" in dashboard.DASHBOARD_HTML

    def hexa(h):
        h = h.lstrip("#")
        return "".join(c * 2 for c in h) if len(h) == 3 else h

    for tema, t in TEMAS.items():
        azul, fondo = hexa(t["--azul"]), hexa(t["--superficie"])
        for pct in (14, 70):
            mezcla = "#" + "".join(
                "%02x" % round(int(azul[i:i + 2], 16) * pct / 100
                               + int(fondo[i:i + 2], 16) * (1 - pct / 100))
                for i in (0, 2, 4))
            c = contraste(t["--texto"], mezcla)
            assert c >= PISO_TEXTO, f"{tema} al {pct}%: {c:.2f}:1"
