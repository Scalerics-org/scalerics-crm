"""Base de la vista previa: datos inventados para ver una PR funcionando.

La usa `server.py` cuando arranca con CRM_PREVIEW=1, en las vistas previas de
Vercel que publica `.github/workflows/preview.yml`. Nunca toca la base de
producción: arranca de una base vacía en /tmp, y cada instancia nueva de la
función la vuelve a sembrar.

Uso:  python -m scripts.preview_seed <ruta de la base>

Entra un solo usuario, admin, con PREVIEW_EMAIL (por defecto
preview@scalerics.com) y la contraseña de PREVIEW_PASSWORD. Sin contraseña no
siembra y sale con error: la vista previa es pública en internet y no puede
quedar con una clave por defecto.
"""

import os
import sqlite3
import sys

from werkzeug.security import generate_password_hash

from database import create_user, get_user_by_email, init_db
from services import agentes as ag
from services import seguimiento as seg

LARGO_MINIMO_CLAVE = 12

# Negocios inventados: ningún dato real de un lead sale a la vista previa.
LEADS = [
    ("Panadería La Espiga", "panaderia", "Montevideo", "demo_1"),
    ("Ferretería Norte", "ferreteria", "Salto", "presupuesto_enviado"),
    ("Óptica Central", "optica", "Paysandú", "demo_agendada"),
    ("Bar El Puerto", "bar", "Montevideo", "sin_contactar"),
    ("Veterinaria Patitas", "veterinaria", "Maldonado", "follow_up_1"),
    ("Librería del Sur", "libreria", "Rivera", "acepto"),
    ("Gimnasio Fuerza", "gimnasio", "Canelones", "cerrado"),
    ("Tienda Bambú", "ropa", "Colonia", "en_desarrollo"),
    ("Almacén Don Pepe", "almacen", "Florida", "en_desarrollo"),
]


def sembrar(db: str, email: str, clave: str) -> None:
    init_db(db)
    if not get_user_by_email(db, email):
        create_user(db, name="Vista previa", email=email, phone="000",
                    password_hash=generate_password_hash(clave))
    conn = sqlite3.connect(db)
    try:
        if not conn.execute("SELECT 1 FROM businesses LIMIT 1").fetchone():
            conn.executemany(
                "INSERT INTO businesses (name, category, city, crm_status, phone, source) "
                "VALUES (?,?,?,?,?,?)",
                [(n, r, c, e, "099000" + str(100 + i), "preview") for i, (n, r, c, e) in enumerate(LEADS)])
            conn.commit()
    finally:
        conn.close()
    if not ag.listar(db):
        autor = {"id": None, "name": "Agente de ejemplo"}
        ag.guardar_brujula(db, "Vista previa: datos inventados.", "Vista previa")
        ag.proponer(db, autor, "crm", "Propuesta de ejemplo",
                    "Así se ve una propuesta en el panel Agentes de la vista previa.",
                    clave="preview-ejemplo")

    _sembrar_seguimiento(db)


def _sembrar_seguimiento(db: str) -> None:
    """Un cliente que avanza en paralelo y otro con todo esperando, para ver
    las dos situaciones del panel Clientes > Seguimiento."""
    conn = sqlite3.connect(db)
    try:
        ids = dict(conn.execute("SELECT name, id FROM businesses WHERE crm_status IN "
                                "('cerrado','en_desarrollo')").fetchall())
        ya = conn.execute("SELECT 1 FROM seguimiento_frentes LIMIT 1").fetchone()
    finally:
        conn.close()
    if ya or not ids:
        return
    quien = "Vista previa"
    bambu = ids.get("Tienda Bambú")
    if bambu:
        seg.anotar(db, quien, bambu, resumen="Tienda online casi lista. Faltan las fotos de la "
                   "temporada; mientras tanto se arma la facturación.", nota="Reunión de avance")
        seg.guardar_frente(db, quien, bambu, titulo="Fotos de productos", estado="esperando_cliente",
                           espera="Fotos de los 40 productos de verano",
                           detalle="- Mandaron 12 de 40\n- Fondo blanco, 1200 px")
        seg.guardar_frente(db, quien, bambu, titulo="Facturación electrónica", estado="nos_toca",
                           proximo_paso="Pedir el certificado de la DGI y elegir proveedor")
        seg.guardar_frente(db, quien, bambu, titulo="Diseño de la home", estado="hecho")
    pepe = ids.get("Almacén Don Pepe")
    if pepe:
        seg.anotar(db, quien, pepe, resumen="Esperando los precios para cargar el catálogo.")
        seg.guardar_frente(db, quien, pepe, titulo="Lista de precios", estado="esperando_cliente",
                           espera="Excel con precios actualizados")
    gim = ids.get("Gimnasio Fuerza")
    if gim:
        seg.guardar_frente(db, quien, gim, titulo="Agenda de clases", estado="nos_toca",
                           proximo_paso="Probar reservas con el dueño")
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE seguimiento_frentes SET estado_desde = datetime('now','-23 days') "
                     "WHERE titulo = 'Fotos de productos'")
        conn.execute("UPDATE seguimiento_frentes SET estado_desde = datetime('now','-9 days') "
                     "WHERE titulo = 'Lista de precios'")
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    if len(sys.argv) != 2:
        print("uso: python -m scripts.preview_seed <ruta de la base>", file=sys.stderr)
        return 2
    clave = os.environ.get("PREVIEW_PASSWORD", "")
    if len(clave) < LARGO_MINIMO_CLAVE:
        print(f"PREVIEW_PASSWORD falta o tiene menos de {LARGO_MINIMO_CLAVE} caracteres: "
              "la vista previa no arranca sin una clave propia.", file=sys.stderr)
        return 1
    sembrar(sys.argv[1], os.environ.get("PREVIEW_EMAIL", "preview@scalerics.com"), clave)
    return 0


if __name__ == "__main__":
    sys.exit(main())
