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
from services import soporte as sp

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
    sembrar_soporte(db)


# Planes de mantenimiento inventados para la mesa de pedidos. Los códigos son
# fijos para poder linkear la página del cliente desde la PR (/pedidos/vista-previa-p1).
def sembrar_soporte(db: str) -> None:
    from datetime import timedelta

    conn = sqlite3.connect(db)
    try:
        if conn.execute("SELECT 1 FROM sp_planes LIMIT 1").fetchone():
            return
        clientes = dict(conn.execute("SELECT name, id FROM businesses").fetchall())
    finally:
        conn.close()
    ahora = sp._ahora()
    hoy = sp.hoy_uy()

    def plan(n, nombre, cliente, horas, precio, mensaje=None):
        p = sp.crear_plan(db, {"nombre": nombre, "business_id": clientes.get(cliente), "horas_mes": horas,
                               "precio_hora": precio, "mensaje": mensaje}, "Vista previa")
        c = sqlite3.connect(db)
        c.execute("UPDATE sp_planes SET codigo = ?, ultima_visita = ? WHERE id = ?",
                  ("vista-previa-p" + str(n), sp._txt(ahora - timedelta(hours=n * 5)), p["id"]))
        c.commit()
        c.close()
        return p["id"]

    def pedido(pid, horas_atras, titulo, descripcion, tipo="cambio", prioridad="normal", de_cliente=True):
        d = sp.crear_pedido(db, pid, {"titulo": titulo, "descripcion": descripcion, "tipo": tipo,
                                      "prioridad": prioridad}, "cliente" if de_cliente else "Vista previa",
                            de_cliente=de_cliente)
        c = sqlite3.connect(db)
        t = sp._txt(ahora - timedelta(hours=horas_atras))
        c.execute("UPDATE sp_pedidos SET creado_en = ?, actualizado_en = ? WHERE id = ?", (t, t, d["id"]))
        c.commit()
        c.close()
        return d["id"]

    def dice(did, texto, quien="Juan", de_cliente=False, **kw):
        sp.responder(db, did, texto, quien, de_cliente=de_cliente, **kw)

    def a_las(did, horas_despues):
        """Corre la respuesta (y el cierre, si lo hay) a unas horas de creado el pedido."""
        c = sqlite3.connect(db)
        creado = sp._dt(c.execute("SELECT creado_en FROM sp_pedidos WHERE id = ?", (did,)).fetchone()[0])
        t = sp._txt(creado + timedelta(hours=horas_despues))
        c.execute("UPDATE sp_mensajes SET creado_en = ? WHERE pedido_id = ?", (t, did))
        c.execute("UPDATE sp_pedidos SET primera_respuesta_en = ?, actualizado_en = ?, "
                  "resuelto_en = CASE WHEN resuelto_en IS NULL THEN NULL ELSE ? END, "
                  "esperando_desde = CASE WHEN esperando_desde IS NULL THEN NULL ELSE ? END WHERE id = ?",
                  (t, t, t, t, did))
        c.commit()
        c.close()

    def horas(did, h, dias_atras, nota):
        sp.cargar_horas(db, did, h, (hoy - timedelta(days=dias_atras)).isoformat(), nota, "Vista previa")

    # 1. Tienda con bolsa de 6 h: un urgente vencido, uno esperando al cliente y varios hechos.
    pid = plan(1, "Tienda online de Tienda Bambú", "Tienda Bambú", 6, 25,
               "¡Hola Ana! Desde acá nos pedís cualquier cambio en la tienda. El plan incluye 6 horas por mes.")
    pedido(pid, 6, "No se puede pagar con Mercado Pago",
           "Desde hoy a la mañana, cuando eligen Mercado Pago queda cargando y no pasa nada.", "error", "urgente")
    d = pedido(pid, 30, "Agregar la colección de verano", "Son 12 productos nuevos, te mando la planilla.",
               "contenido")
    dice(d, "¡Dale! ¿Nos mandás las fotos de los productos? En la planilla no vienen.", estado="esperando")
    a_las(d, 2)
    d = pedido(pid, 24 * 6, "Cambiar el banner de la home", "Que diga 20% off en remeras hasta el domingo.")
    dice(d, "Listo, ya está publicado.", estado="hecho")
    a_las(d, 5)
    horas(d, 1, 5, "Banner nuevo en la home")
    d = pedido(pid, 24 * 9, "El costo de envío a Colonia está mal", "Sale 190 y es 250.", "error")
    dice(d, "Corregido, gracias por avisar.", estado="hecho")
    a_las(d, 3)
    horas(d, 0.5, 8, "Tabla de envíos")
    d = pedido(pid, 24 * 4, "Sumar talles en las camperas", "Ahora tenemos XL y XXL.", "cambio")
    dice(d, "Ojo: las variaciones las maneja el plugin viejo, revisar antes.", interno=True)
    dice(d, "Hecho: ya aparecen los talles nuevos.", estado="hecho")
    a_las(d, 20)
    horas(d, 2.5, 3, "Variaciones de talle en 8 productos")

    # 2. Restaurante con 4 h que ya se pasó: horas extra para facturar.
    pid = plan(2, "Web de Bar El Puerto", "Bar El Puerto", 4, 20)
    d = pedido(pid, 24 * 12, "Menú de primavera", "Cambia todo el menú, te paso el PDF.", "contenido")
    dice(d, "Ya está el menú nuevo.", estado="hecho")
    a_las(d, 26)
    horas(d, 3, 11, "Carga del menú completo")
    d = pedido(pid, 50, "Reservas por la web", "¿Se puede que la gente reserve mesa desde la página?", "nuevo",
               "baja")
    dice(d, "Sí: armamos el formulario y te llega por mail. Te avisamos cuando esté.")
    a_las(d, 6)
    horas(d, 2.5, 1, "Formulario de reservas")
    pedido(pid, 3, "Cambiar el horario de los domingos", "Ahora abrimos 12 a 16.", "contenido", de_cliente=False)

    # 3. Óptica sin bolsa (por hora): recién empezó, un pedido nuevo sin responder.
    pid = plan(3, "Web de Óptica Central", "Óptica Central", 0, 25)
    pedido(pid, 20, "Agregar la marca Ray-Ban a la lista", "Y si se puede, con el logo.")


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
