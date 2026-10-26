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
from services import diagnostico as dg

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
    sembrar_diagnosticos(db)


# Diagnósticos inventados: dominios .example, que no existen. El HTML se arma
# acá y pasa por el mismo `analizar()` que un sitio de verdad, así el informe de
# la vista previa es el que vería un comercio. El primero tiene código fijo,
# así el link de la PR no cambia.
DIAGNOSTICOS = [
    # nombre, dominio, origen, teléfono, mail, seguimiento, aperturas, ms, opciones del HTML
    ("Parrilla El Fogón", "parrillaelfogon.example", "web", "+59899111222", None, "nuevo", 3, 2300,
     dict(viewport=False, whatsapp=False, titulo="Inicio", relleno=True, anio=2021, wp="5.8", scripts=22, imgs=14)),
    ("Veterinaria Patitas", "veterinariapatitas.example", "web", None, "contacto@veterinariapatitas.example",
     "hablado", 1, 950, dict(whatsapp=False, titulo="Veterinaria Patitas | Clínica veterinaria en Pocitos",
                             descripcion=False, scripts=9, imgs=6)),
    ("Óptica Central", "opticacentral.example", "equipo", None, None, "propuesta", 0, 420,
     dict(titulo="Óptica Central · Lentes y armazones en Paysandú", jsonld=True, og=True, analitica=True, imgs=8)),
    ("Tienda Bambú", "tiendabambu.example", "web", "+59898333444", "hola@tiendabambu.example", "nuevo", 0, 380,
     dict(titulo="Tienda Bambú · Ropa de lino hecha en Colonia", jsonld=True, og=True, analitica=True, pixel=True,
          imgs=12, lazy=True)),
]


def html_de_ejemplo(nombre: str, o: dict) -> str:
    """Una página inventada con los defectos que se le pidan."""
    cab = ['<meta charset="utf-8">']
    if o.get("viewport", True):
        cab.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    cab.append(f"<title>{o.get('titulo', nombre)}</title>")
    if o.get("descripcion", True):
        cab.append(f'<meta name="description" content="{nombre}: atención de lunes a sábado, consultas por WhatsApp '
                   'y envíos a todo el país. Conocé lo que hacemos y escribinos.">')
    if o.get("wp"):
        cab.append(f'<meta name="generator" content="WordPress {o["wp"]}">')
    if o.get("og"):
        cab.append('<meta property="og:image" content="https://ejemplo.example/foto.jpg">')
    cab.append('<link rel="icon" href="/favicon.png">')
    if o.get("jsonld"):
        cab.append('<script type="application/ld+json">{"@context":"https://schema.org","@type":"LocalBusiness",'
                   f'"name":"{nombre}"}}</script>')
    cab += [f'<script src="/wp-content/plugins/p{i}.js"></script>' for i in range(o.get("scripts", 6))]
    if o.get("analitica"):
        cab.append('<script async src="https://www.googletagmanager.com/gtag/js?id=G-XXXX"></script>')
    if o.get("pixel"):
        cab.append("<script>!function(f,b,e,v,n,t,s){}(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');fbq('init','1');</script>")
    cuerpo = [f"<h1>{nombre}</h1>", "<p>Bienvenidos a nuestro sitio.</p>"]
    if o.get("relleno"):
        cuerpo.append("<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit.</p>")
    cuerpo += [f'<img src="/foto{i}.jpg"{" loading=lazy" if o.get("lazy") else ""}'
               f'{" width=800 height=600 alt=Foto" if i % 3 else ""}>' for i in range(o.get("imgs", 6))]
    if o.get("whatsapp", True):
        cuerpo.append('<a href="https://wa.me/59899000000">Escribinos por WhatsApp</a>')
    cuerpo += ['<a href="tel:+59842000000">Llamanos</a>', '<a href="https://instagram.com/ejemplo">Instagram</a>',
               '<form><input type="email" name="mail"><textarea name="consulta"></textarea></form>',
               '<iframe src="https://www.google.com/maps/embed?pb=x"></iframe>',
               f"<footer>© {o.get('anio', 2026)} {nombre}</footer>"]
    return f"<!doctype html><html lang=\"es\"><head>{''.join(cab)}</head><body>{''.join(cuerpo)}</body></html>"


def sembrar_diagnosticos(db: str) -> None:
    from datetime import timedelta

    conn = sqlite3.connect(db)
    try:
        if conn.execute("SELECT 1 FROM diagnosticos LIMIT 1").fetchone():
            return
        leads = dict(conn.execute("SELECT name, id FROM businesses").fetchall())
    finally:
        conn.close()
    ahora = dg._ahora()
    for i, (nombre, dominio, origen, tel, mail, seg, aperturas, ms, o) in enumerate(DIAGNOSTICOS):
        url = f"https://{dominio}/"
        pagina = {"url": url, "url_final": url, "html": html_de_ejemplo(nombre, o), "ms": ms,
                  "cabeceras": {} if i == 0 else {"content-encoding": "gzip"}, "sitemap": i > 1,
                  "http_a_https": i != 1}
        res = dg.analizar(pagina)
        d = dg._insertar(db, {"url": url, "nombre": nombre, "email": mail, "telefono": tel, "origen": origen,
                              "business_id": leads.get(nombre), "resultado": res,
                              "creado_por": "web" if origen == "web" else "Vista previa"})
        cuando = dg._txt(ahora - timedelta(days=i * 3, hours=i * 5))
        conn = sqlite3.connect(db)
        try:
            conn.execute("UPDATE diagnosticos SET seguimiento = ?, aperturas = ?, creado_en = ?, actualizado_en = ?, "
                         "ultima_apertura = ? WHERE id = ?",
                         (seg, aperturas, cuando, cuando, cuando if aperturas else None, d["id"]))
            if i == 0:
                conn.execute("UPDATE diagnosticos SET codigo = ? WHERE id = ?", ("vista-previa-diagnostico", d["id"]))
            conn.commit()
        finally:
            conn.close()
    # Uno que no abrió, para ver cómo queda.
    dg._insertar(db, {"url": "https://ferreterianorte.example/", "nombre": "Ferretería Norte", "origen": "equipo",
                      "business_id": leads.get("Ferretería Norte"), "resultado": None,
                      "error": "El sitio tardó demasiado en responder: un cliente se iría antes.",
                      "creado_por": "Vista previa"})


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
