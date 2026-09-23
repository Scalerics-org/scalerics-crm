"""La seccion Seguimientos se saco de la interfaz (pedido de Juan, 14/9).

Era solo una vista: listaba los leads en `llamar_despues` e `interesado` con la
misma `/api/leads` que usa todo el CRM. Los estados, las fechas de callback y la
API siguen; lo que se va es el panel, su item de menu, su tarjeta en la Cola,
sus entradas en las listas de paneles y los estilos `cb-*` que solo usaba ella.

Lo que NO se va, y por eso hay tests: `row-llamar_despues` (lo arma la Cola con
`row-${crm_status}`) y los helpers de SDR que vivian bajo el mismo titulo.
"""

import re
from pathlib import Path

from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db

HTML = dashboard.DASHBOARD_HTML
# PANEL_LABELS y el segundo ALL_PANELS viven en la pagina de administracion,
# que no es parte de DASHBOARD_HTML: se miran sobre el archivo entero.
SRC = Path(dashboard.__file__).read_text(encoding="utf-8")


def test_la_seccion_ya_no_esta():
    for resto in ('id="seguimientos-panel"', 'id="nav-seguimientos"',
                  "showPanel('seguimientos')", "loadSeguimientos", "setSegCohorte",
                  'id="stat-seguimientos"', "segTotal", "'seguimientos')",
                  ".cb-date-pill", ".cb-overdue"):
        assert resto not in SRC, resto


def test_no_figura_en_ninguna_lista_de_paneles():
    """Un panel listado sin HTML deja un boton muerto en la barra del celular o
    un permiso que no abre nada."""
    for patron in (r"const NAV_PRIORITY = \[([^\]]*)\]", r"const ALL_PANELS = \[([^\]]*)\]"):
        listas = re.findall(patron, SRC)
        assert listas, patron
        for lista in listas:
            assert "'seguimientos'" not in lista, lista
    for patron in (r"const NAV_ICONS = \{(.*?)\n\}", r"const NAV_LABELS = \{(.*?)\n\}",
                   r"const PANEL_LABELS = \{([^}]*)\}"):
        m = re.search(patron, SRC, re.S)
        assert m, patron
        assert "seguimientos:" not in m.group(1), patron


def test_la_cola_y_lo_compartido_siguen():
    assert 'id="cola-panel"' in HTML
    assert "async function loadColaStats()" in HTML
    # La cola vieja la reemplazo la de Fidelidad (23/9); sus contadores ya no
    # estan y loadColaStats sale sola si no los encuentra.
    assert "if (!document.getElementById('stat-cola')) return;" in HTML
    assert ".row-llamar_despues" in HTML
    assert "function _calendlyBadge(" in HTML
    assert "function _sdrNameColor(" in HTML


def test_la_pagina_abre_y_la_api_de_llamar_despues_sigue(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "seg.db")
    init_db(db)
    app = dashboard.create_app(db)
    uid = create_user(db, name="Jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    cli = app.test_client()
    with cli.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"

    assert cli.get("/").status_code == 200
    assert cli.get("/api/leads?crm_status=llamar_despues&page=1").status_code == 200
