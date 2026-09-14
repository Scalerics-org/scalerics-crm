"""Los hallazgos que el panel saca solo, sin IA.

Existe porque el modulo prometia "una reflexion de como estamos y hacia donde
apuntar" y eso hoy solo lo da el informe de IA, que esta apagado y cuesta plata.
La mayor parte de esa reflexion no necesita un modelo: es mirar el dossier y
aplicar reglas. Lo que si necesita un modelo es redactarla bien.

La diferencia con el informe de IA es que aca **no hay nada que validar**: los
numeros salen del dossier por construccion, no se generan. Si un hallazgo cita
una metrica, es porque la leyo de ahi.

Cada regla existe porque contesta algo accionable. Una regla que solo describe
—"tenes 242 leads"— no entra: eso ya esta en los KPIs.
"""

import pytest

from services.hallazgos import buscar


def _metrica(mid, valor, n=None, muestra_chica=False):
    return {"id": mid, "etiqueta": mid, "valor": valor, "n": n,
            "muestra_chica": muestra_chica, "formato": "numero"}


def _campana(nombre, slug, gasto=None, leads=None, cpl=None, demos=None,
             costo_demo=None, cierres=None):
    ms = []
    for suf, v in (("gasto", gasto), ("leads_crm", leads), ("cpl", cpl),
                   ("demos", demos), ("costo_demo", costo_demo),
                   ("cierres", cierres)):
        if v is not None:
            ms.append(_metrica(f"campana.{slug}.{suf}", v))
    return {"campana": nombre, "metricas": ms}


def _dossier(**kw):
    base = {"periodo": {"desde": "2026-03-01", "hasta": "2026-09-11"},
            "campanas": [], "embudo_campanas": [], "serie_campanas": [],
            "conciliacion": [], "segmentos": [], "recordatorios": []}
    base.update(kw)
    return base


def _tipos(hallazgos):
    return [h["tipo"] for h in hallazgos]


# ── Plata que se va sin volver ───────────────────────────────────────────────

def test_avisa_de_una_campana_que_gasta_y_no_cierra():
    d = _dossier(campanas=[
        _campana("Form", "form", gasto=981.0, leads=85, demos=7, cierres=0),
        _campana("todas", "todas", gasto=981.0, leads=85, demos=7, cierres=0),
    ])
    hs = buscar(d)
    assert "sin_cierres" in _tipos(hs)
    h = [x for x in hs if x["tipo"] == "sin_cierres"][0]
    assert "Form" in h["titulo"]
    assert "campana.form.gasto" in h["metricas_citadas"]


def test_no_avisa_con_pocos_leads():
    """Cero cierres sobre 3 leads no dice nada: es la muestra, no la campana."""
    d = _dossier(campanas=[_campana("Chica", "chica", gasto=50.0, leads=3,
                                    demos=0, cierres=0)])
    assert "sin_cierres" not in _tipos(buscar(d))


def test_no_avisa_de_una_campana_sin_gasto_conocido():
    """Sin gasto no hay plata que se este yendo: no hay nada que decir."""
    d = _dossier(campanas=[_campana("Vieja", "vieja", gasto=0.0, leads=40,
                                    demos=2, cierres=0)])
    assert "sin_cierres" not in _tipos(buscar(d))


# ── El escalon del embudo ────────────────────────────────────────────────────

def _embudo(campana, **etapas):
    orden = ["leads", "interesados", "agendadas", "demos", "presupuestos",
             "cierres"]
    salida, previo = [], None
    for clave in orden:
        n = etapas.get(clave, 0)
        salida.append({"clave": clave, "etiqueta": clave, "n": n,
                       "tasa": (n / previo) if previo else None})
        previo = n
    return {"campana": campana, "etapas": salida}


def test_encuentra_la_etapa_donde_mas_se_cae():
    d = _dossier(embudo_campanas=[
        _embudo("UY", leads=100, interesados=90, agendadas=85, demos=80,
                presupuestos=20, cierres=18)])
    hs = [h for h in buscar(d) if h["tipo"] == "escalon"]
    assert hs, "no encontro el escalon"
    assert "presupuestos" in hs[0]["cuerpo"]


def test_el_escalon_ignora_las_campanas_chicas():
    d = _dossier(embudo_campanas=[
        _embudo("Chica", leads=4, interesados=1, agendadas=0)])
    assert "escalon" not in _tipos(buscar(d))


def test_no_avisa_de_que_cerrar_es_dificil():
    """Cerrar es SIEMPRE la etapa mas dura, asi que decirlo no informa nada. Y
    cuando los cierres son cero ya lo dice el hallazgo de `sin_cierres`: dos
    hallazgos para el mismo hecho es uno de mas."""
    d = _dossier(embudo_campanas=[
        _embudo("UY", leads=100, interesados=90, agendadas=85, demos=80,
                presupuestos=70, cierres=2)])
    hs = [h for h in buscar(d) if h["tipo"] == "escalon"]
    assert not hs, f"aviso del escalon de cierres: {hs}"


def test_una_caida_suave_no_es_un_escalon():
    """Si todas las etapas caen parejo no hay una etapa que arreglar."""
    d = _dossier(embudo_campanas=[
        _embudo("Pareja", leads=100, interesados=80, agendadas=64, demos=51,
                presupuestos=41, cierres=33)])
    assert "escalon" not in _tipos(buscar(d))


# ── El orden que se da vuelta ────────────────────────────────────────────────

def test_avisa_cuando_el_barato_por_lead_es_caro_por_demo():
    """El hallazgo que motivo el modulo: el Administrador de anuncios solo
    muestra la primera columna."""
    d = _dossier(campanas=[
        _campana("Form", "form", gasto=981.0, leads=85, cpl=11.5, demos=7,
                 costo_demo=140.2),
        _campana("UY", "uy", gasto=1603.0, leads=86, cpl=18.6, demos=23,
                 costo_demo=69.7),
    ])
    hs = [h for h in buscar(d) if h["tipo"] == "orden_invertido"]
    assert hs
    assert "Form" in hs[0]["cuerpo"] and "UY" in hs[0]["cuerpo"]


def test_no_compara_contra_una_campana_de_un_lead():
    """El error que el modulo existe para no cometer.

    Una campana con 1 lead y 1 demo tiene un costo por demo excelente por
    definicion, y ponerla como referencia contra una de 85 leads produce una
    conclusion falsa con cara de dato.
    """
    d = _dossier(campanas=[
        _campana("Grande", "grande", gasto=981.0, leads=85, cpl=11.5, demos=7,
                 costo_demo=140.2),
        _campana("Test", "test", gasto=29.0, leads=1, cpl=29.0, demos=1,
                 costo_demo=29.0),
    ])
    hs = [h for h in buscar(d) if h["tipo"] == "orden_invertido"]
    assert not hs, f"comparo contra una campana de 1 lead: {hs}"


def test_sin_inversion_no_avisa():
    d = _dossier(campanas=[
        _campana("A", "a", gasto=100.0, leads=10, cpl=10.0, demos=5, costo_demo=20.0),
        _campana("B", "b", gasto=300.0, leads=10, cpl=30.0, demos=5, costo_demo=60.0),
    ])
    assert "orden_invertido" not in _tipos(buscar(d))


# ── La brecha con Finanzas ───────────────────────────────────────────────────

def test_avisa_de_un_mes_sin_cargar_en_finanzas():
    d = _dossier(conciliacion=[
        _metrica("conciliacion.gasto_meta.2026_06", 608.0),
        _metrica("conciliacion.gasto_cargado.2026_06", 0.0),
        _metrica("conciliacion.brecha.2026_06", 608.0),
    ])
    hs = [h for h in buscar(d) if h["tipo"] == "brecha_finanzas"]
    assert hs and "2026-06" in hs[0]["cuerpo"]


def test_una_brecha_chica_no_molesta():
    d = _dossier(conciliacion=[
        _metrica("conciliacion.gasto_meta.2026_07", 610.0),
        _metrica("conciliacion.gasto_cargado.2026_07", 600.0),
        _metrica("conciliacion.brecha.2026_07", 10.0),
    ])
    assert "brecha_finanzas" not in _tipos(buscar(d))


# ── Forma de la salida ───────────────────────────────────────────────────────

def test_cada_hallazgo_cita_las_metricas_que_lo_sostienen():
    """Sin las citas no se puede auditar, y un hallazgo que no se puede auditar
    vale lo mismo que una opinion."""
    d = _dossier(campanas=[
        _campana("Form", "form", gasto=981.0, leads=85, demos=7, cierres=0)])
    for h in buscar(d):
        assert h["metricas_citadas"], h["titulo"]


def test_los_hallazgos_vienen_ordenados_por_importancia():
    """Plata que se va sin volver antes que una brecha contable."""
    d = _dossier(
        campanas=[_campana("Form", "form", gasto=981.0, leads=85, demos=7,
                           cierres=0)],
        conciliacion=[_metrica("conciliacion.gasto_meta.2026_06", 608.0),
                      _metrica("conciliacion.gasto_cargado.2026_06", 0.0),
                      _metrica("conciliacion.brecha.2026_06", 608.0)])
    tipos = _tipos(buscar(d))
    assert tipos.index("sin_cierres") < tipos.index("brecha_finanzas")


def test_un_dossier_vacio_no_inventa_hallazgos():
    assert buscar(_dossier()) == []


def test_no_devuelve_una_lista_interminable():
    """Veinte hallazgos son cero hallazgos: nadie los lee."""
    campanas = [_campana(f"C{i}", f"c{i}", gasto=500.0, leads=50, demos=2,
                         cierres=0) for i in range(20)]
    assert len(buscar(_dossier(campanas=campanas))) <= 6
