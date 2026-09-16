"""Inteligencia financiera del lado del servidor: diagnóstico, sugerencias, IA y permisos.

Nada se carga a mano en estas pruebas salvo donde se dice: la pantalla tiene que
sacar conclusiones con lo que ya hay. Las fechas son fijas (AHORA) para que la
ventana de 3 meses y la medición a 30 días no dependan del reloj.
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
from services import inteligencia_fin_ia as ia
from services.equipo import dias_habiles
from services.finanzas import materializar_recurrentes

AHORA = datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)   # 12:00 en Montevideo
HOY = date(2026, 9, 15)
PREVIOS = ("2026-06", "2026-07", "2026-08")

_tel = iter(range(91000000, 91999999))


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "ifn.db")
    init_db(ruta)
    conn = sqlite3.connect(ruta)
    # Una sola persona de 8 h: septiembre de 2026 tiene 22 días hábiles, 176 h.
    conn.execute("UPDATE equipo_personas SET lleva_horas = 0")
    conn.execute("INSERT INTO equipo_personas (nombre, rol, lleva_horas, horas_por_dia, activo) "
                 "VALUES ('Dev de prueba', 'Dev', 1, 8, 1)")
    conn.commit()
    conn.close()
    return ruta


def _negocio(db, nombre, estado="cerrado", source=None, phone=None, scraped_at=None):
    bid = insert_business(db, {"name": nombre, "phone": phone, "source": source,
                               "scraped_at": scraped_at})
    update_business(db, bid, crm_status=estado)
    return bid


def _evento(db, lead_id, estado, fecha):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO lead_events (lead_id, new_status, note, created_at) VALUES (?, ?, '', ?)",
                 (lead_id, estado, f"{fecha} 10:00:00"))
    conn.commit()
    conn.close()


def _mov(db, tipo, monto, fecha, categoria="servicios", client_id=None):
    return crear_movimiento(db, tipo=tipo, fecha=fecha, periodo=fecha[:7], concepto=f"{tipo} {categoria}",
                            categoria=categoria, monto=monto, moneda="USD", monto_usd=monto,
                            client_id=client_id)


def _venta(db, nombre, fecha, source="meta", precio=None):
    bid = _negocio(db, nombre, "cerrado", source=source, phone=f"+598{next(_tel)}",
                   scraped_at=f"{fecha} 09:00:00")
    _evento(db, bid, "cerrado", fecha)
    if precio:
        _mov(db, "ingreso", precio, fecha, "desarrollo_web", client_id=bid)
    return bid


def _lead(db, nombre, fecha, estado):
    return _negocio(db, nombre, estado, source="meta", scraped_at=f"{fecha} 09:00:00")


def _pauta(db, montos):
    conn = sqlite3.connect(db)
    for i, (fecha, monto) in enumerate(montos):
        conn.execute("INSERT INTO meta_insights (date, campaign_id, spend, currency) VALUES (?, ?, ?, 'USD')",
                     (fecha, f"c{i}", monto))
    conn.commit()
    conn.close()


def _proyecto(db, nombre, business_id=None, horas=None, stage="Done",
              inicio="2026-07-01", fin="2026-07-14"):
    page = f"p-{nombre}"
    pid = upsert_project(db, page, nombre, stage=stage, timeline_start=inicio, timeline_end=fin)
    if business_id:
        nc = upsert_notion_client(db, f"nc-{nombre}", f"Ficha {nombre}", status="Presupuesto Aceptado",
                                  notion_project_page_id=page)
        vincular_notion_client(db, nc, business_id)
    if horas:
        _, error = ifn.guardar_esfuerzo(db, pid, {"valor": horas, "unidad": "horas"}, "test", AHORA)
        assert error is None
    return pid


def _diag(db):
    return {d["clave"]: d for d in ifn.diagnostico(ifn._contexto(db, HOY))}


def _reglas(db):
    return {r["regla"]: r for r in ifn.calcular(db, HOY)["recomendaciones"]}


def _sistema_basico(db):
    """Un mes cualquiera, sin nada cargado a mano en Inteligencia financiera."""
    for p in PREVIOS:
        _mov(db, "egreso", 1000, f"{p}-10")
    a = _venta(db, "Cliente A", "2026-07-05", "meta", precio=3000)
    _venta(db, "Cliente B", "2026-08-05", "discovery", precio=1500)
    _pauta(db, [("2026-06-15", 300), ("2026-07-15", 300), ("2026-08-15", 300)])
    for i in range(5):
        _lead(db, f"Lead {i}", "2026-07-10", "llamar_despues")
    crear_recurrente(db, tipo="egreso", concepto="Hosting", categoria="infraestructura",
                     monto=200, moneda="USD", desde="2026-01")
    crear_por_cobrar(db, client_id=a, concepto="Saldo", monto_usd=500, vence="2026-09-01")
    return a


# ── sin pedir datos ──────────────────────────────────────────────────────────

def test_sin_nada_cargado_a_mano_igual_hay_diagnostico_y_sugerencias(db):
    _sistema_basico(db)

    ifn.corrida_diaria(db, AHORA)
    estado = ifn.estado_pantalla(db, ahora=AHORA)

    assert 4 <= len(estado["diagnostico"]) <= 6
    assert estado["recomendaciones"]
    assert "datos" not in estado and "avisos" not in estado
    assert estado["resumen"]["texto"] and estado["resumen"]["origen"] == "deterministico"
    conn = sqlite3.connect(db)
    for tabla in ("perdidas_motivo", "proyectos_esfuerzo", "if_supuestos", "ventas_origen_manual"):
        assert conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] == 0, tabla
    conn.close()


def test_base_vacia_igual_abre_con_objetivo_y_alternativas(db):
    """Pedido de Juan (15/9): "no quiero que al entrar a inteligencia financiera
    me diga que con los datos proporcionados no hay analisis aun".

    Sin un solo movimiento cargado la pantalla abre igual: el objetivo del mes
    (en cero, pero editable) y una alternativa por palanca, cada una contando
    que haria y con que dato se enciende. Nunca un cartel de que no hay nada.
    """
    ifn.corrida_diaria(db, AHORA)
    estado = ifn.estado_pantalla(db, ahora=AHORA)

    # 475: lo que cobra el equipo, precargado con los numeros que dio Juan.
    # El objetivo abre con eso aunque no haya un solo movimiento cargado.
    assert estado["objetivo"]["total"] == 475
    assert [p["clave"] for p in estado["objetivo"]["partes"]] == ["fijos", "aportes", "sueldo"]

    recs = estado["recomendaciones"]
    assert len(recs) >= 3
    assert {r["palanca"] for r in recs} == set(ifn.PALANCAS)
    # Las de oportunidad no tienen impacto en plata, pero si su nota. No son
    # las unicas: la lista del equipo viene precargada, asi que el recorte de
    # marketing es una alternativa de verdad incluso con la base pelada.
    oportunidades = [r for r in recs if r["regla"] == "R0"]
    assert oportunidades
    assert all(r["nota"] and r["impacto_mensual"] is None for r in oportunidades)
    assert all(r["nota"] for r in recs)

    texto = json.dumps(estado, ensure_ascii=False, default=str).lower()
    for frase in ("sin datos", "no hay análisis", "datos insuficientes",
                  "sacar conclusiones", "falta cargar"):
        assert frase not in texto, frase


# ── diagnóstico ──────────────────────────────────────────────────────────────

def test_diagnostico_resultado_y_margen(db):
    for p in PREVIOS:
        _mov(db, "ingreso", 3000, f"{p}-05", "desarrollo_web")
        _mov(db, "egreso", 1000, f"{p}-06")
    _mov(db, "ingreso", 2000, "2026-09-05", "desarrollo_web")
    _mov(db, "egreso", 1500, "2026-09-06")

    d = _diag(db)["resultado"]

    assert d["valor"] == "USD 500"
    assert "quedan USD 500 (25 % de margen)" in d["texto"]
    assert "USD 2.000 por mes: vas por debajo" in d["texto"]
    assert "margen USD 500 ÷ USD 2.000 = 25 %" in d["calculo"]
    assert d["nivel"] == "atencion"


def _escenario_caja(db):
    _mov(db, "ingreso", 6000, "2026-01-10", "desarrollo_web")
    crear_recurrente(db, tipo="egreso", concepto="Infra", categoria="infraestructura",
                     monto=1000, moneda="USD", desde="2026-06")
    materializar_recurrentes(db, HOY)       # junio a septiembre: USD 4.000
    for p in PREVIOS:
        _mov(db, "egreso", 500, f"{p}-20")


def test_diagnostico_meses_de_caja(db):
    _escenario_caja(db)

    d = _diag(db)["caja"]

    # Caja = 6.000 − 4.000 de fijos − 1.500 de variables = 500; gasto del mes 1.500.
    assert d["valor"] == "0,3 meses"
    assert "Caja (Balance General) USD 500 ÷ (fijos USD 1.000 + variables promedio USD 500) = 0,3 meses" in d["calculo"]
    assert "Es poco" in d["texto"] and d["nivel"] == "mal"


def test_diagnostico_punto_de_equilibrio(db):
    cliente = _negocio(db, "Con mantenimiento")
    for p in PREVIOS:
        _mov(db, "ingreso", 3000, f"{p}-05", "desarrollo_web")
        _mov(db, "egreso", 300, f"{p}-06")
    crear_recurrente(db, tipo="egreso", concepto="Retiros", categoria="retiros",
                     monto=1000, moneda="USD", desde="2026-01")
    crear_recurrente(db, tipo="ingreso", concepto="Mant", categoria="mantenimiento",
                     monto=200, moneda="USD", desde="2026-01", client_id=cliente)

    d = _diag(db)["equilibrio"]

    # Variables 900 ÷ ingresos 9.000 = 10 %; 1.000 ÷ 0,9 = 1.111,11.
    assert d["valor"] == "USD 1.111,11"
    assert "ya cubre el 18 %" in d["texto"]
    assert d["nivel"] == "bien"


def test_diagnostico_costo_por_venta_contra_el_ticket(db):
    for fecha in ("2026-07-05", "2026-08-05", "2026-09-05"):
        _venta(db, f"Venta {fecha}", fecha, "meta", precio=2000)
    for i in range(3):
        _lead(db, f"Lead {i}", "2026-08-01", "interesado")
    _mov(db, "egreso", 3000, "2026-08-10")
    _pauta(db, [("2026-07-10", 300), ("2026-08-10", 300), ("2026-09-10", 300)])

    d = _diag(db)["pauta"]

    assert d["valor"] == "USD 300"
    assert "cada lead cuesta USD 150" in d["texto"] and "cada venta cuesta USD 300" in d["texto"]
    assert "ticket promedio de USD 2.000" in d["texto"]
    assert "Margen por venta = USD 2.000 × 50 % = USD 1.000" in d["calculo"]
    assert d["nivel"] == "bien"


def test_diagnostico_pauta_sin_ventas(db):
    _mov(db, "egreso", 100, "2026-08-10")
    _pauta(db, [("2026-07-10", 400)])
    d = _diag(db)["pauta"]
    assert d["nivel"] == "mal" and "ninguna venta" in d["texto"]


def test_diagnostico_la_caida_mas_grande_del_embudo(db):
    for i in range(3):
        _lead(db, f"No {i}", "2026-07-10", "no_interesa")
    _lead(db, "No atiende", "2026-07-10", "llamar_despues")
    for i in range(3):
        _lead(db, f"Agendó {i}", "2026-07-10", "demo_agendada")
    _lead(db, "Hizo la demo", "2026-07-10", "demo_1")
    _lead(db, "Compró 1", "2026-07-10", "cerrado")
    _lead(db, "Compró 2", "2026-07-10", "cerrado")

    d = _diag(db)["embudo"]

    # 6 de 10 agendan, 3 de 6 hacen la demo, 2 de 3 compran.
    assert d["valor"] == "2 de 10"
    assert "La caída más grande es de demo agendada a demo realizada: pasa el 50 %" in d["texto"]
    assert "No atiende: 1 · no le interesa: 3" in d["calculo"]


def test_diagnostico_concentracion(db):
    grande = _negocio(db, "Bar Grande", "interesado")
    chico = _negocio(db, "Kiosco", "interesado")
    _mov(db, "ingreso", 3000, "2026-07-05", "desarrollo_web", client_id=grande)
    _mov(db, "ingreso", 1000, "2026-08-05", "desarrollo_web", client_id=chico)

    d = _diag(db)["concentracion"]

    assert d["valor"] == "75 %" and "viene de Bar Grande" in d["texto"] and d["nivel"] == "mal"


def test_diagnostico_como_mucho_6_y_las_alertas_primero(db):
    _sistema_basico(db)
    lista = ifn.diagnostico(ifn._contexto(db, HOY))
    assert len(lista) == 6
    niveles = [d["nivel"] for d in lista]
    assert niveles == sorted(niveles, key=ifn.NIVELES.index)


# ── sugerencias ──────────────────────────────────────────────────────────────

def test_r1_sin_cuotas_cargadas_usa_el_simulador_y_lo_dice(db):
    for i in range(6):
        _negocio(db, f"Cliente {i}", "finalizado")

    r1 = _reglas(db)["R1"]

    assert r1["titulo"] == "Cobrales mantenimiento a los 6 clientes que ya tenés"
    assert r1["impacto_mensual"] == 570
    assert "Impacto = 6 × USD 100 × (1 − 5 %) = USD 570 por mes" in r1["calculo"]
    assert "Comisión de cobro 5 %: la que usa el Simulador" in r1["supuestos"]
    assert any("la cuota por defecto del Simulador" in s for s in r1["supuestos"])
    assert r1["confianza"] == "media"


def test_r1_con_cuotas_en_finanzas_la_confianza_es_alta(db):
    con = _negocio(db, "Paga")
    for i in range(6):
        _negocio(db, f"Cliente {i}")
    crear_recurrente(db, tipo="ingreso", concepto="Mant", categoria="mantenimiento",
                     monto=80, moneda="USD", desde="2026-01", client_id=con)
    r1 = _reglas(db)["R1"]
    assert r1["impacto_mensual"] == 456 and r1["confianza"] == "alta"
    assert r1["supuestos"] == ["Comisión de cobro 5 %: la que usa el Simulador"]


def test_r1_toma_la_cuota_de_los_presupuestos(db):
    bid = _negocio(db, "Cliente")
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO budgets (client_id, notes, total_amount) VALUES (?, ?, 1500)",
                 (bid, json.dumps({"monthly_price": 120})))
    conn.commit()
    conn.close()
    for i in range(1, 3):
        _negocio(db, f"Cliente {i}")
    r1 = _reglas(db)["R1"]
    assert "Cuota = USD 120 ÷ 1 = USD 120 (promedio del mantenimiento mensual de 1 presupuesto)" in r1["calculo"]


def test_los_valores_del_simulador_son_los_mismos(db):
    fuente = open(dashboard.__file__, encoding="utf-8").read()
    assert f"comisionCobro: {ifn.COMISION_SIMULADOR_PCT}" in fuente
    assert f"cuotaAltaNueva: {ifn.CUOTA_SIMULADOR_USD}" in fuente


def test_r2_subi_la_pauta_con_horas_estimadas(db):
    # Sin esfuerzo cargado: un timeline de 10 días hábiles × 8 h = 80 h por proyecto.
    _proyecto(db, "Web vieja")
    for fecha in ("2026-07-05", "2026-08-05", "2026-09-02"):
        _venta(db, f"Venta {fecha}", fecha, "meta", precio=2000)
    _mov(db, "egreso", 1000, "2026-08-10")
    _pauta(db, [("2026-06-15", 300), ("2026-07-15", 300), ("2026-08-15", 300)])

    recs = _reglas(db)

    r2 = recs["R2"]
    assert r2["titulo"] == "Subí la pauta de USD 300 a USD 600, no más"
    assert "176 h netas" in r2["calculo"] and "80 h por proyecto = 2 proyectos" in r2["calculo"]
    assert "capacidad libre = 2 − 1 = 1" in r2["calculo"]
    assert r2["confianza"] == "baja"
    assert "estimado: duración promedio de 1 timeline" in r2["supuestos"][0]
    assert "Pasarte de USD 600 tira USD 300" in r2["advertencia"]
    assert "R3" not in recs


def test_r3_la_pauta_que_no_rinde_se_baja(db):
    _pauta(db, [("2026-06-15", 300), ("2026-07-15", 300), ("2026-08-15", 300)])

    r3 = _reglas(db)["R3"]

    assert r3["titulo"] == "Bajá o pausá la pauta de Meta"
    assert r3["impacto_mensual"] == 300
    assert "Se pierde = pauta USD 300 − retorno USD 0 = USD 300 por mes" in r3["calculo"]
    assert r3["tipo"] == "recorte" and r3["confianza"] == "media"


def test_r3_con_ventas_que_no_cubren_la_pauta(db):
    _venta(db, "Única", "2026-07-05", "meta", precio=1000)
    _mov(db, "egreso", 800, "2026-07-10")
    _pauta(db, [("2026-06-15", 1000), ("2026-07-15", 1000), ("2026-08-15", 1000)])
    r3 = _reglas(db)["R3"]
    # Margen 20 % → 200 por venta; 1 venta en 3 meses vuelve 66,67 por mes.
    assert "Vuelve por mes: 0,3 ventas de Meta × USD 200 = USD 66,67" in r3["calculo"]
    assert r3["impacto_mensual"] == round(1000 - 200 / 3, 2)


def test_r4_cobros_vencidos_con_whatsapp(db):
    bid = _negocio(db, "Bar Tito", phone="099 123 456")
    crear_por_cobrar(db, client_id=bid, concepto="50% final", monto_usd=800, vence="2026-09-01")
    crear_por_cobrar(db, client_id=bid, concepto="No vence", monto_usd=500, vence="2026-10-01")
    r4 = _reglas(db)["R4"]
    assert r4["titulo"] == "Cobrá los USD 800 vencidos" and r4["unica_vez"] is True
    assert r4["acciones"][0]["wa"] == "59899123456"
    assert "01/09/2026" in r4["acciones"][0]["texto"]


def test_r5_gasto_fijo_nuevo_sin_asignar_canal(db):
    crear_recurrente(db, tipo="egreso", concepto="Zoho", categoria="herramientas",
                     monto=150, moneda="USD", desde="2026-08")
    crear_recurrente(db, tipo="egreso", concepto="BPS", categoria="impuestos",
                     monto=900, moneda="USD", desde="2026-08")

    recs = [r for r in ifn.calcular(db, HOY)["recomendaciones"] if r["regla"] == "R5"]

    assert [r["titulo"] for r in recs] == ["Probá un mes sin Zoho"], "los impuestos no se sugieren recortar"
    assert recs[0]["impacto_mensual"] == 150 and "es nuevo" in recs[0]["calculo"]
    assert recs[0]["advertencia"] == ifn.ADVERTENCIA_R5


def test_r6_revisa_el_precio_nunca_abandonar(db):
    bid = _venta(db, "Cliente", "2026-07-05", "discovery", precio=1000)
    _proyecto(db, "Landing", bid, horas=80)
    dias = len(dias_habiles(date(2026, 6, 1), date(2026, 8, 31)))
    _mov(db, "egreso", 12 * 8 * dias, "2026-07-10")     # costo por hora: USD 12

    r6 = _reglas(db)["R6"]

    assert r6["titulo"] == "Revisá el precio de Desarrollo web"
    assert r6["impacto_mensual"] is None and r6["confianza"] == "alta"
    assert "Margen = (USD 1.000 − USD 960) ÷ USD 1.000 = 4 %" in r6["calculo"]
    assert "nunca abandonar la línea" in r6["advertencia"]


def test_r7_donde_se_pierde_la_plata_en_el_embudo(db):
    for i in range(4):
        _lead(db, f"No atiende {i}", "2026-07-10", "llamar_despues")
    for i in range(2):
        _lead(db, f"Interesado {i}", "2026-07-10", "interesado")
    for i in range(2):
        _lead(db, f"Demo sin cierre {i}", "2026-07-10", "presupuesto_enviado")
    compro = _lead(db, "Compró", "2026-07-10", "cerrado")
    _mov(db, "ingreso", 2000, "2026-07-12", "desarrollo_web", client_id=compro)
    _mov(db, "egreso", 500, "2026-08-10")

    r7 = _reglas(db)["R7"]

    # Margen 75 % × ticket 2.000 = 1.500; 2 demos sin cierre ÷ 3 × 1.500 × 30 % = 300.
    assert r7["titulo"] == "Recuperá a los 2 que tuvieron demo y no cerraron"
    assert r7["impacto_mensual"] == 300 and r7["confianza"] == "baja"
    assert "30 % de recupero" in r7["calculo"]


def test_r7_aprovecha_los_motivos_si_alguien_los_cargo(db):
    for i in range(2):
        _lead(db, f"Demo sin cierre {i}", "2026-07-10", "presupuesto_enviado")
    compro = _lead(db, "Compró", "2026-07-10", "cerrado")
    _mov(db, "ingreso", 2000, "2026-07-12", "desarrollo_web", client_id=compro)
    nc = upsert_notion_client(db, "pg-p", "Perdida", status="Perdido")
    ifn.guardar_motivo(db, "notion_client", nc, "precio", "test", AHORA)
    assert "Motivos cargados a mano: precio 1" in _reglas(db)["R7"]["calculo"]


def test_r8_caja_corta(db):
    _escenario_caja(db)
    bid = _negocio(db, "Debe", "acepto")
    crear_por_cobrar(db, client_id=bid, concepto="Saldo", monto_usd=800, vence="2026-09-01")

    r8 = _reglas(db)["R8"]

    assert r8["titulo"] == "Tenés 0,3 meses de caja: frená Infra (USD 1.000 por mes) o cobrá los USD 800 vencidos"
    assert r8["impacto_mensual"] == 1000 and r8["tipo"] == "alerta" and r8["confianza"] == "alta"


def test_r9_concentracion_en_un_cliente(db):
    grande = _negocio(db, "Bar Grande", "interesado")
    _mov(db, "ingreso", 3000, "2026-07-05", "desarrollo_web", client_id=grande)
    _mov(db, "ingreso", 1000, "2026-08-05", "desarrollo_web")
    r9 = _reglas(db)["R9"]
    assert r9["titulo"] == "Sumá clientes: el 75 % de lo que entra viene de Bar Grande"
    assert r9["impacto_mensual"] is None and r9["tipo"] == "alerta"


def test_r10_demos_que_no_se_hacen(db):
    for i in range(5):
        _lead(db, f"Agendó {i}", "2026-07-10", "demo_agendada")
    for i in range(2):
        _lead(db, f"Hizo {i}", "2026-07-10", "demo_1")
    for i in range(2):
        bid = _lead(db, f"Compró {i}", "2026-07-10", "cerrado")
        _mov(db, "ingreso", 2000, "2026-07-12", "desarrollo_web", client_id=bid)
    _mov(db, "egreso", 1000, "2026-08-10")

    r10 = _reglas(db)["R10"]

    # 9 agendadas, 4 hechas, se caen 5; cierre 50 %; margen 75 % × 2.000 = 1.500.
    assert r10["titulo"] == "Confirmá las demos: se caen 5 de 9"
    assert r10["impacto_mensual"] == 1250


# ── reglas transversales ─────────────────────────────────────────────────────

def test_confianza_siempre_presente_y_supuestos_a_la_vista(db):
    _sistema_basico(db)
    recs = ifn.calcular(db, HOY)["recomendaciones"]
    assert len(recs) >= 4
    assert all(r["confianza"] in ("alta", "media", "baja") for r in recs)
    ifn.corrida_diaria(db, AHORA)
    pantalla = ifn.estado_pantalla(db, ahora=AHORA)["recomendaciones"]
    assert all(r["confianza"] in ("alta", "media", "baja") for r in pantalla)
    r1 = next(r for r in pantalla if r["regla"] == "R1")
    assert "Comisión de cobro 5 %: la que usa el Simulador" in r1["supuestos"]


def test_cada_calculo_muestra_el_numero_que_da(db):
    _sistema_basico(db)
    for r in ifn.calcular(db, HOY)["recomendaciones"]:
        if r["impacto_mensual"] is not None:
            assert ifn.usd(r["impacto_mensual"]) in r["calculo"], r["regla"]


def _falsa(regla, impacto):
    return {"regla": regla, "clave": str(impacto), "impacto_mensual": impacto}


def test_umbral_de_100_por_mes():
    visibles = ifn.ordenar([_falsa("R1", 99.99), _falsa("R4", 100), _falsa("R9", None), _falsa("R5", 40)])
    assert [r["impacto_mensual"] for r in visibles] == [100, None]


def test_maximo_8_ordenadas_por_impacto():
    """Ocho y no seis: el menú muestra al menos una alternativa por palanca (son
    cinco) y tiene que quedar lugar para las reglas con números reales."""
    visibles = ifn.ordenar([_falsa("R1", m) for m in (150, 900, None, 300, 5000, 120, 700, 101)])
    assert [r["impacto_mensual"] for r in visibles] == [5000, 900, 700, 300, 150, 120, 101, None]


def test_una_alternativa_negativa_no_se_filtra():
    """El umbral mira el VALOR ABSOLUTO.

    Con el filtro viejo (`>= 100`) una alternativa que RESTA USD 700 —no darle
    trabajo a alguien— desaparecía justo cuando más hay que verla. La que resta
    menos que el umbral sigue sin aparecer, igual que las que suman poco.
    """
    visibles = ifn.ordenar([_falsa("R1", 300), _falsa("R12", -700), _falsa("R1", -50)])
    assert [r["impacto_mensual"] for r in visibles] == [300, -700]


# ── resumen con IA ───────────────────────────────────────────────────────────

def test_el_resumen_con_ia_usa_solo_numeros_del_json(db):
    _sistema_basico(db)
    vistos = []

    def fake(payload):
        vistos.append(payload)
        d = payload["diagnostico"][0]
        return f"{d['titulo']}: {d['valor']}. Lo primero: {payload['sugerencias'][0]['titulo']}."

    ifn.corrida_diaria(db, AHORA, redactar=fake)
    resumen = ifn.estado_pantalla(db, ahora=AHORA)["resumen"]

    assert resumen["origen"] == "ia" and resumen["texto"].startswith(vistos[0]["diagnostico"][0]["titulo"])
    enviado = json.dumps(vistos[0], ensure_ascii=False)
    assert set(vistos[0]) == {"diagnostico", "sugerencias"}
    assert "acciones" not in enviado and "wa.me" not in enviado and "5989" not in enviado


def test_el_resumen_que_inventa_un_numero_se_descarta(db):
    _sistema_basico(db)
    texto, origen = ia.redactar(ifn.diagnostico(ifn._contexto(db, HOY)),
                                ifn.calcular(db, HOY)["recomendaciones"],
                                llamar=lambda p: "Vas a ganar USD 987.654 este mes.")
    assert origen == "deterministico" and "987" not in texto


def test_si_la_ia_falla_queda_el_texto_deterministico(db):
    _sistema_basico(db)

    def rota(payload):
        raise RuntimeError("sin red")

    diag = ifn.diagnostico(ifn._contexto(db, HOY))
    texto, origen = ia.redactar(diag, [], llamar=rota)
    assert origen == "deterministico" and texto == ia.texto_deterministico(diag, [])


def test_sin_bandera_o_sin_clave_no_se_llama_a_nadie(db, monkeypatch):
    _sistema_basico(db)
    diag = ifn.diagnostico(ifn._contexto(db, HOY))

    def prohibido(payload):
        raise AssertionError("no tenía que llamar a la API")

    monkeypatch.setattr(ia, "_llamar_a_la_api", prohibido)
    monkeypatch.delenv("RADIOGRAFIA_IA_ACTIVA", raising=False)
    assert ia.redactar(diag, [])[1] == "deterministico"
    monkeypatch.setenv("RADIOGRAFIA_IA_ACTIVA", "true")
    assert ia.redactar(diag, [])[1] == "deterministico", "sin ANTHROPIC_API_KEY"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(ia, "_llamar_a_la_api", lambda p: p["diagnostico"][0]["texto"])
    assert ia.redactar(diag, [])[1] == "ia"


# ── Lo voy a hacer y la medición a 30 días ───────────────────────────────────

def _r4_tomada(db):
    bid = _negocio(db, "Bar Tito", "acepto", phone="099 123 456")
    pc = crear_por_cobrar(db, client_id=bid, concepto="50% final", monto_usd=800, vence="2026-09-01")
    ifn.corrida_diaria(db, AHORA)
    rec = next(r for r in ifn.estado_pantalla(db, ahora=AHORA)["recomendaciones"] if r["regla"] == "R4")
    assert ifn.tomar(db, rec["id"], "Juan", AHORA) == (True, None)
    return pc, rec


def test_lo_voy_a_hacer_y_a_los_30_dias_se_mide(db):
    from services.finanzas import saldar_por_cobrar

    pc, rec = _r4_tomada(db)
    seg = ifn.estado_pantalla(db, ahora=AHORA)["seguimiento"][0]
    assert seg["resultado"] == "midiendo" and seg["se_mide_el"] == "15/10/2026"

    ifn.corrida_diaria(db, AHORA + timedelta(days=29))
    assert ifn.estado_pantalla(db, ahora=AHORA + timedelta(days=29))["seguimiento"][0]["resultado"] == "midiendo"

    saldar_por_cobrar(db, pc, fecha="2026-09-20")
    ifn.corrida_diaria(db, AHORA + timedelta(days=31))
    seg = ifn.estado_pantalla(db, ahora=AHORA + timedelta(days=31))["seguimiento"][0]
    assert seg["resultado"] == "funciono" and seg["impacto_real"] == 800


def test_a_los_30_dias_sin_cobrar_no_funciono(db):
    _r4_tomada(db)
    ifn.corrida_diaria(db, AHORA + timedelta(days=31))
    seg = ifn.estado_pantalla(db, ahora=AHORA + timedelta(days=31))["seguimiento"][0]
    assert seg["resultado"] == "no_funciono"


def test_la_pauta_bajada_se_mide_con_lo_que_se_gasto(db):
    _pauta(db, [("2026-06-15", 300), ("2026-07-15", 300), ("2026-08-15", 300)])
    ifn.corrida_diaria(db, AHORA)
    rec = next(r for r in ifn.estado_pantalla(db, ahora=AHORA)["recomendaciones"] if r["regla"] == "R3")
    ifn.tomar(db, rec["id"], "Juan", AHORA)
    _pauta(db, [("2026-09-25", 50)])
    ifn.corrida_diaria(db, AHORA + timedelta(days=31))
    seg = ifn.estado_pantalla(db, ahora=AHORA + timedelta(days=31))["seguimiento"][0]
    assert seg["impacto_real"] == 250 and seg["resultado"] == "funciono"


# ── una vez por día ──────────────────────────────────────────────────────────

def test_recalculo_diario_ya_corrio_hoy(db):
    assert ifn.corrida_diaria(db, AHORA)["calculo_id"]
    assert ifn.corrida_diaria(db, AHORA + timedelta(hours=5)) is None
    ifn.estado_pantalla(db, ahora=AHORA + timedelta(hours=6))
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM if_calculos").fetchone()[0] == 1
    conn.close()
    assert ifn.corrida_diaria(db, AHORA + timedelta(days=1)) is not None


def test_la_tabla_vieja_acepta_las_reglas_nuevas(tmp_path):
    ruta = str(tmp_path / "vieja.db")
    conn = sqlite3.connect(ruta)
    conn.execute("CREATE TABLE if_calculos (id INTEGER PRIMARY KEY AUTOINCREMENT, generada_en TEXT NOT NULL, "
                 "avisos TEXT NOT NULL DEFAULT '[]', encabezado TEXT NOT NULL DEFAULT '{}', "
                 "contexto TEXT NOT NULL DEFAULT '{}')")
    conn.execute("""CREATE TABLE if_recomendaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT, calculo_id INTEGER REFERENCES if_calculos(id),
        regla TEXT NOT NULL CHECK (regla IN ('R1','R2','R3','R4','R5','R6','R7')),
        clave TEXT NOT NULL DEFAULT '', titulo TEXT NOT NULL, detalle TEXT NOT NULL, calculo TEXT NOT NULL,
        impacto_mensual REAL, unica_vez INTEGER NOT NULL DEFAULT 0,
        confianza TEXT NOT NULL CHECK (confianza IN ('alta', 'media', 'baja')),
        tipo TEXT NOT NULL CHECK (tipo IN ('ingreso', 'recorte', 'alerta')),
        advertencia TEXT NOT NULL DEFAULT '', acciones TEXT NOT NULL DEFAULT '[]',
        metrica TEXT NOT NULL DEFAULT '{}', generada_en TEXT NOT NULL,
        estado TEXT NOT NULL DEFAULT 'nueva' CHECK (estado IN ('nueva', 'tomada', 'descartada')),
        descartada_en TEXT)""")
    conn.execute("INSERT INTO if_recomendaciones (id, regla, titulo, detalle, calculo, confianza, tipo, generada_en) "
                 "VALUES (7, 'R4', 'Cobrá', 'x', 'x', 'alta', 'ingreso', '2026-09-01 10:00:00')")
    conn.commit()
    conn.close()

    init_db(ruta)
    init_db(ruta)

    conn = sqlite3.connect(ruta)
    assert conn.execute("SELECT regla, titulo FROM if_recomendaciones WHERE id = 7").fetchone() == ("R4", "Cobrá")
    conn.execute("INSERT INTO if_recomendaciones (regla, titulo, detalle, calculo, confianza, tipo, generada_en) "
                 "VALUES ('R10', 't', 'd', 'c', 'media', 'ingreso', '2026-09-15 10:00:00')")
    tomadas = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'if_recomendaciones_tomadas'").fetchone()[0]
    assert "REFERENCES if_recomendaciones(id)" in tomadas
    conn.close()


# ── API y permisos ───────────────────────────────────────────────────────────

@pytest.fixture
def app(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _cliente_http(app, email, rol_id=None, paneles=None):
    db = app.config["_DB"]
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    conn = sqlite3.connect(db)
    if paneles is not None:
        rol_id = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?, ?)",
                              (f"rol-{email}", json.dumps(paneles))).lastrowid
    if rol_id:
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (rol_id, uid))
    conn.commit()
    conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = email.split("@")[0]
    return c


def test_el_contador_con_el_panel_la_puede_ver(app):
    db = app.config["_DB"]
    conn = sqlite3.connect(db)
    rol = conn.execute("SELECT id, panel_access, paneles_solo_lectura FROM roles WHERE name = 'Contador'").fetchone()
    conn.execute("UPDATE roles SET panel_access = ? WHERE id = ?",
                 (json.dumps(json.loads(rol[1]) + ["finanzas", "inteligencia_fin"]), rol[0]))
    conn.commit()
    conn.close()
    contador = _cliente_http(app, "contador@scalerics.com", rol_id=rol[0])
    r = contador.get("/api/inteligencia-fin")
    assert r.status_code == 200 and "diagnostico" in r.get_json()


def test_sin_el_panel_da_403(app):
    otro = _cliente_http(app, "ventas@scalerics.com", paneles=["finanzas", "simulador"])
    for metodo, url in (("get", "/api/inteligencia-fin"), ("post", "/api/inteligencia-fin/recalcular"),
                        ("post", "/api/inteligencia-fin/recomendaciones/1/tomar")):
        assert getattr(otro, metodo)(url).status_code == 403, url


def test_recalcular_solo_admin(app):
    socio = _cliente_http(app, "socio@scalerics.com", paneles=["inteligencia_fin"])
    assert socio.post("/api/inteligencia-fin/recalcular").status_code == 403
    admin = _cliente_http(app, "raiz@scalerics.com")
    assert admin.post("/api/inteligencia-fin/recalcular").status_code == 200


def test_afinar_sigue_siendo_opcional_y_se_guarda(app):
    db = app.config["_DB"]
    cli = _cliente_http(app, "equipo@scalerics.com", paneles=["projects", "notion_clients"])
    pid = upsert_project(db, "p-x", "Proyecto X", stage="Done")
    assert cli.put(f"/api/proyectos/{pid}/esfuerzo", json={"valor": 5, "unidad": "dias"}).get_json()["horas"] == 40
    nc = upsert_notion_client(db, "pg-a", "Perdida", status="Perdido")
    assert cli.put(f"/api/perdidas/notion_client/{nc}/motivo", json={"motivo": "caro"}).status_code == 400
    assert cli.put(f"/api/perdidas/notion_client/{nc}/motivo", json={"motivo": "precio"}).status_code == 200


def test_no_se_reparte_solo_a_los_roles():
    fuente = open(dashboard.__file__.replace("dashboard.py", "database.py"), encoding="utf-8").read()
    assert '_grant_panel_to_existing_roles(conn, "inteligencia_fin"' not in fuente
