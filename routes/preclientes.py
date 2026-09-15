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

import io
import logging
import math

from flask import Blueprint, current_app, jsonify, request, send_file, session
from werkzeug.utils import secure_filename

from database import (ETAPAS_CLIENTE, ETAPAS_PRECLIENTE, actualizar_demo_realizada,
                      borrar_demo_realizada, borrar_presupuesto_demo,
                      crear_demo_realizada, get_business, get_user_by_id,
                      guardar_presupuesto_demo, insert_business,
                      listar_demos_realizadas, log_activity, obtener_demo_realizada,
                      obtener_presupuesto_demo, update_business)
from database import _connect as _db_connect
from services.finanzas import MONEDAS

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


# Tope de cordura, no de negocio: un desarrollo de mil millones es un error de
# tipeo (un cero de mas pegado dos veces), no un cliente.
_MONTO_MAXIMO = 1_000_000_000


def _monto_pagado_valido(data: dict):
    """Devuelve ({monto_pagado, moneda_pagado}, None) o (None, mensaje de error).

    Un monto vacio o null borra los dos campos: "no se cargo" no es 0. El 0 si
    se acepta, porque un desarrollo regalado o por canje es un dato real.

    La moneda sale de `services.finanzas.MONEDAS` para que Clientes y Finanzas
    no puedan divergir. No se convierte a dolares: se guarda lo que se acordo.
    """
    monto = data.get("monto")
    if monto is None or (isinstance(monto, str) and not monto.strip()):
        return {"monto_pagado": None, "moneda_pagado": None}, None

    # bool es subclase de int: sin esto, `true` se guardaba como 1.
    if isinstance(monto, bool):
        return None, "monto tiene que ser un número"
    try:
        monto = float(monto)
    except (TypeError, ValueError):
        return None, "monto tiene que ser un número"
    if not math.isfinite(monto):
        return None, "monto tiene que ser un número"
    if monto < 0:
        return None, "monto no puede ser negativo"
    if monto > _MONTO_MAXIMO:
        return None, "monto demasiado grande"

    moneda = data.get("moneda")
    if moneda not in MONEDAS:
        return None, f"moneda tiene que ser una de {list(MONEDAS)}"

    return {"monto_pagado": round(monto, 2), "moneda_pagado": moneda}, None


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
    """Los clientes que ya cerraron, con sus tres responsables resueltos a nombre
    y lo que pago cada uno por su desarrollo."""
    marcadores = ",".join("?" * len(ETAPAS_CLIENTE))
    conn = _db_connect(_db())
    try:
        filas = conn.execute(f"""
            SELECT b.id, b.name, b.crm_status, b.phone, b.email, b.city,
                   b.encargado_id, b.mantenimiento_id, b.cobros_id,
                   b.monto_pagado, b.moneda_pagado,
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


@preclientes_bp.route("/api/clientes-activos/<int:client_id>/monto-pagado", methods=["PUT"])
def api_monto_pagado(client_id):
    """Carga, corrige o borra cuanto pago el cliente. Body: {monto, moneda}.

    `monto` null o vacio deja los dos campos en NULL ("sin cargar")."""
    if not get_business(_db(), client_id):
        return jsonify({"ok": False, "error": "Cliente no encontrado"}), 404

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "monto" not in data:
        return jsonify({"ok": False, "error": "Nada para actualizar"}), 400

    campos, error = _monto_pagado_valido(data)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    update_business(_db(), client_id, **campos)
    return jsonify({"ok": True, **campos})


@preclientes_bp.route("/api/clientes-activos", methods=["POST"])
def api_crear_cliente():
    """Alta de un cliente directo en Clientes, sin pasar por la Cola.

    Body: {name, phone?, city?, crm_status?, monto?, moneda?}. Todo se valida
    antes de insertar: un monto mal escrito no puede dejar un cliente creado a
    medias."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}

    nombre = str(data.get("name") or "").strip()[:200]
    if not nombre:
        return jsonify({"ok": False, "error": "El nombre es obligatorio"}), 400

    etapa = data.get("crm_status") or ETAPAS_CLIENTE[0]
    if etapa not in ETAPAS_CLIENTE:
        return jsonify({"ok": False,
                        "error": f"crm_status tiene que ser una de {list(ETAPAS_CLIENTE)}"}), 400

    monto, error = _monto_pagado_valido(data)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    telefono = str(data.get("phone") or "").strip()[:40] or None
    ciudad = str(data.get("city") or "").strip()[:120] or None

    client_id = insert_business(_db(), {"name": nombre, "phone": telefono,
                                        "city": ciudad, "source": "manual"})
    if not client_id:
        # insert_business es INSERT OR IGNORE y `phone` es UNIQUE: el unico
        # motivo real para no insertar es un telefono que ya esta en el CRM.
        return jsonify({"ok": False,
                        "error": "Ya hay un negocio con ese teléfono en el CRM"}), 409

    update_business(_db(), client_id, crm_status=etapa, **monto)
    log_activity(_db(), session.get("user_name", "sistema"), "cliente_creado",
                 "lead", client_id, nombre, "Alta desde Clientes", user_id=_uid())
    return jsonify({"ok": True, "id": client_id, "crm_status": etapa, **monto}), 201


# ── Registro de demos ────────────────────────────────────────────────────────

@preclientes_bp.route("/api/demos-realizadas", methods=["GET"])
def api_listar_demos():
    client_id = request.args.get("client_id", type=int)
    limite = request.args.get("limite", type=int) or 200
    demos = listar_demos_realizadas(_db(), client_id=client_id, limite=limite)
    # El motivo de pérdida de las demos "no cerró" (Inteligencia financiera).
    from services.inteligencia_fin import motivos_por_entidad
    motivos = motivos_por_entidad(_db(), "demo")
    for d in demos:
        d["motivo_perdida"] = motivos.get(d["id"])
    return jsonify({"demos": demos})


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


# ── Presupuesto adjunto a cada demo ──────────────────────────────────────────
# Se guarda como BLOB en lead_attachments (con demo_id). Es aceptable por el
# tamaño real de los presupuestos (250 KB a 1,3 MB, ~50 por año) siempre que se
# cumplan tres cosas: tope de 10 MB validado aca, solo PDF o imagen, y que
# ningun listado traiga file_data (ver listar_demos_realizadas).

MAX_PRESUPUESTO_BYTES = 10 * 1024 * 1024

# El tipo se decide por los primeros bytes, no por lo que dice el navegador:
# un HTML renombrado a .pdf serviria como pagina desde el origen del CRM.
_FIRMAS = (
    (b"%PDF-", "application/pdf", ".pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
)


def _tipo_presupuesto(datos: bytes):
    """(mime, extension) si es PDF o imagen; None si no."""
    for firma, mime, ext in _FIRMAS:
        if datos.startswith(firma):
            return mime, ext
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def _demo_o_404(demo_id):
    demo = obtener_demo_realizada(_db(), demo_id)
    if not demo:
        return None, (jsonify({"ok": False, "error": "Demo no encontrada"}), 404)
    return demo, None


@preclientes_bp.route("/api/demos-realizadas/<int:demo_id>/presupuesto", methods=["POST"])
def api_adjuntar_presupuesto_demo(demo_id):
    demo, error = _demo_o_404(demo_id)
    if error:
        return error

    demasiado = jsonify({"ok": False, "error": "El archivo pesa más de 10 MB"}), 413
    # Cortar antes de parsear el multipart: con un archivo enorme, leerlo
    # entero para recien despues rechazarlo es justo el pico de memoria que
    # tumbo la maquina en septiembre. El margen es para los bordes del multipart.
    if request.content_length and request.content_length > MAX_PRESUPUESTO_BYTES + 64 * 1024:
        return demasiado

    archivo = request.files.get("file")
    if not archivo:
        return jsonify({"ok": False, "error": "Falta el archivo"}), 400
    datos = archivo.read(MAX_PRESUPUESTO_BYTES + 1)
    if len(datos) > MAX_PRESUPUESTO_BYTES:
        return demasiado
    if not datos:
        return jsonify({"ok": False, "error": "El archivo está vacío"}), 400

    tipo = _tipo_presupuesto(datos)
    if not tipo:
        return jsonify({"ok": False, "error": "Solo se aceptan PDF o imágenes"}), 400
    mime, ext = tipo

    nombre = secure_filename(archivo.filename or "") or "presupuesto"
    if not nombre.lower().endswith(ext) and not (ext == ".jpg" and nombre.lower().endswith(".jpeg")):
        nombre = nombre.rsplit(".", 1)[0] + ext

    adjunto_id = guardar_presupuesto_demo(_db(), demo_id, demo["client_id"], nombre, datos, mime)
    cliente = get_business(_db(), demo["client_id"]) or {}
    log_activity(_db(), session.get("user_name", "sistema"), "presupuesto_demo_adjuntado",
                 "lead", demo["client_id"], cliente.get("name", ""), nombre, user_id=_uid())
    return jsonify({"ok": True, "id": adjunto_id, "nombre": nombre}), 201


@preclientes_bp.route("/api/demos-realizadas/<int:demo_id>/presupuesto", methods=["GET"])
def api_descargar_presupuesto_demo(demo_id):
    # La unica ruta que lee el BLOB, y de a un archivo por pedido.
    fila = obtener_presupuesto_demo(_db(), demo_id)
    if not fila or not fila["file_data"]:
        return jsonify({"ok": False, "error": "Esta demo no tiene presupuesto"}), 404
    resp = send_file(io.BytesIO(fila["file_data"]),
                     mimetype=fila["mime_type"] or "application/octet-stream",
                     as_attachment=True, download_name=fila["name"] or "presupuesto")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@preclientes_bp.route("/api/demos-realizadas/<int:demo_id>/presupuesto", methods=["DELETE"])
def api_quitar_presupuesto_demo(demo_id):
    _, error = _demo_o_404(demo_id)
    if error:
        return error
    if not borrar_presupuesto_demo(_db(), demo_id):
        return jsonify({"ok": False, "error": "Esta demo no tiene presupuesto"}), 404
    return jsonify({"ok": True})
