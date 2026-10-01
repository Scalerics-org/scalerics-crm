"""El buscador de arriba del CRM (pedido de Juan, 1/10: "ya tenemos varias
cosas, agregá un buscador arriba para buscar en las secciones, sino es un
mareo").

Las secciones las encuentra el navegador solo, con el menú que ya ve cada uno.
Esta ruta suma lo que hay ADENTRO de las secciones: leads y clientes, comercios
de Fidelidad, fichas de Proceso de venta y tareas. Cada grupo sale solo si el
usuario tiene el panel donde vive (`tiene_panel`): el buscador no puede mostrar
lo que el menú le esconde.
"""

import sqlite3
import unicodedata

from flask import Blueprint, current_app, jsonify, request, session

from services.auth import tiene_panel

buscar_bp = Blueprint("buscar", __name__)

POR_GRUPO = 6


def _normal(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(t.split())


def _coincide(q: str, *campos) -> bool:
    texto = _normal(" ".join(str(c) for c in campos if c))
    return all(palabra in texto for palabra in q.split())


def _columnas(c: sqlite3.Connection, tabla: str) -> set:
    return {f[1] for f in c.execute(f"PRAGMA table_info({tabla})")}


@buscar_bp.route("/api/buscar")
def api_buscar():
    q = _normal(request.args.get("q"))
    if len(q) < 2:
        return jsonify({"grupos": []})
    db = current_app.config["DB_PATH"]
    uid = session.get("user_id")
    puede = lambda *paneles: any(tiene_panel(db, uid, p) for p in paneles)  # noqa: E731
    grupos = []
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        tablas = {f[0] for f in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

        if "businesses" in tablas and puede("clientes", "seg_leads"):
            cols = _columnas(c, "businesses")
            extra = [x for x in ("email", "city", "contact_name") if x in cols]
            filtro = "WHERE archivado_en IS NULL" if "archivado_en" in cols else ""
            items = []
            for f in c.execute(f"SELECT id, name, phone, crm_status{''.join(', ' + x for x in extra)} "
                               f"FROM businesses {filtro} ORDER BY id DESC"):
                f = dict(f)
                if _coincide(q, f["name"], f.get("phone"), *[f.get(x) for x in extra]):
                    cliente = f.get("crm_status") in ("cerrado", "en_desarrollo", "finalizado")
                    items.append({"id": f["id"], "titulo": f["name"],
                                  "detalle": ("Cliente" if cliente else "Lead") + (f" · {f['phone']}" if f.get("phone") else ""),
                                  "tipo": "negocio"})
                    if len(items) >= POR_GRUPO:
                        break
            if items:
                grupos.append({"nombre": "Leads y clientes", "items": items})

        if "fid_prospectos" in tablas and puede("cola"):
            items = []
            for f in c.execute("SELECT id, nombre, barrio, tipo, contacto, contacto_tel, telefono, email, estado "
                               "FROM fid_prospectos WHERE archivado = 0 ORDER BY id DESC"):
                if _coincide(q, f["nombre"], f["barrio"], f["contacto"], f["contacto_tel"], f["telefono"], f["email"]):
                    items.append({"id": f["id"], "titulo": f["nombre"],
                                  "detalle": " · ".join(x for x in (f["barrio"], f["tipo"]) if x),
                                  "tipo": "comercio"})
                    if len(items) >= POR_GRUPO:
                        break
            if items:
                grupos.append({"nombre": "Comercios (Fidelidad)", "items": items})

        if "notion_clients" in tablas and puede("notion_clients"):
            items = [{"id": f["id"], "titulo": f["name"], "detalle": f["status"] or "", "tipo": "proceso_venta"}
                     for f in c.execute("SELECT id, name, status, descripcion FROM notion_clients ORDER BY id DESC")
                     if _coincide(q, f["name"], f["descripcion"])][:POR_GRUPO]
            if items:
                grupos.append({"nombre": "Proceso de venta", "items": items})

        if "tasks" in tablas and puede("tasks"):
            items = [{"id": f["id"], "titulo": f["title"], "detalle": f["status"] or "", "tipo": "tarea"}
                     for f in c.execute("SELECT id, title, description, status FROM tasks ORDER BY id DESC")
                     if _coincide(q, f["title"], f["description"])][:POR_GRUPO]
            if items:
                grupos.append({"nombre": "Tareas", "items": items})
    finally:
        c.close()
    return jsonify({"grupos": grupos})
