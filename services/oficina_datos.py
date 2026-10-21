"""Los agentes de la Oficina leen el CRM entero (pedido de Juan, 21/10:
"tienen que poder analizar todo").

La sesión de Claude Code que responde por ellos pide el esquema y manda
consultas SELECT por /api/oficina-bot/. Tres candados, cada uno alcanza solo:

- la base se abre en modo solo lectura (`mode=ro` + `query_only`);
- un autorizador de SQLite rechaza todo lo que no sea leer, y niega las tablas
  de secretos (contraseñas, tokens, OAuth, sesiones) y devuelve NULL en las
  columnas de secretos de cualquier tabla (por ejemplo el hash de la contraseña
  en `users`). Va por patrón de nombre, así una tabla nueva de tokens queda
  tapada sin acordarse de sumarla;
- tope de filas y de tiempo, para que una consulta pesada no frene el CRM.
"""

import re
import sqlite3
import time

TOPE_FILAS = 500
TOPE_SEGUNDOS = 5
LARGO_SQL = 5000

TABLAS_SECRETAS = re.compile(r"cred|token|oauth|secret|passw|sesion|session|cookie", re.I)
COLUMNAS_SECRETAS = re.compile(
    r"token|secret|passw|hash|clave|refresh|cifrad|api_?key|cookie|cvv|codigo_2fa", re.I)


class ConsultaInvalida(Exception):
    pass


def _tabla_visible(nombre: str) -> bool:
    return not TABLAS_SECRETAS.search(nombre or "")


def _autorizador(accion, arg1, arg2, _db, _origen):
    if accion in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    if accion == sqlite3.SQLITE_READ:
        if not _tabla_visible(arg1):
            return sqlite3.SQLITE_DENY
        if arg2 and COLUMNAS_SECRETAS.search(arg2):
            return sqlite3.SQLITE_IGNORE
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _abrir(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    conn.execute("PRAGMA query_only = ON")
    return conn


def esquema(db_path: str) -> list[dict]:
    """Tablas y columnas que los agentes pueden leer, sin las de secretos."""
    conn = _abrir(db_path)
    try:
        tablas = [f[0] for f in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        salida = []
        for t in tablas:
            if not _tabla_visible(t):
                continue
            columnas = [c[1] for c in conn.execute(f'PRAGMA table_info("{t}")')
                        if not COLUMNAS_SECRETAS.search(c[1])]
            filas = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            salida.append({"tabla": t, "columnas": columnas, "filas": filas})
        return salida
    finally:
        conn.close()


def consultar(db_path: str, sql) -> dict:
    sql = (sql or "").strip() if isinstance(sql, str) else ""
    if not sql:
        raise ConsultaInvalida("Falta la consulta")
    if len(sql) > LARGO_SQL:
        raise ConsultaInvalida(f"La consulta es muy larga: máximo {LARGO_SQL} caracteres")
    conn = _abrir(db_path)
    limite = time.monotonic() + TOPE_SEGUNDOS
    try:
        conn.set_authorizer(_autorizador)
        conn.set_progress_handler(lambda: 1 if time.monotonic() > limite else 0, 10000)
        try:
            cur = conn.execute(sql)
        except sqlite3.Warning:
            raise ConsultaInvalida("Va una sola consulta por vez")
        except sqlite3.DatabaseError as e:
            raise ConsultaInvalida(f"No se pudo: {e}")
        if cur.description is None:
            raise ConsultaInvalida("Solo se puede leer (SELECT)")
        columnas = [d[0] for d in cur.description]
        try:
            filas = cur.fetchmany(TOPE_FILAS + 1)
        except sqlite3.DatabaseError as e:
            raise ConsultaInvalida(f"No se pudo: {e}")
        return {"columnas": columnas, "filas": [list(f) for f in filas[:TOPE_FILAS]],
                "recortado": len(filas) > TOPE_FILAS}
    finally:
        conn.close()
