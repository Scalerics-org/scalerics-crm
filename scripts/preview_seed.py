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
from services import materiales as mt

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
    sembrar_materiales(db)


# Portales de materiales inventados, uno por estado. Los códigos son fijos
# para poder linkear la página del cliente desde la PR (/m/vista-previa-m1).
_LOGO_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40"><rect width="120" height="40" '
             b'fill="#0088CC"/><text x="10" y="26" fill="#fff" font-size="16">Bambu</text></svg>')
_CATALOGO = ("nombre,precio,categoria,stock\nRemera lino,1290,Remeras,14\nPantalón cargo,2490,Pantalones,6\n"
             "Campera jean,3990,Abrigos,3\n").encode()


def _png() -> bytes:
    """Un PNG de 1x1 válido, para que haya fotos de ejemplo sin meter imágenes al repo."""
    import struct
    import zlib

    def trozo(tipo, datos):
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos))
    return (b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + trozo(b"IDAT", zlib.compress(b"\x00\x00\x88\xcc")) + trozo(b"IEND", b""))


def sembrar_materiales(db: str) -> None:
    import io
    from datetime import timedelta

    conn = sqlite3.connect(db)
    try:
        if conn.execute("SELECT 1 FROM mat_portales LIMIT 1").fetchone():
            return
        clientes = dict(conn.execute("SELECT name, id FROM businesses").fetchall())
    finally:
        conn.close()
    ahora = mt._ahora()

    def portal(n, nombre, tipo, cliente, dias, mensaje=None, fecha=None):
        p = mt.crear(db, {"nombre": nombre, "tipo": tipo, "business_id": clientes.get(cliente),
                          "mensaje": mensaje, "fecha_limite": fecha}, "Vista previa")
        c = sqlite3.connect(db)
        c.execute("UPDATE mat_portales SET codigo = ?, creado_en = ? WHERE id = ?",
                  ("vista-previa-m" + str(n), mt._txt(ahora - timedelta(days=dias)), p["id"]))
        c.execute("UPDATE mat_eventos SET en = ? WHERE portal_id = ?",
                  (mt._txt(ahora - timedelta(days=dias)), p["id"]))
        c.commit()
        c.close()
        return p["id"], {it["titulo"]: it["id"] for it in mt.items(db, p["id"])}

    def manda(pid, iid, texto=None, archivos=()):
        mt.enviar(db, pid, iid, "cliente", texto=texto, archivos=[(n, io.BytesIO(b)) for n, b in archivos])

    def cuando(pid, dias):
        c = sqlite3.connect(db)
        t = mt._txt(ahora - timedelta(days=dias, hours=3))
        c.execute("UPDATE mat_portales SET ultimo_cliente = ?, ultima_visita = ? WHERE id = ?", (t, t, pid))
        c.execute("UPDATE mat_eventos SET en = ? WHERE portal_id = ? AND accion != 'creado'", (t, pid))
        c.commit()
        c.close()

    # 1. Para revisar: la tienda mandó casi todo, hay cosas aprobadas y un cambio pedido.
    pid, it = portal(1, "Tienda online de Tienda Bambú", "tienda", "Tienda Bambú", 6,
                     "¡Hola Ana! Acá va todo lo que necesitamos para arrancar la tienda. Podés mandarlo de a partes.",
                     (ahora + timedelta(days=4)).date().isoformat())
    manda(pid, it["Logo"], archivos=[("logo-bambu.svg", _LOGO_SVG)])
    mt.revisar(db, it["Logo"], "aprobar", "", "Vista previa")
    manda(pid, it["Colores y tipografía"], texto="Verde oliva y crema, como el local. La letra del cartel es redondita.")
    manda(pid, it["Catálogo de productos"], texto="Es lo que tenemos en el sistema, faltan las fotos de algunos.",
          archivos=[("catalogo-octubre.csv", _CATALOGO)])
    manda(pid, it["Fotos de productos"], archivos=[("remera-lino.png", _png()), ("pantalon-cargo.png", _png())])
    mt.revisar(db, it["Fotos de productos"], "pedir",
               "Faltan las fotos de la campera jean y las que mandaste salen chiquitas: ¿las tenés en más tamaño?",
               "Vista previa")
    manda(pid, it["Medios de pago"], texto="Tarjetas, Mercado Pago y transferencia.")
    mt.revisar(db, it["Medios de pago"], "aprobar", "", "Vista previa")
    cuando(pid, 1)

    # 2. Trabado: el gimnasio abrió el link una vez y nunca mandó nada.
    pid, _ = portal(2, "Web de Gimnasio Fuerza", "web", "Gimnasio Fuerza", 9)
    c = sqlite3.connect(db)
    c.execute("UPDATE mat_portales SET ultima_visita = ? WHERE id = ?", (mt._txt(ahora - timedelta(days=8)), pid))
    c.commit()
    c.close()

    # 3. Completo: todo lo obligatorio aprobado, listo para bajar el zip.
    pid, it = portal(3, "App de puntos de Librería del Sur", "puntos", "Librería del Sur", 12)
    manda(pid, it["Logo"], archivos=[("logo-libreria.svg", _LOGO_SVG)])
    manda(pid, it["Premios y puntos"], texto="Cada $100 suma 1 punto.\n50 puntos: un café.\n200 puntos: un libro a elección.")
    manda(pid, it["Sucursales"], texto="Sarandí 512, Rivera. Lunes a sábado de 9 a 19.")
    for titulo in ("Logo", "Premios y puntos", "Sucursales"):
        mt.revisar(db, it[titulo], "aprobar", "", "Vista previa")
    cuando(pid, 3)

    # 4. Esperando: recién creado, el cliente ya mandó una parte y está aprobada.
    pid, it = portal(4, "Bot de WhatsApp de Panadería La Espiga", "bot", "Panadería La Espiga", 2)
    manda(pid, it["Preguntas frecuentes"], texto="¿Hacen tortas por encargo? Sí, con 48 h.\n¿Tienen sin TACC? Los viernes.")
    mt.revisar(db, it["Preguntas frecuentes"], "aprobar", "", "Vista previa")
    cuando(pid, 1)


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
