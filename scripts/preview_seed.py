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
from services import resenas as rs

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
    sembrar_resenas(db)


# Comercios inventados para Reseñas. El slug y el código de la opinión de
# ejemplo son fijos para poder linkearlos desde la PR.
COMENTARIOS_BAJOS = [
    ("Esperamos 50 minutos la comida y nadie nos avisó.", "Lucía", "099 000 401"),
    ("La carne vino fría y cuando lo dijimos nos pusieron mala cara.", "Martín", "099 000 402"),
    ("El baño estaba sucio.", None, None),
    ("Nos cobraron dos veces el postre.", "Carolina", "099 000 404"),
    ("Mucho ruido, no se podía hablar.", None, None),
]
COMENTARIOS_ALTOS = [
    "El asado espectacular y la atención de Rodrigo de diez.",
    "Volvemos seguro, la mollejita increíble.",
    "Rápidos y amables, aunque estaba lleno.",
    "Muy rico todo. Las papas fritas podrían venir más calientes.",
    None, None, None,
]


TOKEN_COMERCIO = "vista-previa-panel-del-comercio"


def sembrar_resenas(db: str) -> None:
    from datetime import timedelta

    conn = sqlite3.connect(db)
    try:
        if conn.execute("SELECT 1 FROM rs_negocios LIMIT 1").fetchone():
            return
    finally:
        conn.close()
    n = rs.crear_negocio(db, {"nombre": "Parrilla El Fogón", "slug": "vista-previa-parrilla", "rubro": "Restaurante",
                              "whatsapp": "099 000 400", "color": "#b45309",
                              "google_url": "https://search.google.com/local/writereview?placeid=ChIJvistaPreviaEjemplo"},
                         "Vista previa")
    nid = n["id"]
    fuentes = [rs.crear_fuente(db, nid, x)["id"] for x in ("Mostrador", "Mesas", "Ticket", "WhatsApp post-venta")]
    ahora = rs.ahora_uy()
    conn = sqlite3.connect(db)
    k = 0
    for dia in range(55, -1, -1):
        d = ahora - timedelta(days=dia)
        # Arranca de a poco y crece cuando se suman los QR de las mesas.
        cuantas = 1 + (k % 3) + (2 if dia < 35 else 0)
        for j in range(cuantas):
            k += 1
            puntaje = 5 if k % 3 else (4 if k % 7 else (k % 4) or 2)
            if k % 11 == 0:
                puntaje = 1
            fuente = fuentes[(k + j) % 4] if dia < 35 else fuentes[0]
            creado = d.replace(hour=13 + (k % 9) % 10, minute=(k * 7) % 60)
            comentario, nombre, contacto, quiere, estado, nota, resuelta = None, None, None, 0, "sin_accion", None, None
            if puntaje <= 3:
                comentario, nombre, contacto = COMENTARIOS_BAJOS[k % len(COMENTARIOS_BAJOS)]
                quiere = 1 if contacto else 0
                if dia > 6:
                    estado, nota = "resuelta", "Lo llamamos, pedimos disculpas y lo invitamos a volver."
                    resuelta = rs._txt(creado + timedelta(hours=3 + k % 20))
                else:
                    estado = "abierta" if k % 2 else "en_curso"
            else:
                comentario = COMENTARIOS_ALTOS[k % len(COMENTARIOS_ALTOS)]
            google = rs._txt(creado + timedelta(minutes=1)) if (puntaje >= 4 and k % 5 < 3) or (puntaje <= 3 and k % 4 == 0) else None
            conn.execute(
                "INSERT INTO rs_opiniones (negocio_id, fuente_id, codigo, puntaje, comentario, nombre, contacto, "
                "quiere_contacto, fue_a_google_en, estado, nota, atendido_por, resuelta_en, creado_en) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (nid, fuente, f"vista-previa-op-{k:04d}", puntaje, comentario, nombre, contacto, quiere, google, estado,
                 nota, "Vista previa" if nota or estado == "en_curso" else None, resuelta, rs._txt(creado)))
        # Abren más de los que puntúan: unas 3 vistas por opinión, distinto en cada QR.
        for i, fid in enumerate(fuentes if dia < 35 else fuentes[:1]):
            conn.execute("INSERT INTO rs_vistas (negocio_id, fuente_id, dia, n) VALUES (?,?,?,?)",
                         (nid, fid, d.date().isoformat(), 2 + i + (k + i) % 4 + (cuantas if i == 0 else 1)))
    for dias_atras, rating, cantidad in ((56, 4.1, 86), (28, 4.2, 104)):
        conn.execute("INSERT INTO rs_google (negocio_id, fecha, rating, cantidad, cargado_por) VALUES (?,?,?,?,?)",
                     (nid, (ahora - timedelta(days=dias_atras)).date().isoformat(), rating, cantidad, "Vista previa"))
    conn.commit()
    conn.close()
    # La opinión de ejemplo para la PR: una de 2 estrellas recién hecha, para ver la página de gracias.
    o = rs.opinar(db, nid, 2, "mesas", "vista-previa")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE rs_opiniones SET codigo = 'vista-previa-opinion-baja' WHERE id = ?", (o["id"],))
    conn.commit()
    conn.close()

    # El panel del dueño, con un link fijo para la PR. El token real sale al azar
    # y no se guarda; acá se guarda el hash de uno conocido.
    conn = sqlite3.connect(db)
    # Con avisos de quejas prendidos y un par ya mandados, para ver el historial.
    # La vista previa no tiene hilo de avisos ni RESEND_API_KEY: no sale nada.
    aid = conn.execute("INSERT INTO rs_accesos (negocio_id, nombre, token_hash, creado_por, creado_en, visto_en, "
                       "email, aviso, aviso_desde) VALUES (?,?,?,?,?,?,?,?,?)",
                       (nid, "Marcela (dueña)", rs._hash_token(TOKEN_COMERCIO), "Vista previa",
                        rs._txt(ahora - timedelta(days=20)), rs._txt(ahora - timedelta(days=3)),
                        "marcela@ejemplo.com", "al_momento", rs._txt(ahora - timedelta(days=20)))).lastrowid
    for dias, cant in ((6, 1), (2, 2)):
        conn.execute("INSERT INTO rs_avisos (acceso_id, tipo, cantidad, estado, creado_en) VALUES (?,?,?,?,?)",
                     (aid, "quejas", cant, "enviado", rs._txt((ahora - timedelta(days=dias)).replace(hour=13, minute=40))))
    conn.execute("INSERT INTO rs_accesos (negocio_id, nombre, token_hash, creado_por, creado_en, revocado_en) "
                 "VALUES (?,?,?,?,?,?)", (nid, "Encargado anterior", rs._hash_token("vista-previa-revocado-" + "x" * 10),
                                         "Vista previa", rs._txt(ahora - timedelta(days=50)),
                                         rs._txt(ahora - timedelta(days=21))))
    conn.commit()
    conn.close()

    sembrar_google(db, nid)

    p = rs.crear_negocio(db, {"nombre": "Peluquería Estilo", "slug": "vista-previa-peluqueria", "rubro": "Peluquería",
                              "pregunta": "¿Cómo quedó tu corte?"}, "Vista previa")
    rs.crear_fuente(db, p["id"], "Espejo")


# Lo que devolvería la Places API para la parrilla. La vista previa no tiene
# GOOGLE_PLACES_API_KEY: esto entra por el mismo guardar_lectura que la lectura
# real, así las respuestas sugeridas salen de las plantillas de verdad.
RESENAS_GOOGLE_EJEMPLO = (
    (2, "Sofía Rodríguez", 1, "Esperamos casi una hora el asado y cuando llegó estaba frío. El mozo ni se disculpó."),
    (5, "Martín Pereira", 0, "Excelente atención de las chicas y la carne en su punto. Volvemos seguro."),
    (4, "Lucía Fernández", 3, "Muy rica la comida, un poco caro el vino."),
    (5, "Diego Silva", 6, "El mejor asado de la zona, y rápido."),
    (3, "Ana Martínez", 9, "Bien, pero el baño estaba sucio."),
)


def sembrar_google(db: str, nid: int) -> None:
    from datetime import timedelta

    from services import resenas_google as rg

    ahora = rs.ahora_uy()
    datos = {"rating": 4.4, "userRatingCount": 131, "reviews": [
        {"name": f"places/ChIJvistaPreviaEjemplo/reviews/vista-previa-{i}", "rating": estrellas,
         "originalText": {"text": texto}, "authorAttribution": {"displayName": autor},
         "publishTime": (ahora - timedelta(days=dias, hours=2) - rs.UY).strftime("%Y-%m-%dT%H:%M:%SZ")}
        for i, (estrellas, autor, dias, texto) in enumerate(RESENAS_GOOGLE_EJEMPLO)]}
    n = rs.negocio(db, nid)
    rg.guardar_lectura(db, n, datos, redactar=None, ahora=ahora)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO rs_google_lecturas (negocio_id, tipo, estado, nuevas, creado_en) VALUES (?,?,?,?,?)",
                 (nid, "auto", "ok", len(RESENAS_GOOGLE_EJEMPLO), rs._txt(ahora.replace(hour=7, minute=12))))
    conn.commit()
    conn.close()
    for r in rg.resenas(db, nid, "todas"):
        if r["google_id"].endswith(("-2", "-3")):
            rg.marcar(db, nid, r["id"], "respondida", None, "Marcela (dueña) · comercio")
        elif r["google_id"].endswith("-4"):
            rg.marcar(db, nid, r["id"], "respondida", None, "Vista previa")


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
