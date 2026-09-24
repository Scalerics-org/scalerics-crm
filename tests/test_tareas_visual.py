"""Tareas, que estaba en blanco y negro (pedido de Juan, 16/9).

"A tareas tambien hacela mas atractiva visualmente".

Lo que se midio antes de tocar nada, mirando el panel renderizado a 1280 y a
390: seis columnas identicas de gris sobre gris, sin un punto de color ni un
numero que pese; la fila de meta con seis textos del mismo tamano y del mismo
gris (estado, prioridad, cliente, fecha, "-> Persona", "de Jefe", "Notion"), asi
que no habia por donde empezar a leer; el responsable en texto plano, sin cara
ni color; el vencimiento como "13/9 11:00 a. m. (vencida)" y el de hoy sin
ninguna marca; y el vacio, una linea de gris en el medio de la nada.

Las tres reglas que fijan estos tests, que son las que hacen que el color
signifique algo en vez de decorar:

1. **La persona lleva el color que YA tiene.** El de su rol en Flujos, el mismo
   del organigrama (`--rol-*` via `.eq-rol-COLOR`). No hay paleta nueva: la que
   habia en `_upickColors` (8 hex propios) no coincidia con ninguna otra
   pantalla, asi que la misma persona salia de un color en Tareas y de otro en
   Recursos Humanos.
2. **El vencimiento dice una sola cosa por color**: rojo si paso, ambar si es
   hoy, gris si falta. Y una tarea hecha no vence.
3. **El tablero de Tareas no puede pisar el de Pipeline Notion**, que comparte
   `.kanban-col` y `.kanban-card`. Por eso lo nuevo va en `.task-col` y
   `.task-card`.
"""

import json
import re
import shutil
import sqlite3
import subprocess

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services.flujos import ROL_ESTILOS
from tests.test_tokens_css import _contraste, _tokens

HTML = dashboard.DASHBOARD_HTML

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")


def _funcion(nombre: str) -> str:
    m = re.search(r"\n(?:async )?function " + nombre + r"\(.*?\n\}", HTML, re.S)
    assert m, f"no encontre la funcion {nombre} en el dashboard"
    return m.group(0)


def _una_linea(nombre: str) -> str:
    """Una funcion escrita entera en una sola linea.

    `_funcion` no la puede recortar: busca hasta el primer `\\n}` y, si la
    funcion cierra en su misma linea, se lleva puesto todo lo que haya hasta el
    final de la SIGUIENTE funcion. Es la misma trampa que ya estaba anotada
    para `escJs` en test_calendario_mobile.
    """
    m = re.search(r"^function " + nombre + r"\(.*$", HTML, re.M)
    assert m, f"no encontre {nombre} en el dashboard"
    return m.group(0)


def _css() -> str:
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", HTML, re.S))


def _regla(selector: str) -> str:
    m = re.search(r"^" + re.escape(selector) + r"(?:,[^{]*)?\{([^}]*)\}", _css(), re.M)
    assert m, f"no encontre la regla {selector}"
    return m.group(1)


# ── el color de la persona es el de Flujos, no uno nuevo ─────────────────────

def test_el_avatar_toma_el_color_del_rol_y_no_uno_propio():
    """`.task-av` se pinta con `--rol-c` / `--rol-t`, los mismos tokens que el
    nodo del organigrama y la tarjeta de un paso de Flujos."""
    cuerpo = _regla(".task-av")
    assert "var(--rol-c" in cuerpo and "var(--rol-t" in cuerpo, cuerpo
    assert not re.findall("#[0-9a-fA-F]{3,8}", cuerpo), cuerpo


def test_la_paleta_propia_del_selector_de_usuario_ya_no_existe():
    """Era el sintoma mas concreto de "cada pantalla elige su color": ocho hex
    sueltos, indexados por id de usuario, que no coincidian con el organigrama.
    Si vuelve, la misma persona tiene dos colores en el mismo CRM."""
    assert "_upickColors" not in HTML
    assert "function _upickColor(id) { return _taskColorDe(id); }" in HTML


def test_sin_usuario_conocido_el_avatar_es_gris_y_no_el_color_de_otro():
    js = _funcion("_taskColorDe")
    assert "'neutro'" in js
    assert ".eq-rol-neutro{" in _css(), "el gris de 'no se quien es' tiene que existir"


# ── el vencimiento ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("clase,fondo,texto", [
    (".task-fecha.vencida", "--rojo-tinte", "--rojo-texto"),
    (".task-fecha.es-hoy", "--ambar-tinte", "--ambar"),
])
def test_la_fecha_usa_la_familia_de_su_significado(clase, fondo, texto):
    """Rojo es "paso", ambar es "es hoy". Son las familias que ya existen para
    los estados, no un rojo y un ambar elegidos a mano."""
    cuerpo = _regla(clase)
    assert f"background:var({fondo})" in cuerpo, cuerpo
    assert f"color:var({texto})" in cuerpo, cuerpo


@pytest.mark.parametrize("tema", ["oscuro", "claro"])
def test_la_fecha_se_lee_en_los_dos_temas(tema):
    t = _tokens(":root") if tema == "oscuro" else _tokens("body.light")
    for fondo, texto in (("--rojo-tinte", "--rojo-texto"),
                         ("--ambar-tinte", "--ambar"),
                         ("--relleno", "--texto-tenue")):
        c = _contraste(t[texto], t[fondo])
        assert c >= 4.5, f"{texto} sobre {fondo} en {tema}: {c:.2f}:1"


@pytest.mark.parametrize("tema", ["oscuro", "claro"])
def test_el_nombre_del_responsable_se_lee_sobre_su_tinte(tema):
    """El avatar es el color pleno del rol sobre su tinte. Vale para los seis
    roles y para el rosa de "fuera de Flujos"."""
    t = _tokens(":root") if tema == "oscuro" else _tokens("body.light")
    familias = {e["color"] for e in ROL_ESTILOS.values()} | {"rosa"}
    for color in familias:
        c = _contraste(t[f"--rol-{color}"], t[f"--rol-{color}-tinte"])
        assert c >= 4.5, f"--rol-{color} sobre su tinte en {tema}: {c:.2f}:1"


# ── no pisar el tablero de Pipeline Notion ───────────────────────────────────

def test_el_tablero_de_tareas_no_redefine_las_clases_compartidas():
    """`.kanban-col` y `.kanban-card` los dibuja tambien Pipeline Notion. Lo
    nuevo va en clases propias; si alguien mete el color en las compartidas, ese
    otro tablero cambia sin que nadie lo haya pedido."""
    board = _funcion("_renderTasksBoard")
    assert 'class="kanban-col task-col ${tono}"' in board
    for clase in ("task-col-todo", "task-col-curso", "task-col-hecho"):
        assert clase in board, clase
    assert "task-col-punto" in board and "task-col-n" in board
    tarjeta = _funcion("_taskCardHtml")
    assert 'class="kanban-card task-card' in tarjeta


def test_las_columnas_se_pintan_por_grupo_y_no_por_nombre():
    """El color sale del grupo de Notion (todo / in progress / done), que es lo
    que ya agrupa las columnas. Si saliera del nombre, un estado nuevo en el
    tablero quedaria sin color."""
    board = _funcion("_renderTasksBoard")
    assert "done:'task-col-hecho'}[col.grupo]" in board
    for tono in ("todo", "curso", "hecho"):
        assert f".task-col-{tono}{{--task-c:var(--" in _css(), tono


def test_la_ficha_de_pipeline_notion_quedo_igual():
    """La otra funcion que dibuja `.kanban-card` no se toco."""
    ficha = _funcion("_notionClientCardHtml")
    assert "task-card" not in ficha
    assert "_ncVinculoHtml(c)" in ficha


# ── el celular ───────────────────────────────────────────────────────────────

def test_en_el_celular_el_tablero_va_en_una_sola_columna():
    """A 260px fijos por columna habia que arrastrar de costado para ver las
    seis. Va por `#tasks-board` y no por `.kanban`, que es de los dos tableros."""
    movil = _css()[_css().index("@media(max-width:768px){"):]
    assert "#tasks-board{flex-direction:column" in movil
    assert "#tasks-board .task-col{width:100%" in movil


def test_los_botones_de_la_fila_llegan_a_44px_en_el_celular():
    movil = _css()[_css().index("@media(max-width:768px){"):]
    m = re.search(r"\.task-edit-btn,\.task-del-btn\{([^}]*)\}", movil)
    assert m, "no encontre los botones de la fila en el bloque del celular"
    assert "min-height:44px" in m.group(1) and "min-width:44px" in m.group(1)


# ── se dibuja de verdad ──────────────────────────────────────────────────────

_ARNES = """
function assert(cond, msg) { if (!cond) { throw new Error(msg); } }
let _proyectosPorPagina = {};
let _allLeads = [{id: 7, name: 'Optica Luz'}];
let _allUsers = [
  {id: 1, name: 'Gonzalo Siuciak', color: 'naranja'},
  {id: 2, name: 'Andres Rosi', color: 'rojo'},
  {id: 9, name: 'Alguien de afuera', color: 'rosa'},
];
const _COLUMNAS_NOTION = __COLUMNAS__;
"""


def _correr(cuerpo: str, tmp_path, funciones=()) -> str:
    columnas = re.search(r"const _COLUMNAS_NOTION = (\[.*?\]);", HTML, re.S)
    assert columnas, "no encontre _COLUMNAS_NOTION"
    fuente = "\n".join([
        dashboard.ESC_JS,
        _una_linea("_upickInitials"),
        _ARNES.replace("__COLUMNAS__", columnas.group(1)),
        *[_funcion(n) for n in funciones],
        cuerpo,
    ])
    archivo = tmp_path / "tareas.js"
    archivo.write_text(fuente, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


_PINTA_QUIEN = ("_taskColorDe", "_taskAvatarHtml", "_taskQuienHtml")
_PINTA_FECHA = ("_taskFechaHtml",)


@sin_node
def test_el_avatar_sale_con_el_color_de_la_persona(tmp_path):
    _correr("""
      const html = _taskQuienHtml({assignee_id: 1, assignee_name: 'Gonzalo Siuciak'});
      assert(html.includes('eq-rol-naranja'), html);
      assert(html.includes('>GS<'), 'faltan las iniciales: ' + html);
      assert(html.includes('Gonzalo Siuciak'), html);
      const otro = _taskQuienHtml({assignee_id: 2, assignee_name: 'Andres Rosi'});
      assert(otro.includes('eq-rol-rojo'), otro);
    """, tmp_path, _PINTA_QUIEN)


@sin_node
def test_una_tarea_sin_responsable_lo_dice_y_no_toma_color_de_nadie(tmp_path):
    _correr("""
      const html = _taskQuienHtml({});
      assert(html.includes('Sin asignar'), html);
      assert(!html.includes('eq-rol-naranja') && !html.includes('eq-rol-rojo'), html);
      const desconocido = _taskQuienHtml({assignee_id: 424242, assignee_name: 'Ni idea'});
      assert(desconocido.includes('eq-rol-neutro'), desconocido);
    """, tmp_path, _PINTA_QUIEN)


@sin_node
def test_la_fecha_dice_vencio_hoy_o_la_fecha(tmp_path):
    """El caso que mas importa: una hecha con fecha pasada NO esta vencida."""
    _correr("""
      const dia = 24 * 60 * 60 * 1000;
      const iso = d => new Date(d).toISOString().slice(0, 19).replace('T', ' ');
      const ayer = _taskFechaHtml({deadline: iso(Date.now() - dia)}, false);
      assert(ayer.includes('vencida') && ayer.includes('Venció'), ayer);

      const hoy = new Date(); hoy.setHours(18, 0, 0, 0);
      const deHoy = _taskFechaHtml({deadline: iso(hoy.getTime())}, false);
      assert(deHoy.includes('es-hoy') && deHoy.includes('Hoy'), deHoy);
      assert(!deHoy.includes('vencida'), 'lo de hoy todavia no vencio: ' + deHoy);

      const manana = _taskFechaHtml({deadline: iso(Date.now() + 3 * dia)}, false);
      assert(!manana.includes('vencida') && !manana.includes('es-hoy'), manana);

      const hecha = _taskFechaHtml({deadline: iso(Date.now() - 9 * dia)}, true);
      assert(!hecha.includes('vencida'), 'una tarea hecha no vence: ' + hecha);

      assert(_taskFechaHtml({}, false) === '', 'sin fecha no hay pastilla');
      assert(_taskFechaHtml({deadline: 'cualquier cosa'}, false) === '', 'fecha rota');
    """, tmp_path, _PINTA_FECHA)


@sin_node
def test_el_tablero_pinta_punto_numero_y_columna_vacia(tmp_path):
    salida = _correr("""
      let escrito = '';
      const document = { getElementById: () => ({ set innerHTML(v) { escrito = v; },
                                                  get innerHTML() { return escrito; } }) };
      const tareas = [
        {id: 1, title: 'Una en curso', status: 'in_progress', notion_status: 'In progress',
         assignee_id: 1, assignee_name: 'Gonzalo Siuciak'},
        {id: 2, title: 'Una hecha', status: 'done', notion_status: 'Done'},
      ];
      _renderTasksBoard(tareas);
      console.log(escrito);
    """, tmp_path, _PINTA_QUIEN + _PINTA_FECHA + ("_taskCardHtml", "_renderTasksBoard"))

    assert "task-col task-col-curso" in salida
    assert "task-col task-col-hecho" in salida
    assert "task-col task-col-todo" in salida
    assert salida.count('class="task-col-punto"') == 6, "un punto por columna"
    # La columna vacia lo dice en vez de quedar como un hueco sin explicacion.
    assert "Sin tareas" in salida
    # La ficha en curso lleva su acento y el avatar de quien la tiene.
    assert "kanban-card task-card" in salida and "en-curso" in salida
    assert "eq-rol-naranja" in salida


@sin_node
def test_la_fila_muestra_avatar_fecha_y_conserva_sus_botones(tmp_path):
    salida = _correr("""
      const t = {id: 5, title: 'Llamar a los leads', status: 'todo', priority: 'high',
                 client_id: 7, assignee_id: 2, assignee_name: 'Andres Rosi',
                 created_by_name: 'Jefe', deadline: '2020-01-02 10:00:00'};
      console.log(_taskRowHtml(t));
    """, tmp_path, _PINTA_QUIEN + _PINTA_FECHA + ("_taskRowHtml",))

    assert "task-av eq-rol-rojo" in salida
    assert "task-fecha vencida" in salida and "Venció" in salida
    assert 'class="task-de">de Jefe' in salida
    # Lo que ya hacia sigue estando: cambiar estado, mandar a Notion, editar y
    # borrar. Esto es una pasada visual, no un cambio de comportamiento.
    for accion in ("_setTaskStatus(5)", "_enviarTareaANotion(5)",
                   "openEditTaskModal(5)", "_deleteTask(5)"):
        assert accion in salida, accion
    assert "task-client-link" in salida and "Optica Luz" in salida


# ── resumen y vacio ──────────────────────────────────────────────────────────

def test_el_resumen_pone_los_numeros_grandes():
    js = _funcion("renderTasksList")
    assert "summary.innerHTML" in js
    assert "<b>${tasks.length}</b>" in js
    assert "vencida" in js and "hecha" in js
    cuerpo = _regla(".tasks-summary b")
    assert "font-size:1.15rem" in cuerpo and "var(--texto-fuerte)" in cuerpo


def test_el_vacio_explica_y_no_es_una_linea_de_gris():
    js = _funcion("renderTasksList")
    assert "tasks-empty-tit" in js and "tasks-empty-sub" in js
    assert "No hay tareas para este filtro" in js
    assert "+ Nueva tarea" in js, "el vacio tiene que decir como salir de ahi"


def test_el_nombre_del_usuario_del_resumen_va_escapado():
    """Paso a ser innerHTML: un nombre con `<` dejaria de ser texto."""
    js = _funcion("renderTasksList")
    assert "esc(userLabel)" in js and "esc(filterLabel)" in js


# ── el color de /api/users ───────────────────────────────────────────────────

@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    db = str(tmp_path / "tareas.db")
    init_db(db)
    app = dashboard.create_app(db)
    app.config["_DB"] = db
    uid = create_user(db, name="Jefe", email="jefe@scalerics.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    return c


def _usuario(db, nombre, mail):
    return create_user(db, name=nombre, email=mail, phone="099",
                       password_hash=generate_password_hash("x" * 10))


def test_cada_usuario_viene_con_el_color_que_tiene_en_el_organigrama(cli):
    """El pareo es por nombre, que es el unico vinculo entre `users` y
    `equipo_personas`. Gonzalo es Project manager, o sea naranja, igual que su
    nodo del organigrama y que su paso en Flujos."""
    db = cli.application.config["_DB"]
    _usuario(db, "Gonzalo Siuciak", "gonzalo@scalerics.com")
    _usuario(db, "Andrés Rosi", "andres@scalerics.com")

    por_nombre = {u["name"]: u for u in cli.get("/api/users").get_json()}

    assert por_nombre["Gonzalo Siuciak"]["color"] == "naranja"
    assert por_nombre["Gonzalo Siuciak"]["rol_flujo"] == "Project manager"
    assert por_nombre["Andrés Rosi"]["color"] == "rojo"
    assert por_nombre["Andrés Rosi"]["etiqueta_flujo"] == "Líder marketing digital"


def test_quien_no_esta_en_el_equipo_cae_en_fuera_de_flujos(cli):
    db = cli.application.config["_DB"]
    _usuario(db, "Alguien Nuevo", "nuevo@scalerics.com")

    u = next(u for u in cli.get("/api/users").get_json() if u["name"] == "Alguien Nuevo")

    assert u["color"] == "rosa" and u["rol_flujo"] is None
    assert u["etiqueta_flujo"] == "Fuera de Flujos"


def test_el_color_sigue_al_rol_cuando_lo_cambian(cli):
    """No hay una segunda tabla de colores de Tareas: si el admin cambia el rol
    en el organigrama, el avatar de la tarea cambia con el."""
    db = cli.application.config["_DB"]
    _usuario(db, "Gonzalo Siuciak", "gonzalo@scalerics.com")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE equipo_personas SET rol_flujo='Desarrollo' WHERE nombre='Gonzalo Siuciak'")
    conn.commit()
    conn.close()

    u = next(u for u in cli.get("/api/users").get_json() if u["name"] == "Gonzalo Siuciak")

    assert u["color"] == "azul"


def test_el_color_no_cambia_permisos_ni_saca_campos(cli):
    """Es un agregado de presentacion: lo que la ruta ya devolvia sigue igual."""
    db = cli.application.config["_DB"]
    _usuario(db, "Gonzalo Siuciak", "gonzalo@scalerics.com")

    u = next(u for u in cli.get("/api/users").get_json() if u["name"] == "Gonzalo Siuciak")

    for campo in ("id", "name", "email", "phone", "role_id", "panel_access"):
        assert campo in u, campo
    assert "password" not in u
    # El admin se sigue filtrando de la lista.
    assert all(x["email"] != "jefe@scalerics.com" for x in cli.get("/api/users").get_json())


def test_la_pagina_abre_con_el_panel(cli):
    r = cli.get("/")
    assert r.status_code == 200
    pagina = r.get_data(as_text=True)
    assert pagina.count('id="tasks-panel"') == 1
    assert "task-col-punto" in pagina and "task-av" in pagina


def test_la_carga_inicial_no_cambio():
    """La regla de siempre: al abrir solo se dibuja el calendario.

    Se miran las lineas de codigo del bloque, no los comentarios: justo arriba
    hay uno que menciona `showPanel('cal')` para explicar por que existe
    `calLoaded`.
    """
    assert "calLoaded = true; renderCalendar();" in HTML
    bloque = HTML[HTML.index("// Initial load"):].split(chr(10))[:12]
    codigo = chr(10).join(l for l in bloque if not l.strip().startswith("//"))
    assert "showPanel(" not in codigo, codigo
    assert "setInterval(" not in codigo, codigo


def test_el_json_de_prueba_no_quedo_pegado():
    """El arnes de estos tests usa `json`; si alguien deja datos de prueba en el
    dashboard, se ve aca."""
    assert "Alguien de afuera" not in HTML
    assert json is not None
