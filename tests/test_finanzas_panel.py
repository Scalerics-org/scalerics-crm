"""El panel Finanzas tiene que estar registrado en los seis lugares.

Uno falla en silencio si se olvida: sin la entrada en el ALL_PANELS del
editor de roles no se le puede asignar a nadie. Para el resto de los paneles
hay un séptimo lugar, `_grant_panel_to_existing_roles` en database.py, que
sin él tampoco lo ve nadie en producción -donde la tabla `roles` ya tiene
filas- y es exactamente lo que pasó con `meta`. Finanzas es la excepción a
propósito (Ruling R20): es el único panel que muestra la plata de la
empresa, así que arranca sin nadie asignado y Juan lo reparte a mano desde
el editor de roles, en vez de dárselo a todos los roles que ya existan.
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


def test_database_no_le_da_el_panel_a_los_roles_existentes():
    """A propósito (Ruling R20): a diferencia de los demás paneles nuevos,
    Finanzas no se auto-otorga a los roles que ya existen en producción. Es
    la única sección que muestra la plata de la empresa: arranca sin nadie
    asignado -un admin la ve igual por el bypass de is_admin- y se reparte a
    mano desde el editor de roles."""
    fuente = Path(__file__).resolve().parents[1] / "database.py"
    assert '_grant_panel_to_existing_roles(conn, "finanzas")' not in \
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


def test_el_panel_se_adapta_al_modo_claro():
    """Antes esto miraba que existiera `body.light .fin-card`.

    Ahora la sección usa tokens: `.fin-card` toma `var(--superficie-honda)` y el
    bloque `body.light` redefine ese token. Las once reglas `body.light .fin-*`
    escritas a mano se borraron porque los tokens las cubren — que es la
    ganancia del cambio, no una pérdida de cobertura.
    """
    assert ".fin-card{background:var(--superficie-honda)" in HTML
    assert "--superficie-honda:#fff" in HTML, "el tema claro tiene que redefinirlo"


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


def test_un_fetch_fallido_no_se_muestra_como_lista_vacia():
    """Un 403 o un 500 deserializan a un objeto: sin chequear r.ok, movs.length
    da undefined y el panel muestra "No hay movimientos en el período" -una
    lista vacía de verdad es indistinguible de una que no cargó, y esa mentira
    es peor que mostrar el error. Mismo patrón en loadFijos y en
    _finCargarCategorias, que no tiene su propio contenedor y por eso tira la
    falla para arriba en vez de pintar un mensaje."""
    movs = re.search(r"async function loadMovimientos\(.*?\n\}", HTML, re.S).group(0)
    assert "if (!r.ok)" in movs

    fijos = re.search(r"async function loadFijos\(.*?\n  const fijos = ", HTML, re.S).group(0)
    assert "if (!r.ok)" in fijos

    categorias = re.search(r"async function _finCargarCategorias\(.*?\n\}", HTML, re.S).group(0)
    assert "if (!r.ok)" in categorias


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


def test_el_select_de_cliente_no_desatribuye_en_silencio():
    """crm_group=clientes solo trae cerrado/en_desarrollo/finalizado. Un
    pre-cliente (el atajo de cobro dispara justo en presupuesto_enviado o
    acepto) o un cliente que ya cambió de estado no tienen <option> en esa
    lista: asignarle el value a mano dejaba el <select> en '', y
    guardarMovimiento mandaba client_id: null, borrando la atribución de la
    plata sin que nadie lo pidiera."""
    cuerpo = re.search(r"async function _finCargarClientes\(.*?\n\}", HTML, re.S).group(0)
    assert "function _finCargarClientes(seleccionado, nombre)" in cuerpo
    # Tiene que chequear si falta la <option> ANTES de pisar sel.value, y
    # agregar una sintética con el nombre del prefill en vez de dejar
    # sel.value en '' (que es "Sin atribuir" para guardarMovimiento).
    assert "!sel.querySelector(`option[value=\"${seleccionado}\"]`)" in cuerpo
    assert "createElement('option')" in cuerpo
    assert "finSintetico" in cuerpo
    assert cuerpo.index("createElement('option')") < cuerpo.rindex("sel.value = seleccionado")


def test_el_atajo_de_cobro_manda_el_nombre_del_cliente_al_modal():
    """Sin el nombre en el prefill, el <select> no tiene con qué armar la
    <option> sintética del pre-cliente y cae al placeholder genérico."""
    assert "client_name: cobro.clientName" in HTML
    assert "_finCargarClientes(p.client_id, p.client_name)" in HTML


def test_el_panel_tiene_la_vista_de_pauta():
    assert 'id="fin-vista-pauta"' in HTML
    assert "function loadPauta(" in HTML


def test_cambiar_el_rango_tambien_refresca_pauta_si_esta_abierta():
    """El selector de rango es compartido por las tres vistas. Si el onchange
    llama directo a loadFinanzas(), Pauta se queda mostrando los números de
    un período distinto al que dice el selector, sin recarga y sin aviso."""
    assert 'onchange="loadFinanzas()"' not in HTML
    assert 'onchange="_finRangoCambio()"' in HTML
    assert "function _finRangoCambio(" in HTML
    cuerpo = re.search(r"function _finRangoCambio\(\) \{.*?\n\}", HTML, re.S).group(0)
    assert "loadFinanzas()" in cuerpo
    assert "loadPauta()" in cuerpo


def test_un_costo_sin_denominador_se_muestra_como_guion():
    """La planilla mostraba #DIV/0!. Un cero ahí sería mentira."""
    assert "function _finNum(" in HTML
    assert "return '—'" in HTML


def test_el_modal_del_fijo_tiene_el_toggle_de_iva():
    """El hosting y las herramientas vienen con factura todos los meses. Sin
    el toggle, ese IVA —el más previsible que hay— no se descontaba nunca."""
    assert 'id="fin-fijo-fact-si"' in HTML
    assert 'id="fin-fijo-fact-no"' in HTML
    assert "facturado: _finFijoFacturado" in HTML


def test_el_modal_avisa_que_el_iva_del_fijo_aplica_hacia_adelante():
    """Materializar solo inserta: prenderle el IVA a un fijo que ya corre no
    reescribe el movimiento del mes, que ya existe. Sin este aviso el cambio
    parece no hacer nada y alguien lo vuelve a tocar buscando el error."""
    assert 'id="fin-fijo-iva-nota"' in HTML
    assert "Aplica a los meses que se generen de acá en adelante" in HTML
