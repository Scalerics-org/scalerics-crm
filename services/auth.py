"""Chequeos de autorizacion compartidos.

Existia el mismo bloque de 4 lineas copiado en 6 endpoints de dashboard.py, y en
otros 5 no estaba: /api/admin/roles (GET, POST, PUT, DELETE) y
/api/admin/users/<uid>/role no validaban nada, asi que cualquier usuario logueado
podia crear roles, reescribir los permisos de los existentes, borrarlos dejando a
todo el equipo sin paneles, o cambiarse el rol a si mismo.

Es admin quien tenga el email de ADMIN_EMAIL (la cuenta raiz) o el rol llamado
"Admin". El id=1 solo cuenta si ADMIN_EMAIL no esta configurado.

El rol se incluye a pedido del equipo. Tiene un costo que conviene tener presente:
quien tenga el rol Admin puede editar los roles, y por lo tanto darle admin a
otro. Es la contrapartida de que el rol signifique lo que dice; antes NO contaba,
y el resultado era que cuatro personas con el rol "Admin" no podian abrir la
seccion de usuarios porque solo mandaba ADMIN_EMAIL, que admite un unico valor.

ADMIN_EMAIL sigue siendo la salida de emergencia: esa cuenta es admin aunque le
saquen el rol.
"""

import json
import os
import sqlite3

from flask import g, jsonify, request, session

from database import get_user_by_id
from database import connect as _db_connect

# Fuente de verdad unica de los paneles. Estaba duplicada en dos constantes de
# JavaScript (el dashboard y la pagina de admin) y a AMBAS les faltaba 'sdr', que
# si existe en el nav. Como el editor de roles arma panel_access iterando esta
# lista, guardar cualquier rol borraba el permiso de SDR en silencio.
# Se sirve desde /api/me para que el frontend no la vuelva a hardcodear.
ALL_PANELS = (
    "cola", "seguimientos", "meta", "pipeline", "clientes",
    "tasks", "wa", "cal", "metrics", "sdr", "activity",
)

# Que panel habilita cada blueprint. Es una primera capa GRUESA: alcanza para que
# un rol sin acceso a un area no pueda tocar sus endpoints, pero no distingue
# entre operaciones dentro del mismo blueprint.
#
# leads_bp mapea a varios paneles porque sus 776 lineas sirven a la Cola, a
# Seguimientos, a la ficha de cliente y al Pipeline: se exige tener AL MENOS UNO.
PANELES_POR_BLUEPRINT = {
    "leads":    {"cola", "seguimientos", "clientes", "pipeline"},
    "pipeline": {"pipeline"},
    "calendar": {"cal"},
    "tasks":    {"tasks"},
    "budgets":  {"clientes"},
    "demos":    {"clientes"},
    "wa":       {"wa"},
    "meta":     {"meta"},
}


def es_rol_admin(db_path: str, role_id) -> bool:
    """El rol se llama "Admin"."""
    if not role_id:
        return False
    conn = _db_connect(db_path)
    try:
        fila = conn.execute("SELECT name FROM roles WHERE id = ?", (role_id,)).fetchone()
    finally:
        conn.close()
    return bool(fila) and (fila["name"] or "").strip().lower() == "admin"


def is_admin(db_path: str, user_id) -> bool:
    """Es admin quien cumpla CUALQUIERA de estas:

      1. su email coincide con ADMIN_EMAIL  (la cuenta raiz)
      2. tiene el rol llamado "Admin"
      3. es el usuario id=1, solo si ADMIN_EMAIL no esta configurado

    La (2) se sumo porque sin ella el rol "Admin" no daba acceso a usuarios ni a
    roles: eso dependia unicamente de ADMIN_EMAIL, que admite un solo valor. En
    produccion habia cuatro personas con el rol Admin y ninguna podia entrar,
    salvo la que coincidia con esa variable. Dos cosas distintas se llamaban
    igual, y la que se veia en pantalla no era la que mandaba.
    """
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


def paneles_del_usuario(db_path: str, user_id) -> set | None:
    """Paneles a los que el usuario tiene acceso.

    Devuelve None cuando el acceso es total (admin). Un set vacio significa que no
    puede ver nada. Replica la resolucion de /api/me: el rol tiene prioridad sobre
    el panel_access directo, y quedarse sin rol es quedarse sin acceso.
    """
    if is_admin(db_path, user_id):
        return None
    user = get_user_by_id(db_path, user_id)
    if not user:
        return set()

    crudo = None
    role_id = user.get("role_id")
    if role_id:
        conn = _db_connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rol = conn.execute(
                "SELECT name, panel_access FROM roles WHERE id=?", (role_id,)
            ).fetchone()
        finally:
            conn.close()
        if rol:
            if (rol["name"] or "").lower() == "admin":
                return None
            crudo = rol["panel_access"]
    else:
        crudo = "[]"  # sin rol = sin acceso

    if crudo is None:
        crudo = user.get("panel_access")
    if crudo is None:
        return None  # sin restriccion declarada = acceso total

    try:
        return set(json.loads(crudo) or [])
    except (ValueError, TypeError):
        # Un panel_access corrupto no puede significar "acceso total".
        return set()


def puede_ver_panel(db_path: str, user_id, paneles_requeridos: set) -> bool:
    permitidos = paneles_del_usuario(db_path, user_id)
    if permitidos is None:
        return True
    return bool(permitidos & paneles_requeridos)


def enforce_panel_access(db_path: str):
    """before_request que aplica los permisos de panel del lado del SERVIDOR.

    Hasta ahora panel_access solo escondia items del nav en JavaScript: cualquier
    usuario logueado podia leer o escribir en areas que su rol no le habilitaba con
    un fetch de una linea desde la consola. Un grep por enforcement en routes/ y
    services/ devolvia cero lineas.

    Devuelve None cuando la request puede seguir.
    """
    # El bypass por x-admin-token ya se resolvio en require_login.
    if getattr(g, "admin_token_auth", False):
        return None
    requeridos = PANELES_POR_BLUEPRINT.get(request.blueprint or "")
    if not requeridos:
        return None
    if puede_ver_panel(db_path, session.get("user_id"), requeridos):
        return None
    return jsonify({
        "error": "panel_no_autorizado",
        "detail": "Tu rol no tiene acceso a esta sección.",
    }), 403
