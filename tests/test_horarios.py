"""Recursos Humanos > Horarios: el horario de trabajo de cada programador.

Pedido de Juan (15/9), en 24 h: Gonzalo de lunes a viernes de 12:00 a 16:00;
Juan lunes 11:00-15:00, martes 10:00-14:00, miércoles 14:20-18:30, jueves
14:30-18:30 y viernes 11:00-15:00. Se puede editar, pero arranca con eso.
"""

import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import dashboard
import database
from database import create_user, init_db, listar_personas_equipo, reemplazar_horario_persona
from services.horarios import duracion_texto, estado, validar_semana

HTML = dashboard.DASHBOARD_HTML
RAIZ = Path(__file__).resolve().parents[1]
SRC = (RAIZ / "dashboard.py").read_text(encoding="utf-8")

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")


def _entre(texto: str, desde: str, hasta: str) -> str:
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


PANEL = _entre(SRC, "<!-- ======= HORARIOS PANEL ======= -->", "<!-- ======= FIN HORARIOS PANEL ======= -->")
MODALES = _entre(SRC, "<!-- ======= HORARIOS MODALES ======= -->", "<!-- ======= FIN HORARIOS MODALES ======= -->")
JS = _entre(SRC, "// ========== Horarios ==========", "// ========== FIN Horarios ==========")
CSS = _entre(SRC, "/* ── Horarios", "/* ── Plantillas")
FUENTES = {"panel": PANEL, "modales": MODALES, "js": JS, "css": CSS}

L_V = [0, 1, 2, 3, 4]
GONZALO = {d: [("12:00", "16:00")] for d in L_V}
JUAN = {0: [("11:00", "15:00")], 1: [("10:00", "14:00")], 2: [("14:20", "18:30")],
        3: [("14:30", "18:30")], 4: [("11:00", "15:00")]}


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    db = str(tmp_path / "horarios.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _rol(db, nombre, paneles):
    conn = sqlite3.connect(db)
    try:
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                           (nombre, json.dumps(paneles))).lastrowid
        conn.commit()
        return rid
    finally:
        conn.close()


def _usuario(db, email, role_id=None):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if role_id is not None:
        conn = sqlite3.connect(db)
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (role_id, uid))
        conn.commit()
        conn.close()
    return uid


def _cli(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "test"
    return c


@pytest.fixture
def cli(app):
    return _cli(app, _usuario(app.config["_DB"], "jefe@scalerics.com"))


def _db(cli):
    return cli.application.config["_DB"]


def _id(db, nombre):
    return next(p["id"] for p in listar_personas_equipo(db, incluir_inactivas=True)
                if p["nombre"] == nombre)


def _semana(tramos_por_dia):
    """{día: [(desde, hasta)]} -> el cuerpo que manda el modal."""
    return {"dias": [[{"desde": a, "hasta": b} for a, b in tramos_por_dia.get(d, [])]
                     for d in range(7)]}


def _como_dict(fila):
    return {d["dia"]: [(t["desde"], t["hasta"]) for t in d["tramos"]]
            for d in fila["dias"] if d["tramos"]}


def _fila(est, nombre_corto):
    return next(p for p in est["personas"] if p["nombre_corto"] == nombre_corto)


def _filas_db(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT persona_id, dia, desde, hasta FROM horarios_tramos "
                            "ORDER BY persona_id, dia, desde").fetchall()
    finally:
        conn.close()


def _olvidar_reparto(conn, panel):
    """Con los repartos de una sola vez (tabla `panel_grants_aplicados`), una
    base "de antes" es una sin la marca de ese panel. Si la tabla todavía no
    existe, no hay nada que olvidar."""
    try:
        conn.execute("DELETE FROM panel_grants_aplicados WHERE panel = ?", (panel,))
    except sqlite3.OperationalError:
        pass


# ── precarga ─────────────────────────────────────────────────────────────────

def test_la_precarga_es_exactamente_la_de_juan(tmp_path):
    db = str(tmp_path / "h.db")
    init_db(db)
    est = estado(db)
    assert [p["nombre_corto"] for p in est["personas"]] == ["Juan", "Gonzalo"]
    assert _como_dict(_fila(est, "Gonzalo")) == GONZALO
    assert _como_dict(_fila(est, "Juan")) == JUAN
    assert len(_filas_db(db)) == 10
    assert est["visibles"] == L_V, "nadie trabaja el fin de semana"


def test_la_precarga_no_duplica(tmp_path):
    db = str(tmp_path / "h.db")
    init_db(db)
    antes = _filas_db(db)
    init_db(db)
    init_db(db)
    assert _filas_db(db) == antes and len(antes) == 10


def test_la_precarga_no_pisa_lo_editado(tmp_path):
    db = str(tmp_path / "h.db")
    init_db(db)
    gonzalo, juan = _id(db, "Gonzalo Siuciak"), _id(db, "Juan Tomasetti")
    reemplazar_horario_persona(db, gonzalo, [(0, "09:00", "12:00"), (0, "14:00", "18:00")])
    reemplazar_horario_persona(db, juan, [])  # "no trabaja" toda la semana
    init_db(db)
    est = estado(db)
    assert _como_dict(_fila(est, "Gonzalo")) == {0: [("09:00", "12:00"), ("14:00", "18:00")]}
    assert _como_dict(_fila(est, "Juan")) == {}, "sin tramos no se vuelve a precargar"


def test_la_precarga_busca_por_primer_nombre_si_cambio_el_nombre(tmp_path):
    db = str(tmp_path / "h.db")
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM horarios_tramos")
        conn.execute("DELETE FROM horarios_precarga_hecha")
        conn.execute("UPDATE equipo_personas SET nombre='Juan Manuel Tomasetti' WHERE nombre='Juan Tomasetti'")
        conn.commit()
        assert database._sembrar_horarios(conn) == 2
        # Juan Pereyra no lleva horas: el único Juan candidato es el programador.
        assert _como_dict(_fila(estado(db), "Juan")) == JUAN

        # Con dos Gonzalo que llevan horas no se adivina.
        conn.execute("DELETE FROM horarios_tramos")
        conn.execute("DELETE FROM horarios_precarga_hecha")
        conn.execute("UPDATE equipo_personas SET nombre='Gonzalo S.' WHERE nombre='Gonzalo Siuciak'")
        conn.execute("INSERT INTO equipo_personas (nombre, lleva_horas) VALUES ('Gonzalo Otro', 1)")
        conn.commit()
        assert database._sembrar_horarios(conn) == 1
        hechas = {f[0] for f in conn.execute("SELECT nombre FROM horarios_precarga_hecha")}
        assert hechas == {"Juan Tomasetti"}, "Gonzalo queda para el próximo arranque"
    finally:
        conn.close()


# ── totales ──────────────────────────────────────────────────────────────────

def test_los_totales_de_la_precarga(tmp_path):
    db = str(tmp_path / "h.db")
    init_db(db)
    est = estado(db)
    juan, gonzalo = _fila(est, "Juan"), _fila(est, "Gonzalo")
    assert [d["minutos"] for d in juan["dias"]] == [240, 240, 250, 240, 240, 0, 0]
    assert juan["dias"][2]["texto"] == "4 h 10 min"
    assert juan["minutos_semana"] == 1210 and juan["texto_semana"] == "20 h 10 min"
    assert gonzalo["minutos_semana"] == 1200 and gonzalo["texto_semana"] == "20 h"
    assert juan["dias"][5]["texto"] == "" and juan["dias"][5]["nombre"] == "sábado"


@pytest.mark.parametrize("minutos,texto", [(0, "0 h"), (45, "45 min"), (60, "1 h"),
                                            (250, "4 h 10 min"), (1210, "20 h 10 min")])
def test_duracion_texto(minutos, texto):
    assert duracion_texto(minutos) == texto


# ── validación ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lunes,parte", [
    ([("9:00", "12:00")], "HH:MM"),
    ([("09:00", "24:00")], "HH:MM"),
    ([("09:60", "12:00")], "HH:MM"),
    ([("", "12:00")], "HH:MM"),
    ([(None, "12:00")], "HH:MM"),
    ([(900, "12:00")], "HH:MM"),
    ([("09:00", "12:00 ")], "HH:MM"),
    ([("12:00", "12:00")], "antes que hasta"),
    ([("14:00", "12:00")], "antes que hasta"),
    ([("09:00", "12:00"), ("11:30", "13:00")], "se superponen"),
    ([("09:00", "18:00"), ("10:00", "11:00")], "se superponen"),
    ([("14:00", "18:00"), ("09:00", "14:30")], "se superponen"),
    ([("0%d:00" % h, "0%d:30" % h) for h in range(7)], "hasta 6 tramos"),
])
def test_el_horario_se_valida_en_el_servidor(cli, lunes, parte):
    db = _db(cli)
    antes = _filas_db(db)
    r = cli.put(f"/api/horarios/{_id(db, 'Gonzalo Siuciak')}", json=_semana({0: lunes}))
    assert r.status_code == 400, r.get_json()
    error = r.get_json()["error"]
    assert parte in error and error.startswith("lunes"), error
    assert _filas_db(db) == antes, "con un error no se guarda nada"


@pytest.mark.parametrize("cuerpo,parte", [
    (None, "faltan"),
    ({}, "faltan"),
    ({"dias": "lunes"}, "faltan"),
    ({"dias": [[]] * 6}, "7 días"),
    ({"dias": [[]] * 8}, "7 días"),
    ({"dias": ["x"] + [[]] * 6}, "mal armados"),
    ({"dias": [["x"]] + [[]] * 6}, "mal armados"),
])
def test_un_cuerpo_mal_armado_da_400(cli, cuerpo, parte):
    db = _db(cli)
    url = f"/api/horarios/{_id(db, 'Gonzalo Siuciak')}"
    r = cli.put(url, json=cuerpo) if cuerpo is not None else cli.put(url, data="no es json")
    assert r.status_code == 400 and parte in r.get_json()["error"], r.get_json()
    assert len(_filas_db(db)) == 10


def test_tramos_que_se_tocan_no_se_superponen():
    tramos, error = validar_semana(_semana({2: [("14:00", "18:00"), ("09:00", "14:00")]}))
    assert error is None
    assert tramos == [(2, "09:00", "14:00"), (2, "14:00", "18:00")], "se guardan en orden"


# ── editar ───────────────────────────────────────────────────────────────────

def test_editar_agregar_y_quitar_tramos(cli):
    db = _db(cli)
    gid = _id(db, "Gonzalo Siuciak")
    url = f"/api/horarios/{gid}"

    # Lunes con dos tramos (llegan desordenados), martes no trabaja, sábado sí.
    semana = dict(GONZALO)
    semana[0] = [("14:00", "18:00"), ("09:00", "12:00")]
    semana[1] = []
    semana[5] = [("10:00", "13:00")]
    r = cli.put(url, json=_semana(semana))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["persona"]["texto_semana"] == "22 h"

    est = cli.get("/api/horarios").get_json()
    g = _fila(est, "Gonzalo")
    assert g["dias"][0]["tramos"] == [{"desde": "09:00", "hasta": "12:00"},
                                      {"desde": "14:00", "hasta": "18:00"}]
    assert g["dias"][0]["texto"] == "7 h" and g["dias"][1]["tramos"] == []
    assert g["minutos_semana"] == 7 * 60 + 3 * 4 * 60 + 3 * 60
    assert est["visibles"] == [0, 1, 2, 3, 4, 5], "aparece el sábado y no el domingo"
    assert _como_dict(_fila(est, "Juan")) == JUAN, "no toca a nadie más"

    # Quitar el segundo tramo del lunes y el sábado.
    semana[0] = [("09:00", "12:00")]
    semana[5] = []
    assert cli.put(url, json=_semana(semana)).status_code == 200
    est = cli.get("/api/horarios").get_json()
    assert _fila(est, "Gonzalo")["dias"][0]["tramos"] == [{"desde": "09:00", "hasta": "12:00"}]
    assert _fila(est, "Gonzalo")["texto_semana"] == "15 h"
    assert est["visibles"] == L_V

    conn = sqlite3.connect(db)
    accion = conn.execute("SELECT action, entity_name FROM activity_log "
                          "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert accion == ("horario_editado", "Gonzalo Siuciak")


def test_solo_se_edita_a_quien_esta_en_horarios(cli):
    db = _db(cli)
    assert cli.put(f"/api/horarios/{_id(db, 'Matías Domínguez')}", json=_semana({})).status_code == 404
    assert cli.put("/api/horarios/9999", json=_semana({})).status_code == 404
    assert len(_filas_db(db)) == 10


# ── permisos ─────────────────────────────────────────────────────────────────

def test_sin_el_panel_da_403(app):
    db = app.config["_DB"]
    for paneles in (["cola"], ["equipo", "ausencias"]):
        c = _cli(app, _usuario(db, f"x{len(paneles)}@scalerics.com", _rol(db, f"R{len(paneles)}", paneles)))
        assert c.get("/api/horarios").status_code == 403
        assert c.put(f"/api/horarios/{_id(db, 'Gonzalo Siuciak')}", json=_semana(GONZALO)).status_code == 403


def test_con_el_panel_se_ve_y_se_edita(app):
    """Como en Organigrama y Ausencias: quien tiene el panel, edita."""
    db = app.config["_DB"]
    c = _cli(app, _usuario(db, "prog@scalerics.com", _rol(db, "Programador", ["horarios"])))
    assert c.get("/api/horarios").status_code == 200
    r = c.put(f"/api/horarios/{_id(db, 'Gonzalo Siuciak')}", json=_semana({0: [("12:00", "16:00")]}))
    assert r.status_code == 200
    pagina = c.get("/")
    assert pagina.status_code == 200 and 'id="horarios-panel"' in pagina.get_data(as_text=True)


def test_sin_sesion_no_entra(app):
    assert app.test_client().get("/api/horarios").status_code in (401, 302)


def test_los_roles_reciben_el_panel_una_sola_vez(tmp_path):
    db = str(tmp_path / "h.db")
    init_db(db)
    conn = sqlite3.connect(db)
    acceso = {n: json.loads(p) for n, p in conn.execute("SELECT name, panel_access FROM roles")}
    assert acceso and all("horarios" in p for p in acceso.values()), acceso

    # Si Juan se lo saca a un rol, un arranque no se lo vuelve a poner.
    sin = [p for p in acceso["Caller"] if p != "horarios"]
    conn.execute("UPDATE roles SET panel_access=? WHERE name='Caller'", (json.dumps(sin),))
    conn.commit()
    conn.close()
    init_db(db)
    conn = sqlite3.connect(db)
    caller = json.loads(conn.execute("SELECT panel_access FROM roles WHERE name='Caller'").fetchone()[0])
    conn.close()
    assert "horarios" not in caller


def test_el_panel_llega_a_quien_tiene_organigrama_o_ausencias(tmp_path):
    db = str(tmp_path / "h.db")
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TABLE horarios_tramos")
        for nombre, paneles in (("Caller", ["cola", "equipo"]), ("Ventas", ["ausencias"]),
                                ("Admin", ["meta"])):
            conn.execute("UPDATE roles SET panel_access=? WHERE name=?", (json.dumps(paneles), nombre))
        conn.commit()
        _olvidar_reparto(conn, "horarios")
        database._grant_panel_to_existing_roles(conn, "horarios", si_tiene=("equipo", "ausencias"))
        acceso = {n: json.loads(p) for n, p in conn.execute("SELECT name, panel_access FROM roles")}
    finally:
        conn.close()
    assert acceso["Caller"] == ["cola", "equipo", "horarios"]
    assert acceso["Ventas"] == ["ausencias", "horarios"]
    assert acceso["Admin"] == ["meta"]
    fuente = (RAIZ / "database.py").read_text(encoding="utf-8")
    assert '_grant_panel_to_existing_roles(conn, "horarios", si_tiene=("equipo", "ausencias"))' in fuente


# ── registrado en todos lados ────────────────────────────────────────────────

def test_esta_en_recursos_humanos_despues_de_flujos():
    menu = HTML[HTML.index('<div class="nav-scroll">'):HTML.index('<div class="sidebar-bottom">')]
    grupo = _entre(menu, '<div class="nav-section-label">RECURSOS HUMANOS</div>',
                   '<div class="nav-section-label">CAPTACIÓN</div>')
    assert re.findall(r'id="nav-(\w+)"', grupo) == ["equipo", "ausencias", "flujos", "horarios"]
    assert ('<div class="nav-item" id="nav-horarios" onclick="showPanel(\'horarios\')">'
            '<i data-lucide="clock-4" class="nav-icon"></i> Horarios</div>') in grupo


def test_esta_registrado_en_todos_lados():
    prioridad = re.findall(r"'(\w+)'", re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", HTML).group(1))
    assert prioridad.index("horarios") == prioridad.index("flujos") + 1
    assert "horarios:'clock-4'" in re.search(r"const NAV_ICONS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "horarios:'Horarios'" in re.search(r"const NAV_LABELS = \{(.*?)\n\}", HTML, re.S).group(1)
    listas = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", SRC)
    assert len(listas) == 2 and all("'horarios'" in l for l in listas)
    assert "horarios:'Horarios'" in re.search(r"const PANEL_LABELS = \{([^}]*)\}", SRC).group(1)
    assert "  if (name === 'horarios') hrCargar();" in HTML
    assert "from routes.horarios import horarios_bp" in SRC
    assert "horarios_bp" in re.search(r"for bp in \((.*?)\):", SRC, re.S).group(1)


def test_el_icono_tiene_un_color_propio_en_los_dos_temas():
    """Ningún otro ítem del menú usa el mismo color: ni en oscuro, ni activo, ni
    en claro (Daily entró con el celeste que tenía Horarios)."""
    for patron in (r"^#nav-(\w+) \.nav-icon\{stroke:(#[0-9a-f]+)\}",
                   r"^#nav-(\w+)\.active \.nav-icon\{stroke:(#[0-9a-f]+)\}",
                   r"^body\.light #nav-(\w+) \.nav-icon\{stroke:(#[0-9a-f]+)\}"):
        colores = dict(re.findall(patron, HTML, re.M))
        propio = colores.pop("horarios")
        assert propio not in colores.values(), (patron, propio, colores)


def test_la_carga_inicial_sigue_siendo_el_calendario():
    m = re.search(r"// Initial load.*?\n([^/\s].*?)\n", HTML, re.S)
    assert m.group(1) == "calLoaded = true; renderCalendar();"
    assert "setInterval" not in JS


@pytest.mark.parametrize("nombre", sorted(FUENTES))
def test_nada_que_jinja_o_python_interpreten(nombre):
    texto = FUENTES[nombre]
    for trampa in ("{#", "{{", "{%"):
        assert trampa not in texto, f"{trampa!r} en el {nombre} de Horarios"
    assert chr(92) not in texto, f"un backslash en el {nombre}: Python se lo come"


def test_el_css_usa_solo_tokens():
    reglas = re.findall(r"([^{}]+)\{([^{}]*)\}", CSS)
    assert len(reglas) > 20
    for selector, cuerpo in reglas:
        assert not re.search(r"#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|rgba?\(", cuerpo), selector.strip()
    assert "body.light" not in CSS.split("*/", 1)[1]
    for nombre in ("panel", "modales", "js"):
        assert "style=" not in FUENTES[nombre], f"estilo inline en el {nombre} de Horarios"


def test_todo_lo_del_js_lleva_el_prefijo_hr():
    nombres = re.findall(r"^(?:async )?function ([A-Za-z_$][\w$]*)\(", JS, re.M)
    nombres += re.findall(r"^(?:const|let) ([A-Za-z_$][\w$]*)", JS, re.M)
    assert len(nombres) > 10
    sueltos = [n for n in nombres if not (n.startswith("hr") or n.startswith("HR_"))]
    assert not sueltos, f"sin prefijo: {sueltos}"
    for nombre in nombres:
        declaraciones = re.findall(r"^(?:async )?(?:function|const|let) " + re.escape(nombre) + r"\b",
                                   HTML, re.M)
        assert len(declaraciones) == 1, f"{nombre} está declarado {len(declaraciones)} veces"


def test_la_pagina_abre(cli):
    r = cli.get("/")
    assert r.status_code == 200
    pagina = r.get_data(as_text=True)
    assert pagina.count('id="horarios-panel"') == 1 and pagina.count('id="hr-modal-editor"') == 1


# ── se pinta de verdad ───────────────────────────────────────────────────────

_ARNES = r"""
const _els = {};
function _el(id) {
  if (!_els[id]) {
    _els[id] = { id, innerHTML: '', textContent: '', value: '', placeholder: '', type: '',
                 className: '', style: {}, dataset: {}, options: [], selectedIndex: 0,
                 classList: { _c: new Set(), add(c){ this._c.add(c); }, remove(c){ this._c.delete(c); },
                              toggle(){}, contains(c){ return this._c.has(c); } },
                 querySelectorAll: () => [], querySelector: () => null, closest: () => null,
                 setAttribute(){}, getAttribute: () => null, focus(){},
                 addEventListener(){}, appendChild(){}, remove(){} };
  }
  return _els[id];
}
globalThis.document = {
  getElementById: _el, querySelectorAll: () => [], querySelector: () => null,
  body: { classList: { contains: () => false, add(){}, remove(){}, toggle(){} } },
  createElement: () => _el('tmp'), addEventListener(){},
};
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem(){}, removeItem(){} };
globalThis.lucide = { createIcons(){} };
globalThis.alert = () => {};
globalThis.confirm = () => true;
globalThis.setInterval = () => 0;
const _RESPUESTAS = __RESPUESTAS__;
const _pedidos = [];
globalThis.fetch = (url, opciones) => {
  _pedidos.push([String(url), (opciones && opciones.method) || 'GET', (opciones && opciones.body) || null]);
  const r = _RESPUESTAS[String(url)];
  if (r === 'falla') return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r === undefined ? {} : r) });
};
"""


def _correr_js(tmp_path, respuestas, prueba):
    bloques = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    archivo = tmp_path / "horarios.js"
    archivo.write_text(_ARNES.replace("__RESPUESTAS__", json.dumps(respuestas, ensure_ascii=False))
                       + "\n".join(bloques) + "\n" + prueba, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:2000]
    return json.loads(r.stdout.strip().splitlines()[-1])


@sin_node
def test_la_pantalla_y_el_editor_se_pintan(cli, tmp_path):
    db = _db(cli)
    juan = _id(db, "Juan Tomasetti")
    est = cli.get("/api/horarios").get_json()
    url = f"/api/horarios/{juan}"

    prueba = """
(async () => {
  const s = {};
  showPanel('horarios');
  await new Promise(r => setTimeout(r, 20));
  s.pantalla = _el('hr-contenido').innerHTML;

  hrAbrirEditor(__JUAN__);
  s.abierto = _el('hr-modal-editor').classList.contains('open');
  s.titulo = _el('hr-editor-titulo').textContent;
  s.editor = _el('hr-editor-dias').innerHTML;

  hrAgregarTramo(0);
  s.agregado = JSON.parse(JSON.stringify(hrEdicion.dias[0]));
  const antes = _pedidos.length;
  hrCambiarTramo(0, 1, 0, '14:00');
  await hrGuardar();
  s.errorSuperpone = _el('hr-editor-error').textContent;
  hrCambiarTramo(0, 1, 0, '17:00');
  await hrGuardar();
  s.errorOrden = _el('hr-editor-error').textContent;
  hrCambiarTramo(0, 1, 0, '9:00');
  await hrGuardar();
  s.errorFormato = _el('hr-editor-error').textContent;
  s.putsInvalidos = _pedidos.slice(antes).filter(p => p[1] === 'PUT').length;

  hrQuitarTramo(0, 1);
  hrNoTrabaja(4, true);
  hrNoTrabaja(5, false);
  s.editorFinal = _el('hr-editor-dias').innerHTML;
  await hrGuardar();
  s.put = _pedidos.filter(p => p[1] === 'PUT').pop();
  s.ultimo = _pedidos[_pedidos.length - 1];
  s.cerrado = hrEdicion === null && !_el('hr-modal-editor').classList.contains('open');
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""".replace("__JUAN__", str(juan))
    s = _correr_js(tmp_path, {"/api/horarios": est, url: {"ok": True}}, prueba)

    p = s["pantalla"]
    assert "Juan" in p and "Gonzalo" in p
    assert "Tomasetti" not in p and "Siuciak" not in p, "en pantalla va el primer nombre"
    assert p.count('class="hr-col-dia"') == 5 and "Sáb" not in p
    assert p.count("12:00–16:00") == 10, "cinco en la grilla y cinco en la tarjeta"
    assert "14:20–18:30" in p and "14:30–18:30" in p and "4 h 10 min" in p
    assert '<td class="hr-total"><span class="hr-total-chip">20 h 10 min</span></td>' in p
    assert '<td class="hr-total"><span class="hr-total-chip">20 h</span></td>' in p
    assert len(re.findall(r'<article class="hr-tarjeta hr-color-\d">', p)) == 2
    assert '<span class="hr-total-chip">20 h 10 min</span> por semana' in p
    assert p.count('onclick="hrAbrirEditor(') == 4 and "No trabaja" not in p

    assert s["abierto"] and s["titulo"] == "Horario de Juan"
    assert s["editor"].count('type="time"') == 10
    assert s["editor"].count(" checked") == 2, "sábado y domingo arrancan en no trabaja"
    assert s["editor"].count("+ Agregar tramo") == 5

    assert s["agregado"] == [{"desde": "11:00", "hasta": "15:00"}, {"desde": "15:00", "hasta": "16:00"}]
    assert "se superponen" in s["errorSuperpone"] and s["errorSuperpone"].startswith("Lunes")
    assert "desde tiene que ser antes que hasta" in s["errorOrden"]
    assert "HH:MM" in s["errorFormato"]
    assert s["putsInvalidos"] == 0, "con datos inválidos no se manda nada"
    assert s["editorFinal"].count(" checked") == 2, "viernes no trabaja, sábado sí, domingo no"

    put_url, metodo, cuerpo = s["put"]
    assert (put_url, metodo) == (url, "PUT")
    cuerpo = json.loads(cuerpo)
    assert cuerpo["dias"][0] == [{"desde": "11:00", "hasta": "15:00"}]
    assert cuerpo["dias"][4] == [] and cuerpo["dias"][5] == [{"desde": "09:00", "hasta": "13:00"}]
    assert s["ultimo"][0] == "/api/horarios" and s["cerrado"], "guarda, cierra y recarga"

    # Lo que manda el modal lo acepta el servidor: 4 + 4 + 4 h 10 + 4 + 0 + 4 (sábado).
    r = cli.put(url, json=cuerpo)
    assert r.status_code == 200 and r.get_json()["persona"]["texto_semana"] == "20 h 10 min"


@sin_node
def test_el_sabado_aparece_si_alguien_trabaja_y_una_falla_se_avisa(cli, tmp_path):
    db = _db(cli)
    semana = dict(GONZALO)
    semana[5] = [("09:00", "12:00")]
    assert cli.put(f"/api/horarios/{_id(db, 'Gonzalo Siuciak')}", json=_semana(semana)).status_code == 200
    est = cli.get("/api/horarios").get_json()
    prueba = """
(async () => {
  await hrCargar();
  const conSabado = _el('hr-contenido').innerHTML;
  console.log(JSON.stringify({conSabado: conSabado, vacio: hrPantallaHtml({personas: []})}));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""
    s = _correr_js(tmp_path, {"/api/horarios": est}, prueba)
    assert s["conSabado"].count('class="hr-col-dia"') == 6 and "Sáb" in s["conSabado"] and "Dom" not in s["conSabado"]
    assert s["conSabado"].count("No trabaja") == 2, "Juan el sábado, en la grilla y en la tarjeta"
    assert "Nadie del equipo lleva horas" in s["vacio"]

    falla = """
(async () => {
  await hrCargar();
  console.log(JSON.stringify({html: _el('hr-contenido').innerHTML}));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""
    s = _correr_js(tmp_path, {"/api/horarios": "falla"}, falla)
    assert "No se pudieron cargar los horarios" in s["html"]
