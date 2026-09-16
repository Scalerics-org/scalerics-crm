"""El menú de alternativas: objetivo del mes, selección en vivo y gastos esperados.

Lo que pidió Juan en el PDF del 15/9, en cuatro partes que se prueban acá:

1. El objetivo real del mes es la suma de tres partes editables, y da un número
   siempre (para Scalerics, alrededor de USD 3.270).
2. Cada alternativa es una tarjeta seleccionable con su impacto y su palanca, y
   elegir una actualiza en vivo el total contra el objetivo.
3. Una alternativa que recorta costo pero saca techo de ingreso muestra el
   impacto NETO, negativo y con el signo, no un ahorro limpio.
4. Los gastos esperados suman al objetivo y se emparejan con el movimiento real
   cuando cae, sin contarlo dos veces y sin adivinar cuando hay dudas.

Y la regla que está por encima de todas (pedido del 15/9): la pantalla NUNCA
abre diciendo que no hay análisis todavía.
"""

import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import dashboard
from database import (crear_movimiento, crear_recurrente, init_db,
                      insert_business, update_business, upsert_project)
from services import inteligencia_fin as ifn
from services import intel_objetivo as obj

AHORA = datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)
HOY = date(2026, 9, 15)
MES = "2026-09"
PREVIOS = ("2026-06", "2026-07", "2026-08")

SRC = (Path(__file__).resolve().parents[1] / "dashboard.py").read_text(encoding="utf-8")
HTML = dashboard.DASHBOARD_HTML

# Nada de esto puede aparecer nunca en la pantalla.
PROHIBIDAS = ("sin datos", "no hay análisis", "no hay analisis", "datos insuficientes",
              "no hay suficientes", "sacar conclusiones", "falta cargar")


def _entre(texto: str, desde: str, hasta: str) -> str:
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


CSS = _entre(SRC, "/* ── Inteligencia financiera", "/* ── Plantillas")
PANEL = _entre(SRC, "<!-- ======= INTELIGENCIA FINANCIERA PANEL ======= -->",
               "<!-- ======= FIN INTELIGENCIA FINANCIERA PANEL ======= -->")
JS = _entre(SRC, "// ========== Inteligencia financiera ==========",
            "// ========== FIN Inteligencia financiera ==========")


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "alt.db")
    init_db(ruta)
    conn = sqlite3.connect(ruta)
    conn.execute("UPDATE equipo_personas SET lleva_horas = 0")
    conn.execute("INSERT INTO equipo_personas (nombre, rol, lleva_horas, horas_por_dia, activo) "
                 "VALUES ('Matias Gomez', 'Dev', 1, 8, 1)")
    conn.commit()
    conn.close()
    return ruta


def _mov(db, tipo, monto, fecha, categoria="servicios", concepto=None, client_id=None):
    return crear_movimiento(db, tipo=tipo, fecha=fecha, periodo=fecha[:7],
                            concepto=concepto or f"{tipo} {categoria}", categoria=categoria,
                            monto=monto, moneda="USD", monto_usd=monto, client_id=client_id)


def _venta(db, nombre, periodo, precio):
    bid = insert_business(db, {"name": nombre, "source": "meta",
                               "scraped_at": f"{periodo}-01 09:00:00"})
    update_business(db, bid, crm_status="cerrado")
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO lead_events (lead_id, new_status, note, created_at) VALUES (?,?,'',?)",
                 (bid, "cerrado", f"{periodo}-05 10:00:00"))
    conn.commit()
    conn.close()
    _mov(db, "ingreso", precio, f"{periodo}-05", "desarrollo_web", "Web", client_id=bid)
    return bid


def _negocio_andando(db):
    """Un mes normal: ventas, gastos, un fijo a nombre de alguien del equipo."""
    for p in PREVIOS:
        _mov(db, "egreso", 400, f"{p}-10")
        _venta(db, f"Cliente {p}", p, 4000)
    crear_recurrente(db, tipo="egreso", concepto="Honorarios Matias", categoria="servicios",
                     monto=900, moneda="USD", desde="2026-01")
    upsert_project(db, "p1", "Proyecto 1", stage="Done", timeline_start="2026-07-01",
                   timeline_end="2026-07-10")


# ── 1. el objetivo real del mes ──────────────────────────────────────────────

def test_el_objetivo_es_la_suma_de_las_tres_partes(db):
    crear_recurrente(db, tipo="egreso", concepto="Hosting", categoria="infraestructura",
                     monto=170, moneda="USD", desde="2026-01")

    assert obj.guardar(db, MES, {"aportes_usd": 1100, "sueldo_usd": 2000}) is None
    o = obj.objetivo(db, MES)

    assert [p["clave"] for p in o["partes"]] == ["fijos", "aportes", "sueldo"]
    assert [p["monto"] for p in o["partes"]] == [170, 1100, 2000]
    # El número del PDF para Scalerics.
    assert o["total"] == 3270


def test_los_fijos_salen_de_finanzas_pero_se_pueden_pisar(db):
    crear_recurrente(db, tipo="egreso", concepto="Hosting", categoria="infraestructura",
                     monto=170, moneda="USD", desde="2026-01")
    assert obj.objetivo(db, MES)["partes"][0]["editado"] is False

    obj.guardar(db, MES, {"fijos_usd": 500})
    parte = obj.objetivo(db, MES)["partes"][0]
    assert parte["monto"] == 500 and parte["editado"] is True

    # Y se puede volver a los de Finanzas: se guarda NULL, no el número copiado,
    # asi el objetivo sigue a los fijos cuando cambian.
    obj.guardar(db, MES, {"fijos_usd": None})
    parte = obj.objetivo(db, MES)["partes"][0]
    assert parte["monto"] == 170 and parte["editado"] is False


def test_el_objetivo_se_guarda_por_mes_y_rechaza_negativos(db):
    obj.guardar(db, MES, {"sueldo_usd": 2000})
    assert obj.objetivo(db, MES)["total"] == 2000
    assert obj.objetivo(db, "2026-10")["total"] == 0
    assert obj.guardar(db, MES, {"sueldo_usd": -1}) is not None
    assert obj.guardar(db, MES, {"aportes_usd": "no es un numero"}) is not None
    assert obj.objetivo(db, MES)["total"] == 2000


# ── 2. las alternativas y su palanca ─────────────────────────────────────────

def test_cada_alternativa_trae_palanca_impacto_y_nota(db):
    _negocio_andando(db)
    ifn.corrida_diaria(db, AHORA)

    recs = ifn.estado_pantalla(db, ahora=AHORA)["recomendaciones"]

    assert recs
    for r in recs:
        assert r["palanca"] in ifn.PALANCAS, r["titulo"]
        assert r["nota"], r["titulo"]
        assert r["confianza"] in ("alta", "media", "baja")


def test_estan_las_cinco_palancas_del_pdf():
    assert set(ifn.PALANCAS) == {"pauta", "conversion", "canal", "recorte", "pausa"}
    assert set(ifn.ETIQUETA_PALANCA) == set(ifn.PALANCAS)


# ── 3. la regla de negocio: el neto honesto ──────────────────────────────────

def test_pausar_una_linea_muestra_el_neto_negativo_no_el_ahorro(db):
    """"No darle trabajo a Matías" ahorra su costo pero saca la capacidad de
    entregar. El impacto que se muestra es el NETO, y da negativo."""
    _negocio_andando(db)

    r12 = {r["regla"]: r for r in ifn.calcular(db, HOY)["recomendaciones"]}["R12"]

    assert r12["palanca"] == "pausa"
    assert r12["impacto_mensual"] < 0, "el ahorro limpio esconde lo que se deja de facturar"
    assert "Matias" in r12["titulo"]
    # La cuenta tiene que estar a la vista: lo que se ahorra y lo que se pierde.
    assert "Se ahorra" in r12["calculo"] and "Se pierde" in r12["calculo"]
    assert "Impacto NETO" in r12["calculo"]
    # Y se dice de dónde sale la atribución, que es un supuesto.
    assert any("no está en el sistema" in s for s in r12["supuestos"])


def test_la_negativa_sobrevive_al_umbral_y_queda_ultima(db):
    _negocio_andando(db)
    ifn.corrida_diaria(db, AHORA)

    recs = ifn.estado_pantalla(db, ahora=AHORA)["recomendaciones"]
    negativas = [r for r in recs if (r["impacto_mensual"] or 0) < 0]

    assert negativas, "una alternativa que resta tiene que verse"
    impactos = [r["impacto_mensual"] for r in recs if r["impacto_mensual"] is not None]
    assert impactos == sorted(impactos, reverse=True)


# ── 4. gastos esperados ──────────────────────────────────────────────────────

def test_el_gasto_esperado_suma_al_objetivo_en_vivo(db):
    antes = obj.objetivo(db, MES)["total"]
    obj.crear_esperado(db, MES, {"concepto": "Contador", "monto_usd": 300, "categoria": "fijo"})

    o = obj.objetivo(db, MES)

    assert o["total"] == antes + 300
    assert [g["concepto"] for g in o["esperados"]] == ["Contador"]
    assert o["partes"][0]["esperados"] == 300


def test_validaciones_del_gasto_esperado(db):
    assert obj.crear_esperado(db, MES, {"concepto": "", "monto_usd": 10})[1]
    assert obj.crear_esperado(db, MES, {"concepto": "X", "monto_usd": 0})[1]
    assert obj.crear_esperado(db, MES, {"concepto": "X", "monto_usd": 10, "categoria": "raro"})[1]
    assert obj.crear_esperado(db, MES, {"concepto": "X", "monto_usd": "nada"})[1]


def test_cuando_cae_el_movimiento_real_se_empareja_y_no_se_cuenta_dos_veces(db):
    gasto, _ = obj.crear_esperado(db, MES, {"concepto": "Contador setiembre", "monto_usd": 300,
                                            "categoria": "fijo"})
    assert obj.objetivo(db, MES)["total"] == 300

    # El mismo gasto, cargado en Finanzas con unos dólares de diferencia.
    mid = _mov(db, "egreso", 305, "2026-09-12", "servicios", "Contador setiembre")
    resultado = obj.emparejar_movimiento(db, mid)

    assert resultado["emparejado"] == gasto["id"]
    assert obj.objetivo(db, MES)["total"] == 0
    assert obj.listar_esperados(db, MES) == []


def test_con_dudas_pregunta_en_vez_de_adivinar(db):
    obj.crear_esperado(db, MES, {"concepto": "Servicio mensual A", "monto_usd": 300,
                                 "categoria": "variable"})
    obj.crear_esperado(db, MES, {"concepto": "Servicio mensual B", "monto_usd": 300,
                                 "categoria": "variable"})

    mid = _mov(db, "egreso", 300, "2026-09-12", "servicios", "Servicio mensual")
    resultado = obj.emparejar_movimiento(db, mid)

    assert resultado["emparejado"] is None, "con dos candidatos no puede elegir solo"
    assert len(resultado["dudas"]) == 2
    assert len(obj.listar_esperados(db, MES)) == 2
    # La pantalla las muestra para que Juan confirme.
    assert len(obj.objetivo(db, MES)["dudas"]) == 2

    # Y cuando confirma, se cierra ese y solo ese.
    duda = resultado["dudas"][0]
    assert obj.confirmar_emparejado(db, duda["gasto_id"], mid) is None
    assert len(obj.listar_esperados(db, MES)) == 1


def test_un_gasto_parecido_de_otro_monto_no_se_empareja_solo(db):
    obj.crear_esperado(db, MES, {"concepto": "Contador", "monto_usd": 300, "categoria": "fijo"})
    mid = _mov(db, "egreso", 900, "2026-09-12", "servicios", "Contador")

    resultado = obj.emparejar_movimiento(db, mid)

    assert resultado["emparejado"] is None
    assert resultado["dudas"], "coincide el concepto: hay que preguntar, no ignorarlo"


def test_el_emparejado_tambien_agarra_los_fijos_materializados(db):
    """Un movimiento puede aparecer sin pasar por la pantalla de carga (la
    materialización de un fijo). Igual se empareja al abrir el panel."""
    obj.crear_esperado(db, MES, {"concepto": "Hosting AWS", "monto_usd": 200, "categoria": "fijo"})
    _mov(db, "egreso", 200, "2026-09-03", "infraestructura", "Hosting AWS")

    # Nadie llamó a emparejar_movimiento: lo hace la lectura del objetivo.
    assert obj.objetivo(db, MES)["total"] == 0


# ── 5. nunca abre diciendo que no hay nada ───────────────────────────────────

def test_base_vacia_igual_muestra_objetivo_y_cinco_alternativas(db):
    ifn.corrida_diaria(db, AHORA)
    estado = ifn.estado_pantalla(db, es_admin=True, ahora=AHORA)

    assert len(estado["recomendaciones"]) >= 3
    assert {r["palanca"] for r in estado["recomendaciones"]} == set(ifn.PALANCAS)
    assert estado["objetivo"]["total"] == 0
    assert len(estado["objetivo"]["partes"]) == 3

    texto = json.dumps(estado, ensure_ascii=False, default=str).lower()
    for frase in PROHIBIDAS:
        assert frase not in texto, frase


def test_cada_oportunidad_dice_que_haria_y_con_que_se_enciende(db):
    ifn.corrida_diaria(db, AHORA)

    for r in ifn.estado_pantalla(db, ahora=AHORA)["recomendaciones"]:
        assert r["detalle"], r["titulo"]
        assert "se enciende" in r["nota"].lower(), r["nota"]
        # Una oportunidad es una oportunidad, no un reproche.
        assert "falta" not in r["nota"].lower(), r["nota"]


def test_el_panel_no_tiene_ningun_cartel_de_vacio_bloqueante():
    """El diagnóstico se esconde entero si no hay con qué armarlo."""
    assert "ifn-oculto" in PANEL
    assert 'id="ifn-diagnostico"' in PANEL and "ifn-diagnostico" in JS
    assert "hayDiag" in JS


# ── 6. lo que se ve: colores por palanca y tokens en los dos temas ───────────

def test_cada_palanca_tiene_su_color_y_su_tinte():
    for palanca in ifn.PALANCAS:
        assert f".ifn-alt-{palanca}" in CSS, palanca
        assert f"--pal-{palanca}:" in SRC and f"--pal-{palanca}-tinte:" in SRC, palanca


def test_los_tokens_nuevos_existen_en_los_dos_temas():
    oscuro = _entre(SRC, ":root{", "}")
    claro = _entre(SRC, "body.light{", "}")
    nuevos = [f"--pal-{p}" for p in ifn.PALANCAS] + [f"--pal-{p}-tinte" for p in ifn.PALANCAS] \
        + ["--obj-fondo", "--obj-texto", "--obj-rotulo", "--obj-logro", "--obj-pista"]
    for token in nuevos:
        assert f"{token}:" in oscuro, f"{token} falta en el tema oscuro"
        assert f"{token}:" in claro, f"{token} falta en el tema claro"


def test_el_css_nuevo_sigue_siendo_solo_tokens():
    """Sin colores escritos a mano: el tema claro sale de redefinir el token."""
    for selector, cuerpo in re.findall(r"([^{}]+)\{([^{}]*)\}", CSS):
        assert not re.search(r"#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|rgba?\(", cuerpo), selector.strip()
    assert "body.light" not in CSS


def test_el_impacto_negativo_se_pinta_en_rojo_y_con_signo():
    assert ".ifn-alt-neg{color:var(--rojo-texto)}" in CSS
    assert "ifn-alt-neg" in JS and "'−'" in JS


# ── 7. qué es un gasto fijo (corrección de Juan, 16/9) ───────────────────────

def _finanzas_como_en_produccion(db):
    """Los datos con la forma que tienen de verdad: los costos de cliente
    cargados como infraestructura común, sin cliente, y los sueldos como
    movimientos sueltos mes a mes."""
    for nombre in ("Diego Heinze", "Jose Pereira", "Rodrigo Silva"):
        bid = insert_business(db, {"name": nombre})
        update_business(db, bid, crm_status="finalizado")
    for concepto, monto, cat in [("Claude", 100, "herramientas"),
                                 ("Cloudfare", 5, "infraestructura"),
                                 ("Pasarela de Pagos", 100, "infraestructura"),
                                 ("Servidores Diego Heinze", 25, "infraestructura"),
                                 ("VPS Jose", 6.24, "infraestructura")]:
        crear_recurrente(db, tipo="egreso", concepto=concepto, categoria=cat,
                         monto=monto, moneda="USD", desde="2026-01")
    crear_recurrente(db, tipo="ingreso", concepto="Mantenimiento Rodrigo", categoria="software_medida",
                     monto=50, moneda="USD", desde="2026-01")
    for p in PREVIOS:
        for concepto, monto in [("Sueldo Programadores", 900), ("Honorarios Marketing", 400)]:
            _mov(db, "egreso", monto, f"{p}-05", "servicios", concepto)
        _mov(db, "egreso", 700, f"{p}-08", "publicidad", "Pauta Meta")
    # Un pago de un solo mes: no es un sueldo.
    _mov(db, "egreso", 200, "2026-07-20", "servicios", "Honorarios Mati Dominguez")


def test_el_costo_indirecto_de_un_cliente_no_es_un_gasto_fijo(db):
    """"gastos fijos son los sueldos de los empleados y los demas salvo gastos
    indirectos por cliente de mantenimiento como 25 de diego heinze"."""
    _finanzas_como_en_produccion(db)

    clases = obj.clasificar_fijos(db, MES)
    estructura = {f["concepto"] for f in clases["estructura"]}
    cliente = {f["concepto"] for f in clases["cliente"]}

    assert "Servidores Diego Heinze" in cliente and "VPS Jose" in cliente
    assert {"Claude", "Cloudfare", "Pasarela de Pagos"} <= estructura
    # Cada uno dice por qué quedó donde quedó: no es una adivinanza muda.
    assert all(f["motivo"] for f in clases["cliente"])


def test_una_palabra_tecnica_no_convierte_un_gasto_en_costo_de_cliente(db):
    """"Pasarela de Pagos" no puede cruzarse con un negocio que tenga "pagos"
    en el nombre: sin esto, media estructura se iría a costos de cliente."""
    bid = insert_business(db, {"name": "Pagos del Este"})
    update_business(db, bid, crm_status="finalizado")
    crear_recurrente(db, tipo="egreso", concepto="Pasarela de Pagos", categoria="infraestructura",
                     monto=100, moneda="USD", desde="2026-01")

    clases = obj.clasificar_fijos(db, MES)

    assert [f["concepto"] for f in clases["estructura"]] == ["Pasarela de Pagos"]
    assert clases["cliente"] == []


def test_juan_puede_corregir_la_clasificacion(db):
    _finanzas_como_en_produccion(db)
    fijo = next(f for f in obj.clasificar_fijos(db, MES)["cliente"]
                if f["concepto"] == "VPS Jose")

    assert obj.marcar_fijo(db, fijo["id"], "estructura", quien="Juan") is None

    clases = obj.clasificar_fijos(db, MES)
    movido = next(f for f in clases["estructura"] if f["concepto"] == "VPS Jose")
    assert movido["motivo"] == "lo marcaste vos"
    assert "VPS Jose" not in {f["concepto"] for f in clases["cliente"]}


def test_los_sueldos_cuentan_aunque_no_esten_en_la_pestana_de_fijos(db):
    """Los sueldos son el gasto fijo más grande y se cargan como movimientos.
    Si el objetivo mirara solo los recurrentes, quedaría por el piso."""
    _finanzas_como_en_produccion(db)

    equipo = obj.costo_equipo(db, MES)
    base = obj.base_estructural(db, MES)

    assert equipo["total"] == 1300, [f["concepto"] for f in equipo["filas"]]
    assert [f["concepto"] for f in equipo["sueltos"]] == ["Honorarios Mati Dominguez"]
    # Estructura (Claude 100 + Cloudfare 5 + Pasarela 100 = 205) + equipo
    # (1.300), sin los 31,24 de Diego Heinze y Jose: esos son de sus clientes.
    assert base["recurrentes_total"] == 205
    assert base["total"] == 1505
    assert base["por_cliente_total"] == 31.24


def test_la_pauta_y_los_impuestos_no_son_estructura(db):
    _finanzas_como_en_produccion(db)
    crear_recurrente(db, tipo="egreso", concepto="IVA", categoria="impuestos",
                     monto=1293, moneda="USD", desde="2026-01")
    crear_recurrente(db, tipo="egreso", concepto="Meta", categoria="publicidad",
                     monto=800, moneda="USD", desde="2026-01")

    base = obj.base_estructural(db, MES)

    fuera = {f["concepto"] for f in base["fuera"]}
    assert fuera == {"IVA", "Meta"}
    assert all(f["motivo"] for f in base["fuera"]), "tiene que decir por qué queda afuera"
    assert base["recurrentes_total"] == 205


def test_el_mantenimiento_se_razona_en_neto(db):
    """Un cliente que paga 50 y cuesta 25 en servidores deja 25, no 50."""
    _finanzas_como_en_produccion(db)
    for i in range(4):
        otro = insert_business(db, {"name": f"Sin cuota {i}"})
        update_business(db, otro, crm_status="finalizado")

    r1 = {r["regla"]: r for r in ifn.calcular(db, HOY)["recomendaciones"]}["R1"]

    assert "Cuota neta" in r1["calculo"], r1["calculo"]
    assert any("Costo indirecto por cliente" in s for s in r1["supuestos"]), r1["supuestos"]


def test_el_punto_de_equilibrio_no_cuenta_los_costos_de_cliente(db):
    _finanzas_como_en_produccion(db)

    ctx = ifn._contexto(db, HOY)

    assert ctx["fijos_cliente_total"] == 31.24
    # Solo los recurrentes de estructura: la pauta de estos datos es un
    # movimiento, no un fijo, asi que no entra por aca.
    assert ctx["fijos_total"] == 205
    assert "Servidores Diego Heinze" not in {r["concepto"] for r, _ in ctx["fijos"]}


# ── 8. poco texto ───────────────────────────────────────────────────────────

def test_ninguna_nota_de_tarjeta_pasa_de_60_caracteres(db):
    """"Tambien hay mucho texto". La tarjeta es título, una línea y el número."""
    _negocio_andando(db)
    ifn.corrida_diaria(db, AHORA)

    for r in ifn.estado_pantalla(db, ahora=AHORA)["recomendaciones"]:
        assert len(r["nota"]) <= 60, (len(r["nota"]), r["nota"])


def test_la_cuenta_y_el_analisis_vienen_plegados():
    assert '<summary class="ifn-ver">Ver la cuenta</summary>' in JS
    assert "ifn-analisis" in PANEL and "Cómo viene el mes" in PANEL
    # Ningún <details> arranca abierto.
    assert "open" not in "".join(re.findall(r"<details[^>]*>", JS + PANEL))


def test_la_bajada_es_una_linea(db):
    ifn.corrida_diaria(db, AHORA)
    bajada = ifn.estado_pantalla(db, ahora=AHORA)["bajada"]
    assert len(bajada) <= 60, bajada
