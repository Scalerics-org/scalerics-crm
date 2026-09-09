"""Las dos tablas de finanzas y sus accesores.

El test que más importa acá es el del índice único: es lo único que impide
que cada deploy —que reinicia la máquina y vuelve a materializar los fijos—
duplique los gastos del mes.
"""

import sqlite3

import pytest

from database import (actualizar_movimiento, borrar_movimiento, crear_movimiento,
                      crear_recurrente, get_movimiento, get_recurrente, init_db,
                      listar_movimientos, listar_recurrentes)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "f.db")
    init_db(ruta)
    return ruta


def _mov(db, **extra):
    campos = dict(tipo="egreso", fecha="2026-09-20", periodo="2026-09",
                  concepto="Fly", categoria="infraestructura",
                  monto=4.18, moneda="USD", monto_usd=4.18)
    campos.update(extra)
    return crear_movimiento(db, **campos)


def test_crear_y_leer_un_movimiento(db):
    mid = _mov(db)
    fila = get_movimiento(db, mid)
    assert fila["concepto"] == "Fly"
    assert fila["monto_usd"] == 4.18
    assert fila["anulado"] == 0


def test_un_fijo_no_puede_generar_dos_veces_el_mismo_mes(db):
    rid = crear_recurrente(db, tipo="egreso", concepto="Fly",
                           categoria="infraestructura", monto=4.18,
                           moneda="USD", dia_del_mes=1, desde="2026-09")
    _mov(db, recurrente_id=rid, periodo="2026-09")
    with pytest.raises(sqlite3.IntegrityError):
        _mov(db, recurrente_id=rid, periodo="2026-09")


def test_dos_movimientos_a_mano_del_mismo_mes_conviven(db):
    # recurrente_id NULL: el índice es parcial y no los alcanza.
    _mov(db, concepto="Dominio")
    _mov(db, concepto="Zoho")
    assert len(listar_movimientos(db)) == 2


def test_listar_filtra_por_periodo_y_tipo(db):
    _mov(db, periodo="2026-08", fecha="2026-08-20")
    _mov(db, periodo="2026-09", tipo="ingreso", categoria="desarrollo_web")
    assert len(listar_movimientos(db, desde="2026-09", hasta="2026-09")) == 1
    assert len(listar_movimientos(db, tipo="ingreso")) == 1


def test_los_anulados_no_aparecen_salvo_que_se_pidan(db):
    mid = _mov(db)
    actualizar_movimiento(db, mid, anulado=1)
    assert listar_movimientos(db) == []
    assert len(listar_movimientos(db, incluir_anulados=True)) == 1


def test_borrar_un_movimiento_lo_saca(db):
    mid = _mov(db)
    borrar_movimiento(db, mid)
    assert get_movimiento(db, mid) is None


def test_listar_recurrentes_solo_activos(db):
    crear_recurrente(db, tipo="egreso", concepto="Fly",
                     categoria="infraestructura", monto=4.18,
                     moneda="USD", dia_del_mes=1, desde="2026-09")
    crear_recurrente(db, tipo="egreso", concepto="Viejo",
                     categoria="herramientas", monto=10, moneda="USD",
                     dia_del_mes=1, desde="2026-01", activo=0)
    assert len(listar_recurrentes(db)) == 2
    assert len(listar_recurrentes(db, solo_activos=True)) == 1


def test_el_panel_finanzas_no_le_llega_solo_a_los_roles_que_ya_existian(tmp_path):
    """Ruling R20, a propósito: a diferencia de todos los demás paneles
    nuevos, Finanzas NO usa _grant_panel_to_existing_roles. Es el único panel
    que muestra la plata de la empresa, así que arranca sin nadie asignado
    -ni siquiera un rol que ya tenía otros paneles- en vez de dárselo a todos
    los roles de producción, Caller incluido. Un admin lo ve igual por el
    bypass de is_admin en tiene_panel(); el resto lo asigna Juan a mano."""
    ruta = str(tmp_path / "vieja.db")
    init_db(ruta)  # siembra Admin/Caller/Ventas
    conn = sqlite3.connect(ruta)
    conn.execute("UPDATE roles SET panel_access = ? WHERE name = 'Ventas'",
                 ('["cola","clientes"]',))
    conn.commit()
    conn.close()

    init_db(ruta)  # segundo arranque: acá NO tiene que entrar ningún grant

    conn = sqlite3.connect(ruta)
    fila = conn.execute("SELECT panel_access FROM roles WHERE name='Ventas'").fetchone()
    conn.close()
    assert "finanzas" not in fila[0]


def test_crear_movimiento_con_campo_mal_escrito_levanta_valueerror(db):
    # "nota" en vez de "notas": antes se ignoraba en silencio.
    with pytest.raises(ValueError):
        _mov(db, nota="tipeo")


def test_crear_recurrente_con_campo_inexistente_levanta_valueerror(db):
    with pytest.raises(ValueError):
        crear_recurrente(db, tipo="egreso", concepto="Fly",
                         categoria="infraestructura", monto=4.18,
                         moneda="USD", desde="2026-09", cliente_id=1)


def test_crear_solo_con_obligatorios_no_levanta_nada(db):
    mid = crear_movimiento(db, tipo="egreso", fecha="2026-09-20",
                           periodo="2026-09", concepto="Fly",
                           categoria="infraestructura", monto=4.18,
                           moneda="USD", monto_usd=4.18)
    assert get_movimiento(db, mid) is not None

    rid = crear_recurrente(db, tipo="egreso", concepto="Fly",
                           categoria="infraestructura", monto=4.18,
                           moneda="USD", desde="2026-09")
    assert get_recurrente(db, rid) is not None
