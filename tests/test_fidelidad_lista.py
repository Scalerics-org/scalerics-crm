"""La lista única del Outbound de Fidelidad (pedido de Juan, 25/9).

Lo que se pidió, cada cosa con su test:
- una sola lista: vencidas primero, después las de hoy, después el resto
  ordenado por qué tan target es cada comercio (`armar_lista`)
- el más target es la pizzería o hamburguesería de barrio tipo Trouville; en
  peluquerías, la barbería (`target`)
- filtros de ciudad (Montevideo / Buenos Aires) y rubro (restaurantes /
  peluquerías), y un buscador
- cinco botones que registran la llamada al tocarlos; No atendió, Otro día y
  Reunión llevan fecha y nota rápida (`registrar_accion`, `editar_llamada`)
- No interesa lo manda a un año
- el botón equivocado se corrige sin dejar rastro (`deshacer_llamada`)
- dueño y celular del dueño en la fila

Hoy está fijo en el martes 23/9/2026, 10:30 de Montevideo.
"""

import sqlite3
from datetime import datetime

import pytest

import dashboard
import routes.fidelidad as rutas
from database import create_user, init_db
from services import fidelidad as fid
from werkzeug.security import generate_password_hash

AHORA = datetime(2026, 9, 23, 10, 30)          # martes


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "lista.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def cli(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(rutas, "_ahora", lambda: AHORA)
    app = dashboard.create_app(db)
    uid = create_user(db, name="jefe", email="jefe@scalerics.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Lucas"
    return c


_TEL = iter(range(100, 999))


def _p(db, nombre, **extra):
    # Cada uno con su celular: con el mismo teléfono serían el mismo comercio.
    datos = {"nombre": nombre, "zona": "Municipio CH", "barrio": "Pocitos", "tipo": "Pizzería",
             "telefono": f"099 123 {next(_TEL)}", "rating": 4.4, "resenas": 400}
    datos.update(extra)
    pid, que = fid.crear_prospecto(db, datos)
    assert que == "creado", que
    return pid


def _exec(db, sql, params=()):
    c = sqlite3.connect(db)
    try:
        c.execute(sql, params)
        c.commit()
    finally:
        c.close()


def _ids(d):
    return [p["id"] for p in d["items"]]


# ── qué tan target es ────────────────────────────────────────────────────────

def test_la_pizzeria_de_barrio_le_gana_a_la_alta_cocina_y_a_la_parrilla_turistica():
    pizzeria = fid.target({"nombre": "Pizzería La Esquina", "tipo": "Pizzería", "telefono": "099123456",
                           "resenas": 400, "rating": 4.4, "barrio": "Pocitos"})
    parrilla = fid.target({"nombre": "La Perdiz", "tipo": "Parrilla", "telefono": "27118963",
                           "resenas": 5489, "rating": 4.5, "barrio": "Punta Carretas"})
    gourmet = fid.target({"nombre": "Bistró del Puerto", "tipo": "Restaurante de alta cocina",
                          "telefono": "29151234", "resenas": 900, "rating": 4.7, "barrio": "Ciudad Vieja"})
    assert pizzeria >= 80
    assert pizzeria > parrilla > gourmet


def test_hamburgueseria_arriba_y_cadenas_en_cero():
    assert fid.target({"nombre": "Smash Bros", "tipo": "Hamburguesería", "telefono": "098555210",
                       "resenas": 300, "rating": 4.5, "barrio": "Cordón"}) >= 80
    for cadena in ("McDonald's Pocitos", "La Pasiva", "Burger King", "Cerini Belgrano"):
        assert fid.target({"nombre": cadena, "tipo": "Hamburguesería", "telefono": "099123456"}) == 0, cadena


def test_en_peluquerias_la_barberia_va_primero():
    barberia = fid.target({"nombre": "Barbería Don Pepe", "tipo": "Barbería", "rubro": "peluqueria",
                           "telefono": "098111222", "resenas": 120, "rating": 4.8, "barrio": "Cordón"})
    spa = fid.target({"nombre": "Relax", "tipo": "Spa", "rubro": "peluqueria", "telefono": "27081234",
                      "resenas": 120, "rating": 4.8, "barrio": "Cordón"})
    assert barberia > spa


def test_sin_telefono_y_nombre_repetido_bajan():
    base = {"nombre": "Pizza Nápoles", "tipo": "Pizzería", "telefono": "099123456", "resenas": 400, "rating": 4.4}
    assert fid.target(dict(base, telefono=None)) < fid.target(base) - 30
    assert fid.target(base, repetidos={"pizza napoles"}) < fid.target(base) - 30


def test_lo_avanzado_sube_y_los_no_atendio_bajan():
    base = {"nombre": "Pizza Nápoles", "tipo": "Pizzería", "telefono": "27081234", "resenas": 400, "rating": 4.0}
    assert fid.target(dict(base, estado="reunion_agendada")) > fid.target(base)
    assert fid.target(dict(base, n_no_atendio=3)) < fid.target(base)


def test_celulares_de_uruguay_y_argentina():
    assert fid.es_celular("099 123 456") and fid.es_celular("+598 98 555 210")
    assert fid.es_celular("+54 9 11 5555 1234") and fid.es_celular("11 15 5555 1234")
    assert not fid.es_celular("2708 1234") and not fid.es_celular("+54 11 4555 1234")


# ── ciudad y rubro ───────────────────────────────────────────────────────────

def test_rubro_sale_del_tipo_y_buenos_aires_entra_con_su_barrio(db):
    a = _p(db, "Barbería Don Pepe", tipo="Barbería")
    b, que = fid.crear_prospecto(db, {"nombre": "Pizzería de Almagro", "ciudad": "Buenos Aires",
                                      "barrio": "Almagro", "tipo": "Pizzería"})
    assert que == "creado"
    pa, pb = fid.get_prospecto(db, a), fid.get_prospecto(db, b)
    assert pa["rubro"] == "peluqueria" and pa["ciudad"] == "Montevideo"
    assert pb["ciudad"] == "Buenos Aires" and pb["zona"] == "Almagro" and pb["rubro"] == "restaurante"
    # En Montevideo sigue valiendo el territorio del socio.
    _, que = fid.crear_prospecto(db, {"nombre": "Pizzería del Cerro", "barrio": "Cerro"})
    assert que == "fuera_de_zona"


def test_los_prospectos_de_antes_quedan_en_montevideo_y_con_rubro(db):
    pid = _p(db, "Barbería Vieja", tipo="Barbería")
    _exec(db, "UPDATE fid_prospectos SET ciudad = NULL, rubro = NULL WHERE id = ?", (pid,))
    init_db(db)
    p = fid.get_prospecto(db, pid)
    assert p["ciudad"] == "Montevideo" and p["rubro"] == "peluqueria"


def test_la_lista_filtra_por_ciudad_rubro_y_busqueda(db):
    pizza = _p(db, "Pizzería La Esquina")
    barberia = _p(db, "Barbería Don Pepe", tipo="Barbería")
    porteña, _ = fid.crear_prospecto(db, {"nombre": "Pizzería de Almagro", "ciudad": "Buenos Aires",
                                          "barrio": "Almagro", "tipo": "Pizzería", "telefono": "1145551234"})
    assert _ids(fid.armar_lista(db, "Montevideo", "restaurante", cuando=AHORA)) == [pizza]
    assert _ids(fid.armar_lista(db, "Montevideo", "peluqueria", cuando=AHORA)) == [barberia]
    assert _ids(fid.armar_lista(db, "Buenos Aires", "restaurante", cuando=AHORA)) == [porteña]
    fid.editar_prospecto(db, pizza, {"contacto": "Martín", "contacto_tel": "098 555 210"})
    _, lid, _ = fid.registrar_accion(db, pizza, "otro_dia", "Lucas", nota="mandarle la demo", cuando=AHORA)
    for q in ("martin", "555 210", "demo", "esquina"):
        assert _ids(fid.armar_lista(db, "Montevideo", "restaurante", q=q, cuando=AHORA)) == [pizza], q


# ── el orden ─────────────────────────────────────────────────────────────────

def test_vencidas_primero_despues_hoy_despues_el_mas_target(db):
    flojo = _p(db, "Parrilla Turística", tipo="Parrilla", resenas=6000, telefono="27081234")
    bueno = _p(db, "Pizzería La Esquina")
    hoy = _p(db, "Hamburguesería Hoy", tipo="Hamburguesería")
    vencida = _p(db, "Parrilla Vencida", tipo="Parrilla", telefono="27081235")
    reunion_hoy = _p(db, "Burger Reunión", tipo="Hamburguesería")
    cliente = _p(db, "Pizza Cliente")
    _exec(db, "UPDATE fid_prospectos SET proxima_llamada = '2026-09-22 11:00', estado = 'contactado' WHERE id = ?", (vencida,))
    _exec(db, "UPDATE fid_prospectos SET proxima_llamada = '2026-09-23 16:00', estado = 'contactado' WHERE id = ?", (hoy,))
    _exec(db, "UPDATE fid_prospectos SET fecha_reunion = '2026-09-23 09:00', estado = 'reunion_agendada' WHERE id = ?", (reunion_hoy,))
    _exec(db, "UPDATE fid_prospectos SET estado = 'cerrado' WHERE id = ?", (cliente,))
    d = fid.armar_lista(db, "Montevideo", "restaurante", cuando=AHORA)
    # La reunión de las 9 ya es hora; la llamada de las 16 va después.
    assert _ids(d) == [vencida, reunion_hoy, hoy, bueno, flojo, cliente]
    assert [p["grupo"] for p in d["items"]] == [0, 1, 1, 2, 2, 3]
    assert d["kpis"]["vencidas"] == 1 and d["kpis"]["hoy"] == 2


# ── los cinco botones ────────────────────────────────────────────────────────

def test_no_atendio_se_cuenta_al_toque_y_propone_manana(db):
    pid = _p(db, "Pizzería La Esquina")
    p, lid, err = fid.registrar_accion(db, pid, "no_atendio", "Lucas", cuando=AHORA)
    assert err is None and lid
    assert p["estado"] == "sin_contactar"  # no habló con nadie
    assert p["proxima_llamada"] == "2026-09-24 16:00"
    d = fid.armar_lista(db, "Montevideo", "restaurante", cuando=AHORA)
    assert d["kpis"]["llamadas_hoy"] == 1
    assert d["items"][0]["llamada_hoy"]["resultado"] == "no_atendio"
    # Después elige otra fecha y deja una nota.
    p, err = fid.editar_llamada(db, lid, fecha="2026-09-23T18:00", nota="sonó ocupado", cuando=AHORA)
    assert err is None and p["proxima_llamada"] == "2026-09-23 18:00"
    assert fid.armar_lista(db, "Montevideo", "restaurante", cuando=AHORA)["items"][0]["ultima_nota"] == "sonó ocupado"


def test_otro_dia_y_reunion_llevan_fecha(db):
    a, b = _p(db, "Pizzería A"), _p(db, "Pizzería B")
    p, lid, _ = fid.registrar_accion(db, a, "otro_dia", "Lucas", cuando=AHORA)
    assert p["estado"] == "contactado" and p["proxima_llamada"] == "2026-09-24 11:00"
    p, lid, _ = fid.registrar_accion(db, b, "reunion", "Lucas", cuando=AHORA)
    assert p["estado"] == "reunion_agendada" and p["fecha_reunion"] == "2026-09-24 11:00"
    p, err = fid.editar_llamada(db, lid, fecha="2026-09-29 15:30", cuando=AHORA)
    assert p["fecha_reunion"] == "2026-09-29 15:30" and p["proxima_llamada"] is None
    _, err = fid.editar_llamada(db, lid, fecha="2026-09-01 10:00", cuando=AHORA)
    assert err == "la fecha no puede quedar en el pasado"


def test_no_interesa_vuelve_en_un_ano_y_cerro_es_cliente(db):
    a, b = _p(db, "Pizzería A"), _p(db, "Pizzería B")
    p, _, _ = fid.registrar_accion(db, a, "no_interesa", "Lucas", cuando=AHORA)
    assert p["estado"] == "descartado" and p["proxima_llamada"].startswith("2027-09-23")
    p, _, _ = fid.registrar_accion(db, b, "cerro", "Lucas", cuando=AHORA)
    assert p["estado"] == "cerrado" and p["cerrado_en"] == "2026-09-23" and p["mensual_usd"] == 150
    assert p["proxima_llamada"] is None


def test_el_boton_equivocado_se_deshace_sin_rastro(db):
    pid = _p(db, "Pizzería La Esquina")
    _exec(db, "UPDATE fid_prospectos SET estado = 'contactado', proxima_llamada = '2026-09-22 11:00' WHERE id = ?", (pid,))
    _, lid, _ = fid.registrar_accion(db, pid, "cerro", "Lucas", cuando=AHORA)
    p, err = fid.deshacer_llamada(db, lid)
    assert err is None
    assert p["estado"] == "contactado" and p["proxima_llamada"] == "2026-09-22 11:00"
    assert p["cerrado_en"] is None and p["mensual_usd"] is None
    assert p["llamadas"] == [] and p["cambios"] == []


def test_solo_se_deshace_la_ultima(db):
    pid = _p(db, "Pizzería La Esquina")
    _, primera, _ = fid.registrar_accion(db, pid, "no_atendio", "Lucas", cuando=AHORA)
    fid.registrar_accion(db, pid, "otro_dia", "Lucas", cuando=AHORA)
    _, err = fid.deshacer_llamada(db, primera)
    assert err == "solo se puede deshacer la última llamada"


# ── la API ───────────────────────────────────────────────────────────────────

def test_la_api_de_punta_a_punta(db, cli):
    pid = _p(db, "Pizzería La Esquina")
    r = cli.post(f"/api/fidelidad/prospectos/{pid}/accion", json={"accion": "no_atendio"})
    assert r.status_code == 201
    lid = r.get_json()["llamada_id"]
    r = cli.put(f"/api/fidelidad/llamadas/{lid}", json={"fecha": "2026-09-25T11:00", "nota": "probar a la tarde"})
    assert r.status_code == 200 and r.get_json()["prospecto"]["proxima_llamada"] == "2026-09-25 11:00"
    d = cli.get("/api/fidelidad/lista?ciudad=Montevideo&rubro=restaurante").get_json()
    assert d["items"][0]["ultima_nota"] == "probar a la tarde" and d["kpis"]["llamadas_hoy"] == 1
    assert cli.delete(f"/api/fidelidad/llamadas/{lid}").status_code == 200
    assert cli.get("/api/fidelidad/lista").get_json()["kpis"]["llamadas_hoy"] == 0
    assert cli.post(f"/api/fidelidad/prospectos/{pid}/accion", json={"accion": "x"}).status_code == 400
    assert cli.post("/api/fidelidad/prospectos/999/accion", json={"accion": "cerro"}).status_code == 404
    r = cli.put(f"/api/fidelidad/prospectos/{pid}", json={"contacto": "Martín", "contacto_tel": "098555210"})
    assert r.get_json()["prospecto"]["contacto_tel"] == "098555210"


def test_la_pantalla_es_una_sola_lista():
    html = dashboard.DASHBOARD_HTML
    for id_ in ("fid-lista", "fid-q", "fid-seg-ciudad", "fid-seg-rubro", "fid-kpis-lista"):
        assert f'id="{id_}"' in html, id_
    for viejo in ('id="fid-t-hoy"', 'id="fid-t-pipe"', 'id="fid-t-todos"', 'id="fid-t-reu"', 'id="fid-drawer"'):
        assert viejo not in html, viejo
    assert ">Restaurantes</button>" in html and ">Peluquerías</button>" in html


# ── el calendario general ────────────────────────────────────────────────────

def test_lo_que_se_agenda_en_fidelidad_se_ve_en_el_calendario_general(db, cli):
    pid = _p(db, "Pizzería La Esquina")
    _, lid, _ = fid.registrar_accion(db, pid, "reunion", "Lucas", cuando=AHORA)
    fid.editar_llamada(db, lid, fecha="2026-09-29 15:30", cuando=AHORA)
    fid.crear_evento(db, {"titulo": "Visita a Pocitos", "inicio": "2026-09-30T10:00", "minutos": 60}, "Lucas")
    otra = _p(db, "Pizzería B")
    fid.registrar_accion(db, otra, "otro_dia", "Lucas", cuando=AHORA)   # llamada: no va
    evs = cli.get("/api/calendar/events?start=2026-09-01&end=2026-10-31").get_json()["events"]
    de_fid = [e for e in evs if e["origen"] == "fidelidad"]
    assert [(e["date"], e["time"], e["title"]) for e in de_fid] == [
        ("2026-09-29", "15:30", "Fidelidad · Reunión · Pizzería La Esquina"),
        ("2026-09-30", "10:00", "Fidelidad · Visita a Pocitos")]
    assert de_fid[0]["prospecto_id"] == pid and de_fid[0]["duration_min"] == fid.REUNION_MINUTOS
    # Se lee en vivo: si la reunión se deshace en Fidelidad, desaparece de allá.
    fid.deshacer_llamada(db, lid)
    evs = cli.get("/api/calendar/events?start=2026-09-01&end=2026-10-31").get_json()["events"]
    assert [e["title"] for e in evs if e["origen"] == "fidelidad"] == ["Fidelidad · Visita a Pocitos"]


def test_el_calendario_general_no_deja_mover_lo_de_fidelidad():
    html = dashboard.DASHBOARD_HTML
    assert "if (origen === 'calendly' || origen === 'fidelidad') { e.preventDefault(); return; }" in html
    assert "function _calChipFidelidad" in html and "function _fidAgruparLlamadas" in html
