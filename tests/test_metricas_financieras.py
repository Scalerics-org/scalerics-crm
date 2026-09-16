"""Métricas financieras: la sección que antes se llamaba Inteligencia financiera.

Juan (16/9): "Vamos a borrar la seccion inteligencia financiera, no es
eficiente. [...] cambiale el nombre borra lo que contiene ahora y despues en un
futuro le ire agregando metricas".

Lo que se cuida acá:
- Lo que se ve dice "Métricas financieras" (menú, título, barra del celular y
  editor de roles).
- El id interno sigue siendo `inteligencia_fin`: los roles de producción ya lo
  tienen guardado en `panel_access` (Admin y Contador). Cambiarlo les sacaría
  el panel en silencio. Y sigue sin repartirse solo a los roles (Ruling R20).
- El panel está vacío: el título y una línea tranquila, nada más.
- No quedó ningún pedido a los endpoints que se borraron, ni el código de atrás.
- Las tablas que usaba la sección siguen creándose: los datos no se tocan.
"""

import json
import re
import sqlite3
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db

HTML = dashboard.DASHBOARD_HTML
RAIZ = Path(__file__).resolve().parents[1]
SRC = (RAIZ / "dashboard.py").read_text(encoding="utf-8")
TITULO = "Métricas financieras"
INICIO = "<!-- ======= METRICAS FINANCIERAS PANEL ======= -->"
FIN = "<!-- ======= FIN METRICAS FINANCIERAS PANEL ======= -->"


def _panel() -> str:
    i = HTML.index(INICIO)
    return HTML[i:HTML.index(FIN, i)]


# ── el nombre ────────────────────────────────────────────────────────────────

def test_el_menu_dice_metricas_financieras_con_el_mismo_id():
    item = re.search(r'<div class="nav-item" id="nav-inteligencia_fin"[^>]*>(.*?)</div>', HTML)
    assert item, "el item del menu tiene que seguir con el id nav-inteligencia_fin"
    assert "showPanel('inteligencia_fin')" in item.group(0)
    assert item.group(1).strip().endswith(TITULO)


def test_el_titulo_del_panel_dice_metricas_financieras():
    panel = _panel()
    assert 'id="inteligencia_fin-panel"' in panel
    assert re.findall(r"<h1>(.*?)</h1>", panel) == [TITULO]


def test_las_etiquetas_del_celular_y_del_editor_de_roles():
    nav = re.search(r"const NAV_LABELS = \{([^}]*)\}", SRC).group(1)
    roles = re.search(r"const PANEL_LABELS = \{([^}]*)\}", SRC).group(1)
    assert f"inteligencia_fin:'{TITULO}'" in nav
    assert f"inteligencia_fin:'{TITULO}'" in roles


def test_no_queda_el_nombre_viejo_a_la_vista():
    for viejo in ("Inteligencia financiera", "Intel. financiera", "Inteligencia Financiera"):
        # Solo puede quedar en el comentario HTML que explica el id.
        fuera_de_comentarios = re.sub(r"<!--.*?-->", "", HTML, flags=re.S)
        assert viejo not in fuera_de_comentarios, viejo


def test_el_id_sigue_registrado_en_todos_lados():
    listas = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", SRC)
    assert len(listas) == 2 and all("'inteligencia_fin'" in lista for lista in listas)
    assert "'inteligencia_fin'" in re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", SRC).group(1)
    assert "inteligencia_fin:'lightbulb'" in re.search(r"const NAV_ICONS = \{([^}]*)\}", SRC).group(1)


def test_sigue_sin_repartirse_solo_a_los_roles():
    """Ruling R20: es un panel de plata, lo tilda Juan en el editor de roles."""
    fuente = (RAIZ / "database.py").read_text(encoding="utf-8")
    assert '_grant_panel_to_existing_roles(conn, "inteligencia_fin"' not in fuente


# ── vacío ────────────────────────────────────────────────────────────────────

def test_el_panel_esta_vacio_y_tranquilo():
    panel = _panel()
    visible = re.sub(r"<!--.*?-->", "", panel, flags=re.S)
    texto = [t.strip() for t in re.sub(r"<[^>]+>", "\n", visible).split("\n") if t.strip()]
    assert texto == [TITULO, "Acá van a ir las métricas financieras."]
    for resto in ("<button", "<section", "<details", "<input", "<select", "onclick", "ifn"):
        assert resto not in visible, resto


def test_mostrar_el_panel_no_carga_nada():
    assert "name === 'inteligencia_fin'" not in HTML


def test_sin_trampas_de_jinja_en_el_panel():
    panel = _panel()
    for trampa in ("{#", "{{", "{%"):
        assert trampa not in panel
    assert chr(92) not in panel


# ── lo de atrás, borrado ─────────────────────────────────────────────────────

BORRADOS = ("/api/inteligencia-fin", "/api/perdidas/", "/esfuerzo", "/api/ventas/")


def test_ningun_js_llama_a_un_endpoint_borrado():
    for url in BORRADOS:
        assert url not in HTML, url
    assert not re.search(r"\bifn[A-Z]\w*", HTML)
    assert "IFN_" not in HTML and "ifn-" not in HTML


@pytest.mark.parametrize("modulo", ["routes/inteligencia_fin.py", "services/inteligencia_fin.py",
                                    "services/inteligencia_fin_ia.py", "services/intel_objetivo.py"])
def test_los_modulos_de_la_seccion_no_estan(modulo):
    assert not (RAIZ / modulo).exists()


def test_nadie_importa_los_modulos_borrados():
    for archivo in list(RAIZ.glob("*.py")) + list((RAIZ / "routes").glob("*.py")) + \
            list((RAIZ / "services").glob("*.py")) + list((RAIZ / "scripts").glob("*.py")):
        texto = archivo.read_text(encoding="utf-8", errors="ignore")
        for nombre in ("inteligencia_fin", "intel_objetivo"):
            assert not re.search(rf"(import|from)\s+[\w.]*{nombre}\b", texto), (archivo.name, nombre)
            assert f"import {nombre}" not in texto, (archivo.name, nombre)


def test_no_hay_hilo_de_fondo():
    assert "start_inteligencia_fin" not in SRC


# ── de punta a punta ─────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    db = str(tmp_path / "mf.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _cli(app, email, rol_id=None):
    db = app.config["_DB"]
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if rol_id:
        conn = sqlite3.connect(db)
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (rol_id, uid))
        conn.commit()
        conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = email.split("@")[0]
    return c


def test_la_pagina_se_pinta_con_el_panel_vacio(app):
    r = _cli(app, "raiz@scalerics.com").get("/")
    assert r.status_code == 200
    cuerpo = r.get_data(as_text=True)
    assert 'id="inteligencia_fin-panel"' in cuerpo and f"<h1>{TITULO}</h1>" in cuerpo


def test_los_endpoints_viejos_ya_no_existen(app):
    cli = _cli(app, "raiz@scalerics.com")
    assert cli.get("/api/inteligencia-fin").status_code == 404
    assert cli.post("/api/inteligencia-fin/recalcular").status_code == 404
    assert cli.put("/api/inteligencia-fin/objetivo", json={}).status_code == 404


def test_el_contador_sigue_viendo_el_panel(app):
    """Como en producción: el rol Contador con `inteligencia_fin` tildado."""
    db = app.config["_DB"]
    conn = sqlite3.connect(db)
    rol = conn.execute("SELECT id, panel_access FROM roles WHERE name = 'Contador'").fetchone()
    conn.execute("UPDATE roles SET panel_access = ? WHERE id = ?",
                 (json.dumps(json.loads(rol[1]) + ["finanzas", "inteligencia_fin"]), rol[0]))
    conn.commit()
    conn.close()
    me = _cli(app, "contador@scalerics.com", rol_id=rol[0]).get("/api/me").get_json()
    # panel_access viaja como string JSON, igual que se guarda en el rol.
    assert "inteligencia_fin" in json.loads(me["panel_access"])
    assert me["is_admin"] is False


TABLAS_QUE_QUEDAN = ("perdidas_motivo", "proyectos_esfuerzo", "ventas_origen_manual", "fijos_canal",
                     "if_supuestos", "if_calculos", "if_recomendaciones", "if_recomendaciones_tomadas",
                     "if_objetivo", "if_equipo_costos", "if_costos_activos", "if_gastos_esperados",
                     "if_fijo_clasificacion")


def test_las_tablas_de_la_seccion_siguen_en_la_base(tmp_path):
    """Los datos se guardan: Juan puede reusar esos números para las métricas."""
    db = str(tmp_path / "tablas.db")
    init_db(db)
    conn = sqlite3.connect(db)
    tablas = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert set(TABLAS_QUE_QUEDAN) <= tablas
