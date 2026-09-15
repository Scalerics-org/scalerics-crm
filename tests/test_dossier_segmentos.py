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
    assert valores == {"Montevideo": 2, "Interior": 1}


def test_los_valores_salen_ordenados_por_volumen(db):
    _lead(db, "a", {"city": "Maldonado"})
    _lead(db, "b", {"city": "Salto"})
    _lead(db, "c", {"city": "Montevideo"})
    assert [v["valor_declarado"] for v in _bloques(db)["ciudad"]["valores"]] == \
        ["Interior", "Montevideo"]


# ── Ciudad: Montevideo e Interior (pedido de Juan, 14/9) ─────────────────

_OBJETIVO_LIBRE = "¿cuál_es_el_objetivo_que_tenes_en_este_2026?"


@pytest.mark.parametrize("ciudad", ["Montevideo", "montevideo", " MONTEVIDEO ",
                                    "Ciudad de Montevideo", "Montevidéo",
                                    "Mdeo", "MVD", "Montevideo, Uruguay"])
def test_las_variantes_de_montevideo_son_montevideo(ciudad):
    from services.dossier import zona_de_ciudad
    assert zona_de_ciudad(ciudad) == "Montevideo"


@pytest.mark.parametrize("ciudad", ["Maldonado", "Canelones", "vender", "Salto",
                                    "Punta del Este", "123", "Montevide"])
def test_todo_lo_demas_es_interior_incluida_la_basura(ciudad):
    from services.dossier import zona_de_ciudad
    assert zona_de_ciudad(ciudad) == "Interior"


def test_la_ciudad_se_agrupa_y_la_tasa_se_recalcula_sobre_el_grupo(db):
    """Montevideo junta 3 leads de tres grafias, con una sola demo: 1/3. Si se
    promediaran las tasas de cada grafia (0, 0 y 1) daria 1/3 igual por
    casualidad, asi que las grafias llevan pesos distintos: "Montevideo" 2
    leads sin demo y "Mdeo" 1 con demo. Promediar daria 0,5; sumar da 0,3333."""
    _lead(db, "a", {"city": "Montevideo"})
    _lead(db, "b", {"city": "montevideo "})
    _lead(db, "c", {"city": "Mdeo"}, eventos=("demo_1",))
    _lead(db, "d", {"city": "Maldonado"}, eventos=("demo_1",))
    _lead(db, "e", {"city": "vender"})
    bloque = _bloques(db)["ciudad"]
    valores = {v["valor_declarado"]: v for v in bloque["valores"]}
    assert set(valores) == {"Montevideo", "Interior"}
    assert bloque["valores_distintos"] == 2

    def tasa(v):
        return [m for m in v["metricas"] if m["id"].endswith(".tasa_demo")][0]

    mvd, interior = tasa(valores["Montevideo"]), tasa(valores["Interior"])
    assert (mvd["numerador"], mvd["denominador"], mvd["valor"]) == (1, 3, 0.3333)
    assert (interior["numerador"], interior["denominador"], interior["valor"]) == (1, 2, 0.5)
    assert mvd["id"] == "segmento.ciudad.montevideo.tasa_demo"
    assert interior["id"] == "segmento.ciudad.interior.tasa_demo"
    assert mvd["muestra_chica"] is True


# ── Etiquetas legibles ───────────────────────────────────────────────────

@pytest.mark.parametrize("crudo,legible", [
    ("continuar_vendiendo", "Continuar vendiendo"),
    ("aún_no_lo_se", "Aún no lo sé"),
    ("aun_no_lo_se", "Aún no lo sé"),
    ("entre_usd_500_y_usd_1.000", "Entre USD 500 y USD 1.000"),
    ("más_de_usd_1.000", "Más de USD 1.000"),
    ("mas_de_usd_1.000", "Más de USD 1.000"),
    ("crear_mi_ecommerce", "Crear mi ecommerce"),
    ("automatizaciones", "Automatizaciones"),
    ("Montevideo", "Montevideo"),
    ("Interior", "Interior"),
    ("otros (4 respuestas distintas)", "otros (4 respuestas distintas)"),
    ("Quiero vender más en mi local", "Quiero vender más en mi local"),
    ("", ""),
])
def test_las_etiquetas_se_leen_en_castellano(crudo, legible):
    from services.dossier import etiqueta_legible
    assert etiqueta_legible(crudo) == legible


def test_la_etiqueta_legible_no_cambia_el_dato_ni_el_id(db):
    _lead(db, "a", {"¿contás_con_un_presupuesto_para_este_proyecto?": "aún_no_lo_se"})
    v = _bloques(db)["presupuesto"]["valores"][0]
    assert v["valor_declarado"] == "aún_no_lo_se"
    assert v["etiqueta"] == "Aún no lo sé"
    assert v["metricas"][0]["id"].startswith("segmento.presupuesto.aun_no_lo_se.")


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


def test_dos_grafias_de_la_misma_respuesta_libre_son_un_solo_segmento(db):
    """El objetivo del formulario viejo es texto libre. "Crecer" y "crecer" son
    la misma respuesta, y ademas producian el mismo id de metrica: dos metricas
    con el mismo id rompen la citacion del informe y hacen que el delta compare
    contra el gemelo equivocado. (Hasta el 14/9 este test usaba la ciudad, que
    ahora se agrupa en Montevideo e Interior.)"""
    _lead(db, "a", {_OBJETIVO_LIBRE: "Crecer en ventas"})
    _lead(db, "b", {_OBJETIVO_LIBRE: "crecer en ventas"})
    _lead(db, "c", {_OBJETIVO_LIBRE: " Crecer en ventas "})
    valores = _bloques(db)["objetivo_v2"]["valores"]
    assert len(valores) == 1
    assert valores[0]["n"] == 3


def test_se_muestra_la_grafia_mas_frecuente(db):
    _lead(db, "a", {_OBJETIVO_LIBRE: "Crecer"})
    _lead(db, "b", {_OBJETIVO_LIBRE: "Crecer"})
    _lead(db, "c", {_OBJETIVO_LIBRE: "crecer"})
    assert _bloques(db)["objetivo_v2"]["valores"][0]["valor_declarado"] == "Crecer"


def test_ningun_id_de_metrica_se_repite(db):
    _lead(db, "a", {_OBJETIVO_LIBRE: "Vender más", "city": "Maldonado"})
    _lead(db, "b", {_OBJETIVO_LIBRE: "vender mas", "city": "maldonado"})
    _lead(db, "c", {_OBJETIVO_LIBRE: "Córdoba", "city": "Montevideo"})
    _lead(db, "d", {_OBJETIVO_LIBRE: "cordoba", "city": "Mdeo"})
    ids = [m["id"] for b in por_segmento(db, "2026-03-01", "2026-03-31")
           for v in b["valores"] for m in v["metricas"]]
    assert len(ids) == len(set(ids))


def test_la_cola_larga_se_junta_en_otros(db):
    """El objetivo del formulario viejo es texto libre y tiene una cola de
    respuestas con n=1. Sin tope, el dossier daba 352 KB."""
    from services.dossier import TOPE_VALORES_POR_PREGUNTA

    for i in range(TOPE_VALORES_POR_PREGUNTA + 5):
        _lead(db, f"l{i}", {_OBJETIVO_LIBRE: f"Objetivo {i}"})
    bloque = _bloques(db)["objetivo_v2"]
    assert len(bloque["valores"]) == TOPE_VALORES_POR_PREGUNTA + 1
    assert bloque["valores"][-1]["valor_declarado"].startswith("otros (")
    assert bloque["valores"][-1]["n"] == 5
    assert bloque["valores_distintos"] == TOPE_VALORES_POR_PREGUNTA + 5


def test_sin_cola_no_hay_bucket_otros(db):
    _lead(db, "a", {"city": "Montevideo"})
    assert len(_bloques(db)["ciudad"]["valores"]) == 1
