"""Panel Agente de pauta: lo que hace el agente en Meta y el control del de marketing.

Pide el panel `pauta`. Quien lo ve puede frenar y reactivar al agente, aprobar,
rechazar y deshacer cambios y subir piezas. Solo un administrador fija el tope
del mes, cambia el modo, decide las subas del gasto total y deshace la pausa
por tope (todas suben el gasto o le sacan el freno).
"""

from flask import Blueprint, Response, current_app, jsonify, request, session

from services import pauta_agente as pa
from services import sombra_meta as sm
from services.auth import is_admin, require_panel

pauta_bp = Blueprint("pauta", __name__)

_MIMES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
          ".mp4": "video/mp4", ".mov": "video/quicktime"}


def _db() -> str:
    return current_app.config["DB_PATH"]


def _usuario() -> str:
    return str(session.get("user_name") or session.get("user_id") or "")


def _admin() -> bool:
    return is_admin(_db(), session.get("user_id"))


def _no(e: Exception, codigo: int = 400):
    return jsonify({"ok": False, "error": str(e)}), codigo


@pauta_bp.before_request
def _candado():
    return require_panel(_db(), "pauta")


@pauta_bp.route("/api/pauta/estado")
def api_estado():
    return jsonify({
        "ok": True,
        "agente": pa.estado(_db()),
        "nivel": pa.nivel(_db()),
        "encendido": pa.activo(_db()),
        "escritura": pa.escritura(_db()),
        "tope_mes": pa.tope_mes(_db()),
        "modo": pa.modo(_db()),
        "tope_cpl": sm.tope_cpl(_db()),
        "resumen": pa.resumen(_db()),
        "acciones": pa.listar(_db()),
        "piezas": pa.listar_piezas(_db()),
        "es_admin": _admin(),
    })


@pauta_bp.route("/api/pauta/frenar", methods=["POST"])
def api_frenar():
    pa.frenar(_db(), _usuario())
    return jsonify({"ok": True, "agente": pa.estado(_db())})


@pauta_bp.route("/api/pauta/reactivar", methods=["POST"])
def api_reactivar():
    pa.reactivar(_db(), _usuario())
    return jsonify({"ok": True, "agente": pa.estado(_db())})


@pauta_bp.route("/api/pauta/acciones/<int:acc_id>/deshacer", methods=["POST"])
def api_deshacer(acc_id):
    try:
        return jsonify({"ok": True, "accion": pa.deshacer(_db(), acc_id, _usuario(), _admin())})
    except pa.NoSePuede as e:
        return _no(e)


@pauta_bp.route("/api/pauta/acciones/<int:acc_id>/aprobar", methods=["POST"])
def api_aprobar(acc_id):
    try:
        return jsonify({"ok": True, "accion": pa.aprobar(_db(), acc_id, _usuario(), _admin())})
    except pa.NoSePuede as e:
        return _no(e)


@pauta_bp.route("/api/pauta/acciones/<int:acc_id>/rechazar", methods=["POST"])
def api_rechazar(acc_id):
    try:
        return jsonify({"ok": True, "accion": pa.rechazar(_db(), acc_id, _usuario(), _admin())})
    except pa.NoSePuede as e:
        return _no(e)


@pauta_bp.route("/api/pauta/tope", methods=["POST"])
def api_tope():
    if not _admin():
        return _no(Exception("El tope del mes lo fija un administrador"), 403)
    crudo = (request.get_json(silent=True) or {}).get("valor")
    if crudo in (None, ""):
        valor = None
    else:
        try:
            valor = round(float(str(crudo).replace(",", ".")), 2)
        except ValueError:
            return _no(Exception("El tope tiene que ser un número."))
        if not 10 <= valor <= 20000:
            return _no(Exception("El tope tiene que estar entre 10 y 20.000 USD."))
    pa.fijar_tope_mes(_db(), valor, _usuario())
    return jsonify({"ok": True, "tope_mes": valor})


@pauta_bp.route("/api/pauta/nivel", methods=["POST"])
def api_nivel():
    """Apagado / ensayo / encendido. Lo cambia un administrador, sin deploy."""
    if not _admin():
        return _no(Exception("Solo un administrador prende o apaga el agente"), 403)
    try:
        pa.fijar_nivel(_db(), (request.get_json(silent=True) or {}).get("valor", ""), _usuario())
    except pa.NoSePuede as e:
        return _no(e)
    return jsonify({"ok": True, "nivel": pa.nivel(_db())})


@pauta_bp.route("/api/pauta/modo", methods=["POST"])
def api_modo():
    if not _admin():
        return _no(Exception("El modo lo cambia un administrador"), 403)
    try:
        pa.fijar_modo(_db(), (request.get_json(silent=True) or {}).get("valor", ""), _usuario())
    except pa.NoSePuede as e:
        return _no(e)
    return jsonify({"ok": True, "modo": pa.modo(_db())})


@pauta_bp.route("/api/pauta/correr", methods=["POST"])
def api_correr():
    """Lee Meta y corre el agente ya (en ensayo salvo que este encendido)."""
    if not _admin():
        return _no(Exception("Solo un administrador lo corre a mano"), 403)
    r = pa.correr_ahora(_db())
    codigo = 429 if r["estado"] == "esperar" else (502 if r["estado"] == "error" else 200)
    return jsonify({"ok": r["estado"] in ("ok", "frenado"), **r}), codigo


@pauta_bp.route("/api/pauta/piezas", methods=["POST"])
def api_subir_pieza():
    archivo = request.files.get("archivo")
    if not archivo:
        return _no(Exception("Falta el archivo."))
    datos = archivo.read(pa.MAX_VIDEO + 1)
    try:
        pieza_id = pa.guardar_pieza(_db(), archivo.filename or "", datos,
                                    request.form.get("texto", ""),
                                    request.form.get("campana_id", ""), _usuario())
    except pa.NoSePuede as e:
        return _no(e)
    return jsonify({"ok": True, "id": pieza_id})


@pauta_bp.route("/api/pauta/piezas/<int:pieza_id>/archivo")
def api_archivo_pieza(pieza_id):
    pieza = pa.obtener_pieza(_db(), pieza_id)
    if not pieza:
        return _no(Exception("No existe"), 404)
    try:
        with open(pa.ruta_pieza(_db(), pieza), "rb") as fh:
            datos = fh.read()
    except OSError:
        return _no(Exception("No se encontró el archivo"), 404)
    return Response(datos, mimetype=_MIMES.get(pieza["extension"], "application/octet-stream"))
