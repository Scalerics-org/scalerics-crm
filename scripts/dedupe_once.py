"""Deduplicacion de businesses — migracion de UNA SOLA VEZ.

Esto vivia dentro de init_db(), o sea que corria en cada arranque del servidor.
El segundo bloque borraba leads de Meta sin telefono agrupando por (name, fecha),
y routes/meta.py usa 'Lead Meta' como nombre por defecto: un formulario sin
telefono colapsaba todos los leads de Meta de ese dia a uno solo, en silencio.

Correr a mano, con backup hecho, y SIEMPRE mirando primero el --dry-run:

    python scripts/dedupe_once.py --db /data/leads.db --dry-run
    python scripts/dedupe_once.py --db /data/leads.db --apply
"""

import argparse
import sqlite3
import sys

DUP_TELEFONO = """
    SELECT id, name, phone FROM businesses
    WHERE phone IS NOT NULL
      AND id NOT IN (SELECT MIN(id) FROM businesses WHERE phone IS NOT NULL GROUP BY phone)
"""

DUP_META_SIN_TEL = """
    SELECT id, name, scraped_at FROM businesses
    WHERE source = 'meta' AND phone IS NULL
      AND id NOT IN (
          SELECT MIN(id) FROM businesses
          WHERE source = 'meta' AND phone IS NULL
          GROUP BY name, SUBSTR(COALESCE(scraped_at, ''), 1, 10)
      )
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Deduplica businesses (una sola vez)")
    ap.add_argument("--db", required=True, help="Ruta a la base SQLite")
    modo = ap.add_mutually_exclusive_group(required=True)
    modo.add_argument("--dry-run", action="store_true", help="Solo mostrar que se borraria")
    modo.add_argument("--apply", action="store_true", help="Borrar de verdad")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        for etiqueta, query in (("telefono duplicado", DUP_TELEFONO),
                                ("meta sin telefono", DUP_META_SIN_TEL)):
            filas = conn.execute(query).fetchall()
            print(f"\n=== {etiqueta}: {len(filas)} fila(s) candidatas a borrar ===")
            for f in filas[:50]:
                print(f"  id={f['id']:<6} {(f['name'] or '')[:45]}")
            if len(filas) > 50:
                print(f"  ... y {len(filas) - 50} mas")

            if args.apply and filas:
                ids = [f["id"] for f in filas]
                conn.executemany("DELETE FROM businesses WHERE id = ?", [(i,) for i in ids])
                conn.commit()
                print(f"  -> {len(ids)} fila(s) borradas")

        if args.dry_run:
            print("\n[dry-run] No se toco nada. Volve a correr con --apply para aplicar.")
        else:
            print("\nListo. Reinicia la app para que se cree el indice unico de telefono.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
