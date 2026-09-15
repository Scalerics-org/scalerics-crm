"""Ejecuta el pintado del panel de verdad, contra un DOM falso.

Existe por un bug que ya pasó dos veces y que `node --check` NO ve: usar un
`const` antes de su declaración. Es sintaxis válida —compila— y revienta recién
en ejecución con un ReferenceError. Y como el pintado es una función sola, la
excepción corta el panel entero a partir de ahí: quedan los títulos, que son
HTML estático, y ningún gráfico. Se ve como "el panel está raro", no como un
error.

La primera vez fue `campanas` en el bloque del ranking. La segunda,
`nombreSemana` en las series. No hay tercera.

Esto no reemplaza a los tests de cada gráfico: verifica que la orquestación
corra de punta a punta y que cada contenedor termine con algo adentro.

El 14/9 el panel pasó a mostrar la pauta como una sola (se fueron los bloques
por campaña) y las piezas a ir mes por mes, con su propio pedido a
`/api/marketing/piezas`. Por eso las piezas se pintan aparte, con su propio
fixture.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import dashboard

RAIZ = Path(__file__).resolve().parent.parent

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")

# Los contenedores que el pintado tiene que llenar. Si alguno queda vacío, o
# reventó antes de llegar o se lo está dibujando en un id que no existe.
#
# `mk-llegada` no está: desde el 14/9 "Cuándo llegan los leads" va semana por
# semana con su propio pedido, como las piezas, y se prueba aparte más abajo.
_CONTENEDORES = ["mk-tiles", "mk-embudo", "mk-mensual", "mk-series",
                 "mk-acumulado", "mk-segmentos",
                 "mk-conciliacion", "mk-hallazgos"]


def _dossier_de_prueba():
    """Un dossier con la forma real, chico pero con todos los bloques llenos.

    Con bloques vacíos el pintado toma los caminos de "sin datos" y no ejercita
    nada: el test pasaría con el panel roto.
    """
    def m(mid, valor, formato="numero"):
        return {"id": mid, "etiqueta": mid, "valor": valor, "formato": formato,
                "n": 20, "ic95": None, "muestra_chica": False, "fuente": "crm"}

    def campana(nombre, slug):
        return {"campana": nombre, "moneda": "USD", "metricas": [
            m(f"campana.{slug}.gasto", 500.0, "moneda"),
            m(f"campana.{slug}.impresiones", 10000),
            m(f"campana.{slug}.clics", 300),
            m(f"campana.{slug}.ctr", 0.03, "porcentaje"),
            m(f"campana.{slug}.leads_crm", 40),
            m(f"campana.{slug}.leads_meta", 42),
            m(f"campana.{slug}.cpl", 12.5, "moneda"),
            m(f"campana.{slug}.interesados", 20),
            m(f"campana.{slug}.agendadas", 12),
            m(f"campana.{slug}.demos", 8),
            m(f"campana.{slug}.presupuestos", 3),
            m(f"campana.{slug}.cierres", 1),
            m(f"campana.{slug}.costo_demo", 62.5, "moneda"),
            m(f"campana.{slug}.costo_presupuesto", 166.0, "moneda"),
            m(f"campana.{slug}.tasa_demo", 0.2, "porcentaje"),
            m(f"campana.{slug}.tasa_interes", 0.5, "porcentaje"),
        ]}

    # Tres semanas con actividad y una vacía en el medio, como devuelve ahora
    # `serie_semanal`: el hueco tiene que pintarse sin romper nada.
    semanas = [("2026-06-15", True), ("2026-06-22", False),
               ("2026-06-29", True), ("2026-07-06", True)]
    return {
        "periodo": {"desde": "2026-06-17", "hasta": "2026-07-08"},
        # Los bloques por campaña siguen viniendo en el dossier (el informe los
        # usa): el panel tiene que ignorarlos, no romperse con ellos.
        "campanas": [campana("UY", "uy"), campana("ARG", "arg"),
                     campana("todas", "todas")],
        "embudo_campanas": [],
        "serie_campanas": [],
        "serie_mensual": [
            {"periodo": "2026-06", "nombre": "Junio", "leads": 59, "demos": 8,
             "ventas": 2, "gasto": 368.98, "cpl": 6.25, "costo_demo": 46.1,
             "costo_venta": 184.5},
            {"periodo": "2026-07", "nombre": "Julio", "leads": 6,
             "demos": 3, "ventas": 0, "gasto": 293.86, "cpl": 48.98,
             "costo_demo": 97.95, "costo_venta": None},
        ],
        "serie_semanal": [
            {"inicio": s, "semana": f"2026-W2{i}",
             "gasto": 100.0 if activa else 0.0,
             "impresiones": 5000 if activa else 0,
             "clics": 150 if activa else 0,
             "leads_crm": 5 if activa else 0,
             "leads_meta": 5 if activa else 0,
             "cpl": 20.0 if activa else None,
             "con_actividad": activa,
             "gasto_acum": 100.0 * (i + 1), "leads_acum": 5 * (i + 1)}
            for i, (s, activa) in enumerate(semanas)],
        "segmentos": [{"pregunta": "presupuesto", "etiqueta": "Presupuesto",
                       "n": 40, "valores_distintos": 2, "valores": [
                           {"valor_declarado": "menos_de_500", "n": 25,
                            "metricas": [m("seg.presupuesto.menos.tasa_demo",
                                           0.2, "porcentaje")]},
                           {"valor_declarado": "mas_de_1000", "n": 15,
                            "metricas": [m("seg.presupuesto.mas.tasa_demo",
                                           0.1, "porcentaje")]}]}],
        "conciliacion": [m("conciliacion.gasto_meta.2026_06", 600.0, "moneda"),
                         m("conciliacion.gasto_cargado.2026_06", 0.0, "moneda"),
                         m("conciliacion.brecha.2026_06", 600.0, "moneda")],
        "tiempos": [m("tiempos.dias_hasta_demo", 3.5)],
        "recordatorios": [],
        "llegada": {
            "celdas": [{"dia": d, "franja": f, "n": (d + f) % 4}
                       for d in range(7) for f in range(0, 24, 3)],
            "total": 40, "maximo": 3, "sin_hora": 2, "horas_por_franja": 3,
        },
        # Tres anuncios corriendo: uno con foto, uno de video sin foto y uno
        # que gasto sin traer nada. Con bloques vacios el pintado toma los
        # caminos de "sin datos" y el test pasaria con la seccion rota.
        "anuncios": [
            {"ad_id": "120253602403650249", "nombre": "Web hace ganar - RMKTG",
             "campana": "Brand - Set26", "conjunto": "RMKTG", "tipo": "SHARE",
             "corriendo": True, "estado": "ACTIVE",
             "titulo": "Agencia de desarrollo web", "cuerpo": "Un cuerpo",
             "imagen_archivo": "/data/creativos/120253602403650249.jpg",
             "moneda": "USD", "gasto": 369.17, "leads": 33, "impresiones": 40000,
             "clics": 900, "cpl": 11.19, "ctr": 0.0225, "tasa_lead": 0.0367,
             "desde": "2026-06-14", "gasto_total": 512.40, "leads_total": 40,
             "cpl_total": 12.81, "ctr_total": 0.021,
             "leads_atribuidos": 18, "demos": 6, "costo_demo": 85.4,
             "mediana_cpl": 14.0, "oportunidad": 36.6,
             "recomendacion": {"accion": "subir", "texto": "Es de los que mejor rinden.",
                               "metricas_citadas": ["anuncio.120253602403650249.cpl"]}},
            {"ad_id": "120253602403650250", "nombre": "UGC - 2",
             "campana": "Brand - Set26", "conjunto": "UGC", "tipo": "VIDEO",
             "corriendo": True, "estado": "ACTIVE",
             "titulo": None, "cuerpo": "Mira como lo hacemos",
             "imagen_archivo": None,
             "moneda": "USD", "gasto": 120.0, "leads": 6, "impresiones": 15000,
             "clics": 200, "cpl": 20.0, "ctr": 0.0133, "tasa_lead": 0.03,
             "desde": "2026-08-01", "gasto_total": 150.0, "leads_total": 7,
             "cpl_total": 21.43, "ctr_total": 0.013,
             "leads_atribuidos": 7, "demos": 2, "costo_demo": 75.0,
             "mediana_cpl": 14.0, "oportunidad": 10.7,
             "recomendacion": {"accion": "ajustar", "texto": "Cada lead te sale caro.",
                               "metricas_citadas": ["anuncio.120253602403650250.cpl"]}},
            {"ad_id": "120243368449890249", "nombre": "12/03 - Hiciste lo mas dificil",
             "campana": "Leads - Marzo", "conjunto": "Amplio", "tipo": "SHARE",
             "corriendo": False, "estado": "PAUSED",
             "titulo": "Hiciste lo mas dificil", "cuerpo": "Otro cuerpo",
             "imagen_archivo": "/data/creativos/120243368449890249.jpg",
             "moneda": "USD", "gasto": 85.0, "leads": 0, "impresiones": 9000,
             "clics": 120, "cpl": None, "ctr": 0.0133, "tasa_lead": 0.0,
             "desde": "2026-09-01", "gasto_total": 85.0, "leads_total": 0,
             "cpl_total": None, "ctr_total": 0.0133,
             "leads_atribuidos": 0, "demos": 0, "costo_demo": None,
             "mediana_cpl": 14.0, "oportunidad": 6.07,
             "recomendacion": {"accion": "apagado", "texto": "Está apagado.",
                               "metricas_citadas": ["anuncio.120243368449890249.gasto"]}},
        ],
        "anuncios_resumen": {"anuncios": 3, "corriendo": 2, "apagados": 1,
                             "gasto": 574.17, "leads": 39,
                             "cpl": 14.72, "gasto_total": 747.40,
                             "leads_total": 47, "cpl_total": 15.90,
                             "desde": "2026-06-14", "moneda": "USD"},
        "historico": {
            "hay": True, "desde": "2026-03-11", "hasta": "2026-06-16",
            "semanas": 13, "leads": 120, "demos": 24, "gasto": 1500.0,
            "clics": 3800, "impresiones": 250000,
            "cpl": 12.5, "costo_demo": 62.5, "leads_semana": 9.23,
            "gasto_semana": 115.38, "clics_semana": 292.31,
            "impresiones_semana": 19230.77,
        },
        # Los de campaña no se tienen que ver: el panel lee `hallazgos_pauta`.
        "hallazgos": [{"tipo": "sin_cierres", "severidad": "alta",
                       "titulo": "UY gastó y todavía no cerró a nadie",
                       "cuerpo": "c", "metricas_citadas": ["campana.uy.gasto"]}],
        "hallazgos_pauta": [{"tipo": "pauta_sin_cierres", "severidad": "alta",
                             "titulo": "La pauta gastó y no cerró", "cuerpo": "c",
                             "metricas_citadas": ["campana.todas.gasto"]}],
    }


def _pieza(ad_id, nombre, corriendo, gasto, leads, imagen=True, leads_crm=None):
    return {"ad_id": ad_id, "nombre": nombre, "tipo": "SHARE" if imagen else "VIDEO",
            "titulo": None, "tiene_imagen": imagen, "corriendo": corriendo,
            "moneda": "USD", "gasto": gasto, "impresiones": 12000, "clics": 240,
            "leads": leads, "cpl": round(gasto / leads, 2) if leads else None,
            "ctr": 0.02, "primer_dia": "2026-09-02", "ultimo_dia": "2026-09-13",
            "leads_crm": leads_crm}


_PIEZAS = {
    "mes": "2026-09", "nombre": "Setiembre 2026", "desde": "2026-09-01",
    "hasta": "2026-09-30", "mes_actual": "2026-09", "primer_mes": "2026-03",
    "primer_dia": "2026-03-04", "estado_datos": "con_piezas", "datos_desde": None,
    "activas": [
        _pieza("120253602403650249", "Web hace ganar", True, 369.17, 33,
               leads_crm=28),
        _pieza("120253602403650250", "UGC - 2", True, 120.0, 6, imagen=False,
               leads_crm=4),
    ],
    "inactivas": [_pieza("120243368449890249", "Hiciste lo mas dificil", False,
                         85.0, 0, leads_crm=0)],
    "totales": {"piezas": 3, "activas": 2, "inactivas": 1, "gasto": 574.17,
                "leads": 39, "impresiones": 36000, "clics": 720, "cpl": 14.72,
                "ctr": 0.02, "moneda": "USD", "leads_crm": 35,
                "leads_crm_sin_pieza": 3},
    "gasto_pauta": 700.0,
}


_ARNES = r"""
// Un DOM mínimo: lo único que el pintado necesita es getElementById y poder
// escribirle innerHTML/textContent/value/style a lo que devuelve.
const _els = {};
function _el(id) {
  if (!_els[id]) {
    _els[id] = { id, innerHTML: '', textContent: '', value: '', disabled: false,
                 style: {}, dataset: {},
                 classList: { add(){}, remove(){}, toggle(){} },
                 querySelectorAll: () => [], querySelector: () => null,
                 addEventListener(){}, appendChild(){}, remove(){} };
  }
  return _els[id];
}
globalThis.document = {
  getElementById: _el,
  querySelectorAll: () => [],
  querySelector: () => null,
  body: { classList: { contains: () => false, add(){}, remove(){} } },
  createElement: () => _el('tmp'),
  addEventListener(){},
};
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem(){}, removeItem(){} };
globalThis.fetch = () => Promise.resolve({ ok: true, json: () => ({}) });
globalThis.lucide = { createIcons(){} };
globalThis.alert = () => {};
globalThis.confirm = () => true;
"""


def _correr(tmp_path, cola):
    bloques = re.findall(r"<script>(.*?)</script>", dashboard.DASHBOARD_HTML, re.S)
    charts = (RAIZ / "static" / "charts.js").read_text(encoding="utf-8")
    archivo = tmp_path / "pintar.js"
    archivo.write_text(_ARNES + charts + "\n" + "\n".join(bloques) + "\n" + cola,
                       encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, "el pintado del panel reventó:\n" + (r.stderr or "")[:2000]
    return json.loads(r.stdout.strip().splitlines()[-1])


_VOLCAR = ("console.log(JSON.stringify(Object.fromEntries("
           "Object.entries(_els).map(([k, v]) => [k, v.innerHTML || '']))));\n")


@sin_node
def test_el_panel_se_pinta_entero_sin_reventar(tmp_path):
    """El test que `node --check` no puede hacer: ejecutar.

    Si el pintado tira una excepción, los contenedores de después quedan vacíos
    y el panel se ve "raro" en vez de roto. Acá eso es un fallo con nombre.
    """
    dossier = json.dumps(_dossier_de_prueba(), ensure_ascii=False)
    html = _correr(tmp_path, f"_mkDossier = {dossier};\n_mkPintar();\n" + _VOLCAR)
    vacios = [c for c in _CONTENEDORES if not html.get(c)]
    assert not vacios, (
        f"estos contenedores quedaron vacíos: {vacios}. O el pintado cortó "
        "antes de llegar, o se está escribiendo en un id que no existe.")

    # Una sola pauta: ningún nombre de campaña llega a la pantalla.
    todo = "".join(html.values())
    assert "UY gastó" not in todo, "se pintó un hallazgo por campaña"
    assert "La pauta gastó" in html["mk-hallazgos"]
    # El mes a mes tiene su tabla, y la semana vacía del medio se dibuja.
    assert 'class="sc-tabla"' in html["mk-mensual"]
    assert html["mk-series"].count("Semana ") >= 4
    assert "Semana 2 · Leads: 0" in html["mk-series"]
    # Por lo que el lead declaró: donas, con el % de reunión en la leyenda.
    assert '<div class="sc-donas">' in html["mk-segmentos"]
    assert html["mk-segmentos"].count('class="sc-dona"') == 1
    assert "llegó a reunión" in html["mk-segmentos"]
    assert "sc-barras" not in html["mk-segmentos"].replace("sc-barras-ayuda", "")


@sin_node
def test_las_piezas_se_pintan_en_dos_grupos_y_legibles(tmp_path):
    piezas = json.dumps(_PIEZAS, ensure_ascii=False)
    html = _correr(tmp_path, f"_mkPintarPiezas({piezas});\n" + _VOLCAR)
    caja = html["mk-piezas"]
    assert "Activas hoy" in caja and "Ya no están activas" in caja
    assert caja.count('<article class="sc-anun"') == 3
    # Las activas van antes que las que ya no.
    assert caja.index("Web hace ganar") < caja.index("Hiciste lo mas dificil")
    assert caja.index("Activas hoy") < caja.index("Ya no están activas")
    # Nada en gris ni transparente, tampoco inline.
    assert "opacity" not in caja and "grayscale" not in caja
    # Las métricas del mes, todas.
    for rotulo in ("Gasto", "Impresiones", "Clics", "Leads", "Costo por lead"):
        assert rotulo in caja, rotulo
    assert "369,17" in caja and "574,17" in caja
    # Las piezas no dicen de qué campaña son.
    assert "Brand" not in caja
    # Las dos fuentes no cuadran (574,17 contra 700): el panel lo avisa.
    assert "700,00" in caja
    # Los dos leads, cada uno con su nombre, y ninguno sumado al otro.
    assert "Leads según Meta" in caja and "Leads en el CRM" in caja
    assert "3 sin pieza identificada" in caja
    # Nada que no sea del mes: se fue la recomendación de toda la vida.
    assert "sc-anun-reco" not in caja
    assert "desde que arrancó" not in caja


@sin_node
def test_un_mes_sin_piezas_lo_dice(tmp_path):
    vacio = dict(_PIEZAS, activas=[], inactivas=[], gasto_pauta=None,
                 estado_datos="sin_pauta",
                 totales=dict(_PIEZAS["totales"], piezas=0, activas=0,
                              inactivas=0, gasto=0.0, leads=0))
    html = _correr(tmp_path, f"_mkPintarPiezas({json.dumps(vacio)});\n" + _VOLCAR)
    assert "no se pautó ninguna pieza" in html["mk-piezas"]
    assert "No hay datos por pieza" not in html["mk-piezas"]
    assert "<article" not in html["mk-piezas"]


@sin_node
def test_un_mes_sin_datos_por_pieza_no_dice_que_no_se_pauto(tmp_path):
    """Mayo, anterior al primer dia guardado: falta el dato, no la pauta."""
    mayo = dict(_PIEZAS, mes="2026-05", nombre="Mayo 2026", desde="2026-05-01",
                hasta="2026-05-31", primer_mes="2026-06", primer_dia="2026-06-01",
                estado_datos="sin_datos_por_pieza", activas=[], inactivas=[],
                gasto_pauta=None)
    html = _correr(tmp_path, f"_mkPintarPiezas({json.dumps(mayo)});\n" + _VOLCAR)
    caja = html["mk-piezas"]
    assert "No hay datos por pieza de Meta guardados para Mayo 2026" in caja
    assert "01/06/2026" in caja
    assert "no se pautó" not in caja


@sin_node
def test_un_hueco_con_gasto_por_campana_dice_que_falta_traerlo(tmp_path):
    julio = dict(_PIEZAS, mes="2026-07", nombre="Julio 2026", desde="2026-07-01",
                 hasta="2026-07-31", estado_datos="sin_datos_por_pieza",
                 activas=[], inactivas=[], gasto_pauta=80.0)
    html = _correr(tmp_path, f"_mkPintarPiezas({json.dumps(julio)});\n" + _VOLCAR)
    assert "Meta registra gasto de pauta en ese mes" in html["mk-piezas"]


@sin_node
def test_avisa_si_el_mes_tiene_datos_desde_la_mitad(tmp_path):
    parcial = dict(_PIEZAS, datos_desde="2026-09-08")
    html = _correr(tmp_path, f"_mkPintarPiezas({json.dumps(parcial)});\n" + _VOLCAR)
    assert "empiezan el 08/09/2026" in html["mk-piezas"]


@sin_node
def test_sin_datos_de_meta_no_rompe(tmp_path):
    nada = dict(_PIEZAS, activas=[], inactivas=[], gasto_pauta=None,
                primer_mes=None, primer_dia=None, estado_datos="nada_sincronizado",
                totales=dict(_PIEZAS["totales"], piezas=0))
    html = _correr(tmp_path, f"_mkPintarPiezas({json.dumps(nada)});\n" + _VOLCAR)
    assert "Todavía no hay piezas sincronizadas" in html["mk-piezas"]


@sin_node
def test_la_flecha_lleva_exactamente_al_mes_pedido(tmp_path):
    """EL BUG: retroceder antes del primer mes con datos te dejaba en ese
    primer mes sin avisar. Juan creia estar en mayo y miraba agosto."""
    base = dict(_PIEZAS, activas=[], inactivas=[], gasto_pauta=None,
                primer_mes="2026-08", primer_dia="2026-08-10")
    cola = f"""
const _BASE = {json.dumps(base, ensure_ascii=False)};
_mkMesDeHoy = function () {{ return '2026-09'; }};
const _pedidos = [];
const _fetchDelArnes = globalThis.fetch;
globalThis.fetch = (url) => {{
  // Otros bloques del panel también piden cosas: solo cuentan las piezas.
  if (String(url).indexOf('/api/marketing/piezas') === -1) return _fetchDelArnes(url);
  _pedidos.push(url);
  const mes = decodeURIComponent(url.split('mes=')[1]);
  const d = Object.assign({{}}, _BASE, {{
    mes: mes, nombre: _mkNombreMes(mes), hasta: mes + '-31',
    estado_datos: mes < '2026-08' ? 'sin_datos_por_pieza' : 'sin_pauta' }});
  return Promise.resolve({{ ok: true, status: 200, json: () => Promise.resolve(d) }});
}};
_mkPiezasMesActual = null;
mkPiezasMes(1);                       // al futuro no va
const _alFuturo = _pedidos.length;
_mkPintarPiezas(Object.assign({{}}, _BASE, {{ mes: '2026-08', nombre: 'Agosto 2026' }}));
_mkPiezasMesActual = '2026-08';
mkPiezasMes(-1); mkPiezasMes(-1); mkPiezasMes(-1);
setTimeout(() => console.log(JSON.stringify({{
  alFuturo: _alFuturo, pedidos: _pedidos, actual: _mkPiezasMesActual,
  rotulo: _els['mk-piezas-mes'].textContent, ant: _els['mk-piezas-ant'].disabled,
  caja: _els['mk-piezas'].innerHTML }})), 50);
"""
    r = _correr(tmp_path, cola)
    assert r["alFuturo"] == 0
    assert [p.split("mes=")[1] for p in r["pedidos"]] == ["2026-07", "2026-06", "2026-05"]
    assert r["actual"] == "2026-05"
    assert r["rotulo"] == "Mayo 2026"
    assert r["ant"] is False, "la flecha hacia atras no se apaga en el primer mes con datos"
    assert "No hay datos por pieza de Meta guardados para Mayo 2026" in r["caja"]
    assert "Agosto" not in r["caja"]


# ── Cuándo llegan los leads, semana por semana ───────────────────────────

def _semana(lunes="2026-09-07", actual="2026-09-14", primera="2026-08-31", horas=None):
    """La forma de `/api/marketing/leads-semana`."""
    from datetime import date, timedelta
    horas = horas or {}
    inicio = date.fromisoformat(lunes)
    dias = []
    for i in range(7):
        fila = [horas.get((i, h), 0) for h in range(24)]
        dias.append({"fecha": (inicio + timedelta(days=i)).isoformat(),
                     "horas": fila, "total": sum(fila)})
    total = sum(d["total"] for d in dias)
    return {"semana": lunes, "hasta": (inicio + timedelta(days=6)).isoformat(),
            "semana_actual": actual, "primera_semana": primera, "dias": dias,
            "total": total, "maximo": max(max(d["horas"]) for d in dias) if total else None,
            "sin_hora": 0}


@sin_node
def test_la_semana_se_pinta_dia_por_dia_y_hora_por_hora(tmp_path):
    datos = _semana(horas={(0, 0): 1, (3, 11): 3, (6, 23): 1})
    datos["sin_hora"] = 2
    r = _correr(tmp_path, f"_mkPintarLlegada({json.dumps(datos)});\n" + """
console.log(JSON.stringify({ caja: _els['mk-llegada'].innerHTML,
  rotulo: _els['mk-llegada-semana'].textContent,
  ant: _els['mk-llegada-ant'].disabled, sig: _els['mk-llegada-sig'].disabled }));
""")
    caja = r["caja"]
    assert r["rotulo"] == "7 al 13 de setiembre 2026"
    assert r["sig"] is False, "desde la semana anterior a la actual se puede avanzar"
    assert r["ant"] is False, "la primera semana (31/8) es anterior: se puede retroceder"
    for dia in ("Lun 7/9", "Mar 8/9", "Jue 10/9", "Dom 13/9"):
        assert f'<th scope="row">{dia}</th>' in caja, dia
    # 24 columnas de hora, de 0 a 23, más el día y el total.
    assert caja.count('<th scope="col"') == 26
    assert '<th scope="col">0</th>' in caja and '<th scope="col">23</th>' in caja
    assert caja.count("<td") == 7 * 25
    assert 'data-n="3"' in caja and "Jue 10/9, de 11 a 12 h: 3 leads" in caja
    assert "<b>5 leads</b> en la semana" in caja
    assert '<td class="sc-lleg-total">3</td>' in caja
    assert "2 leads de esta semana no tienen hora guardada" in caja
    assert "NaN" not in caja and "undefined" not in caja
    # Colores solo con tokens.
    assert "var(--azul)" in caja and "#" not in caja.replace("&#39;", "")


@sin_node
def test_la_semana_actual_no_deja_avanzar(tmp_path):
    datos = _semana(lunes="2026-09-14", primera="2026-09-14", horas={(0, 5): 1})
    r = _correr(tmp_path, f"_mkPintarLlegada({json.dumps(datos)});\n" + """
console.log(JSON.stringify({ ant: _els['mk-llegada-ant'].disabled,
  sig: _els['mk-llegada-sig'].disabled }));
""")
    assert r == {"ant": True, "sig": True}


@sin_node
def test_una_semana_sin_leads_lo_dice(tmp_path):
    vacia = _semana()
    html = _correr(tmp_path, f"_mkPintarLlegada({json.dumps(vacia)});\n" + _VOLCAR)
    assert ("No entró ningún lead de Meta en la semana del 7 al 13 de setiembre 2026"
            in html["mk-llegada"])
    assert "<table" not in html["mk-llegada"]
    nunca = _semana(primera=None)
    html = _correr(tmp_path, f"_mkPintarLlegada({json.dumps(nunca)});\n" + _VOLCAR)
    assert "Todavía no hay ningún lead de Meta guardado" in html["mk-llegada"]


@sin_node
def test_el_rotulo_de_la_semana_cruza_mes_y_anio(tmp_path):
    r = _correr(tmp_path, """
console.log(JSON.stringify([_mkNombreSemana('2026-08-31'),
  _mkNombreSemana('2025-12-29'), _mkNombreSemana('2026-09-14')]));
""")
    assert r == ["31 de agosto al 6 de setiembre 2026",
                 "29 de diciembre 2025 al 4 de enero 2026",
                 "14 al 20 de setiembre 2026"]


@sin_node
def test_las_flechas_de_semana_no_van_al_futuro_ni_antes_del_primer_lead(tmp_path):
    cola = f"""
const _ACTUAL = {json.dumps(_semana(lunes="2026-09-14"))};
const _pedidos = [];
const _fetchDelArnes = globalThis.fetch;
globalThis.fetch = (url) => {{
  if (String(url).indexOf('/api/marketing/leads-semana') === -1) return _fetchDelArnes(url);
  _pedidos.push(String(url));
  const semana = String(url).indexOf('semana=') === -1
    ? _ACTUAL.semana : decodeURIComponent(String(url).split('semana=')[1]);
  const d = Object.assign({{}}, _ACTUAL, {{ semana: semana }});
  return Promise.resolve({{ ok: true, status: 200, json: () => Promise.resolve(d) }});
}};
const _espera = () => new Promise(r => setTimeout(r, 5));
(async () => {{
  _mkCargarLlegada(); await _espera();          // arranca en la actual, sin parámetro
  mkLlegadaSemana(1); await _espera();          // al futuro no va
  const alFuturo = _pedidos.length;
  mkLlegadaSemana(-1); await _espera();         // 7/9
  mkLlegadaSemana(-1); await _espera();         // 31/8, la del primer lead
  const enLaPrimera = {{ ant: _els['mk-llegada-ant'].disabled,
    rotulo: _els['mk-llegada-semana'].textContent }};
  mkLlegadaSemana(-1); await _espera();         // antes del primer lead: no
  const antesDelPrimero = _pedidos.length;
  mkLlegadaSemanaHoy(); await _espera();
  console.log(JSON.stringify({{ alFuturo, enLaPrimera, antesDelPrimero,
    pedidos: _pedidos, actual: _mkLlegadaSemana,
    rotulo: _els['mk-llegada-semana'].textContent }}));
}})();
"""
    r = _correr(tmp_path, cola)
    assert r["alFuturo"] == 1
    assert r["antesDelPrimero"] == 3
    assert [p.split("leads-semana")[1] for p in r["pedidos"]] == [
        "", "?semana=2026-09-07", "?semana=2026-08-31", ""]
    assert r["enLaPrimera"] == {"ant": True, "rotulo": "31 de agosto al 6 de setiembre 2026"}
    assert r["actual"] is None
    assert r["rotulo"] == "14 al 20 de setiembre 2026"


@sin_node
def test_con_un_mes_arriba_las_piezas_son_de_ese_mes(tmp_path):
    """EL BUG del 14/9: arriba decía abril y abajo seguían las piezas de
    setiembre, porque las piezas tenían su propio mes y la flecha de arriba no
    lo movía. Con "Un mes" arriba, hay un solo mes."""
    dossier = json.dumps(_dossier_de_prueba(), ensure_ascii=False)
    base = json.dumps(_PIEZAS, ensure_ascii=False)
    cola = f"""
const _DOSSIER = {dossier};
const _BASE = {base};
const _SEMANA = {json.dumps(_semana())};
_mkMesDeHoy = function () {{ return '2026-09'; }};
_mkMesVisible = function () {{ return new Date(2026, 8 + _mkMesOffset, 1); }};
_el('mk-rango').value = 'mes';
const _piezas = [];
globalThis.fetch = (url) => {{
  const u = String(url);
  let d = {{}};
  if (u.indexOf('/api/marketing/dossier') !== -1) d = _DOSSIER;
  if (u.indexOf('/api/marketing/leads-semana') !== -1) d = _SEMANA;
  if (u.indexOf('/api/marketing/piezas') !== -1) {{
    const mes = decodeURIComponent(u.split('mes=')[1]);
    _piezas.push(mes);
    d = Object.assign({{}}, _BASE, {{ mes: mes, nombre: _mkNombreMes(mes) }});
  }}
  return Promise.resolve({{ ok: true, status: 200, json: () => Promise.resolve(d) }});
}};
const _espera = () => new Promise(r => setTimeout(r, 50));
(async () => {{
  const s = {{}};
  mkMes(-5); await _espera();
  s.abril = [_piezas[_piezas.length - 1], _els['mk-piezas-mes'].textContent];
  mkPiezasMes(1); await _espera();
  s.mayo = [_piezas[_piezas.length - 1], _mkMesOffset, _els['mk-piezas-mes'].textContent];
  mkPiezasMesHoy(); await _espera();
  s.hoy = [_piezas[_piezas.length - 1], _mkMesOffset];
  s.caja = _els['mk-piezas'].innerHTML.length > 0;
  console.log(JSON.stringify(s));
}})();
"""
    r = _correr(tmp_path, cola)
    assert r["abril"] == ["2026-04", "Abril 2026"]
    assert r["mayo"] == ["2026-05", -4, "Mayo 2026"], "la flecha de las piezas mueve la sección"
    assert r["hoy"] == ["2026-09", 0]
    assert r["caja"]


# Hubo aquí un segundo test que buscaba el mismo bug leyendo el texto: por cada
# `const`, mirar si el nombre aparecía en una línea anterior. Se sacó porque
# daba falsos positivos y no había forma barata de arreglarlos: confundía los
# parámetros de las funciones flecha —`p`, `d`, `x`, `valor`— con variables del
# ámbito de afuera, y esos viven en su propio scope.
#
# Un test que grita sin motivo se termina ignorando, y ahí deja de proteger. El
# de arriba hace el mismo trabajo ejecutando, que además es la única forma
# honesta de saber que el panel se pinta.
