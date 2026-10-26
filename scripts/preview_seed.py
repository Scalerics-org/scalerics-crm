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
from services import asistentes as asis

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
    sembrar_asistentes(db)


# Asistentes inventados. El token del de ejemplo es fijo para poder linkear el
# link de prueba desde la PR.
TOKEN_ASISTENTE = "vista-previa-asistente"

FICHAS_BARBERIA = {
    "ubicacion": "Estamos en 8 de Octubre 3456, casi Propios, frente a la plaza. Hay parada del 103 en la puerta.",
    "pago": "Efectivo, débito, crédito (hasta 3 cuotas sin recargo con OCA y Visa) y Mercado Pago.",
    "precios": "Corte $450 · Corte y barba $650 · Barba $300 · Niños hasta 12 años $350 · Color desde $900.",
    "reserva": "Reservás desde reservar.barberiadonjulio.uy (elegís barbero y hora) o por acá: decinos día y hora.",
    "cancelar": "Podés cancelar o cambiar el turno hasta 2 horas antes, sin costo. Avisanos por acá.",
    "servicios": "Corte, barba con toalla caliente, perfilado, color y tratamientos capilares. Un corte lleva 40 minutos.",
}


def sembrar_asistentes(db: str) -> None:
    conn = sqlite3.connect(db)
    try:
        if conn.execute("SELECT 1 FROM as_asistentes LIMIT 1").fetchone():
            return
    finally:
        conn.close()
    horario = {"0": "", "1": "10-20", "2": "10-20", "3": "10-20", "4": "10-20", "5": "10-20", "6": "9-14"}
    b = asis.crear(db, {"nombre": "Barbería Don Julio", "familia": "turnos", "humano": "Julio",
                        "horario": horario}, "Vista previa")
    aid = b["id"]
    conn = sqlite3.connect(db)
    conn.execute("UPDATE as_asistentes SET token = ? WHERE id = ?", (TOKEN_ASISTENTE, aid))
    conn.commit()
    conn.close()
    for f in b["fichas"]:
        if f["clave"] in FICHAS_BARBERIA:
            asis.guardar_ficha(db, aid, {**f, "contenido": FICHAS_BARBERIA[f["clave"]]}, f["id"])
    asis.guardar_ficha(db, aid, {"titulo": "Estacionamiento", "contenido": "Hay lugar en la calle y un parking en la esquina "
                                 "con 30 minutos gratis si sacás el ticket acá.",
                                 "variantes": "¿hay donde estacionar?\n¿tienen parking?\n¿dónde dejo el auto?"})
    asis.sugerir_pruebas(db, aid)
    # Charlas inventadas, para que haya números y "Sin respuesta".
    charlas = [("api", "+598 99 000 001", ["Hola", "¿a qué hora abren el sábado?", "gracias!"]),
               ("api", "+598 99 000 002", ["¿Cuánto sale corte y barba?", "¿aceptan tarjeta?"]),
               ("api", "+598 99 000 003", ["¿hacen depilación de cejas?"]),
               ("api", "+598 99 000 004", ["quiero sacar turno para mañana a las 5", "¿tienen gift card para regalar?"]),
               ("api", "+598 99 000 005", ["Tengo un reclamo, me cortaron mal"]),
               ("link", "vista-previa-charla-1", ["¿dónde quedan?", "¿hay donde estacionar?"])]
    for origen, quien, textos in charlas:
        for t in textos:
            asis.conversar(db, aid, t, origen, quien)
    asis.correr_pruebas(db, aid)
    asis.nueva_clave(db, aid)        # la clave no se muestra: en la vista previa solo importa que exista
    asis.conectar(db, aid, True)

    asis.crear(db, {"nombre": "Pizzería La Esquina", "familia": "gastronomia", "humano": "Marcela"}, "Vista previa")


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
