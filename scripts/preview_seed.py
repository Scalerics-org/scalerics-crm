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
from services import pedidos as pd

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
    sembrar_pedidos(db)


# Pizzería inventada para Pedidos directos. El slug y el código del pedido de
# ejemplo son fijos para poder linkearlos desde la PR.
CARTA = [
    ("Pizzas", [("Muzzarella", 420, "Salsa de tomate, muzzarella y aceitunas.", "Tamaño: chica, grande +240\nExtras (varios): huevo +60, jamón +90, morrones +70", True),
                ("Napolitana", 480, "Muzzarella, tomate en rodajas, ajo y perejil.", "Tamaño: chica, grande +260", False),
                ("Calabresa", 510, "Muzzarella y longaniza calabresa.", "Tamaño: chica, grande +260", False),
                ("Fugazzeta", 490, "Rellena de muzzarella, con cebolla.", "", False)]),
    ("Fainá y empanadas", [("Fainá", 110, "La porción.", "", False),
                           ("Empanada de carne", 95, "Cortada a cuchillo.", "", True),
                           ("Empanada de jamón y queso", 90, None, "", False),
                           ("Docena de empanadas", 990, "Elegí los gustos en la nota.", "", False)]),
    ("Postres", [("Flan casero", 190, "Con dulce de leche o crema.", "Con: dulce de leche, crema, mixto +40", False)]),
    ("Bebidas", [("Coca-Cola 1.5 L", 190, None, "", False), ("Agua 1.5 L", 120, None, "", False),
                 ("Cerveza Patricia 1 L", 260, None, "", False)]),
]
CLIENTES = ["Martín Silva", "Lucía Fernández", "Diego Rodríguez", "Valentina Pereira", "Santiago López",
            "Camila Méndez", "Bruno Castro", "Sofía Sosa", "Tomás García", "Florencia Núñez", "Federico Díaz",
            "Agustina Romero", "Joaquín Suárez", "Micaela Torres"]


def sembrar_pedidos(db: str) -> None:
    from datetime import timedelta

    conn = sqlite3.connect(db)
    try:
        if conn.execute("SELECT 1 FROM pd_locales LIMIT 1").fetchone():
            return
    finally:
        conn.close()
    loc = pd.crear_local(db, {
        "nombre": "Pizzería La Esquina", "slug": "vista-previa-pizzeria", "rubro": "Pizzería y empanadas",
        "direccion": "Av. Rivera 3100, Pocitos", "whatsapp": "099 000 111", "color": "#b91c1c",
        "horarios": "mar-dom 11:30-15:00 19:00-00:30", "modo": "abierto", "pedido_minimo": 500,
        "demora_delivery": 45, "demora_retiro": 25, "comision_app": 25,
        "pagos": ["efectivo", "transferencia", "tarjeta", "mercadopago"],
        "alias_transferencia": "BROU caja de ahorro 000000000-00001 (vista previa)",
        "link_mercadopago": "https://mpago.la/vista-previa",
        "mensaje": "Pedí directo y sin recargos. Los martes, 2x1 en muzzarella chica."}, "Vista previa")
    lid = loc["id"]
    for cat, prods in CARTA:
        cid = pd.guardar_categoria(db, lid, {"nombre": cat})["id"]
        for nombre, precio, desc, ops, dest in prods:
            pd.guardar_producto(db, lid, {"nombre": nombre, "precio": precio, "descripcion": desc, "opciones": ops,
                                          "categoria_id": cid, "destacado": dest})
    for nombre, costo, minimo in [("Pocitos", 0, 0), ("Punta Carretas", 60, 0), ("Buceo", 80, 0),
                                  ("Parque Batlle", 80, 0), ("Malvín", 120, 900)]:
        pd.guardar_zona(db, lid, {"nombre": nombre, "costo": costo, "minimo": minimo})
    loc = pd.local(db, lid)
    prods = {p["nombre"]: p for p in loc["productos"]}
    zonas = loc["zonas"]
    combos = [[("Muzzarella", 1, [(0, 1)]), ("Fainá", 2, [])], [("Napolitana", 1, [(0, 1)]), ("Coca-Cola 1.5 L", 1, [])],
              [("Docena de empanadas", 1, [])], [("Calabresa", 1, [(0, 0)]), ("Cerveza Patricia 1 L", 2, [])],
              [("Muzzarella", 2, [(0, 1), (1, 0)]), ("Flan casero", 2, [(0, 0)])],
              [("Empanada de carne", 4, []), ("Empanada de jamón y queso", 2, []), ("Agua 1.5 L", 1, [])],
              [("Fugazzeta", 1, []), ("Fainá", 1, []), ("Coca-Cola 1.5 L", 1, [])]]
    hoy = pd.hoy_uy()
    ahora = pd.ahora_uy()
    conn = sqlite3.connect(db)
    k = 0
    for dia in range(-34, 1):
        d = hoy + timedelta(days=dia)
        if d.weekday() == 0:
            continue                               # los lunes cierra
        cuantos = (6 if d.weekday() >= 4 else 3) + (k % 4) + (34 + dia) // 8
        for j in range(cuantos):
            k += 1
            hora = [12, 13, 20, 20, 21, 21, 21, 22, 22, 23][(k * 7 + j) % 10]
            creado = f"{d.isoformat()} {hora:02d}:{(k * 13) % 60:02d}"
            if dia == 0 and creado >= pd._txt(ahora - timedelta(minutes=90)):
                continue                           # lo de recién lo cargan los pedidos de ejemplo
            lineas = pd.leer_carrito(loc, "_".join(pd._token(prods[n]["id"], c, sel) for n, c, sel in combos[k % len(combos)]))
            tipo = "delivery" if k % 3 else "retiro"
            zona = zonas[k % len(zonas)] if tipo == "delivery" else None
            sub = sum(ln["subtotal"] for ln in lineas)
            envio = zona["costo"] if zona else 0
            estado = "cancelado" if k % 23 == 0 else "entregado"
            t = pd._dt(creado)
            pago = ["efectivo", "transferencia", "tarjeta", "mercadopago", "efectivo"][k % 5]
            cliente = CLIENTES[(k * 5) % len(CLIENTES)] if k % 4 else CLIENTES[k % 3]
            pid = conn.execute(
                "INSERT INTO pd_pedidos (local_id, numero, codigo, cliente, telefono, tipo, zona_id, zona, direccion, "
                "pago, subtotal, envio, total, estado, demora_min, motivo, creado_en, aceptado_en, listo_en, salio_en, "
                "entregado_en, cancelado_en, atendido_por) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (lid, j + 1, f"vista-previa-h{k:05d}", cliente, "59899" + str(100000 + (k * 5) % len(CLIENTES)),
                 tipo, zona["id"] if zona else None, zona["nombre"] if zona else None,
                 f"Calle Inventada {100 + k}" if zona else None, pago, sub, envio, sub + envio, estado, 40,
                 "No contestaba el teléfono" if estado == "cancelado" else None, creado,
                 pd._txt(t + timedelta(minutes=2 + k % 3)) if estado != "cancelado" else None,
                 pd._txt(t + timedelta(minutes=18 + k % 15)) if estado != "cancelado" else None,
                 pd._txt(t + timedelta(minutes=22 + k % 15)) if estado != "cancelado" and tipo == "delivery" else None,
                 pd._txt(t + timedelta(minutes=40 + k % 15)) if estado != "cancelado" else None,
                 creado if estado == "cancelado" else None, "Vista previa")).lastrowid
            conn.executemany(
                "INSERT INTO pd_items (pedido_id, producto_id, nombre, opciones, cantidad, precio_unit, subtotal) "
                "VALUES (?,?,?,?,?,?,?)",
                [(pid, ln["producto_id"], ln["nombre"], ln["opciones"] or None, ln["cantidad"], ln["precio_unit"],
                  ln["subtotal"]) for ln in lineas])
    conn.commit()
    conn.close()
    # Pedidos de recién, uno en cada columna del tablero, por el camino de verdad.
    ejemplos = [
        ("Lucía Fernández", "delivery", [("Muzzarella", 2, [(0, 1), (1, 1)]), ("Coca-Cola 1.5 L", 1, [])], "nuevo", "Tocar timbre 2, por favor."),
        ("Cliente de ejemplo", "delivery", [("Napolitana", 1, [(0, 1)]), ("Fainá", 2, [])], "preparando", None),
        ("Diego Rodríguez", "retiro", [("Docena de empanadas", 1, [])], "listo", "6 de carne y 6 de jamón y queso."),
        ("Valentina Pereira", "delivery", [("Calabresa", 1, [(0, 1)]), ("Flan casero", 2, [(0, 2)])], "en_camino", None),
        ("Bruno Castro", "retiro", [("Fainá", 3, [])], "nuevo", None),
    ]
    for i, (cliente, tipo, items, hasta, nota) in enumerate(ejemplos):
        c = "_".join(pd._token(prods[n]["id"], cant, sel) for n, cant, sel in items)
        p = pd.pedir(db, lid, c, {"cliente": cliente, "telefono": f"099 000 3{i:02d}", "tipo": tipo,
                                  "zona_id": zonas[1 + i % 3]["id"], "direccion": f"Calle Inventada {1200 + i}",
                                  "pago": ["efectivo", "transferencia", "mercadopago", "tarjeta", "efectivo"][i],
                                  "nota": nota})
        recorrido = ["preparando", "listo", "en_camino"]
        for e in recorrido[:recorrido.index(hasta) + 1] if hasta != "nuevo" else []:
            pd.avanzar(db, lid, p["id"], e, "Vista previa")
        conn = sqlite3.connect(db)
        conn.execute("UPDATE pd_pedidos SET creado_en = ?, codigo = ? WHERE id = ?",
                     (pd._txt(ahora - timedelta(minutes=[3, 18, 30, 45, 7][i])),
                      "vista-previa-pedido" if cliente == "Cliente de ejemplo" else f"vista-previa-hoy-{i}", p["id"]))
        conn.commit()
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
