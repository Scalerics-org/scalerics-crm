"""Chequeos de autorizacion compartidos.

Existia el mismo bloque de 4 lineas copiado en 6 endpoints de dashboard.py, y en
otros 5 no estaba: /api/admin/roles (GET, POST, PUT, DELETE) y
/api/admin/users/<uid>/role no validaban nada, asi que cualquier usuario logueado
podia crear roles, reescribir los permisos de los existentes, borrarlos dejando a
todo el equipo sin paneles, o cambiarse el rol a si mismo.

La definicion de admin se mantiene identica a la que ya usaban los endpoints que
si validaban, para no ampliar el acceso sin querer:
  - si ADMIN_EMAIL esta configurado -> es admin quien tenga ese email
  - si no lo esta                    -> es admin el usuario id=1

A proposito NO se considera admin a quien tenga un rol llamado "admin": los roles
son justamente el recurso que estos endpoints protegen, y hacerlos parte de la
condicion permitiria escalar privilegios modificando el propio rol.
"""

import os

from flask import jsonify, session

from database import get_user_by_id


def is_admin(db_path: str, user_id) -> bool:
    if not user_id:
        return False
    current = get_user_by_id(db_path, user_id)
    if not current:
        return False
    admin_email = os.environ.get("ADMIN_EMAIL", "").strip()
    if admin_email:
        return (current["email"] or "").lower() == admin_email.lower()
    return current["id"] == 1


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
