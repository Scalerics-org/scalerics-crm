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
from database import add_attachment
from services import agentes as ag
from services import propuestas_online as po

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


PRESUPUESTO = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<style>body{{font-family:Arial,sans-serif;max-width:760px;margin:32px auto;padding:0 24px;color:#1c2b40}}
h1{{background:#0f1f3d;color:#fff;padding:24px;border-radius:8px}}h2{{color:#0088CC}}
.precio{{font-size:32px;font-weight:700;color:#0f1f3d}}</style></head><body>
<h1>Presupuesto · {negocio}</h1>
<h2>Alcance</h2><ul><li>Sitio web de 5 secciones, adaptado a celular</li>
<li>Botón de WhatsApp y formulario de contacto</li><li>SEO local en Google</li></ul>
<h2>Tiempo</h2><p>Entre 3 y 4 semanas.</p>
<h2>Inversión</h2><p class="precio">USD {monto}</p><p>50% al empezar, 50% al entregar.</p>
<p><i>Documento de ejemplo de la vista previa: datos inventados.</i></p></body></html>"""

# (negocio, monto, días desde que se mandó, aperturas, segundos, horas desde la última, cómo terminó)
PROPUESTAS = [
    ("Ferretería Norte", 1800, 2, 3, 420, 3, None),
    ("Óptica Central", 1200, 1, 0, 0, None, None),
    ("Veterinaria Patitas", 950, 6, 1, 45, 120, None),
    ("Librería del Sur", 1500, 9, 4, 600, 150, "aceptada"),
    ("Bar El Puerto", 2200, 12, 2, 90, 260, "rechazada"),
]


def _sembrar_propuestas(db: str) -> None:
    """Links de presupuesto en todos los estados, para ver el panel lleno."""
    from datetime import timedelta
    conn = sqlite3.connect(db)
    try:
        ids = dict(conn.execute("SELECT name, id FROM businesses"))
    finally:
        conn.close()
    ahora = po._ahora()
    for negocio, monto, dias, vistas, segundos, horas, fin in PROPUESTAS:
        html = PRESUPUESTO.format(negocio=negocio, monto=f"{monto:,}".replace(",", ".")).encode()
        aid = add_attachment(db, ids[negocio], "budget", "presupuesto.html",
                             file_data=html, mime_type="text/html")
        r = po.publicar(db, ids[negocio], html, f"Propuesta para {negocio}", "Vista previa",
                        attachment_id=aid, monto=monto)
        creado = ahora - timedelta(days=dias)
        # Código fijo: cada instancia de Vercel siembra su propia base, y el
        # link copiado en una tiene que abrir en otra.
        r["codigo"] = f"vista-previa-{ids[negocio]}"
        ultima = ahora - timedelta(hours=horas) if horas is not None else None
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "UPDATE propuestas_online SET codigo=?, creado_en=?, vence_en=?, vistas=?, segundos=?, "
                "primera_vista=?, ultima_vista=? WHERE id=?",
                (r["codigo"], po._txt(creado), po._txt(creado + timedelta(days=po.DIAS_VIGENCIA)), vistas, segundos,
                 po._txt(ultima - timedelta(hours=vistas)) if ultima else None,
                 po._txt(ultima) if ultima else None, r["id"]))
            conn.execute("UPDATE propuestas_online_eventos SET en=? WHERE propuesta_id=?",
                         (po._txt(creado), r["id"]))
            for i in range(vistas):
                conn.execute("INSERT INTO propuestas_online_eventos (propuesta_id, tipo, en) VALUES (?,?,?)",
                             (r["id"], "vista", po._txt(ultima - timedelta(hours=i))))
            conn.commit()
        finally:
            conn.close()
        p = po.por_codigo(db, r["codigo"])
        if fin == "aceptada":
            po.aceptar(db, p, "Laura Gómez", "laura@ejemplo.com", True)
        elif fin == "rechazada":
            po.rechazar(db, p, "Por ahora no tenemos presupuesto, lo vemos en marzo.")
        if fin:
            conn = sqlite3.connect(db)
            try:
                conn.execute("UPDATE propuestas_online SET resuelta_en=? WHERE id=?", (po._txt(ultima), r["id"]))
                conn.execute("UPDATE propuestas_online_eventos SET en=? WHERE propuesta_id=? AND tipo=?",
                             (po._txt(ultima), r["id"], fin))
                conn.commit()
            finally:
                conn.close()


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
    if not po.listar(db):
        _sembrar_propuestas(db)
    if not ag.listar(db):
        autor = {"id": None, "name": "Agente de ejemplo"}
        ag.guardar_brujula(db, "Vista previa: datos inventados.", "Vista previa")
        ag.proponer(db, autor, "crm", "Propuesta de ejemplo",
                    "Así se ve una propuesta en el panel Agentes de la vista previa.",
                    clave="preview-ejemplo")


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
