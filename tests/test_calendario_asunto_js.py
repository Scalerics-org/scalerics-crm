"""El lado del navegador de "otro asunto", invitados y repeticiones (Juan, 15/9).

El JS vive adentro de un string de Python: como en
`tests/test_calendario_contador_mes.py` y `tests/test_calendario_mobile.py`,
se recortan las funciones y se corren en node contra un DOM y un fetch de
mentira, con el reloj fijo.

  modal        elegir "Otro asunto", titulo obligatorio, mails validados, la
               repeticion que se arma y lo que se manda al servidor
  calendario   las ocurrencias en el mes y en la semana, con su color, su
               etiqueta y el icono de "se repite", sin correrse de dia ni de
               hora con el navegador en UTC o en Tokio
  contador     las de otro asunto no son reuniones de ventas: van aparte
  alcance      mover y borrar una ocurrencia pregunta a cuales aplica
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

_COMUNES = [_RELOJ, _ASSERT, dashboard.ESC_JS, _linea("function escJs("),
            _linea("const CAL_MESES = "), _linea("const CAL_MESES_CORTOS = "),
            _linea("const CAL_DIAS = "), _linea("const CAL_DIAS_PLURAL = ")]


def _node(partes: list, cuerpo: str, tmp_path, tz: str = "America/Montevideo") -> str:
    fuente = "\n".join(_COMUNES + partes + ["(async () => {", cuerpo,
                                            "})().catch(e => { console.error(e); process.exit(1); });"])
    archivo = tmp_path / "asunto.js"
    archivo.write_text(fuente, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8", env=dict(os.environ, TZ=tz))
    assert r.returncode == 0, r.stderr
    return r.stdout


def _funciones(*nombres) -> list:
    return [_funcion(n) for n in nombres]


# El "servidor": lo que devuelve GET /api/calendar/events para una serie de
# viernes 19:00 que arranca el 4/9, mas una reunion con cliente.
_SERVIDOR = """
const VIERNES = {freq: 'semanal', dias: [4], fin: 'nunca'};
const reuniones = [];
['2026-09-04', '2026-09-11', '2026-09-18', '2026-09-25', '2026-10-02', '2026-10-09',
 '2026-10-16', '2026-10-23', '2026-10-30'].forEach(f => reuniones.push({
  id: 'asunto-5@' + f, reunion_id: 5, tipo: 'asunto', serie: true, ocurrencia: f,
  repeticion: VIERNES, date: f, time: '19:00', title: 'Marketing semanal',
  duration_min: 60, origen: 'crm', invitados: ['a@x.com', 'b@y.com', 'c@z.com']}));
reuniones.push({id: '12', reunion_id: 12, tipo: 'cliente', serie: false, date: '2026-09-10',
                time: '10:00', title: 'Demo Optica Luz', origen: 'crm', client_name: 'Optica Luz'});
const pedidos = [];
function fetch(url) {
  pedidos.push(url);
  const start = url.match(/start=([0-9-]+)/)[1];
  const end = url.match(/end=([0-9-]+)/)[1];
  const cuerpo = {events: reuniones.filter(e => e.date >= start && e.date <= end)};
  return Promise.resolve({json: async () => cuerpo});
}
const window = {innerWidth: 1280};
const els = {};
const document = {getElementById: id => els[id] || (els[id] =
  {id: id, textContent: '', innerHTML: '', style: {}})};
const grilla = () => document.getElementById('cal-days').innerHTML;
const contador = () => document.getElementById('cal-count').textContent;
let calLoaded = false; let calMonthOffset = 0; let calWeekOffset = 0;
let calView = 'mes'; let _calPedido = 0; let _calEventos = [];
function _calSeleccionarDiaMobile() {}
"""

_CALENDARIO = _funciones(
    "_calIsoLocal", "_calAhoraMvd", "_calMesVisto", "_calRangoMes", "_calMesDeLaSemana",
    "_calRangoSemana", "_calResumenMes", "_calPlural", "_calTextoContador",
    "_calPintarContador", "renderCalendar", "renderCalWeek", "_calWeekStart",
    "_calHourRange", "_calHoraLabel", "_calChip", "_calWeekChip", "_calChipHtml",
    "_calTextoRepeticion")


# ── el calendario con ocurrencias ────────────────────────────────────────────

@node
@pytest.mark.parametrize("tz", ["America/Montevideo", "UTC", "Asia/Tokyo"])
def test_el_mes_dibuja_todos_los_viernes_con_su_etiqueta_y_el_icono(tmp_path, tz):
    _node(_CALENDARIO + [_SERVIDOR], """
      fijarReloj('2026-09-01T12:00:00Z');   // 09:00 del 1/9 en Montevideo
      await renderCalendar();
      const html = grilla();
      // Hasta la celda siguiente. No sirve cortar en 'class="cal-cell': el
      // numero del dia es "cal-cell-day" y dejaria afuera los chips.
      const celda = f => {
        const i = html.indexOf('data-date="' + f + '"');
        assert(i !== -1, 'no esta la celda ' + f);
        const j = html.indexOf(' data-date="', i + 1);
        return html.slice(i, j === -1 ? html.length : j);
      };
      ['2026-09-04', '2026-09-11', '2026-09-18', '2026-09-25'].forEach(f => {
        const c = celda(f);
        assert(c.includes('Marketing semanal'), 'falta el viernes ' + f);
        assert(c.includes('tipo-asunto'), 'sin el color de otro asunto: ' + f);
        assert(c.includes('cal-chip-tag">Asunto'), 'sin la etiqueta: ' + f);
        assert(c.includes('cal-chip-rep'), 'sin el icono de se repite: ' + f);
        assert(c.includes('19:00'), 'se corrio la hora: ' + f);
        assert(c.includes('Todos los viernes, sin fin'), 'sin el detalle en el title: ' + f);
      });
      ['2026-09-03', '2026-09-05', '2026-09-12', '2026-09-26'].forEach(f =>
        assert(!celda(f).includes('Marketing semanal'), 'aparecio un dia que no es viernes: ' + f));
      assert((html.match(/cal-chip-title">Marketing semanal/g) || []).length === 4, 'no son 4');
      const demo = celda('2026-09-10');
      assert(demo.includes('Demo Optica Luz') && !demo.includes('tipo-asunto') && !demo.includes('cal-chip-rep'),
             'la reunion con cliente no lleva etiqueta ni icono');

      calMonthOffset = 1;
      await renderCalendar();
      assert((grilla().match(/cal-chip-title">Marketing semanal/g) || []).length === 5, 'octubre tiene 5');
    """, tmp_path, tz)


@node
@pytest.mark.parametrize("tz", ["America/Montevideo", "UTC", "Asia/Tokyo"])
def test_la_semana_pone_el_viernes_19_en_su_franja(tmp_path, tz):
    _node(_CALENDARIO + [_SERVIDOR], """
      window.innerWidth = 1280;
      calView = 'semana';
      fijarReloj('2026-09-16T12:00:00Z');   // miercoles 16/9
      await renderCalendar();
      const html = grilla();
      const franjas = html.split('class="calw-slot"').slice(1);
      const con = franjas.filter(f => f.includes('Marketing semanal'));
      assert(con.length === 1, 'tiene que estar en una sola franja: ' + con.length);
      assert(con[0].startsWith(' data-date="2026-09-18" data-time="19:00"'), con[0].slice(0, 60));
      assert(con[0].includes('tipo-asunto') && con[0].includes('cal-chip-rep'), 'sin color o icono');
    """, tmp_path, tz)


@node
def test_el_contador_cuenta_otros_asuntos_aparte(tmp_path):
    _node(_CALENDARIO + [_SERVIDOR], """
      fijarReloj('2026-09-01T12:00:00Z');
      await renderCalendar();
      assert(contador() === 'Septiembre 2026 · 1 reunión · 0 hechas · 1 por venir · 4 de otros asuntos', contador());

      calMonthOffset = 1;
      await renderCalendar();
      assert(contador() === 'Octubre 2026 · 0 agendadas · 5 de otros asuntos', contador());

      const r = _calResumenMes([{date: '2026-09-02', time: '09:00', tipo: 'asunto'}], 2026, 8, '2026-09-15T10:00');
      assert(r.total === 0 && r.hechas === 0 && r.asuntos === 1, JSON.stringify(r));
      assert(_calTextoContador(r, 2026, 8) === 'Septiembre 2026 · 0 reuniones · 0 hechas · 0 por venir · 1 de otro asunto',
             _calTextoContador(r, 2026, 8));
    """, tmp_path)


@node
def test_el_contador_sin_asuntos_queda_igual_que_antes(tmp_path):
    _node(_CALENDARIO + [_SERVIDOR], """
      const r = _calResumenMes([{date: '2026-09-02', time: '09:00'}], 2026, 8, '2026-09-15T10:00');
      assert(_calTextoContador(r, 2026, 8) === 'Septiembre 2026 · 1 reunión · 1 hecha · 0 por venir',
             _calTextoContador(r, 2026, 8));
    """, tmp_path)


# ── textos y mails ───────────────────────────────────────────────────────────

@node
def test_el_texto_de_la_repeticion(tmp_path):
    _node(_funciones("_calPlural", "_calTextoRepeticion"), """
      const t = _calTextoRepeticion;
      assert(t({freq: 'semanal', dias: [4], fin: 'nunca'}, '19:00') === 'Todos los viernes a las 19:00, sin fin', t({freq: 'semanal', dias: [4], fin: 'nunca'}, '19:00'));
      assert(t({freq: 'semanal', dias: [0, 2, 4], fin: 'veces', veces: 8}) === 'Todas las semanas: los lunes, los miércoles y los viernes, 8 veces');
      assert(t({freq: 'quincenal', dias: [1], fin: 'fecha', hasta: '2026-12-31'}, '08:30') === 'Cada 2 semanas: los martes a las 08:30, hasta el 31/12/2026');
      assert(t({freq: 'diaria', fin: 'veces', veces: 1}) === 'Todos los días, 1 vez');
      assert(t({freq: 'mensual', fin: 'nunca'}, '10:00', '2026-09-18') === 'Todos los meses el día 18 a las 10:00, sin fin');
      assert(t(null) === '' && t({}) === '', 'sin regla no hay texto');
    """, tmp_path)


@node
def test_leer_los_mails(tmp_path):
    _node(_funciones("_calLeerMails"), """
      const a = _calLeerMails('ana@agencia.com, Pablo@Estudio.uy;sofi@marca.com.uy  ana@AGENCIA.com');
      assert(JSON.stringify(a.lista) === JSON.stringify(['ana@agencia.com', 'Pablo@Estudio.uy', 'sofi@marca.com.uy']), JSON.stringify(a));
      assert(a.malos.length === 0, JSON.stringify(a));
      const b = _calLeerMails('pablo@, sin-arroba.com, ana@agencia, @marca.com, ok@bien.com');
      assert(JSON.stringify(b.malos) === JSON.stringify(['pablo@', 'sin-arroba.com', 'ana@agencia', '@marca.com']), JSON.stringify(b));
      assert(JSON.stringify(b.lista) === JSON.stringify(['ok@bien.com']), JSON.stringify(b));
      assert(_calLeerMails('').lista.length === 0 && _calLeerMails(null).malos.length === 0, 'vacio');
    """, tmp_path)


# ── el modal ─────────────────────────────────────────────────────────────────

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
let redibujos = 0;
function renderCalendar() { redibujos++; }
const pedidos = [];
async function fetch(url, opts) {
  pedidos.push({url: url, cuerpo: JSON.parse(opts.body)});
  return {json: async () => ({ok: true})};
}
"""

# `closeNewEventModal` esta en una sola linea: `_funcion` se seguiria de largo
# hasta la proxima llave en la columna 0 y arrastraria codigo de nivel superior.
_MODAL = ([_linea("function closeNewEventModal("),
           _linea("const CAL_INVITADOS_FIJOS = "), _linea("const CAL_TIPOS = "),
           _linea("let _calMailsLead = "), _linea("let _calFijosPuestos = ")]
          + _funciones(
    "openNewEventModal", "_calElegirTipo", "_calPonerCliente",
    "_calQuitarCliente", "_calReglaDelModal", "_calPintarRepeticion", "_calTextoRepeticion",
    "_calPlural", "_calLeerMails", "_calDiaSemana", "_calAhoraMvd", "saveEvent",
    "_calAvisoGoogle", "_calTextoGoogle", "_calOpcionesTipo", "_calPintarTipo",
    "_calTipoDelModal", "_calMezclarInvitados", "_calMailsDelLead",
    "_calSincronizarInvitados", "_calTogglePresencial",
))


@node
def test_marketing_semanal_desde_el_modal(tmp_path):
    """El caso de Juan, de punta a punta en el modal."""
    _node(_MODAL + [_DOM_MODAL], """
      fijarReloj('2026-09-15T02:00:00Z');   // 23:00 del 14/9 en Montevideo
      openNewEventModal();
      assert($('event-modal').classList.contains('open'), 'no abrio');
      assert($('ev-date').value === '2026-09-14', 'la fecha de hoy es la de Montevideo: ' + $('ev-date').value);
      assert($('ev-tipo-cliente').classList.contains('active') && !$('ev-bloque-cliente').hidden, 'arranca con cliente');

      _calElegirTipo('asunto');
      assert($('ev-bloque-cliente').hidden === true, 'con otro asunto no se pide cliente');
      assert($('ev-title-label').textContent === 'Asunto', $('ev-title-label').textContent);
      assert($('ev-title').placeholder === 'Ej: Marketing semanal', $('ev-title').placeholder);
      assert($('ev-tipo-asunto').attrs['aria-checked'] === 'true', 'aria');

      await saveEvent();
      assert(pedidos.length === 0, 'sin titulo no se manda');
      assert($('ev-error').textContent.includes('asunto'), $('ev-error').textContent);

      $('ev-title').value = 'Marketing semanal';
      $('ev-date').value = '2026-09-18';
      $('ev-time').value = '19:00';
      $('ev-rep-freq').value = 'semanal';
      _calPintarRepeticion();
      assert(!$('ev-rep-opciones').hidden && !$('ev-rep-dias-bloque').hidden, 'faltan las opciones');
      assert($('ev-rep-hasta-bloque').hidden && $('ev-rep-veces-bloque').hidden, 'fin nunca');
      assert(casillas[4].checked && casillas.filter(c => c.checked).length === 1, 'arranca en viernes');
      assert($('ev-rep-resumen').textContent === 'Todos los viernes a las 19:00, sin fin', $('ev-rep-resumen').textContent);

      $('ev-invitados').value = 'ana@agencia.com, pablo@';
      await saveEvent();
      assert(pedidos.length === 0, 'con un mail malo no se manda');
      assert($('ev-error').textContent.includes('pablo@'), $('ev-error').textContent);

      $('ev-invitados').value = 'ana@agencia.com, pablo@estudio.uy; sofi@marca.com';
      await saveEvent();
      assert(pedidos.length === 1, 'no se mando');
      const p = pedidos[0];
      assert(p.url === '/api/calendar/events', p.url);
      const c = p.cuerpo;
      assert(c.tipo === 'asunto' && c.client_id === null, JSON.stringify(c));
      assert(c.title === 'Marketing semanal' && c.date === '2026-09-18' && c.time === '19:00', JSON.stringify(c));
      assert(JSON.stringify(c.invitados) === JSON.stringify(['ana@agencia.com', 'pablo@estudio.uy', 'sofi@marca.com']), JSON.stringify(c.invitados));
      assert(c.repeticion.freq === 'semanal' && c.repeticion.fin === 'nunca', JSON.stringify(c.repeticion));
      assert(JSON.stringify(c.repeticion.dias) === '[4]', JSON.stringify(c.repeticion));
      assert(!$('event-modal').classList.contains('open') && redibujos === 1, 'no cerro o no redibujo');
    """, tmp_path, tz="UTC")


@node
def test_con_cliente_desde_la_ficha_y_sin_cliente_no_se_manda(tmp_path):
    _node(_MODAL + [_DOM_MODAL], """
      fijarReloj('2026-09-15T15:00:00Z');
      await openNewEventModal({tipo: 'cliente', clientId: 7, clientName: 'Optica Luz'});
      assert($('ev-title').value === 'Reunión con Optica Luz', $('ev-title').value);
      assert(!$('ev-cliente-elegido').hidden && $('ev-cliente-elegido').innerHTML.includes('Optica Luz'), 'no muestra el cliente');
      assert($('ev-cliente-buscar').hidden, 'el buscador sobra');
      assert($('ev-rep-opciones').hidden, 'no se repite por defecto');
      await saveEvent();
      const c = pedidos[0].cuerpo;
      assert(c.tipo === 'cliente' && c.client_id === '7' && c.repeticion === null, JSON.stringify(c));
      // Desde el 16/9 elegir un lead propone los tres fijos de Scalerics. Aca
      // el fetch de mentira no devuelve ficha, asi que el mail del lead no va.
      assert(JSON.stringify(c.invitados) === JSON.stringify(CAL_INVITADOS_FIJOS), JSON.stringify(c.invitados));

      openNewEventModal();
      assert($('ev-client-id').value === '' && $('ev-title').value === '', 'no se limpio');
      $('ev-title').value = 'Demo';
      await saveEvent();
      assert(pedidos.length === 1, 'sin cliente no se manda');
      assert($('ev-error').textContent.includes('Otro asunto'), $('ev-error').textContent);
    """, tmp_path)


@node
def test_fin_por_fecha_y_por_cantidad_en_el_modal(tmp_path):
    _node(_MODAL + [_DOM_MODAL], """
      fijarReloj('2026-09-15T15:00:00Z');
      openNewEventModal();
      _calElegirTipo('asunto');
      $('ev-title').value = 'Cierre de mes';
      $('ev-date').value = '2026-09-18';
      $('ev-time').value = '10:00';
      $('ev-rep-freq').value = 'mensual';
      $('ev-rep-fin').value = 'veces';
      $('ev-rep-veces').value = '6';
      _calPintarRepeticion();
      assert($('ev-rep-dias-bloque').hidden, 'mensual no elige dias');
      assert(!$('ev-rep-veces-bloque').hidden && $('ev-rep-hasta-bloque').hidden, 'bloques de fin');
      assert($('ev-rep-resumen').textContent === 'Todos los meses el día 18 a las 10:00, 6 veces', $('ev-rep-resumen').textContent);

      $('ev-rep-fin').value = 'fecha';
      _calPintarRepeticion();
      await saveEvent();
      assert(pedidos.length === 0 && $('ev-error').textContent.includes('hasta qué fecha'), $('ev-error').textContent);
      $('ev-rep-hasta').value = '2026-09-01';
      await saveEvent();
      assert(pedidos.length === 0 && $('ev-error').textContent.includes('antes'), $('ev-error').textContent);
      $('ev-rep-hasta').value = '2026-12-31';
      _calPintarRepeticion();
      assert($('ev-rep-resumen').textContent === 'Todos los meses el día 18 a las 10:00, hasta el 31/12/2026', $('ev-rep-resumen').textContent);
      await saveEvent();
      const r = pedidos[0].cuerpo.repeticion;
      assert(r.freq === 'mensual' && r.fin === 'fecha' && r.hasta === '2026-12-31' && r.dias === undefined, JSON.stringify(r));
    """, tmp_path)


# ── solo esta / esta y las siguientes / todas ────────────────────────────────

_DOM_ALCANCE = """
function elemento(id) {
  const clases = new Set();
  return {id: id, textContent: '', classList: {add: c => clases.add(c),
          remove: c => clases.delete(c), contains: c => clases.has(c)}};
}
const els = {};
const document = {getElementById: id => els[id] || (els[id] = elemento(id))};
const $ = id => document.getElementById(id);
let _calAlcanceResolver = null;
let _calEventos = [
  {id: 'asunto-5@2026-09-18', reunion_id: 5, tipo: 'asunto', serie: true, ocurrencia: '2026-09-18',
   date: '2026-09-18', time: '19:00', title: 'Marketing semanal', duration_min: 60},
  {id: '12@2026-09-11', reunion_id: 12, tipo: 'cliente', serie: true, ocurrencia: '2026-09-11',
   date: '2026-09-11', time: '10:00', title: 'Seguimiento', duration_min: 30},
  {id: '40', reunion_id: 40, tipo: 'cliente', serie: false, date: '2026-09-10', time: '10:00', title: 'Demo'},
];
let confirmados = 0;
function confirm() { confirmados++; return true; }
function alert(m) { throw new Error('alert: ' + m); }
let redibujos = 0;
function renderCalendar() { redibujos++; }
const pedidos = [];
async function fetch(url, opts) {
  pedidos.push({url: url, method: opts.method, cuerpo: opts.body ? JSON.parse(opts.body) : null});
  return {json: async () => ({ok: true})};
}
const esperar = () => new Promise(ok => setTimeout(ok, 0));
"""

_ALCANCE = _funciones("deleteCalEvent", "_calMover", "_calElegirAlcance", "_calResolverAlcance",
                      "_calRutaReunion", "_calEvento", "_calAvisoGoogle", "_calTextoGoogle")


@node
def test_borrar_una_ocurrencia_pregunta_a_cuales(tmp_path):
    _node(_ALCANCE + [_DOM_ALCANCE], """
      let p = deleteCalEvent('asunto-5@2026-09-18', 'Marketing semanal');
      await esperar();
      assert($('alcance-modal').classList.contains('open'), 'no pregunto');
      assert($('alcance-titulo').textContent.includes('Borrar'), $('alcance-titulo').textContent);
      assert(confirmados === 0, 'una serie no usa el confirm de siempre');
      _calResolverAlcance('esta');
      await p;
      assert(!$('alcance-modal').classList.contains('open'), 'no cerro');
      assert(pedidos[0].method === 'DELETE', pedidos[0].method);
      assert(pedidos[0].url === '/api/calendar/asuntos/5?alcance=esta&ocurrencia=2026-09-18', pedidos[0].url);

      p = deleteCalEvent('asunto-5@2026-09-18', 'Marketing semanal');
      await esperar();
      _calResolverAlcance(null);
      await p;
      assert(pedidos.length === 1, 'cancelar no borra');

      p = deleteCalEvent('12@2026-09-11', 'Seguimiento');
      await esperar();
      _calResolverAlcance('siguientes');
      await p;
      assert(pedidos[1].url === '/api/calendar/meetings/12?alcance=siguientes&ocurrencia=2026-09-11', pedidos[1].url);

      await deleteCalEvent('40', 'Demo');
      assert(confirmados === 1 && pedidos[2].url === '/api/calendar/meetings/40', pedidos[2].url);
      assert(redibujos === 3, 'redibujos=' + redibujos);
    """, tmp_path)


@node
def test_arrastrar_una_ocurrencia_pregunta_y_manda_el_alcance(tmp_path):
    _node(_ALCANCE + [_DOM_ALCANCE], """
      let p = _calMover(_calEventos[0], '2026-09-17', '18:00');
      await esperar();
      assert($('alcance-titulo').textContent.includes('Cambiar'), $('alcance-titulo').textContent);
      _calResolverAlcance('esta');
      await p;
      assert(pedidos[0].method === 'PATCH' && pedidos[0].url === '/api/calendar/asuntos/5', pedidos[0].url);
      let c = pedidos[0].cuerpo;
      assert(c.date === '2026-09-17' && c.time === '18:00' && c.ocurrencia === '2026-09-18' && c.alcance === 'esta', JSON.stringify(c));
      assert(c.title === 'Marketing semanal' && c.duration_min === 60, 'solo esta conserva nombre y duracion');

      p = _calMover(_calEventos[0], '2026-09-18', '20:00');
      await esperar();
      _calResolverAlcance('todas');
      await p;
      c = pedidos[1].cuerpo;
      assert(c.alcance === 'todas' && c.title === undefined, JSON.stringify(c));

      p = _calMover(_calEventos[0], '2026-09-18', '21:00');
      await esperar();
      _calResolverAlcance(null);
      await p;
      assert(pedidos.length === 2, 'cancelar no mueve');

      await _calMover(_calEventos[2], '2026-09-11', '10:00');
      assert(pedidos[2].url === '/api/calendar/meetings/40' && pedidos[2].cuerpo.alcance === undefined, JSON.stringify(pedidos[2]));
    """, tmp_path)


# ── celular ──────────────────────────────────────────────────────────────────

@node
def test_en_el_celular_se_ve_la_etiqueta_y_la_repeticion_y_los_botones_andan(tmp_path):
    _node(_funciones("_calItemMobile", "_calTextoRepeticion", "_calPlural") + ["""
      const llamadas = [];
      function _calAbrirEditor(id) { llamadas.push(['editar', id]); }
      function deleteCalEvent(id, titulo) { llamadas.push(['borrar', id, titulo]); }
      function tocar(html, clase) {
        const m = html.match(new RegExp('class="[^"]*' + clase + '[^"]*"[^>]*onclick="([^"]*)"'));
        if (!m) return false;
        eval(m[1].replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&'));
        return true;
      }
    """], """
      const html = _calItemMobile({id: 'asunto-5@2026-09-18', reunion_id: 5, tipo: 'asunto', serie: true,
        ocurrencia: '2026-09-18', repeticion: {freq: 'semanal', dias: [4], fin: 'nunca'},
        title: 'Marketing semanal', time: '19:00', origen: 'crm'});
      assert(html.includes('cal-mobile-ev tipo-asunto'), 'sin el color');
      assert(html.includes('Otro asunto'), 'sin la etiqueta');
      assert(html.includes('↻ Todos los viernes, sin fin'), 'sin la repeticion');
      assert(html.includes('19:00'), 'sin la hora');
      assert(!html.includes('style='), 'los colores van por clase');
      assert(tocar(html, 'cal-mobile-act-editar') && tocar(html, 'cal-mobile-act-borrar'), 'faltan botones');
      assert(JSON.stringify(llamadas) === JSON.stringify([['editar', 'asunto-5@2026-09-18'],
             ['borrar', 'asunto-5@2026-09-18', 'Marketing semanal']]), JSON.stringify(llamadas));

      const cliente = _calItemMobile({id: '40', tipo: 'cliente', title: 'Demo', origen: 'crm'});
      assert(!cliente.includes('Otro asunto') && !cliente.includes('↻'), 'una suelta con cliente queda como antes');
    """, tmp_path)


# ── estatico ─────────────────────────────────────────────────────────────────

def test_el_css_nuevo_usa_tokens():
    reglas = re.findall(r"^(\.cal-(?:tipo|cliente|rep|modal|serie|alcance|chip-tag|chip-rep|mobile-ev-tag|mobile-ev-rep|leyenda-asunto|leyenda-rep)[^{]*)\{([^}]*)\}",
                        HTML, re.M)
    assert len(reglas) >= 20, len(reglas)
    for selector, cuerpo in reglas:
        sueltos = re.findall("#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])", cuerpo)
        # Blanco sobre el azul de marca es la excepcion de siempre (.btn-primary):
        # `--texto-fuerte` seria azul marino sobre azul en el tema claro.
        if "background:var(--azul)" in cuerpo:
            sueltos = [h for h in sueltos if h != "#fff"]
        assert not sueltos, f"{selector} tiene colores a mano: {sueltos}"
    assert "body.light .cal-tipo" not in HTML and "body.light .cal-alcance" not in HTML


def test_el_chip_de_otro_asunto_le_gana_al_borde_del_tema_claro():
    """`body.light .cal-event-chip` pinta el borde de azul con mas especificidad."""
    assert "body.light .cal-event-chip.tipo-asunto" in HTML


def test_editar_y_borrar_van_a_la_ruta_de_cada_tipo():
    for nombre in ("deleteCalEvent", "_calMover", "_calGuardarHorario"):
        cuerpo = _funcion(nombre)
        assert "_calRutaReunion(" in cuerpo, nombre
        assert "'/api/calendar/meetings/' +" not in cuerpo, nombre


def test_la_carga_inicial_no_cambio():
    inicio = HTML.index("// Initial load")
    for decl in ("let calLoaded", "let calMonthOffset", "let calView"):
        assert HTML.index(decl) < inicio, decl
    assert "calLoaded = true; renderCalendar();" in HTML[inicio:inicio + 1200]
