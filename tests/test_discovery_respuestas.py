"""Detectar que un comercio contesto, para no pisarle la conversacion.

El segundo contacto sale a los 7 dias del primero. Sin esto, alguien que
contesta "si, contame" recibe una semana despues un "Ultimo mail para X"
automatico — que no es una molestia, es pisar una conversacion viva.

El freno ya existia: `comercios_a_seguir` exige crm_status = 'sin_contactar',
asi que cualquier otro estado corta el seguimiento. Lo que faltaba era quien
cambia el estado. Las respuestas caen en scalerics@gmail.com (contacto@ reenvia
ahi), que es la misma casilla que ya lee el sincronizador de Calendly.
"""

import pytest

from database import init_db, insert_business, get_business
from services.discovery_emails import comercios_a_seguir, registrar_envio
from services.discovery_respuestas import (direcciones_contactadas,
                                           es_respuesta_automatica,
                                           partir_en_consultas,
                                           sincronizar_respuestas)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


def _comercio(db, n, **extra):
    datos = {"name": f"Comercio {n}", "phone": f"09900{n:04}",
             "website": f"https://c{n}.uy", "email": f"info@c{n}.uy",
             "source": "discovery", "maps_url": f"https://maps.google.com/?cid={n}"}
    datos.update(extra)
    return insert_business(db, datos)


def _contactado(db, n, **extra):
    bid = _comercio(db, n, **extra)
    registrar_envio(db, bid, 1)
    return bid


def _buscador(mensajes):
    """Un Gmail falso: devuelve los mensajes cuyo remitente aparece en la query."""
    def buscar(query):
        return [m for m in mensajes if m["from"].lower() in query.lower()]
    return buscar


# ─── A quien mirar ────────────────────────────────────────────────────────────

def test_solo_mira_a_los_que_recibieron_un_mail(db):
    _contactado(db, 1)
    _comercio(db, 2)                       # en el padron pero nunca contactado
    assert set(direcciones_contactadas(db)) == {"info@c1.uy"}


def test_no_mira_a_los_que_ya_salieron_de_sin_contactar(db):
    """Si ya lo movio alguien, el seguimiento ya esta frenado y no hay nada que
    marcar de nuevo."""
    bid = _contactado(db, 3)
    from database import update_business
    update_business(db, bid, crm_status="reunion_agendada")
    assert direcciones_contactadas(db) == {}


def test_no_mira_otras_cohortes(db):
    bid = _comercio(db, 4, source="meta")
    registrar_envio(db, bid, 1)
    assert direcciones_contactadas(db) == {}


# ─── Respuestas automaticas ───────────────────────────────────────────────────

@pytest.mark.parametrize("cabeceras,asunto", [
    ({"Auto-Submitted": "auto-replied"}, "Re: Una idea para X"),
    ({"X-Autoreply": "yes"}, "Re: Una idea para X"),
    ({}, "Automatic reply: Una idea para X"),
    ({}, "Respuesta automática: Una idea para X"),
    ({}, "Out of Office: Una idea para X"),
    ({"Precedence": "auto_reply"}, ""),
])
def test_las_automaticas_no_cuentan(cabeceras, asunto):
    """Un fuera-de-oficina no es una conversacion: frenar el seguimiento por eso
    seria perder el unico contacto que quedaba."""
    assert es_respuesta_automatica(cabeceras, asunto) is True


@pytest.mark.parametrize("asunto", [
    "Re: Una idea para Inmobiliaria Sur",
    "RE: UNA IDEA PARA X",
    "consulta",
    "",
])
def test_una_respuesta_de_verdad_si_cuenta(asunto):
    assert es_respuesta_automatica({}, asunto) is False


# ─── La consulta a Gmail ──────────────────────────────────────────────────────

def test_parte_las_direcciones_en_varias_consultas():
    """Gmail no acepta una query infinita: con miles de direcciones hay que
    partirla o la peticion se rechaza entera."""
    direcciones = [f"a{i}@x.uy" for i in range(70)]
    consultas = partir_en_consultas(direcciones, por_consulta=30, days_back=15)
    assert len(consultas) == 3
    assert all("newer_than:15d" in q for q in consultas)
    assert sum(q.count("@x.uy") for q in consultas) == 70


def test_sin_direcciones_no_arma_ninguna_consulta():
    """Sin esto se mandaria `from:()` y Gmail devolveria media casilla."""
    assert partir_en_consultas([], por_consulta=30, days_back=15) == []


# ─── El sincronizado completo ─────────────────────────────────────────────────

def test_marca_al_que_contesto(db):
    bid = _contactado(db, 10)
    res = sincronizar_respuestas(db, _buscador([
        {"from": "info@c10.uy", "subject": "Re: Una idea para X", "headers": {}}]))
    assert res["respondieron"] == 1
    assert get_business(db, bid)["crm_status"] != "sin_contactar"


def test_el_que_contesto_ya_no_recibe_el_seguimiento(db):
    """La prueba que importa: que el freno efectivamente frene."""
    bid = _contactado(db, 11)
    # Antes de marcar, con el piso de dias cumplido, seria candidato.
    sincronizar_respuestas(db, _buscador([
        {"from": "info@c11.uy", "subject": "Re: X", "headers": {}}]))
    assert [c["id"] for c in comercios_a_seguir(db, 50)] == []


def test_el_que_no_contesto_queda_como_estaba(db):
    bid = _contactado(db, 12)
    res = sincronizar_respuestas(db, _buscador([]))
    assert res["respondieron"] == 0
    assert get_business(db, bid)["crm_status"] == "sin_contactar"


def test_un_fuera_de_oficina_no_lo_marca(db):
    bid = _contactado(db, 13)
    res = sincronizar_respuestas(db, _buscador([
        {"from": "info@c13.uy", "subject": "Automatic reply: X",
         "headers": {"Auto-Submitted": "auto-replied"}}]))
    assert res["automaticas"] == 1
    assert res["respondieron"] == 0
    assert get_business(db, bid)["crm_status"] == "sin_contactar"


def test_un_mail_de_alguien_ajeno_no_marca_nada(db):
    _contactado(db, 14)
    res = sincronizar_respuestas(db, _buscador([
        {"from": "spam@otro.com", "subject": "hola", "headers": {}}]))
    assert res["respondieron"] == 0


def test_la_direccion_se_compara_sin_importar_mayusculas(db):
    bid = _contactado(db, 15, email="INFO@C15.uy")
    sincronizar_respuestas(db, _buscador([
        {"from": "info@c15.UY", "subject": "Re: X", "headers": {}}]))
    assert get_business(db, bid)["crm_status"] != "sin_contactar"


def test_dos_respuestas_del_mismo_no_lo_cuentan_dos_veces(db):
    _contactado(db, 16)
    res = sincronizar_respuestas(db, _buscador([
        {"from": "info@c16.uy", "subject": "Re: X", "headers": {}},
        {"from": "info@c16.uy", "subject": "Re: X otra vez", "headers": {}}]))
    assert res["respondieron"] == 1


def test_dry_run_no_toca_nada(db):
    bid = _contactado(db, 17)
    res = sincronizar_respuestas(db, _buscador([
        {"from": "info@c17.uy", "subject": "Re: X", "headers": {}}]), dry_run=True)
    assert res["respondieron"] == 1
    assert get_business(db, bid)["crm_status"] == "sin_contactar"


def test_sin_nadie_contactado_no_consulta_gmail(db):
    """Sin direcciones que buscar no hay nada que preguntarle a Gmail."""
    llamadas = []

    def buscar(query):
        llamadas.append(query)
        return []

    assert sincronizar_respuestas(db, buscar)["revisados"] == 0
    assert llamadas == []
