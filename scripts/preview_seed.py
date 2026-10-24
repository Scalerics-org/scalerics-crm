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
from services import monitor_sitios as ms

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
    sembrar_monitor(db)


# Sitios inventados del monitor: dominios .example, que no existen. La vista
# previa no sale a internet ("Revisar ahora" lo dice), así que el historial se
# arma acá: 30 días de chequeos cada 30 minutos.
MONITOR = [
    # nombre, dominio, plan, cliente, ms base, incidentes (días atrás, minutos), días de SSL
    ("Tienda Bambú", "tiendabambu.example", "mantenimiento", "Tienda Bambú", 650, [(12, 38)], 64),
    ("Gimnasio Fuerza", "gimnasiofuerza.example", "mantenimiento", "Gimnasio Fuerza", 420, [], 12),
    ("Librería del Sur", "libreriadelsur.example", "sin_plan", "Librería del Sur", 4600, [(3, 95), (20, 50)], 80),
    ("Óptica Central", "opticacentral.example", "mantenimiento", None, 900, [(0, None)], 45),
]


def sembrar_monitor(db: str) -> None:
    """De una, con una sola conexión: cada instancia de Vercel siembra al arrancar."""
    import random
    from datetime import timedelta

    conn = sqlite3.connect(db)
    try:
        if conn.execute("SELECT 1 FROM monitor_sitios LIMIT 1").fetchone():
            return
        clientes = dict(conn.execute("SELECT name, id FROM businesses").fetchall())
    finally:
        conn.close()
    azar = random.Random(24)
    ahora = ms._ahora().replace(second=0, microsecond=0)
    mes = ms.mes_actual(ahora)
    for i, (nombre, dominio, plan, cliente, base, incidentes, ssl_dias) in enumerate(MONITOR):
        s = ms.crear(db, {"nombre": nombre, "url": "https://" + dominio, "plan": plan,
                          "palabra": "Comprar" if i == 0 else "",
                          "business_id": clientes.get(cliente) if cliente else None}, "Vista previa")
        caidas = []
        for dias, minutos in incidentes:
            ini = ahora - timedelta(days=dias, hours=3 + i)
            caidas.append((ini, ini + timedelta(minutes=minutos) if minutos else None))
        chequeos, dias_ = [], {}
        t = ahora - timedelta(days=30)
        while t <= ahora:
            caido = any(a <= t and (b is None or t < b) for a, b in caidas)
            ms_ = None if caido else int(base * azar.uniform(.7, 1.4))
            chequeos.append((s["id"], ms._txt(t), 0 if caido else 1, 503 if caido else 200, ms_,
                             "Contestó 503" if caido else None))
            d = dias_.setdefault(ms._dia_mvd(t), [0, 0, 0, 0, 0])
            d[0] += 1
            d[1] += caido
            if ms_:
                d[2] += ms_
                d[3] += 1
                d[4] = max(d[4], ms_)
            t += timedelta(minutes=30)
        ultimo = chequeos[-1]
        conn = sqlite3.connect(db)
        try:
            conn.executemany("INSERT INTO monitor_chequeos (sitio_id, en, ok, codigo, ms, error) VALUES (?,?,?,?,?,?)",
                             chequeos)
            conn.executemany("INSERT INTO monitor_dias (sitio_id, dia, chequeos, fallas, ms_suma, ms_n, ms_max) "
                             "VALUES (?,?,?,?,?,?,?)", [(s["id"], k, *v) for k, v in dias_.items()])
            conn.executemany("INSERT INTO monitor_incidentes (sitio_id, empezo, termino, causa) VALUES (?,?,?,?)",
                             [(s["id"], ms._txt(a), ms._txt(b) if b else None, "Contestó 503") for a, b in caidas])
            conn.execute("""UPDATE monitor_sitios SET creado_en = ?, codigo = ?, ultimo_chequeo = ?, ultimo_ok = ?,
                            ultimo_ms = ?, ultimo_error = ?, fallas_seguidas = ?, ssl_vence = ?, ssl_revisado_en = ?
                            WHERE id = ?""",
                         (ms._txt(ahora - timedelta(days=31)), "vista-previa-" + str(i + 1), ultimo[1], ultimo[2],
                          ultimo[4], ultimo[5], 0 if ultimo[2] else 3, ms._txt(ahora + timedelta(days=ssl_dias)),
                          ultimo[1], s["id"]))
            conn.commit()
        finally:
            conn.close()
        if plan == "mantenimiento":
            ms.guardar_nota(db, s["id"], mes, "Actualizamos WordPress y 6 plugins.\nRenovamos el certificado SSL.\n"
                            "Cambiamos el banner de la home por la promo del mes.", "Vista previa")


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
