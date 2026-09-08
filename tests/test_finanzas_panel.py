"""El panel Finanzas tiene que estar registrado en los siete lugares.

Dos de los siete fallan en silencio si se olvidan: sin la entrada en el
ALL_PANELS del editor de roles no se le puede asignar a nadie, y sin el
_grant_panel_to_existing_roles en database.py no lo ve nadie en producción,
donde la tabla `roles` ya tiene filas. Es exactamente lo que pasó con `meta`.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import dashboard

HTML = dashboard.DASHBOARD_HTML

FUENTE = Path(__file__).resolve().parents[1] / "dashboard.py"
SRC = FUENTE.read_text(encoding="utf-8")


def test_el_item_del_nav_existe():
    assert 'id="nav-finanzas"' in HTML
    assert "showPanel('finanzas')" in HTML


def test_el_div_del_panel_existe():
    assert 'id="finanzas-panel"' in HTML


def test_show_panel_llama_a_load_finanzas():
    assert "if (name === 'finanzas') loadFinanzas();" in HTML


def test_esta_en_los_dos_all_panels():
    """Uno vive en DASHBOARD_HTML, el otro en el HTML del editor de roles.

    El segundo es una variable local de `admin_users_page()`, no parte de
    DASHBOARD_HTML, asi que hay que leer el archivo fuente y no el string.
    Si falta, el panel existe pero no se le puede asignar a nadie.
    """
    listas = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", SRC)
    assert len(listas) == 2, f"cambio la cantidad de ALL_PANELS: {len(listas)}"
    for lista in listas:
        assert "'finanzas'" in lista


def test_tiene_etiqueta_en_el_editor_de_roles():
    """Sin esto el panel existe pero no se le puede asignar a nadie."""
    etiquetas = re.search(r"const PANEL_LABELS = \{([^}]*)\}", SRC).group(1)
    assert "finanzas:'Finanzas'" in etiquetas.replace(" ", "")


def test_esta_en_la_navegacion_mobile():
    """NAV_ICONS y NAV_LABELS son objetos distintos: _buildMobileNav usa los dos."""
    prioridad = re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", HTML).group(1)
    assert "'finanzas'" in prioridad
    iconos = re.search(r"const NAV_ICONS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "finanzas:'wallet'" in iconos.replace(" ", "")
    etiquetas = re.search(r"const NAV_LABELS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "finanzas:'Finanzas'" in etiquetas.replace(" ", "")


def test_database_le_da_el_panel_a_los_roles_existentes():
    fuente = Path(__file__).resolve().parents[1] / "database.py"
    assert '_grant_panel_to_existing_roles(conn, "finanzas")' in \
        fuente.read_text(encoding="utf-8")


def test_no_hay_emojis_en_lo_que_agrega_finanzas():
    """El CRM usa lucide. Un emoji suelto se ve distinto en cada sistema.

    Se miran las lineas propias de la seccion (las que nombran `finanzas` o
    una clase `fin-`) y no una ventana de N caracteres alrededor del panel:
    esa ventana agarra modales viejos que ya traen emojis y no son de esta
    rama.
    """
    propias = [l for l in SRC.splitlines() if "finanzas" in l or "fin-" in l]
    assert propias, "no encontre ninguna linea de la seccion"
    con_emoji = [l for l in propias if re.search(r"[\U0001F300-\U0001FAFF]", l)]
    assert not con_emoji, f"emojis en: {con_emoji[:3]}"


def test_los_montos_se_muestran_en_dolares():
    """El panel nunca inventa una conversión: muestra el monto_usd que vino."""
    assert "'USD '" in HTML
    assert "_finUsd" in HTML


def test_el_panel_tiene_reglas_para_modo_claro():
    assert "body.light .fin-card" in HTML


def test_el_json_de_los_onclick_va_escapado():
    """Un concepto con apóstrofo partiría el atributo y mataría el botón."""
    assert "function _finAttr(" in HTML
    assert "abrirMovimiento(${_finAttr(m)})" in HTML
    assert "abrirMovimiento(${JSON.stringify(m)})" not in HTML


@pytest.mark.skipif(shutil.which("node") is None, reason="node no esta instalado")
def test_finattr_escapa_lo_que_el_html_decodificaria_como_comilla(tmp_path):
    """Que exista `function _finAttr(` no prueba que escape nada: ese era el
    test viejo, y por eso sobrevivió el bug. El HTML decodifica las entidades
    del atributo ANTES de compilarlo como JS, así que un nombre de negocio con
    el texto literal `&quot;` (seis caracteres, no una comilla real) decodifica
    a una comilla real y rompe el JSON.stringify que _finAttr arma — mismo
    ataque con `&#92;` para una barra invertida. Esto corre el cuerpo real de
    _finAttr en node y lo prueba contra ese payload."""
    m = re.search(r"function _finAttr\(obj\) \{.*?\n\}", HTML, re.S)
    assert m, "no encontré _finAttr en el HTML"

    payload = "a&quot;});alert(1);//" + "'" + "<x>" + "\\"
    script = m.group(0) + "\nconsole.log(_finAttr({clientName: %s}));" % json.dumps(payload)
    archivo = tmp_path / "_finAttr.js"
    archivo.write_text(script, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True)
    assert r.returncode == 0, f"_finAttr no corrió en node:\n{r.stderr}"
    salida = r.stdout

    # "&quot;" crudo en el atributo decodifica a una comilla real antes de
    # que el navegador compile el JS: tiene que sobrevivir solo como
    # "&amp;quot;" (que decodifica de vuelta al texto "&quot;", no a una
    # comilla). Ninguna "&quot;" cruda puede quedar en la salida.
    assert "&amp;quot;" in salida
    assert "&quot;" not in salida.replace("&amp;quot;", "")
    # Comillas simples y angulares tampoco pueden viajar crudas.
    assert "'" not in salida.replace("&#39;", "")
    assert "<" not in salida
    assert ">" not in salida


def test_el_modal_muestra_el_monto_en_dolares_antes_de_guardar():
    """Ver el número congelado antes de congelarlo es el punto del modal."""
    assert "Se va a guardar como" in HTML
    assert 'id="fin-tc-preview"' in HTML


def test_borrar_un_movimiento_de_un_fijo_avisa_que_el_fijo_sigue():
    assert "el fijo sigue activo" in HTML


def test_el_json_del_fijo_tambien_va_escapado():
    assert "abrirFijo(${_finAttr(f)})" in HTML
    assert "abrirFijo(${JSON.stringify(f)})" not in HTML


def test_borrar_un_fijo_aclara_que_los_movimientos_quedan():
    assert "son plata que se gastó" in HTML


def test_loadfijos_no_convierte_moneda_en_el_navegador():
    """La cuenta se va al servidor: el panel solo suma el monto_usd que vino."""
    assert "f.tipo_cambio || 1" not in HTML


def test_el_atajo_desde_presupuesto_precarga_el_movimiento():
    """El botón de la ficha del presupuesto abre el modal ya cargado.

    Un presupuesto aprobado no es plata, es una expectativa: el atajo ahorra
    tipeo pero no dispara nada solo, sigue siendo abrirMovimiento del medio.
    """
    assert "function registrarCobro(" in HTML
    assert "tipo: 'ingreso'" in HTML
    assert "budget_id: cobro.budgetId" in HTML
    assert "showPanel('finanzas')" in HTML
    assert "abrirMovimiento({" in HTML


def test_el_atajo_del_presupuesto_tambien_escapa_el_apostrofo():
    """El nombre del cliente viaja dentro de un onclick con comillas simples:
    un "Bar O'Higgins" sin escapar rompería el atributo."""
    assert "onclick='registrarCobro(${_finAttr(" in HTML


def test_el_panel_tiene_la_vista_de_pauta():
    assert 'id="fin-vista-pauta"' in HTML
    assert "function loadPauta(" in HTML


def test_un_costo_sin_denominador_se_muestra_como_guion():
    """La planilla mostraba #DIV/0!. Un cero ahí sería mentira."""
    assert "function _finNum(" in HTML
    assert "return '—'" in HTML
