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


# ── Que el lector no se desincronice de lo que mandamos ──────────────────────
# `_PATRONES_META` es un espejo de los asuntos que arma email_service, en otro
# archivo. Si alguien cambia un asunto y no toca el patron, nada falla en
# produccion: el lead que contesta desde otra casilla simplemente no se detecta,
# y la secuencia le sigue escribiendo encima de una conversacion viva. Ya pasó:
# el 28-8-2026 se reescribieron los 13 asuntos y solo uno seguia matcheando.

def _todos_los_asuntos(negocio="Casa Garrido", rubro="un_software_a_medida"):
    from services.email_service import (_cuerpo_por_contacto, _cuerpo_por_estado,
                                        _frase_rubro, _parrafo_valor)
    from services.secuencia_contactos import SECUENCIAS_POR_ESTADO, DIAS_DE_CADA_CONTACTO
    apertura = (f"Dejaste tus datos porque {_frase_rubro(rubro)} para {negocio}, "
                f"y todavía estamos a tiempo de tomarlo.")
    fuera = []
    for n in range(1, len(DIAS_DE_CADA_CONTACTO) + 1):
        fuera.append(_cuerpo_por_contacto(n, apertura, _parrafo_valor(rubro), negocio)[0])
    for estado, dias in SECUENCIAS_POR_ESTADO.items():
        if estado == "sin_contactar":
            continue
        for n in range(1, len(dias) + 1):
            fuera.append(_cuerpo_por_estado(estado, n, negocio, rubro)[0])
    return fuera


def test_todos_los_asuntos_que_mandamos_se_pueden_leer():
    from services.discovery_respuestas import negocio_del_asunto
    asuntos = _todos_los_asuntos()
    assert len(asuntos) == 13, "cambio la cantidad de plantillas"
    sin_leer = [a for a in asuntos if negocio_del_asunto(a) != "casa garrido"]
    assert not sin_leer, (
        "estos asuntos salen de email_service y _PATRONES_META no los entiende: "
        + repr(sin_leer))


def test_tambien_se_leen_cuando_vienen_como_respuesta():
    from services.discovery_respuestas import negocio_del_asunto
    for asunto in _todos_los_asuntos():
        assert negocio_del_asunto("Re: " + asunto) == "casa garrido", asunto


# ── Frenar de verdad, y sin retroceder ───────────────────────────────────────

def _lead(db, bid, source, estado, mail="x@y.com"):
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO businesses (id,name,email,crm_status,source) VALUES (?,?,?,?,?)",
                 (bid, f"Negocio {bid}", mail, estado, source))
    conn.commit()
    conn.close()


def _estado(db, bid):
    import sqlite3
    conn = sqlite3.connect(db)
    v = conn.execute("SELECT crm_status FROM businesses WHERE id=?", (bid,)).fetchone()[0]
    conn.close()
    return v


def test_el_que_contesta_un_presupuesto_no_retrocede(db):
    """Lo mas caliente que hay. Moverlo a 'interesado' seria empujarlo para atras
    y ademas meterlo en OTRA secuencia de mails."""
    from services.discovery_respuestas import marcar_respondio
    _lead(db, 1, "meta", "presupuesto_enviado")
    marcar_respondio(db, 1, "quien@sea.com")
    assert _estado(db, 1) == "follow_up_1"


def test_un_lead_frio_de_meta_que_contesta_sale_de_toda_secuencia(db):
    """'interesado' ya no sirve para frenar: desde que cada estado tiene su
    secuencia, ese tambien manda un mail."""
    from services.discovery_respuestas import marcar_respondio
    from services.secuencia_contactos import SECUENCIAS_POR_ESTADO
    _lead(db, 1, "meta", "sin_contactar")
    marcar_respondio(db, 1, "quien@sea.com")
    assert _estado(db, 1) not in SECUENCIAS_POR_ESTADO, "quedo en un estado que sigue mandando"


def test_no_pisa_a_quien_ya_esta_mas_adelante(db):
    from services.discovery_respuestas import marcar_respondio
    _lead(db, 1, "meta", "cliente_cerrado")
    marcar_respondio(db, 1, "quien@sea.com")
    assert _estado(db, 1) == "cliente_cerrado"


def test_igual_deja_el_evento_aunque_no_mueva_el_estado(db):
    import sqlite3
    from services.discovery_respuestas import marcar_respondio
    _lead(db, 1, "meta", "cliente_cerrado")
    marcar_respondio(db, 1, "quien@sea.com")
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM lead_events WHERE lead_id=1").fetchone()[0]
    conn.close()
    assert n == 1, "sin evento, nadie se entera de que contestó"


def test_discovery_sigue_yendo_a_interesado(db):
    from services.discovery_respuestas import marcar_respondio
    _lead(db, 1, "discovery", "sin_contactar")
    marcar_respondio(db, 1, "quien@sea.com")
    assert _estado(db, 1) == "interesado"


def test_se_vigilan_los_cinco_estados_de_meta_no_solo_sin_contactar(db):
    """Mirar solo 'sin_contactar' dejaba sin vigilar a 134 de los 157 en secuencia."""
    import sqlite3
    from services.discovery_respuestas import direcciones_contactadas
    from services.secuencia_contactos import ESTADOS_CON_SECUENCIA
    conn = sqlite3.connect(db)
    for i, estado in enumerate(ESTADOS_CON_SECUENCIA, start=1):
        conn.execute("INSERT INTO businesses (id,name,email,crm_status,source) "
                     "VALUES (?,?,?,?,'meta')", (i, f"N{i}", f"lead{i}@x.com", estado))
        conn.execute("INSERT INTO meta_reminders (business_id,estado,numero,token,sent_at) "
                     "VALUES (?,?,1,?,datetime('now'))", (i, estado, f"tok{i}"))
    conn.commit()
    conn.close()
    vigilados = direcciones_contactadas(db)
    assert len(vigilados) == len(ESTADOS_CON_SECUENCIA)


# ── "Nos escribió" no es "nos respondió" ─────────────────────────────────────
# La busqueda de Gmail solo acota por `newer_than:30d`. Sin comparar contra
# nuestro ultimo envio, un mail que el lead nos mando ANTES de entrar a la
# secuencia se contaba como respuesta: le frenaba el seguimiento y le abria una
# negociacion que nunca existio.

def _lead_con_envio(db, bid, mail, sent_at, estado="sin_contactar"):
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO businesses (id,name,email,crm_status,source) "
                 "VALUES (?,?,?,?,'meta')", (bid, f"N{bid}", mail, estado))
    conn.execute("INSERT INTO meta_reminders (business_id,estado,numero,token,sent_at) "
                 "VALUES (?,?,1,?,?)", (bid, estado, f"tok{bid}", sent_at))
    conn.commit()
    conn.close()


def _buscar_fijo(mensajes):
    return lambda consulta: mensajes


def test_un_mail_anterior_a_nuestro_envio_no_es_una_respuesta(db):
    from services.discovery_respuestas import sincronizar_respuestas
    _lead_con_envio(db, 1, "lead@x.com", "2026-08-20 10:00:00")
    r = sincronizar_respuestas(db, _buscar_fijo([
        {"from": "lead@x.com", "subject": "Consulta suelta",
         "date": "2026-08-05 09:00:00", "headers": {}}]))
    assert r["respondieron"] == 0 and r["previas"] == 1
    assert _estado(db, 1) == "sin_contactar", "no se le frena la secuencia"


def test_un_mail_posterior_si_es_una_respuesta(db):
    from services.discovery_respuestas import sincronizar_respuestas
    _lead_con_envio(db, 1, "lead@x.com", "2026-08-20 10:00:00")
    r = sincronizar_respuestas(db, _buscar_fijo([
        {"from": "lead@x.com", "subject": "Re: Sobre tu consulta para N1",
         "date": "2026-08-20 10:05:00", "headers": {}}]))
    assert r["respondieron"] == 1
    assert _estado(db, 1) == "follow_up_1"


def test_sin_fecha_se_cuenta_como_respuesta(db):
    """Perder una respuesta real y seguir escribiendole encima es peor que
    abrir una negociacion de mas, que un humano descarta en diez segundos."""
    from services.discovery_respuestas import sincronizar_respuestas
    _lead_con_envio(db, 1, "lead@x.com", "2026-08-20 10:00:00")
    r = sincronizar_respuestas(db, _buscar_fijo([
        {"from": "lead@x.com", "subject": "algo", "headers": {}}]))
    assert r["respondieron"] == 1


def test_gmail_convierte_internaldate_al_formato_de_la_casa():
    """internalDate viene en milisegundos epoch UTC y sent_at es UTC naive."""
    from datetime import datetime, timezone
    from services.discovery_respuestas import buscar_con_gmail
    _ESPERADO = "2026-08-27 18:33:23"
    _MS = int(datetime(2026, 8, 27, 18, 33, 23, tzinfo=timezone.utc).timestamp() * 1000)

    class _Msgs:
        def list(self, **kw):
            return _Ejecuta({"messages": [{"id": "1"}]})
        def get(self, **kw):
            return _Ejecuta({"internalDate": str(_MS),
                             "payload": {"headers": [{"name": "From", "value": "a@b.com"},
                                                      {"name": "Subject", "value": "hola"}]}})
    class _Ejecuta:
        def __init__(self, v): self.v = v
        def execute(self): return self.v
    class _Users:
        def messages(self): return _Msgs()
    class _Svc:
        def users(self): return _Users()

    msg = buscar_con_gmail(_Svc())("q")[0]
    assert msg["date"] == _ESPERADO
    assert msg["from"] == "a@b.com"


# ── La baja pedida a mano ────────────────────────────────────────────────────
# Quien contesta "sacame de la lista" no esta negociando: pidio algo que hay que
# ejecutar. Sin esto quedaba como oportunidad abierta en el pipeline, nadie
# hacia lo que pidio, y si volvia a entrar por otro formulario recibia mails de
# nuevo.

@pytest.mark.parametrize("asunto", [
    "Re: sacame de la lista",
    "RE: Casa Garrido — el presupuesto  no me escriban mas por favor",
    "dar de baja",
    "Unsubscribe",
    "no quiero recibir mas mails",
])
def test_reconoce_el_pedido_de_baja(asunto):
    from services.discovery_respuestas import pide_la_baja
    assert pide_la_baja(asunto) is True


@pytest.mark.parametrize("asunto", [
    "Re: Cerramos lo de Casa Garrido",
    "Re: ¿Damos por cerrado lo de Baja Vista?",
    "Re: Casa Garrido — ¿qué te frenó?",
    "consulta por la baja de precios",
    "",
])
def test_no_confunde_un_asunto_normal_con_un_pedido_de_baja(asunto):
    from services.discovery_respuestas import pide_la_baja
    assert pide_la_baja(asunto) is False


def test_la_baja_veda_la_direccion_y_saca_al_lead_de_la_secuencia(db):
    from services.discovery_respuestas import sincronizar_respuestas
    from services.mails_vedados import esta_vedado
    _lead_con_envio(db, 1, "lead@x.com", "2026-08-20 10:00:00")
    r = sincronizar_respuestas(db, _buscar_fijo([
        {"from": "lead@x.com", "subject": "Re: sacame de la lista",
         "date": "2026-08-21 10:00:00", "headers": {}}]))
    assert r["bajas"] == 1 and r["respondieron"] == 0
    assert _estado(db, 1) == "no_interesa"
    assert esta_vedado(db, "lead@x.com"), "la direccion tiene que quedar vedada"


def test_a_un_cliente_que_pide_la_baja_no_se_lo_degrada(db):
    """Pidio no recibir mas mails, no dejar de ser cliente.

    Se prueba la guarda directo: un lead en 'en_desarrollo' no esta en la lista
    de vigilados —no recibe secuencia, no hay nada que frenar— asi que por el
    sync no llega. La guarda igual tiene que estar, porque el dia que la lista
    se ensanche va a ser lo unico que impida degradar a un cliente.
    """
    from services.discovery_respuestas import marcar_baja_pedida
    from services.mails_vedados import esta_vedado
    _lead(db, 1, "meta", "en_desarrollo", mail="cliente@x.com")
    marcar_baja_pedida(db, 1, "cliente@x.com", "Re: dar de baja")
    assert _estado(db, 1) == "en_desarrollo"
    assert esta_vedado(db, "cliente@x.com"), "igual se le corta el mail"


def test_al_lead_en_secuencia_que_pide_la_baja_si_se_lo_mueve(db):
    from services.discovery_respuestas import marcar_baja_pedida
    _lead(db, 1, "meta", "presupuesto_enviado", mail="lead@x.com")
    marcar_baja_pedida(db, 1, "lead@x.com", "Re: sacame de la lista")
    assert _estado(db, 1) == "no_interesa"


def test_la_baja_no_se_cuenta_tambien_como_respuesta(db):
    from services.discovery_respuestas import sincronizar_respuestas
    _lead_con_envio(db, 1, "lead@x.com", "2026-08-20 10:00:00")
    r = sincronizar_respuestas(db, _buscar_fijo([
        {"from": "lead@x.com", "subject": "Re: no me escriban",
         "date": "2026-08-21 10:00:00", "headers": {}}]))
    assert r["bajas"] == 1 and r["respondieron"] == 0


# ── Que el fallo de Gmail no sea mudo ────────────────────────────────────────
# El sync corre justo antes de cada tanda y quien lo llama atrapa la excepcion
# para mandar igual. Sin aviso, la deteccion de respuestas se apaga en silencio
# y las secuencias siguen escribiendo encima de conversaciones vivas.

def _con_admin(db, mail="admin@scalerics.com"):
    import sqlite3
    conn = sqlite3.connect(db)
    # init_db ya siembra el rol Admin: crearlo de nuevo choca contra el UNIQUE.
    rid = conn.execute("SELECT id FROM roles WHERE LOWER(name)='admin'").fetchone()[0]
    conn.execute("INSERT INTO users (name, email, phone, password, role_id, created_at) "
                 "VALUES ('a', ?, '099', 'x', ?, datetime('now'))", (mail, rid))
    conn.commit()
    conn.close()


def test_avisa_a_los_admins_cuando_gmail_falla(db, monkeypatch):
    from services import discovery_respuestas as dr
    _con_admin(db)
    avisados = []
    monkeypatch.setattr("services.email_service.send_meta_token_alert",
                        lambda to, detalle: avisados.append((to, detalle)) or True)
    assert dr.avisar_que_gmail_fallo(db, "RefreshError: invalid_grant") is True
    assert avisados and avisados[0][0] == "admin@scalerics.com"
    assert "invalid_grant" in avisados[0][1]


def test_no_repite_el_aviso_cada_corrida(db, monkeypatch):
    """Un token vencido no mejora porque le manden veinte mails."""
    from services import discovery_respuestas as dr
    _con_admin(db)
    monkeypatch.setattr("services.email_service.send_meta_token_alert",
                        lambda to, detalle: True)
    assert dr.avisar_que_gmail_fallo(db, "x") is True
    assert dr.avisar_que_gmail_fallo(db, "x") is False, "el segundo tiene que callarse"


def test_el_fallo_se_avisa_y_ademas_se_propaga(db, monkeypatch):
    """Quien llama tiene que poder decidir si manda igual o no."""
    import pytest as _pytest
    from services import discovery_respuestas as dr
    _con_admin(db)
    llamados = []
    monkeypatch.setattr(dr, "_sincronizar_desde_gmail",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sin token")))
    monkeypatch.setattr(dr, "avisar_que_gmail_fallo",
                        lambda db_path, detalle: llamados.append(detalle) or True)
    with _pytest.raises(RuntimeError):
        dr.sincronizar_desde_gmail(db)
    assert llamados and "sin token" in llamados[0]


def test_si_el_aviso_tambien_falla_igual_se_propaga_el_error_real(db, monkeypatch):
    import pytest as _pytest
    from services import discovery_respuestas as dr
    monkeypatch.setattr(dr, "_sincronizar_desde_gmail",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sin token")))
    monkeypatch.setattr(dr, "avisar_que_gmail_fallo",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("tampoco")))
    with _pytest.raises(RuntimeError, match="sin token"):
        dr.sincronizar_desde_gmail(db)
