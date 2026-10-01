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


def test_el_segundo_ya_no_dice_que_es_el_ultimo():
    # Juan (30/9): se les vuelve a escribir cada mes.
    asunto, parrafos = cuerpo_fidelidad(2, "La Pasiva")
    assert "La Pasiva" in asunto and "último" not in (asunto + " ".join(parrafos)).lower()




def test_sale_del_subdominio_con_el_nombre_de_juan_y_baja(monkeypatch):
    monkeypatch.delenv("FID_FROM_EMAIL", raising=False)
    monkeypatch.setenv("DISCOVERY_FROM_EMAIL", "Scalerics <hola@novedades.scalerics.com>")
    with capturar_envio() as cap:
        assert send_fidelidad_email("a@b.uy", "Bar <Tito>", "https://x/baja/t", 1) == "ok"
    m = cap[0]
    assert m["from"] == "Juan de Scalerics <hola@novedades.scalerics.com>"
    assert "https://x/baja/t" in m["html"] and "Bar &lt;Tito&gt;" in m["html"]
    assert "workers.dev" not in m["html"]
    assert "+598 94 053 389" in m["html"] and "+598 94 053 389" in m["text"]
    assert "97 250 713" not in m["html"]


def test_lo_de_la_agencia_va_como_agregado_al_final():
    _, parrafos = cuerpo_fidelidad(1, "La Pasiva")
    assert parrafos[-1].startswith("PD:") and "a medida" in parrafos[-1]


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
    for n in range(1, auto.RAMPA_INICIAL + 6):
        _local(db, n)
    r = auto.enviar(db, "https://crm")
    assert r["enviados"] == auto.RAMPA_INICIAL == len(enviados)
    assert auto.enviar(db, "https://crm")["enviados"] == 0


def test_anota_en_el_historial_sin_llenar_la_lista_del_vendedor(db, enviados):
    pid = _local(db, 1)
    auto.enviar(db, "https://crm")
    p = fid.get_prospecto(db, pid)
    assert p["estado"] == "sin_contactar" and p["proxima_llamada"] is None
    assert p["mails"][0]["usuario"] == auto.USUARIO
    assert enviados[0]["unsub"].startswith("https://crm/baja/")




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


# ─── la sección de Captación ─────────────────────────────────────────────────

def _evento(db, email, numero, **cols):
    sets = ", ".join(f"{k} = datetime('now')" for k in cols)
    _sql(db, "INSERT INTO emails_enviados (tipo, destinatario, numero, enviado_at) "
             "VALUES ('fidelidad', ?, ?, datetime('now'))", (email, numero))
    if sets:
        _sql(db, f"UPDATE emails_enviados SET {sets} WHERE destinatario = ? AND numero = ?", (email, numero))


def test_panel_cuenta_y_lista_solo_restaurantes(db, enviados):
    _local(db, 1)
    _local(db, 2, ciudad="Buenos Aires")
    _local(db, 3)
    auto.enviar(db, "https://crm")
    _evento(db, "hola@parrilla1.uy", 1, entregado_at=1, abierto_at=1)
    _evento(db, "hola@parrilla2.uy", 1, rebotado_at=1)
    _sql(db, "INSERT INTO emails_enviados (tipo, destinatario, enviado_at) "
             "VALUES ('discovery', 'otro@x.uy', datetime('now'))")
    d = auto.panel(db)
    assert d["enviados"] == 3 and d["abiertos"] == 1 and d["bajas_rebotes"] == 1
    estados = {e["email"]: e["estado"] for e in d["envios"]}
    assert estados == {"hola@parrilla1.uy": "abierto", "hola@parrilla2.uy": "rebote",
                       "hola@parrilla3.uy": "enviado"}
    assert auto.panel(db, ciudad="Buenos Aires")["enviados"] == 1
    assert auto.panel(db, mes="2020-01")["envios"] == []


def test_panel_marca_respondio(db, enviados):
    pid = _local(db, 1)
    auto.enviar(db, "https://crm")
    auto.marcar_respuesta(db, pid, "hola@parrilla1.uy", "Re: hola")
    d = auto.panel(db)
    assert d["respondieron"] == 1 and d["envios"][0]["estado_texto"] == "Respondió"


def test_ver_mail_no_trae_el_link_de_baja_real(db, enviados):
    _local(db, 1)
    auto.enviar(db, "https://crm")
    token = enviados[0]["unsub"].rsplit("/", 1)[-1]
    envio_id = auto.panel(db)["envios"][0]["id"]
    m = auto.mail_enviado(db, envio_id, "https://crm")
    assert "Parrilla 1" in m["asunto"] and "prototipo" in m["html"]
    assert token not in m["html"]
    assert auto.mail_enviado(db, 999, "https://crm") is None


# ─── «la máxima cantidad posible sin gastar plata» (Juan, 30/9) ───────────────

def _otros_envios(db, n, tipo="recordatorio_meta", hace="0 hours"):
    c = sqlite3.connect(db)
    c.executemany("INSERT INTO emails_enviados (tipo, destinatario, enviado_at) "
                  "VALUES (?, 'x@y.uy', datetime('now', ?))", [(tipo, f"-{hace}")] * n)
    c.commit()
    c.close()


def test_el_cupo_arranca_en_la_rampa(db):
    c = auto.cupo_del_dia(db)
    assert c["por_dia"] == auto.RAMPA_INICIAL and c["limita"] == "rampa"


def test_el_cupo_sube_con_los_dias_de_envio(db, enviados):
    _local(db, 1)
    auto.enviar(db, "https://crm")
    _atrasar(db, 3)
    assert auto.cupo_del_dia(db)["por_dia"] == auto.RAMPA_INICIAL + 3 * auto.RAMPA_POR_DIA




def test_el_cupo_no_se_pasa_de_los_3000_del_mes(db, monkeypatch):
    monkeypatch.setattr(auto, "RAMPA_INICIAL", 100)
    _otros_envios(db, auto.CUOTA_MES - auto.MARGEN_MES, tipo="aviso_equipo")
    assert auto.cupo_del_dia(db)["por_dia"] == 0




# ─── la secuencia: hoy, 15 días, un mes, tres meses ──────────────────────────────────────────────────────







def test_el_reenvio_del_mes_se_reconoce_en_la_respuesta():
    assert auto.negocio_del_asunto("RE: " + auto.ASUNTO_FID_MES.format(n="La Pasiva")) == "la pasiva"
    assert auto.negocio_del_asunto("Re: " + auto.ASUNTO_FID_2.format(n="La Pasiva")) == "la pasiva"


# ─── que no pare: el buscador de GitHub Actions ──────────────────────────────

def test_webs_sin_buscar_y_guardar_lo_encontrado(db):
    con_web = _local(db, 1, email="", web="https://p1.uy", resenas=10)
    otro = _local(db, 2, email="", web="https://p2.uy", resenas=500)
    caido = _local(db, 3, email="", web="https://p3.uy")
    _local(db, 4, email="", web="")
    assert [w["id"] for w in auto.webs_sin_buscar(db, 10)] == [otro, con_web, caido]
    r = auto.guardar_mails_encontrados(db, [
        {"id": con_web, "email": "Hola@P1.uy", "abrio": True},
        {"id": otro, "email": None, "abrio": True},
        {"id": caido, "email": None, "abrio": False},
        {"id": "x"}])
    assert r == {"encontrados": 1, "sin_mail": 1, "no_abrio": 1, "ignorados": 1}
    assert fid.get_prospecto(db, con_web)["email"] == "hola@p1.uy"
    assert [w["id"] for w in auto.webs_sin_buscar(db, 10)] == [caido]
    assert [p["id"] for p in auto.a_contactar(db, 10)] == [con_web]


def test_no_pisa_un_mail_cargado_a_mano(db):
    pid = _local(db, 1, email="dueno@p1.uy", web="https://p1.uy")
    auto.guardar_mails_encontrados(db, [{"id": pid, "email": "info@p1.uy", "abrio": True}])
    assert fid.get_prospecto(db, pid)["email"] == "dueno@p1.uy"


def test_las_busquedas_automaticas_cubren_montevideo_y_caba():
    todas = fid.busquedas_automaticas()
    assert len({(b["tipo"], b["barrio"], b["ciudad"]) for b in todas}) == len(todas)  # hay un Palermo en cada ciudad
    assert {b["ciudad"] for b in todas} == {"Montevideo", "Buenos Aires"}
    assert todas[0]["tipo"] == "restaurante"
    assert any(b["barrio"] == "Barra de Carrasco" and b["depto"] == "Canelones" for b in todas)


def test_el_estado_dice_cuantos_dias_de_cola_quedan(db):
    for n in range(1, 51):
        _local(db, n)
    e = auto.estado(db)
    assert e["dias_de_cola"] == 50 // auto.RAMPA_INICIAL and e["webs_sin_buscar"] == 0


def test_avisa_si_se_queda_sin_cola_una_vez_cada_tres_dias(db, monkeypatch):
    import services.email_service as es
    avisos = []
    monkeypatch.setattr(es, "send_fidelidad_alerta", lambda to, a, t: avisos.append(to) or True)
    monkeypatch.setenv("ADMIN_EMAIL", "juan@x.uy")
    assert auto.avisar_si_se_quedo_sin_cola(db, {"candidatos": 0, "cupo": 25})
    assert not auto.avisar_si_se_quedo_sin_cola(db, {"candidatos": 0, "cupo": 25})
    assert avisos == ["juan@x.uy"]
    assert not auto.avisar_si_se_quedo_sin_cola(db, {"candidatos": 3, "cupo": 25})


def test_la_api_del_buscador_pide_token(db, monkeypatch):
    import dashboard
    monkeypatch.setenv("ADMIN_TOKEN", "tok")
    monkeypatch.setenv("CRM_SIN_PROCESOS_DE_FONDO", "true")
    _local(db, 1, email="", web="https://p1.uy")
    cli = dashboard.create_app(db).test_client()
    assert cli.get("/api/fidelidad/mails-auto/webs").status_code in (302, 401, 403)
    h = {"x-admin-token": "tok"}
    webs = cli.get("/api/fidelidad/mails-auto/webs", headers=h).get_json()
    assert webs == [{"id": 1, "web": "https://p1.uy"}]
    r = cli.post("/api/fidelidad/mails-auto/encontrados", headers=h,
                 json={"resultados": [{"id": 1, "email": "a@p1.uy", "abrio": True}]}).get_json()
    assert r["encontrados"] == 1
    assert cli.get("/api/fidelidad/mails-auto", headers=h).get_json()["sin_contactar_con_mail"] == 1



# ─── la secuencia de Juan (30/9): hoy, a los 15 días, al mes, a los 3 meses ──

def test_el_tercero_ofrece_el_prototipo():
    asunto, parrafos = cuerpo_fidelidad(3, "La Pasiva")
    texto = " ".join(parrafos)
    assert "prototipo" in asunto and "La Pasiva" in asunto
    assert "videollamada" in texto and "http" not in texto


def test_el_cuarto_es_el_ultimo():
    asunto, parrafos = cuerpo_fidelidad(4, "La Pasiva")
    assert asunto.startswith("Último mail") and "último mail" in " ".join(parrafos)
    assert auto.negocio_del_asunto("Re: " + asunto) == "la pasiva"


def test_la_secuencia_es_15_30_90_y_despues_se_descarta(db, enviados):
    pid = _local(db, 1)
    auto.enviar(db, "https://crm")
    for espera, numero in ((15, 2), (30, 3), (90, 4)):
        _atrasar(db, espera - 1)
        assert auto.enviar(db, "https://crm")["enviados"] == 0, f"antes del {numero}"
        _atrasar(db, espera)
        auto.enviar(db, "https://crm")
        assert enviados[-1]["numero"] == numero
    assert [e["numero"] for e in enviados] == [1, 2, 3, 4]
    _atrasar(db, 200)
    assert auto.enviar(db, "https://crm")["enviados"] == 0
    _atrasar(db, auto.DIAS_PARA_DESCARTAR - 1)
    assert auto.descartar_sin_respuesta(db) == 0
    _atrasar(db, auto.DIAS_PARA_DESCARTAR)
    assert auto.descartar_sin_respuesta(db) == 1
    p = fid.get_prospecto(db, pid)
    assert p["estado"] == "descartado" and p["motivo_descarte"] == auto.MOTIVO_DESCARTE
    assert auto.descartar_sin_respuesta(db) == 0


def test_si_contesto_no_sigue_la_secuencia_ni_se_descarta(db, enviados):
    pid = _local(db, 1)
    auto.enviar(db, "https://crm")
    auto.marcar_respuesta(db, pid, "hola@parrilla1.uy", "Re: hola")
    fid.mover_estado(db, pid, "sin_contactar", "Lucas")   # aunque lo vuelvan atrás
    _atrasar(db, 200)
    assert auto.enviar(db, "https://crm")["seguimientos"] == 0
    assert auto.descartar_sin_respuesta(db) == 0


def test_los_seguimientos_van_antes_que_los_nuevos(db, enviados, monkeypatch):
    monkeypatch.setattr(auto, "RAMPA_INICIAL", 10)
    monkeypatch.setattr(auto, "RAMPA_POR_DIA", 0)
    for n in range(1, 9):
        _local(db, n)
    auto.enviar(db, "https://crm")
    _atrasar(db, 15)
    for n in range(9, 30):
        _local(db, n)
    r = auto.enviar(db, "https://crm")
    assert r["seguimientos"] == 8 and r["nuevos"] == 2


def test_las_respuestas_se_buscan_mas_atras_que_la_espera_mas_larga():
    assert auto.DIAS_DE_RESPUESTAS > max(auto.ESPERA_ANTES_DE.values())


# ─── la reserva de las otras campañas sale de lo que mandan de verdad ────────

def test_el_cupo_deja_lugar_a_lo_que_las_otras_mandan_en_un_dia(db, monkeypatch):
    monkeypatch.setattr(auto, "CUOTA_MES", 10 ** 6)   # que mida solo el tope del día
    monkeypatch.setenv("DISCOVERY_EMAILS", "on")
    monkeypatch.setattr(auto, "RAMPA_INICIAL", 100)
    # Hace dos días: Meta mandó 15 y discovery 50. Hoy todavía nada.
    _otros_envios(db, 15, hace="2 days")
    _otros_envios(db, 50, tipo="discovery", hace="2 days")
    assert auto.cupo_del_dia(db)["por_dia"] <= auto.CUOTA_DIA - auto.MARGEN_DIA - 65
    _otros_envios(db, 15)
    _otros_envios(db, 50, tipo="discovery")
    assert auto.cupo_del_dia(db)["por_dia"] <= auto.CUOTA_DIA - auto.MARGEN_DIA - 65


def test_si_meta_no_manda_no_se_le_guarda_lugar(db, monkeypatch):
    monkeypatch.setattr(auto, "CUOTA_MES", 10 ** 6)   # que mida solo el tope del día
    # El 30/9: la reserva fija de 20 para Meta dejaba el cupo en 8.
    monkeypatch.setattr(auto, "RAMPA_INICIAL", 100)
    monkeypatch.delenv("DISCOVERY_EMAILS", raising=False)
    assert auto.cupo_del_dia(db)["por_dia"] == auto.CUOTA_DIA - auto.MARGEN_DIA


def test_sin_discovery_no_se_le_reserva_nada(db, monkeypatch):
    monkeypatch.setattr(auto, "CUOTA_MES", 10 ** 6)   # que mida solo el tope del día
    monkeypatch.setattr(auto, "RAMPA_INICIAL", 100)
    _otros_envios(db, 50, tipo="discovery", hace="2 days")
    monkeypatch.delenv("DISCOVERY_EMAILS", raising=False)
    sin = auto.cupo_del_dia(db)["por_dia"]
    monkeypatch.setenv("DISCOVERY_EMAILS", "on")
    assert auto.cupo_del_dia(db)["por_dia"] == sin - 50



# ─── 1/10: el buscador tomó press@linktr.ee de la «web» de un restaurante ───

def test_no_se_le_escribe_a_la_casilla_de_una_plataforma(db, enviados):
    _local(db, 1, email="press@linktr.ee")
    _local(db, 2, email="hola@instagram.com")
    ok = _local(db, 3)
    assert [p["id"] for p in auto.a_contactar(db, 10)] == [ok]


def test_al_que_ya_le_llego_no_le_sigue_la_secuencia(db, enviados):
    pid = _local(db, 1)
    auto.enviar(db, "https://crm")
    _sql(db, "UPDATE fid_prospectos SET email = 'press@linktr.ee' WHERE id = ?", (pid,))
    _sql(db, "UPDATE fid_mails_auto SET email = 'press@linktr.ee'")
    _atrasar(db, 20)
    assert auto.enviar(db, "https://crm")["seguimientos"] == 0


def test_el_buscador_no_guarda_mails_de_plataformas(db):
    pid = _local(db, 1, email="", web="https://linktr.ee/resto")
    r = auto.guardar_mails_encontrados(db, [{"id": pid, "email": "press@linktr.ee", "abrio": True}])
    assert r["encontrados"] == 0 and r["sin_mail"] == 1
    assert not fid.get_prospecto(db, pid)["email"]
