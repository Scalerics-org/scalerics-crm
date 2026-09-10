"""Un gasto fijo facturado tiene que descontar IVA como cualquier otro.

El hosting, las herramientas y el contador vienen con factura todos los meses,
pero los fijos se materializaban sin impuesto: el IVA de lo recurrente —que es
lo más previsible que hay— no entraba en el saldo.

**El cambio afecta de acá en adelante, no hacia atrás.** Materializar es
idempotente por el índice `(recurrente_id, periodo)`: si el movimiento del mes
ya existe, no se toca. Eso ya era así para el monto —cambiarle el precio a un
fijo no reescribe los meses ya generados— y el IVA sigue la misma regla. Si hay
que corregir el mes en curso, se edita el movimiento, que para eso está abierto.

Es deliberado: reescribir hacia atrás tocaría meses cerrados, que es justo lo
que el candado existe para impedir.
"""

from datetime import date

import pytest

from database import (crear_recurrente, actualizar_recurrente, init_db,
                      listar_movimientos)
from services.finanzas import IVA_TASA, materializar_recurrentes


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "f.db")
    init_db(ruta)
    return ruta


HOY = date(2026, 9, 15)


def _fijo(db, concepto="Hosting", monto=100, facturado=False, desde="2026-09",
          moneda="USD", tipo_cambio=None):
    return crear_recurrente(db, tipo="egreso", concepto=concepto,
                            categoria="infraestructura", monto=monto,
                            moneda=moneda, tipo_cambio=tipo_cambio,
                            dia_del_mes=1, desde=desde,
                            facturado=1 if facturado else 0)


def _movs(db):
    return listar_movimientos(db)


# ── lo básico ────────────────────────────────────────────────────────────────

def test_un_fijo_facturado_materializa_con_iva(db):
    _fijo(db, monto=100, facturado=True)

    materializar_recurrentes(db, hoy=HOY)

    m = _movs(db)[0]
    assert m["facturado"] == 1
    assert round(m["iva_usd"], 2) == 22.0
    assert m["monto_usd"] == 100, "el monto sigue siendo el líquido"


def test_un_fijo_sin_factura_no_lleva_iva(db):
    _fijo(db, monto=100, facturado=False)

    materializar_recurrentes(db, hoy=HOY)

    m = _movs(db)[0]
    assert m["facturado"] == 0
    assert m["iva_usd"] == 0


def test_los_fijos_que_ya_existian_arrancan_sin_iva(db):
    """La columna es nueva: suponer que los viejos llevaban factura inventaría
    un impuesto que nunca se descontó."""
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO finanzas_recurrentes (tipo, concepto, categoria, monto, "
        "moneda, dia_del_mes, desde) VALUES ('egreso','Viejo','otros',100,'USD',1,'2026-09')")
    conn.commit()
    conn.close()

    materializar_recurrentes(db, hoy=HOY)

    assert _movs(db)[0]["facturado"] == 0


def test_el_iva_se_calcula_sobre_el_monto_en_dolares(db):
    """Un fijo en pesos descuenta el IVA de su equivalente en USD, igual que
    el resto del módulo."""
    _fijo(db, monto=4000, facturado=True, moneda="UYU", tipo_cambio=40)

    materializar_recurrentes(db, hoy=HOY)

    m = _movs(db)[0]
    assert m["monto_usd"] == 100
    assert round(m["iva_usd"], 2) == 22.0


def test_la_tasa_es_la_misma_que_para_los_movimientos(db):
    assert IVA_TASA == 0.22


# ── varios meses ─────────────────────────────────────────────────────────────

def test_cada_mes_generado_lleva_su_iva(db):
    _fijo(db, monto=100, facturado=True, desde="2026-07")

    materializar_recurrentes(db, hoy=HOY)

    movs = _movs(db)
    assert len(movs) == 3, "julio, agosto y septiembre"
    assert all(m["facturado"] == 1 for m in movs)
    assert all(round(m["iva_usd"], 2) == 22.0 for m in movs)


def test_materializar_dos_veces_no_duplica_ni_cambia_el_iva(db):
    """La guarda del módulo sigue intacta."""
    _fijo(db, monto=100, facturado=True)

    materializar_recurrentes(db, hoy=HOY)
    materializar_recurrentes(db, hoy=HOY)
    materializar_recurrentes(db, hoy=HOY)

    movs = _movs(db)
    assert len(movs) == 1
    assert round(movs[0]["iva_usd"], 2) == 22.0


# ── cambiar el fijo no reescribe lo ya generado ──────────────────────────────

def test_prender_el_iva_no_toca_los_meses_ya_generados(db):
    """Igual que el monto: materializar no reescribe hacia atrás, porque eso
    tocaría meses cerrados."""
    rid = _fijo(db, monto=100, facturado=False)
    materializar_recurrentes(db, hoy=HOY)

    actualizar_recurrente(db, rid, facturado=1)
    materializar_recurrentes(db, hoy=HOY)

    m = _movs(db)[0]
    assert m["facturado"] == 0, "el mes ya generado queda como estaba"
    assert m["iva_usd"] == 0


def test_prender_el_iva_si_aplica_al_mes_siguiente(db):
    rid = _fijo(db, monto=100, facturado=False)
    materializar_recurrentes(db, hoy=HOY)
    actualizar_recurrente(db, rid, facturado=1)

    materializar_recurrentes(db, hoy=date(2026, 10, 15))

    por_periodo = {m["periodo"]: m for m in _movs(db)}
    assert por_periodo["2026-09"]["iva_usd"] == 0
    assert round(por_periodo["2026-10"]["iva_usd"], 2) == 22.0


def test_apagar_el_iva_tampoco_reescribe_hacia_atras(db):
    rid = _fijo(db, monto=100, facturado=True)
    materializar_recurrentes(db, hoy=HOY)

    actualizar_recurrente(db, rid, facturado=0)
    materializar_recurrentes(db, hoy=HOY)

    assert round(_movs(db)[0]["iva_usd"], 2) == 22.0


# ── un fijo roto no tumba a los demás ────────────────────────────────────────

def test_un_fijo_en_pesos_sin_tipo_de_cambio_se_saltea_y_los_otros_pasan(db):
    """Ya era así; el IVA no puede cambiarlo."""
    crear_recurrente(db, tipo="egreso", concepto="Roto", categoria="otros",
                     monto=100, moneda="UYU", tipo_cambio=None, dia_del_mes=1,
                     desde="2026-09", facturado=1)
    _fijo(db, concepto="Bueno", monto=100, facturado=True)

    materializar_recurrentes(db, hoy=HOY)

    movs = _movs(db)
    assert [m["concepto"] for m in movs] == ["Bueno"]
    assert round(movs[0]["iva_usd"], 2) == 22.0


# ── la migracion sobre una base que ya existe ────────────────────────────────
# Es el camino real del deploy: producción no se crea de cero, se le agrega la
# columna a una tabla que ya tiene fijos cargados. `CREATE TABLE IF NOT EXISTS`
# no la agrega —la tabla ya está— así que si `_add_column` no corriera, esto
# reventaría recién en producción y no en ningún test.

def test_la_columna_se_le_agrega_a_una_base_que_ya_tenia_fijos(tmp_path):
    import sqlite3

    ruta = str(tmp_path / "vieja.db")
    conn = sqlite3.connect(ruta)
    conn.execute("""
        CREATE TABLE finanzas_recurrentes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo        TEXT NOT NULL,
            concepto    TEXT NOT NULL,
            categoria   TEXT NOT NULL,
            monto       REAL NOT NULL,
            moneda      TEXT NOT NULL,
            tipo_cambio REAL,
            dia_del_mes INTEGER NOT NULL DEFAULT 1,
            desde       TEXT NOT NULL,
            hasta       TEXT,
            activo      INTEGER NOT NULL DEFAULT 1,
            client_id   INTEGER,
            notas       TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "INSERT INTO finanzas_recurrentes (tipo, concepto, categoria, monto, "
        "moneda, dia_del_mes, desde) VALUES ('egreso','Fly','infraestructura',25,'USD',1,'2026-09')")
    conn.commit()
    conn.close()

    init_db(ruta)  # la migración

    conn = sqlite3.connect(ruta)
    columnas = [c[1] for c in conn.execute("PRAGMA table_info(finanzas_recurrentes)")]
    conn.close()
    assert "facturado" in columnas

    materializar_recurrentes(ruta, hoy=HOY)
    m = listar_movimientos(ruta)[0]
    assert m["concepto"] == "Fly"
    assert m["facturado"] == 0, "el fijo que ya existía no inventa IVA"


def test_correr_la_migracion_dos_veces_no_rompe(tmp_path):
    """Cada deploy vuelve a llamar a `init_db`."""
    ruta = str(tmp_path / "f.db")
    init_db(ruta)
    init_db(ruta)
    init_db(ruta)

    rid = crear_recurrente(ruta, tipo="egreso", concepto="Fly",
                           categoria="infraestructura", monto=100, moneda="USD",
                           dia_del_mes=1, desde="2026-09", facturado=1)
    materializar_recurrentes(ruta, hoy=HOY)

    assert round(listar_movimientos(ruta)[0]["iva_usd"], 2) == 22.0
    assert rid
