"""Endpoints del Daily Programador.

Finos, como los de Equipo: validan, llaman a la base y a `services/daily.py`, y
serializan. Todo detrás del panel `daily`: quien lo tiene entra al día de
cualquier programador (pedido de Juan, 15/9: una entrada por persona en el
menú). Cada ítem guarda quién lo creó (`created_by_id` de la sesión).
"""

from datetime import date, datetime, timezone

from flask import Blueprint, current_app, jsonify, request, session

from database import (actualizar_actividad_daily, actualizar_recordatorio_daily,
                      borrar_actividad_daily, borrar_recordatorio_daily,
                      crear_actividad_daily, crear_recordatorio_daily,
                      get_actividad_daily, get_persona_equipo,
                      get_recordatorio_daily, listar_programadores,
                      marcar_recordatorio_daily)
from services.auth import require_panel
from services.daily import (dia, dias_de_texto, hoy_montevideo, primer_nombre,
                            validar_recordatorio, validar_texto)
from services.equipo import parse_fecha

daily_bp = Blueprint("daily", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


def _ahora() -> datetime:
    """Aparte para poder fijar la hora en los tests. El servidor corre en UTC."""
    return datetime.now(timezone.utc)


def _hoy() -> date:
    return hoy_montevideo(_ahora())


@daily_bp.before_request
def _candado():
    return require_panel(_db(), "daily")


def _error(mensaje: str, codigo: int = 400):
    return jsonify({"ok": False, "error": mensaje}), codigo


def _cuerpo() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _programador(db: str, crudo):
    """(persona, None) o (None, respuesta de error)."""
    try:
        persona_id = int(crudo)
    except (TypeError, ValueError):
        return None, _error("persona_id tiene que ser un número")
    persona = get_persona_equipo(db, persona_id)
    if not persona or not persona["activo"] or not persona.get("programador"):
        return None, _error("esa persona no tiene Daily Programador", 404)
    return persona, None


def _fecha(crudo, por_defecto: date):
    if crudo in (None, ""):
        return por_defecto, None
    fecha = parse_fecha(crudo)
    if fecha is None:
        return None, _error("fecha tiene que ser AAAA-MM-DD")
    return fecha, None


@daily_bp.route("/api/daily/personas")
def api_personas():
    return jsonify({
        "hoy": _hoy().isoformat(),
        "personas": [{"id": p["id"], "nombre": p["nombre"], "primer_nombre": primer_nombre(p["nombre"])}
                     for p in listar_programadores(_db())],
    })


@daily_bp.route("/api/daily")
def api_dia():
    db = _db()
    persona, err = _programador(db, request.args.get("persona_id"))
    if err:
        return err
    hoy = _hoy()
    fecha, err = _fecha(request.args.get("fecha"), hoy)
    if err:
        return err
    return jsonify(dia(db, persona, fecha, hoy))


# ── actividades ──────────────────────────────────────────────────────────────

@daily_bp.route("/api/daily/actividades", methods=["POST"])
def api_crear_actividad():
    db, data = _db(), _cuerpo()
    persona, err = _programador(db, data.get("persona_id"))
    if err:
        return err
    fecha, err = _fecha(data.get("fecha"), _hoy())
    if err:
        return err
    texto, error = validar_texto(data.get("texto"))
    if error:
        return _error(error)
    aid = crear_actividad_daily(db, persona["id"], fecha.isoformat(), texto,
                                session.get("user_id"), session.get("user_name", "sistema"))
    return jsonify({"ok": True, "id": aid}), 201


def _actividad(db: str, actividad_id: int):
    actividad = get_actividad_daily(db, actividad_id)
    if not actividad:
        return None, _error("no existe esa actividad", 404)
    return actividad, None


@daily_bp.route("/api/daily/actividades/<int:actividad_id>", methods=["PATCH"])
def api_editar_actividad(actividad_id):
    db, data = _db(), _cuerpo()
    _a, err = _actividad(db, actividad_id)
    if err:
        return err
    campos = {}
    if "hecha" in data:
        if not isinstance(data["hecha"], bool):
            return _error("hecha tiene que ser true o false")
        campos["hecha"] = 1 if data["hecha"] else 0
    if "texto" in data:
        texto, error = validar_texto(data["texto"])
        if error:
            return _error(error)
        campos["texto"] = texto
    if not campos:
        return _error("no hay nada para cambiar: mandá hecha o texto")
    actualizar_actividad_daily(db, actividad_id, **campos)
    return jsonify({"ok": True})


@daily_bp.route("/api/daily/actividades/<int:actividad_id>", methods=["DELETE"])
def api_borrar_actividad(actividad_id):
    db = _db()
    _a, err = _actividad(db, actividad_id)
    if err:
        return err
    borrar_actividad_daily(db, actividad_id)
    return jsonify({"ok": True})


@daily_bp.route("/api/daily/actividades/<int:actividad_id>/pasar", methods=["POST"])
def api_pasar_actividad(actividad_id):
    """Lo que quedó sin hacer pasa a otro día (hoy, si no se dice cuál). Se
    mueve, no se copia: así no queda repetido como pendiente. `pasada_de`
    guarda el día en que se cargó por primera vez."""
    db, data = _db(), _cuerpo()
    actividad, err = _actividad(db, actividad_id)
    if err:
        return err
    fecha, err = _fecha(data.get("fecha"), _hoy())
    if err:
        return err
    if fecha.isoformat() != actividad["fecha"]:
        actualizar_actividad_daily(db, actividad_id, fecha=fecha.isoformat(),
                                   pasada_de=actividad["pasada_de"] or actividad["fecha"])
    return jsonify({"ok": True, "fecha": fecha.isoformat()})


# ── recordatorios ────────────────────────────────────────────────────────────

@daily_bp.route("/api/daily/recordatorios", methods=["POST"])
def api_crear_recordatorio():
    db, data = _db(), _cuerpo()
    persona, err = _programador(db, data.get("persona_id"))
    if err:
        return err
    campos, error = validar_recordatorio(data)
    if error:
        return _error(error)
    rid = crear_recordatorio_daily(db, persona["id"], campos["texto"], campos["frecuencia"],
                                   campos["dias"], _hoy().isoformat(), campos["activo"],
                                   session.get("user_id"), session.get("user_name", "sistema"))
    return jsonify({"ok": True, "id": rid}), 201


@daily_bp.route("/api/daily/recordatorios/<int:recordatorio_id>", methods=["PUT"])
def api_editar_recordatorio(recordatorio_id):
    db, data = _db(), _cuerpo()
    actual = get_recordatorio_daily(db, recordatorio_id)
    if not actual:
        return _error("no existe ese recordatorio", 404)
    # Lo que no se manda queda como estaba: pausar es mandar solo `activo`.
    completo = {"texto": actual["texto"], "frecuencia": actual["frecuencia"],
                "dias": dias_de_texto(actual["dias"]), "activo": bool(actual["activo"])}
    completo.update({k: data[k] for k in ("texto", "frecuencia", "dias", "activo") if k in data})
    campos, error = validar_recordatorio(completo)
    if error:
        return _error(error)
    actualizar_recordatorio_daily(db, recordatorio_id, **campos)
    return jsonify({"ok": True})


@daily_bp.route("/api/daily/recordatorios/<int:recordatorio_id>", methods=["DELETE"])
def api_borrar_recordatorio(recordatorio_id):
    db = _db()
    if not get_recordatorio_daily(db, recordatorio_id):
        return _error("no existe ese recordatorio", 404)
    borrar_recordatorio_daily(db, recordatorio_id)
    return jsonify({"ok": True})


@daily_bp.route("/api/daily/recordatorios/<int:recordatorio_id>/marca", methods=["PUT"])
def api_marcar_recordatorio(recordatorio_id):
    """Hecho o no en UN día. Los otros días del mismo recordatorio no cambian."""
    db, data = _db(), _cuerpo()
    if not get_recordatorio_daily(db, recordatorio_id):
        return _error("no existe ese recordatorio", 404)
    fecha = parse_fecha(data.get("fecha"))
    if fecha is None:
        return _error("fecha tiene que ser AAAA-MM-DD")
    if not isinstance(data.get("hecha"), bool):
        return _error("hecha tiene que ser true o false")
    marcar_recordatorio_daily(db, recordatorio_id, fecha.isoformat(), data["hecha"],
                              session.get("user_id"))
    return jsonify({"ok": True})
