"""La cuenta del simulador financiero, corrida de verdad en node.

`simCalcular` vive en el JS del dashboard, entre dos marcas que dicen "sim:
calculo puro". Lo que hay entre las marcas no toca el DOM, así que se recorta
entero y se ejecuta. Cada test es un criterio de aceptación de la
especificación de Juan o un borde: 0 programadores, 0 proyectos, costo por lead
0, vacíos y negativos que toman el default.

Con los defaults (gastos fijos de respaldo del 14/9, que suman 930):
facturado 5.600, sale 2.530. Desde el 15/9 cada tipo tiene su forma de cobro
y las webs se cobran enteras al confirmar: cobrado 1.400 de webs + la mitad de
2.200 de ecommerce + la mitad de 2.000 a medida = 3.500, caja del mes 970.
Lo de las formas de cobro en detalle está en test_simulador_cobro_por_tipo.py.
"""

import shutil
import subprocess

import pytest

import dashboard

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node no esta instalado")

INICIO = "// ── sim: calculo puro (inicio) ──"
FIN = "// ── sim: calculo puro (fin) ──"

_AYUDAS = """
function assert(c, m) { if (!c) { throw new Error(m || 'assert'); } }
function igual(a, b, m) {
  if (typeof a !== 'number' || Math.abs(a - b) > 1e-6) {
    throw new Error((m || 'igual') + ': ' + a + ' != ' + b);
  }
}
function contiene(texto, pedazo) {
  if (String(texto).indexOf(pedazo) < 0) {
    throw new Error('"' + texto + '" no contiene "' + pedazo + '"');
  }
}
function escenario(cambios) {
  const e = simEscenarioBase(null);
  if (cambios) cambios(e);
  return e;
}
"""


def _bloque() -> str:
    html = dashboard.DASHBOARD_HTML
    assert html.count(INICIO) == 1, "falta (o se repite) la marca de inicio"
    assert html.count(FIN) == 1, "falta (o se repite) la marca de fin"
    return html[html.index(INICIO):html.index(FIN)]


def _correr(cuerpo: str, tmp_path) -> str:
    archivo = tmp_path / "sim.js"
    archivo.write_text(_bloque() + "\n" + _AYUDAS + "\n" + cuerpo, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_el_bloque_puro_no_toca_el_dom():
    """Si alguien mete un `document.` entre las marcas, el recorte deja de
    poder correr solo y estos tests pasan a probar otra cosa."""
    bloque = _bloque()
    for prohibido in ("document.", "window.", "fetch(", "localStorage"):
        assert prohibido not in bloque, prohibido


def test_los_defaults_dan_la_cuenta_hecha_a_mano(tmp_path):
    _correr("""
      const r = simCalcular(escenario());
      igual(r.capacidad, 6); igual(r.totalProyectos, 5); igual(r.exceso, 0);
      igual(r.vendidos, 5); igual(r.ratio, 1);
      igual(r.facturadoProyectos, 5600);
      igual(r.recurrente, 0); igual(r.facturado, 5600);
      igual(r.cobroDeNuevos, 3500); igual(r.quedaDeEsteMes, 2100); igual(r.cobrado, 3500);
      igual(r.listas.gastosFijos.total, 930);
      igual(r.salidas, 1000 + 930 + 600);
      igual(r.cajaDelMes, 970);
      igual(r.porCobrarAdelante, 2100);
      igual(r.cajaActual, 0); igual(r.cajaAlCierre, 970);
      assert(r.avisoCobros === false, 'con caja positiva no hay aviso');
      assert(r.capacidadEstado === 'verde', r.capacidadEstado);
      igual(r.leads, 600 / 18); igual(r.ventasPosibles, 600 / 18 * 0.25 * 0.3);
      igual(r.costoPorVenta, 240);
      assert(r.conDefault.length === 0, 'ningun campo deberia tomar default: ' + r.conDefault);
    """, tmp_path)


def test_facturar_no_es_cobrar_son_dos_numeros(tmp_path):
    """Segundo principio: con todo en mitad y mitad se factura el doble de lo que
    se cobra. Con los defaults (webs enteras) la diferencia es lo de entregas."""
    _correr("""
      const r = simCalcular(escenario());
      assert(r.facturado !== r.cobrado, 'facturado y cobrado no pueden coincidir aca');
      igual(r.facturado - r.cobrado, 2100);
      const mitad = simCalcular(escenario(e => { e.cobros.formas.web = 'mitad'; }));
      igual(mitad.facturado - mitad.recurrente, 2 * (mitad.cobrado - mitad.recurrente));
    """, tmp_path)


# ── criterios de aceptación ──────────────────────────────────────────────────

def test_criterio_1_el_precio_del_ecommerce_mueve_todo(tmp_path):
    _correr("""
      const antes = simCalcular(escenario(e => { e.meta.sueldoObjetivo = 2000; }));
      const despues = simCalcular(escenario(e => {
        e.meta.sueldoObjetivo = 2000; e.ventas.precioEcom = '1500';
      }));
      igual(despues.facturado, 6400); igual(despues.cobrado, 3900);
      igual(despues.cajaDelMes, 1370); igual(despues.porCobrarAdelante, 2500);
      for (const k of ['facturado', 'cobrado', 'cajaDelMes', 'porCobrarAdelante',
                       'cajaAlCierre', 'precioPromedio', 'facturadoProyectos']) {
        assert(antes[k] !== despues[k], k + ' no se movio');
      }
      assert(antes.meta.proyectos !== despues.meta.proyectos, 'la meta no se movio');
    """, tmp_path)


def test_criterio_2_apagar_marketing_impacta_al_instante(tmp_path):
    _correr("""
      const e = escenario();
      const i = e.gastosFijos.findIndex(f => f.nombre === 'Agencia de marketing');
      assert(i >= 0, 'no esta la agencia de marketing');
      const antes = simCalcular(e);
      e.gastosFijos[i].activo = false;
      const despues = simCalcular(e);
      igual(antes.salidas - despues.salidas, 300);
      igual(despues.cajaDelMes - antes.cajaDelMes, 300);
      assert(simTextoLista(despues.listas.gastosFijos) === '5 activos · USD 630',
             simTextoLista(despues.listas.gastosFijos));
      assert(e.gastosFijos.length === 6, 'apagar no borra');
    """, tmp_path)


def test_criterio_3_nueve_proyectos_con_dos_programadores_no_da(tmp_path):
    _correr("""
      const r = simCalcular(escenario(e => { e.ventas.webs = 6; }));
      igual(r.totalProyectos, 9); igual(r.capacidad, 6);
      assert(r.capacidadEstado === 'rojo', r.capacidadEstado);
      contiene(simTextoCapacidad(r), '2 programadores aguantan 6 y estás poniendo 9');
      contiene(simTextoCapacidad(r), 'No da');
      igual(r.vendidos, 6); igual(r.ratio, 6 / 9);
      igual(r.facturadoProyectos, (6 * 700 + 2 * 1100 + 2000) * 6 / 9);

      const s = simCalcular(escenario(e => { e.ventas.webs = 6; e.palancas.subcontratar = true; }));
      assert(s.capacidadEstado === 'ambar', s.capacidadEstado);
      igual(s.vendidos, 9); igual(s.exceso, 3);
      igual(s.costoSubcontrato, 3 * (700 + 1100 + 2000) / 3 * 0.6);
    """, tmp_path)


def test_criterio_4_pocas_ventas_y_pendientes_viejos_la_caja_da_positiva(tmp_path):
    _correr("""
      const flojo = e => { e.ventas.webs = 1; e.ventas.ecommerce = 0; e.ventas.aMedida = 0;
                           e.pendientes = [{nombre: 'Cliente viejo', monto: 3000, activo: false}]; };
      const apagado = simCalcular(escenario(flojo));
      assert(apagado.cajaDelMes < 0, 'sin cobrar el pendiente la caja da negativa');
      igual(apagado.porCobrarAdelante, 3000, 'la web se cobra entera: no queda nada de ella');

      const prendido = simCalcular(escenario(e => { flojo(e); e.pendientes[0].activo = true; }));
      igual(prendido.facturado, 700);
      igual(prendido.cobrado, 700 + 3000);
      igual(prendido.cajaDelMes, 3700 - 2530);
      assert(prendido.cajaDelMes > 0, 'la caja tiene que dar positiva');
      igual(prendido.porCobrarAdelante, 0);
    """, tmp_path)


def test_criterio_5_se_puede_agregar_un_gasto_que_no_estaba(tmp_path):
    _correr("""
      const e = escenario();
      const antes = simCalcular(e);
      e.gastosFijos.push({nombre: 'Figma', monto: '15', activo: true});
      const despues = simCalcular(e);
      igual(despues.salidas - antes.salidas, 15);
      assert(simTextoLista(despues.listas.gastosFijos) === '7 activos · USD 945',
             simTextoLista(despues.listas.gastosFijos));
    """, tmp_path)


def test_criterio_6_cada_mantenimiento_tiene_su_cuota(tmp_path):
    _correr("""
      const e = escenario(x => {
        x.mantenimientos = [{nombre: 'A', monto: 80, activo: true},
                            {nombre: 'B', monto: 150, activo: true},
                            {nombre: 'C', monto: 300, activo: false}];
      });
      const r = simCalcular(e);
      igual(r.recurrenteBruto, 230);
      igual(r.recurrente, 230 * 0.95);
      igual(r.facturado, 5600 + 230 * 0.95);
      e.mantenimientos[1].monto = 200;
      const s = simCalcular(e);
      igual(s.recurrenteBruto, 280, 'cambiar la cuota de B no toca la de A');
    """, tmp_path)


def test_criterio_7_sueldo_objetivo_de_2000(tmp_path):
    """base = 2x500 + 930 + 600 = 2.530; +2.000 = 4.530. Por proyecto entran al
    confirmar (700 entera + 1.100/2 + 2.000/2) / 3 = 750 -> 6,04 -> 7 proyectos
    -> 3 programadores: falta 1."""
    _correr("""
      const r = simCalcular(escenario(e => { e.meta.sueldoObjetivo = 2000; }));
      igual(r.meta.base, 2530); igual(r.meta.necesario, 4530);
      igual(r.meta.porProyecto, 750);
      assert(r.meta.proyectos === 7, 'proyectos: ' + r.meta.proyectos);
      assert(r.meta.faltanProg === 1, 'faltan: ' + r.meta.faltanProg);
      contiene(simTextoMeta(r), '7 proyectos por mes');
      contiene(simTextoMeta(r), 'USD 750 por proyecto');
      contiene(simTextoMeta(r), 'Te falta 1 programador');
    """, tmp_path)


def test_criterio_8_cada_campo_editable_mueve_la_cuenta(tmp_path):
    """La otra mitad del criterio 8 (que no haya literales en la cuenta) está
    en test_simulador_panel.py. Esta prueba que cada campo de SIM_CAMPOS
    llega a la cuenta: si uno no moviera nada, sería un control de adorno."""
    _correr("""
      const base = escenario(e => {
        e.ventas.webs = 6;
        e.mantenimiento.altasNuevasPorMes = 2;
        e.mantenimientos = [{nombre: 'A', monto: 100, activo: true}];
        Object.keys(e.palancas).forEach(k => { e.palancas[k] = true; });
      });
      const sinDefaults = r => { const c = Object.assign({}, r); delete c.conDefault; return JSON.stringify(c); };
      const ref = sinDefaults(simCalcular(base));
      SIM_CAMPOS.forEach(([ruta, tipo]) => {
        const e = simClonar(base);
        const actual = Number(simLeer(e, ruta));
        const nuevo = tipo === 'porcentaje' && actual + 7 > 100 ? actual - 7 : actual + (tipo === 'entero' ? 1 : 7);
        simEscribir(e, ruta, nuevo);
        const r = simCalcular(e);
        assert(r.conDefault.indexOf(ruta) < 0, ruta + ' tomo el default con ' + nuevo);
        assert(sinDefaults(r) !== ref, ruta + ' no mueve ningun resultado');
      });
    """, tmp_path)


def test_todo_default_numerico_es_un_campo_editable(tmp_path):
    """Si alguien suma un supuesto a SIM_DEFAULTS y se olvida de SIM_CAMPOS,
    queda un número que la cuenta no lee o que no tiene control."""
    _correr("""
      const rutas = SIM_CAMPOS.map(c => c[0]);
      const hojas = [];
      (function recorrer(o, prefijo) {
        Object.keys(o).forEach(k => {
          const ruta = prefijo ? prefijo + '.' + k : k;
          if (ruta === 'precarga' || ruta === 'gastosFijos') return;
          if (typeof o[k] === 'number') hojas.push(ruta);
          else if (o[k] && typeof o[k] === 'object') recorrer(o[k], ruta);
        });
      })(SIM_DEFAULTS, '');
      hojas.forEach(h => assert(rutas.indexOf(h) >= 0, h + ' no esta en SIM_CAMPOS'));
      rutas.forEach(r => assert(typeof simLeer(SIM_DEFAULTS, r) === 'number', r + ' no tiene default'));
      assert(hojas.length === rutas.length, hojas.length + ' vs ' + rutas.length);
    """, tmp_path)


# ── aviso clave, semáforos y textos ─────────────────────────────────────────

def test_el_aviso_clave_papel_bien_caja_mal(tmp_path):
    _correr("""
      const r = simCalcular(escenario(e => { e.cobros.porcentajeAlFirmar = 10; }));
      assert(r.cajaDelMes < 0, 'la caja tiene que dar negativa');
      assert(r.resultadoEnPapel >= 0, 'el papel tiene que dar positivo');
      assert(r.avisoCobros === true, 'tiene que avisar');
      contiene(simTextoAvisoCobros(r), 'El problema son los cobros, no las ventas');

      const malPapel = simCalcular(escenario(e => { e.ventas.webs = 0; e.ventas.ecommerce = 0; e.ventas.aMedida = 0; }));
      assert(malPapel.avisoCobros === false, 'si el papel tambien da mal, no es un problema de cobros');
      assert(simTextoAvisoCobros(malPapel) === '', 'sin aviso no hay texto');
    """, tmp_path)


def test_el_semaforo_del_embudo(tmp_path):
    _correr("""
      const r = simCalcular(escenario());
      assert(r.embudoEstado === 'rojo', r.embudoEstado);
      assert(simTextoEmbudo(r) === 'Con esa pauta salen 2 ventas, no 5.', simTextoEmbudo(r));

      const alcanza = simCalcular(escenario(e => { e.ventas.pauta = 2500; }));
      assert(alcanza.embudoEstado === 'verde', alcanza.embudoEstado);
      contiene(simTextoEmbudo(alcanza), 'alcanza');
    """, tmp_path)


def test_la_caja_al_cierre_no_cambia_la_caja_del_mes(tmp_path):
    _correr("""
      const r = simCalcular(escenario(e => { e.caja.cajaActual = '-500'; }));
      igual(r.cajaActual, -500, 'la caja puede estar en negativo');
      igual(r.cajaDelMes, 970, 'cajaDelMes es la de la especificacion');
      igual(r.cajaAlCierre, 470);
      assert(r.conDefault.indexOf('caja.cajaActual') < 0);
      assert(simTextoCierre(r) === 'Caja al cierre del mes: USD 470 = caja actual USD -500 + caja del mes USD 970.',
             simTextoCierre(r));
    """, tmp_path)


def test_las_altas_se_topean_con_lo_que_se_cierra(tmp_path):
    _correr("""
      const r = simCalcular(escenario(e => { e.mantenimiento.altasNuevasPorMes = 7; }));
      igual(r.altas, 5); assert(r.altasTopeadas === true);
      igual(r.recurrenteBruto, 5 * 100);
      assert(simTextoAvisoAltas(r) === 'Pusiste 7 altas de mantenimiento pero este mes se cierran 5 proyectos: se calcula con 5.',
             simTextoAvisoAltas(r));
      const ok = simCalcular(escenario(e => { e.mantenimiento.altasNuevasPorMes = 2; }));
      assert(ok.altasTopeadas === false && simTextoAvisoAltas(ok) === '');
    """, tmp_path)


def test_las_palancas(tmp_path):
    _correr("""
      const base = simCalcular(escenario());
      const pm = simCalcular(escenario(e => { e.palancas.projectManager = true; }));
      igual(pm.salidas - base.salidas, 500); igual(pm.capacidad, base.capacidad, 'el PM no suma capacidad');
      igual(pm.meta.base - base.meta.base, 500, 'el PM entra en la meta');

      const yo = simCalcular(escenario(e => { e.palancas.miSueldo = true; e.montosPalancas.miSueldo = 800; }));
      igual(yo.salidas - base.salidas, 800);
      igual(yo.meta.base, base.meta.base, 'mi sueldo no entra en la base de la meta: es el objetivo');

      const matias = simCalcular(escenario(e => { e.palancas.matias50 = true; }));
      igual(matias.comisionMatias, 2000 * 0.5);
      const matiasTope = simCalcular(escenario(e => { e.palancas.matias50 = true; e.ventas.webs = 6; }));
      igual(matiasTope.comisionMatias, 2000 * 0.5 * 6 / 9, 'se escala con el ratio');

      const javier = simCalcular(escenario(e => { e.palancas.aporteJavier = true; }));
      igual(javier.cajaDelMes - base.cajaDelMes, 1000);
      igual(javier.facturado, base.facturado, 'el aporte no es venta');
      igual(javier.cobrado, base.cobrado, 'el aporte no es cobro');
    """, tmp_path)


# ── bordes ───────────────────────────────────────────────────────────────────

def test_cero_programadores(tmp_path):
    _correr("""
      const r = simCalcular(escenario(e => { e.equipo.cantidadProgramadores = 0; }));
      igual(r.capacidad, 0); igual(r.vendidos, 0); igual(r.ratio, 0);
      igual(r.facturado, 0); igual(r.cobrado, 0); igual(r.costoEquipo, 0);
      assert(r.capacidadEstado === 'rojo');
      contiene(simTextoCapacidad(r), 'Sin programadores no hay capacidad y estás poniendo 5');
      assert(r.meta.proyectos === 4 && r.meta.faltanProg === 2, JSON.stringify(r.meta));
    """, tmp_path)


def test_cero_proyectos(tmp_path):
    _correr("""
      const r = simCalcular(escenario(e => {
        e.ventas.webs = 0; e.ventas.ecommerce = 0; e.ventas.aMedida = 0;
        e.mantenimiento.altasNuevasPorMes = 2;
      }));
      igual(r.totalProyectos, 0); igual(r.ratio, 0); igual(r.facturadoProyectos, 0);
      igual(r.altas, 0); assert(r.altasTopeadas === true);
      assert(r.capacidadEstado === 'verde');
      assert(r.embudoEstado === 'verde');
      igual(r.cajaDelMes, -2530);
    """, tmp_path)


def test_costo_por_lead_cero_no_divide_por_cero(tmp_path):
    _correr("""
      const r = simCalcular(escenario(e => { e.embudo.costoPorLead = 0; }));
      assert(r.leads === null && r.demos === null && r.ventasPosibles === null, 'sin estimacion');
      assert(r.costoPorVenta === null);
      assert(r.embudoEstado === 'neutro', r.embudoEstado);
      assert(r.conDefault.indexOf('embudo.costoPorLead') < 0, '0 es un valor valido, no se reemplaza');
      contiene(simTextoEmbudo(r), 'costo por lead en 0');
    """, tmp_path)


def test_pauta_cero(tmp_path):
    _correr("""
      const r = simCalcular(escenario(e => { e.ventas.pauta = 0; }));
      igual(r.leads, 0); igual(r.ventasPosibles, 0);
      assert(r.costoPorVenta === null, 'sin ventas no hay costo por venta');
      assert(simTextoEmbudo(r) === 'Con esa pauta salen 0 ventas, no 5.', simTextoEmbudo(r));
    """, tmp_path)


def test_vacios_negativos_y_basura_toman_el_default(tmp_path):
    _correr("""
      for (const malo of ['', '   ', '-100', -1, 'abc', null, undefined]) {
        const r = simCalcular(escenario(e => { e.equipo.sueldoPorProgramador = malo; }));
        igual(r.costoEquipo, 1000, 'sueldo ' + JSON.stringify(malo));
        assert(r.conDefault.indexOf('equipo.sueldoPorProgramador') >= 0, 'marca el default');
      }
      const pct = simCalcular(escenario(e => {
        e.mantenimiento.comisionCobro = 150;
        e.mantenimientos = [{nombre: 'A', monto: 100, activo: true}];
      }));
      igual(pct.recurrente, 95, 'un porcentaje arriba de 100 toma el default');

      const tope = simCalcular(escenario(e => { e.equipo.cantidadProgramadores = 9; }));
      igual(tope.cantidadProgramadores, 6, 'programadores va de 0 a 6');

      const decimal = simCalcular(escenario(e => { e.ventas.webs = '2.7'; e.equipo.proyectosPorProgramador = '1,5'; }));
      igual(decimal.totalProyectos, 2 + 2 + 1, 'una cantidad es entera');
      igual(decimal.capacidad, 3, 'la coma decimal se acepta');

      const vacia = simCalcular(escenario(e => { e.caja.cajaActual = ''; }));
      igual(vacia.cajaActual, 0);

      const filas = simCalcular(escenario(e => {
        e.gastosFijos = [{nombre: 'x', monto: '', activo: true}, {nombre: 'y', monto: -50, activo: true},
                         {nombre: 'z', monto: 'nada', activo: true}];
      }));
      igual(filas.listas.gastosFijos.total, 0); igual(filas.listas.gastosFijos.activos, 3);
    """, tmp_path)


def test_un_escenario_vacio_o_nulo_no_rompe(tmp_path):
    _correr("""
      for (const e of [null, undefined, {}, [], 'x', {equipo: null, gastosFijos: 'no'}]) {
        const r = simCalcular(e);
        igual(r.salidas, 1000 + 600, JSON.stringify(e));
        igual(r.listas.gastosFijos.activos, 0);
      }
    """, tmp_path)


def test_la_meta_sin_denominador(tmp_path):
    _correr("""
      const sinPrecio = simCalcular(escenario(e => {
        e.ventas.precioWeb = 0; e.ventas.precioEcom = 0; e.ventas.precioMedida = 0;
      }));
      assert(sinPrecio.meta.proyectos === null && sinPrecio.meta.faltanProg === null);
      contiene(simTextoMeta(sinPrecio), 'no hay cantidad de proyectos que alcance');

      const sinProy = simCalcular(escenario(e => { e.equipo.proyectosPorProgramador = 0; }));
      igual(sinProy.capacidad, 0);
      assert(sinProy.meta.proyectos === 5 && sinProy.meta.faltanProg === null, JSON.stringify(sinProy.meta));
      contiene(simTextoMeta(sinProy), 'Con 0 proyectos por programador');

      const cubierto = simCalcular(escenario(e => {
        e.mantenimientos = [{nombre: 'Grande', monto: 100000, activo: true}];
      }));
      assert(cubierto.meta.proyectos === 0 && cubierto.meta.faltanProg === 0);
      contiene(simTextoMeta(cubierto), 'no hace falta vender proyectos');
    """, tmp_path)


def test_redondeo_y_formato_es_uy(tmp_path):
    _correr("""
      assert(simUsd(1680) === 'USD 1.680', simUsd(1680));
      assert(simUsd(1234.5) === 'USD 1.235', simUsd(1234.5));
      assert(simUsd(-0.4) === 'USD 0', 'sin menos cero: ' + simUsd(-0.4));
      assert(simUsd(-2180.2) === 'USD -2.180', simUsd(-2180.2));
      assert(simUsd(null) === 'USD 0');
      assert(simTextoLista({activos: 1, total: 99.6}) === '1 activo · USD 100');
    """, tmp_path)


# ── la copia inicial ─────────────────────────────────────────────────────────

def test_la_base_usa_finanzas_si_hay_y_el_respaldo_si_no(tmp_path):
    _correr("""
      const sin = simEscenarioBase(null);
      assert(sin.gastosFijos.length === 6 && sin.origen.gastosFijos === 'defaults');
      assert(sin.gastosFijos.every(f => f.activo), 'el respaldo arranca prendido');
      assert(!sin.gastosFijos.some(f => f.nombre === 'Matías demos'), 'Matias ya no se paga');
      assert(sin.origen.caja === 'cajaDefaults' && sin.caja.cajaActual === 0);

      const con = simEscenarioBase({
        cajaActual: 1234.5,
        gastosFijos: [{nombre: 'Fly.io', monto: 45, activo: true, nota: ''}],
        mantenimientos: [{nombre: 'La Vaca', activo: true}],
        pendientes: [{nombre: 'Bar', monto: 500, activo: true, nota: 'vence 2026-10-01'}]
      });
      assert(con.gastosFijos.length === 1 && con.gastosFijos[0].nombre === 'Fly.io');
      assert(con.origen.gastosFijos === 'finanzas' && con.origen.caja === 'cajaFinanzas');
      igual(con.caja.cajaActual, 1234.5);
      assert(con.mantenimientos[0].activo === false, 'mantenimientos arrancan apagados');
      igual(con.mantenimientos[0].monto, SIM_DEFAULTS.precarga.cuotaPorCliente);
      assert(con.pendientes[0].activo === false, 'pendientes arrancan como cobro futuro');
      igual(simCalcular(con).porCobrarAdelante, 2100 + 500);

      const otra = simEscenarioBase(null);
      otra.equipo.sueldoPorProgramador = 1;
      assert(SIM_DEFAULTS.equipo.sueldoPorProgramador === 500, 'la base es una copia, no los defaults');
    """, tmp_path)


def test_un_escenario_viejo_se_completa_con_los_defaults(tmp_path):
    _correr("""
      const e = simEscenarioAbierto({equipo: {sueldoPorProgramador: 800},
                                     pendientes: [{nombre: 'x', monto: 10, activo: true}]});
      igual(e.equipo.sueldoPorProgramador, 800);
      igual(e.equipo.cantidadProgramadores, 2);
      igual(e.caja.cajaActual, 0);
      assert(e.gastosFijos.length === 0, 'una lista que no estaba queda vacia');
      assert(e.pendientes.length === 1);
      igual(simCalcular(e).costoEquipo, 1600);
      assert(e.cobros.formas.web === 'mitad' && e.cobros.formas.ecommerce === 'mitad'
             && e.cobros.formas.aMedida === 'mitad', 'uno viejo se abre con todo en dos partes');
    """, tmp_path)
