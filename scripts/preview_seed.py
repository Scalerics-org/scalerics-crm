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
from services import analitica as an

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
    sembrar_analitica(db)


# Sitios inventados de la analítica: dominios .example, que no existen. Las
# visitas se arman acá, 95 días para atrás. El primero tiene clave y código
# fijos, así los links de la PR (informe y página de prueba) no cambian.
ANALITICA = [
    # nombre, dominio, cliente, visitas por día, ticket, cierre %, páginas, días sin datos al final
    ("Barbería Don Julio", "barberiadonjulio.example", None, 45, 600, 60,
     ["/", "/servicios", "/precios", "/reservas", "/contacto"], 0),
    ("Tienda Bambú", "tiendabambu.example", "Tienda Bambú", 80, 1800, 25,
     ["/", "/tienda", "/producto/remera-lino", "/producto/vestido-flores", "/carrito", "/contacto"], 0),
    ("Gimnasio Fuerza", "gimnasiofuerza.example", "Gimnasio Fuerza", 25, None, None,
     ["/", "/planes", "/horarios", "/contacto"], 5),
    ("Óptica Central", "opticacentral.example", None, 0, None, None, ["/"], 0),
]
# canal, origen, peso, probabilidad de contacto de esa visita
CANALES = [("Google", "google.com", 40, .07), ("Directo", "", 18, .06), ("Instagram", "instagram.com", 16, .05),
           ("Google Ads", "google", 10, .11), ("Meta Ads", "facebook", 8, .06), ("Facebook", "facebook.com", 5, .04),
           ("WhatsApp", "whatsapp", 3, .09)]
CONTACTOS = [("whatsapp", 62), ("llamada", 18), ("formulario", 14), ("mail", 6)]
HORAS = [1, 1, 0, 0, 0, 0, 1, 2, 4, 7, 9, 10, 9, 8, 7, 7, 8, 9, 10, 11, 10, 8, 5, 2]


def sembrar_analitica(db: str) -> None:
    import json
    import random
    import secrets
    from datetime import timedelta

    conn = sqlite3.connect(db)
    try:
        if conn.execute("SELECT 1 FROM analitica_sitios LIMIT 1").fetchone():
            return
        clientes = dict(conn.execute("SELECT name, id FROM businesses").fetchall())
    finally:
        conn.close()
    azar = random.Random(26)
    hoy = an.hoy_mvd()
    ahora = an._ahora()
    canales = [c for c in CANALES for _ in range(c[2])]
    tipos = [t for t, peso in CONTACTOS for _ in range(peso)]
    horas = [h for h, peso in enumerate(HORAS) for _ in range(peso)]
    for i, (nombre, dominio, cliente, base, ticket, cierre, paginas, apagado) in enumerate(ANALITICA):
        s = an.crear(db, {"nombre": nombre, "dominios": dominio, "ticket_medio": ticket, "cierre_pct": cierre,
                          "business_id": clientes.get(cliente) if cliente else None}, "Vista previa")
        filas = []
        for atras in range(95, apagado - 1, -1) if base else []:
            dia = hoy - timedelta(days=atras)
            factor = (1.25 if dia.weekday() < 5 else .7) * (1 + (95 - atras) / 220) * azar.uniform(.75, 1.25)
            del_dia = []
            for _ in range(int(base * factor)):
                canal, origen, _, prob = azar.choice(canales)
                # Uno de cada cinco vuelve el mismo día: el mismo visitante.
                visitante = azar.choice(del_dia) if del_dia and azar.random() < .2 else secrets.token_hex(8)
                del_dia.append(visitante)
                movil = "movil" if azar.random() < .74 else "escritorio"
                campana = "promo-" + ("primavera" if atras < 45 else "invierno") if canal.endswith("Ads") else None
                hora = azar.choice(horas)
                if not atras:   # hoy: nada en horas que todavía no pasaron
                    hora = min(hora, max(0, int(an.dia_hora_mvd(ahora)) - 1))
                en = (f"{dia.isoformat()} {hora:02d}:{azar.randint(0, 59):02d}:00")
                # La hora va en Montevideo para el día y en UTC (3 horas más) para `en`.
                en = an._txt(datetime_desde(en) + timedelta(hours=3))
                entrada = azar.choice(paginas[:3])
                recorrido = [entrada] + [azar.choice(paginas) for _ in range(azar.choice([0, 0, 1, 1, 2, 3]))]
                for j, pag in enumerate(recorrido):
                    filas.append((s["id"], en, dia.isoformat(), "vista", pag, int(j == 0), canal, origen or None,
                                  campana, movil, visitante, None))
                if azar.random() < prob * (1.3 if "/contacto" in recorrido or "/precios" in recorrido else 1):
                    tipo = azar.choice(tipos)
                    filas.append((s["id"], en, dia.isoformat(), tipo, recorrido[-1], 0, canal, origen or None,
                                  campana, movil, visitante, "contacto" if tipo == "formulario" else None))
                if ticket and "/carrito" in recorrido and azar.random() < .45:
                    filas.append((s["id"], en, dia.isoformat(), "compra", "/finalizar-compra/pedido-recibido", 0,
                                  canal, origen or None, campana, movil, visitante, None))
        conn = sqlite3.connect(db)
        try:
            conn.executemany(
                "INSERT INTO analitica_eventos (sitio_id, en, dia, tipo, pagina, entrada, canal, origen, campana, "
                "dispositivo, visitante, detalle) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", filas)
            ultimo = max((f[1] for f in filas), default=None)
            conn.execute("UPDATE analitica_sitios SET creado_en = ?, ultimo_evento = ?, dominios = ? WHERE id = ?",
                         (an._txt(ahora - timedelta(days=96)), ultimo, json.dumps([dominio]), s["id"]))
            if i == 0:
                conn.execute("UPDATE analitica_sitios SET clave = ?, codigo = ? WHERE id = ?",
                             ("vista-previa-prueba", "vista-previa-analitica", s["id"]))
            conn.commit()
        finally:
            conn.close()


def datetime_desde(txt: str):
    from datetime import datetime
    return datetime.strptime(txt, "%Y-%m-%d %H:%M:%S")


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
