"""El aviso "No sincronizada con Google" y "Reintentar en Google", en node.

Cuando Google falla al crear o editar, la reunion queda en el CRM y la pantalla
tiene que decirlo: un aviso arriba del calendario, una marca en la reunion y un
boton para reintentar, en escritorio y en el celular. Como el resto del
calendario, el JS vive en un string de Python: se recortan las funciones y se
corren contra un DOM y un fetch de mentira.
"""

import re
import shutil
import subprocess

import pytest

import dashboard


HTML = dashboard.DASHBOARD_HTML

node = pytest.mark.skipif(shutil.which("node") is None,
                          reason="node no esta instalado")


def _funcion(nombre: str) -> str:
    m = re.search(r"\n(?:async )?function " + nombre + r"\(.*?\n\}", HTML, re.S)
    assert m, f"no encontre la funcion {nombre} en el dashboard"
    return m.group(0)


def _linea(prefijo: str) -> str:
    m = re.search(r"^" + re.escape(prefijo) + r".*$", HTML, re.M)
    assert m, f"no encontre {prefijo!r} en el dashboard"
    return m.group(0)


def _node(partes: list, cuerpo: str, tmp_path) -> str:
    fuente = "\n".join(
        ["function assert(cond, msg) { if (!cond) { throw new Error(msg); } }",
         dashboard.ESC_JS, _linea("function escJs("), _linea("const CAL_DIAS_PLURAL = ")]
        + partes
        + ["(async () => {", cuerpo, "})().catch(e => { console.error(e); process.exit(1); });"])
    archivo = tmp_path / "google.js"
    archivo.write_text(fuente, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


_REUNIONES = """
const SIN_GOOGLE = {id: 'asunto-5@2026-09-18', reunion_id: 5, tipo: 'asunto', serie: true,
  ocurrencia: '2026-09-18', repeticion: {freq: 'semanal', dias: [4], fin: 'nunca'},
  title: 'Marketing semanal', date: '2026-09-18', time: '19:00', origen: 'crm',
  google: {estado: 'error', error: 'falta permiso de escritura en Google Calendar'}};
const EN_GOOGLE = Object.assign({}, SIN_GOOGLE, {google: {estado: 'ok', error: ''}});
"""

_ACCIONES = """
const llamadas = [];
function _calAbrirEditor(id) { llamadas.push(['editar', id]); }
function deleteCalEvent(id, titulo) { llamadas.push(['borrar', id]); }
function _calReintentarGoogle(id) { llamadas.push(['reintentar', id]); }
// Lo que hace el navegador al tocar: desescapar el atributo y correrlo.
function tocar(html, clase) {
  const m = html.match(new RegExp('class="[^"]*' + clase + '[^"]*"[^>]*onclick="([^"]*)"'));
  if (!m) return false;
  const event = {stopPropagation() {}};
  eval(m[1].replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&'));
  return true;
}
"""


@node
def test_el_chip_marca_la_reunion_y_ofrece_reintentar(tmp_path):
    _node([_funcion("_calChipHtml"), _funcion("_calTextoRepeticion"), _funcion("_calPlural"),
           _REUNIONES, _ACCIONES], """
      const html = _calChipHtml(SIN_GOOGLE, 'cal-event-chip');
      assert(html.includes('cal-chip-sync'), 'falta la marca');
      assert(html.includes('No sincronizada con Google (falta permiso de escritura en Google Calendar)'), 'falta el motivo');
      assert(tocar(html, 'cal-act-retry'), 'falta Reintentar');
      assert(JSON.stringify(llamadas) === JSON.stringify([['reintentar', 'asunto-5@2026-09-18']]), JSON.stringify(llamadas));

      const ok = _calChipHtml(EN_GOOGLE, 'calw-chip');
      assert(!ok.includes('cal-chip-sync') && !ok.includes('cal-act-retry') && !ok.includes('No sincronizada'),
             'una sincronizada no avisa');
      const vieja = _calChipHtml({id: '9', title: 'Demo', origen: 'crm'}, 'cal-event-chip');
      assert(!vieja.includes('cal-act-retry'), 'sin dato de Google no avisa');
    """, tmp_path)


@node
def test_en_el_celular_se_ve_el_aviso_y_el_boton(tmp_path):
    _node([_funcion("_calItemMobile"), _funcion("_calTextoRepeticion"), _funcion("_calPlural"),
           _REUNIONES, _ACCIONES], """
      const html = _calItemMobile(SIN_GOOGLE);
      assert(html.includes('No sincronizada con Google: falta permiso de escritura en Google Calendar'), html);
      assert(html.includes('>Reintentar en Google<'), 'falta el boton');
      assert(!html.includes('style='), 'los colores van por clase');
      assert(tocar(html, 'cal-mobile-act-reintentar'), 'no se puede tocar');
      assert(tocar(html, 'cal-mobile-act-editar') && tocar(html, 'cal-mobile-act-borrar'), 'editar y borrar siguen');
      assert(JSON.stringify(llamadas.map(l => l[0])) === JSON.stringify(['reintentar', 'editar', 'borrar']), JSON.stringify(llamadas));

      const ok = _calItemMobile(EN_GOOGLE);
      assert(!ok.includes('Reintentar') && !ok.includes('No sincronizada'), 'una sincronizada no avisa');
    """, tmp_path)


@node
def test_reintentar_llama_a_la_ruta_de_cada_tipo_y_actualiza_el_aviso(tmp_path):
    _node([_funcion("_calReintentarGoogle"), _funcion("_calAvisoGoogle"),
           _funcion("_calRutaReunion"), _funcion("_calEvento"), _REUNIONES, """
      const aviso = {textContent: '', hidden: true};
      const document = {getElementById: id => id === 'cal-aviso-google' ? aviso : null};
      let _calEventos = [SIN_GOOGLE, {id: '12', reunion_id: 12, tipo: 'cliente', google: {estado: 'error'}}];
      let respuesta = {ok: false, error: 'Google respondió con error 503', google: {estado: 'error'}};
      const pedidos = [];
      async function fetch(url, opts) { pedidos.push([url, opts.method]); return {json: async () => respuesta}; }
      let redibujos = 0;
      function renderCalendar() { redibujos++; }
    """], """
      await _calReintentarGoogle('asunto-5@2026-09-18');
      assert(JSON.stringify(pedidos[0]) === JSON.stringify(['/api/calendar/asuntos/5/google', 'POST']), JSON.stringify(pedidos));
      assert(!aviso.hidden && aviso.textContent.includes('Google respondió con error 503'), aviso.textContent);
      assert(redibujos === 1, 'no redibujo');

      respuesta = {ok: true, google: {estado: 'ok', error: ''}};
      await _calReintentarGoogle('12');
      assert(pedidos[1][0] === '/api/calendar/meetings/12/google', pedidos[1][0]);
      assert(aviso.hidden && aviso.textContent === '', 'el aviso no se fue');
      assert(redibujos === 2, 'no redibujo');
    """, tmp_path)


_DOM_MODAL = """
function elemento(id) {
  const clases = new Set();
  return {id: id, _v: '', get value() { return this._v; }, set value(x) { this._v = String(x); },
          hidden: false, textContent: '', innerHTML: '', placeholder: '', style: {}, attrs: {},
          setAttribute(k, v) { this.attrs[k] = v; },
          classList: {add: c => clases.add(c), remove: c => clases.delete(c),
                      contains: c => clases.has(c),
                      toggle: (c, on) => { if (on) { clases.add(c); } else { clases.delete(c); } }}};
}
const els = {};
const casillas = [0, 1, 2, 3, 4, 5, 6].map(n => ({value: String(n), checked: false}));
const document = {
  getElementById: id => els[id] || (els[id] = elemento(id)),
  querySelectorAll: sel => sel === '#ev-rep-dias input' ? casillas : [],
};
const $ = id => document.getElementById(id);
const window = {innerWidth: 1280};
let calDiaMobile = null;
let _calTipoNueva = 'cliente';
function renderCalendar() {}
let respuesta = {ok: true, google: {estado: 'error', error: 'falta permiso de escritura en Google Calendar'}};
async function fetch() { return {json: async () => respuesta}; }
"""


@node
def test_crear_con_google_caido_avisa_arriba_del_calendario(tmp_path):
    partes = [_linea("function closeNewEventModal(")] + [_funcion(n) for n in (
        "openNewEventModal", "_calElegirTipo", "_calPonerCliente", "_calQuitarCliente",
        "_calReglaDelModal", "_calPintarRepeticion", "_calTextoRepeticion", "_calPlural",
        "_calLeerMails", "_calDiaSemana", "_calAhoraMvd", "saveEvent", "_calAvisoGoogle",
        "_calTextoGoogle")] + [_DOM_MODAL]
    _node(partes, """
      openNewEventModal();
      _calElegirTipo('asunto');
      $('ev-title').value = 'Marketing semanal';
      $('ev-date').value = '2026-09-18';
      $('ev-time').value = '19:00';
      await saveEvent();
      const aviso = $('cal-aviso-google');
      assert(!aviso.hidden, 'el aviso no se ve');
      assert(aviso.textContent.includes('quedó guardada en el CRM'), aviso.textContent);
      assert(aviso.textContent.includes('no se creó en Google Calendar: falta permiso de escritura en Google Calendar'), aviso.textContent);
      assert(aviso.textContent.includes('Reintentar en Google'), aviso.textContent);
      assert(!$('event-modal').classList.contains('open'), 'la reunion se guardo: el modal se cierra');

      respuesta = {ok: true, google: {estado: 'ok', error: ''}};
      openNewEventModal();
      _calElegirTipo('asunto');
      $('ev-title').value = 'Otra';
      await saveEvent();
      assert(aviso.hidden && aviso.textContent === '', 'con Google al dia el aviso se va');

      respuesta = {ok: true, google: {estado: '', error: ''}};
      openNewEventModal();
      _calElegirTipo('asunto');
      $('ev-title').value = 'Sin Google';
      await saveEvent();
      assert(aviso.hidden, 'con la creacion apagada no hay nada que avisar');
    """, tmp_path)


def test_el_css_del_aviso_usa_tokens():
    for selector in (".cal-aviso-google", ".cal-chip-sync", ".cal-act-retry",
                     ".cal-mobile-ev-sync", ".cal-mobile-act-reintentar"):
        m = re.search(r"^" + re.escape(selector) + r"\{([^}]*)\}", HTML, re.M)
        assert m, f"falta la regla {selector}"
        assert not re.findall("#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])", m.group(1)), selector
        assert "var(--" in m.group(1), selector


def test_el_aviso_arranca_oculto_y_sin_estilo_inline():
    m = re.search(r'<div id="cal-aviso-google"[^>]*>', HTML)
    assert m and " hidden" in m.group(0) and "style=" not in m.group(0), m and m.group(0)


def test_crear_mover_y_editar_muestran_el_resultado_de_google():
    for nombre in ("saveEvent", "_calMover", "_calGuardarHorario"):
        assert "_calAvisoGoogle(_calTextoGoogle(" in _funcion(nombre), nombre
