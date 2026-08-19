"""Extraccion de direcciones de mail del HTML de un sitio."""

import sqlite3

import pytest

from database import init_db, insert_business
from services.email_finder import (buscar_mail_del_sitio, es_mail_basura,
                                   extraer_mails, procesar_pendientes)


@pytest.mark.parametrize("mail", [
    "usuario@dominio.com",
    "tuemail@tudominio.com",
    "ejemplo@example.com",
    "noreply@inmobiliaria.com.uy",
    "no-reply@inmobiliaria.com.uy",
    "info@sentry.wixpress.com",
    "logo@2x.png",
    "donotreply@banco.com.uy",
    "tucorreo@radio.com.uy",
])
def test_direcciones_basura(mail):
    """Mandarle a una direccion de ejemplo es dano puro a la reputacion.

    `usuario@dominio.com` no es hipotetico: aparecio en el sondeo, dejado en
    la plantilla de una inmobiliaria real.
    """
    assert es_mail_basura(mail) is True


@pytest.mark.parametrize("mail", [
    "info@imas.uy",
    "hola@acsa.uy",
    "contacto@diegoalfonso.com.uy",
    "ferraripropiedades@adinet.com.uy",
    "inmobiliaria.uru@gmail.com",
    "correo@estudio.com.uy",
    "email@empresa.com.uy",
])
def test_direcciones_buenas(mail):
    """Salidas reales del sondeo: ninguna se puede descartar."""
    assert es_mail_basura(mail) is False


@pytest.mark.parametrize("falso", [
    "icons@2.woff",
    "hero@3x.avif",
    "style.css@v1.2.min.css",
    "logo@2x.png",
    "sprite@2x.jxl",
    "bundle@1.0.0.js",
    "app@2.min.css",
])
def test_nombres_de_archivo_no_son_direcciones(falso):
    """El convenio retina `@2x` de los assets choca de frente con la sintaxis
    de mail: cualquier tema de WordPress o Wix produce `hero@3x.avif` en el
    markup. Si eso pasa el filtro, la fila queda con un `To:` invalido y con
    `status='email_found'`, o sea marcada como resuelta y nunca reintentada.
    """
    assert es_mail_basura(falso) is True


def test_extrae_del_mailto():
    html = '<a href="mailto:info@inmobiliaria.com.uy">Escribinos</a>'
    assert extraer_mails(html) == ["info@inmobiliaria.com.uy"]


def test_el_mailto_va_antes_que_el_texto_suelto():
    """Un mailto es una direccion que el dueno puso para que le escriban.
    Una suelta en el HTML puede ser cualquier cosa."""
    html = """
      <p>Escribile al contador: contador@estudio.com.uy</p>
      <a href="mailto:info@inmobiliaria.com.uy">Contacto</a>
    """
    assert extraer_mails(html)[0] == "info@inmobiliaria.com.uy"


def test_filtra_la_basura_del_html():
    html = """
      <a href="mailto:usuario@dominio.com">Mail</a>
      <p>info@inmobiliaria.com.uy</p>
    """
    assert extraer_mails(html) == ["info@inmobiliaria.com.uy"]


def test_no_repite_la_misma_direccion_con_otra_capitalizacion():
    html = """
      <a href="mailto:Info@Inmobiliaria.com.uy">Mail</a>
      <p>info@inmobiliaria.com.uy</p>
    """
    assert len(extraer_mails(html)) == 1


def test_sitio_sin_direcciones():
    assert extraer_mails("<html><body><p>Llamanos al 2900 1111</p></body></html>") == []


def test_ignora_el_query_del_mailto():
    html = '<a href="mailto:info@x.com.uy?subject=Consulta">Mail</a>'
    assert extraer_mails(html) == ["info@x.com.uy"]


def test_mailto_con_dos_destinatarios_se_parte_en_dos():
    """Lo del mailto es texto crudo. Guardar la cadena con la coma entera es un
    `To:` invalido, y ademas pierde la segunda direccion."""
    html = '<a href="mailto:ventas@x.com.uy,info@x.com.uy">Mail</a>'
    assert extraer_mails(html) == ["ventas@x.com.uy", "info@x.com.uy"]


def test_mailto_sin_comillas_conserva_la_prioridad():
    """Sin comillas es HTML valido; si solo lo encuentra el regex de texto
    suelto, el mail del pie de pagina le gana al que el dueno puso para que le
    escriban."""
    html = """
      <p>Web hecha por diseno@agencia.com.uy</p>
      <a href=mailto:info@inmo.com.uy>Contacto</a>
    """
    assert extraer_mails(html)[0] == "info@inmo.com.uy"


def test_mailto_con_la_arroba_codificada():
    html = '<a href="mailto:info%40inmo.com.uy">Mail</a>'
    assert extraer_mails(html) == ["info@inmo.com.uy"]


def _abrir_falso(paginas: dict):
    """Devuelve un `abrir` que sirve HTML de un diccionario url -> html."""
    def abrir(url):
        return paginas.get(url)
    return abrir


def test_encuentra_el_mail_en_la_home():
    abrir = _abrir_falso({
        "https://inmo.com.uy": '<a href="mailto:info@inmo.com.uy">Mail</a>',
    })
    assert buscar_mail_del_sitio(abrir, "https://inmo.com.uy") == ("info@inmo.com.uy", True)


def test_sigue_a_la_pagina_de_contacto_si_la_home_no_tiene():
    abrir = _abrir_falso({
        "https://inmo.com.uy": "<p>Bienvenidos</p>",
        "https://inmo.com.uy/contacto": '<a href="mailto:hola@inmo.com.uy">Mail</a>',
    })
    assert buscar_mail_del_sitio(abrir, "https://inmo.com.uy") == ("hola@inmo.com.uy", True)


def test_le_agrega_el_esquema_al_dominio_pelado():
    abrir = _abrir_falso({
        "https://inmo.com.uy": '<a href="mailto:info@inmo.com.uy">Mail</a>',
    })
    assert buscar_mail_del_sitio(abrir, "inmo.com.uy") == ("info@inmo.com.uy", True)


def test_las_rutas_de_contacto_cuelgan_del_origen_y_no_de_la_query():
    """El campo "sitio web" de una ficha de Google My Business muy seguido trae
    ?utm_source=gmb porque el dueno lo pego asi. Concatenar la ruta despues de
    la query daba un 404 y el job perdia las ocho rutas de contacto."""
    abrir = _abrir_falso({
        "https://www.inmo.com.uy/?utm_source=gmb": "<p>Bienvenidos</p>",
        "https://www.inmo.com.uy/contacto": '<a href="mailto:hola@inmo.com.uy">Mail</a>',
    })
    assert buscar_mail_del_sitio(
        abrir, "https://www.inmo.com.uy/?utm_source=gmb") == ("hola@inmo.com.uy", True)


def test_el_esquema_en_mayusculas_no_produce_una_url_imposible():
    """`HTTPS://Inmo.com.uy` no empieza con "http" en minusculas, asi que se le
    pegaba otro esquema adelante y la fila no abria nunca."""
    abrir = _abrir_falso({
        "https://Inmo.com.uy": '<a href="mailto:info@inmo.com.uy">Mail</a>',
    })
    assert buscar_mail_del_sitio(abrir, "HTTPS://Inmo.com.uy") == ("info@inmo.com.uy", True)


def test_sitio_que_no_abre_no_revienta():
    """Un dominio caido es lo normal en un padron raspado, no una excepcion.

    El segundo valor es False: no abrio ninguna pagina, o sea que no dio
    evidencia de que no publique direccion.
    """
    abrir = _abrir_falso({})
    assert buscar_mail_del_sitio(abrir, "https://caido.com.uy") == (None, False)


def test_sitio_que_abrio_y_no_publica_direccion():
    """Distinto del anterior: este si se pudo mirar, asi que es evidencia."""
    abrir = _abrir_falso({"https://mudo.com.uy": "<p>Llamanos al 2900 1111</p>"})
    assert buscar_mail_del_sitio(abrir, "https://mudo.com.uy") == (None, True)


def test_corta_apenas_encuentra_una_direccion():
    visitadas = []

    def abrir(url):
        visitadas.append(url)
        return '<a href="mailto:info@inmo.com.uy">Mail</a>'

    buscar_mail_del_sitio(abrir, "https://inmo.com.uy")
    assert len(visitadas) == 1, "con la home alcanzo, no hay que seguir visitando"


def _db_con(tmp_path, negocios):
    path = str(tmp_path / "discovery.db")
    init_db(path)
    for n in negocios:
        insert_business(path, n)
    return path


def test_guarda_el_mail_y_marca_el_negocio(tmp_path):
    db = _db_con(tmp_path, [{
        "name": "Inmo Uno", "phone": "+598 2900 0001",
        "maps_url": "https://maps.google.com/?cid=1",
        "website": "https://uno.com.uy", "source": "discovery",
    }])
    abrir = _abrir_falso({
        "https://uno.com.uy": '<a href="mailto:info@uno.com.uy">Mail</a>',
    })

    res = procesar_pendientes(db, abrir)

    assert res == {"revisados": 1, "con_mail": 1, "sin_mail": 0, "no_abrio": 0}
    conn = sqlite3.connect(db)
    fila = conn.execute("SELECT email, status FROM businesses").fetchone()
    conn.close()
    assert fila == ("info@uno.com.uy", "email_found")


def test_el_que_no_da_mail_queda_marcado_y_no_se_reintenta(tmp_path):
    db = _db_con(tmp_path, [{
        "name": "Inmo Dos", "phone": "+598 2900 0002",
        "maps_url": "https://maps.google.com/?cid=2",
        "website": "https://dos.com.uy", "source": "discovery",
    }])
    abrir = _abrir_falso({"https://dos.com.uy": "<p>Solo telefono</p>"})

    primera = procesar_pendientes(db, abrir)
    assert primera == {"revisados": 1, "con_mail": 0, "sin_mail": 1, "no_abrio": 0}

    segunda = procesar_pendientes(db, abrir)
    assert segunda["revisados"] == 0, "un sitio que no dio mail no se reintenta"


def test_no_toca_a_los_que_no_son_de_discovery(tmp_path):
    """El padron sin web y los leads de Meta no son asunto de este job.

    El tercer caso es el que importa: una fila del padron de WhatsApp que si
    tiene `website` cargado. Lo unico que la deja afuera es el `source`, y
    tocarle el email o el status a esa cohorte es el error mas caro del
    proyecto.
    """
    db = _db_con(tmp_path, [
        {"name": "Sin Web", "phone": "+598 2900 0003",
         "maps_url": "https://maps.google.com/?cid=3"},
        {"name": "Lead Meta", "phone": "+598 2900 0004",
         "maps_url": "https://maps.google.com/?cid=4",
         "website": "https://meta.com.uy", "source": "meta"},
        {"name": "Sin Source Con Web", "phone": "+598 2900 0009",
         "maps_url": "https://maps.google.com/?cid=9",
         "website": "https://padron.com.uy"},
    ])

    assert procesar_pendientes(db, _abrir_falso({}))["revisados"] == 0


def test_no_pisa_un_mail_que_ya_estaba(tmp_path):
    db = _db_con(tmp_path, [{
        "name": "Inmo Tres", "phone": "+598 2900 0005",
        "maps_url": "https://maps.google.com/?cid=5",
        "website": "https://tres.com.uy", "source": "discovery",
        "email": "yalotenia@tres.com.uy",
    }])

    assert procesar_pendientes(db, _abrir_falso({}))["revisados"] == 0


def test_una_excepcion_de_abrir_no_corta_la_tanda_ni_quema_la_fila(tmp_path):
    """Un padron raspado tiene sitios que hacen cosas raras: si `abrir` explota
    para uno, los demas negocios de la tanda igual se tienen que revisar.

    Y el que exploto NO queda marcado: una excepcion transitoria (DNS, timeout,
    certificado) es indistinguible de una permanente, asi que se reintenta. El
    error queda en la base para que la perdida deje rastro.
    """
    db = _db_con(tmp_path, [
        {"name": "Explota", "phone": "+598 2900 0006",
         "maps_url": "https://maps.google.com/?cid=6",
         "website": "https://explota.com.uy", "source": "discovery"},
        {"name": "Anda Bien", "phone": "+598 2900 0007",
         "maps_url": "https://maps.google.com/?cid=7",
         "website": "https://andabien.com.uy", "source": "discovery"},
    ])

    def abrir(url):
        if "explota" in url:
            raise TimeoutError("certificado vencido")
        return '<a href="mailto:info@andabien.com.uy">Mail</a>'

    res = procesar_pendientes(db, abrir)

    assert res == {"revisados": 2, "con_mail": 1, "sin_mail": 0, "no_abrio": 1}
    conn = sqlite3.connect(db)
    filas = {f[0]: f[1:] for f in conn.execute(
        "SELECT name, status, error_message FROM businesses").fetchall()}
    conn.close()
    assert filas["Explota"][0] != "no_email", "una excepcion no es evidencia de nada"
    assert "certificado vencido" in (filas["Explota"][1] or "")
    assert filas["Anda Bien"][0] == "email_found"


def test_el_sitio_que_no_abrio_se_reintenta_en_la_proxima_tanda(tmp_path):
    """El error mas caro del proyecto es perder un prospecto en silencio. Un
    sitio caido un rato no puede quedar marcado como resuelto para siempre."""
    db = _db_con(tmp_path, [{
        "name": "Caido", "phone": "+598 2900 0008",
         "maps_url": "https://maps.google.com/?cid=8",
        "website": "https://caido.com.uy", "source": "discovery",
    }])

    primera = procesar_pendientes(db, _abrir_falso({}))
    assert primera == {"revisados": 1, "con_mail": 0, "sin_mail": 0, "no_abrio": 1}

    segunda = procesar_pendientes(db, _abrir_falso({
        "https://caido.com.uy": '<a href="mailto:info@caido.com.uy">Mail</a>',
    }))
    assert segunda["con_mail"] == 1
    conn = sqlite3.connect(db)
    fila = conn.execute("SELECT email, status FROM businesses").fetchone()
    conn.close()
    assert fila == ("info@caido.com.uy", "email_found")


def test_la_tanda_se_corta_si_se_murio_el_browser(tmp_path):
    """`cmd_buscar_mails` usa una sola page para toda la tanda. Si se muere en
    el sitio 12 de 200, todos los goto siguientes fallan y sin este corte la
    corrida quemaria los 188 restantes en unos segundos.

    Las filas que no se llegaron a visitar tienen que quedar como estaban.
    """
    db = _db_con(tmp_path, [
        {"name": f"Inmo {i}", "phone": f"+598 2900 20{i:02d}",
         "maps_url": f"https://maps.google.com/?cid=2{i}",
         "website": f"https://inmo{i}.com.uy", "source": "discovery"}
        for i in range(15)
    ])

    res = procesar_pendientes(db, _abrir_falso({}), limite=15)

    assert res["revisados"] == 10, "se corta a los 10 seguidos sin abrir"
    assert res["no_abrio"] == 10
    conn = sqlite3.connect(db)
    estados = [f[0] for f in conn.execute("SELECT status FROM businesses").fetchall()]
    conn.close()
    assert "no_email" not in estados, "ninguna fila se quema por un browser muerto"


def test_un_sitio_bueno_reinicia_la_cuenta_de_sitios_que_no_abren(tmp_path):
    """El corte es por fallos *consecutivos*: un padron con dominios caidos
    salteados no puede abortar la tanda."""
    db = _db_con(tmp_path, [
        {"name": f"Inmo {i}", "phone": f"+598 2900 30{i:02d}",
         "maps_url": f"https://maps.google.com/?cid=3{i}",
         "website": f"https://inmo{i}.com.uy", "source": "discovery"}
        for i in range(12)
    ])
    paginas = {"https://inmo5.com.uy": '<a href="mailto:info@inmo5.com.uy">Mail</a>'}

    res = procesar_pendientes(db, _abrir_falso(paginas), limite=12)

    assert res["revisados"] == 12
    assert res["con_mail"] == 1


def test_respeta_el_limite(tmp_path):
    db = _db_con(tmp_path, [
        {"name": f"Inmo {i}", "phone": f"+598 2900 10{i:02d}",
         "maps_url": f"https://maps.google.com/?cid=1{i}",
         "website": f"https://inmo{i}.com.uy", "source": "discovery"}
        for i in range(5)
    ])

    assert procesar_pendientes(db, _abrir_falso({}), limite=2)["revisados"] == 2


def test_un_limite_negativo_no_procesa_la_cohorte_entera(tmp_path):
    """En SQLite LIMIT -1 significa SIN limite."""
    db = _db_con(tmp_path, [
        {"name": f"Inmo {i}", "phone": f"+598 2900 40{i:02d}",
         "maps_url": f"https://maps.google.com/?cid=4{i}",
         "website": f"https://inmo{i}.com.uy", "source": "discovery"}
        for i in range(3)
    ])

    assert procesar_pendientes(db, _abrir_falso({}), limite=-1)["revisados"] == 0
