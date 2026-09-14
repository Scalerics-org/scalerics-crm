"""Pipeline Notion pasa a llamarse "Proceso de venta" (pedido de Juan, 14/9).

Solo cambia lo que se ve: el menu, el titulo, la barra del celular, la pantalla
de permisos, el texto de Actividad y la nota que queda en el historial de un
negocio cuando pasa a Clientes. Por dentro el panel sigue siendo
`notion_clients`, porque asi estan guardados los permisos de cada rol. El panel
NO se borra: lo unico que se saco ese dia fue Seguimientos.
"""

import re
from pathlib import Path

import dashboard
from services import notion_service as ns

HTML = dashboard.DASHBOARD_HTML
SRC = Path(dashboard.__file__).read_text(encoding="utf-8")


def test_el_menu_y_el_titulo_dicen_proceso_de_venta():
    assert re.search(r'id="nav-notion_clients"[^>]*>.*?Proceso de venta</div>', HTML)
    assert "<h1>Proceso de venta</h1>" in HTML


def test_no_queda_el_nombre_viejo_a_la_vista():
    """Los comentarios del codigo pueden seguir diciendo Pipeline Notion; lo que
    ve la persona, no."""
    assert "> Pipeline Notion</div>" not in HTML
    assert "<h1>Pipeline Notion</h1>" not in HTML
    assert "en el Pipeline Notion" not in SRC
    assert "notion_clients:'Pipeline" not in SRC
    assert "Proceso venta" not in SRC
    assert SRC.count("notion_clients:'Proceso de venta'") == 2  # celular y permisos


def test_el_panel_sigue_existiendo_y_es_notion_clients_por_dentro():
    assert 'id="notion_clients-panel"' in HTML
    assert "showPanel('notion_clients')" in HTML
    for lista in re.findall(r"const ALL_PANELS = \[([^\]]*)\]", SRC):
        assert "'notion_clients'" in lista


def test_el_historial_del_cliente_dice_proceso_de_venta():
    fuente = Path(ns.__file__).read_text(encoding="utf-8")
    cuerpo = fuente.split("def pasar_a_cliente", 1)[1].split("\ndef ", 1)[0]
    assert "Pipeline Notion" not in cuerpo
    assert 'quien: str = "Proceso de venta"' in fuente
    assert "Presupuesto Aceptado en Proceso de venta" in fuente
