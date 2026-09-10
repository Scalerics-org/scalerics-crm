"""Verifica que las credenciales de Insights andan y con que nombre vienen los leads.

Meta no devuelve los leads como un campo: hay que buscarlos entre las acciones,
y el nombre de esa accion cambio entre versiones de la API. En
`services/meta_insights.py` estan los tres nombres conocidos, pero cual usa la
cuenta de Scalerics no se puede saber sin credenciales.

Si no es ninguno de los tres, la columna `leads` queda en cero, todos los CPL
dan "sin datos" y parece un bug del codigo cuando es un nombre que falta en una
lista. Este script lo dice de una.

Uso, con META_ADS_TOKEN y META_AD_ACCOUNT_ID en el entorno:

    python scripts/verificar_meta_insights.py [dias]

No escribe nada en la base: solo pide y muestra.
"""

import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from services.meta_insights import (_ACCIONES_DE_LEAD, _traer_de_la_api,  # noqa: E402
                                    hay_credenciales)


def main() -> int:
    if not hay_credenciales():
        print("Faltan META_ADS_TOKEN y/o META_AD_ACCOUNT_ID.")
        print("Ver docs/puesta-en-produccion-marketing-meta.md")
        return 1

    dias = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    hasta = date.today()
    desde = hasta - timedelta(days=dias)
    print(f"Pidiendo Insights de {desde} a {hasta}...\n")

    filas = _traer_de_la_api(desde.isoformat(), hasta.isoformat())
    if not filas:
        print("La API no devolvio ninguna fila. Puede ser que:")
        print("  - no haya gasto en el periodo (probar con mas dias)")
        print("  - el usuario del sistema no tenga asignada la cuenta")
        print("    publicitaria: en ese caso la API contesta vacio, no con error")
        return 1

    print(f"{len(filas)} filas.\n")

    campanas = Counter(f.get("campaign_name") or "(sin nombre)" for f in filas)
    print("Campañas:")
    for nombre, n in campanas.most_common():
        print(f"  {n:4d} días  {nombre}")

    acciones = Counter()
    for f in filas:
        for a in f.get("actions") or []:
            acciones[a.get("action_type")] += 1

    print("\naction_type que devuelve la cuenta:")
    if not acciones:
        print("  ninguno — las filas no traen `actions`.")
        print("  Sin eso no hay leads y todos los CPL van a dar 'sin datos'.")
        return 1

    reconocido = False
    for tipo, n in acciones.most_common():
        marca = ""
        if tipo in _ACCIONES_DE_LEAD:
            marca = "  <-- reconocido como lead"
            reconocido = True
        print(f"  {n:4d}  {tipo}{marca}")

    if reconocido:
        print("\nOK: el modulo va a contar los leads bien.")
        return 0

    print("\nPROBLEMA: ninguno de los action_type de esta cuenta esta en")
    print(f"_ACCIONES_DE_LEAD, que hoy es {_ACCIONES_DE_LEAD}.")
    print("La columna `leads` va a quedar en cero y todos los CPL en 'sin datos'.")
    print("Agregar el que corresponda en services/meta_insights.py y anotar en")
    print("el docstring cual usa la cuenta.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
