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
from services import agenda as rv
from services import agentes as ag

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
    sembrar_agenda(db)


# Comercios inventados para la Agenda online. Los slugs y el código del turno
# de ejemplo son fijos para poder linkearlos desde la PR.
def sembrar_agenda(db: str) -> None:
    from datetime import timedelta

    conn = sqlite3.connect(db)
    try:
        if conn.execute("SELECT 1 FROM rv_negocios LIMIT 1").fetchone():
            return
    finally:
        conn.close()
    n = rv.crear_negocio(db, {"nombre": "Barbería Don Pepe", "slug": "vista-previa-barberia", "rubro": "Barbería",
                              "direccion": "Av. Brasil 2550, Pocitos", "whatsapp": "099 000 111", "color": "#1f2937",
                              "intervalo_min": 30, "anticipacion_min": 60, "cancelar_hasta_h": 3,
                              "mensaje": "Reservá tu turno en un minuto. Si llegás más de 10 minutos tarde, "
                                         "puede que tengamos que pasarte para después."}, "Vista previa")
    nid = n["id"]
    servs = [rv.guardar_servicio(db, nid, {"nombre": a, "duracion_min": b, "precio": c, "descripcion": d})["id"]
             for a, b, c, d in [("Corte", 30, 600, "Con lavado y peinado."), ("Corte y barba", 60, 900, None),
                                ("Barba", 30, 400, "Perfilado con navaja y toalla caliente."),
                                ("Corte niños", 30, 450, "Hasta 12 años.")]]
    profs = [rv.guardar_profesional(db, nid, {"nombre": a, "horarios": h})["id"] for a, h in [
        ("Pepe", "lun-vie 10:00-13:00 15:00-20:00; sáb 09:00-14:00"),
        ("Nico", "mar-sáb 11:00-20:00")]]
    profs.append(rv.guardar_profesional(db, nid, {"nombre": "Sofi", "horarios": "lun-vie 14:00-20:00",
                                                  "servicios": [servs[0], servs[3]]})["id"])

    trabaja = {p["id"]: p["horarios"] for p in rv.negocio(db, nid)["profesionales"]}

    def en_horario(pid, d, ini, fin):
        return any(h["dia"] == d.weekday() and h["desde"] <= ini and fin <= h["hasta"] for h in trabaja[pid])

    hoy = rv.hoy_uy()
    clientes = ["Martín Silva", "Lucas Fernández", "Diego Rodríguez", "Facundo Pereira", "Santiago López",
                "Joaquín Méndez", "Bruno Castro", "Mateo Sosa", "Tomás García", "Agustín Núñez", "Federico Díaz",
                "Gonzalo Romero"]
    conn = sqlite3.connect(db)
    k = 0
    for dia in range(-20, 8):
        d = hoy + timedelta(days=dia)
        for pid, horas in zip(profs, (("10:00", "11:00", "15:30", "17:00", "18:30"), ("11:30", "13:00", "16:00", "19:00"),
                                      ("14:00", "16:30", "18:00"))):
            for j, h in enumerate(horas):
                k += 1
                if (k * 7 + dia) % 5 == 0:
                    continue
                sid = servs[(k + j) % 4] if pid != profs[2] else servs[0 if k % 3 else 3]
                dur = 60 if sid == servs[1] else 30
                ini = f"{d.isoformat()} {h}"
                fin_m = int(h[:2]) * 60 + int(h[3:]) + dur
                fin = f"{d.isoformat()} {fin_m // 60:02d}:{fin_m % 60:02d}"
                if conn.execute("SELECT 1 FROM rv_turnos WHERE profesional_id = ? AND inicio < ? AND fin > ? "
                                "AND estado != 'cancelado'", (pid, fin, ini)).fetchone():
                    continue
                if not en_horario(pid, d, ini[11:], fin[11:]):
                    continue
                if dia < 0:
                    estado = "falto" if k % 11 == 0 else ("cancelado" if k % 13 == 0 else "asistio")
                else:
                    estado = "cancelado" if k % 17 == 0 else "confirmado"
                precio = {servs[0]: 600, servs[1]: 900, servs[2]: 400, servs[3]: 450}[sid]
                creado = d - timedelta(days=1 + k % 4)
                hora_c = (7, 10, 13, 22, 23, 18)[k % 6]
                conn.execute(
                    "INSERT INTO rv_turnos (negocio_id, profesional_id, servicio_id, inicio, fin, cliente, telefono, "
                    "precio, estado, codigo, origen, creado_por, creado_en, recordado_en) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (nid, pid, sid, ini, fin, clientes[k % len(clientes)], "59899" + str(100000 + k), precio, estado,
                     "vista-previa-turno-" + str(k).zfill(4), "equipo" if k % 4 == 0 else "link",
                     "Vista previa" if k % 4 == 0 else "cliente", f"{creado.isoformat()} {hora_c:02d}:15",
                     f"{d.isoformat()} 09:00" if dia < 0 or (dia == 0 and k % 2) else None))
    conn.commit()
    conn.close()
    # El turno de ejemplo para la PR: pasado mañana, con link para cambiar o cancelar.
    t = rv.reservar(db, nid, {"servicio_id": servs[1], "profesional_id": profs[0],
                              "fecha": (hoy + timedelta(days=2 if (hoy + timedelta(days=2)).weekday() < 5 else 4)).isoformat(),
                              "hora": "12:00", "cliente": "Cliente de ejemplo", "telefono": "099 000 222",
                              "nota": "Primera vez que vengo."}, origen="equipo", quien="Vista previa")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE rv_turnos SET codigo = 'vista-previa-turno-ejemplo', origen = 'link' WHERE id = ?", (t["id"],))
    conn.commit()
    conn.close()
    rv.crear_bloqueo(db, nid, {"desde": (hoy + timedelta(days=5)).isoformat(), "profesional_id": profs[1],
                               "motivo": "Curso de barbería"})

    v = rv.crear_negocio(db, {"nombre": "Veterinaria Patitas", "slug": "vista-previa-veterinaria",
                              "rubro": "Veterinaria", "direccion": "Maldonado", "whatsapp": "099 000 333",
                              "color": "#0f766e", "intervalo_min": 20, "anticipacion_min": 120}, "Vista previa")
    for a, b, c in [("Consulta general", 20, 900), ("Vacunación", 20, 700), ("Baño y corte", 60, 1200)]:
        rv.guardar_servicio(db, v["id"], {"nombre": a, "duracion_min": b, "precio": c})
    rv.guardar_profesional(db, v["id"], {"nombre": "Dra. Paula", "horarios": "lun-vie 09:00-12:00 16:00-20:00"})


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
