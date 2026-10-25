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
from services import rentabilidad as rt

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
    sembrar_rentabilidad(db)


# Rentabilidad: un equipo y proyectos inventados. Las personas precargadas del
# organigrama no llevan horas acá, así ningún nombre real queda con un costo
# por hora, aunque sea inventado.
EQUIPO_PREVIEW = [("Lucía Ferrari", "Programadora", 620), ("Martín Acosta", "Programador", 520),
                  ("Sofía Benítez", "Diseñadora", 480), ("Diego Correa", "Project manager", 580)]

# (nombre, cliente, tipo, moneda, precio, horas estimadas, días desde el inicio,
#  días de plazo, factor de horas reales sobre las estimadas, estado)
PROYECTOS_PREVIEW = [
    ("Web institucional", "Estudio Contable Sur", "web", "UYU", 38000, 40, 170, 30, 0.9, "entregado"),
    ("Web con reservas", "Hotel Las Dunas", "web", "USD", 1400, 55, 150, 35, 1.25, "entregado"),
    ("Landing y formulario", "Gimnasio Fuerza", "web", "UYU", 22000, 22, 120, 14, 1.1, "entregado"),
    ("Web y blog", "Inmobiliaria Rambla", "web", "UYU", 45000, 48, 95, 30, 1.4, "entregado"),
    ("Tienda WooCommerce", "Tienda Bambú", "ecommerce", "USD", 2200, 90, 160, 45, 1.35, "entregado"),
    ("Tienda con Mercado Pago", "Librería del Sur", "ecommerce", "UYU", 85000, 80, 110, 40, 1.15, "entregado"),
    ("Bot de pedidos por WhatsApp", "Panadería La Espiga", "automatizacion", "UYU", 30000, 30, 130, 21, 0.85, "entregado"),
    ("Sync de stock con planilla", "Ferretería Norte", "automatizacion", "UYU", 26000, 25, 75, 14, 1.05, "entregado"),
    ("Sistema de turnos a medida", "Clínica Pocitos", "aMedida", "USD", 4800, 180, 140, 60, 1.2, "entregado"),
    ("Tienda de ropa", "Óptica Central", "ecommerce", "USD", 2000, 85, 30, 40, 1.1, "activo"),
    ("Web corporativa", "Bar El Puerto", "web", "UYU", 40000, 45, 20, 30, 0.85, "activo"),
    ("App de puntos", "Veterinaria Patitas", "aMedida", "USD", 3500, 140, 50, 45, 0.9, "activo"),
    ("Automatización de facturas", "Gimnasio Fuerza", "automatizacion", "UYU", 28000, 28, 12, 21, 0.35, "activo"),
]


def sembrar_rentabilidad(db: str) -> None:
    from datetime import timedelta

    conn = sqlite3.connect(db)
    try:
        if conn.execute("SELECT 1 FROM rt_proyectos LIMIT 1").fetchone():
            return
        conn.execute("UPDATE equipo_personas SET lleva_horas = 0")
        ids = []
        for nombre, rol, costo in EQUIPO_PREVIEW:
            conn.execute("INSERT OR IGNORE INTO equipo_personas (nombre, rol, lleva_horas, horas_por_dia, activo) "
                         "VALUES (?,?,1,6,1)", (nombre, rol))
            pid = conn.execute("SELECT id FROM equipo_personas WHERE nombre = ?", (nombre,)).fetchone()[0]
            conn.execute("UPDATE equipo_personas SET lleva_horas = 1, horas_por_dia = 6 WHERE id = ?", (pid,))
            conn.execute("INSERT OR REPLACE INTO rt_costos (persona_id, costo_hora) VALUES (?,?)", (pid, costo))
            ids.append((pid, rol))
        conn.commit()
    finally:
        conn.close()

    hoy = rt.hoy_uy()
    fases_por_rol = {"Diseñadora": ("diseno", "diseno", "correcciones"),
                     "Project manager": ("reuniones", "soporte", "correcciones"),
                     "Programadora": ("desarrollo", "desarrollo", "contenido", "correcciones"),
                     "Programador": ("desarrollo", "desarrollo", "desarrollo", "correcciones")}
    conn = sqlite3.connect(db)
    for n, (nombre, cliente, tipo, moneda, precio, est, hace, plazo, factor, estado) in enumerate(PROYECTOS_PREVIEW):
        inicio = hoy - timedelta(days=hace)
        entrega = inicio + timedelta(days=plazo)
        pid = rt.crear_proyecto(db, {"nombre": nombre, "cliente": cliente, "tipo": tipo, "moneda": moneda,
                                     "precio": precio, "horas_estimadas": est, "inicio": inicio.isoformat(),
                                     "entrega": entrega.isoformat(), "estado": "activo"}, "Vista previa")
        # Las horas se reparten en los días hábiles del plazo (o hasta hoy).
        fin = min(hoy, entrega + timedelta(days=int(plazo * max(factor - 1, 0)))) if estado == "activo" else \
            entrega + timedelta(days=int(plazo * max(factor - 1, 0)))
        dias = [inicio + timedelta(days=i) for i in range((fin - inicio).days + 1)
                if (inicio + timedelta(days=i)).weekday() < 5 and inicio + timedelta(days=i) <= hoy]
        objetivo = int(est * factor * 60)
        cargado = k = 0
        while dias and cargado < objetivo:
            d = dias[k % len(dias)]
            persona, rol = ids[(k * 3 + n) % len(ids)]
            fases = fases_por_rol[rol]
            fase = fases[(k + n) % len(fases)]
            # Los que se pasan de horas se pasan en correcciones.
            if factor > 1.2 and k % 4 == 0:
                fase = "correcciones"
            mins = min(objetivo - cargado, (90, 120, 150, 180, 240, 60)[(k + n) % 6])
            conn.execute("INSERT INTO rt_horas (proyecto_id, persona_id, fecha, minutos, fase, costo_hora, origen, "
                         "creado_por, creado_en) VALUES (?,?,?,?,?,(SELECT costo_hora FROM rt_costos WHERE persona_id = ?),"
                         "?,?,?)", (pid, persona, d.isoformat(), mins, fase, persona,
                                    "reloj" if k % 3 == 0 else "manual", "Vista previa", d.isoformat() + " 18:00"))
            cargado += mins
            k += 1
        if estado == "entregado":
            conn.execute("UPDATE rt_proyectos SET estado = 'entregado', cerrado_en = ? WHERE id = ?",
                         (max(dias).isoformat() if dias else entrega.isoformat(), pid))
        conn.commit()
    # Un reloj prendido para ver cómo se ve.
    activo = conn.execute("SELECT id FROM rt_proyectos WHERE estado = 'activo' ORDER BY id LIMIT 1").fetchone()
    if activo:
        desde = rt.ahora_uy() - timedelta(minutes=47)
        conn.execute("INSERT OR REPLACE INTO rt_relojes (persona_id, proyecto_id, fase, desde) VALUES (?,?,?,?)",
                     (ids[0][0], activo[0], "desarrollo", desde.isoformat(" ")))
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
