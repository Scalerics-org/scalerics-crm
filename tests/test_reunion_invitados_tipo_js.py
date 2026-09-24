"""El lado del navegador de los invitados automaticos y el tipo (Juan, 16/9).

Como en `tests/test_calendario_asunto_js.py`: el JS vive adentro de un string
de Python, asi que se recortan las funciones y se corren en node contra un DOM
y un fetch de mentira.

  invitados   al elegir el lead, el campo se llena SOLO y a la vista con el
              mail del lead y los tres fijos; sin repetidos; lo que se borra
              queda borrado; cambiar de lead cambia el mail del lead y no toca
              lo que Juan agrego a mano
  tipo        el selector sale de CAL_TIPOS, "Otro" abre el texto libre, y todo
              viaja en el cuerpo del pedido
  celular     el tipo se ve en la tarjeta del dia
"""

import os
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


def _funciones(*nombres) -> list:
    return [_funcion(n) for n in nombres]


_ASSERT = "function assert(cond, msg) { if (!cond) { throw new Error(msg); } }"

_RELOJ = """
function fijarReloj(iso) {
  const Real = globalThis.__DateReal || Date;
  globalThis.__DateReal = Real;
  const fijo = Real.parse(iso);
  globalThis.Date = class extends Real {
    constructor(...a) { if (a.length) { super(...a); } else { super(fijo); } }
    static now() { return fijo; }
  };
}
"""

_COMUNES = [_RELOJ, _ASSERT, dashboard.ESC_JS, _linea("function escJs(")]


def _node(partes: list, cuerpo: str, tmp_path, tz: str = "America/Montevideo") -> str:
    fuente = "\n".join(_COMUNES + partes + ["(async () => {", cuerpo,
                                            "})().catch(e => { console.error(e); process.exit(1); });"])
    archivo = tmp_path / "invitados.js"
    archivo.write_text(fuente, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8", env=dict(os.environ, TZ=tz))
    assert r.returncode == 0, r.stderr
    return r.stdout


# El "servidor": la ficha de cada lead, que es de donde sale el mail. El
# buscador de leads no lo trae, por eso el modal pide la ficha.
_DOM = """
const leads = {
  7:  {email: 'hola@opticaluz.uy'},
  8:  {email: 'uno@panaderia.uy, dos@panaderia.uy'},
  9:  {email: ''},
  10: {email: 'gonzalosiuciak@gmail.com'},
};
const pedidos = [];
async function fetch(url, opts) {
  const u = String(url);
  if (u.indexOf('/api/leads/') === 0) {
    const ficha = leads[u.split('/').pop()];
    if (!ficha) return {ok: false, json: async () => ({})};
    return {ok: true, json: async () => ficha};
  }
  pedidos.push({url: u, cuerpo: JSON.parse(opts.body)});
  return {json: async () => ({ok: true})};
}
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
let redibujos = 0;
function renderCalendar() { redibujos++; }
const mails = () => $('ev-invitados').value;
"""

_MODAL = ([_linea("function closeNewEventModal("),
           _linea("const CAL_INVITADOS_FIJOS = "),
           _linea("const CAL_TIPOS = "),
           _linea("let _calMailsLead = "),
           _linea("let _calFijosPuestos = ")]
          + _funciones(
              "openNewEventModal", "_calElegirTipo", "_calPonerCliente",
              "_calQuitarCliente", "_calReglaDelModal", "_calPintarRepeticion",
              "_calTextoRepeticion", "_calPlural", "_calLeerMails", "_calDiaSemana",
              "_calAhoraMvd", "saveEvent", "_calAvisoGoogle", "_calTextoGoogle",
              "_calTogglePresencial",
              "_calOpcionesTipo", "_calPintarTipo", "_calTipoDelModal",
              "_calMezclarInvitados", "_calMailsDelLead", "_calSincronizarInvitados"))

_LOS_TRES = ("juan.pereyra.comunicacion@gmail.com, gonzalosiuciak@gmail.com, "
             "juantomasetti240@gmail.com")


# ── los invitados aparecen solos al elegir el lead ───────────────────────────

@node
def test_al_elegir_el_lead_aparecen_su_mail_y_los_tres_fijos(tmp_path):
    """El caso de Juan: los ve en el campo ANTES de guardar."""
    _node(_MODAL + [_DOM], """
      fijarReloj('2026-09-16T15:00:00Z');
      await openNewEventModal({tipo: 'cliente', clientId: 7, clientName: 'Optica Luz'});
      assert(mails() === 'hola@opticaluz.uy, %s', mails());
      assert($('event-modal').classList.contains('open'), 'no abrio');
    """ % _LOS_TRES, tmp_path)


@node
def test_un_lead_sin_mail_pone_solo_los_tres(tmp_path):
    _node(_MODAL + [_DOM], """
      fijarReloj('2026-09-16T15:00:00Z');
      await openNewEventModal({tipo: 'cliente', clientId: 9, clientName: 'Sin mail'});
      assert(mails() === '%s', mails());
    """ % _LOS_TRES, tmp_path)


@node
def test_un_lead_con_varios_mails_los_pone_a_todos(tmp_path):
    _node(_MODAL + [_DOM], """
      fijarReloj('2026-09-16T15:00:00Z');
      await openNewEventModal({tipo: 'cliente', clientId: 8, clientName: 'Panaderia'});
      assert(mails() === 'uno@panaderia.uy, dos@panaderia.uy, %s', mails());
    """ % _LOS_TRES, tmp_path)


@node
def test_si_el_lead_es_uno_de_los_tres_no_se_repite(tmp_path):
    """El mail del lead va primero; el fijo que es el mismo no se agrega de nuevo."""
    _node(_MODAL + [_DOM], """
      fijarReloj('2026-09-16T15:00:00Z');
      await openNewEventModal({tipo: 'cliente', clientId: 10, clientName: 'Gonza'});
      assert(mails() === 'gonzalosiuciak@gmail.com, juan.pereyra.comunicacion@gmail.com, juantomasetti240@gmail.com', mails());
      const veces = mails().split('gonzalosiuciak@gmail.com').length - 1;
      assert(veces === 1, 'aparece ' + veces + ' veces');
    """, tmp_path)


@node
def test_la_ficha_que_no_contesta_no_rompe_el_modal(tmp_path):
    _node(_MODAL + [_DOM], """
      fijarReloj('2026-09-16T15:00:00Z');
      await openNewEventModal({tipo: 'cliente', clientId: 404, clientName: 'Fantasma'});
      assert(mails() === '%s', mails());
    """ % _LOS_TRES, tmp_path)


@node
def test_lo_que_borra_no_vuelve_y_cambiar_de_lead_solo_cambia_su_mail(tmp_path):
    """Saca dos fijos, agrega uno propio y cambia de lead: se va el mail del
    lead viejo, entra el del nuevo, y ni los fijos ni lo suyo se tocan."""
    _node(_MODAL + [_DOM], """
      fijarReloj('2026-09-16T15:00:00Z');
      await openNewEventModal({tipo: 'cliente', clientId: 7, clientName: 'Optica Luz'});
      $('ev-invitados').value = 'hola@opticaluz.uy, gonzalosiuciak@gmail.com, socio@mia.uy';

      await _calPonerCliente(8, 'Panaderia');

      assert(mails() === 'gonzalosiuciak@gmail.com, socio@mia.uy, uno@panaderia.uy, dos@panaderia.uy', mails());
      assert(mails().indexOf('hola@opticaluz.uy') === -1, 'quedo el mail del lead viejo');
      assert(mails().indexOf('juantomasetti240') === -1, 'volvio un fijo borrado');
    """, tmp_path)


@node
def test_el_modal_de_la_reunion_siguiente_vuelve_a_proponer_los_tres(tmp_path):
    """Borrarlos en una reunion no los apaga para siempre."""
    _node(_MODAL + [_DOM], """
      fijarReloj('2026-09-16T15:00:00Z');
      await openNewEventModal({tipo: 'cliente', clientId: 7, clientName: 'Optica Luz'});
      $('ev-invitados').value = '';

      await openNewEventModal({tipo: 'cliente', clientId: 7, clientName: 'Optica Luz'});

      assert(mails() === 'hola@opticaluz.uy, %s', mails());
    """ % _LOS_TRES, tmp_path)


@node
def test_los_invitados_viajan_en_el_pedido(tmp_path):
    _node(_MODAL + [_DOM], """
      fijarReloj('2026-09-16T15:00:00Z');
      await openNewEventModal({tipo: 'cliente', clientId: 7, clientName: 'Optica Luz'});
      $('ev-date').value = '2026-09-18';
      $('ev-time').value = '19:00';

      await saveEvent();

      const c = pedidos[0].cuerpo;
      assert(JSON.stringify(c.invitados) === JSON.stringify(['hola@opticaluz.uy',
        'juan.pereyra.comunicacion@gmail.com', 'gonzalosiuciak@gmail.com',
        'juantomasetti240@gmail.com']), JSON.stringify(c.invitados));
    """, tmp_path)


# ── el tipo de proyecto ──────────────────────────────────────────────────────

@node
def test_el_selector_tiene_las_cinco_opciones(tmp_path):
    _node(_MODAL + [_DOM], """
      fijarReloj('2026-09-16T15:00:00Z');
      openNewEventModal();
      const html = $('ev-tipo-proy').innerHTML;
      ['automatizacion', 'web', 'ecommerce', 'aMedida', 'otro'].forEach(k =>
        assert(html.indexOf('value="' + k + '"') !== -1, 'falta ' + k));
      ['Automatización', 'Página web', 'E-commerce', 'Desarrollo a medida', 'Otro'].forEach(t =>
        assert(html.indexOf('>' + t + '<') !== -1, 'falta ' + t));
      assert(html.indexOf('Sin especificar') !== -1, 'se tiene que poder no elegir');
      assert((html.match(/<option/g) || []).length === 6, html);
    """, tmp_path)


@node
def test_otro_abre_el_texto_libre_y_los_demas_lo_esconden(tmp_path):
    _node(_MODAL + [_DOM], """
      fijarReloj('2026-09-16T15:00:00Z');
      openNewEventModal();
      assert($('ev-tipo-otro').hidden === true, 'arranca escondido');

      $('ev-tipo-proy').value = 'otro';
      _calPintarTipo('ev');
      assert($('ev-tipo-otro').hidden === false, 'con Otro se escribe');

      $('ev-tipo-proy').value = 'web';
      _calPintarTipo('ev');
      assert($('ev-tipo-otro').hidden === true, 'con los demas no');
    """, tmp_path)


@node
def test_el_tipo_viaja_en_el_pedido(tmp_path):
    _node(_MODAL + [_DOM], """
      fijarReloj('2026-09-16T15:00:00Z');
      await openNewEventModal({tipo: 'cliente', clientId: 7, clientName: 'Optica Luz'});
      $('ev-date').value = '2026-09-18';
      $('ev-time').value = '19:00';
      $('ev-tipo-proy').value = 'automatizacion';
      _calPintarTipo('ev');

      await saveEvent();

      const c = pedidos[0].cuerpo;
      assert(c.tipo_proyecto === 'automatizacion' && c.tipo_otro === '', JSON.stringify(c));
    """, tmp_path)


@node
def test_con_otro_viaja_el_texto_escrito(tmp_path):
    _node(_MODAL + [_DOM], """
      fijarReloj('2026-09-16T15:00:00Z');
      await openNewEventModal({tipo: 'cliente', clientId: 7, clientName: 'Optica Luz'});
      $('ev-date').value = '2026-09-18';
      $('ev-time').value = '19:00';
      $('ev-tipo-proy').value = 'otro';
      _calPintarTipo('ev');
      $('ev-tipo-otro').value = '  Chatbot de WhatsApp  ';

      await saveEvent();

      const c = pedidos[0].cuerpo;
      assert(c.tipo_proyecto === 'otro' && c.tipo_otro === 'Chatbot de WhatsApp', JSON.stringify(c));
    """, tmp_path)


@node
def test_el_texto_de_otro_no_viaja_si_se_cambia_de_opcion(tmp_path):
    _node(_MODAL + [_DOM], """
      fijarReloj('2026-09-16T15:00:00Z');
      await openNewEventModal({tipo: 'cliente', clientId: 7, clientName: 'Optica Luz'});
      $('ev-date').value = '2026-09-18';
      $('ev-time').value = '19:00';
      $('ev-tipo-proy').value = 'otro';
      $('ev-tipo-otro').value = 'Chatbot';
      $('ev-tipo-proy').value = 'web';
      _calPintarTipo('ev');

      await saveEvent();

      const c = pedidos[0].cuerpo;
      assert(c.tipo_proyecto === 'web' && c.tipo_otro === '', JSON.stringify(c));
    """, tmp_path)


@node
def test_el_modal_arranca_sin_tipo_elegido(tmp_path):
    _node(_MODAL + [_DOM], """
      fijarReloj('2026-09-16T15:00:00Z');
      openNewEventModal();
      $('ev-tipo-proy').value = 'web';
      openNewEventModal();
      assert($('ev-tipo-proy').value === '', $('ev-tipo-proy').value);
      assert($('ev-tipo-otro').value === '' && $('ev-tipo-otro').hidden === true, 'no se limpio');
    """, tmp_path)


# ── el editor muestra lo que quedo guardado ──────────────────────────────────

_DOM_EDITOR = """
function elemento(id) {
  const clases = new Set();
  return {id: id, _v: '', get value() { return this._v; }, set value(x) { this._v = String(x); },
          hidden: false, textContent: '', innerHTML: '', href: '', style: {},
          classList: {add: c => clases.add(c), remove: c => clases.delete(c),
                      contains: c => clases.has(c)}};
}
const els = {};
const document = {getElementById: id => els[id] || (els[id] = elemento(id))};
const $ = id => document.getElementById(id);
let _calEditando = null;
let _calEventos = [
  {id: '12', reunion_id: 12, tipo: 'cliente', serie: false, date: '2026-09-18',
   time: '19:00', title: 'Demo Optica Luz', duration_min: 60, origen: 'crm',
   client_name: 'Optica Luz', tipo_proyecto: 'otro', tipo_otro: 'Chatbot',
   invitados: ['hola@opticaluz.uy', 'gonzalosiuciak@gmail.com']},
];
"""

_EDITOR = ([_linea("const CAL_TIPOS = ")]
           + _funciones("_calAbrirEditor", "_calEvento", "_calHoraDeLaReunion",
                        "_calHoraLabel", "_calAhoraMvd", "_calTextoEstadoGoogle",
                        "_calTextoRepeticion", "_calPlural", "_calOpcionesTipo",
                        "_calPintarTipo", "_calTipoDelModal"))


@node
def test_el_editor_muestra_el_tipo_guardado_y_no_agrega_invitados(tmp_path):
    """Lo que Juan saco al crear la reunion sigue afuera cuando la vuelve a abrir."""
    _node(_EDITOR + [_DOM_EDITOR], """
      fijarReloj('2026-09-16T15:00:00Z');
      _calAbrirEditor('12');

      assert($('reprog-tipo-proy').value === 'otro', $('reprog-tipo-proy').value);
      assert($('reprog-tipo-otro').value === 'Chatbot', $('reprog-tipo-otro').value);
      assert($('reprog-tipo-otro').hidden === false, 'con Otro se ve el texto');
      assert($('reprog-invitados').value === 'hola@opticaluz.uy, gonzalosiuciak@gmail.com',
             $('reprog-invitados').value);
      assert($('reprog-invitados').value.indexOf('juantomasetti240') === -1,
             'el editor NO vuelve a proponer los fijos');
      assert(_calTipoDelModal('reprog').tipo_otro === 'Chatbot', 'lo que se manda');
    """, tmp_path)


# ── el celular ───────────────────────────────────────────────────────────────

@node
def test_en_el_celular_se_ve_de_que_es_la_reunion(tmp_path):
    _node(_funciones("_calItemMobile", "_calTextoRepeticion", "_calPlural"), """
      const html = _calItemMobile({id: '12', tipo: 'cliente', title: 'Demo Optica Luz',
        time: '19:00', origen: 'crm', tipo_texto: 'Automatización'});
      assert(html.includes('cal-mobile-ev-tipo'), 'sin la clase del tipo');
      assert(html.includes('Automatización'), 'sin el tipo');
      assert(!html.includes('style='), 'los colores van por clase');

      const sinTipo = _calItemMobile({id: '13', tipo: 'cliente', title: 'Demo', origen: 'crm'});
      assert(!sinTipo.includes('cal-mobile-ev-tipo'), 'sin tipo no dibuja nada');
    """, tmp_path)


@node
def test_el_chip_del_calendario_muestra_el_tipo(tmp_path):
    _node(_funciones("_calChipHtml", "_calTextoRepeticion", "_calPlural"), """
      const html = _calChipHtml({id: '12', tipo: 'cliente', title: 'Demo', time: '19:00',
        origen: 'crm', tipo_texto: 'E-commerce'}, 'cal-event-chip');
      assert(html.includes('cal-chip-tipo'), 'sin la pastilla');
      assert(html.includes('E-commerce'), 'sin el tipo');
      assert(html.includes('title="19:00 Demo · E-commerce"'), html.slice(0, 200));

      const sinTipo = _calChipHtml({id: '13', tipo: 'cliente', title: 'Demo', origen: 'crm'},
                                   'cal-event-chip');
      assert(!sinTipo.includes('cal-chip-tipo'), 'sin tipo no dibuja nada');
    """, tmp_path)


# ── estatico ─────────────────────────────────────────────────────────────────

def test_el_selector_y_el_texto_libre_estan_en_los_dos_modales():
    for campo in ("ev-tipo-proy", "ev-tipo-otro", "reprog-tipo-proy", "reprog-tipo-otro"):
        assert f'id="{campo}"' in HTML, campo


def test_el_texto_de_otro_no_deja_escribir_de_mas():
    """El mismo tope que services/tipos_proyecto.MAX_OTRO."""
    from services import tipos_proyecto as tp
    for campo in ("ev-tipo-otro", "reprog-tipo-otro"):
        m = re.search(r'<input[^>]*id="' + campo + r'"[^>]*>', HTML)
        assert m, campo
        assert f'maxlength="{tp.MAX_OTRO}"' in m.group(0), m.group(0)


def test_el_css_del_tipo_usa_tokens():
    for regla in (r"\.cal-chip-tipo\{([^}]*)\}", r"\.cal-mobile-ev-tipo\{([^}]*)\}"):
        m = re.search(regla, HTML)
        assert m, regla
        assert not re.findall("#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])", m.group(1)), m.group(0)


def test_la_carga_inicial_no_cambio():
    inicio = HTML.index("// Initial load")
    for decl in ("let calLoaded", "let calMonthOffset", "let calView"):
        assert HTML.index(decl) < inicio, decl
    assert "calLoaded = true; renderCalendar();" in HTML[inicio:inicio + 1200]
