"""El modulo de graficos tiene que parsear y sus funciones puras dar bien.

Sigue el patron de tests/test_dashboard_js.py: node --check desde pytest, y se
saltea si node no esta instalado para no romperle la suite a nadie por una
dependencia que el proyecto no tiene.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
CHARTS = RAIZ / "static" / "charts.js"

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")


def _correr(js: str, tmp_path):
    """Carga charts.js en node y corre `js`, que tiene que imprimir JSON."""
    archivo = tmp_path / "correr.js"
    archivo.write_text(
        "globalThis.window = globalThis;\n"
        + CHARTS.read_text(encoding="utf-8")
        + "\nconst SC = globalThis.SC;\n"
        + js + "\n", encoding="utf-8")
    # encoding utf-8 explicito: con text=True a secas, Windows decodifica la
    # salida de node con la codepage del sistema (cp1252) y rompe los acentos.
    r = subprocess.run(["node", str(archivo)], capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@sin_node
def test_el_modulo_compila():
    r = subprocess.run(["node", "--check", str(CHARTS)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, f"charts.js no compila:\n{r.stderr}"


@sin_node
def test_la_paleta_es_la_validada(tmp_path):
    """Si alguien cambia un color hay que volver a correr el validador de la
    skill de dataviz contra las dos superficies. El test existe para que ese
    cambio no pase inadvertido."""
    p = _correr("console.log(JSON.stringify(SC.PALETA));", tmp_path)
    assert p["claro"][:5] == ["#0088CC", "#A855F7", "#0D9488", "#EA580C", "#4F46E5"]
    assert p["oscuro"][:5] == ["#0088CC", "#A855F7", "#0D9488", "#EA580C", "#6366F1"]
    # El indigo cambia de paso: #4F46E5 sobre #111827 da 2,82:1, bajo el piso.
    assert p["claro"][4] != p["oscuro"][4]


@sin_node
def test_sin_campana_va_en_gris(tmp_path):
    """Ese bucket no es una categoria mas: es un desconocido."""
    r = _correr(
        "SC._resetColores();"
        "console.log(JSON.stringify(["
        "  SC.colorDeCampana('(sin campaña)', 0, 'oscuro'),"
        "  SC.colorDeCampana('Leads - UY - 2026', 0, 'oscuro')]));", tmp_path)
    assert r[0] == "#64748B"
    assert r[1] != r[0]


@sin_node
def test_el_color_sigue_a_la_campana_no_a_la_posicion(tmp_path):
    """Filtrar no puede repintar a las que quedan."""
    r = _correr(
        "SC._resetColores();"
        "console.log(JSON.stringify(["
        "  SC.colorDeCampana('Leads - UY - 2026', 2, 'claro'),"
        "  SC.colorDeCampana('otra', 0, 'claro'),"
        "  SC.colorDeCampana('Leads - UY - 2026', 0, 'claro')]));", tmp_path)
    assert r[0] == r[2]
    assert r[1] != r[0]


@sin_node
def test_una_septima_campana_cae_en_el_gris(tmp_path):
    """No se generan hues nuevos."""
    r = _correr(
        "SC._resetColores();"
        "const nombres = ['a','b','c','d','e','f','g'];"
        "console.log(JSON.stringify(nombres.map((n,i) => "
        "  SC.colorDeCampana(n, i, 'claro'))));", tmp_path)
    assert len(set(r[:5])) == 5
    assert r[5] == "#94A3B8"
    assert r[6] == "#94A3B8"


@sin_node
def test_los_numeros_van_en_formato_uruguayo(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(["
        "  SC.fmt(1234.5, 'moneda'),"
        "  SC.fmt(0.1084, 'porcentaje'),"
        "  SC.fmt(4500, 'numero'),"
        "  SC.fmt(1234567, 'numero')]));", tmp_path)
    assert r[0] == "1.234,50"
    assert r[1] == "10,8%"
    assert r[2] == "4.500"
    assert r[3] == "1.234.567"


@sin_node
def test_un_null_dice_sin_datos_y_no_cero(tmp_path):
    """costo() devuelve null cuando no hay de que dividir. Dibujarlo como cero
    diria que el costo por demo fue cero, que es otra afirmacion."""
    r = _correr(
        "console.log(JSON.stringify([SC.fmt(null,'numero'),"
        " SC.fmt(undefined,'moneda'), SC.fmt(0,'moneda')]));", tmp_path)
    assert r[0] == "sin datos"
    assert r[1] == "sin datos"
    assert r[2] == "0,00", "un cero de verdad si se muestra"


@sin_node
def test_la_escala_mapea_los_extremos(tmp_path):
    r = _correr(
        "const e = SC.escalaLineal([0, 100], [0, 400]);"
        "console.log(JSON.stringify([e(0), e(50), e(100)]));", tmp_path)
    assert r == [0, 200, 400]


@sin_node
def test_una_escala_de_dominio_cero_no_divide_por_cero(tmp_path):
    r = _correr(
        "const e = SC.escalaLineal([5, 5], [0, 400]);"
        "console.log(JSON.stringify(e(5)));", tmp_path)
    assert r == 0


@sin_node
def test_los_ticks_son_numeros_redondos(tmp_path):
    r = _correr("console.log(JSON.stringify(SC.ticks(0, 97, 5)));", tmp_path)
    assert r[0] == 0
    assert r[-1] >= 97
    assert r == sorted(r)
    paso = r[1] - r[0]
    assert all(abs((b - a) - paso) < 1e-6 for a, b in zip(r, r[1:]))


@sin_node
def test_los_ticks_de_un_rango_plano_no_cuelgan(tmp_path):
    r = _correr("console.log(JSON.stringify(SC.ticks(7, 7, 5)));", tmp_path)
    assert r == [7]


@sin_node
def test_el_texto_se_escapa_antes_de_entrar_al_svg(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(SC.esc('Leads & <UY>')));", tmp_path)
    assert "<" not in r
    assert "&amp;" in r and "&lt;" in r


@sin_node
def test_el_delta_trae_su_signo(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(["
        "  SC.fmtDelta(12.5, 'moneda'), SC.fmtDelta(-3, 'moneda'),"
        "  SC.fmtDelta(0, 'moneda'), SC.fmtDelta(null, 'moneda')]));", tmp_path)
    assert r[0]["signo"] == "sube"
    assert r[1]["signo"] == "baja"
    assert r[2]["signo"] == "igual"
    assert r[3]["signo"] == "sin_comparacion"
    assert "-" not in r[1]["texto"], "el signo va aparte, no pegado al numero"


# ── Tiles ────────────────────────────────────────────────────────────────────

@sin_node
def test_un_tile_sin_periodo_anterior_no_finge_un_delta(tmp_path):
    """La primera corrida no tiene contra que comparar. Eso no es 0%."""
    r = _correr(
        "console.log(JSON.stringify(SC.tiles([{id:'x', etiqueta:'Gasto',"
        " valor:100, formato:'moneda', delta_periodo_anterior:null}],"
        " 'oscuro')));", tmp_path)
    assert "sin período anterior" in r
    assert "0,00" not in r.split("delta")[-1]


@sin_node
def test_un_tile_sin_valor_dice_sin_datos(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(SC.tiles([{id:'x', etiqueta:'CPL',"
        " valor:null, formato:'moneda', delta_periodo_anterior:null}],"
        " 'oscuro')));", tmp_path)
    assert "sin datos" in r


@sin_node
def test_el_tile_escapa_la_etiqueta(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(SC.tiles([{id:'x',"
        " etiqueta:'<script>alert(1)</script>', valor:1, formato:'numero',"
        " delta_periodo_anterior:null}], 'oscuro')));", tmp_path)
    assert "<script>" not in r


@sin_node
def test_en_un_costo_subir_es_malo(tmp_path):
    """En cpl y costo_demo, subir no es una buena noticia. El sentido lo trae
    el tile, no se adivina del signo."""
    r = _correr(
        "const sube = {valor:10, formato:'moneda', delta_periodo_anterior:2};"
        "console.log(JSON.stringify(["
        "  SC.tiles([Object.assign({id:'cpl', etiqueta:'CPL', mejor:'bajo'},"
        "    sube)], 'oscuro'),"
        "  SC.tiles([Object.assign({id:'leads', etiqueta:'Leads', mejor:'alto'},"
        "    sube)], 'oscuro')]));", tmp_path)
    assert 'data-animo="malo"' in r[0]
    assert 'data-animo="bueno"' in r[1]


@sin_node
def test_el_delta_no_depende_solo_del_color(tmp_path):
    """Un signo que solo se distingue por color no se distingue."""
    r = _correr(
        "console.log(JSON.stringify(SC.tiles([{id:'x', etiqueta:'Gasto',"
        " valor:100, formato:'moneda', delta_periodo_anterior:5,"
        " mejor:'alto'}], 'oscuro')));", tmp_path)
    assert "<svg" in r, "falta el icono de flecha"
    assert 'data-signo="sube"' in r


# ── Embudo ───────────────────────────────────────────────────────────────────

@sin_node
def test_el_embudo_dibuja_una_fila_por_etapa(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(SC.embudo(["
        " {clave:'impresiones', etiqueta:'Impresiones', valor:44900,"
        "  fuente:'meta_insights'},"
        " {clave:'clics', etiqueta:'Clics', valor:2000, fuente:'meta_insights'},"
        " {clave:'leads', etiqueta:'Leads', valor:338, fuente:'crm'}],"
        " 'oscuro')));", tmp_path)
    assert r.count("<rect") >= 3
    assert "44.900" in r and "2.000" in r and "338" in r


@sin_node
def test_el_embudo_muestra_la_caida_entre_etapas(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(SC.embudo(["
        " {clave:'a', etiqueta:'A', valor:100, fuente:'crm'},"
        " {clave:'b', etiqueta:'B', valor:25, fuente:'crm'}], 'oscuro')));",
        tmp_path)
    assert "25,0%" in r


@sin_node
def test_el_embudo_distingue_meta_del_crm(tmp_path):
    """Las tres primeras etapas las tiene cualquier reporte de ads. Las seis
    siguientes son lo que solo tenemos nosotros, y eso se ve."""
    r = _correr(
        "console.log(JSON.stringify(SC.embudo(["
        " {clave:'a', etiqueta:'A', valor:10, fuente:'meta_insights'},"
        " {clave:'b', etiqueta:'B', valor:5, fuente:'crm'}], 'oscuro')));",
        tmp_path)
    assert 'data-fuente="meta_insights"' in r
    assert 'data-fuente="crm"' in r
    assert "hasta acá llega" in r, "falta el rotulo del corte"


@sin_node
def test_una_etapa_en_cero_no_rompe_el_embudo(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(SC.embudo(["
        " {clave:'a', etiqueta:'A', valor:0, fuente:'crm'},"
        " {clave:'b', etiqueta:'B', valor:0, fuente:'crm'}], 'oscuro')));",
        tmp_path)
    assert "NaN" not in r and "Infinity" not in r


@sin_node
def test_el_embudo_vacio_no_rompe(tmp_path):
    r = _correr("console.log(JSON.stringify(SC.embudo([], 'oscuro')));",
                tmp_path)
    assert "Sin datos" in r


@sin_node
def test_una_etapa_sin_valor_no_se_dibuja_como_cero(tmp_path):
    """Sin credenciales de Insights no hay impresiones. Eso no es cero
    impresiones: es que no lo sabemos."""
    r = _correr(
        "console.log(JSON.stringify(SC.embudo(["
        " {clave:'impresiones', etiqueta:'Impresiones', valor:null,"
        "  fuente:'meta_insights'},"
        " {clave:'leads', etiqueta:'Leads', valor:10, fuente:'crm'}],"
        " 'oscuro')));", tmp_path)
    assert "sin datos" in r


# ── Series temporales ────────────────────────────────────────────────────────

@sin_node
def test_la_serie_dibuja_un_solo_eje_y(tmp_path):
    """Nunca doble eje: con dos escalas se puede fabricar cualquier
    correlacion moviendo un eje."""
    r = _correr(
        "console.log(JSON.stringify(SC.serie(["
        " {x:'2026-W10', y:100},{x:'2026-W11', y:150}],"
        " {etiqueta:'Gasto', formato:'moneda'}, 'oscuro')));", tmp_path)
    assert r.count('class="sc-eje-y"') == 1


@sin_node
def test_un_hueco_en_la_serie_corta_la_linea(tmp_path):
    """Una semana sin CPL no se une con una recta a la siguiente: eso
    inventaria un dato que no hay."""
    r = _correr(
        "console.log(JSON.stringify(SC.serie(["
        " {x:'a', y:1},{x:'b', y:null},{x:'c', y:3}],"
        " {etiqueta:'CPL', formato:'moneda'}, 'oscuro')));", tmp_path)
    assert r.count('class="sc-linea"') == 2


@sin_node
def test_la_serie_lleva_su_titulo_y_no_necesita_leyenda(tmp_path):
    """Una sola serie: el titulo la nombra, no hace falta caja de leyenda."""
    r = _correr(
        "console.log(JSON.stringify(SC.serie([{x:'a', y:1},{x:'b', y:2}],"
        " {etiqueta:'Gasto por semana', formato:'moneda'}, 'claro')));",
        tmp_path)
    assert "Gasto por semana" in r
    assert 'class="sc-leyenda"' not in r


@sin_node
def test_la_serie_vacia_no_rompe(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(SC.serie([], {etiqueta:'X',"
        " formato:'numero'}, 'oscuro')));", tmp_path)
    assert "Sin datos" in r or "sin datos" in r


@sin_node
def test_el_par_apilado_comparte_el_eje_de_tiempo(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(SC.parApilado("
        "  {puntos:[{x:'a',y:1},{x:'b',y:2}], etiqueta:'Gasto', formato:'moneda'},"
        "  {puntos:[{x:'a',y:3},{x:'b',y:4}], etiqueta:'CPL', formato:'moneda'},"
        "  'oscuro')));", tmp_path)
    assert r.count('class="sc-panel-serie"') == 2


# ── Barras con intervalo de confianza ────────────────────────────────────────

@sin_node
def test_una_muestra_chica_dibuja_su_intervalo(tmp_path):
    """Con 51 leads, cinco puntos de diferencia contra otra campana no
    significan nada, y el grafico tiene que decirlo."""
    r = _correr(
        "console.log(JSON.stringify(SC.barrasConIC([{etiqueta:'ARG',"
        " metrica:{valor:0.2, n:10, ic95:[0.05,0.5], muestra_chica:true,"
        " formato:'porcentaje'}}], {}, 'claro')));", tmp_path)
    assert 'class="sc-ic"' in r
    assert "n=10" in r
    assert "muestra chica" in r


@sin_node
def test_una_muestra_grande_no_lleva_la_advertencia(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(SC.barrasConIC([{etiqueta:'UY',"
        " metrica:{valor:0.2, n:400, ic95:[0.18,0.22], muestra_chica:false,"
        " formato:'porcentaje'}}], {}, 'claro')));", tmp_path)
    assert "muestra chica" not in r
    assert "n=400" in r, "el n va siempre, aunque sea grande"


@sin_node
def test_una_metrica_sin_valor_no_dibuja_barra(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(SC.barrasConIC([{etiqueta:'X',"
        " metrica:{valor:null, n:0, ic95:[0,1], muestra_chica:true,"
        " formato:'moneda'}}], {}, 'claro')));", tmp_path)
    assert "sin datos" in r


@sin_node
def test_la_barra_toma_el_color_de_su_campana(tmp_path):
    """Ordenar por valor no puede repintar: el color sigue a la entidad."""
    r = _correr(
        "SC._resetColores();"
        "SC.colorDeCampana('UY', 0, 'claro');"
        "SC.colorDeCampana('ARG', 1, 'claro');"
        "console.log(JSON.stringify(SC.barrasConIC(["
        " {etiqueta:'ARG', campana:'ARG', metrica:{valor:0.5, n:50,"
        "  ic95:[0.4,0.6], muestra_chica:false, formato:'porcentaje'}},"
        " {etiqueta:'UY', campana:'UY', metrica:{valor:0.2, n:80,"
        "  ic95:[0.1,0.3], muestra_chica:false, formato:'porcentaje'}}],"
        " {}, 'claro')));", tmp_path)
    assert "#A855F7" in r, "ARG conserva su color aunque quede primera"
    assert "#0088CC" in r
