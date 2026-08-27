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


# ─── Los tres agujeros que encontramos el 27/8/2026 ───────────────────────────
#
# Buscando a mano si alguien habia contestado aparecio que este modulo no lo
# habria visto en tres casos:
#
#  1. El dueno contesta desde su Gmail personal y no desde la casilla a la que
#     le escribimos. La busqueda era `from:` la direccion exacta.
#  2. La respuesta cae en spam. Las consultas de Gmail excluyen spam y papelera
#     salvo que se pida `in:anywhere` — y de hecho habia un mensaje sin leer ahi.
#  3. Los leads de Meta no se miraban: la cohorte era solo discovery.

from services.discovery_respuestas import (CAMPANAS, consultas_por_asunto,
                                           negocio_del_asunto)


@pytest.mark.parametrize("asunto,esperado", [
    ("Re: Una idea para Inmobiliaria Sur", "inmobiliaria sur"),
    ("RE: Una idea para Barberia JF", "barberia jf"),
    ("Último mail para Odontologia Pocitos", "odontologia pocitos"),
    ("Sobre tu consulta para Fullprint", "fullprint"),
    ("Re: Sobre tu consulta para Casa Blanca", "casa blanca"),
    ("RV: Una idea para Vet del Sur", "vet del sur"),
])
def test_saca_el_negocio_del_asunto(asunto, esperado):
    """El asunto lleva el nombre del negocio y sobrevive a la respuesta, venga
    de la casilla que venga. Es la unica pista cuando contestan desde otra
    direccion."""
    assert negocio_del_asunto(asunto) == esperado


@pytest.mark.parametrize("asunto", [
    "Una idea para tu negocio",          # variante sin nombre
    "Último mail de Scalerics",          # idem
    "Sobre tu consulta a Scalerics",     # idem
    "Consulta por un presupuesto",       # nada que ver
    "Re:", "", None,
])
def test_un_asunto_sin_negocio_no_inventa_uno(asunto):
    """Devolver algo aca haria marcar al comercio equivocado."""
    assert negocio_del_asunto(asunto) == ""


def test_las_consultas_miran_spam_y_papelera():
    """El agujero mas caro: habia un mensaje sin leer en spam y no lo veiamos."""
    for q in consultas_por_asunto(days_back=30):
        assert "in:anywhere" in q, q


def test_las_consultas_cubren_las_dos_campanas():
    todas = " ".join(consultas_por_asunto(days_back=30))
    assert "Una idea para" in todas          # discovery, contacto 1
    assert "ltimo mail" in todas             # discovery, contacto 2
    assert "Sobre tu consulta" in todas      # meta


def test_las_consultas_excluyen_nuestros_propios_envios():
    """Sin esto, cada mail que mandamos se contaria como una respuesta."""
    for q in consultas_por_asunto(days_back=30):
        assert "-from:scalerics.com" in q and "-from:novedades.scalerics.com" in q


def test_hay_una_campana_por_cada_tabla_de_envios():
    assert set(CAMPANAS) == {"discovery", "meta"}
    assert CAMPANAS["meta"]["source"] == "meta"
    assert CAMPANAS["discovery"]["tabla"] == "discovery_reminders"


def _lead_meta(db, n):
    from services.meta_reminders import registrar_envio as reg_meta
    bid = insert_business(db, {"name": f"Lead {n}", "phone": f"098000{n:03}",
                               "email": f"lead{n}@x.uy", "source": "meta",
                               "scraped_at": "2020-01-01 00:00:00",
                               "maps_url": f"https://maps.google.com/?cid=m{n}"})
    reg_meta(db, bid, 1)
    return bid


from database import insert_business  # noqa: E402


def test_ahora_tambien_mira_los_leads_de_meta(db):
    """No se miraban: la cohorte era solo discovery, y Meta manda 7 contactos."""
    bid = _lead_meta(db, 1)
    assert "lead1@x.uy" in direcciones_contactadas(db)


def _buscador_por_asunto(mensajes):
    """Gmail falso que entiende tanto `from:` como `subject:`."""
    def buscar(query):
        salida = []
        for m in mensajes:
            if m["from"].lower() in query.lower():
                salida.append(m)
            elif "subject:" in query.lower():
                enc = query.lower().split('subject:"')[1].split('"')[0]
                if m["subject"].lower().replace("re: ", "").startswith(enc):
                    salida.append(m)
        return salida
    return buscar


def test_marca_al_que_contesta_desde_otra_casilla(db):
    """El agujero mas importante: le escribimos a info@ y contesta el dueno
    desde su Gmail personal. La direccion no coincide, el asunto si."""
    bid = _comercio(db, 50, name="Barberia JF")
    registrar_envio(db, bid, 1)
    res = sincronizar_respuestas(db, _buscador_por_asunto([
        {"from": "jorgito.personal@gmail.com",
         "subject": "Re: Una idea para Barberia JF", "headers": {}}]))
    assert res["respondieron"] == 1
    assert get_business(db, bid)["crm_status"] != "sin_contactar"


def test_un_asunto_que_no_matchea_ningun_negocio_no_marca_nada(db):
    _comercio(db, 51, name="Barberia JF")
    res = sincronizar_respuestas(db, _buscador_por_asunto([
        {"from": "otro@gmail.com",
         "subject": "Re: Una idea para Un Negocio Que No Existe", "headers": {}}]))
    assert res["respondieron"] == 0


def test_una_respuesta_de_meta_desde_otra_casilla_tambien_se_agarra(db):
    from services.meta_reminders import registrar_envio as reg_meta
    bid = insert_business(db, {"name": "Fullprint", "phone": "098000999",
                               "email": "info@fullprint.uy", "source": "meta",
                               "scraped_at": "2020-01-01 00:00:00",
                               "maps_url": "https://maps.google.com/?cid=fp"})
    reg_meta(db, bid, 1)
    res = sincronizar_respuestas(db, _buscador_por_asunto([
        {"from": "dueno@gmail.com",
         "subject": "Re: Sobre tu consulta para Fullprint", "headers": {}}]))
    assert res["respondieron"] == 1
    assert get_business(db, bid)["crm_status"] != "sin_contactar"
