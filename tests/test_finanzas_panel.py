"""El panel Finanzas tiene que estar registrado en los siete lugares.

Dos de los siete fallan en silencio si se olvidan: sin la entrada en el
ALL_PANELS del editor de roles no se le puede asignar a nadie, y sin el
_grant_panel_to_existing_roles en database.py no lo ve nadie en producción,
donde la tabla `roles` ya tiene filas. Es exactamente lo que pasó con `meta`.
"""

import re
from pathlib import Path

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
