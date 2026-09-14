"""El panel de Marketing esta armado y su JS compila."""

import pathlib
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
                  "mk-series", "mk-campanas", "mk-segmentos",
                  # mk-hallazgos, mk-embudos, mk-evolucion y mk-acumulado son
                  # los bloques nuevos; mk-recordatorios se saco del panel.
                  "mk-hallazgos", "mk-embudos", "mk-evolucion", "mk-acumulado",
                  "mk-desde", "mk-hasta", "mk-campana", "mk-fecha"):
        assert f'id="{ident}"' in dashboard.DASHBOARD_HTML, f"falta #{ident}"


def test_no_hay_emojis_en_el_panel():
    """El CRM usa iconos SVG, no emojis."""
    bloque = dashboard.DASHBOARD_HTML.split('id="marketing-panel"')[1][:9000]
    encontrados = re.findall(r"[\U0001F300-\U0001FAFF]", bloque)
    assert not encontrados, f"emojis en el panel: {encontrados}"


# Las reglas del panel que hasta el 11/9/2026 tenian una gemela `body.light`
# escrita a mano. Ahora el tema claro sale de los tokens del CRM, asi que la
# gemela sobra: su presencia es el sintoma de que alguien volvio a escribir el
# mismo cambio dos veces.
_REGLAS_DEL_PANEL = (".sc-tile", ".sc-bloque", ".sc-aviso", ".sc-tabla",
                     ".sc-informe", ".sc-hallazgo-tit", ".sc-chip")


def _cuerpos(selector):
    """Los cuerpos de toda regla que arranque con `selector`."""
    return re.findall(r"^" + re.escape(selector) + r"[^{]*\{([^}]*)\}",
                      dashboard.DASHBOARD_HTML, re.M)


@pytest.mark.parametrize("selector", _REGLAS_DEL_PANEL)
def test_el_panel_no_escribe_colores_a_mano(selector):
    """El panel se pinta con los tokens del CRM, no con hex propios.

    Antes este test pedia lo contrario —que hubiera una regla `body.light` por
    cada una— porque el panel nacio antes de que el resto del dashboard tuviera
    tokens. Con los tokens puestos, un hex suelto aca es el panel despegandose
    del tema claro sin que nadie lo note.
    """
    cuerpos = _cuerpos(selector)
    assert cuerpos, f"no encontre ninguna regla {selector}"
    for cuerpo in cuerpos:
        sueltos = re.findall(r"#[0-9a-fA-F]{3,8}\b", cuerpo)
        assert not sueltos, f"{selector} tiene colores a mano: {sueltos}"


@pytest.mark.parametrize("selector", _REGLAS_DEL_PANEL)
def test_el_panel_no_tiene_reglas_claras_propias(selector):
    patron = r"^body\.light " + re.escape(selector) + r"[^{]*\{"
    assert not re.search(patron, dashboard.DASHBOARD_HTML, re.M), (
        f"sobra `body.light {selector}`: los tokens ya lo cubren")


def test_los_numeros_siguen_teniendo_una_vista_en_tabla():
    """La guia de visualizacion pide una salida en tabla, y no solo por
    accesibilidad: es lo que deja auditar un numero sin estimarlo contra una
    grilla.

    Habia un bloque "Los numeros crudos" con TODAS las metricas del dossier.
    Se saco el 14/9 a pedido de Juan, dos veces: "los numeros crudos siguen sin
    entenderse", "saca lo de los datos crudos". Estaba bien calculado y no se
    leia, y un bloque que no se lee no audita nada — solo ocupa lugar.

    Lo que queda en tabla son las dos que sostienen decisiones: el ranking de
    campanas (donde se ve que el orden se da vuelta segun que mires) y la
    conciliacion contra Finanzas. El resto de los numeros sigue estando en los
    graficos, cada uno con su valor escrito al lado de la marca.
    """
    assert "Los números crudos" not in dashboard.DASHBOARD_HTML
    assert dashboard.DASHBOARD_HTML.count('class="sc-tabla"') >= 2


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


def test_el_fondo_de_los_graficos_es_la_superficie_de_la_tarjeta():
    """El fondo del SVG no es decorativo.

    Es la superficie contra la que se validan los contrastes de la paleta, y
    ademas tiene que ser el mismo color que la tarjeta que lo contiene: si se
    despegan, cada grafico se ve como un recuadro mas oscuro adentro del bloque.
    Este test los ata, para que mover `--superficie` no deje los graficos atras.
    """
    js = (pathlib.Path(dashboard.__file__).parent / "static" / "charts.js")
    raiz = re.search(r":root\{([^}]*)\}", dashboard.DASHBOARD_HTML).group(1)
    superficie = re.search(r"--superficie:\s*([^;]+);", raiz).group(1).strip()
    assert f"oscuro: '{superficie}'" in js.read_text(encoding="utf-8"), (
        f"el fondo oscuro de charts.js no es --superficie ({superficie})")


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
