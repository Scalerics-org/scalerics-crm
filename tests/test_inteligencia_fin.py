"""Inteligencia financiera del lado del servidor: reglas, datos previos, permisos.

Las fechas son fijas (AHORA) y se pasan a las funciones: la ventana de 3 meses,
los vencimientos y la medición a 30 días no pueden depender del reloj de quien
corre la suite.
"""

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (create_user, crear_movimiento, crear_por_cobrar,
                      crear_recurrente, init_db, insert_business,
                      update_business, upsert_notion_client, upsert_project,
                      vincular_notion_client)
from services import inteligencia_fin as ifn
from services.equipo import dias_habiles

AHORA = datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)   # 12:00 en Montevideo
HOY = date(2026, 9, 15)

_tel = iter(range(91000000, 91999999))


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "ifn.db")
    init_db(ruta)
    conn = sqlite3.connect(ruta)
    # El equipo precargado no cuenta: una sola persona de 8 h, para que la
    # capacidad del mes sea una cuenta que se pueda seguir (22 días hábiles).
    conn.execute("UPDATE equipo_personas SET lleva_horas = 0")
    conn.execute("INSERT INTO equipo_personas (nombre, rol, lleva_horas, horas_por_dia, activo) "
                 "VALUES ('Dev de prueba', 'Dev', 1, 8, 1)")
    conn.commit()
    conn.close()
    return ruta


def _negocio(db, nombre, estado="cerrado", source=None, phone=None, maps_url=None):
    bid = insert_business(db, {"name": nombre, "phone": phone, "source": source,
                               "maps_url": maps_url})
    update_business(db, bid, crm_status=estado)
    return bid


def _evento(db, lead_id, estado, fecha):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO lead_events (lead_id, new_status, note, created_at) VALUES (?, ?, '', ?)",
                 (lead_id, estado, f"{fecha} 10:00:00"))
    conn.commit()
    conn.close()


def _venta(db, nombre, fecha, source="meta"):
    bid = _negocio(db, nombre, "cerrado", source=source, phone=f"+598{next(_tel)}")
    _evento(db, bid, "cerrado", fecha)
    return bid


def _ingreso(db, client_id, monto, fecha, categoria="desarrollo_web"):
    return crear_movimiento(db, tipo="ingreso", fecha=fecha, periodo=fecha[:7], concepto="Cobro",
                            categoria=categoria, monto=monto, moneda="USD", monto_usd=monto,
                            client_id=client_id)


def _egreso(db, monto, fecha, categoria="servicios"):
    return crear_movimiento(db, tipo="egreso", fecha=fecha, periodo=fecha[:7], concepto="Gasto",
                            categoria=categoria, monto=monto, moneda="USD", monto_usd=monto)


def _proyecto(db, nombre, business_id, horas=None, stage="Done"):
    page = f"p-{nombre}"
    pid = upsert_project(db, page, nombre, stage=stage,
                         timeline_start="2026-07-01", timeline_end="2026-07-31")
    nc = upsert_notion_client(db, f"nc-{nombre}", f"Ficha {nombre}", status="Presupuesto Aceptado",
                              notion_project_page_id=page)
    vincular_notion_client(db, nc, business_id)
    if horas:
        _, error = ifn.guardar_esfuerzo(db, pid, {"valor": horas, "unidad": "horas"}, "test", AHORA)
        assert error is None
    return pid


def _pauta(db, montos):
    conn = sqlite3.connect(db)
    for i, (fecha, monto) in enumerate(montos):
        conn.execute("INSERT INTO meta_insights (date, campaign_id, spend, currency) VALUES (?, ?, ?, 'USD')",
                     (fecha, f"c{i}", monto))
    conn.commit()
    conn.close()


def _escenario_margen(db, source="meta", gasto=True):
    """3 ventas en la ventana, una con proyecto de 80 h y USD 2.000 cobrados."""
    b1 = _venta(db, "Cliente uno", "2026-07-01", source)
    _venta(db, "Cliente dos", "2026-08-01", source)
    _venta(db, "Cliente tres", "2026-09-01", source)
    _ingreso(db, b1, 2000, "2026-08-10")
    _proyecto(db, "Web uno", b1, horas=80)
    _egreso(db, 660, "2026-08-05")
    if gasto:
        _pauta(db, [("2026-07-10", 300), ("2026-08-10", 300), ("2026-09-10", 300)])
    return b1


def _reglas(db, hoy=HOY):
    calc = ifn.calcular(db, hoy)
    return {r["regla"]: r for r in calc["recomendaciones"]}, calc


def _aviso(calc, clave):
    return next((a for a in calc["avisos"] if a["clave"] == clave), None)


# ── R1 ───────────────────────────────────────────────────────────────────────

def test_r1_con_6_clientes_sin_mantenimiento_aparece_con_el_calculo(db):
    con_cuota = _negocio(db, "Ya paga", "en_desarrollo")
    for i in range(6):
        _negocio(db, f"Sin cuota {i}", "finalizado")
    crear_recurrente(db, tipo="ingreso", concepto="Mantenimiento Ya paga", categoria="mantenimiento",
                     monto=100, moneda="USD", desde="2026-01", client_id=con_cuota)
    assert ifn.guardar_supuesto(db, "comision_cobro_pct", 5) is None

    recs, _ = _reglas(db)

    r1 = recs["R1"]
    assert r1["titulo"] == "Cobrales mantenimiento a los 6 clientes que ya tenés"
    assert r1["impacto_mensual"] == 570
    assert "Impacto = 6 × USD 100 × (1 − 5 %) = USD 570 por mes" in r1["calculo"]
    assert "Cuota promedio = USD 100 ÷ 1 = USD 100" in r1["calculo"]
    assert (r1["confianza"], r1["tipo"]) == ("alta", "ingreso")


def test_r1_sin_comision_no_estima_y_avisa(db):
    con_cuota = _negocio(db, "Ya paga")
    _negocio(db, "Sin cuota")
    crear_recurrente(db, tipo="ingreso", concepto="Mant", categoria="mantenimiento",
                     monto=100, moneda="USD", desde="2026-01", client_id=con_cuota)

    recs, calc = _reglas(db)

    assert "R1" not in recs
    assert "R1" in _aviso(calc, "comision")["reglas"]


def test_r1_sin_ninguna_cuota_de_referencia_avisa_en_vez_de_inventarla(db):
    _negocio(db, "Sin cuota")
    ifn.guardar_supuesto(db, "comision_cobro_pct", 5)

    recs, calc = _reglas(db)

    assert "R1" not in recs
    assert _aviso(calc, "cuota")["reglas"] == ["R1"]


# ── R2 y R3 ──────────────────────────────────────────────────────────────────

def test_r2_con_capacidad_libre_1_recomienda_la_pauta_de_1_venta_y_dice_cuanto_se_tira(db):
    # 176 h en septiembre ÷ 80 h por proyecto = 2 huecos. La pauta (USD 300 por
    # mes, USD 300 por venta) ya trae 1 venta: la capacidad libre es 1.
    _escenario_margen(db)

    recs, calc = _reglas(db)

    r2 = recs["R2"]
    assert r2["titulo"] == "Subí la pauta de USD 300 a USD 600, no más"
    assert "capacidad libre = 2 − 1 = 1" in r2["calculo"]
    assert "Pauta recomendada = USD 300 + 1 × USD 300 = USD 600" in r2["calculo"]
    assert "176 h" in r2["calculo"] and "80 h por proyecto" in r2["calculo"]
    assert "Pasarte en una venta tira USD 300" in r2["advertencia"]
    assert "el tope lo pone el equipo, no el presupuesto" in r2["detalle"]
    margen = calc["contexto"]["margen"]["margen_venta"]
    assert r2["impacto_mensual"] == round(margen - 300, 2)
    assert (r2["confianza"], r2["tipo"]) == ("media", "ingreso")
    assert "R3" not in recs, "la misma capacidad no se cuenta dos veces"


def test_r2_nunca_pasa_la_capacidad_aunque_el_retorno_invite_a_mas(db):
    _escenario_margen(db)
    # Mucha más plata para pauta no cambia nada: el techo es el equipo.
    _pauta(db, [("2026-09-11", 0)])
    recs, _ = _reglas(db)
    assert "1 venta más" in recs["R2"]["detalle"]


def test_r3_capacidad_ociosa_cuando_la_pauta_no_alcanza(db):
    _escenario_margen(db, source="discovery", gasto=False)

    recs, calc = _reglas(db)

    r3 = recs["R3"]
    margen = calc["contexto"]["margen"]["margen_venta"]
    assert r3["titulo"] == "Te sobran 2 proyectos de capacidad y la pauta no alcanza"
    assert "Capacidad ociosa = 2 − 0 = 2" in r3["calculo"]
    assert r3["impacto_mensual"] == round(2 * margen, 2)
    assert r3["confianza"] == "media"
    assert "R2" not in recs


def test_r2_y_r3_con_ventas_sin_origen_avisan(db):
    _escenario_margen(db)
    _venta(db, "Alta a mano", "2026-09-02", source="manual")

    recs, calc = _reglas(db)

    assert "R2" not in recs and "R3" not in recs
    aviso = _aviso(calc, "origen")
    assert {"R2", "R3"} <= set(aviso["reglas"]) and aviso["cantidad"] == 1


# ── R4 ───────────────────────────────────────────────────────────────────────

def test_r4_cobros_vencidos_con_boton_de_whatsapp(db):
    bid = _negocio(db, "Bar Tito", phone="099 123 456")
    crear_por_cobrar(db, client_id=bid, concepto="50% final", monto_usd=800, vence="2026-09-01")
    crear_por_cobrar(db, client_id=bid, concepto="Todavía no vence", monto_usd=500, vence="2026-10-01")

    recs, _ = _reglas(db)

    r4 = recs["R4"]
    assert r4["titulo"] == "Cobrá los USD 800 vencidos"
    assert r4["impacto_mensual"] == 800 and r4["unica_vez"] is True
    assert "Total vencido = USD 800 = USD 800, por única vez" in r4["calculo"]
    accion = r4["acciones"][0]
    assert accion["wa"] == "59899123456"
    assert "USD 800" in accion["texto"] and "01/09/2026" in accion["texto"]
    assert (r4["confianza"], r4["tipo"]) == ("alta", "ingreso")


# ── R5 ───────────────────────────────────────────────────────────────────────

def test_r5_gasto_fijo_sin_ventas_con_la_advertencia(db):
    rid = crear_recurrente(db, tipo="egreso", concepto="Zoho", categoria="herramientas",
                           monto=150, moneda="USD", desde="2026-01")
    assert ifn.guardar_canal_fijo(db, rid, "outbound") is None
    _venta(db, "Vino por Meta", "2026-08-01", source="meta")

    recs, _ = _reglas(db)

    r5 = recs["R5"]
    assert r5["titulo"] == "Probá un mes sin los gastos fijos de Outbound"
    assert r5["impacto_mensual"] == 150
    assert r5["advertencia"] == ifn.ADVERTENCIA_R5
    assert "no mide" in r5["advertencia"] and "un mes sin" in r5["advertencia"]
    assert (r5["confianza"], r5["tipo"]) == ("media", "recorte")


def test_r5_no_aparece_si_el_canal_vendio(db):
    rid = crear_recurrente(db, tipo="egreso", concepto="Zoho", categoria="herramientas",
                           monto=150, moneda="USD", desde="2026-01")
    ifn.guardar_canal_fijo(db, rid, "outbound")
    _venta(db, "Del padrón", "2026-08-01", source="discovery")
    recs, _ = _reglas(db)
    assert "R5" not in recs


# ── R6 ───────────────────────────────────────────────────────────────────────

def test_r6_sin_esfuerzo_cargado_no_aparece_y_muestra_el_aviso(db):
    bid = _venta(db, "Cliente", "2026-08-01")
    _ingreso(db, bid, 1000, "2026-08-10")
    _proyecto(db, "Sin horas", bid, horas=None)
    _egreso(db, 500, "2026-08-05")

    recs, calc = _reglas(db)

    assert "R6" not in recs
    aviso = _aviso(calc, "esfuerzo")
    assert aviso and "R6" in aviso["reglas"]
    assert "Habilitaría" in aviso["detalle"]


def test_r6_margen_bajo_dice_revisar_el_precio_nunca_abandonar(db):
    bid = _venta(db, "Cliente", "2026-08-01")
    _ingreso(db, bid, 1000, "2026-08-10")
    _proyecto(db, "Landing", bid, horas=80)
    dias = len(dias_habiles(*ifn.ventana(HOY)))
    _egreso(db, 12 * 8 * dias, "2026-08-05")   # costo por hora: USD 12

    recs, _ = _reglas(db)

    r6 = recs["R6"]
    assert r6["titulo"] == "Los proyectos de tipo Desarrollo web te dejan 4 %"
    assert r6["impacto_mensual"] is None
    assert "nunca abandonar la línea" in r6["advertencia"]
    assert "Revisá el precio" in r6["detalle"]
    assert "Landing: USD 1.000 (cobros en Finanzas) − 80 h × USD 12 = USD 40" in r6["calculo"]
    assert (r6["confianza"], r6["tipo"]) == ("alta", "alerta")


# ── R7 ───────────────────────────────────────────────────────────────────────

def _fichas_perdidas(db, motivos):
    ids = []
    for i, motivo in enumerate(motivos):
        nc = upsert_notion_client(db, f"perdida-{i}", f"Perdida {i}", status="Perdido")
        if motivo:
            assert ifn.guardar_motivo(db, "notion_client", nc, motivo, "test", AHORA) is None
        else:
            ifn.registrar_perdida_ficha(db, f"perdida-{i}", AHORA)
        ids.append(nc)
    return ids


def test_r7_motivo_con_mas_del_40_por_ciento(db):
    _escenario_margen(db)
    _fichas_perdidas(db, ["precio", "precio", "se_enfrio"])

    recs, calc = _reglas(db)

    r7 = recs["R7"]
    margen = calc["contexto"]["margen"]["margen_venta"]
    assert r7["titulo"] == "El 67 % de lo que perdés es por precio"
    assert r7["impacto_mensual"] == round(2 / 3 * margen * 0.30, 2)
    assert "2 ÷ 3 = 66,7 %" in r7["calculo"]
    assert "30 % de recupero" in r7["calculo"]
    assert r7["confianza"] == "baja"


def test_r7_no_aparece_si_ningun_motivo_pasa_el_40(db):
    _escenario_margen(db)
    _fichas_perdidas(db, ["precio", "se_enfrio", "eligio_otro"])
    recs, _ = _reglas(db)
    assert "R7" not in recs


def test_r7_con_perdidas_sin_motivo_avisa_en_vez_de_estimar(db):
    _escenario_margen(db)
    _fichas_perdidas(db, ["precio", "precio", None])
    recs, calc = _reglas(db)
    assert "R7" not in recs
    assert _aviso(calc, "motivo")["cantidad"] == 1


# ── reglas transversales ─────────────────────────────────────────────────────

def _todo(db):
    """Un sistema con R2, R4, R5 y R7 a la vez."""
    _escenario_margen(db)
    _fichas_perdidas(db, ["precio", "precio", "se_enfrio"])
    # Debe plata pero no es una venta (acepto, falta firma): si fuera cliente sin
    # origen, bloquearía R2 y R5, que es justo lo que tiene que pasar.
    bid = _negocio(db, "Debe", "acepto", phone="099 555 444")
    crear_por_cobrar(db, client_id=bid, concepto="Saldo", monto_usd=800, vence="2026-09-01")
    rid = crear_recurrente(db, tipo="egreso", concepto="Clay", categoria="herramientas",
                           monto=150, moneda="USD", desde="2026-01")
    ifn.guardar_canal_fijo(db, rid, "referido")


def test_ninguna_recomendacion_sin_confianza(db):
    _todo(db)
    recs, _ = _reglas(db)
    assert len(recs) >= 4
    for r in recs.values():
        assert r["confianza"] in ("alta", "media", "baja")
    ifn.recalcular(db, AHORA)
    estado = ifn.estado_pantalla(db, ahora=AHORA)
    assert estado["recomendaciones"]
    assert all(r["confianza"] in ("alta", "media", "baja") for r in estado["recomendaciones"])


def test_cada_calculo_muestra_el_numero_que_da(db):
    """Criterio 8: el impacto sale de la cuenta que está a la vista."""
    _todo(db)
    recs, _ = _reglas(db)
    for r in recs.values():
        if r["impacto_mensual"] is not None:
            assert ifn.usd(r["impacto_mensual"]) in r["calculo"], r["regla"]


def _falsa(regla, impacto):
    return {"regla": regla, "clave": str(impacto), "impacto_mensual": impacto}


def test_umbral_de_100_por_mes():
    visibles = ifn.ordenar([_falsa("R1", 99.99), _falsa("R4", 100), _falsa("R6", None), _falsa("R5", 40)])
    assert [r["impacto_mensual"] for r in visibles] == [100, None]


def test_impacto_menor_a_100_no_llega_a_la_pantalla(db):
    con_cuota = _negocio(db, "Ya paga")
    _negocio(db, "Sin cuota")
    crear_recurrente(db, tipo="ingreso", concepto="Mant", categoria="mantenimiento",
                     monto=50, moneda="USD", desde="2026-01", client_id=con_cuota)
    ifn.guardar_supuesto(db, "comision_cobro_pct", 5)
    assert _reglas(db)[0]["R1"]["impacto_mensual"] == 47.5
    ifn.corrida_diaria(db, AHORA)
    assert ifn.estado_pantalla(db, ahora=AHORA)["recomendaciones"] == []


def test_maximo_6_ordenadas_por_impacto():
    recs = [_falsa("R1", m) for m in (150, 900, None, 300, 5000, 120, 700, 101)]
    visibles = ifn.ordenar(recs)
    assert [r["impacto_mensual"] for r in visibles] == [5000, 900, 700, 300, 150, 120]


def test_criterio_1_con_datos_faltantes_no_hay_recomendaciones_que_dependan_de_ellos(db):
    b1 = _venta(db, "Cliente uno", "2026-07-01")
    _ingreso(db, b1, 2000, "2026-08-10")
    _proyecto(db, "Sin horas", b1)
    _egreso(db, 660, "2026-08-05")
    _pauta(db, [("2026-08-10", 900)])

    ifn.corrida_diaria(db, AHORA)
    estado = ifn.estado_pantalla(db, ahora=AHORA)

    reglas = {r["regla"] for r in estado["recomendaciones"]}
    assert not reglas & {"R2", "R3", "R6", "R7"}
    esfuerzo = next(a for a in estado["avisos"] if a["clave"] == "esfuerzo")
    assert {"R2", "R3", "R6"} <= set(esfuerzo["reglas"])
    assert estado["datos"]["esfuerzo"]["completo"] is False
    assert estado["datos"]["esfuerzo"]["pendientes"][0]["nombre"] == "Sin horas"


# ── Lo voy a hacer y la medición a 30 días ───────────────────────────────────

def _r4_tomada(db):
    bid = _negocio(db, "Bar Tito", phone="099 123 456")
    pc = crear_por_cobrar(db, client_id=bid, concepto="50% final", monto_usd=800, vence="2026-09-01")
    ifn.corrida_diaria(db, AHORA)
    rec = next(r for r in ifn.estado_pantalla(db, ahora=AHORA)["recomendaciones"] if r["regla"] == "R4")
    ok, error = ifn.tomar(db, rec["id"], "Juan", AHORA)
    assert ok and error is None
    return pc, rec


def test_lo_voy_a_hacer_guarda_impacto_y_fecha_y_a_los_30_dias_mide(db):
    from services.finanzas import saldar_por_cobrar

    pc, rec = _r4_tomada(db)
    estado = ifn.estado_pantalla(db, ahora=AHORA)
    assert rec["id"] not in [r["id"] for r in estado["recomendaciones"]]
    seg = estado["seguimiento"][0]
    assert seg["resultado"] == "midiendo" and seg["impacto_esperado"] == 800
    assert seg["tomada_el"] == "15/09/2026" and seg["se_mide_el"] == "15/10/2026"
    assert ifn.tomar(db, rec["id"], "Juan", AHORA)[1][0] == 409

    # A los 29 días sigue midiendo.
    ifn.corrida_diaria(db, AHORA + timedelta(days=29))
    assert ifn.estado_pantalla(db, ahora=AHORA + timedelta(days=29))["seguimiento"][0]["resultado"] == "midiendo"

    saldar_por_cobrar(db, pc, fecha="2026-09-20")
    ifn.corrida_diaria(db, AHORA + timedelta(days=31))
    seg = ifn.estado_pantalla(db, ahora=AHORA + timedelta(days=31))["seguimiento"][0]
    assert seg["resultado"] == "funciono"
    assert seg["impacto_real"] == 800
    assert "Cobrados 1 de 1" in seg["detalle_real"]


def test_a_los_30_dias_sin_cobrar_no_funciono(db):
    _r4_tomada(db)
    ifn.corrida_diaria(db, AHORA + timedelta(days=31))
    seg = ifn.estado_pantalla(db, ahora=AHORA + timedelta(days=31))["seguimiento"][0]
    assert seg["resultado"] == "no_funciono" and seg["impacto_real"] == 0


def test_lo_tomado_no_se_vuelve_a_sugerir_mientras_se_mide(db):
    _r4_tomada(db)
    ifn.corrida_diaria(db, AHORA + timedelta(days=1))
    reglas = [r["regla"] for r in ifn.estado_pantalla(db, ahora=AHORA + timedelta(days=1))["recomendaciones"]]
    assert "R4" not in reglas


def test_descartar_la_saca_por_el_resto_del_mes(db):
    bid = _negocio(db, "Bar Tito", phone="099 123 456")
    crear_por_cobrar(db, client_id=bid, concepto="Saldo", monto_usd=800, vence="2026-09-01")
    ifn.corrida_diaria(db, AHORA)
    rec = ifn.estado_pantalla(db, ahora=AHORA)["recomendaciones"][0]
    assert ifn.descartar(db, rec["id"], AHORA) == (True, None)
    ifn.corrida_diaria(db, AHORA + timedelta(days=2))
    assert ifn.estado_pantalla(db, ahora=AHORA + timedelta(days=2))["recomendaciones"] == []
    ifn.corrida_diaria(db, datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc))
    assert ifn.estado_pantalla(db, ahora=datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc))["recomendaciones"]


# ── una vez por día ──────────────────────────────────────────────────────────

def _calculos(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM if_calculos").fetchone()[0]
    finally:
        conn.close()


def test_recalculo_diario_ya_corrio_hoy(db):
    assert ifn.ya_corrio_hoy(db, AHORA) is False
    assert ifn.corrida_diaria(db, AHORA)["calculo_id"]
    assert ifn.ya_corrio_hoy(db, AHORA) is True
    assert ifn.corrida_diaria(db, AHORA + timedelta(hours=5)) is None
    ifn.estado_pantalla(db, ahora=AHORA + timedelta(hours=6))
    ifn.estado_pantalla(db, ahora=AHORA + timedelta(hours=7))
    assert _calculos(db) == 1, "cargar la pantalla no recalcula"
    assert ifn.corrida_diaria(db, AHORA + timedelta(days=1)) is not None
    assert _calculos(db) == 2
    assert ifn.corrida_diaria(db, AHORA + timedelta(days=1), forzar=True) is not None
    assert _calculos(db) == 3


def test_el_dia_es_el_de_montevideo(db):
    # 01:00 UTC del 16 son las 22:00 del 15 en Montevideo.
    ifn.corrida_diaria(db, datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc))
    assert ifn.corrida_diaria(db, datetime(2026, 9, 16, 1, 0, tzinfo=timezone.utc)) is None
    assert ifn.corrida_diaria(db, datetime(2026, 9, 16, 4, 0, tzinfo=timezone.utc)) is not None


def test_el_hilo_no_arranca_en_los_tests():
    src = open(dashboard.__file__, encoding="utf-8").read()
    guarda = src.index('if os.environ.get("CRM_SIN_PROCESOS_DE_FONDO", "").lower() != "true":')
    assert guarda < src.index("start_inteligencia_fin(app)")


# ── los tres datos previos ───────────────────────────────────────────────────

def test_la_lista_de_motivos_es_cerrada(db):
    assert list(ifn.MOTIVOS) == ["precio", "se_enfrio", "eligio_otro", "no_era_momento", "no_calificaba"]
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO perdidas_motivo (entidad, entidad_id, motivo) VALUES ('lead', 1, 'caro')")
    conn.close()
    src = open(dashboard.__file__, encoding="utf-8").read()
    js = src[src.index("const IFN_MOTIVOS = "):]
    js = js[:js.index("\n")]
    assert [c for c in ifn.MOTIVOS] == [p.split("'")[1] for p in js.split("[")[2:]]


def test_canal_de_source():
    assert ifn.canal_de_source("meta") == "meta_ads"
    assert ifn.canal_de_source("discovery") == "outbound"
    assert ifn.canal_de_source(None, "https://maps.google.com/x") == "outbound"
    assert ifn.canal_de_source(None) is None
    assert ifn.canal_de_source("manual") is None
    assert ifn.canal_de_source("calendly_gcal") == "otro"


def test_perder_una_ficha_anota_la_fecha_sin_motivo(db):
    from services.notion_service import cliente_cambio_de_estado

    nc = upsert_notion_client(db, "pg-1", "Ficha", status="Demo Agendada")
    cliente_cambio_de_estado(db, "pg-1", "Demo Agendada", "Perdido")

    conn = sqlite3.connect(db)
    fila = conn.execute("SELECT entidad_id, motivo, perdida_en FROM perdidas_motivo").fetchone()
    conn.close()
    assert fila[0] == nc and fila[1] is None and fila[2]
    assert ifn.estado_datos(db, HOY)["motivo"]["sin_motivo"] == 1


def test_una_perdida_por_negocio(db):
    bid = _negocio(db, "Bar", "rechazo")
    nc = upsert_notion_client(db, "pg-2", "Ficha Bar", status="Perdido")
    vincular_notion_client(db, nc, bid)
    ifn.guardar_motivo(db, "lead", bid, "precio", "test", AHORA)
    grupos = ifn.perdidas(db)
    assert len(grupos) == 1 and grupos[0]["motivo"] == "precio"
    assert ifn.motivos_por_entidad(db, "notion_client") == {nc: "precio"}


# ── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _cliente_http(app, email, paneles=None):
    db = app.config["_DB"]
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if paneles is not None:
        conn = sqlite3.connect(db)
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?, ?)",
                           (f"rol-{email}", json.dumps(paneles))).lastrowid
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
        conn.commit()
        conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = email.split("@")[0]
    return c


def test_api_motivo_valida_la_lista_cerrada(app):
    db = app.config["_DB"]
    cli = _cliente_http(app, "vende@scalerics.com", ["notion_clients"])
    perdida = upsert_notion_client(db, "pg-a", "Perdida", status="Perdido")
    viva = upsert_notion_client(db, "pg-b", "Viva", status="Demo Agendada")

    assert cli.put(f"/api/perdidas/notion_client/{perdida}/motivo", json={"motivo": "caro"}).status_code == 400
    assert cli.put(f"/api/perdidas/notion_client/{viva}/motivo", json={"motivo": "precio"}).status_code == 404
    assert cli.put(f"/api/perdidas/cualquiera/{perdida}/motivo", json={"motivo": "precio"}).status_code == 400
    r = cli.put(f"/api/perdidas/notion_client/{perdida}/motivo", json={"motivo": "se_enfrio"})
    assert r.status_code == 200

    fichas = {c["id"]: c for c in cli.get("/api/notion-clients").get_json()["clientes"]}
    assert fichas[perdida]["motivo_perdida"] == "se_enfrio"
    assert fichas[viva]["motivo_perdida"] is None


def test_api_motivo_de_una_demo_que_no_cerro(app):
    db = app.config["_DB"]
    cli = _cliente_http(app, "demos@scalerics.com", ["notion_clients"])
    bid = _negocio(db, "Demo sin cierre", "presupuesto_enviado")
    conn = sqlite3.connect(db)
    demo = conn.execute("INSERT INTO demos_realizadas (client_id, fecha, origen, estado_planilla, mes_planilla) "
                        "VALUES (?, '2026-09-01', 'planilla', 'no_cerro', '2026-09')", (bid,)).lastrowid
    conn.commit()
    conn.close()
    assert cli.put(f"/api/perdidas/demo/{demo}/motivo", json={"motivo": "no_era_momento"}).status_code == 200
    demos = cli.get("/api/demos-realizadas").get_json()["demos"]
    assert demos[0]["motivo_perdida"] == "no_era_momento"


def test_api_esfuerzo_en_horas_y_en_dias(app):
    db = app.config["_DB"]
    cli = _cliente_http(app, "dev@scalerics.com", ["projects"])
    pid = upsert_project(db, "p-x", "Proyecto X", stage="Done")

    r = cli.put(f"/api/proyectos/{pid}/esfuerzo", json={"valor": 5, "unidad": "dias"})
    assert r.status_code == 200 and r.get_json()["horas"] == 40, "5 días × 8 h del equipo"
    r = cli.put(f"/api/proyectos/{pid}/esfuerzo", json={"valor": "32,5", "unidad": "horas"})
    assert r.status_code == 200 and r.get_json()["horas"] == 32.5
    assert cli.put(f"/api/proyectos/{pid}/esfuerzo", json={"valor": -3}).status_code == 400
    assert cli.put(f"/api/proyectos/{pid}/esfuerzo", json={"valor": 3, "unidad": "semanas"}).status_code == 400
    assert cli.put("/api/proyectos/999/esfuerzo", json={"valor": 3}).status_code == 404

    proyecto = cli.get("/api/projects").get_json()[0]
    assert proyecto["esfuerzo_horas"] == 32.5 and proyecto["esfuerzo_unidad"] == "horas"


def test_api_origen_manual_de_una_venta_sin_origen(app):
    db = app.config["_DB"]
    cli = _cliente_http(app, "socio@scalerics.com", ["inteligencia_fin"])
    bid = _venta(db, "Alta a mano", "2026-09-02", source="manual")
    sin_lead = upsert_notion_client(db, "pg-c", "Aceptada suelta", status="Presupuesto Aceptado")

    pendientes = ifn.estado_datos(db, HOY)["origen"]["pendientes"]
    assert {(p["entidad"], p["id"]) for p in pendientes} == {("business", bid), ("notion_client", sin_lead)}
    assert [p for p in pendientes if p["entidad"] == "notion_client"][0]["sin_lead"] is True

    assert cli.put(f"/api/ventas/business/{bid}/origen", json={"canal": "google"}).status_code == 400
    assert cli.put(f"/api/ventas/business/{bid}/origen", json={"canal": "referido"}).status_code == 200
    assert cli.put(f"/api/ventas/notion_client/{sin_lead}/origen", json={"canal": "otro"}).status_code == 200
    assert ifn.estado_datos(db, HOY)["origen"]["sin_origen"] == 0
    canales = {(v["entidad"], v["id"]): v["canal"] for v in ifn.ventas(db)}
    assert canales[("business", bid)] == "referido"
    assert canales[("notion_client", sin_lead)] == "otro"


def test_api_tomar_y_descartar(app):
    db = app.config["_DB"]
    cli = _cliente_http(app, "socio@scalerics.com", ["inteligencia_fin"])
    bid = _negocio(db, "Debe", phone="099 000 111")
    crear_por_cobrar(db, client_id=bid, concepto="Saldo", monto_usd=800, vence="2020-01-01")
    ifn.recalcular(db, AHORA)
    rec = ifn._q(db, "SELECT id FROM if_recomendaciones WHERE regla = 'R4'")[0]["id"]

    assert cli.post(f"/api/inteligencia-fin/recomendaciones/{rec}/tomar").status_code == 200
    assert cli.post(f"/api/inteligencia-fin/recomendaciones/{rec}/tomar").status_code == 409
    assert cli.post(f"/api/inteligencia-fin/recomendaciones/{rec}/descartar").status_code == 409
    assert cli.post("/api/inteligencia-fin/recomendaciones/999/descartar").status_code == 404


def test_permisos_403_sin_el_panel(app):
    db = app.config["_DB"]
    otro = _cliente_http(app, "contador@scalerics.com", ["finanzas", "simulador"])
    pid = upsert_project(db, "p-y", "Proyecto Y")
    for metodo, url, cuerpo in (
            ("get", "/api/inteligencia-fin", None),
            ("post", "/api/inteligencia-fin/recalcular", None),
            ("post", "/api/inteligencia-fin/recomendaciones/1/tomar", None),
            ("post", "/api/inteligencia-fin/recomendaciones/1/descartar", None),
            ("put", "/api/inteligencia-fin/supuestos", {"comision_cobro_pct": 5}),
            ("put", "/api/inteligencia-fin/fijos/1/canal", {"canal": "otro"}),
            ("put", "/api/ventas/business/1/origen", {"canal": "otro"}),
            ("put", "/api/perdidas/lead/1/motivo", {"motivo": "precio"}),
            ("put", f"/api/proyectos/{pid}/esfuerzo", {"valor": 3})):
        r = getattr(otro, metodo)(url, json=cuerpo) if cuerpo else getattr(otro, metodo)(url)
        assert r.status_code == 403, url


def test_recalcular_y_supuestos_solo_admin(app):
    socio = _cliente_http(app, "socio@scalerics.com", ["inteligencia_fin"])
    assert socio.get("/api/inteligencia-fin").status_code == 200
    assert socio.get("/api/inteligencia-fin").get_json()["es_admin"] is False
    assert socio.post("/api/inteligencia-fin/recalcular").status_code == 403
    assert socio.put("/api/inteligencia-fin/supuestos", json={"comision_cobro_pct": 5}).status_code == 403

    admin = _cliente_http(app, "raiz@scalerics.com")
    assert admin.get("/api/inteligencia-fin").get_json()["es_admin"] is True
    assert admin.post("/api/inteligencia-fin/recalcular").status_code == 200
    assert admin.put("/api/inteligencia-fin/supuestos", json={"comision_cobro_pct": 150}).status_code == 400
    r = admin.put("/api/inteligencia-fin/supuestos", json={"comision_cobro_pct": "4,5"})
    assert r.status_code == 200 and r.get_json()["supuestos"]["comision_cobro_pct"] == 4.5


def test_no_se_reparte_solo_a_los_roles(tmp_path):
    fuente = open(dashboard.__file__.replace("dashboard.py", "database.py"), encoding="utf-8").read()
    assert '_grant_panel_to_existing_roles(conn, "inteligencia_fin"' not in fuente
    ruta = str(tmp_path / "roles.db")
    init_db(ruta)
    conn = sqlite3.connect(ruta)
    conn.execute("INSERT INTO roles (name, panel_access) VALUES ('Rol de prueba IFN', ?)",
                 (json.dumps(["notion_clients", "finanzas", "simulador"]),))
    conn.commit()
    conn.close()
    init_db(ruta)
    conn = sqlite3.connect(ruta)
    paneles = [json.loads(f[0]) for f in conn.execute("SELECT panel_access FROM roles WHERE panel_access IS NOT NULL")]
    conn.close()
    assert paneles and all("inteligencia_fin" not in p for p in paneles if isinstance(p, list))
