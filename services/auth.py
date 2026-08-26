"""Quien es admin del CRM.

El acceso a usuarios y roles dependia UNICAMENTE de ADMIN_EMAIL, que admite un
solo valor. En produccion hay cuatro personas con el rol "Admin" y ninguna podia
abrir la seccion de usuarios: solo entraba quien coincidiera con esa variable.
Dos cosas distintas se llamaban igual, y la que se veia en pantalla no mandaba.

Es admin quien cumpla CUALQUIERA de estas:

  1. su email coincide con ADMIN_EMAIL   (cuenta raiz, salida de emergencia)
  2. tiene el rol llamado "Admin"
  3. es el usuario id=1, solo si ADMIN_EMAIL no esta configurado

El costo de (2), explicito: quien tenga el rol Admin puede editar los roles, y
por lo tanto darle admin a otro. Es la contrapartida de que el rol signifique lo
que dice. ADMIN_EMAIL queda como salida de emergencia: esa cuenta es admin aunque
le saquen el rol.

La decision estaba duplicada inline en 8 endpoints de dashboard.py, y esa
duplicacion es justamente por que el rol no contaba: habia que acordarse de
mirarlo en ocho lugares.
"""

import os

from flask import jsonify, session

from database import get_user_by_id
from database import _connect as _db_connect


def es_rol_admin(db_path: str, role_id) -> bool:
    """El rol asignado se llama "Admin" (sin distinguir mayusculas)."""
    if not role_id:
        return False
    conn = _db_connect(db_path)
    try:
        fila = conn.execute("SELECT name FROM roles WHERE id = ?", (role_id,)).fetchone()
    finally:
        conn.close()
    return bool(fila) and (fila["name"] or "").strip().lower() == "admin"


def is_admin(db_path: str, user_id) -> bool:
    if not user_id:
        return False
    current = get_user_by_id(db_path, user_id)
    if not current:
        return False

    admin_email = os.environ.get("ADMIN_EMAIL", "").strip()
    if admin_email and (current["email"] or "").lower() == admin_email.lower():
        return True
    if es_rol_admin(db_path, current.get("role_id")):
        return True
    # Solo cuando no hay ADMIN_EMAIL: si no, el id=1 seria admin encubierto.
    return not admin_email and current["id"] == 1


def require_admin(db_path: str):
    """Devuelve una respuesta 403 si el usuario de la sesion no es admin, o None
    si puede seguir. Uso:

        err = require_admin(db_path)
        if err:
            return err
    """
    if not is_admin(db_path, session.get("user_id")):
        return jsonify({"ok": False, "error": "No autorizado"}), 403
    return None
