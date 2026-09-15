"""La pantalla de WhatsApp como app de chat (14/9, pedido de Juan: "que quede
mas vistoso, esta un poco incomodo").

Lo que se prueba: que la pagina abra, que el HTML y el JS nuevos esten, que el
CSS vaya con tokens (el panel viejo era oscuro tambien en tema claro), y el
pintado de verdad con node: burbujas, separadores por dia, busqueda, no
leidos, Enter / Shift+Enter, y que el envio siga pegandole a la misma ruta con
el mismo cuerpo.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db

RAIZ = Path(__file__).resolve().parent.parent
HTML = dashboard.DASHBOARD_HTML

node = pytest.mark.skipif(shutil.which("node") is None, reason="node no esta instalado")


def _entre(desde: str, hasta: str) -> str:
    i = HTML.index(desde)
    return HTML[i:HTML.index(hasta, i + len(desde))]


# El JS nuevo, sin el render de medios que se pega en el medio por marcador
# (`WA_MEDIOS_JS` es un string raw aparte, con sus propias regex y sus tests).
JS_WA = (_entre("// ========== WhatsApp panel ==========", "function mediosDeMensaje(")
         + _entre("// La conversación: una burbuja por mensaje", "// ========== Calendar panel =========="))
PANEL_WA = _entre('<div id="wa-panel" class="panel">', "<!-- ======= TASKS PANEL")


# ── la página abre ───────────────────────────────────────────────────────────


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    db = str(tmp_path / "wa.db")
    init_db(db)
    app = dashboard.create_app(db)
    uid = create_user(db, name="jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    return c


def test_la_pagina_abre_con_la_bandeja_nueva(cli):
    r = cli.get("/")
    assert r.status_code == 200
    for fragmento in ('id="wa-container"', 'id="wa-buscar"', 'oninput="waFiltrar(this.value)"',
                      'id="wa-volver"', 'onclick="waVolver()"', 'id="wa-chat-avatar"',
                      '<textarea class="wa-input" id="wa-input"',
                      'onkeydown="waTeclaInput(event)"', 'id="wa-send-btn"',
                      'id="wa-plantillas-btn"', "Shift+Enter",
                      "function waPintarMensajes(", "function waPintarLista(",
                      "function waTeclaInput("):
        assert fragmento.encode() in r.data, fragmento


def test_siguen_los_ids_que_usa_el_resto_del_panel():
    """El interruptor del bot, el devolver al bot y las plantillas no cambiaron
    de lógica: sus ids tienen que seguir estando."""
    for ident in ("wa-lead-list", "wa-empty-state", "wa-chat-content", "wa-chat-name",
                  "wa-chat-phone", "wa-pausa-badge", "wa-bot-switch", "wa-bot-label",
                  "wa-human-badge", "wa-release-btn", "wa-messages", "wa-templates-panel",
                  "wa-template-form", "wa-tmpl-name", "wa-tmpl-body", "wa-template-list"):
        assert f'id="{ident}"' in PANEL_WA, ident


# ── las trampas del string de Python ─────────────────────────────────────────


def test_el_js_y_el_html_nuevos_no_rompen_jinja_ni_pierden_barras():
    """`DASHBOARD_HTML` pasa por render_template_string y es un string normal:
    una llave doble tumba la página y una barra invertida se pierde."""
    for nombre, trozo in (("js", JS_WA), ("html", PANEL_WA)):
        for peligro in ("{" + "{", "{" + "%", "{" + "#"):
            assert peligro not in trozo, f"{peligro!r} en el {nombre} de WhatsApp"
        assert chr(92) not in trozo, f"barra invertida en el {nombre} de WhatsApp"


def test_no_arranca_ningun_reloj_al_cargar_la_pagina():
    """El refresco arranca al abrir el panel. Un setInterval suelto a nivel de
    script cuelga los tests de node."""
    assert JS_WA.count("setInterval(") == 1
    assert "function waIniciarRefresco()" in JS_WA


# ── CSS con tokens ───────────────────────────────────────────────────────────


def _css() -> str:
    return chr(10).join(re.findall(r"<style[^>]*>(.*?)</style>", HTML, re.S))


def test_las_reglas_de_whatsapp_usan_tokens():
    reglas = re.findall(r"^(\.wa-[^{]*)\{([^}]*)\}", _css(), re.M)
    assert len(reglas) > 60, f"el parser vio pocas reglas: {len(reglas)}"
    a_mano = [sel for sel, cuerpo in reglas
              if re.findall("#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])", cuerpo.replace("#fff", ""))
              or "rgba(" in cuerpo]
    assert not a_mano, f"reglas de WhatsApp con colores a mano: {a_mano}"


def test_whatsapp_no_tiene_reglas_claras_propias():
    assert not re.search(r"^body[.]light [.]wa-", _css(), re.M)


def test_el_panel_no_pinta_colores_inline():
    assert not re.findall("(?:color|background|border)[a-z-]*:#", PANEL_WA)
    assert not re.findall("style=[^>]*#[0-9a-fA-F]{3}", JS_WA)


def _tokens(selector):
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", HTML)
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(1)))


def _lab(hexa):
    h = hexa.strip().lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h

    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    x = (r * .4124 + g * .3576 + b * .1805) / .95047
    y = r * .2126 + g * .7152 + b * .0722
    z = (r * .0193 + g * .1192 + b * .9505) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > .008856 else 7.787 * t + 16 / 116

    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def _delta_e(a, b):
    return sum((p - q) ** 2 for p, q in zip(_lab(a), _lab(b))) ** .5


def _token_de(selector, propiedad):
    m = re.search("^" + re.escape(selector) + r"\{([^}]*)\}", _css(), re.M)
    assert m, selector
    t = re.search("(?:^|;)" + propiedad + r":var\((--[a-z-]+)\)", m.group(1))
    assert t, f"{selector} no toma {propiedad} de un token"
    return t.group(1)


@pytest.mark.parametrize("tema", [":root", "body.light"])
def test_las_burbujas_se_distinguen_en_los_dos_temas(tema):
    """La que sale se despega del fondo del chat y de la que entra; la que
    entra, además de su color, lleva borde (en claro es blanco sobre gris muy
    claro, como en WhatsApp)."""
    t = _tokens(tema)
    fondo = t[_token_de(".wa-chat", "background")]
    entra = t[_token_de(".wa-bubble-in", "background")]
    sale = t[_token_de(".wa-bubble-out", "background")]

    assert _delta_e(sale, fondo) >= 4
    assert _delta_e(sale, entra) >= 4
    assert _delta_e(entra, fondo) >= 2
    assert "border:1px solid var(--borde)" in re.search(r"^\.wa-bubble-in\{([^}]*)\}", _css(), re.M).group(1)


# ── el pintado, con node de verdad ───────────────────────────────────────────

_ARNES = r"""
const _els = {};
function _clases() {
  const s = new Set();
  return { add: c => s.add(c), remove: c => s.delete(c), contains: c => s.has(c),
           toggle: (c, f) => { const on = f === undefined ? !s.has(c) : f; on ? s.add(c) : s.delete(c); return on; },
           lista: () => Array.from(s) };
}
function _el(id) {
  if (!_els[id]) {
    _els[id] = { id, innerHTML: '', textContent: '', value: '', disabled: false, className: '',
                 style: {}, dataset: {}, classList: _clases(), scrollTop: 0, scrollHeight: 0, clientHeight: 0,
                 querySelectorAll: () => [], querySelector: () => null, focus(){},
                 addEventListener(){}, appendChild(){}, remove(){} };
  }
  return _els[id];
}
globalThis.document = {
  getElementById: _el, querySelectorAll: () => [], querySelector: () => null,
  body: { classList: { contains: () => false, add(){}, remove(){} } },
  createElement: () => _el('tmp'), addEventListener(){},
};
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem(){}, removeItem(){} };
globalThis.fetch = () => Promise.resolve({ ok: true, json: () => ({}) });
globalThis.lucide = { createIcons(){} };
globalThis.alert = () => {};
globalThis.confirm = () => true;
"""


def _correr(tmp_path, cola, antes=""):
    bloques = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    charts = (RAIZ / "static" / "charts.js").read_text(encoding="utf-8")
    archivo = tmp_path / "wa.js"
    archivo.write_text(_ARNES + antes + charts + chr(10) + chr(10).join(bloques) + chr(10) + cola,
                       encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True, encoding="utf-8",
                       timeout=60)
    assert r.returncode == 0, "el JS reventó:" + chr(10) + (r.stderr or "")[:2000]
    return json.loads(r.stdout.strip().splitlines()[-1])


_MENSAJES = """
const _hoy = new Date(2026, 8, 14, 18, 0);
const _m = (dia, h, mi, dir, content, extra) => Object.assign(
  { id: dia * 10000 + h * 100 + mi, direction: dir, content: content,
    created_at: new Date(2026, 8, dia, h, mi).toISOString() }, extra || {});
const _conversacion = [
  _m(13, 9, 5, 'in', 'Hola, ¿hacen páginas web?'),
  _m(13, 9, 6, 'out', 'Hola! Sí, contame qué necesitás'),
  _m(14, 10, 5, 'in', '<b>tengo</b> un local'),
  _m(14, 10, 7, 'in', '', { media: [{ tipo: 'imagen', url: '/api/messages/77/media/0' }] }),
  _m(14, 10, 9, 'out', 'Buenísimo' + String.fromCharCode(10) + 'Te paso precios'),
];
"""


@node
def test_la_conversacion_tiene_burbujas_hora_y_separador_por_dia(tmp_path):
    r = _correr(tmp_path, _MENSAJES + """
console.log(JSON.stringify({ html: waPintarMensajes(_conversacion, _hoy),
                             vacia: waPintarMensajes([], _hoy) }));
""")
    html = r["html"]
    assert html.count('class="wa-dia"') == 2
    assert html.index(">Ayer<") < html.index(">Hoy<")
    assert html.count("wa-bubble-in") == 3
    assert html.count("wa-bubble-out") == 2
    assert "10:05" in html and "09:06" in html
    # Lo que escribe el contacto va escapado.
    assert "&lt;b&gt;tengo&lt;/b&gt;" in html and "<b>tengo" not in html
    # La foto se sigue dibujando con el render de medios de siempre.
    assert "/api/wa/media/77/0" in html
    assert "Sin mensajes todavía" in r["vacia"]


@node
def test_la_lista_ordena_busca_y_marca_no_leidos(tmp_path):
    r = _correr(tmp_path, """
waLeads = [
  { phone: '+59899111222', name: 'Bruno Díaz', state: 'SCHEDULED', last_activity: '2026-09-10T12:00:00Z' },
  { phone: '+59898765432', name: 'Ana Pérez', state: 'QUAL_1', last_activity: '2026-09-14T12:00:00Z' },
  { phone: '+59891000000', name: '', state: null, last_activity: '2026-09-12T12:00:00Z' },
];
waVistos = { '+59899111222': '2026-09-10T12:00:00Z', '+59898765432': '2026-09-13T12:00:00Z',
             '+59891000000': '2026-09-12T12:00:00Z' };
waUltimos['+59899111222'] = { texto: 'Nos vemos el jueves', dir: 'out', actividad: '2026-09-10T12:00:00Z' };
const lista = () => _els['wa-lead-list'].innerHTML;
waFiltrar('');
const todos = lista();
waFiltrar('ana');
const ana = lista();
waFiltrar('099 111');
const porTelefono = lista();
waFiltrar('zzz');
const nada = lista();
waLeads = [];
waFiltrar('');
const vacia = lista();
console.log(JSON.stringify({ todos, ana, porTelefono, nada, vacia, contador: _els['wa-count'].textContent }));
""")
    todos = r["todos"]
    # El más reciente arriba.
    assert todos.index("Ana Pérez") < todos.index("+59891000000") < todos.index("Bruno Díaz")
    # Ana escribió después de la última vez que se abrió: no leído. Bruno no.
    assert todos.count("wa-no-leido") == 1
    assert 'class="wa-lead-item wa-no-leido" id="wa-lead-+59898765432"' in todos
    assert "✓ Nos vemos el jueves" in todos
    assert ">AP<" in todos and ">BD<" in todos and ">00<" in todos
    assert "Calificando" in todos and "Agendado" in todos
    assert "Ana Pérez" in r["ana"] and "Bruno" not in r["ana"]
    assert "Bruno Díaz" in r["porTelefono"] and "Ana" not in r["porTelefono"]
    assert "Ningún chat coincide" in r["nada"]
    assert "Todavía no hay conversaciones" in r["vacia"]


@node
def test_enter_envia_y_shift_enter_no(tmp_path):
    r = _correr(tmp_path, """
let _envios = 0;
sendWaMessage = () => { _envios++; };
const _evento = (shift) => { const e = { key: 'Enter', shiftKey: shift, isComposing: false, frenado: false,
  preventDefault() { this.frenado = true; } }; waTeclaInput(e); return e.frenado; };
const conShift = _evento(true);
const despuesShift = _envios;
const sinShift = _evento(false);
waTeclaInput({ key: 'a', shiftKey: false, preventDefault() {} });
console.log(JSON.stringify({ conShift, despuesShift, sinShift, envios: _envios }));
""")
    assert r == {"conShift": False, "despuesShift": 0, "sinShift": True, "envios": 1}


@node
def test_abrir_un_chat_baja_al_final_y_volver_muestra_la_lista(tmp_path):
    r = _correr(tmp_path, _MENSAJES + """
waLeads = [{ phone: '+59898765432', name: 'Ana Pérez', state: 'NEW', last_activity: '2026-09-14T12:00:00Z' }];
globalThis.fetch = (url) => Promise.resolve({ ok: true,
  json: () => Promise.resolve(String(url).indexOf('/messages') !== -1 ? _conversacion : {}) });
_el('wa-messages').scrollHeight = 1800;
(async () => {
  await selectWaLead('+59898765432', 'Ana Pérez');
  const abierto = { clases: _els['wa-container'].classList.lista(), scroll: _els['wa-messages'].scrollTop,
                    burbujas: (_els['wa-messages'].innerHTML.match(/wa-bubble-(in|out)/g) || []).length,
                    avatar: _els['wa-chat-avatar'].textContent, contenido: _els['wa-chat-content'].style.display };
  waVolver();
  console.log(JSON.stringify({ abierto, despues: _els['wa-container'].classList.lista(),
                               seleccionado: selectedPhone, contenido: _els['wa-chat-content'].style.display }));
  process.exit(0);
})();
""")
    assert r["abierto"]["clases"] == ["wa-en-chat"]
    assert r["abierto"]["scroll"] == 1800
    assert r["abierto"]["burbujas"] == 5
    assert r["abierto"]["avatar"] == "AP"
    assert r["abierto"]["contenido"] == "flex"
    assert r["despues"] == []
    assert r["seleccionado"] is None
    assert r["contenido"] == "none"


@node
def test_enviar_sigue_usando_la_misma_ruta_y_el_mismo_cuerpo(tmp_path):
    r = _correr(tmp_path, """
const _pedidos = [];
globalThis.fetch = (url, opciones) => {
  _pedidos.push({ url: String(url), metodo: opciones && opciones.method, cuerpo: opciones && opciones.body });
  const respuesta = String(url) === '/api/wa/send' ? { ok: true } : [];
  return Promise.resolve({ ok: true, json: () => Promise.resolve(respuesta) });
};
selectedPhone = '+59898765432';
_el('wa-input').value = '  Hola, te paso el presupuesto  ';
(async () => {
  await sendWaMessage();
  const envio = _pedidos.find(p => p.url === '/api/wa/send');
  console.log(JSON.stringify({ envio, caja: _els['wa-input'].value, boton: _els['wa-send-btn'].disabled }));
  process.exit(0);
})();
""")
    assert r["envio"]["metodo"] == "POST"
    assert json.loads(r["envio"]["cuerpo"]) == {"phone": "+59898765432",
                                                "text": "Hola, te paso el presupuesto"}
    assert r["caja"] == ""
    assert r["boton"] is False


@node
def test_el_link_del_mail_abre_la_conversacion(tmp_path):
    r = _correr(tmp_path, """
let _abierto = null;
showPanel = (nombre) => { _abierto = nombre; };
setTimeout(() => {
  console.log(JSON.stringify({ panel: _abierto, pendiente: waChatPendiente }));
  process.exit(0);
}, 20);
""", antes="globalThis.location = { search: '?panel=wa&chat=59898765432' };" + chr(10))
    assert r == {"panel": "wa", "pendiente": "59898765432"}
