"""Registro, baja, elegibilidad y tanda de la campana de discovery.

El error caro de esta campana es escribirle a alguien de otra cohorte —sobre
todo a un lead de Meta, que recibe correo real todos los dias— o dos veces a la
misma persona. Casi todos los tests de aca son sobre eso.
"""

import sqlite3
from unittest.mock import patch

import pytest

from database import init_db, insert_business
from services import discovery_emails
from services.discovery_emails import (comercios_a_contactar, comercios_a_seguir,
                                       dar_de_baja, enviados_ultimas_24h,
                                       enviar_discovery, esta_dado_de_baja,
                                       registrar_envio, start_discovery_emails)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "discovery.db")
    init_db(path)
    return path


def _comercio(db, n, **extra):
    datos = {
        "name": f"Inmobiliaria {n}",
        "category": "Inmobiliaria",
        "phone": f"+598 2900 {n:04d}",
        "maps_url": f"https://maps.google.com/?cid={n}",
        "website": f"https://inmo{n}.com.uy",
        "email": f"info@inmo{n}.com.uy",
        "source": "discovery",
    }
    datos.update(extra)
    return insert_business(db, datos)


def _envio(db, bid, numero, dias_atras=0):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO discovery_reminders (business_id, numero, token, sent_at) "
        "VALUES (?, ?, ?, datetime('now', ?))",
        (bid, numero, f"tok-{bid}-{numero}", f"-{int(dias_atras)} days"),
    )
    conn.commit()
    conn.close()


# ─── Registro y baja ─────────────────────────────────────────────────────────

def test_registrar_envio_devuelve_un_token(db):
    token = registrar_envio(db, _comercio(db, 1), 1)
    assert token and len(token) > 20


def test_no_se_puede_mandar_dos_veces_el_mismo_contacto(db):
    """La garantia la da el UNIQUE, no el codigo que llama."""
    bid = _comercio(db, 2)
    registrar_envio(db, bid, 1)
    with pytest.raises(sqlite3.IntegrityError):
        registrar_envio(db, bid, 1)


def test_el_mismo_comercio_puede_recibir_los_dos_contactos(db):
    bid = _comercio(db, 3)
    assert registrar_envio(db, bid, 1) != registrar_envio(db, bid, 2)


def test_la_baja_vale_para_toda_la_secuencia(db):
    bid = _comercio(db, 4)
    assert dar_de_baja(db, registrar_envio(db, bid, 1)) is True
    assert esta_dado_de_baja(db, bid) is True


def test_un_token_que_no_existe_no_revienta(db):
    """La ruta /baja le pasa a esta campana tokens que son de Meta."""
    assert dar_de_baja(db, "token-de-otra-campana") is False


def test_la_baja_es_idempotente(db):
    token = registrar_envio(db, _comercio(db, 5), 1)
    assert dar_de_baja(db, token) is True
    assert dar_de_baja(db, token) is True


# ─── A quien le toca, y a quien NO ───────────────────────────────────────────

def test_un_comercio_nuevo_entra_como_contacto_1(db):
    _comercio(db, 10)
    elegidos = comercios_a_contactar(db, limite=10)
    assert len(elegidos) == 1
    assert elegidos[0]["numero"] == 1


def test_no_toca_al_padron_sin_web(db):
    """source IS NULL es el padron de WhatsApp: no es de esta campana."""
    _comercio(db, 11, source=None, website=None)
    assert comercios_a_contactar(db, limite=10) == []


def test_no_toca_a_los_leads_de_meta(db):
    """Reciben una secuencia propia con correo real. Es el error mas caro."""
    _comercio(db, 12, source="meta")
    assert comercios_a_contactar(db, limite=10) == []


def test_no_le_escribe_a_quien_ya_se_toco(db):
    """Cualquier crm_status distinto de sin_contactar significa que alguien ya
    hablo con ese comercio.

    El estado se cambia con update_business y no en el insert a proposito:
    insert_business arma su lista de columnas a mano y NO incluye crm_status,
    asi que pasarlo ahi se pierde en silencio. Es el mismo patron que ya mordio
    con `email` y con `website`.
    """
    from database import update_business

    bid = _comercio(db, 13)
    update_business(db, bid, crm_status="interesado")

    assert comercios_a_contactar(db, limite=10) == []


def test_no_le_escribe_a_quien_no_tiene_mail(db):
    _comercio(db, 14, email=None)
    _comercio(db, 15, email="   ")
    assert comercios_a_contactar(db, limite=10) == []


def test_dos_sucursales_con_la_misma_direccion_reciben_una_sola_vez(db):
    """Medido en la primera corrida real: 13 mails, 12 direcciones unicas."""
    _comercio(db, 16, email="info@imas.uy")
    _comercio(db, 17, email="INFO@IMAS.UY")

    elegidos = comercios_a_contactar(db, limite=10)

    assert len(elegidos) == 1, "la misma persona recibiria el mismo mail dos veces"


def test_no_le_escribe_a_quien_comparte_direccion_con_un_lead_de_meta(db):
    _comercio(db, 18, source="meta", email="duenio@inmo.com.uy")
    _comercio(db, 19, email="Duenio@Inmo.com.uy")
    assert comercios_a_contactar(db, limite=10) == []


def test_el_que_ya_recibio_no_vuelve_a_entrar_como_contacto_1(db):
    bid = _comercio(db, 20)
    _envio(db, bid, 1)
    assert comercios_a_contactar(db, limite=10) == []


def test_el_seguimiento_espera_los_dias_que_corresponden(db):
    """El corte esta en el dia exacto: a los 6 no, a los 7 si."""
    seis = _comercio(db, 21)
    _envio(db, seis, 1, dias_atras=6)
    assert comercios_a_seguir(db, limite=10) == []

    siete = _comercio(db, 22)
    _envio(db, siete, 1, dias_atras=7)
    elegidos = comercios_a_seguir(db, limite=10)
    assert [e["id"] for e in elegidos] == [siete]
    assert elegidos[0]["numero"] == 2


def test_el_segundo_es_el_ultimo_de_la_vida(db):
    bid = _comercio(db, 23)
    _envio(db, bid, 1, dias_atras=90)
    _envio(db, bid, 2, dias_atras=80)
    assert comercios_a_seguir(db, limite=10) == []


def test_el_que_se_dio_de_baja_no_recibe_el_seguimiento(db):
    bid = _comercio(db, 24)
    dar_de_baja(db, registrar_envio(db, bid, 1))
    conn = sqlite3.connect(db)
    conn.execute("UPDATE discovery_reminders SET sent_at = datetime('now','-30 days')")
    conn.commit()
    conn.close()
    assert comercios_a_seguir(db, limite=10) == []


def test_una_baja_en_meta_saca_al_comercio_de_discovery(db):
    """Quien dijo basta, dijo basta, y no le importa por cual campana le
    estabamos escribiendo."""
    from services.meta_reminders import dar_de_baja as baja_meta
    from services.meta_reminders import registrar_envio as envio_meta

    bid_meta = _comercio(db, 25, source="meta", email="basta@inmo.com.uy")
    baja_meta(db, envio_meta(db, bid_meta, 1))

    _comercio(db, 26, email="basta@inmo.com.uy")

    assert comercios_a_contactar(db, limite=10) == []


def test_enviados_ultimas_24h_cuenta_solo_la_ventana(db):
    _envio(db, _comercio(db, 27), 1, dias_atras=0)
    _envio(db, _comercio(db, 28), 1, dias_atras=3)
    assert enviados_ultimas_24h(db) == 1


# ─── La tanda ────────────────────────────────────────────────────────────────

def test_el_tope_diario_es_de_50(db):
    """El numero sale de la reputacion, no de la cuota: con una semana de envio
    limpio el subdominio banca 50. Ver el comentario en discovery_emails."""
    for i in range(40, 110):
        _comercio(db, i)
    with patch("services.discovery_emails.send_discovery_email", return_value="ok"):
        res = enviar_discovery(db, "https://crm")
    assert res["enviados"] == 50


def test_los_seguimientos_van_antes_que_los_nuevos(db):
    for i in range(60, 75):
        _comercio(db, i)
    viejo = _comercio(db, 90)
    _envio(db, viejo, 1, dias_atras=10)

    mandados = []

    def fake(to, negocio, rubro, url, numero=1):
        mandados.append((to, numero))
        return "ok"

    with patch("services.discovery_emails.send_discovery_email", side_effect=fake):
        enviar_discovery(db, "https://crm")

    assert ("info@inmo90.com.uy", 2) in mandados
    assert mandados[0][1] == 2, "el seguimiento tiene que salir primero"


def test_el_dry_run_no_escribe_ni_manda(db):
    _comercio(db, 100)
    with patch("services.discovery_emails.send_discovery_email") as enviar:
        res = enviar_discovery(db, "https://crm", dry_run=True)
        enviar.assert_not_called()
    assert res["candidatos"] == 1 and res["enviados"] == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM discovery_reminders").fetchone()[0] == 0
    conn.close()


def test_el_dry_run_lista_igual_con_el_cupo_agotado(db):
    """Es la herramienta de diagnostico mas segura: tiene que servir justo
    cuando el cupo esta en 0, que en produccion es casi todo el dia."""
    # Un cupo entero en la ventana: el cupo real queda en 0. Se lee del modulo
    # a proposito, para que subir el tope no vuelva a romper este test.
    tope = discovery_emails._TOPE_DIARIO
    for i in range(200, 200 + tope):
        _envio(db, _comercio(db, i), 1, dias_atras=0)
    assert enviados_ultimas_24h(db) == tope

    # Y un comercio que todavia no recibio nada.
    _comercio(db, 120)

    real = enviar_discovery(db, "https://crm")
    assert real["candidatos"] == 0, "la tanda real si respeta el cupo"

    seco = enviar_discovery(db, "https://crm", dry_run=True)
    assert seco["candidatos"] == 1, "el dry-run lista igual"


def test_un_fallo_borra_solo_ese_contacto(db):
    """El token del contacto 1 ya viaja dentro de un mail que alguien recibio."""
    bid = _comercio(db, 110)
    _envio(db, bid, 1, dias_atras=10)
    with patch("services.discovery_emails.send_discovery_email", return_value="fallo"):
        enviar_discovery(db, "https://crm")
    conn = sqlite3.connect(db)
    numeros = [r[0] for r in conn.execute(
        "SELECT numero FROM discovery_reminders WHERE business_id = ? ORDER BY numero", (bid,))]
    conn.close()
    assert numeros == [1]


def test_un_estado_desconocido_deja_la_fila_puesta(db):
    """Pudo haber salido: reintentar significaria mandar dos veces."""
    bid = _comercio(db, 115)
    with patch("services.discovery_emails.send_discovery_email", return_value="desconocido"):
        res = enviar_discovery(db, "https://crm")
    assert res["inciertos"] == 1
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT COUNT(*) FROM discovery_reminders WHERE business_id = ?", (bid,)
    ).fetchone()[0] == 1
    conn.close()


def test_el_hilo_no_arranca_sin_el_interruptor(monkeypatch, caplog):
    import logging
    monkeypatch.delenv("DISCOVERY_EMAILS", raising=False)
    with caplog.at_level(logging.INFO):
        start_discovery_emails(object())
    assert "apagado" in caplog.text.lower()


# ─── Lo que la revision encontro abierto ─────────────────────────────────────

def test_el_seguimiento_no_sale_si_el_comercio_se_volvio_lead_de_meta(db):
    """Entre el contacto 1 y el 2 pasan siete dias, y en esos siete dias el
    duenio puede llenar el formulario del anuncio. Si eso pasa, esa persona
    recibe la secuencia de Meta: el segundo mail en frio no puede salir."""
    bid = _comercio(db, 300, email="duenio@inmo.com.uy")
    _envio(db, bid, 1, dias_atras=10)
    assert len(comercios_a_seguir(db, limite=10)) == 1, "sanity: sin Meta, sale"

    _comercio(db, 301, source="meta", email="Duenio@Inmo.com.uy")

    assert comercios_a_seguir(db, limite=10) == []


def test_una_baja_en_meta_corta_tambien_el_seguimiento(db):
    """Quien dijo basta por un link de Meta, dijo basta para todo."""
    from services.meta_reminders import dar_de_baja as baja_meta
    from services.meta_reminders import registrar_envio as envio_meta

    bid = _comercio(db, 310, email="basta2@inmo.com.uy")
    _envio(db, bid, 1, dias_atras=10)

    bid_meta = _comercio(db, 311, source="meta", email="basta2@inmo.com.uy")
    baja_meta(db, envio_meta(db, bid_meta, 1))

    assert comercios_a_seguir(db, limite=10) == []


def test_la_veda_ve_el_mail_que_esta_dentro_de_form_data(db):
    """En Meta el mail llega en el JSON del formulario y sube a la columna por
    un backfill manual que no corre solo. Mirar solo la columna deja fuera de
    la veda a todo lead al que no se le haya corrido."""
    import json
    _comercio(db, 320, source="meta", email=None,
              form_data=json.dumps({"email": "enterrado@inmo.com.uy"}))
    _comercio(db, 321, email="Enterrado@Inmo.com.uy")

    assert comercios_a_contactar(db, limite=10) == []


def test_la_veda_tolera_un_form_data_que_no_es_json(db):
    """form_data es texto libre: si no es JSON valido, la consulta no puede
    reventar y dejar la campana sin correr."""
    _comercio(db, 330, source="meta", email=None, form_data="{roto")
    _comercio(db, 331, email="sano@inmo.com.uy")

    elegidos = comercios_a_contactar(db, limite=10)

    assert [e["email"] for e in elegidos] == ["sano@inmo.com.uy"]


# ─── Rubros pausados ──────────────────────────────────────────────────────────

def test_un_rubro_pausado_no_recibe_el_primer_contacto(db):
    """Fisioterapia daba 14,7% de bajas contra 3,7% de promedio, y estetica
    8,3%. Cada baja ensucia la reputacion del subdominio, asi que se frenan
    hasta escribirles una linea de apertura propia: las dos caen hoy en la
    generica de la familia 'turnos', que a un fisioterapeuta le suena a que no
    sabemos como trabaja."""
    from services.discovery_emails import _RUBROS_PAUSADOS
    assert "fisioterapia" in _RUBROS_PAUSADOS

    _comercio(db, 500, category="odontologia")
    _comercio(db, 501, category="fisioterapia")
    claves = {c["category"] for c in comercios_a_contactar(db, 10)}
    assert claves == {"odontologia"}


def test_un_rubro_pausado_tampoco_recibe_el_seguimiento(db):
    """Lo que importa: al que ya recibio el primero no se le manda el segundo.
    Si no, la pausa no frena nada de lo que ya esta en vuelo."""
    import sqlite3

    bid = _comercio(db, 502, category="estetica")
    registrar_envio(db, bid, 1)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE discovery_reminders SET sent_at = datetime('now','-30 days')")
    conn.commit()
    conn.close()
    assert comercios_a_seguir(db, 10) == []


def test_la_pausa_no_distingue_mayusculas(db):
    _comercio(db, 503, category="Fisioterapia")
    _comercio(db, 504, category="  ESTETICA ")
    assert comercios_a_contactar(db, 10) == []


def test_los_demas_rubros_siguen_saliendo(db):
    for i, cat in enumerate(("peluqueria", "veterinaria", "inmobiliaria", "hotel")):
        _comercio(db, 510 + i, category=cat)
    assert len(comercios_a_contactar(db, 10)) == 4
