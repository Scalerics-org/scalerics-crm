"""Pre-clientes, clientes activos y registro de demos.

Tres vistas sobre los mismos negocios, separadas por en que momento del ciclo
esta cada uno:

  pre-clientes   todavia se esta vendiendo. El eje son las demos, por eso las
                 etapas van demo_agendada -> demo_1..3 -> presupuesto -> follow up
  clientes       ya cerro. Lo que importa no es la etapa sino quien se ocupa:
                 dia a dia, mantenimiento y cobro
  demos          el historial de cada demo dada, con quien la tuvo y como viene.
                 El tablero dice donde esta cada uno HOY; esto dice como llego
"""

import logging

from flask import Blueprint, current_app, jsonify, request, session

from database import (ETAPAS_CLIENTE, ETAPAS_PRECLIENTE, actualizar_demo_realizada,
                      borrar_demo_realizada, crear_demo_realizada, get_business,
                      get_user_by_id, listar_demos_realizadas, log_activity,
                      obtener_demo_realizada, update_business)
from database import _connect as _db_connect

logger = logging.getLogger(__name__)
preclientes_bp = Blueprint("preclientes", __name__)

# Etiquetas para mostrar. Viven en el backend para que el tablero y los selectores
# no puedan divergir, que es como se perdio el panel SDR en su momento.
ETIQUETAS_ETAPA = {
    "demo_agendada":       "Demo agendada",
    "demo_1":              "Demo 1",
    "demo_2":              "Demo 2",
    "demo_3":              "Demo 3",
    "presupuesto_enviado": "Presupuesto enviado",
    "follow_up_1":         "Follow up 1",
    "follow_up_2":         "Follow up 2",
    "acepto":              "Aceptó",
    "en_espera":           "En espera",
    "rechazo":             "Rechazo",
    "cerrado":             "Cerrado",
    "en_desarrollo":       "En desarrollo",
    "finalizado":          "Finalizado",
}

_ROLES_CLIENTE = ("encargado_id", "mantenimiento_id", "cobros_id")


def _db() -> str:
    return current_app.config["DB_PATH"]


def _uid():
    return session.get("user_id")


def _usuario_valido(valor):
    """Devuelve (id, error). Un puesto vacante es None, no un id que no existe."""
    if valor in (None, "", "null"):
        return None, None
    try:
        valor = int(valor)
    except (TypeError, ValueError):
        return None, "valor inválido"
    if not get_user_by_id(_db(), valor):
        return None, "Usuario inexistente"
    return valor, None


# ── Pre-clientes ─────────────────────────────────────────────────────────────

@preclientes_bp.route("/api/preclientes/etapas")
def api_etapas():
    """Las etapas y sus etiquetas, en orden. El frontend las pide en vez de
    tenerlas hardcodeadas."""
    return jsonify({
        "preclientes": [{"key": e, "label": ETIQUETAS_ETAPA[e]} for e in ETAPAS_PRECLIENTE],
        "clientes":    [{"key": e, "label": ETIQUETAS_ETAPA[e]} for e in ETAPAS_CLIENTE],
    })


@preclientes_bp.route("/api/preclientes")
def api_preclientes():
    """Los pre-clientes agrupados por etapa, listos para dibujar el tablero.

    Se agrupa en SQL y se devuelve ya ordenado: la vista solo dibuja. Incluye
    quien dio la ultima demo, que es lo que el equipo mira para saber a quien
    preguntarle por ese lead.
    """
    marcadores = ",".join("?" * len(ETAPAS_PRECLIENTE))
    conn = _db_connect(_db())
    try:
        filas = conn.execute(f"""
            SELECT b.id, b.name, b.crm_status, b.phone, b.email, b.city, b.category,
                   b.last_event_at,
                   (SELECT COUNT(*) FROM demos_realizadas d WHERE d.client_id = b.id)
                       AS demos_dadas,
                   (SELECT u.name FROM demos_realizadas d
                     LEFT JOIN users u ON d.realizada_por = u.id
                     WHERE d.client_id = b.id
                     ORDER BY COALESCE(d.fecha, d.created_at) DESC, d.id DESC
                     LIMIT 1) AS ultima_demo_por
            FROM businesses b
            WHERE b.crm_status IN ({marcadores})
            ORDER BY COALESCE(b.last_event_at, b.scraped_at) DESC
        """, list(ETAPAS_PRECLIENTE)).fetchall()
    finally:
        conn.close()

    por_etapa = {e: [] for e in ETAPAS_PRECLIENTE}
    for f in filas:
        por_etapa[f["crm_status"]].append(dict(f))
    return jsonify({
        "etapas": [
            {"key": e, "label": ETIQUETAS_ETAPA[e], "leads": por_etapa[e],
             "total": len(por_etapa[e])}
            for e in ETAPAS_PRECLIENTE
        ],
        "total": len(filas),
    })


# ── Clientes activos ─────────────────────────────────────────────────────────

@preclientes_bp.route("/api/clientes-activos")
def api_clientes_activos():
    """Los clientes que ya cerraron, con sus tres responsables resueltos a nombre."""
    marcadores = ",".join("?" * len(ETAPAS_CLIENTE))
    conn = _db_connect(_db())
    try:
        filas = conn.execute(f"""
            SELECT b.id, b.name, b.crm_status, b.phone, b.email, b.city,
                   b.encargado_id, b.mantenimiento_id, b.cobros_id,
                   ue.name AS encargado_nombre,
                   um.name AS mantenimiento_nombre,
                   uc.name AS cobros_nombre
            FROM businesses b
            LEFT JOIN users ue ON b.encargado_id     = ue.id
            LEFT JOIN users um ON b.mantenimiento_id = um.id
            LEFT JOIN users uc ON b.cobros_id        = uc.id
            WHERE b.crm_status IN ({marcadores})
            ORDER BY b.name COLLATE NOCASE
        """, list(ETAPAS_CLIENTE)).fetchall()
    finally:
        conn.close()
    return jsonify({"clientes": [dict(f) for f in filas], "total": len(filas)})


@preclientes_bp.route("/api/clientes-activos/<int:client_id>/responsables", methods=["PUT"])
def api_responsables(client_id):
    """Asigna o quita los tres responsables. Un valor null deja el puesto vacante."""
    if not get_business(_db(), client_id):
        return jsonify({"ok": False, "error": "Cliente no encontrado"}), 404

    data = request.get_json() or {}
    campos = {}
    for rol in _ROLES_CLIENTE:
        if rol not in data:
            continue
        valor, error = _usuario_valido(data[rol])
        if error:
            return jsonify({"ok": False, "error": f"{rol}: {error}"}), 400
        campos[rol] = valor

    if not campos:
        return jsonify({"ok": False, "error": "Nada para actualizar"}), 400

    update_business(_db(), client_id, **campos)
    return jsonify({"ok": True, **campos})


# ── Registro de demos ────────────────────────────────────────────────────────

@preclientes_bp.route("/api/demos-realizadas", methods=["GET"])
def api_listar_demos():
    client_id = request.args.get("client_id", type=int)
    limite = request.args.get("limite", type=int) or 200
    return jsonify({"demos": listar_demos_realizadas(_db(), client_id=client_id,
                                                     limite=limite)})


@preclientes_bp.route("/api/demos-realizadas", methods=["POST"])
def api_crear_demo():
    data = request.get_json() or {}
    try:
        client_id = int(data.get("client_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "client_id requerido"}), 400

    cliente = get_business(_db(), client_id)
    if not cliente:
        return jsonify({"ok": False, "error": "Lead no encontrado"}), 404

    if "realizada_por" in data:
        realizada_por, error = _usuario_valido(data["realizada_por"])
        if error:
            return jsonify({"ok": False, "error": f"realizada_por: {error}"}), 400
    else:
        # Por defecto la dio quien la registra, que es el caso habitual.
        realizada_por = _uid()

    demo_id = crear_demo_realizada(
        _db(), client_id,
        numero=data.get("numero"),
        realizada_por=realizada_por,
        fecha=(data.get("fecha") or "").strip() or None,
        actualizacion=(data.get("actualizacion") or "").strip(),
        created_by=_uid(),
    )
    log_activity(_db(), session.get("user_name", "sistema"), "demo_registrada",
                 "lead", client_id, cliente.get("name", ""),
                 (data.get("actualizacion") or "")[:120], user_id=_uid())
    return jsonify({"ok": True, "id": demo_id}), 201


@preclientes_bp.route("/api/demos-realizadas/<int:demo_id>", methods=["PUT"])
def api_editar_demo(demo_id):
    if not obtener_demo_realizada(_db(), demo_id):
        return jsonify({"ok": False, "error": "Demo no encontrada"}), 404
    data = request.get_json() or {}
    campos = {k: data[k] for k in ("numero", "fecha", "actualizacion") if k in data}
    if "realizada_por" in data:
        valor, error = _usuario_valido(data["realizada_por"])
        if error:
            return jsonify({"ok": False, "error": f"realizada_por: {error}"}), 400
        campos["realizada_por"] = valor
    if not campos:
        return jsonify({"ok": False, "error": "Nada para actualizar"}), 400
    actualizar_demo_realizada(_db(), demo_id, **campos)
    return jsonify({"ok": True})


@preclientes_bp.route("/api/demos-realizadas/<int:demo_id>", methods=["DELETE"])
def api_borrar_demo(demo_id):
    if not obtener_demo_realizada(_db(), demo_id):
        return jsonify({"ok": False, "error": "Demo no encontrada"}), 404
    borrar_demo_realizada(_db(), demo_id)
    return jsonify({"ok": True})
