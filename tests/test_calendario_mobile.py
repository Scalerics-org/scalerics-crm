"""En el celular no se veian las reuniones del dia.

Dos cosas a la vez:

1. `#cal-day-events-mobile` tenia `style="display:none"` inline, y el CSS de
   mobile lo mostraba con una regla sin `!important`. El inline le gana
   siempre, asi que el listado quedaba oculto para siempre, y
   `_calCellClick` llenaba un contenedor que nadie veia.
2. Aun arreglado eso, al abrir el calendario no habia ningun dia elegido: la
   lista arrancaba vacia y habia que adivinar que se toca un dia.

La visibilidad quedo en el CSS (oculto de base, visible en el @media) y el mes
elige hoy al dibujarse. Los tests de node corren el JS de verdad contra un
DOM de mentira: lo que se mide es que termina escrito en la lista.
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


# ─── la visibilidad la manda el CSS ───────────────────────────────────────────

def test_el_contenedor_no_tiene_estilo_inline():
    """Con cualquier `display` inline, la regla del @media no puede ganar."""
    m = re.search(r'<div id="cal-day-events-mobile"[^>]*>', HTML)
    assert m, "falta el contenedor de las reuniones del dia"
    assert "style=" not in m.group(0), m.group(0)


def test_oculto_de_base_y_visible_en_el_celular_en_ese_orden():
    """Misma especificidad: gana la que viene despues. Si la de base quedara
    abajo del @media, el celular volveria a no ver nada."""
    base = HTML.find("#cal-day-events-mobile{display:none")
    movil = HTML.find("@media(max-width:768px){#cal-day-events-mobile{display:block}}")
    assert base != -1, "falta la regla que lo oculta en escritorio"
    assert movil != -1, "falta la regla que lo muestra en el celular"
    assert base < movil
    assert HTML.count("#cal-day-events-mobile{") == 2, \
        "otra regla suelta para el contenedor puede volver a pisar estas dos"


def test_el_js_no_toca_la_visibilidad_ni_pinta_colores():
    """Un `style.display` desde JS es un inline: sobrevive a agrandar la ventana
    y deja la lista del celular abierta en escritorio. Los colores van por
    clase, que cambia con el tema; el viejo `#111827` inline era oscuro en claro."""
    for nombre in ("_calCellClick", "_calSeleccionarDiaMobile"):
        js = _funcion(nombre)
        assert "style.display" not in js, nombre
        assert "style=" not in js, nombre
        assert ".style." not in js, nombre


def test_dibujar_el_mes_elige_el_dia_del_celular():
    assert "_calSeleccionarDiaMobile();" in _funcion("renderCalendar")


def test_en_escritorio_las_dos_funciones_cortan_primero():
    """Arriba de 768px el click en una celda no hace nada: ahi se arrastra."""
    for nombre in ("_calCellClick", "_calSeleccionarDiaMobile"):
        cuerpo = _funcion(nombre).split("{", 1)[1].strip()
        assert cuerpo.startswith("if (window.innerWidth > 768) return;"), nombre


# ─── que dia se elige ─────────────────────────────────────────────────────────

def _correr(cuerpo: str, tmp_path) -> str:
    fuente = "\n".join([
        _funcion("_calIsoLocal"),
        _funcion("_calDiaMobile"),
        _funcion("_calSeleccionarDiaMobile"),
        _funcion("_calCellClick"),
        """
        let calDiaMobile = null;
        const window = {innerWidth: 390, _calEventMap: {}};
        function esc(s) { return String(s); }
        function assert(cond, msg) { if (!cond) { throw new Error(msg); } }
        function celda(ds) {
          const clases = new Set();
          return {dataset: {date: ds}, classList: {
            add: c => clases.add(c), remove: c => clases.delete(c),
            contains: c => clases.has(c)}};
        }
        const lista = {innerHTML: '', desplazado: 0,
                       scrollIntoView() { this.desplazado++; }};
        let celdas = [];
        const document = {
          getElementById: id => id === 'cal-day-events-mobile' ? lista : null,
          querySelectorAll: sel => sel === '.cal-cell.sel-mobile'
            ? celdas.filter(c => c.classList.contains('sel-mobile'))
            : celdas,
        };
        const hoy = _calIsoLocal(new Date());
        const elegidas = () => celdas.filter(c => c.classList.contains('sel-mobile'))
                                     .map(c => c.dataset.date);
        """,
        cuerpo,
    ])
    archivo = tmp_path / "mobile.js"
    archivo.write_text(fuente, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


@node
def test_elegir_prefiere_lo_elegido_despues_hoy(tmp_path):
    _correr("""
      const mes = ['2026-09-01', '2026-09-14', '2026-09-20'];
      assert(_calDiaMobile(mes, '2026-09-14', null) === '2026-09-14', 'hoy');
      assert(_calDiaMobile(mes, '2026-09-14', '2026-09-20') === '2026-09-20', 'elegido');
      assert(_calDiaMobile(mes, '2026-09-14', '2026-08-03') === '2026-09-14',
             'lo elegido en otro mes no cuenta');
      assert(_calDiaMobile(mes, '2026-10-01', null) === null, 'mes sin hoy');
      assert(_calDiaMobile([], '2026-09-14', null) === null, 'sin celdas');
    """, tmp_path)


@node
def test_abrir_en_el_celular_muestra_las_reuniones_de_hoy(tmp_path):
    """El bug entero: abrir el calendario y ver lo de hoy sin tocar nada."""
    _correr("""
      celdas = [celda('2000-01-01'), celda(hoy)];
      window._calEventMap[hoy] = [{title: 'Demo Optica Luz', time: '15:00',
                                   meeting_url: 'https://meet.google.com/x'}];
      _calSeleccionarDiaMobile();
      assert(elegidas().join() === hoy, 'elegidas=' + elegidas());
      assert(lista.innerHTML.includes('Demo Optica Luz'), lista.innerHTML);
      assert(lista.innerHTML.includes('15:00'), 'falta la hora');
      assert(lista.innerHTML.includes('Unirse'), 'falta el link');
      assert(lista.desplazado === 0, 'abrir no tiene que mover la pantalla');
    """, tmp_path)


@node
def test_en_escritorio_no_elige_nada(tmp_path):
    _correr("""
      window.innerWidth = 1280;
      celdas = [celda(hoy)];
      window._calEventMap[hoy] = [{title: 'Demo'}];
      _calSeleccionarDiaMobile();
      _calCellClick(celdas[0], hoy);
      assert(lista.innerHTML === '', lista.innerHTML);
      assert(elegidas().length === 0, 'elegidas=' + elegidas());
      assert(calDiaMobile === null, 'no tiene que recordar nada');
    """, tmp_path)


@node
def test_tocar_otro_dia_mueve_la_marca_y_sobrevive_al_redibujo(tmp_path):
    """Guardar o mover una reunion vuelve a dibujar el mes: no puede saltar a hoy."""
    _correr("""
      celdas = [celda(hoy), celda('2000-01-02')];
      window._calEventMap['2000-01-02'] = [{title: 'Llamada Vinoteca Sur'}];
      _calSeleccionarDiaMobile();
      _calCellClick(celdas[1], '2000-01-02');
      assert(elegidas().join() === '2000-01-02', 'una sola marca: ' + elegidas());
      assert(lista.desplazado === 1, 'tocar si desplaza hasta la lista');

      celdas = [celda(hoy), celda('2000-01-02')];   // el mes se dibuja de nuevo
      _calSeleccionarDiaMobile();
      assert(elegidas().join() === '2000-01-02', 'volvio a hoy: ' + elegidas());
      assert(lista.innerHTML.includes('Llamada Vinoteca Sur'), lista.innerHTML);
    """, tmp_path)


@node
def test_un_dia_sin_reuniones_lo_dice(tmp_path):
    _correr("""
      celdas = [celda(hoy)];
      _calSeleccionarDiaMobile();
      assert(lista.innerHTML.includes('Sin reuniones este día'), lista.innerHTML);
    """, tmp_path)


@node
def test_otro_mes_sin_nada_elegido_invita_a_tocar_un_dia(tmp_path):
    _correr("""
      celdas = [celda('2000-02-01'), celda('2000-02-02')];
      _calSeleccionarDiaMobile();
      assert(elegidas().length === 0, 'elegidas=' + elegidas());
      assert(lista.innerHTML.includes('Tocá un día'), lista.innerHTML);
    """, tmp_path)
