"""El panel de Marketing esta armado y su JS compila."""

import re
import shutil
import subprocess

import pytest

import dashboard


def test_el_panel_esta_en_la_navegacion():
    assert "showPanel('marketing')" in dashboard.DASHBOARD_HTML
    assert 'id="nav-marketing"' in dashboard.DASHBOARD_HTML


def test_el_panel_existe():
    assert 'id="marketing-panel"' in dashboard.DASHBOARD_HTML


def test_showpanel_lo_carga():
    assert "if (name === 'marketing') loadMarketing();" in dashboard.DASHBOARD_HTML


def test_el_panel_carga_charts_js():
    assert '/static/charts.js' in dashboard.DASHBOARD_HTML


def test_estan_los_contenedores_que_el_js_llena():
    """Si el JS escribe en un id que no existe, el panel queda mudo y no se
    entera nadie: innerHTML sobre null tira una excepcion silenciosa."""
    for ident in ("mk-estado", "mk-cuerpo", "mk-avisos", "mk-tiles", "mk-embudo",
                  "mk-series", "mk-campanas", "mk-segmentos", "mk-recordatorios",
                  "mk-tabla", "mk-desde", "mk-hasta", "mk-campana", "mk-fecha"):
        assert f'id="{ident}"' in dashboard.DASHBOARD_HTML, f"falta #{ident}"


def test_no_hay_emojis_en_el_panel():
    """El CRM usa iconos SVG, no emojis."""
    bloque = dashboard.DASHBOARD_HTML.split('id="marketing-panel"')[1][:9000]
    encontrados = re.findall(r"[\U0001F300-\U0001FAFF]", bloque)
    assert not encontrados, f"emojis en el panel: {encontrados}"


def test_el_panel_tiene_sus_dos_temas():
    """El CRM tiene body.light. Nada de invertir colores automaticamente."""
    for regla in (".sc-tile", ".sc-bloque", ".sc-aviso", ".sc-tabla"):
        assert f"body.light {regla}" in dashboard.DASHBOARD_HTML, \
            f"{regla} no tiene su version clara"


def test_la_tabla_de_datos_existe():
    """La guia de visualizacion la pide como salida accesible, y ademas es lo
    que permite auditar cualquier numero del panel."""
    assert "Los números crudos" in dashboard.DASHBOARD_HTML


@pytest.mark.skipif(shutil.which("node") is None, reason="node no esta instalado")
def test_el_javascript_del_dashboard_sigue_compilando(tmp_path):
    bloques = re.findall(r"<script>(.*?)</script>", dashboard.DASHBOARD_HTML, re.S)
    assert bloques
    archivo = tmp_path / "d.js"
    archivo.write_text("\n".join(bloques), encoding="utf-8")
    r = subprocess.run(["node", "--check", str(archivo)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr


def test_el_panel_tiene_donde_mostrar_el_informe():
    """El motor de IA existia pero el panel no tenia donde pintar la lectura:
    aunque se prendiera, la reflexion no se veia en ningun lado."""
    assert 'id="mk-informe"' in dashboard.DASHBOARD_HTML
    assert "_mkInforme" in dashboard.DASHBOARD_HTML
    assert "/api/marketing/radiografia" in dashboard.DASHBOARD_HTML


def test_los_estados_sin_informe_se_distinguen():
    """Apagado no es lo mismo que fallido, y ninguno es 'no corrio nunca'."""
    for estado in ("sin_ia", "error_validacion", "error_ia"):
        assert f"'{estado}'" in dashboard.DASHBOARD_HTML, estado


def test_el_informe_muestra_las_metricas_citadas():
    """Son lo que deja bajar a la tabla y comprobar cada afirmacion."""
    assert "metricas_citadas" in dashboard.DASHBOARD_HTML
    assert "se sostiene en" in dashboard.DASHBOARD_HTML


def test_el_informe_tiene_sus_dos_temas():
    for regla in (".sc-informe", ".sc-hallazgo-tit", ".sc-chip"):
        assert f"body.light {regla}" in dashboard.DASHBOARD_HTML, regla


def test_el_panel_compara_costo_por_lead_contra_costo_por_demo():
    """El hallazgo mas util del modulo requeria comparar dos graficos de barras
    a ojo. En la misma tabla, el punto se ve solo."""
    assert 'id="mk-ranking"' in dashboard.DASHBOARD_HTML
    assert "Qué campaña rinde de verdad" in dashboard.DASHBOARD_HTML
    # El aviso esta partido en dos literales por el ancho de linea, asi que se
    # busca la clase que lo marca y no el texto entero.
    assert "sc-rank-invertido" in dashboard.DASHBOARD_HTML
    assert "costo_demo" in dashboard.DASHBOARD_HTML or ".costo_demo" in dashboard.DASHBOARD_HTML


def test_el_ranking_necesita_al_menos_dos_campanas_con_gasto():
    """Con una sola campana no hay nada que rankear."""
    assert "conGasto.length < 2" in dashboard.DASHBOARD_HTML
