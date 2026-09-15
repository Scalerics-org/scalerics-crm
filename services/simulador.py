"""Lo que el simulador financiero lee del sistema, y lo que valida al guardar.

La cuenta del simulador NO vive acá: está en el navegador (`simCalcular` en
dashboard.py) porque recalcula con cada tecla, y se testea en node. Este módulo
arma la copia inicial desde Finanzas y los clientes, y valida los escenarios
que se guardan.

Nunca escribe en las tablas de Finanzas: solo llama funciones de lectura.
Tampoco materializa los fijos: lee las plantillas de `finanzas_recurrentes`, no
los movimientos del mes.

Moneda: todo en USD. Un fijo en pesos se convierte con `a_usd` y el tipo de
cambio cargado en el propio fijo, que es el mismo criterio que usan
`GET /api/finanzas/recurrentes` y `materializar_recurrentes`. Los pendientes
por cobrar ya se guardan en USD (`monto_usd`).
"""

import json
from datetime import date

from database import (listar_clientes_activos, listar_movimientos,
                      listar_por_cobrar, listar_recurrentes)
# `_totales` es privado, pero se importa a propósito: la caja actual tiene que
# sumar exactamente igual que los KPIs de Finanzas (monto_usd líquido, sin los
# anulados). Una suma escrita de nuevo acá daría otro número el día que alguien
# cambie el criterio allá.
from services.finanzas import _totales, a_usd

# Un escenario son unas decenas de campos y tres listas cortas. 200 KB es mucho
# más de lo que ocupa uno real y frena a quien mande cualquier cosa.
TAMANIO_MAXIMO = 200_000
LARGO_NOMBRE = 80

NOTA_SIN_TIPO_DE_CAMBIO = ("en pesos sin tipo de cambio en Finanzas: "
                           "cargá el monto en dólares")


def _mes(hoy: date) -> str:
    return f"{hoy.year:04d}-{hoy.month:02d}"


def gastos_desde_fijos(db_path: str, hoy: date | None = None) -> list[dict]:
    """Los gastos fijos activos de Finanzas, en USD, todos prendidos.

    Es la fuente principal: Juan carga ahí sus fijos reales. Solo entran los
    egresos (un ingreso fijo no es un gasto), los activos, y los que no
    terminaron: un fijo con `hasta` anterior a este mes ya no se paga.

    Un fijo en pesos sin tipo de cambio no se puede convertir. En vez de
    inventarle un monto entra en 0 con una nota que lo dice, para cargarlo a
    mano en dólares.
    """
    mes = _mes(hoy or date.today())
    filas = []
    for fijo in listar_recurrentes(db_path, solo_activos=True):
        if fijo["tipo"] != "egreso":
            continue
        if fijo["hasta"] and fijo["hasta"] < mes:
            continue
        nota = ""
        try:
            monto = a_usd(fijo["monto"], fijo["moneda"], fijo["tipo_cambio"])
        except (TypeError, ValueError):
            monto, nota = 0.0, NOTA_SIN_TIPO_DE_CAMBIO
        filas.append({
            "nombre": fijo["concepto"],
            "monto": round(monto, 2),
            "activo": True,
            "nota": nota,
        })
    return filas


def caja_actual(db_path: str, hoy: date | None = None) -> dict:
    """La caja de hoy según Finanzas: ingresos menos egresos acumulados.

    Finanzas no guarda un saldo de banco, así que se calcula con el criterio de
    sus KPIs (`_totales`: monto_usd líquido, sin IVA y sin anulados) sobre todos
    los movimientos hasta el mes en curso inclusive. Los de meses futuros no
    cuentan: todavía no pasaron por la caja.

    No materializa los fijos: el simulador no escribe en Finanzas. Si nadie
    abrió Finanzas este mes, los fijos del mes todavía no son movimientos y no
    están restados.
    """
    mes = _mes(hoy or date.today())
    ingresos, egresos = _totales(listar_movimientos(db_path, hasta=mes))
    return {"ingresos": ingresos, "egresos": egresos,
            "saldo": round(ingresos - egresos, 2), "hasta": mes}


def pendientes_por_cobrar(db_path: str) -> list[dict]:
    """Los saldos que todavía no se cobraron. Todos apagados: prender uno es
    decir "lo cobro este mes", y eso lo decide quien simula."""
    filas = []
    for p in listar_por_cobrar(db_path):
        nombre = p.get("client_name") or p["concepto"]
        partes = []
        if p.get("client_name"):
            partes.append(p["concepto"])
        if p.get("vence"):
            partes.append(f"vence {p['vence']}")
        filas.append({
            "nombre": nombre,
            "monto": round(float(p["monto_usd"] or 0), 2),
            "activo": False,
            "nota": " · ".join(partes),
        })
    return filas


def mantenimientos_de_clientes(db_path: str) -> list[dict]:
    """Un renglón por cliente real, apagado: hoy ninguno tiene mantenimiento.

    Sin monto: la cuota inicial es un default del panel (`SIM_DEFAULTS`), que
    es el único lugar donde viven los valores por defecto.
    """
    return [{"nombre": c["name"], "activo": False, "nota": ""}
            for c in listar_clientes_activos(db_path)]


def precarga(db_path: str, hoy: date | None = None) -> dict:
    caja = caja_actual(db_path, hoy)
    return {
        "moneda": "USD",
        "cajaActual": caja["saldo"],
        "caja": caja,
        "gastosFijos": gastos_desde_fijos(db_path, hoy),
        "mantenimientos": mantenimientos_de_clientes(db_path),
        "pendientes": pendientes_por_cobrar(db_path),
    }


def validar_escenario(data) -> tuple[tuple[str, str] | None, str | None]:
    """Devuelve ((nombre, datos en JSON), None) o (None, mensaje de error).

    No valida cada campo del escenario: el cálculo ya trata un número vacío o
    negativo tomando el default, y un escenario viejo al que le falte un campo
    tiene que poder abrirse igual.
    """
    if not isinstance(data, dict):
        return None, "el cuerpo tiene que ser un objeto JSON"
    nombre = data.get("nombre")
    nombre = nombre.strip() if isinstance(nombre, str) else ""
    if not nombre:
        return None, "el escenario necesita un nombre"
    if len(nombre) > LARGO_NOMBRE:
        return None, f"el nombre no puede pasar de {LARGO_NOMBRE} caracteres"
    datos = data.get("datos")
    if not isinstance(datos, dict):
        return None, "datos tiene que ser un objeto"
    texto = json.dumps(datos, ensure_ascii=False)
    if len(texto) > TAMANIO_MAXIMO:
        return None, "el escenario es demasiado grande"
    return (nombre, texto), None
