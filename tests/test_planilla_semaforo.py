# -*- coding: utf-8 -*-
"""El sync de la planilla de semaforo.

Los CEO marcan el resultado de cada llamada pintando la fila de la planilla.
Hasta el 27-8-2026 el CRM no se enteraba: decia "Sin contactar" sobre 216 leads
ya contactados, y la secuencia de mails le escribia a los mas frios mientras se
callaba con los que ya habian visto un presupuesto.
"""
import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services.planilla_semaforo import (
    aplicar,
    estado_de_color,
    normalizar_color,
    normalizar_telefono,
)

AMARILLO = "#ffff00"      # interesado
ROJO = "#ff0000"          # llamar_despues
CELESTE = "#00ffff"       # demo_1 (demo realizada)
VIOLETA = "#ff00ff"       # presupuesto_enviado
VERDE_OSC = "#274e13"     # cerrado
BLANCO = "#ffffff"        # sin pintar


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


def _lead(db, bid, phone, estado="sin_contactar", source="meta", email=None):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO businesses (id, name, phone, email, crm_status, source) "
        "VALUES (?,?,?,?,?,?)",
        (bid, f"Negocio {bid}", phone, email, estado, source),
    )
    conn.commit()
    conn.close()


def _estado(db, bid):
    conn = sqlite3.connect(db)
    v = conn.execute("SELECT crm_status FROM businesses WHERE id=?", (bid,)).fetchone()[0]
    conn.close()
    return v


# ── Lectura del color ────────────────────────────────────────────────────────

def test_los_tres_formatos_de_color_son_el_mismo():
    """Apps Script manda '#rrggbb' y openpyxl 'AARRGGBB'. Entran por la misma puerta."""
    assert normalizar_color("#FFFF00") == "ffff00"
    assert normalizar_color("FFFFFF00") == "ffff00"
    assert normalizar_color("ffff00") == "ffff00"


@pytest.mark.parametrize("color,estado", [
    ("#000000", "no_interesa"),
    (ROJO, "llamar_despues"),
    (AMARILLO, "interesado"),
    ("#00ff00", "demo_agendada"),
    (CELESTE, "demo_1"),
    (VIOLETA, "presupuesto_enviado"),
    ("#274e13", "cerrado"),
    ("#38761d", "cerrado"),
])
def test_la_leyenda_del_semaforo(color, estado):
    assert estado_de_color(color) == estado


def test_el_blanco_no_es_un_estado():
    """Una celda sin pintar vuelve como blanco: significa que nadie la marco."""
    assert estado_de_color(BLANCO) == ""
    assert estado_de_color("") == ""
    assert estado_de_color("#123456") == "", "un color fuera de la leyenda no inventa estado"


def test_el_telefono_se_normaliza():
    assert normalizar_telefono("+598 99 913 326") == "59899913326"
    assert normalizar_telefono("099913326") == "99913326"


# ── Las reglas que no se negocian ────────────────────────────────────────────

def test_lleva_el_color_al_estado(db):
    _lead(db, 1, "+59899913326")
    r = aplicar(db, [{"tel": "59899913326", "color": AMARILLO}])
    assert _estado(db, 1) == "interesado"
    assert r["actualizados"] == 1


def test_no_toca_leads_de_otras_cohortes(db):
    """La planilla es de Meta. No puede mover un lead de discovery ni del padron."""
    _lead(db, 1, "+59899913326", source="discovery")
    _lead(db, 2, "+59899913327", source=None)
    r = aplicar(db, [{"tel": "59899913326", "color": VIOLETA},
                     {"tel": "59899913327", "color": VIOLETA}])
    assert _estado(db, 1) == "sin_contactar"
    assert _estado(db, 2) == "sin_contactar"
    assert r["sin_match"] == 2


def test_nunca_retrocede(db):
    """La planilla la mantiene gente a mano y va atrasada respecto del trabajo real."""
    _lead(db, 1, "+59899913326", estado="finalizado")
    r = aplicar(db, [{"tel": "59899913326", "color": VERDE_OSC}])
    assert _estado(db, 1) == "finalizado"
    assert r["no_retrocede"] == 1 and r["actualizados"] == 0


def test_es_idempotente(db):
    _lead(db, 1, "+59899913326")
    filas = [{"tel": "59899913326", "color": CELESTE}]
    assert aplicar(db, filas)["actualizados"] == 1
    segunda = aplicar(db, filas)
    assert segunda["actualizados"] == 0 and segunda["sin_cambio"] == 1
    assert _estado(db, 1) == "demo_1"


def test_el_mismo_telefono_en_varios_meses_gana_el_mas_avanzado(db):
    """El orden de las filas no puede decidir el resultado."""
    _lead(db, 1, "+59899913326")
    aplicar(db, [{"tel": "59899913326", "color": VIOLETA},
                 {"tel": "59899913326", "color": ROJO}])
    assert _estado(db, 1) == "presupuesto_enviado"

    _lead(db, 2, "+59899913399")
    aplicar(db, [{"tel": "59899913399", "color": ROJO},
                 {"tel": "59899913399", "color": VIOLETA}])
    assert _estado(db, 2) == "presupuesto_enviado", "al reves da lo mismo"


def test_casa_por_mail_cuando_el_telefono_no_coincide(db):
    _lead(db, 1, "+59899913326", email="lead@ejemplo.com")
    aplicar(db, [{"tel": "0000", "mail": "LEAD@Ejemplo.com", "color": AMARILLO}])
    assert _estado(db, 1) == "interesado"


def test_casa_por_los_ultimos_ocho_digitos(db):
    """El mismo numero escrito con y sin codigo de pais."""
    _lead(db, 1, "+59899913326")
    aplicar(db, [{"tel": "99913326", "color": AMARILLO}])
    assert _estado(db, 1) == "interesado"


def test_el_dry_run_no_escribe(db):
    _lead(db, 1, "+59899913326")
    r = aplicar(db, [{"tel": "59899913326", "color": VIOLETA}], dry_run=True)
    assert r["actualizados"] == 1 and r["cambios"][0]["a"] == "presupuesto_enviado"
    assert _estado(db, 1) == "sin_contactar", "en dry no se toca la base"


def test_deja_rastro_en_lead_events(db):
    _lead(db, 1, "+59899913326")
    aplicar(db, [{"tel": "59899913326", "color": AMARILLO}])
    conn = sqlite3.connect(db)
    fila = conn.execute(
        "SELECT note, created_by FROM lead_events WHERE lead_id = 1"
    ).fetchone()
    conn.close()
    assert fila == ("sync planilla semaforo", "sistema (planilla)")


def test_una_fila_que_no_casa_no_rompe_la_corrida(db):
    _lead(db, 1, "+59899913326")
    r = aplicar(db, [{"tel": "99999999999", "color": AMARILLO},
                     {"tel": "59899913326", "color": AMARILLO},
                     {"tel": "", "mail": "", "color": BLANCO}])
    assert r["actualizados"] == 1 and r["sin_match"] == 1 and r["color_ignorado"] == 1
    assert _estado(db, 1) == "interesado"


# ── El endpoint ──────────────────────────────────────────────────────────────

@pytest.fixture
def cliente(db, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-test")
    monkeypatch.setenv("DB_PATH", db)
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    return app.test_client()


AUTH = {"x-admin-token": "token-de-test"}


def test_sin_token_no_entra(cliente):
    r = cliente.post("/api/meta/sync-planilla", json={"filas": []})
    assert r.status_code == 401


def test_el_endpoint_aplica(cliente, db):
    _lead(db, 1, "+59899913326")
    r = cliente.post("/api/meta/sync-planilla", headers=AUTH,
                     json={"filas": [{"tel": "59899913326", "color": VIOLETA}]})
    assert r.status_code == 200 and r.get_json()["actualizados"] == 1
    assert _estado(db, 1) == "presupuesto_enviado"


def test_el_endpoint_respeta_el_dry(cliente, db):
    _lead(db, 1, "+59899913326")
    r = cliente.post("/api/meta/sync-planilla?dry=1", headers=AUTH,
                     json={"filas": [{"tel": "59899913326", "color": VIOLETA}]})
    assert r.get_json()["dry"] is True
    assert _estado(db, 1) == "sin_contactar"


def test_el_endpoint_rechaza_un_cuerpo_mal_armado(cliente):
    assert cliente.post("/api/meta/sync-planilla", headers=AUTH,
                        json={"filas": "no soy una lista"}).status_code == 400
    assert cliente.post("/api/meta/sync-planilla", headers=AUTH,
                        json={"filas": [{}] * 5001}).status_code == 400


def test_el_telefono_guardado_como_numero_no_gana_un_cero():
    """Google guarda un telefono sin + como numero: sale "59895720157.0".

    Sacar los no-digitos a lo bruto lo convertia en "598957201570" y el lead
    dejaba de parear sin que nada lo avisara. Paso con dos filas reales.
    """
    assert normalizar_telefono("59895720157.0") == "59895720157"
    assert normalizar_telefono("5493582459263.0") == "5493582459263"
    # Y lo que ya andaba tiene que seguir andando.
    assert normalizar_telefono("+598 99 913 326") == "59899913326"
    assert normalizar_telefono("099913326") == "99913326"


def test_el_lead_con_telefono_numerico_ahora_casa(db):
    _lead(db, 1, "+59895720157")
    aplicar(db, [{"tel": "59895720157.0", "color": VIOLETA}])
    assert _estado(db, 1) == "presupuesto_enviado"


# ── La planilla tiene que poder decir "este murió" ───────────────────────────
# 'no_interesa' es rango 1, asi que la regla de no retroceder lo dejaba
# aplicable solo sobre 'sin_contactar'. Un lead que avanzo y despues dijo que no
# se quedaba en el pipeline y siguiendo en secuencia.

NEGRO = "#000000"


@pytest.mark.parametrize("desde", ["interesado", "llamar_despues", "reunion_hecha",
                                   "presupuesto_enviado", "negociacion"])
def test_el_negro_de_la_planilla_siempre_gana(db, desde):
    _lead(db, 1, "+59899913326", estado=desde)
    r = aplicar(db, [{"tel": "59899913326", "color": NEGRO}])
    assert _estado(db, 1) == "no_interesa", f"desde {desde}"
    assert r["actualizados"] == 1


@pytest.mark.parametrize("cliente", ["cliente_cerrado", "en_desarrollo", "finalizado"])
def test_pero_no_puede_borrar_un_cliente(db, cliente):
    """Ahi hay plata y una celda mal pintada no puede llevarsela."""
    _lead(db, 1, "+59899913326", estado=cliente)
    r = aplicar(db, [{"tel": "59899913326", "color": NEGRO}])
    assert _estado(db, 1) == cliente
    assert r["no_retrocede"] == 1


def test_el_resto_de_los_colores_sigue_sin_retroceder(db):
    _lead(db, 1, "+59899913326", estado="presupuesto_enviado")
    aplicar(db, [{"tel": "59899913326", "color": AMARILLO}])
    assert _estado(db, 1) == "presupuesto_enviado", "amarillo no puede bajar un ppto"
