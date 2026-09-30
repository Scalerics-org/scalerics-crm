"""Mails automáticos en frío a los restaurantes de Fidelidad (Juan, 30/9).

Lo caro acá es escribirle a quien no corresponde (un local que el vendedor ya
está trabajando, alguien que pidió la baja, un cliente de la agencia) o dos
veces a la misma casilla. Resend no se llama nunca: se reemplaza el envío.
"""

import sqlite3

import pytest

from database import init_db, insert_business
from services import fid_mails_auto as auto
from services import fidelidad as fid
from services.email_service import capturar_envio, cuerpo_fidelidad, send_fidelidad_email
from services.mails_vedados import esta_vedado, vedar


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(auto, "_PAUSA_ENTRE_ENVIOS", 0)
    ruta = str(tmp_path / "fid_auto.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def enviados(monkeypatch):
    lista = []

    def falso(to, negocio, unsub, numero=1):
        lista.append({"to": to, "negocio": negocio, "unsub": unsub, "numero": numero})
        return "ok"
    monkeypatch.setattr(auto, "send_fidelidad_email", falso)
    return lista


def _local(db, n, **extra):
    datos = {"nombre": f"Parrilla {n}", "tipo": "Restaurante", "rubro": "restaurante",
             "ciudad": "Montevideo", "telefono": f"2900{n:04d}", "email": f"hola@parrilla{n}.uy",
             "resenas": n}
    datos.update(extra)
    pid, _ = fid.crear_prospecto(db, datos)
    return pid


def _sql(db, q, args=()):
    c = sqlite3.connect(db)
    try:
        r = c.execute(q, args).fetchall()
        c.commit()
        return r
    finally:
        c.close()


def _atrasar(db, dias):
    _sql(db, "UPDATE fid_mails_auto SET sent_at = datetime('now', ?)", (f"-{dias} days",))


# ─── el mail ─────────────────────────────────────────────────────────────────

def test_el_mail_pide_videollamada_ofrece_prototipo_y_no_lleva_la_demo():
    asunto, parrafos = cuerpo_fidelidad(1, "La Pasiva")
    texto = " ".join(parrafos)
    assert "McDonald's" in asunto and "La Pasiva" in asunto
    assert "videollamada" in texto and "prototipo" in texto and "Respondé" in texto
    assert "workers.dev" not in texto and "http" not in texto


def test_el_segundo_avisa_que_es_el_ultimo():
    asunto, parrafos = cuerpo_fidelidad(2, "La Pasiva")
    assert asunto.startswith("Último mail") and "último mail" in " ".join(parrafos)


def test_sale_del_subdominio_con_el_nombre_de_juan_y_baja(monkeypatch):
    monkeypatch.delenv("FID_FROM_EMAIL", raising=False)
    monkeypatch.setenv("DISCOVERY_FROM_EMAIL", "Scalerics <hola@novedades.scalerics.com>")
    with capturar_envio() as cap:
        assert send_fidelidad_email("a@b.uy", "Bar <Tito>", "https://x/baja/t", 1) == "ok"
    m = cap[0]
    assert m["from"] == "Juan de Scalerics <hola@novedades.scalerics.com>"
    assert "https://x/baja/t" in m["html"] and "Bar &lt;Tito&gt;" in m["html"]
    assert "workers.dev" not in m["html"]


def test_no_sale_del_dominio_principal(monkeypatch):
    monkeypatch.setenv("FID_FROM_EMAIL", "juan@scalerics.com")
    monkeypatch.delenv("DISCOVERY_FROM_EMAIL", raising=False)
    assert send_fidelidad_email("a@b.uy", "X", "u", 1) == "fallo"


# ─── a quién le toca ────────────────────────────────────────────────────────

def test_solo_restaurantes_sin_tocar_con_mail(db):
    ok = _local(db, 1)
    _local(db, 2, rubro="peluqueria", nombre="Pelu 2")
    _local(db, 3, email="")
    llamado = _local(db, 4)
    fid.registrar_llamada(db, llamado, "no_atendio", "Lucas")
    contactado = _local(db, 5, estado="contactado")
    visitado = _local(db, 6)
    _sql(db, "INSERT INTO fid_visitas (prospecto_id, hecha_en, resultado) VALUES (?, '2026-09-29', 'x')",
         (visitado,))
    a_mano = _local(db, 7)
    fid.registrar_mail(db, a_mano, "Lucas", "l@x", "hola@parrilla7.uy", "hola")
    ids = {p["id"] for p in auto.a_contactar(db, 100)}
    assert ids == {ok}
    assert contactado not in ids


def test_entra_buenos_aires(db):
    pid = _local(db, 1, ciudad="Buenos Aires")
    assert [p["id"] for p in auto.a_contactar(db, 10)] == [pid]


def test_veda_bajas_clientes_y_discovery(db):
    _local(db, 1)
    _local(db, 2)
    _local(db, 3)
    vedar(db, "hola@parrilla1.uy", "rebote_duro")
    insert_business(db, {"name": "Cliente", "phone": "1", "email": "hola@parrilla2.uy", "source": "meta"})
    bid = insert_business(db, {"name": "Disc", "phone": "2", "email": "hola@parrilla3.uy",
                               "source": "discovery"})
    _sql(db, "INSERT INTO discovery_reminders (business_id, numero, token, sent_at) "
             "VALUES (?, 1, 't', datetime('now'))", (bid,))
    assert auto.a_contactar(db, 10) == []


def test_una_casilla_recibe_un_solo_mail(db):
    _local(db, 1, email="info@cadena.uy")
    _local(db, 2, email="INFO@cadena.uy ")
    assert len(auto.a_contactar(db, 10)) == 1


def test_los_de_mas_resenas_primero(db):
    poco = _local(db, 5)
    mucho = _local(db, 900)
    assert [p["id"] for p in auto.a_contactar(db, 10)] == [mucho, poco]


# ─── la tanda ────────────────────────────────────────────────────────────────

def test_respeta_el_tope_diario(db, enviados):
    for n in range(1, auto.TOPE_DIARIO + 6):
        _local(db, n)
    r = auto.enviar(db, "https://crm")
    assert r["enviados"] == auto.TOPE_DIARIO == len(enviados)
    assert auto.enviar(db, "https://crm")["enviados"] == 0


def test_anota_en_el_historial_sin_llenar_la_lista_del_vendedor(db, enviados):
    pid = _local(db, 1)
    auto.enviar(db, "https://crm")
    p = fid.get_prospecto(db, pid)
    assert p["estado"] == "sin_contactar" and p["proxima_llamada"] is None
    assert p["mails"][0]["usuario"] == auto.USUARIO
    assert enviados[0]["unsub"].startswith("https://crm/baja/")


def test_segundo_mail_a_los_4_dias_y_despues_nada(db, enviados):
    _local(db, 1)
    auto.enviar(db, "https://crm")
    _atrasar(db, 3)
    assert auto.enviar(db, "https://crm")["enviados"] == 0
    _atrasar(db, 4)
    auto.enviar(db, "https://crm")
    assert [e["numero"] for e in enviados] == [1, 2]
    _atrasar(db, 10)
    assert auto.enviar(db, "https://crm")["enviados"] == 0


def test_si_el_vendedor_lo_llamo_no_sale_el_segundo(db, enviados):
    pid = _local(db, 1)
    auto.enviar(db, "https://crm")
    fid.registrar_llamada(db, pid, "no_atendio", "Lucas")
    _atrasar(db, 5)
    assert auto.enviar(db, "https://crm")["enviados"] == 0


def test_un_fallo_se_reintenta(db, monkeypatch):
    _local(db, 1)
    monkeypatch.setattr(auto, "send_fidelidad_email", lambda *a, **k: "fallo")
    assert auto.enviar(db, "https://crm")["fallidos"] == 1
    assert _sql(db, "SELECT COUNT(*) FROM fid_mails_auto")[0][0] == 0


def test_apagado_por_defecto(monkeypatch):
    monkeypatch.delenv("FID_MAILS_AUTO", raising=False)
    arrancados = []
    monkeypatch.setattr(auto.threading, "Thread", lambda *a, **k: arrancados.append(1))
    auto.start_fid_mails_auto(object())
    assert arrancados == []


# ─── baja y respuestas ───────────────────────────────────────────────────────

def test_la_baja_veda_la_direccion(db, enviados):
    _local(db, 1)
    auto.enviar(db, "https://crm")
    token = enviados[0]["unsub"].rsplit("/", 1)[-1]
    assert auto.dar_de_baja(db, token)
    assert esta_vedado(db, "hola@parrilla1.uy")
    assert not auto.dar_de_baja(db, "otro-token")
    _atrasar(db, 5)
    assert auto.enviar(db, "https://crm")["enviados"] == 0


def test_quien_responde_pasa_a_contactado_con_la_llamada_para_hoy(db, enviados):
    pid = _local(db, 1)
    auto.enviar(db, "https://crm")
    msgs = [{"from": "hola@parrilla1.uy", "subject": "Re: " + auto.ASUNTO_FID_1.format(n="Parrilla 1"),
             "date": "2999-01-01 00:00:00", "headers": {}}]
    r = auto.sincronizar_respuestas(db, lambda q: msgs)
    assert r["respondieron"] == 1
    p = fid.get_prospecto(db, pid)
    assert p["estado"] == "contactado" and p["proxima_llamada"]
    assert "videollamada" in p["notas"]
    _atrasar(db, 5)
    assert auto.enviar(db, "https://crm")["enviados"] == 0


def test_respuesta_desde_otra_casilla_se_reconoce_por_el_asunto(db, enviados):
    pid = _local(db, 1)
    auto.enviar(db, "https://crm")
    msgs = [{"from": "dueno@gmail.com", "subject": "RE: " + auto.ASUNTO_FID_1.format(n="Parrilla 1"),
             "headers": {}}]
    auto.sincronizar_respuestas(db, lambda q: msgs)
    assert fid.get_prospecto(db, pid)["estado"] == "contactado"


def test_un_fuera_de_oficina_no_cuenta(db, enviados):
    pid = _local(db, 1)
    auto.enviar(db, "https://crm")
    msgs = [{"from": "hola@parrilla1.uy", "subject": "Respuesta automática", "headers": {}}]
    auto.sincronizar_respuestas(db, lambda q: msgs)
    assert fid.get_prospecto(db, pid)["estado"] == "sin_contactar"


def test_responder_pidiendo_la_baja_la_ejecuta(db, enviados):
    pid = _local(db, 1)
    auto.enviar(db, "https://crm")
    msgs = [{"from": "hola@parrilla1.uy", "subject": "no me escriban más", "headers": {}}]
    assert auto.sincronizar_respuestas(db, lambda q: msgs)["bajas"] == 1
    assert esta_vedado(db, "hola@parrilla1.uy")
    assert fid.get_prospecto(db, pid)["estado"] == "sin_contactar"


def test_estado_para_mirar_antes_de_prender(db, enviados):
    _local(db, 1)
    _local(db, 2)
    e = auto.estado(db)
    assert e["sin_contactar_con_mail"] == 2 and e["activo"] is False
    auto.enviar(db, "https://crm")
    assert auto.estado(db)["primeros"] == 2
