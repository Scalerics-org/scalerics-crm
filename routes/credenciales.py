"""Panel de contraseñas de la empresa (pedido de Juan, 22/9): mail, Instagram,
Plexo, lo que sea. Solo admin (Ruling R20, igual que Finanzas): no se reparte
por rol, `require_admin` bloquea todo el blueprint.

Las claves se cifran en services/credenciales.py antes de tocar la base.
"""

from flask import Blueprint, current_app, jsonify, request

from database import (actualizar_credencial, borrar_credencial,
                      crear_credencial, get_credencial, listar_credenciales)
from services import credenciales as cred
from services.auth import require_admin

credenciales_bp = Blueprint("credenciales", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@credenciales_bp.before_request
def _candado():
    return require_admin(_db())


def _publica(fila: dict) -> dict:
    return {
        "id": fila["id"],
        "servicio": fila["servicio"],
        "usuario": fila.get("usuario") or "",
        "clave": cred.descifrar(fila["clave_cifrada"]),
        "codigo_2fa": fila.get("codigo_2fa") or "",
        "notas": fila.get("notas") or "",
        "creado_en": fila.get("creado_en"),
        "actualizado_en": fila.get("actualizado_en"),
    }


@credenciales_bp.route("/api/credenciales", methods=["GET"])
def api_listar():
    if not cred.activo():
        return jsonify({"ok": False, "error": "Falta CREDENCIALES_KEY: correr "
                        "'Activar contraseñas.bat' del Escritorio."}), 503
    return jsonify({"ok": True, "credenciales": [_publica(f) for f in listar_credenciales(_db())]})


def _validar(data: dict) -> tuple[dict | None, str | None]:
    servicio = (data.get("servicio") or "").strip()
    clave = data.get("clave") or ""
    if not servicio:
        return None, "El nombre (mail, Instagram, Plexo...) es obligatorio"
    if not clave.strip():
        return None, "La contraseña es obligatoria"
    return {
        "servicio": servicio,
        "usuario": (data.get("usuario") or "").strip(),
        "clave_cifrada": cred.cifrar(clave),
        "codigo_2fa": (data.get("codigo_2fa") or "").strip(),
        "notas": (data.get("notas") or "").strip(),
    }, None


@credenciales_bp.route("/api/credenciales", methods=["POST"])
def api_crear():
    if not cred.activo():
        return jsonify({"ok": False, "error": "Falta CREDENCIALES_KEY: correr "
                        "'Activar contraseñas.bat' del Escritorio."}), 503
    campos, error = _validar(request.get_json(silent=True) or {})
    if error:
        return jsonify({"ok": False, "error": error}), 400
    cid = crear_credencial(_db(), **campos)
    return jsonify({"ok": True, "credencial": _publica(get_credencial(_db(), cid))}), 201


@credenciales_bp.route("/api/credenciales/<int:cred_id>", methods=["PUT"])
def api_editar(cred_id):
    if not get_credencial(_db(), cred_id):
        return jsonify({"ok": False, "error": "No existe esa credencial"}), 404
    campos, error = _validar(request.get_json(silent=True) or {})
    if error:
        return jsonify({"ok": False, "error": error}), 400
    actualizar_credencial(_db(), cred_id, **campos)
    return jsonify({"ok": True, "credencial": _publica(get_credencial(_db(), cred_id))})


@credenciales_bp.route("/api/credenciales/<int:cred_id>", methods=["DELETE"])
def api_borrar(cred_id):
    if not get_credencial(_db(), cred_id):
        return jsonify({"ok": False, "error": "No existe esa credencial"}), 404
    borrar_credencial(_db(), cred_id)
    return jsonify({"ok": True})
