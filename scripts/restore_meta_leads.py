"""Restaura los leads de Meta borrados el 30-07-2026 desde el backup JSON.

Los 7 ids de CLIENTES_VIVOS nunca se borraron: a esos se les hace UPDATE de
source y form_data. Al resto se le hace INSERT.
"""

import json
import sqlite3
import sys

CLIENTES_VIVOS = {598, 626, 655, 694, 20149, 26740, 32095}

TABLAS_RELACIONADAS = [
    "lead_events", "call_logs", "lead_attachments",
    "budgets", "demos", "meetings", "activity_log",
]

# Cada tabla relacionada apunta al negocio con un nombre de columna distinto.
# activity_log no tiene FK real: usa entity_type/entity_id de forma genérica.
COLUMNA_FK = {
    "lead_events": "lead_id",
    "call_logs": "lead_id",
    "lead_attachments": "lead_id",
    "budgets": "client_id",
    "demos": "client_id",
    "meetings": "client_id",
}


def _columnas(conn, tabla: str) -> set:
    return {fila[1] for fila in conn.execute(f"PRAGMA table_info({tabla})")}


def _filtrar(fila: dict, columnas: set) -> dict:
    """Se queda solo con las claves que la tabla realmente tiene hoy."""
    return {k: v for k, v in fila.items() if k in columnas}


def _id_de_negocio(tabla: str, fila: dict):
    """Extrae el id de negocio al que refiere la fila, según cómo lo
    nombre cada tabla (lead_id, client_id, o entity_id/entity_type)."""
    if tabla == "activity_log":
        if fila.get("entity_type") in ("lead", "business"):
            return fila.get("entity_id")
        return None
    columna = COLUMNA_FK.get(tabla)
    if columna is None:
        return None
    return fila.get(columna)


def restore(db_path: str, backup_path: str, dry_run: bool = False) -> dict:
    with open(backup_path, encoding="utf-8") as f:
        datos = json.load(f)

    conn = sqlite3.connect(db_path)
    res = {"inserted": 0, "updated": 0, "skipped": 0}

    cols_biz = _columnas(conn, "businesses")
    # Solo los negocios que de verdad se re-insertan (nunca los 7 vivos)
    # arrastran sus tablas relacionadas: esos 7 nunca se borraron, así que
    # su historial en producción ya está intacto y no hay que tocarlo.
    ids_insertados = set()

    for fila in datos.get("businesses", []):
        bid = fila.get("id")
        if bid is None:
            res["skipped"] += 1
            continue

        if bid in CLIENTES_VIVOS:
            existe = conn.execute(
                "SELECT 1 FROM businesses WHERE id = ?", (bid,)
            ).fetchone()
            if existe:
                if not dry_run:
                    conn.execute(
                        "UPDATE businesses SET source = ?, form_data = ? WHERE id = ?",
                        (fila.get("source"), fila.get("form_data"), bid),
                    )
                res["updated"] += 1
                continue

        datos_fila = _filtrar(fila, cols_biz)
        campos = ", ".join(datos_fila)
        marcas = ", ".join("?" for _ in datos_fila)
        if not dry_run:
            conn.execute(
                f"INSERT OR IGNORE INTO businesses ({campos}) VALUES ({marcas})",
                list(datos_fila.values()),
            )
        res["inserted"] += 1
        ids_insertados.add(bid)

    for tabla in TABLAS_RELACIONADAS:
        filas = datos.get(tabla, [])
        if not filas:
            continue
        try:
            cols = _columnas(conn, tabla)
        except sqlite3.Error:
            continue
        if not cols:
            continue
        for fila in filas:
            if _id_de_negocio(tabla, fila) not in ids_insertados:
                continue
            datos_fila = _filtrar(fila, cols)
            if not datos_fila:
                continue
            campos = ", ".join(datos_fila)
            marcas = ", ".join("?" for _ in datos_fila)
            if not dry_run:
                conn.execute(
                    f"INSERT OR IGNORE INTO {tabla} ({campos}) VALUES ({marcas})",
                    list(datos_fila.values()),
                )

    if not dry_run:
        conn.commit()
    conn.close()
    return res


if __name__ == "__main__":
    seco = "--dry-run" in sys.argv
    argumentos = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(argumentos) != 2:
        print("uso: python -m scripts.restore_meta_leads <db> <backup.json> [--dry-run]")
        sys.exit(1)
    print(restore(argumentos[0], argumentos[1], dry_run=seco))
