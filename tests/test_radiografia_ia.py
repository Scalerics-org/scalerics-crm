"""El motor de IA: que ve el modelo y que se le permite decir.

Ningun test toca la API. Todos contra respuestas simuladas: la fase 3 se
escribio entera sin gastar un token.
"""

import io
import json

import pytest

from services.radiografia_ia import (MODELO, ia_activa, indice_de_metricas,
                                     preparar)


def test_el_modulo_no_importa_anthropic_a_nivel_de_archivo():
    """Son 19,6 MB y la maquina de Fly tiene 256 MB. El import va adentro de la
    funcion que lo usa, como en services/budget_ai.py."""
    fuente = io.open("services/radiografia_ia.py", encoding="utf-8").read()
    cabeza = fuente.split("\ndef ")[0]
    assert "import anthropic" not in cabeza


def test_el_modelo_es_opus_5():
    """El trabajo es razonamiento analitico y corre una vez por semana:
    degradar el modelo para ahorrar centavos no tiene sentido aca."""
    assert MODELO == "claude-opus-5"


def test_nace_apagado(monkeypatch):
    monkeypatch.delenv("RADIOGRAFIA_IA_ACTIVA", raising=False)
    assert ia_activa() is False


def test_se_prende_con_la_bandera(monkeypatch):
    monkeypatch.setenv("RADIOGRAFIA_IA_ACTIVA", "true")
    assert ia_activa() is True


def test_una_bandera_ambigua_no_prende(monkeypatch):
    """Prender esto cuesta plata: solo un si explicito."""
    for valor in ("", "0", "no", "False", "quizas"):
        monkeypatch.setenv("RADIOGRAFIA_IA_ACTIVA", valor)
        assert ia_activa() is False, valor


def test_el_indice_encuentra_toda_metrica_del_dossier():
    d = {"campanas": [{"campana": "x", "metricas": [
             {"id": "a", "valor": 1}, {"id": "b", "valor": 2}]}],
         "segmentos": [{"pregunta": "p", "valores": [
             {"valor_declarado": "v", "metricas": [{"id": "c", "valor": 3}]}]}],
         "tiempos": [{"id": "d", "valor": 4}]}
    assert set(indice_de_metricas(d)) == {"a", "b", "c", "d"}


def test_preparar_no_manda_datos_personales():
    """El dossier ya no los trae, pero si alguien los agrega manana esto lo
    frena antes de que salgan hacia un servicio externo."""
    d = {"campanas": [{"campana": "x", "metricas": [
            {"id": "a", "valor": 1, "etiqueta": "Gasto"}]}],
         "leads": [{"nombre": "Ana", "telefono": "+59899"}]}
    salida = json.dumps(preparar(d), ensure_ascii=False)
    assert "Ana" not in salida
    assert "+59899" not in salida


def test_preparar_conserva_lo_que_el_informe_necesita_citar():
    d = {"periodo": {"desde": "2026-03-01", "hasta": "2026-09-30"},
         "campanas": [{"campana": "x", "metricas": [
             {"id": "a", "valor": 0.2, "etiqueta": "Tasa", "n": 50,
              "ic95": [0.1, 0.3], "muestra_chica": False, "fuente": "crm",
              "delta_periodo_anterior": 0.05}]}]}
    preparado = preparar(d)
    assert preparado["periodo"] == d["periodo"]
    m = preparado["campanas"][0]["metricas"][0]
    assert {"id", "etiqueta", "valor", "n", "ic95", "muestra_chica",
            "fuente", "delta_periodo_anterior"} <= set(m)


def test_preparar_saca_los_bloques_que_no_son_metricas():
    """La serie semanal se dibuja, no se analiza: son 28 puntos por metrica que
    no aportan nada al informe y ocupan la mitad del dossier."""
    d = {"campanas": [], "serie_semanal": [{"semana": "2026-W10", "gasto": 1}]}
    assert "serie_semanal" not in preparar(d)


def test_un_bloque_nuevo_del_dossier_no_llega_solo_al_modelo():
    """La promesa que sostiene el costo y el validador a la vez.

    `preparar` es una lista blanca: arma el payload campo por campo. Si algun
    dia pasara a copiar el dossier entero, cada bloque nuevo que alguien agregue
    para dibujar —y ya van tres— se colaria al prompt sin que nadie lo decida:
    subiria la factura, y peor, el modelo podria citar numeros que el validador
    no tiene en su lista de legitimos y rechazaria su propio informe.

    Este test no protege un bloque puntual: protege el mecanismo.
    """
    d = {
        "campanas": [],
        "bloque_que_nadie_vio_venir": [{"n": 1}],
        "embudo_campanas": [{"campana": "UY", "etapas": []}],
        "serie_campanas": [{"campana": "UY", "puntos": []}],
    }
    listo = preparar(d)
    for clave in ("bloque_que_nadie_vio_venir", "embudo_campanas",
                  "serie_campanas"):
        assert clave not in listo, f"{clave} se colo al prompt"


# ── El validador anti-invención ──────────────────────────────────────────────

from services.radiografia_ia import validar  # noqa: E402


def _dossier():
    return {"periodo": {"desde": "2026-03-01", "hasta": "2026-09-30"},
            "campanas": [{"campana": "UY", "metricas": [
                {"id": "campana.uy.cpl", "valor": 9.38, "etiqueta": "CPL",
                 "n": None, "muestra_chica": False, "fuente": "derivada",
                 "formato": "moneda"},
                {"id": "campana.uy.tasa_demo", "valor": 0.229,
                 "etiqueta": "Tasa demo", "n": 83, "muestra_chica": False,
                 "fuente": "crm", "formato": "porcentaje"},
                {"id": "campana.arg.tasa_demo", "valor": 0.2,
                 "etiqueta": "Tasa demo ARG", "n": 5, "muestra_chica": True,
                 "fuente": "crm", "formato": "porcentaje"}]}]}


def _informe(**kw):
    base = {"titulo": "t", "tipo": "contexto", "cuerpo": "c",
            "metricas_citadas": ["campana.uy.cpl"], "confianza": "alta",
            "recomendacion": "r", "advertencia_muestra": False}
    base.update(kw)
    return {"resumen": "Sin novedades.", "hallazgos": [base],
            "cambios_desde_la_ultima": []}


def test_un_informe_limpio_pasa():
    assert validar(_informe(cuerpo="El CPL fue de 9,38."), _dossier()) == []


def test_un_numero_inventado_se_rechaza():
    """La regla que sostiene todo el diseno."""
    problemas = validar(_informe(cuerpo="El CPL fue de 47,20."), _dossier())
    assert any("47" in p for p in problemas)


def test_un_numero_inventado_en_el_resumen_tambien_se_rechaza():
    informe = _informe(cuerpo="El CPL fue de 9,38.")
    informe["resumen"] = "Cerramos 137 ventas."
    assert any("137" in p for p in validar(informe, _dossier()))


def test_un_numero_inventado_en_la_recomendacion_tambien(self=None):
    problemas = validar(_informe(cuerpo="ok", recomendacion="Subir a 900."),
                        _dossier())
    assert any("900" in p for p in problemas)


def test_un_id_inexistente_se_rechaza():
    assert validar(_informe(metricas_citadas=["campana.inventada.cpl"]),
                   _dossier())


def test_no_citar_ninguna_metrica_se_rechaza():
    assert validar(_informe(metricas_citadas=[]), _dossier())


def test_muestra_chica_sin_advertencia_se_rechaza():
    """Que la senal sea debil se dice, no se omite."""
    assert validar(_informe(metricas_citadas=["campana.arg.tasa_demo"],
                            advertencia_muestra=False), _dossier())


def test_muestra_chica_con_advertencia_pasa():
    assert validar(_informe(metricas_citadas=["campana.arg.tasa_demo"],
                            advertencia_muestra=True), _dossier()) == []


def test_los_numeros_del_periodo_no_cuentan_como_inventados():
    """Un ano o una fecha no es una metrica."""
    assert validar(_informe(cuerpo="Entre marzo y septiembre de 2026."),
                   _dossier()) == []


def test_un_porcentaje_escrito_como_porcentaje_se_reconoce():
    """El dossier guarda 0,229 y el informe escribe 22,9%."""
    assert validar(_informe(metricas_citadas=["campana.uy.tasa_demo"],
                            cuerpo="La tasa de demo fue 22,9%."),
                   _dossier()) == []


def test_un_n_citado_no_cuenta_como_inventado():
    """El n de la metrica es un numero legitimo del dossier."""
    assert validar(_informe(metricas_citadas=["campana.uy.tasa_demo"],
                            cuerpo="Sobre 83 leads."), _dossier()) == []


def test_la_causalidad_avisa_pero_no_rechaza():
    problemas = validar(
        _informe(cuerpo="El CPL bajo porque se cambio el creativo."),
        _dossier())
    assert any(p.startswith("aviso:") for p in problemas)
    assert not [p for p in problemas if not p.startswith("aviso:")]


@pytest.mark.parametrize("frase", [
    "El CPL bajo gracias al cambio de creativo.",
    "Subio debido al aumento de puja.",
    "El costo por demo se debe al publico elegido.",
    "Mejoro a raiz del nuevo formulario.",
    "El cambio de creativo hizo que bajara el CPL.",
    "El presupuesto mas alto llevo a mas leads.",
])
def test_la_causalidad_tambien_avisa_con_las_formas_contraidas(frase):
    """`gracias a` con \b no matchea `gracias al`, que es como se escribe.

    El patron pedia que despues de la preposicion viniera un limite de palabra,
    asi que las contracciones —al, del— se le escapaban enteras. En castellano
    esas son las formas normales: nadie escribe "gracias a el cambio".
    """
    problemas = validar(_informe(cuerpo=frase), _dossier())
    assert any(p.startswith("aviso:") for p in problemas), frase


def test_un_informe_sin_hallazgos_se_rechaza():
    informe = _informe()
    informe["hallazgos"] = []
    assert validar(informe, _dossier())


# ── La llamada, con reintento ────────────────────────────────────────────────

from services.radiografia_ia import redactar  # noqa: E402


def test_sin_la_bandera_no_llama(monkeypatch):
    monkeypatch.delenv("RADIOGRAFIA_IA_ACTIVA", raising=False)
    llamadas = []
    r = redactar(_dossier(), llamar=lambda *a, **k: llamadas.append(1))
    assert llamadas == []
    assert r["status"] == "sin_ia"
    assert r["informe"] is None


def test_un_informe_valido_se_devuelve(monkeypatch):
    monkeypatch.setenv("RADIOGRAFIA_IA_ACTIVA", "true")
    r = redactar(_dossier(),
                 llamar=lambda *a, **k: (_informe(cuerpo="CPL 9,38."), 100, 50))
    assert r["status"] == "ok"
    assert r["informe"]["hallazgos"]
    assert r["tokens_in"] == 100
    assert r["tokens_out"] == 50


def test_un_numero_inventado_dispara_un_reintento(monkeypatch):
    """Y el reintento recibe el error concreto, no un 'proba de nuevo'."""
    monkeypatch.setenv("RADIOGRAFIA_IA_ACTIVA", "true")
    intentos = []

    def _llamar(dossier, correccion=None):
        intentos.append(correccion)
        if len(intentos) == 1:
            return (_informe(cuerpo="CPL 47,20."), 10, 10)
        return (_informe(cuerpo="CPL 9,38."), 10, 10)

    r = redactar(_dossier(), llamar=_llamar)
    assert len(intentos) == 2
    assert intentos[0] is None
    assert "47" in (intentos[1] or ""), "el reintento tiene que decir que fallo"
    assert r["status"] == "ok"


def test_dos_fallos_seguidos_no_publican_nada(monkeypatch):
    """Nunca se publica un informe sin validar."""
    monkeypatch.setenv("RADIOGRAFIA_IA_ACTIVA", "true")
    r = redactar(_dossier(),
                 llamar=lambda *a, **k: (_informe(cuerpo="CPL 47,20."), 10, 10))
    assert r["status"] == "error_validacion"
    assert r["informe"] is None
    assert "47" in r["error"]


def test_un_aviso_de_causalidad_no_impide_publicar(monkeypatch):
    """Es la unica regla blanda de las cuatro."""
    monkeypatch.setenv("RADIOGRAFIA_IA_ACTIVA", "true")
    r = redactar(_dossier(), llamar=lambda *a, **k: (
        _informe(cuerpo="El CPL bajo porque cambiamos el creativo."), 10, 10))
    assert r["status"] == "ok"
    assert r["avisos"]


def test_un_error_de_la_api_no_tumba_la_corrida(monkeypatch):
    monkeypatch.setenv("RADIOGRAFIA_IA_ACTIVA", "true")

    def _explota(*a, **k):
        raise RuntimeError("503 del proveedor")

    r = redactar(_dossier(), llamar=_explota)
    assert r["status"] == "error_ia"
    assert r["informe"] is None
    assert "503" in r["error"]
