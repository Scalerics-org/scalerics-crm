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
from services import planos as pl

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
    sembrar_planos(db)


# Planos inventados para el Planificador. El token del de ejemplo es fijo para
# poder linkear la página del cliente desde la PR.
TOKEN_PLANO = "vista-previa-plano-clinica"


def sembrar_planos(db: str) -> None:
    conn = sqlite3.connect(db)
    try:
        if conn.execute("SELECT 1 FROM pl_planos LIMIT 1").fetchone():
            return
    finally:
        conn.close()
    clinica = pl.crear(db, {"nombre": "Clínica Dental Sonrisa", "rubro": "Odontología", "ciudad": "Salto",
                            "servicios": "Implantes\nOrtodoncia\nBlanqueamiento\nOdontopediatría",
                            "whatsapp": "099 000 500", "anios": 12, "dominio": "clinicasonrisa.com.uy",
                            "diferencial": "Atendemos los sábados y con las principales mutualistas"}, "Vista previa")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE pl_planos SET token = ? WHERE id = ?", (TOKEN_PLANO, clinica["id"]))
    conn.commit()
    conn.close()
    pl.marcar_enviado(db, clinica["id"])
    pl.responder(db, TOKEN_PLANO, "inicio", "aprobada", "", "Laura")
    pl.responder(db, TOKEN_PLANO, "equipo", "aprobada", "", "Laura")
    pl.responder(db, TOKEN_PLANO, "servicio-ortodoncia", "cambios",
                 "Hacemos ortodoncia invisible también, que es lo que más nos piden. ¿Lo pueden sumar?", "Laura")

    pl.crear(db, {"nombre": "Climatiza Norte", "rubro": "Aire acondicionado", "ciudad": "Maldonado",
                  "servicios": "Instalación\nService y limpieza\nVenta de equipos", "tono": "profesional"},
             "Vista previa")
    tienda = pl.crear(db, {"nombre": "Tienda Bambú", "rubro": "Ropa", "tipo": "ecommerce", "ciudad": "Colonia",
                           "servicios": "Remeras\nBuzos\nAccesorios"}, "Vista previa")
    pl.marcar_enviado(db, tienda["id"])
    conn = sqlite3.connect(db)
    token = conn.execute("SELECT token FROM pl_planos WHERE id = ?", (tienda["id"],)).fetchone()[0]
    conn.close()
    pl.responder(db, token, "*", "aprobada", "", "Martina")


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
