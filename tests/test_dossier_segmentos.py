"""Los segmentos que el lead declara en el formulario de Meta.

Es la mejor variable de segmentacion que hay y ningun reporte la usa. La trampa
real, medida contra produccion, no es el encoding —los datos estan bien— sino
que hay dos versiones del formulario con preguntas distintas conviviendo.
"""

import json

import pytest

from database import _connect, init_db
from services.dossier import claves_no_mapeadas, normalizar_clave, por_segmento


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _lead(db, nombre, form, eventos=(), entro="2026-03-05"):
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO businesses (name, phone, source, scraped_at, form_data) "
            "VALUES (?,?,?,?,?)",
            (nombre, nombre, "meta", entro, json.dumps(form)))
        lead_id = cur.lastrowid
        for estado in eventos:
            conn.execute("INSERT INTO lead_events (lead_id, new_status) VALUES (?,?)",
                         (lead_id, estado))
        conn.commit()
    finally:
        conn.close()


def _bloques(db):
    return {b["pregunta"]: b for b in por_segmento(db, "2026-03-01", "2026-03-31")}


def test_normalizar_saca_acentos_signos_y_mayusculas():
    assert (normalizar_clave("¿Qué_es_lo_que_buscás_para_tu_negocio?")
            == "que_es_lo_que_buscas_para_tu_negocio")


def test_normalizar_colapsa_lo_que_no_es_alfanumerico():
    assert normalizar_clave("perfil_de_instagram/sitio (si corresponde)") == \
        "perfil_de_instagram_sitio_si_corresponde"


def test_normalizar_aguanta_none_y_vacio():
    assert normalizar_clave(None) == ""
    assert normalizar_clave("") == ""


def test_agrupa_por_lo_que_busca(db):
    _lead(db, "a", {"¿qué_es_lo_que_buscás_para_tu_negocio?": "automatizaciones"})
    _lead(db, "b", {"¿qué_es_lo_que_buscás_para_tu_negocio?": "automatizaciones"})
    _lead(db, "c", {"¿qué_es_lo_que_buscás_para_tu_negocio?": "crear_mi_ecommerce"})

    valores = {v["valor_declarado"]: v for v in _bloques(db)["que_busca"]["valores"]}
    assert valores["automatizaciones"]["n"] == 2
    assert valores["crear_mi_ecommerce"]["n"] == 1


def test_la_tasa_de_demo_por_presupuesto_declarado(db):
    """La pregunta del modulo: ¿los que declaran mas plata avanzan mas?"""
    _lead(db, "rico", {"¿contás_con_un_presupuesto_para_este_proyecto?":
                       "más_de_usd_1.000"}, eventos=("demo_1",))
    _lead(db, "duda", {"¿contás_con_un_presupuesto_para_este_proyecto?":
                       "aún_no_lo_se"})

    valores = {v["valor_declarado"]: v
               for v in _bloques(db)["presupuesto"]["valores"]}
    tasa = [m for m in valores["más_de_usd_1.000"]["metricas"]
            if m["id"].endswith(".tasa_demo")][0]
    assert tasa["valor"] == 1.0
    assert tasa["muestra_chica"] is True


def test_las_dos_versiones_del_formulario_no_se_mezclan(db):
    """191 leads contestan un juego de preguntas y 47 contestan otro. Dos
    preguntas distintas nunca van bajo la misma etiqueta."""
    _lead(db, "nuevo", {"¿cuál_es_tu_objetivo_para_este_año?": "crecer"})
    _lead(db, "viejo", {"¿cuál_es_el_objetivo_que_tenes_en_este_2026?": "crecer"})

    b = _bloques(db)
    assert b["objetivo"]["n"] == 1
    assert b["objetivo_v2"]["n"] == 1
    assert b["objetivo"]["etiqueta"] != b["objetivo_v2"]["etiqueta"]


def test_la_ciudad_tambien_es_un_segmento(db):
    _lead(db, "a", {"city": "Montevideo"})
    _lead(db, "b", {"city": "Montevideo"})
    _lead(db, "c", {"city": "Maldonado"})
    valores = {v["valor_declarado"]: v["n"] for v in _bloques(db)["ciudad"]["valores"]}
    assert valores == {"Montevideo": 2, "Maldonado": 1}


def test_los_valores_salen_ordenados_por_volumen(db):
    _lead(db, "a", {"city": "Maldonado"})
    _lead(db, "b", {"city": "Montevideo"})
    _lead(db, "c", {"city": "Montevideo"})
    assert [v["valor_declarado"] for v in _bloques(db)["ciudad"]["valores"]][0] == \
        "Montevideo"


def test_no_se_segmenta_por_datos_personales(db):
    """El nombre, el telefono y el mail no son segmentos: son PII y ademas
    tienen un valor distinto por lead."""
    _lead(db, "a", {"full_name": "Ana", "phone_number": "+59899",
                    "email": "ana@x.com", "¿cómo_se_llama_tu_negocio?": "X"})
    assert por_segmento(db, "2026-03-01", "2026-03-31") == []


def test_un_form_data_roto_no_rompe_el_dossier(db):
    conn = _connect(db)
    try:
        conn.execute("INSERT INTO businesses (name, phone, source, scraped_at, "
                     "form_data) VALUES ('x','x','meta','2026-03-05','no es json')")
        conn.commit()
    finally:
        conn.close()
    assert por_segmento(db, "2026-03-01", "2026-03-31") == []


def test_un_valor_vacio_no_cuenta(db):
    _lead(db, "a", {"city": ""})
    assert por_segmento(db, "2026-03-01", "2026-03-31") == []


def test_una_clave_desconocida_se_reporta_en_vez_de_desaparecer(db):
    """Si Meta cambia la redaccion de una pregunta, el segmento se caeria a
    cero en silencio. Tiene que quedar registro de que aparecio algo nuevo."""
    _lead(db, "a", {"¿cuantos_empleados_tenes?": "5"})
    sueltas = claves_no_mapeadas(db, "2026-03-01", "2026-03-31")
    assert "cuantos_empleados_tenes" in sueltas
    assert sueltas["cuantos_empleados_tenes"] == 1


def test_los_leads_de_prueba_de_meta_no_son_un_segmento(db):
    """La herramienta de prueba de formularios de Meta manda respuestas con el
    prefijo '<test lead:'. Hay varias en produccion y aparecian como un valor
    declarado mas, con n=1."""
    _lead(db, "real", {"city": "Montevideo"})
    _lead(db, "prueba", {"city": "<test lead: dummy data for city>"})
    valores = {v["valor_declarado"] for v in _bloques(db)["ciudad"]["valores"]}
    assert valores == {"Montevideo"}
