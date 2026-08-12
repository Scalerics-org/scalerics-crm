"""Agrega la columna form_data a businesses. Idempotente."""

import sqlite3
import sys


def migrate(db_path: str) -> bool:
    conn = sqlite3.connect(db_path)
    existentes = {fila[1] for fila in conn.execute("PRAGMA table_info(businesses)")}
    if "form_data" in existentes:
        conn.close()
        return False
    conn.execute("ALTER TABLE businesses ADD COLUMN form_data TEXT")
    conn.commit()
    conn.close()
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("uso: python -m scripts.add_form_data_column <db>")
        sys.exit(1)
    print("columna agregada" if migrate(sys.argv[1]) else "ya existía")
