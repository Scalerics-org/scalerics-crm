"""Limpieza de filas huerfanas — correr UNA VEZ antes de confiar en las cascadas.

Las foreign keys estuvieron desactivadas todo este tiempo (SQLite las apaga por
defecto y hay que activarlas por conexion), asi que los ON DELETE CASCADE del
esquema nunca corrieron: cada lead borrado dejo atras sus lead_events, adjuntos,
call_logs y demos, con la PII adentro.

_connect() ya activa el pragma, con lo cual los borrados NUEVOS cascadean bien.
Este script limpia lo que quedo de antes. SQLite no valida las filas existentes
al activar el pragma, asi que hay que hacerlo a mano.

    python scripts/limpiar_huerfanos.py --db /data/leads.db --dry-run
    python scripts/limpiar_huerfanos.py --db /data/leads.db --apply
"""

import argparse
import sqlite3
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="Borra filas huerfanas (viola FK)")
    ap.add_argument("--db", required=True)
    modo = ap.add_mutually_exclusive_group(required=True)
    modo.add_argument("--dry-run", action="store_true")
    modo.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        violaciones = conn.execute("PRAGMA foreign_key_check").fetchall()
        if not violaciones:
            print("No hay huerfanos. Nada para hacer.")
            return 0

        # (tabla, rowid, tabla_padre, indice_fk)
        por_tabla: dict[str, list[int]] = {}
        for tabla, rowid, padre, _ in violaciones:
            por_tabla.setdefault((tabla, padre), []).append(rowid)

        total = 0
        for (tabla, padre), rowids in sorted(por_tabla.items()):
            print(f"\n=== {tabla}: {len(rowids)} fila(s) apuntando a {padre} inexistente ===")
            for rid in rowids[:20]:
                fila = conn.execute(f"SELECT * FROM {tabla} WHERE rowid = ?", (rid,)).fetchone()
                print(f"  rowid={rid}  {str(fila)[:100]}")
            if len(rowids) > 20:
                print(f"  ... y {len(rowids) - 20} mas")
            total += len(rowids)

            if args.apply:
                conn.executemany(f"DELETE FROM {tabla} WHERE rowid = ?", [(r,) for r in rowids])
                conn.commit()
                print(f"  -> borradas")

        if args.dry_run:
            print(f"\n[dry-run] {total} fila(s) huerfanas. Volve con --apply para borrarlas.")
        else:
            restantes = conn.execute("PRAGMA foreign_key_check").fetchall()
            print(f"\nListo. {total} borradas. Violaciones restantes: {len(restantes)}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
