"""La logica de la vista semanal del calendario, corrida de verdad en node.

El JS del CRM vive dentro de un string de Python, asi que estos tests recortan
las funciones puras de la vista semanal y las ejecutan. No hay DOM: lo que se
prueba es donde empieza la semana y que franja de horas dibuja la grilla: una
reunion fuera de ese rango no se dibujaria en ninguna fila.
"""

import re
import shutil
import subprocess

import pytest

import dashboard


pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node no esta instalado")


def _funcion(nombre: str) -> str:
    """Recorta `function nombre(...) { ... }` del JS del dashboard.

    Se apoya en que las funciones de nivel superior cierran con una llave en
    la columna 0, que es como esta escrito todo el archivo.
    """
    m = re.search(r"\nfunction " + nombre + r"\(.*?\n\}", dashboard.DASHBOARD_HTML, re.S)
    assert m, f"no encontre la funcion {nombre} en el dashboard"
    return m.group(0)


def _correr(cuerpo: str, tmp_path) -> str:
    fuente = "\n".join([
        _funcion("_calIsoLocal"),
        _funcion("_calWeekStart"),
        _funcion("_calHourRange"),
        "function assert(cond, msg) { if (!cond) { throw new Error(msg); } }",
        cuerpo,
    ])
    archivo = tmp_path / "semana.js"
    archivo.write_text(fuente, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


# ─── donde empieza la semana ──────────────────────────────────────────────────

def test_la_semana_empieza_el_lunes(tmp_path):
    """Miercoles 9/9/2026 -> lunes 7/9/2026."""
    _correr("""
      const lunes = _calWeekStart(new Date(2026, 8, 9), 0);
      assert(_calIsoLocal(lunes) === '2026-09-07', _calIsoLocal(lunes));
    """, tmp_path)


def test_el_domingo_cae_en_la_semana_que_termina(tmp_path):
    """Con getDay()==0 hay que ir seis dias atras, no uno adelante."""
    _correr("""
      const lunes = _calWeekStart(new Date(2026, 8, 13), 0);
      assert(_calIsoLocal(lunes) === '2026-09-07', _calIsoLocal(lunes));
    """, tmp_path)


def test_el_offset_mueve_de_a_semanas_cruzando_el_mes(tmp_path):
    _correr("""
      assert(_calIsoLocal(_calWeekStart(new Date(2026, 8, 9), 1)) === '2026-09-14');
      assert(_calIsoLocal(_calWeekStart(new Date(2026, 8, 9), -2)) === '2026-08-24');
      assert(_calIsoLocal(_calWeekStart(new Date(2026, 8, 30), 1)) === '2026-10-05');
    """, tmp_path)


# ─── que horas dibuja la grilla ───────────────────────────────────────────────

def test_una_semana_vacia_va_de_8_a_20(tmp_path):
    _correr("""
      const r = _calHourRange([]);
      assert(r.from === 8, 'from=' + r.from);
      assert(r.to === 20, 'to=' + r.to);
    """, tmp_path)


def test_una_reunion_temprano_baja_el_piso(tmp_path):
    """Sin esto, una reunion a las 7 no tendria celda en la grilla."""
    _correr("""
      const r = _calHourRange([{time: '07:15'}, {time: '11:00'}]);
      assert(r.from === 7, 'from=' + r.from);
      assert(r.to === 20, 'to=' + r.to);
    """, tmp_path)


def test_una_reunion_tarde_sube_el_techo(tmp_path):
    """El techo es exclusivo: una reunion 21:30 necesita que la fila 21 exista."""
    _correr("""
      const r = _calHourRange([{time: '21:30'}]);
      assert(r.from === 8, 'from=' + r.from);
      assert(r.to === 22, 'to=' + r.to);
    """, tmp_path)


def test_los_eventos_sin_hora_no_corren_el_rango(tmp_path):
    _correr("""
      const r = _calHourRange([{time: ''}, {}, {time: '10:00'}]);
      assert(r.from === 8, 'from=' + r.from);
      assert(r.to === 20, 'to=' + r.to);
    """, tmp_path)


# ─── fechas locales ───────────────────────────────────────────────────────────

def test_iso_local_no_se_corre_de_dia(tmp_path):
    """toISOString() pasa por UTC; a las 23:00 de Montevideo devuelve el dia siguiente."""
    _correr("""
      assert(_calIsoLocal(new Date(2026, 8, 10, 23, 30)) === '2026-09-10');
      assert(_calIsoLocal(new Date(2026, 0, 5, 0, 30)) === '2026-01-05');
    """, tmp_path)


# ─── la hora que propone el editor ────────────────────────────────────────────

def _correr_mes(cuerpo: str, tmp_path) -> str:
    fuente = "\n".join([
        _funcion("_calHoraLabel"),
        _funcion("_calHoraDeLaReunion"),
        "function assert(cond, msg) { if (!cond) { throw new Error(msg); } }",
        cuerpo,
    ])
    archivo = tmp_path / "mes.js"
    archivo.write_text(fuente, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_el_editor_propone_la_hora_que_ya_tenia(tmp_path):
    """Abrir el modal no cambia nada: el campo hora arranca donde estaba."""
    _correr_mes("""
      assert(_calHoraDeLaReunion({time: '15:30'}) === '15:30');
      assert(_calHoraDeLaReunion({time: '07:00'}) === '07:00');
    """, tmp_path)


def test_una_reunion_sin_hora_propone_las_diez(tmp_path):
    """Las importadas de dia entero no traen hora, y el back exige una."""
    _correr_mes("""
      assert(_calHoraDeLaReunion({time: ''}) === '10:00');
      assert(_calHoraDeLaReunion({}) === '10:00');
    """, tmp_path)


def test_la_hora_va_siempre_en_hh_mm(tmp_path):
    """Google y el endpoint esperan HH:MM; '9:5' no parsea."""
    _correr_mes("""
      assert(_calHoraDeLaReunion({time: '9:05'}) === '09:05');
      assert(_calHoraDeLaReunion({time: '15:00:00'}) === '15:00');
    """, tmp_path)


# ─── guardar sin cambios ──────────────────────────────────────────────────────

def _correr_destino(cuerpo: str, tmp_path) -> str:
    fuente = "\n".join([
        _funcion("_calDestinoValido"),
        "function assert(cond, msg) { if (!cond) { throw new Error(msg); } }",
        cuerpo,
    ])
    archivo = tmp_path / "destino.js"
    archivo.write_text(fuente, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_guardar_sin_cambiar_nada_no_es_un_movimiento(tmp_path):
    """Sin esto, abrir el modal y darle a Guardar le manda un mail al cliente."""
    _correr_destino("""
      const m = {date: '2026-09-09', time: '15:00'};
      assert(_calDestinoValido(m, '2026-09-09', '15:00') === false);
    """, tmp_path)


def test_cambiar_solo_la_hora_si_es_movimiento(tmp_path):
    _correr_destino("""
      const m = {date: '2026-09-09', time: '15:00'};
      assert(_calDestinoValido(m, '2026-09-09', '15:30') === true);
    """, tmp_path)


def test_cambiar_solo_el_dia_si_es_movimiento(tmp_path):
    _correr_destino("""
      const m = {date: '2026-09-09', time: '15:00'};
      assert(_calDestinoValido(m, '2026-10-20', '15:00') === true);
    """, tmp_path)


def test_sin_reunion_o_sin_fecha_no_hay_destino(tmp_path):
    _correr_destino("""
      assert(_calDestinoValido(null, '2026-09-09', '15:00') === false);
      assert(_calDestinoValido({date:'2026-09-09',time:'15:00'}, '', '15:00') === false);
    """, tmp_path)
