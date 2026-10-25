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
from services import qa_entrega as qa

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
    sembrar_qa(db)


# Control de calidad: sitios inventados (.example no existe) que se "revisan"
# con un sitio en memoria, así las revisiones salen del mismo código que en
# producción. "Revisar de nuevo" en la vista previa sí sale a internet: con
# un sitio real (scalerics.com, por ejemplo) se ve la revisión de verdad.
def _qa_html(nombre, cuerpo="", head="", anio=2026):
    return ("<!doctype html><html lang=\"es\"><head><title>" + nombre + "</title>"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<meta name=\"description\" content=\"" + nombre + " en Uruguay\">"
            "<meta property=\"og:image\" content=\"/og.jpg\"><link rel=\"icon\" href=\"/favicon.png\">"
            "<script src=\"https://www.googletagmanager.com/gtag/js?id=G-PREVIEW\"></script>" + head +
            "</head><body><h1>" + nombre + "</h1>" + cuerpo +
            "<a href=\"https://wa.me/59899000000\">WhatsApp</a><footer>© " + str(anio) + "</footer></body></html>")


def _qa_sitio(dominio, paginas, extra=None):
    base = "https://" + dominio
    mapa = {base + "/robots.txt": "User-agent: *\nDisallow: /wp-admin/\nSitemap: " + base + "/sitemap.xml"}
    mapa.update({base + camino: html for camino, html in paginas.items()})
    extra = extra or {}

    def obtener(url, metodo="GET"):
        if url in extra:
            return {"url": url, **extra[url]}
        if url.startswith("http://"):
            return {"codigo": 200, "url": "https://" + url[7:], "headers": {}}
        if url in mapa:
            return {"codigo": 200, "url": url, "texto": mapa[url], "ms": 380 + len(url) * 7, "headers": {}}
        if url.endswith((".jpg", ".png", ".webp")):
            return {"codigo": 200, "url": url, "headers": {"content-length": "180000"}}
        return {"codigo": 404, "url": url, "texto": "", "headers": {}}
    return obtener


def _qa_revisar(db, pid, url, obtener, quien, hace_horas, url_prueba=""):
    from datetime import timedelta
    r = qa.auditar(url, url_prueba, obtener=obtener, vence_ssl=lambda h: qa._ahora() + timedelta(days=70))
    r["segundos"] = 6.4
    qa.guardar_revision(db, pid, r, quien, en=qa._ahora() - timedelta(hours=hace_horas))


def sembrar_qa(db: str) -> None:
    if qa.listar(db):
        return
    clientes = {}
    conn = sqlite3.connect(db)
    try:
        clientes = dict(conn.execute("SELECT name, id FROM businesses").fetchall())
    finally:
        conn.close()
    menu = "<a href=\"/productos\">Productos</a><a href=\"/envios\">Envíos</a><a href=\"/contacto\">Contacto</a>"

    # Tienda Bambú: la primera revisión con lo de siempre, la segunda con casi todo arreglado.
    p = qa.crear(db, {"nombre": "Tienda Bambú", "tipo": "ecommerce", "url": "https://tiendabambu.example/",
                      "url_prueba": "https://tiendabambu.hostingersite.com",
                      "business_id": clientes.get("Tienda Bambú")}, "Vista previa")
    antes = _qa_sitio("tiendabambu.example", {
        "/": _qa_html("Inicio", menu + "<img src=\"/hero.jpg\"><p>Lorem ipsum dolor sit amet.</p>",
                      "<meta name=\"robots\" content=\"noindex, nofollow\">", anio=2025),
        "/productos": _qa_html("Productos · Tienda Bambú", "<a href=\"https://tiendabambu.hostingersite.com/"
                               "producto/remera\">Remera</a><a href=\"/ofertas\">Ofertas</a>"),
        "/envios": _qa_html("Envíos · Tienda Bambú", "<p>Haz clic en el botón Editar para cambiar este texto.</p>"),
        "/contacto": _qa_html("Contacto · Tienda Bambú", "<a href=\"https://wa.me/099 000 000\">Escribinos</a>"
                              "<form></form>"),
    }, extra={"https://tiendabambu.example/hero.jpg": {"codigo": 200, "headers": {"content-length": "3400000"}}})
    _qa_revisar(db, p["id"], p["url"], antes, "Vista previa", 30, p["url_prueba"])
    despues = _qa_sitio("tiendabambu.example", {
        "/": _qa_html("Tienda Bambú · Ropa en Colonia", menu + "<img src=\"/hero.webp\" alt=\"Colección\">"),
        "/productos": _qa_html("Productos · Tienda Bambú", "<a href=\"/producto/remera\">Remera</a>"
                               "<img src=\"/remera.webp\">"),
        "/producto/remera": _qa_html("Remera de lino · Tienda Bambú"),
        "/envios": _qa_html("Envíos · Tienda Bambú", "<p>Montevideo en 24 h, interior por DAC.</p>"),
        "/contacto": _qa_html("Contacto · Tienda Bambú", "<form></form>"),
    })
    _qa_revisar(db, p["id"], p["url"], despues, "Vista previa", 2, p["url_prueba"])
    for clave in ("form_probado", "wa_probado", "revisado_celular", "compra_prueba", "envios"):
        qa.marcar(db, p["id"], clave, True, "Vista previa")

    # Gimnasio Fuerza: listo para entregar.
    p = qa.crear(db, {"nombre": "Gimnasio Fuerza", "tipo": "web", "url": "https://gimnasiofuerza.example/",
                      "business_id": clientes.get("Gimnasio Fuerza")}, "Vista previa")
    sano = _qa_sitio("gimnasiofuerza.example", {
        "/": _qa_html("Gimnasio Fuerza · Canelones", "<a href=\"/clases\">Clases</a><a href=\"/planes\">Planes</a>"
                      ).replace("<meta property=\"og:image\" content=\"/og.jpg\">", ""),
        "/clases": _qa_html("Clases · Gimnasio Fuerza", "<form></form>"),
        "/planes": _qa_html("Planes y precios · Gimnasio Fuerza"),
    })
    _qa_revisar(db, p["id"], p["url"], sano, "Vista previa", 5)
    for i in qa.checklist_de("web"):
        if i["obligatorio"]:
            qa.marcar(db, p["id"], i["clave"], True, "Vista previa")
    qa.no_aplica(db, p["id"], "sin_og", "El cliente no comparte el sitio por redes", "Vista previa")

    # Óptica Central: recién pasado de prueba a definitivo, con bloqueantes.
    p = qa.crear(db, {"nombre": "Óptica Central", "tipo": "web", "url": "https://opticacentral.example/",
                      "url_prueba": "https://staging.opticacentral.example"}, "Vista previa")
    roto = _qa_sitio("opticacentral.example", {
        "/": _qa_html("Óptica Central", "<a href=\"/lentes\">Lentes</a><a href=\"/turnos\">Turnos</a>"
                      "<a href=\"https://instagram.com/opticacentral\">IG</a>",
                      "<link rel=\"canonical\" href=\"https://staging.opticacentral.example/\">"),
        "/lentes": _qa_html("Lentes · Óptica Central", "<img src=\"/armazon.jpg\"><img src=\"/sol.jpg\">"
                            "<a href=\"/marcas\">Marcas</a>"),
        "/turnos": _qa_html("Turnos · Óptica Central", "<b>Warning</b>: Undefined array key in "
                            "/home/u1/public_html/wp-content/plugins/agenda/agenda.php on line 88<form></form>"),
    }, extra={"https://instagram.com/opticacentral": {"codigo": 429, "headers": {}}})
    _qa_revisar(db, p["id"], p["url"], roto, "Vista previa", 1, p["url_prueba"])

    # Librería del Sur: cargada, sin revisar.
    qa.crear(db, {"nombre": "Librería del Sur", "tipo": "web", "url": "https://libreriadelsur.example/",
                  "business_id": clientes.get("Librería del Sur")}, "Vista previa")

    # Una automatización: va solo con checklist.
    p = qa.crear(db, {"nombre": "Recordatorios por WhatsApp · Veterinaria Patitas", "tipo": "automatizacion",
                      "business_id": clientes.get("Veterinaria Patitas")}, "Vista previa")
    qa.marcar(db, p["id"], "punta_a_punta", True, "Vista previa")
    qa.marcar(db, p["id"], "credenciales", True, "Vista previa")

    # Una ya entregada.
    p = qa.crear(db, {"nombre": "Panadería La Espiga", "tipo": "web", "url": "https://laespiga.example/",
                      "business_id": clientes.get("Panadería La Espiga")}, "Vista previa")
    _qa_revisar(db, p["id"], p["url"], _qa_sitio("laespiga.example", {
        "/": _qa_html("Panadería La Espiga · Montevideo", "<a href=\"/productos\">Productos</a>"),
        "/productos": _qa_html("Productos · La Espiga", "<form></form>")}), "Vista previa", 200)
    for i in qa.checklist_de("web"):
        if i["obligatorio"]:
            qa.marcar(db, p["id"], i["clave"], True, "Vista previa")
    qa.entregar(db, p["id"], "Vista previa")


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
