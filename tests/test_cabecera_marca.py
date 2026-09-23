"""La cabecera de marca del CRM y los colores del menu (pedido de Juan, 14/9).

- La frase de equipo, en su version compacta del PDF "Frase de equipo -
  Scalerics": franja oscura de marca (#0F2430), texto claro y la ultima oracion
  en verde de marca (#80CD2A). Va arriba de todos los paneles.
- El logo de la franja es el mismo archivo que ya usa el CRM (el del sidebar),
  no el isotipo aproximado de la maqueta.
- Cada seccion del menu tiene su color de icono.
- SDR paso de VENTAS a CAPTACION.
- Lo primero que se abre sigue siendo el Calendario.
"""

import re

from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db

HTML = dashboard.DASHBOARD_HTML
FRASE = "La IA avanza rápido, es cierto. Pero el mercado la entiende lento."
REMATE = "Ahí están nuestras oportunidades."


def _franja() -> str:
    m = re.search(r'<div class="frase-equipo"[^>]*>.*?</div>', HTML, re.S)
    assert m, "no esta la franja de la frase de equipo"
    return m.group(0)


def test_la_frase_de_equipo_esta_una_sola_vez_arriba_de_los_paneles():
    assert HTML.count('class="frase-equipo"') == 1
    franja = _franja()
    assert FRASE in franja
    assert f"<strong>{REMATE}</strong>" in franja
    main = HTML.index('<div class="main">')
    primer_panel = HTML.index('class="panel', main)
    assert main < HTML.index('class="frase-equipo"') < primer_panel


def test_el_logo_de_la_franja_es_el_del_crm():
    logo_crm = re.search(r'id="sidebar-logo" src="([^"]+)"', HTML).group(1)
    logo_franja = re.search(r'class="frase-equipo-logo" src="([^"]+)"', _franja()).group(1)
    assert logo_franja == logo_crm


def test_la_franja_usa_la_paleta_de_marca():
    assert re.search(r"\.frase-equipo\{[^}]*background:#0F2430", HTML)
    assert re.search(r"\.frase-equipo-texto strong\{[^}]*color:#80CD2A", HTML)
    assert re.search(r"\.frase-equipo-texto\{[^}]*color:#EFEFEF", HTML)


def test_cada_seccion_del_menu_tiene_su_color():
    bloque = HTML[HTML.index('<div class="nav-scroll">'):HTML.index('<div class="sidebar-bottom">')]
    paneles = re.findall(r'id="nav-([\w]+)"', bloque)
    assert len(paneles) >= 15
    sin_color = [p for p in paneles if not re.search(r"#nav-" + p + r" \.nav-icon\{stroke:#", HTML)]
    assert not sin_color, f"sin color de icono: {sin_color}"
    claros = [p for p in paneles if not re.search(r"body\.light #nav-" + p + r" \.nav-icon\{stroke:#", HTML)]
    assert not claros, f"sin color en tema claro: {claros}"


def test_captacion_es_de_fidelidad_y_sin_sdr():
    # 23/9: Captación pasó a ser de Scalerics Fidelidad y SDR salió del menú.
    bloque = HTML[HTML.index('nav-section-label">CAPTACIÓN'):HTML.index('<div class="sidebar-bottom">')]
    assert 'id="nav-cola"' in bloque and 'id="nav-metrics"' in bloque
    assert 'id="nav-sdr"' not in HTML


def test_lo_primero_sigue_siendo_el_calendario():
    assert '<div class="nav-item active" id="nav-cal"' in HTML
    assert '<div id="cal-panel" class="panel active">' in HTML
    assert "{#" not in HTML


def test_la_pagina_abre_con_la_franja(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "marca.db")
    init_db(db)
    app = dashboard.create_app(db)
    uid = create_user(db, name="Jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    cli = app.test_client()
    with cli.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    r = cli.get("/")
    assert r.status_code == 200
    assert "Ahí están nuestras oportunidades.".encode("utf-8") in r.data
