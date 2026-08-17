"""Sube a la columna `email` los mails que quedaron dentro de form_data.

El webhook los extraia y no los guardaba (ver routes/meta.py). Este script
recupera los que ya entraron; el arreglo del webhook evita que vuelva a pasar.
"""

import json
import sqlite3
import sys

CLAVES = ("email", "correo")


def _email_de(form_data: str) -> str:
    try:
        campos = json.loads(form_data or "{}")
    except (ValueError, TypeError):
        return ""
    if not isinstance(campos, dict):
        return ""
    for clave in CLAVES:
        valor = (campos.get(clave) or "").strip()
        if "@" in valor:
            return valor
    return ""


def backfill(db_path: str, dry_run: bool = False) -> dict:
    conn = sqlite3.connect(db_path)
    res = {"actualizados": 0, "sin_email": 0, "ya_tenian": 0}

    filas = conn.execute(
        "SELECT id, email, form_data FROM businesses WHERE source = ?", ("meta",)
    ).fetchall()

    for bid, email_actual, form_data in filas:
        if email_actual and email_actual.strip():
            res["ya_tenian"] += 1
            continue
        email = _email_de(form_data)
        if not email:
            res["sin_email"] += 1
            continue
        if not dry_run:
            conn.execute("UPDATE businesses SET email = ? WHERE id = ?", (email, bid))
        res["actualizados"] += 1

    if not dry_run:
        conn.commit()
    conn.close()
    return res


if __name__ == "__main__":
    seco = "--dry-run" in sys.argv
    argumentos = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(argumentos) != 1:
        print("uso: python -m scripts.backfill_meta_emails <db> [--dry-run]")
        sys.exit(1)
    print(backfill(argumentos[0], dry_run=seco))
