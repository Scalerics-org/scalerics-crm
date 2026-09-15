"""Contador de reuniones del mes y navegacion mes a mes (pedido de Juan, 15/9).

"Quiero que en calendario figure arriba la cantidad de reuniones que van en el
mes y poder deslizar para adelante o para atras en el mes y ver las que
hubieron."

  contador     arriba del calendario, del MES que se mira (tambien en la vista
               semana): "Septiembre 2026 · 18 reuniones · 11 hechas · 7 por
               venir". Mes pasado: "N reuniones". Mes futuro: "N agendadas".
               Hechas / por venir contra la hora de Montevideo (UTC-3).
  navegacion   flechas y "Hoy"; en el celular, ademas, deslizar sobre la
               grilla. Cada mes pide su propio rango, pasados incluidos.

El JS vive adentro de un string de Python: los tests recortan las funciones y
las corren en node, contra un DOM y un fetch de mentira, con el reloj fijo.
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

# Fija el reloj: `new Date()` y `Date.now()` devuelven ese instante. Las
# fechas con argumentos siguen andando igual.
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

_PURAS = ["_calIsoLocal", "_calAhoraMvd", "_calMesVisto", "_calRangoMes",
          "_calMesDeLaSemana", "_calRangoSemana", "_calResumenMes", "_calPlural",
          "_calTextoContador"]


def _node(fuente: str, tmp_path, tz: str = "America/Montevideo") -> str:
    archivo = tmp_path / "cal_mes.js"
    archivo.write_text(fuente, encoding="utf-8")
    env = dict(os.environ, TZ=tz)
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _correr_puras(cuerpo: str, tmp_path, tz: str = "America/Montevideo") -> str:
    fuente = "\n".join([_linea("const CAL_MESES = "), _ASSERT]
                       + [_funcion(n) for n in _PURAS] + [cuerpo])
    return _node(fuente, tmp_path, tz)


# ─── el contador ──────────────────────────────────────────────────────────────

_EVENTOS = """
const eventos = [
  {id: '1', date: '2026-08-31', time: '23:00', title: 'agosto'},
  {id: '2', date: '2026-09-01', time: '10:00', title: 'principio de mes'},
  {id: '3', date: '2026-09-15', time: '14:00', title: 'hoy, ya paso'},
  {id: '4', date: '2026-09-15', time: '15:00', title: 'hoy, todavia no'},
  {id: '5', date: '2026-09-15', time: '', title: 'hoy, dia entero'},
  {id: '6', date: '2026-09-30', time: '10:00', title: 'fin de mes'},
  {id: '7', date: '2026-10-01', time: '09:00', title: 'octubre'},
];
const texto = (anio, mes, ahora) =>
  _calTextoContador(_calResumenMes(eventos, anio, mes, ahora), anio, mes);
"""


@node
def test_mes_actual_cuenta_hechas_y_por_venir(tmp_path):
    """El 15/9 a las 14:30: la de las 14 ya paso, la de las 15 no, y la de dia
    entero de hoy todavia no termino. Las de agosto y octubre no entran."""
    _correr_puras(_EVENTOS + """
      const r = _calResumenMes(eventos, 2026, 8, '2026-09-15T14:30');
      assert(r.tipo === 'actual', r.tipo);
      assert(r.total === 5, 'total=' + r.total);
      assert(r.hechas === 2, 'hechas=' + r.hechas);
      assert(r.porVenir === 3, 'porVenir=' + r.porVenir);
      const t = texto(2026, 8, '2026-09-15T14:30');
      assert(t === 'Septiembre 2026 · 5 reuniones · 2 hechas · 3 por venir', t);
    """, tmp_path)


@node
def test_una_reunion_que_empieza_justo_ahora_cuenta_como_hecha(tmp_path):
    _correr_puras(_EVENTOS + """
      const r = _calResumenMes(eventos, 2026, 8, '2026-09-15T15:00');
      assert(r.hechas === 3 && r.porVenir === 2, JSON.stringify(r));
    """, tmp_path)


@node
def test_mes_pasado_solo_dice_cuantas_hubo(tmp_path):
    _correr_puras(_EVENTOS + """
      const t = texto(2026, 7, '2026-09-15T14:30');
      assert(t === 'Agosto 2026 · 1 reunión', t);
      const vacio = texto(2026, 5, '2026-09-15T14:30');
      assert(vacio === 'Junio 2026 · 0 reuniones', vacio);
    """, tmp_path)


@node
def test_mes_futuro_dice_agendadas(tmp_path):
    _correr_puras(_EVENTOS + """
      const t = texto(2026, 9, '2026-09-15T14:30');
      assert(t === 'Octubre 2026 · 1 agendada', t);
      eventos.push({id: '8', date: '2026-10-20', time: '11:00'});
      const dos = texto(2026, 9, '2026-09-15T14:30');
      assert(dos === 'Octubre 2026 · 2 agendadas', dos);
    """, tmp_path)


@node
def test_mes_actual_sin_reuniones(tmp_path):
    _correr_puras("""
      const t = _calTextoContador(_calResumenMes([], 2026, 8, '2026-09-15T10:00'), 2026, 8);
      assert(t === 'Septiembre 2026 · 0 reuniones · 0 hechas · 0 por venir', t);
      const uno = _calTextoContador(
        _calResumenMes([{date: '2026-09-02', time: '09:00'}], 2026, 8, '2026-09-15T10:00'), 2026, 8);
      assert(uno === 'Septiembre 2026 · 1 reunión · 1 hecha · 0 por venir', uno);
    """, tmp_path)


@node
@pytest.mark.parametrize("tz", ["UTC", "America/Montevideo", "Asia/Tokyo"])
def test_borde_de_mes_en_hora_de_montevideo(tmp_path, tz):
    """01:30 UTC del 1/10 son las 22:30 del 30/9 en Montevideo: el mes que
    corre es setiembre, octubre todavia es futuro, y la reunion de las 23 del
    30 esta por venir. No depende del huso del navegador (el servidor esta en
    UTC)."""
    _correr_puras(_EVENTOS + """
      const ahora = _calAhoraMvd(Date.UTC(2026, 9, 1, 1, 30));
      assert(ahora === '2026-09-30T22:30', ahora);
      const visto = _calMesVisto(0, ahora);
      assert(visto.anio === 2026 && visto.mes === 8, JSON.stringify(visto));

      eventos.push({id: '9', date: '2026-09-30', time: '23:00'});
      const r = _calResumenMes(eventos, 2026, 8, ahora);
      assert(r.tipo === 'actual', r.tipo);
      assert(r.total === 6 && r.hechas === 5 && r.porVenir === 1, JSON.stringify(r));
      assert(_calResumenMes(eventos, 2026, 9, ahora).tipo === 'futuro', 'octubre');

      // A las 03:00 UTC ya es 1/10 en Montevideo.
      const despues = _calAhoraMvd(Date.UTC(2026, 9, 1, 3, 0));
      assert(despues === '2026-10-01T00:00', despues);
      assert(_calResumenMes(eventos, 2026, 8, despues).tipo === 'pasado', 'setiembre paso');
      assert(_calResumenMes(eventos, 2026, 9, despues).tipo === 'actual', 'octubre corre');
    """, tmp_path, tz)


@node
def test_el_mes_visto_cruza_de_anio(tmp_path):
    _correr_puras("""
      const a = _calMesVisto(-9, '2026-09-15T10:00');
      assert(a.anio === 2025 && a.mes === 11, JSON.stringify(a));
      const b = _calMesVisto(4, '2026-09-15T10:00');
      assert(b.anio === 2027 && b.mes === 0, JSON.stringify(b));
      const r = _calRangoMes(2024, 1);
      assert(r.start === '2024-02-01' && r.end === '2024-02-29', JSON.stringify(r));
    """, tmp_path)


@node
def test_la_semana_cuenta_su_mes(tmp_path):
    """Si hoy esta en la semana, su mes; si no, el del jueves. Y se pide la
    semana entera mas el mes entero."""
    _correr_puras("""
      const hoySemana = _calMesDeLaSemana(new Date(2026, 8, 28), '2026-09-30');
      assert(hoySemana.mes === 8, 'con hoy adentro: ' + JSON.stringify(hoySemana));
      const cruza = _calMesDeLaSemana(new Date(2026, 8, 28), '2026-09-15');
      assert(cruza.mes === 9, 'jueves 1/10: ' + JSON.stringify(cruza));
      const agosto = _calMesDeLaSemana(new Date(2026, 7, 31), '2026-09-15');
      assert(agosto.mes === 8, 'jueves 3/9: ' + JSON.stringify(agosto));

      const r1 = _calRangoSemana(new Date(2026, 8, 28), 2026, 8);
      assert(r1.start === '2026-09-01' && r1.end === '2026-10-04', JSON.stringify(r1));
      const r2 = _calRangoSemana(new Date(2026, 7, 31), 2026, 8);
      assert(r2.start === '2026-08-31' && r2.end === '2026-09-30', JSON.stringify(r2));
      const r3 = _calRangoSemana(new Date(2026, 8, 7), 2026, 8);
      assert(r3.start === '2026-09-01' && r3.end === '2026-09-30', JSON.stringify(r3));
    """, tmp_path)


# ─── navegacion: el calendario de verdad, con DOM y fetch de mentira ─────────

def _correr_calendario(cuerpo: str, tmp_path, tz: str = "America/Montevideo") -> str:
    fuente = "\n".join([
        _RELOJ, _ASSERT,
        _linea("const CAL_MESES = "), _linea("const CAL_MESES_CORTOS = "),
        _linea("const CAL_DIAS = "),
        "let calLoaded = false; let calMonthOffset = 0; let calWeekOffset = 0;",
        "let calView = 'mes'; let _calPedido = 0; let _calEventos = [];",
        "let _calToque = null;",
    ] + [_funcion(n) for n in _PURAS + [
        "_calPintarContador", "calShift", "calHoy", "renderCalendar", "renderCalWeek",
        "_calWeekStart", "_calHourRange", "_calHoraLabel",
        "_calDireccionSwipe", "_calToqueInicio", "_calToqueFin",
    ]] + ["""
        function _calChip(ev) { return '[' + ev.title + ']'; }
        function _calWeekChip(ev) { return '[' + ev.title + ']'; }
        function _calSeleccionarDiaMobile() {}
        const window = {innerWidth: 390};
        const els = {};
        const document = {getElementById: id => els[id] || (els[id] =
          {id: id, textContent: '', innerHTML: '', style: {}})};
        const contador = () => document.getElementById('cal-count').textContent;
        const titulo = () => document.getElementById('cal-week-label').textContent;
        const grilla = () => document.getElementById('cal-days').innerHTML;

        // El "servidor": filtra por start/end como routes/calendar.py.
        const reuniones = [
          {id: '1', date: '2026-07-10', time: '11:00', title: 'Julio'},
          {id: '2', date: '2026-08-05', time: '10:00', title: 'Agosto A'},
          {id: '3', date: '2026-08-20', time: '16:00', title: 'Agosto B'},
          {id: '4', date: '2026-09-02', time: '09:00', title: 'Sep pasada'},
          {id: '5', date: '2026-09-15', time: '18:00', title: 'Sep hoy tarde'},
          {id: '6', date: '2026-09-29', time: '10:00', title: 'Sep semana'},
          {id: '7', date: '2026-10-02', time: '10:00', title: 'Oct semana'},
          {id: '8', date: '2026-10-21', time: '10:00', title: 'Oct lejos'},
        ];
        const pedidos = [];
        let demorar = false;
        const demorados = [];
        function fetch(url) {
          pedidos.push(url);
          const start = url.match(/start=([0-9-]+)/)[1];
          const end = url.match(/end=([0-9-]+)/)[1];
          const cuerpo = {events: reuniones.filter(e => e.date >= start && e.date <= end)};
          const resp = {json: async () => cuerpo};
          if (!demorar) return Promise.resolve(resp);
          return new Promise(ok => demorados.push(() => ok(resp)));
        }
        const esperar = () => new Promise(ok => setTimeout(ok, 0));
        const rango = url => url.replace('/api/calendar/events?', '');
        const ultimo = () => rango(pedidos[pedidos.length - 1]);
        function deslizar(x0, y0, x1, y1) {
          _calToqueInicio({touches: [{clientX: x0, clientY: y0}]});
          _calToqueFin({changedTouches: [{clientX: x1, clientY: y1}]});
        }
    """, "(async () => {", cuerpo, "})().catch(e => { console.error(e); process.exit(1); });"])
    return _node(fuente, tmp_path, tz)


@node
def test_abrir_pide_el_mes_actual_y_cuenta(tmp_path):
    _correr_calendario("""
      fijarReloj('2026-09-15T17:30:00Z');   // 14:30 en Montevideo
      await renderCalendar();
      assert(ultimo() === 'start=2026-09-01&end=2026-09-30', ultimo());
      assert(titulo() === 'Septiembre 2026', titulo());
      assert(contador() === 'Septiembre 2026 · 3 reuniones · 1 hecha · 2 por venir', contador());
    """, tmp_path)


@node
def test_ir_para_atras_pide_y_muestra_los_meses_pasados(tmp_path):
    _correr_calendario("""
      fijarReloj('2026-09-15T17:30:00Z');
      await renderCalendar();

      calShift(-1); await esperar();
      assert(calMonthOffset === -1, 'offset=' + calMonthOffset);
      assert(ultimo() === 'start=2026-08-01&end=2026-08-31', ultimo());
      assert(titulo() === 'Agosto 2026', titulo());
      assert(contador() === 'Agosto 2026 · 2 reuniones', contador());
      assert(grilla().includes('[Agosto A]') && grilla().includes('[Agosto B]'), grilla());

      calShift(-1); await esperar();
      assert(ultimo() === 'start=2026-07-01&end=2026-07-31', ultimo());
      assert(contador() === 'Julio 2026 · 1 reunión', contador());

      calShift(1); calShift(1); calShift(1); await esperar();
      assert(calMonthOffset === 1, 'offset=' + calMonthOffset);
      assert(ultimo() === 'start=2026-10-01&end=2026-10-31', ultimo());
      assert(contador() === 'Octubre 2026 · 2 agendadas', contador());

      calHoy(); await esperar();
      assert(calMonthOffset === 0 && calWeekOffset === 0, 'hoy');
      assert(ultimo() === 'start=2026-09-01&end=2026-09-30', ultimo());
    """, tmp_path)


@node
def test_con_el_navegador_en_utc_igual_manda_montevideo(tmp_path):
    """01:30 UTC del 1/10: en Montevideo sigue siendo 30/9, y eso se abre."""
    _correr_calendario("""
      fijarReloj('2026-10-01T01:30:00Z');
      await renderCalendar();
      assert(ultimo() === 'start=2026-09-01&end=2026-09-30', ultimo());
      assert(contador() === 'Septiembre 2026 · 3 reuniones · 3 hechas · 0 por venir', contador());
    """, tmp_path, tz="UTC")


@node
def test_crear_o_borrar_actualiza_el_contador(tmp_path):
    """Guardar, mover y borrar terminan en renderCalendar: el contador sale de
    lo que vuelve a traer."""
    _correr_calendario("""
      fijarReloj('2026-09-15T17:30:00Z');
      await renderCalendar();
      reuniones.push({id: '9', date: '2026-09-25', time: '12:00', title: 'Nueva'});
      await renderCalendar();
      assert(contador() === 'Septiembre 2026 · 4 reuniones · 1 hecha · 3 por venir', contador());
      reuniones.splice(reuniones.findIndex(r => r.id === '4'), 1);
      await renderCalendar();
      assert(contador() === 'Septiembre 2026 · 3 reuniones · 0 hechas · 3 por venir', contador());
    """, tmp_path)


def test_crear_editar_mover_y_borrar_redibujan():
    for nombre in ("saveEvent", "deleteCalEvent", "_calGuardarHorario", "_calMover"):
        assert "renderCalendar()" in _funcion(nombre), nombre


@node
def test_una_respuesta_vieja_no_pisa_el_mes_que_se_mira(tmp_path):
    """Deslizar dos veces rapido: si la respuesta del primer mes llega ultima,
    no puede dejar el contador hablando de ese mes."""
    _correr_calendario("""
      fijarReloj('2026-09-15T17:30:00Z');
      await renderCalendar();
      demorar = true;
      calShift(-1);          // agosto, queda colgado
      calShift(-1);          // julio
      await esperar();
      demorados[1](); await esperar();    // llega julio
      demorados[0](); await esperar();    // llega agosto, tarde
      assert(titulo() === 'Julio 2026', titulo());
      assert(contador() === 'Julio 2026 · 1 reunión', contador());
    """, tmp_path)


@node
def test_la_vista_semana_cuenta_el_mes_de_la_semana(tmp_path):
    _correr_calendario("""
      window.innerWidth = 1280;
      calView = 'semana';
      fijarReloj('2026-09-30T15:00:00Z');   // miercoles 30/9, 12:00 en Montevideo
      await renderCalendar();
      assert(ultimo() === 'start=2026-09-01&end=2026-10-04', ultimo());
      assert(contador() === 'Septiembre 2026 · 3 reuniones · 3 hechas · 0 por venir', contador());
      // La grilla solo dibuja la semana, aunque haya pedido el mes entero.
      assert(grilla().includes('[Sep semana]') && grilla().includes('[Oct semana]'), grilla());
      assert(!grilla().includes('[Sep pasada]'), 'dibujo una reunion de otra semana');

      calShift(1); await esperar();
      assert(calWeekOffset === 1 && calMonthOffset === 0, 'movio la semana');
      assert(ultimo() === 'start=2026-10-01&end=2026-10-31', ultimo());
      assert(contador() === 'Octubre 2026 · 2 agendadas', contador());
    """, tmp_path)


@node
def test_en_el_celular_las_flechas_mueven_el_mes_aunque_quede_semana(tmp_path):
    """El celular no tiene vista semana: sin esto, la flecha cambiaba la semana
    escondida y el mes no se movia."""
    _correr_calendario("""
      calView = 'semana';
      fijarReloj('2026-09-15T17:30:00Z');
      await renderCalendar();
      calShift(1); await esperar();
      assert(calMonthOffset === 1 && calWeekOffset === 0, calMonthOffset + '/' + calWeekOffset);
      assert(ultimo() === 'start=2026-10-01&end=2026-10-31', ultimo());
    """, tmp_path)


# ─── deslizar ─────────────────────────────────────────────────────────────────

@node
def test_deslizar_a_la_izquierda_va_al_mes_siguiente_y_a_la_derecha_al_anterior(tmp_path):
    _correr_calendario("""
      fijarReloj('2026-09-15T17:30:00Z');
      await renderCalendar();
      deslizar(300, 200, 120, 215); await esperar();
      assert(calMonthOffset === 1, 'izquierda: ' + calMonthOffset);
      assert(ultimo() === 'start=2026-10-01&end=2026-10-31', ultimo());
      deslizar(100, 200, 280, 190); await esperar();
      deslizar(100, 200, 280, 190); await esperar();
      assert(calMonthOffset === -1, 'derecha: ' + calMonthOffset);
      assert(contador() === 'Agosto 2026 · 2 reuniones', contador());
    """, tmp_path)


@node
def test_un_gesto_vertical_corto_o_con_dos_dedos_no_cambia_de_mes(tmp_path):
    _correr_calendario("""
      fijarReloj('2026-09-15T17:30:00Z');
      await renderCalendar();
      const antes = pedidos.length;
      deslizar(200, 100, 230, 500);   // scroll vertical
      deslizar(200, 100, 170, 105);   // toque corto: abrir un dia
      deslizar(200, 100, 150, 100);   // justo 50px: no alcanza
      deslizar(200, 100, 120, 190);   // mas vertical que horizontal
      _calToqueInicio({touches: [{clientX: 300, clientY: 0}, {clientX: 10, clientY: 0}]});
      _calToqueFin({changedTouches: [{clientX: 50, clientY: 0}]});   // zoom con dos dedos
      _calToqueFin({changedTouches: [{clientX: 0, clientY: 0}]});    // fin sin inicio
      await esperar();
      assert(calMonthOffset === 0, 'offset=' + calMonthOffset);
      assert(pedidos.length === antes, 'pidio de nuevo: ' + pedidos.slice(antes));
    """, tmp_path)


@node
def test_la_direccion_del_gesto(tmp_path):
    _correr_calendario("""
      assert(_calDireccionSwipe(-51, 0) === 1, 'izquierda');
      assert(_calDireccionSwipe(51, 0) === -1, 'derecha');
      assert(_calDireccionSwipe(-50, 0) === 0, 'umbral');
      assert(_calDireccionSwipe(-80, 80) === 0, 'diagonal pareja');
      assert(_calDireccionSwipe(-80, -79) === 1, 'apenas mas horizontal');
    """, tmp_path)


def test_el_deslizar_se_escucha_sobre_la_grilla_sin_frenar_el_scroll():
    """Pasivos: un listener no pasivo de touch puede trabar el scroll vertical.
    Y sobre #cal-days, no sobre la lista del dia, donde estan Editar y Borrar."""
    for evento, funcion in (("touchstart", "_calToqueInicio"), ("touchend", "_calToqueFin")):
        assert f"zona.addEventListener('{evento}', {funcion}, {{passive: true}});" in HTML
    assert "document.getElementById('cal-days');\n  if (!zona) return;" in HTML
    for nombre in ("_calToqueInicio", "_calToqueFin"):
        assert "preventDefault" not in _funcion(nombre), nombre


# ─── la carga inicial, el CSS y la pagina ─────────────────────────────────────

def test_lo_que_renderCalendar_usa_antes_del_primer_await_esta_declarado_antes():
    inicio = HTML.index("// Initial load")
    for decl in ("let _calPedido", "const CAL_MESES ", "let calMonthOffset"):
        assert HTML.index(decl) < inicio, decl
    cuerpo = _funcion("renderCalendar")
    antes = cuerpo[:cuerpo.index("await")]
    assert "_calPedido" in antes and "_calPintarContador" in antes


def test_el_contador_usa_tokens_y_no_tiene_regla_clara():
    regla = re.search(r"^\.cal-count\{([^}]*)\}", HTML, re.M)
    assert regla, "falta la regla .cal-count"
    assert not re.findall("#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])", regla.group(1)), regla.group(1)
    assert "var(--" in regla.group(1)
    assert "body.light .cal-count" not in HTML


def test_en_el_celular_el_contador_va_en_su_renglon_despues_de_la_base():
    base = HTML.index(".cal-count{font-size")
    movil = HTML.index(".cal-count{order:3;width:100%")
    assert base < movil, "la regla de base pisaria la del celular"


def test_la_pagina_principal_se_renderiza(tmp_path, monkeypatch):
    """Jinja: un `{#` en el CSS o el JS nuevo daria 500 para todos (paso en v204)."""
    from werkzeug.security import generate_password_hash
    from database import create_user, init_db

    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "render.db")
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

    assert r.status_code == 200, r.status_code
    assert b'id="cal-count"' in r.data
    assert b"_calResumenMes" in r.data
    assert "Deslizá el calendario para cambiar de mes".encode() in r.data
