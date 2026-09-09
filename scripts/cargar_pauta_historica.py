"""Carga los seis meses de pauta de `Scalerics - Leads - 2026.xlsx` y contrasta.

Hace dos cosas, en este orden:

1. Inserta la inversión de marzo a agosto de 2026 como egresos de categoría
   `publicidad`, para que la sección financiera no arranque vacía.
2. Compara los leads, calificados, demos y ventas que el CRM calcula contra los
   que la planilla trae a mano, mes por mes, y muestra la diferencia.

El paso 2 es el que importa. No está verificado que `lead_events` cubra esos
seis meses: si no llega tan atrás, los números del CRM van a dar por debajo de
los de la planilla. Este script no arregla eso — lo muestra, para que la
decisión se tome viendo el número y no suponiéndolo.

Es idempotente: si ya cargó un mes, no lo duplica.

Uso:  python scripts/cargar_pauta_historica.py [--db leads.db] [--aplicar]
Sin `--aplicar` no escribe nada: solo muestra qué haría y el contraste.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import crear_movimiento, listar_movimientos  # noqa: E402
from services.finanzas import rendimiento_pauta  # noqa: E402

# Copiado a mano de la hoja «Análisis». La columna rotulada «ROI» en la planilla
# es en realidad el costo por venta (3017.15 / 2 = 1508.575), así que no se
# transcribe: el ROI de verdad lo calcula el CRM con los ingresos.
PLANILLA = [
    # periodo,  inversion, leads, calificados, demos, ventas
    ("2026-03", 368.98, 59, 4, 3, 0),
    ("2026-04", 528.52, 25, 6, 5, 1),
    ("2026-05", 452.18, 28, 7, 8, 0),
    ("2026-06", 608.01, 49, 19, 12, 1),
    ("2026-07", 610.48, 40, 17, 11, 0),
    ("2026-08", 448.98, 26, 11, 5, 0),
]


def cargar(db_path: str, aplicar: bool) -> int:
    ya = {m["periodo"] for m in listar_movimientos(db_path, categoria="publicidad")}
    creados = 0
    for periodo, inversion, *_ in PLANILLA:
        if periodo in ya:
            print(f"  {periodo}  ya estaba, no se toca")
            continue
        print(f"  {periodo}  USD {inversion:>7.2f}  "
              f"{'CARGANDO' if aplicar else '(simulacro)'}")
        if aplicar:
            crear_movimiento(db_path, tipo="egreso", fecha=f"{periodo}-01",
                             periodo=periodo, concepto="Meta Ads",
                             categoria="publicidad", monto=inversion,
                             moneda="USD", monto_usd=inversion,
                             notas="Importado de Scalerics - Leads - 2026.xlsx",
                             created_by_name="carga histórica")
            creados += 1
    return creados


def contrastar(db_path: str) -> bool:
    r = rendimiento_pauta(db_path, PLANILLA[0][0], PLANILLA[-1][0])
    por_periodo = {m["periodo"]: m for m in r["meses"]}
    print(f"\n{'Mes':<9} {'concepto':<13} {'planilla':>9} {'CRM':>7} {'dif':>7}")
    print("-" * 50)
    coincide = True
    for periodo, _, leads, calificados, demos, ventas in PLANILLA:
        m = por_periodo.get(periodo, {})
        for etiqueta, esperado, clave in (("leads", leads, "leads"),
                                          ("calificados", calificados, "calificados"),
                                          ("demos", demos, "demos"),
                                          ("ventas", ventas, "ventas")):
            real = m.get(clave, 0)
            dif = real - esperado
            if dif:
                coincide = False
            marca = "" if not dif else ("  <<<" if abs(dif) > 2 else "  <")
            print(f"{periodo:<9} {etiqueta:<13} {esperado:>9} {real:>7} {dif:>+7}{marca}")
    return coincide


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=os.environ.get("DB_PATH", "leads.db"))
    p.add_argument("--aplicar", action="store_true",
                   help="escribir de verdad (sin esto es un simulacro)")
    args = p.parse_args()

    print(f"Base: {args.db}\n\nInversión en pauta:")
    creados = cargar(args.db, args.aplicar)
    print(f"\n{creados} movimiento(s) creado(s).")

    print("\nContraste contra la planilla:")
    if contrastar(args.db):
        print("\nTodo coincide. El CRM reemplaza la hoja «Análisis».")
    else:
        print("\nHay diferencias. Lo mas probable es que `lead_events` no cubra")
        print("todos esos meses, asi que la historia vieja esta incompleta y el")
        print("CRM cuenta de menos. No lo decidas sin mirar estos numeros.")


if __name__ == "__main__":
    main()
