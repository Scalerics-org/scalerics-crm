"""Extraccion de direcciones de mail del HTML de un sitio."""

import pytest

from services.email_finder import es_mail_basura, extraer_mails
import sqlite3

from database import init_db, insert_business
from services.email_finder import (RUTAS_CONTACTO, buscar_mail_del_sitio,
                                   procesar_pendientes)


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


def _abrir_falso(paginas: dict):
    """Devuelve un `abrir` que sirve HTML de un diccionario url -> html."""
    def abrir(url):
        return paginas.get(url)
    return abrir


def test_encuentra_el_mail_en_la_home():
    abrir = _abrir_falso({
        "https://inmo.com.uy": '<a href="mailto:info@inmo.com.uy">Mail</a>',
    })
    assert buscar_mail_del_sitio(abrir, "https://inmo.com.uy") == "info@inmo.com.uy"


def test_sigue_a_la_pagina_de_contacto_si_la_home_no_tiene():
    abrir = _abrir_falso({
        "https://inmo.com.uy": "<p>Bienvenidos</p>",
        "https://inmo.com.uy/contacto": '<a href="mailto:hola@inmo.com.uy">Mail</a>',
    })
    assert buscar_mail_del_sitio(abrir, "https://inmo.com.uy") == "hola@inmo.com.uy"


def test_le_agrega_el_esquema_al_dominio_pelado():
    abrir = _abrir_falso({
        "https://inmo.com.uy": '<a href="mailto:info@inmo.com.uy">Mail</a>',
    })
    assert buscar_mail_del_sitio(abrir, "inmo.com.uy") == "info@inmo.com.uy"


def test_sitio_que_no_abre_no_revienta():
    """Un dominio caido es lo normal en un padron raspado, no una excepcion."""
    abrir = _abrir_falso({})
    assert buscar_mail_del_sitio(abrir, "https://caido.com.uy") is None


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

    assert res == {"revisados": 1, "con_mail": 1, "sin_mail": 0}
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
    assert primera == {"revisados": 1, "con_mail": 0, "sin_mail": 1}

    segunda = procesar_pendientes(db, abrir)
    assert segunda["revisados"] == 0, "un sitio que no dio mail no se reintenta"


def test_no_toca_a_los_que_no_son_de_discovery(tmp_path):
    """El padron sin web y los leads de Meta no son asunto de este job."""
    db = _db_con(tmp_path, [
        {"name": "Sin Web", "phone": "+598 2900 0003",
         "maps_url": "https://maps.google.com/?cid=3"},
        {"name": "Lead Meta", "phone": "+598 2900 0004",
         "maps_url": "https://maps.google.com/?cid=4",
         "website": "https://meta.com.uy", "source": "meta"},
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


def test_una_excepcion_de_abrir_no_corta_la_tanda(tmp_path):
    """Un padron raspado tiene sitios que hacen cosas raras: si `abrir` explota
    para uno, los demas negocios de la tanda igual se tienen que revisar."""
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

    assert res == {"revisados": 2, "con_mail": 1, "sin_mail": 1}
    conn = sqlite3.connect(db)
    filas = dict(conn.execute("SELECT name, status FROM businesses").fetchall())
    conn.close()
    assert filas["Explota"] == "no_email"
    assert filas["Anda Bien"] == "email_found"


def test_respeta_el_limite(tmp_path):
    db = _db_con(tmp_path, [
        {"name": f"Inmo {i}", "phone": f"+598 2900 10{i:02d}",
         "maps_url": f"https://maps.google.com/?cid=1{i}",
         "website": f"https://inmo{i}.com.uy", "source": "discovery"}
        for i in range(5)
    ])

    assert procesar_pendientes(db, _abrir_falso({}), limite=2)["revisados"] == 2
