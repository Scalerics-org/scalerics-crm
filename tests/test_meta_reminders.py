import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from database import init_db
from services.meta_reminders import (
    _PISO_ENTRE_CONTACTOS_DIAS,
    dar_de_baja,
    enviar_recordatorios,
    esta_dado_de_baja,
    leads_a_recordar,
    registrar_envio,
)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


def test_registrar_envio_es_una_sola_vez(db):
    token = registrar_envio(db, 42, 1)
    assert token, "tiene que devolver un token"

    with pytest.raises(sqlite3.IntegrityError):
        registrar_envio(db, 42, 1)


def test_registrar_envio_numera_los_contactos(db):
    from services.meta_reminders import contactos_enviados

    t1 = registrar_envio(db, 30, 1)
    t2 = registrar_envio(db, 30, 2)

    assert t1 != t2, "cada contacto lleva su propio token"
    assert contactos_enviados(db, 30) == 2


def test_no_se_puede_mandar_dos_veces_el_mismo_contacto(db):
    registrar_envio(db, 31, 1)

    with pytest.raises(sqlite3.IntegrityError):
        registrar_envio(db, 31, 1)


def test_la_baja_en_un_contacto_vale_para_toda_la_secuencia(db):
    registrar_envio(db, 32, 1)
    token2 = registrar_envio(db, 32, 2)

    assert esta_dado_de_baja(db, 32) is False
    assert dar_de_baja(db, token2) is True
    assert esta_dado_de_baja(db, 32) is True, (
        "se dio de baja en el contacto 2: no puede recibir el 3 ni los trimestrales")


def test_la_baja_marca_al_lead(db):
    token = registrar_envio(db, 7, 1)
    assert esta_dado_de_baja(db, 7) is False

    assert dar_de_baja(db, token) is True
    assert esta_dado_de_baja(db, 7) is True


def test_un_token_que_no_existe_no_rompe(db):
    assert dar_de_baja(db, "token-inventado") is False


def test_las_fechas_se_guardan_en_el_formato_que_compara(db):
    """sent_at y unsubscribed_at tienen que usar '%Y-%m-%d %H:%M:%S' UTC, igual
    que scraped_at: es el formato con el que datetime('now', ...) compara."""
    token = registrar_envio(db, 500, 1)
    dar_de_baja(db, token)

    conn = sqlite3.connect(db)
    try:
        sent_at, unsub_at = conn.execute(
            "SELECT sent_at, unsubscribed_at FROM meta_reminders WHERE business_id = 500"
        ).fetchone()
        (recientes,) = conn.execute(
            "SELECT COUNT(*) FROM meta_reminders WHERE sent_at >= datetime('now','-1 day')"
        ).fetchone()
        (viejos,) = conn.execute(
            "SELECT COUNT(*) FROM meta_reminders WHERE sent_at < datetime('now','-1 day')"
        ).fetchone()
    finally:
        conn.close()

    patron = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
    assert re.fullmatch(patron, sent_at), f"sent_at no compara: {sent_at!r}"
    assert re.fullmatch(patron, unsub_at), f"unsubscribed_at no compara: {unsub_at!r}"
    assert recientes == 1, "un envio de recien tiene que caer dentro de las ultimas 24 horas"
    assert viejos == 0


def test_la_pagina_de_baja_funciona_sin_login(tmp_path):
    from dashboard import create_app
    from services.meta_reminders import esta_dado_de_baja, registrar_envio

    ruta = str(tmp_path / "baja.db")
    init_db(ruta)  # create_app NO crea las tablas: eso lo hace server.py aparte
    app = create_app(ruta)
    app.config["TESTING"] = True
    token = registrar_envio(ruta, 99, 1)

    r = app.test_client().get(f"/baja/{token}")

    assert r.status_code == 200, "la pagina de baja no puede exigir login"
    assert esta_dado_de_baja(ruta, 99) is True


def test_la_baja_en_un_click_de_gmail_llega_por_post(tmp_path):
    """El boton nativo de Gmail hace POST (RFC 8058). Hoy la ruta era solo GET
    y habria contestado 405."""
    from dashboard import create_app
    from services.meta_reminders import esta_dado_de_baja, registrar_envio

    ruta = str(tmp_path / "baja_post.db")
    init_db(ruta)
    app = create_app(ruta)
    app.config["TESTING"] = True
    token = registrar_envio(ruta, 98, 1)

    r = app.test_client().post(f"/baja/{token}")

    assert r.status_code == 200
    assert esta_dado_de_baja(ruta, 98) is True


def test_la_pagina_de_baja_no_se_indexa(tmp_path):
    from dashboard import create_app
    from services.meta_reminders import registrar_envio

    ruta = str(tmp_path / "baja_noindex.db")
    init_db(ruta)
    app = create_app(ruta)
    app.config["TESTING"] = True
    token = registrar_envio(ruta, 97, 1)

    cuerpo = app.test_client().get(f"/baja/{token}").get_data(as_text=True)

    assert 'name="robots"' in cuerpo and "noindex" in cuerpo, (
        "es una URL publica con token adentro"
    )


def test_la_pagina_de_baja_con_token_invalido_no_rompe(tmp_path):
    from dashboard import create_app

    ruta = str(tmp_path / "baja2.db")
    init_db(ruta)
    app = create_app(ruta)
    app.config["TESTING"] = True

    r = app.test_client().get("/baja/no-existe")

    assert r.status_code == 200, "un token viejo o mal copiado muestra una pagina, no un error"


def _lead(conn, bid, dias, **kw):
    campos = {
        "crm_status": "sin_contactar",
        "email": f"lead{bid}@ejemplo.com",
        "source": "meta",
        "form_data": json.dumps({
            "¿cómo_se_llama_tu_negocio?": f"Negocio {bid}",
            "¿que_es_lo_que_buscás_para_tu_negocio?": "una_nueva_página_web",
        }),
    }
    campos.update(kw)
    cuando = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO businesses (id, name, email, source, crm_status, form_data, scraped_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (bid, f"Lead {bid}", campos["email"], campos["source"],
         campos["crm_status"], campos["form_data"], cuando),
    )


def test_elige_solo_a_los_que_corresponde(db):
    conn = sqlite3.connect(db)
    _lead(conn, 1, dias=5)                                  # elegible
    _lead(conn, 2, dias=1)                                  # muy nuevo
    _lead(conn, 3, dias=5, crm_status="no_interesa")        # dijo que no: no recibe
    _lead(conn, 4, dias=5, email=None)                      # sin mail
    _lead(conn, 5, dias=5, source="google")                 # no es de Meta
    _lead(conn, 6, dias=5, crm_status="reunion_agendada")   # tiene reunion: no se lo molesta
    _lead(conn, 7, dias=5, crm_status="finalizado")         # ya es cliente
    conn.commit()
    conn.close()

    elegidos = [x["id"] for x in leads_a_recordar(db)]

    assert elegidos == [1]


def test_no_repite_a_quien_ya_recibio(db):
    conn = sqlite3.connect(db)
    _lead(conn, 10, dias=5)
    conn.commit()
    conn.close()

    assert [x["id"] for x in leads_a_recordar(db)] == [10]
    registrar_envio(db, 10, 1)
    assert leads_a_recordar(db) == []


def test_respeta_el_limite_y_completa_el_cupo_con_los_mas_viejos(db):
    conn = sqlite3.connect(db)
    for i, dias in enumerate([5, 40, 20], start=20):
        _lead(conn, i, dias=dias)
    conn.commit()
    conn.close()

    elegidos = [x["id"] for x in leads_a_recordar(db, limite=2)]

    assert elegidos == [20, 21], (
        "primero el recien elegible (5 dias) y despues el mas viejo del backlog (40)"
    )


def test_un_lead_nuevo_no_espera_a_que_drene_el_backlog(db):
    """El spec promete que un lead nuevo con mail recibe el recordatorio a los 3
    dias. Con ORDER BY scraped_at ASC a secas quedaba detras de todo el backlog,
    o sea a ~12 dias."""
    conn = sqlite3.connect(db)
    for i in range(20):
        _lead(conn, 300 + i, dias=60 + i)
    _lead(conn, 400, dias=4)
    conn.commit()
    conn.close()

    elegidos = [x["id"] for x in leads_a_recordar(db)]

    assert len(elegidos) == 15
    assert elegidos[0] == 400, "el lead de 4 dias es el mas caliente: va primero"
    assert elegidos[1:] == [319, 318, 317, 316, 315, 314, 313, 312, 311, 310, 309, 308, 307, 306], (
        "el resto del cupo lo completan los mas viejos del backlog"
    )


def test_dos_filas_con_el_mismo_mail_reciben_un_solo_recordatorio(db):
    """La garantia declarada es sobre personas, no sobre filas de businesses:
    dos envios del formulario con el telefono escrito distinto no fusionan, y
    la persona recibe dos mails el mismo dia."""
    conn = sqlite3.connect(db)
    _lead(conn, 501, dias=10, email="Repetido@Ejemplo.com")
    _lead(conn, 502, dias=5, email="  repetido@ejemplo.com ")
    _lead(conn, 503, dias=8, email="otro@ejemplo.com")
    conn.commit()
    conn.close()

    elegidos = [x["id"] for x in leads_a_recordar(db)]

    assert sorted(elegidos) == [501, 503], (
        "una sola fila por direccion, y de las repetidas la mas vieja"
    )


def test_al_dia_siguiente_tampoco_le_llega_a_la_fila_gemela(db):
    conn = sqlite3.connect(db)
    _lead(conn, 511, dias=10, email="repetido2@ejemplo.com")
    _lead(conn, 512, dias=5, email="REPETIDO2@ejemplo.com")
    conn.commit()
    conn.close()

    registrar_envio(db, 511, 1)

    assert leads_a_recordar(db) == [], "esa direccion ya recibio su mail"


def test_trae_los_datos_para_personalizar(db):
    conn = sqlite3.connect(db)
    _lead(conn, 30, dias=5)
    conn.commit()
    conn.close()

    lead = leads_a_recordar(db)[0]

    assert lead["negocio"] == "Negocio 30"
    assert lead["rubro"] == "una nueva página web", "los guiones bajos se limpian"


def test_correr_dos_veces_manda_un_solo_mail(db):
    conn = sqlite3.connect(db)
    _lead(conn, 50, dias=5)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok") as enviar:
        primera = enviar_recordatorios(db, "https://crm")
        segunda = enviar_recordatorios(db, "https://crm")

    assert primera["enviados"] == 1
    assert segunda["enviados"] == 0, "la segunda corrida no le escribe de nuevo"
    assert enviar.call_count == 1


def _ya_enviado(conn, bid, dias):
    cuando = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO meta_reminders (business_id, token, sent_at) VALUES (?,?,?)",
        (bid, f"token-{bid}", cuando),
    )


def test_el_tope_de_15_es_por_dia_no_por_corrida(db):
    """Sin esto, cada reinicio de la maquina dispara otros 15 mails."""
    conn = sqlite3.connect(db)
    _lead(conn, 90, dias=5)
    for i in range(15):
        _ya_enviado(conn, 1000 + i, dias=0)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok") as enviar:
        res = enviar_recordatorios(db, "https://crm")

    assert enviar.called is False, "ya se mandaron 15 en las ultimas 24 horas"
    assert res["enviados"] == 0
    assert res["candidatos"] == 0


def test_pasado_el_dia_la_cuota_se_renueva(db):
    conn = sqlite3.connect(db)
    _lead(conn, 91, dias=5)
    for i in range(15):
        _ya_enviado(conn, 1100 + i, dias=3)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok") as enviar:
        res = enviar_recordatorios(db, "https://crm")

    assert enviar.call_count == 1, "los 15 de hace 3 dias no gastan la cuota de hoy"
    assert res["enviados"] == 1


def test_la_cuota_del_dia_es_lo_que_queda(db, monkeypatch):
    import services.meta_reminders as mr
    monkeypatch.setattr(mr, "_PAUSA_ENTRE_ENVIOS", 0)

    conn = sqlite3.connect(db)
    for i in range(5):
        _lead(conn, 200 + i, dias=5 + i)
    for i in range(13):
        _ya_enviado(conn, 1200 + i, dias=0)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok") as enviar:
        res = enviar_recordatorios(db, "https://crm")

    assert enviar.call_count == 2, "quedaban 2 de los 15 del dia"
    assert res["enviados"] == 2


def test_el_cupo_se_relee_antes_de_cada_envio(db, monkeypatch):
    """Si el cupo se leyera una sola vez al empezar, una corrida que arranca en
    el medio de otra (cada reinicio de Fly dispara una tanda) manda sus 15
    completos y salen hasta 30 en la ventana de 24 horas. Releerlo hace que la
    segunda corte apenas la ventana llega al tope."""
    import services.meta_reminders as mr
    monkeypatch.setattr(mr, "_PAUSA_ENTRE_ENVIOS", 0)

    conn = sqlite3.connect(db)
    for i in range(3):
        _lead(conn, 250 + i, dias=5 + i)
    conn.commit()
    conn.close()

    # La ventana esta vacia al empezar la tanda y llega al tope despues del
    # primer envio, como si otra corrida hubiera mandado sus mails en el medio.
    lecturas = iter([0, 0, 15, 15])
    monkeypatch.setattr(mr, "enviados_ultimas_24h", lambda _: next(lecturas))

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok") as enviar:
        res = enviar_recordatorios(db, "https://crm")

    assert enviar.call_count == 1, "la tanda corta apenas la ventana llega al tope"
    assert res["enviados"] == 1
    assert res["candidatos"] == 3, "los otros dos siguen elegibles para la proxima tanda"


def test_dry_run_no_manda_ni_registra(db):
    conn = sqlite3.connect(db)
    _lead(conn, 60, dias=5)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder") as enviar:
        res = enviar_recordatorios(db, "https://crm", dry_run=True)

    assert enviar.called is False
    assert res["candidatos"] == 1
    assert leads_a_recordar(db), "sigue elegible: el dry-run no registro nada"


def test_el_dry_run_lista_igual_con_el_cupo_agotado(db):
    """El dry-run no manda ni registra nada, asi que no gasta cupo y no tiene
    por que respetarlo. En produccion el cupo esta en 0 la mayor parte del dia:
    si cortara por cupo, la herramienta de diagnostico mas segura del sistema
    seria inutilizable justo cuando hace falta."""
    conn = sqlite3.connect(db)
    _lead(conn, 61, dias=5)
    for i in range(15):
        _ya_enviado(conn, 1300 + i, dias=0)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder") as enviar:
        res = enviar_recordatorios(db, "https://crm", dry_run=True)

    assert enviar.called is False
    assert res["candidatos"] == 1, "el cupo agotado no puede vaciar la lista del dry-run"


def test_con_el_cupo_agotado_la_tanda_real_sigue_sin_mandar_nada(db):
    """El espejo del test de arriba: aflojar el cupo en dry-run no puede
    aflojarlo en el camino que manda mails de verdad."""
    conn = sqlite3.connect(db)
    _lead(conn, 62, dias=5)
    for i in range(15):
        _ya_enviado(conn, 1400 + i, dias=0)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder") as enviar:
        res = enviar_recordatorios(db, "https://crm")

    assert enviar.called is False
    assert res["candidatos"] == 0
    assert res["enviados"] == 0


def test_el_log_final_desglosa_seguimientos_y_nuevos(db, monkeypatch):
    """En Fly el log es la unica superficie de diagnostico: sin el desglose no
    hay forma de saber si el backlog de contactos 1 esta drenando o si los
    seguimientos se estan comiendo el cupo."""
    import services.meta_reminders as mr
    monkeypatch.setattr(mr, "_PAUSA_ENTRE_ENVIOS", 0)

    conn = sqlite3.connect(db)
    _lead(conn, 63, dias=60)
    _envio(conn, 63, 1, dias_atras=20)
    _lead(conn, 64, dias=5)
    _lead(conn, 65, dias=6)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok"):
        res = enviar_recordatorios(db, "https://crm")

    assert res["seguimientos"] == 1
    assert res["nuevos"] == 2
    assert res["candidatos"] == 3


def test_el_override_intercepta_el_destinatario(db, monkeypatch, caplog):
    """META_NOTIFY_OVERRIDE es la valvula para probar el camino completo contra
    una casilla propia sin escribirle a un lead real."""
    conn = sqlite3.connect(db)
    _lead(conn, 95, dias=5)
    conn.commit()
    conn.close()

    monkeypatch.setenv("META_NOTIFY_OVERRIDE", "yo@gmail.com")

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok") as enviar, \
         caplog.at_level(logging.WARNING, logger="services.meta_reminders"):
        res = enviar_recordatorios(db, "https://crm")

    assert enviar.call_args.args[0] == "yo@gmail.com", "el mail no puede irle al lead"
    assert res["enviados"] == 1
    assert "lead95@ejemplo.com" in caplog.text, "el log tiene que decir a quien le hubiera ido"
    assert "META_NOTIFY_OVERRIDE" in caplog.text
    assert leads_a_recordar(db) == [], "el registro se hace igual: es una prueba del camino entero"


def test_sin_override_el_mail_le_va_al_lead(db, monkeypatch):
    conn = sqlite3.connect(db)
    _lead(conn, 96, dias=5)
    conn.commit()
    conn.close()

    monkeypatch.delenv("META_NOTIFY_OVERRIDE", raising=False)

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok") as enviar:
        enviar_recordatorios(db, "https://crm")

    assert enviar.call_args.args[0] == "lead96@ejemplo.com"


def test_si_el_mail_falla_no_lo_da_por_enviado(db):
    conn = sqlite3.connect(db)
    _lead(conn, 70, dias=5)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="fallo"):
        res = enviar_recordatorios(db, "https://crm")

    assert res["enviados"] == 0
    assert res["fallidos"] == 1
    assert leads_a_recordar(db), "si no salio, tiene que poder reintentarse manana"


def test_un_fallo_seguro_borra_el_registro_y_manana_se_reintenta(db):
    """'fallo' es Resend diciendo que no: el mail no salio, se puede reintentar."""
    conn = sqlite3.connect(db)
    _lead(conn, 71, dias=5)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="fallo"):
        res = enviar_recordatorios(db, "https://crm")

    conn = sqlite3.connect(db)
    try:
        (filas,) = conn.execute(
            "SELECT COUNT(*) FROM meta_reminders WHERE business_id = 71"
        ).fetchone()
    finally:
        conn.close()

    assert filas == 0, "un fallo seguro borra el registro"
    assert res["fallidos"] == 1
    assert res.get("inciertos", 0) == 0
    assert [x["id"] for x in leads_a_recordar(db)] == [71], "manana se reintenta"


def test_un_fallo_en_un_seguimiento_no_borra_los_contactos_anteriores(db):
    """El DELETE de limpieza tiene que filtrar por numero: un fallo en el
    contacto 2 no puede llevarse puesto el contacto 1 ya mandado, con su token
    de baja ya publicado en un mail que la persona recibio de verdad."""
    conn = sqlite3.connect(db)
    _lead(conn, 74, dias=60)
    _envio(conn, 74, 1, dias_atras=20)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="fallo"):
        res = enviar_recordatorios(db, "https://crm")

    conn = sqlite3.connect(db)
    try:
        filas = conn.execute(
            "SELECT numero, token FROM meta_reminders WHERE business_id = 74 ORDER BY numero"
        ).fetchall()
    finally:
        conn.close()

    assert [f[0] for f in filas] == [1], (
        "solo tiene que quedar el contacto 1: el 2 (el que fallo) se borra, "
        "el 1 (ya mandado antes) no")
    assert filas[0][1] == "tok-74-1", "el token del contacto 1 no se toca"
    assert res["fallidos"] == 1


def test_un_envio_incierto_deja_la_fila_y_el_token_puestos(db, caplog):
    """Un timeout pudo haber mandado el mail igual. Si borramos la fila, manana
    le llega un segundo mail — y si la persona se dio de baja con el link de ese
    primer mail, el token ya no existe y la baja no lo protege."""
    conn = sqlite3.connect(db)
    _lead(conn, 72, dias=5)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="desconocido"), \
         caplog.at_level(logging.ERROR, logger="services.meta_reminders"):
        res = enviar_recordatorios(db, "https://crm")

    conn = sqlite3.connect(db)
    try:
        fila = conn.execute(
            "SELECT token FROM meta_reminders WHERE business_id = 72"
        ).fetchone()
    finally:
        conn.close()

    assert fila, "ante la duda la fila se queda: mandar de menos, no de mas"
    assert res["enviados"] == 0
    assert res["fallidos"] == 0, "un incierto no es un fallo"
    assert res["inciertos"] == 1
    assert "72" in caplog.text, "el incierto tiene que quedar en el log con el business_id"
    assert leads_a_recordar(db) == [], "manana NO se le vuelve a escribir"

    assert dar_de_baja(db, fila[0]) is True, "el link de baja de ese mail sigue sirviendo"
    assert esta_dado_de_baja(db, 72) is True


@pytest.mark.parametrize("valor, arranca", [
    (None, False),
    ("", False),
    ("off", False),
    ("cualquier_cosa", False),
    ("on", True),
    ("ON", True),
])
def test_el_job_solo_arranca_con_la_variable_en_on(monkeypatch, valor, arranca):
    """Una automatizacion que le escribe a terceros no puede fallar hacia
    'encendida': Fly reinicia la maquina para aplicar un secret, y el hilo
    corre 180s despues de cada boot."""
    from services import meta_reminders

    if valor is None:
        monkeypatch.delenv("META_RECORDATORIOS", raising=False)
    else:
        monkeypatch.setenv("META_RECORDATORIOS", valor)

    with patch("services.meta_reminders.threading.Thread") as hilo:
        meta_reminders.start_meta_reminders(object())

    assert hilo.called is arranca


class _ConexionQueFallaAlBorrar:
    """Envuelve una conexion real de sqlite3 y hace fallar solo el DELETE de
    limpieza de meta_reminders, para simular un "database is locked" en ese
    paso puntual sin tocar el resto de las consultas. Todo lo demas (incluido
    row_factory, que leads_a_recordar necesita) se delega a la conexion real."""

    def __init__(self, real):
        object.__setattr__(self, "_real", real)

    def execute(self, sql, *args, **kwargs):
        if sql.strip().startswith("DELETE FROM meta_reminders"):
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        setattr(self._real, name, value)


class _ConexionQueFallaAlRegistrar:
    """Igual que la anterior, pero hace fallar el INSERT del registro de envio
    de un business_id puntual, para simular un 'database is locked' ahi."""

    def __init__(self, real, ids_que_fallan):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_ids", ids_que_fallan)

    def execute(self, sql, *args, **kwargs):
        if sql.strip().startswith("INSERT INTO meta_reminders") and args:
            if args[0][0] in self._ids:
                raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        setattr(self._real, name, value)


def test_un_lock_al_registrar_el_envio_no_aborta_la_tanda(db, caplog):
    """La app tiene worker, import diario y webhooks escribiendo en la misma
    base: un OperationalError por lock no puede llevarse puesta la tanda del
    dia entera. Y sin registro no se manda: mandar de menos."""
    conn = sqlite3.connect(db)
    _lead(conn, 82, dias=6)
    _lead(conn, 83, dias=5)
    conn.commit()
    conn.close()

    conectar_real = sqlite3.connect

    def _conn_que_falla(db_path):
        return _ConexionQueFallaAlRegistrar(conectar_real(db_path), {82})

    with patch("services.meta_reminders._conn", side_effect=_conn_que_falla), \
         patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok") as enviar, \
         caplog.at_level(logging.WARNING, logger="services.meta_reminders"):
        res = enviar_recordatorios(db, "https://crm")

    assert res["candidatos"] == 2
    assert res["enviados"] == 1, "el lock se llevo solo al lead 82, no a la tanda"
    assert [c.args[0] for c in enviar.call_args_list] == ["lead83@ejemplo.com"], (
        "al lead que no se pudo registrar no se le manda nada"
    )
    assert "82" in caplog.text


def test_si_el_borrado_de_limpieza_tambien_falla_no_aborta_la_tanda(db, caplog):
    conn = sqlite3.connect(db)
    _lead(conn, 80, dias=6)
    _lead(conn, 81, dias=5)
    conn.commit()
    conn.close()

    conectar_real = sqlite3.connect

    def _conn_que_falla(db_path):
        return _ConexionQueFallaAlBorrar(conectar_real(db_path))

    with patch("services.meta_reminders._conn", side_effect=_conn_que_falla), \
         patch("services.meta_reminders.send_meta_lead_reminder", return_value="fallo"), \
         caplog.at_level(logging.ERROR, logger="services.meta_reminders"):
        res = enviar_recordatorios(db, "https://crm")

    assert res["candidatos"] == 2
    assert res["enviados"] == 0
    assert res["fallidos"] == 2, "no exploto: proceso a los dos candidatos aunque el borrado fallara"
    assert "80" in caplog.text and "81" in caplog.text, (
        "el fallo de limpieza tiene que quedar visible en el log, con el id del lead"
    )


def test_un_estado_inesperado_no_borra_la_fila(db, caplog):
    """La rama que borra es la explicita, no el `else`.

    Si el `else` fuera el destructivo, cualquier valor que no sea uno de los
    tres estados — None, un mock sin configurar, un cuarto estado que alguien
    agregue mas adelante — le mandaria un segundo mail a una persona real. El
    default tiene que ser el silencio, que se arregla a mano.
    """
    conn = sqlite3.connect(db)
    _lead(conn, 90, dias=5)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value=None), \
         caplog.at_level(logging.ERROR, logger="services.meta_reminders"):
        res = enviar_recordatorios(db, "https://crm")

    conn = sqlite3.connect(db)
    try:
        fila = conn.execute(
            "SELECT token FROM meta_reminders WHERE business_id = 90"
        ).fetchone()
    finally:
        conn.close()

    assert fila, "un estado que no reconocemos se trata como incierto: la fila se queda"
    assert res["fallidos"] == 0
    assert res["inciertos"] == 1
    assert "None" in caplog.text, "el log tiene que decir que estado raro llego"
    assert leads_a_recordar(db) == [], "y manana no se le vuelve a escribir"


def _envio(conn, bid, numero, dias_atras):
    from datetime import datetime, timedelta, timezone
    cuando = (datetime.now(timezone.utc) - timedelta(days=dias_atras)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO meta_reminders (business_id, numero, token, sent_at) "
                 "VALUES (?,?,?,?)", (bid, numero, f"tok-{bid}-{numero}", cuando))


def test_el_seguimiento_espera_los_dias_que_corresponden(db):
    """El contacto 2 va a los 10 dias del primero, no antes."""
    from services.meta_reminders import leads_a_seguir

    conn = sqlite3.connect(db)
    _lead(conn, 40, dias=30)
    _envio(conn, 40, 1, dias_atras=9)
    _lead(conn, 41, dias=30)
    _envio(conn, 41, 1, dias_atras=10)
    conn.commit()
    conn.close()

    elegidos = leads_a_seguir(db)

    assert [x["id"] for x in elegidos] == [41]
    assert elegidos[0]["numero"] == 2


def test_los_trimestrales_se_cuentan_desde_el_primer_envio(db):
    """El ancla es el primer contacto, no el anterior: asi el atraso de una
    tanda no se acumula sobre los siguientes."""
    from services.meta_reminders import leads_a_seguir

    conn = sqlite3.connect(db)
    _lead(conn, 42, dias=200)
    _envio(conn, 42, 1, dias_atras=120)
    _envio(conn, 42, 2, dias_atras=110)
    _envio(conn, 42, 3, dias_atras=95)
    conn.commit()
    conn.close()

    elegidos = leads_a_seguir(db)

    assert [x["id"] for x in elegidos] == [42], "el 4 va a los 115 dias del primero"
    assert elegidos[0]["numero"] == 4


def test_el_septimo_es_el_ultimo_de_la_vida(db):
    from services.meta_reminders import leads_a_seguir

    conn = sqlite3.connect(db)
    _lead(conn, 43, dias=500)
    for n, dias in ((1, 400), (2, 390), (3, 375), (4, 285), (5, 195), (6, 105), (7, 35)):
        _envio(conn, 43, n, dias_atras=dias)
    conn.commit()
    conn.close()

    assert leads_a_seguir(db) == [], "despues del 7 no vuelve a entrar nunca"


def test_el_que_se_dio_de_baja_no_recibe_el_siguiente(db):
    from services.meta_reminders import leads_a_seguir

    conn = sqlite3.connect(db)
    _lead(conn, 44, dias=30)
    _envio(conn, 44, 1, dias_atras=20)
    conn.execute("UPDATE meta_reminders SET unsubscribed_at = ? WHERE business_id = ?",
                 ("2026-08-18 10:00:00", 44))
    conn.commit()
    conn.close()

    assert leads_a_seguir(db) == []


def test_el_que_dejo_de_estar_sin_contactar_no_recibe_el_siguiente(db):
    """Es la unica senal de corte cuando alguien contesta el mail."""
    from services.meta_reminders import leads_a_seguir

    conn = sqlite3.connect(db)
    _lead(conn, 45, dias=30, crm_status="interesado")
    _envio(conn, 45, 1, dias_atras=20)
    conn.commit()
    conn.close()

    assert leads_a_seguir(db) == []


def test_una_fila_salteada_no_repite_numero(db):
    """Un borrado manual de la fila del 2 (la forma documentada de reintentar)
    deja los contactos 1 y 3 sin el 2. El numero que corresponde es el
    siguiente al MAS ALTO ya mandado (4), no COUNT(*)+1 (3): ese ya existe y
    choca contra el UNIQUE(business_id, numero), lo que en enviar_recordatorios
    se confunde con 'otra corrida se adelanto' y deja al lead trabado para
    siempre sin ningun rastro en el log."""
    from services.meta_reminders import leads_a_seguir

    conn = sqlite3.connect(db)
    _lead(conn, 3000, dias=200)
    _envio(conn, 3000, 1, dias_atras=120)
    _envio(conn, 3000, 3, dias_atras=50)
    conn.commit()
    conn.close()

    elegidos = leads_a_seguir(db)

    assert [x["id"] for x in elegidos] == [3000]
    assert elegidos[0]["numero"] == 4, "el 3 ya existe; asignarlo de nuevo chocaria contra el UNIQUE"


def test_un_lead_atrasado_no_recibe_dos_contactos_seguidos(db):
    """Contar desde el primer envio hace que un lead atrasado pase TODOS los
    umbrales de golpe: le tocaria el 2, y apenas sale, tambien el 3, el 4 y
    hasta el 7. No hace falta esperar a manana — cada reinicio de Fly dispara
    una tanda, asi que los seis pueden salir dentro de la misma hora, y esa
    persona recibe "te escribimos hace unos dias" y "no te escribimos mas"
    seguidos. Ni el UNIQUE(business_id, numero) lo impide (son numeros
    distintos) ni el tope de 15 (son 6 de 15). El piso entre contactos
    consecutivos es lo unico que lo frena."""
    from services.meta_reminders import leads_a_seguir

    conn = sqlite3.connect(db)
    _lead(conn, 46, dias=420)
    _envio(conn, 46, 1, dias_atras=400)
    conn.commit()
    conn.close()

    elegidos = leads_a_seguir(db)
    assert [x["numero"] for x in elegidos] == [2], "hoy le toca el 2, que quedo atrasado"

    # Sale el contacto 2 ahora mismo, y se pregunta de nuevo: la tanda
    # siguiente puede ser dentro de un minuto.
    conn = sqlite3.connect(db)
    _envio(conn, 46, 2, dias_atras=0)
    conn.commit()
    conn.close()

    assert leads_a_seguir(db) == [], (
        "el 3 no puede salir pegado al 2 aunque hayan pasado 400 dias del primer envio")


def test_pasado_el_piso_el_lead_atrasado_retoma(db):
    """El piso frena la rafaga, no la secuencia: pasados los dias del piso el
    lead atrasado sigue avanzando, un contacto por vez."""
    from services.meta_reminders import leads_a_seguir

    conn = sqlite3.connect(db)
    _lead(conn, 47, dias=420)
    _envio(conn, 47, 1, dias_atras=400)
    _envio(conn, 47, 2, dias_atras=_PISO_ENTRE_CONTACTOS_DIAS)
    conn.commit()
    conn.close()

    elegidos = leads_a_seguir(db)

    assert [x["numero"] for x in elegidos] == [3]


def test_el_mail_sale_sin_espacios_alrededor(db):
    """La dedup compara con TRIM, asi que un mail con espacios entra igual al
    grupo; si despues lo mandamos crudo, Resend lo rechaza, eso cuenta como
    fallo, se borra la fila y el lead se reintenta todos los dias para siempre."""
    conn = sqlite3.connect(db)
    _lead(conn, 91, dias=5, email="  Ana@Ejemplo.com  ")
    conn.commit()
    conn.close()

    assert [x["email"] for x in leads_a_recordar(db)] == ["Ana@Ejemplo.com"]


def test_un_seguimiento_entra_en_la_tanda_con_su_numero(db, monkeypatch):
    """Con la tanda llena de contactos nuevos, el seguimiento igual entra y lo
    hace con su `numero` (2), no con el 1 de un contacto nuevo: es el cableado
    del numero desde `leads_a_seguir` hasta `send_meta_lead_reminder`.

    Este test NO discrimina el orden: 14 nuevos + 1 seguimiento dan 15 tambien
    con la prioridad invertida. El que prueba la prioridad es su hermano de mas
    abajo, con 20 seguimientos disponibles para un cupo de 15."""
    import services.meta_reminders as mr
    monkeypatch.setattr(mr, "_PAUSA_ENTRE_ENVIOS", 0)
    from services.meta_reminders import enviar_recordatorios

    conn = sqlite3.connect(db)
    for i in range(50, 50 + 14):
        _lead(conn, i, dias=30)
    _lead(conn, 90, dias=60)
    _envio(conn, 90, 1, dias_atras=20)
    conn.commit()
    conn.close()

    mandados = []
    def fake(to, negocio, rubro, url, numero=1, estado="sin_contactar"):
        mandados.append((to, numero))
        return "ok"

    with patch("services.meta_reminders.send_meta_lead_reminder", side_effect=fake):
        res = enviar_recordatorios(db, "https://crm")

    assert res["enviados"] == 15
    assert ("lead90@ejemplo.com", 2) in mandados, "el seguimiento entra en la tanda"


def test_el_tope_diario_cuenta_juntos_seguimientos_y_nuevos(db, monkeypatch):
    """Ademas de sumar 15 entre los dos tipos, los seguimientos tienen que
    ganarle el cupo a los nuevos: con 20 seguimientos disponibles y cupo 15,
    los 15 que salen tienen que ser seguimientos (numero > 1), no una mezcla."""
    import services.meta_reminders as mr
    monkeypatch.setattr(mr, "_PAUSA_ENTRE_ENVIOS", 0)
    from services.meta_reminders import enviar_recordatorios

    conn = sqlite3.connect(db)
    for i in range(100, 120):
        _lead(conn, i, dias=60)
        _envio(conn, i, 1, dias_atras=20)
    for i in range(200, 210):
        _lead(conn, i, dias=30)
    conn.commit()
    conn.close()

    mandados = []
    def fake(to, negocio, rubro, url, numero=1, estado="sin_contactar"):
        mandados.append((to, numero))
        return "ok"

    with patch("services.meta_reminders.send_meta_lead_reminder", side_effect=fake):
        res = enviar_recordatorios(db, "https://crm")

    assert res["enviados"] == 15, "15 en total, no 15 de cada tipo"
    assert len(mandados) == 15
    assert all(numero > 1 for _, numero in mandados), (
        "con 20 seguimientos disponibles para un cupo de 15, los 15 que salen "
        "tienen que ser todos seguimientos: si los nuevos entraran antes, "
        "saldrian 10 nuevos + 5 seguimientos")


def test_el_numero_que_se_registra_es_el_que_se_mando(db):
    from services.meta_reminders import enviar_recordatorios

    conn = sqlite3.connect(db)
    _lead(conn, 300, dias=60)
    _envio(conn, 300, 1, dias_atras=20)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok"):
        enviar_recordatorios(db, "https://crm")

    conn = sqlite3.connect(db)
    try:
        numeros = [r[0] for r in conn.execute(
            "SELECT numero FROM meta_reminders WHERE business_id = 300 ORDER BY numero")]
    finally:
        conn.close()

    assert numeros == [1, 2]


# ── Secuencias por estado ────────────────────────────────────────────────────
# El contador es por (lead, estado), no por lead: un lead que cambia de estado
# empieza la secuencia nueva en el contacto 1. Antes de esto habria recibido el
# contacto 2 de una secuencia que nunca empezo, y con el texto equivocado.

def _envio_estado(conn, bid, estado, numero, dias_atras):
    """Mete a mano una fila de meta_reminders con fecha, sin pasar por el envio."""
    cuando = (datetime.now(timezone.utc) - timedelta(days=dias_atras)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO meta_reminders (business_id, estado, numero, token, sent_at) "
        "VALUES (?,?,?,?,?)",
        (bid, estado, numero, f"tok-{bid}-{estado}-{numero}", cuando),
    )


def test_cada_estado_con_secuencia_entra_y_dice_cual_es(db):
    conn = sqlite3.connect(db)
    for i, estado in enumerate(
        ["sin_contactar", "llamar_despues", "interesado",
         "reunion_hecha", "presupuesto_enviado"], start=1
    ):
        _lead(conn, i, dias=10, crm_status=estado)
    conn.commit()
    conn.close()

    elegidos = {x["id"]: x["estado"] for x in leads_a_recordar(db)}

    assert elegidos == {
        1: "sin_contactar", 2: "llamar_despues", 3: "interesado",
        4: "reunion_hecha", 5: "presupuesto_enviado",
    }
    assert all(x["numero"] == 1 for x in leads_a_recordar(db))


def test_cambiar_de_estado_arranca_la_secuencia_nueva_en_uno(db):
    conn = sqlite3.connect(db)
    # Recibio dos contactos cuando estaba frio y despues lo llamaron: hoy es
    # 'presupuesto_enviado' y le toca el contacto 1 de ESA secuencia.
    _lead(conn, 1, dias=200, crm_status="presupuesto_enviado")
    _envio_estado(conn, 1, "sin_contactar", 1, dias_atras=100)
    _envio_estado(conn, 1, "sin_contactar", 2, dias_atras=90)
    conn.commit()
    conn.close()

    elegidos = leads_a_recordar(db)

    assert [(x["id"], x["estado"], x["numero"]) for x in elegidos] == \
           [(1, "presupuesto_enviado", 1)]


def test_un_estado_ya_recorrido_no_se_repite(db):
    from services.meta_reminders import leads_a_seguir
    conn = sqlite3.connect(db)
    # 'interesado' tiene un solo contacto y ya lo recibio. Aunque el lead
    # vuelva a ese estado, no le corresponde nada mas.
    _lead(conn, 1, dias=200, crm_status="interesado")
    _envio_estado(conn, 1, "interesado", 1, dias_atras=120)
    conn.commit()
    conn.close()

    assert leads_a_recordar(db) == []
    assert leads_a_seguir(db) == []


def test_la_baja_en_un_estado_corta_todos_los_demas(db):
    conn = sqlite3.connect(db)
    _lead(conn, 1, dias=200, crm_status="presupuesto_enviado")
    _envio_estado(conn, 1, "sin_contactar", 1, dias_atras=100)
    conn.commit()
    token = conn.execute(
        "SELECT token FROM meta_reminders WHERE business_id = 1"
    ).fetchone()[0]
    conn.close()

    assert leads_a_recordar(db), "antes de la baja, le tocaba"
    dar_de_baja(db, token)
    assert leads_a_recordar(db) == [], "la baja es global, no por estado"


def test_el_piso_de_dias_se_mide_sobre_todos_los_estados(db):
    conn = sqlite3.connect(db)
    # Le mandamos ayer con el estado viejo y hoy cambio de estado: el contacto 1
    # de la secuencia nueva no puede salir el mismo dia.
    _lead(conn, 1, dias=200, crm_status="reunion_hecha")
    _envio_estado(conn, 1, "sin_contactar", 1, dias_atras=1)
    conn.commit()
    conn.close()

    assert leads_a_recordar(db) == []

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE meta_reminders SET sent_at = ? WHERE business_id = 1",
        ((datetime.now(timezone.utc) - timedelta(days=_PISO_ENTRE_CONTACTOS_DIAS + 1))
         .strftime("%Y-%m-%d %H:%M:%S"),),
    )
    conn.commit()
    conn.close()

    assert [x["id"] for x in leads_a_recordar(db)] == [1]


def test_el_seguimiento_usa_los_dias_de_su_propio_estado(db):
    from services.meta_reminders import leads_a_seguir
    from services.secuencia_contactos import SECUENCIAS_POR_ESTADO
    dia_del_dos = SECUENCIAS_POR_ESTADO["presupuesto_enviado"][1]

    conn = sqlite3.connect(db)
    _lead(conn, 1, dias=200, crm_status="presupuesto_enviado")
    _envio_estado(conn, 1, "presupuesto_enviado", 1, dias_atras=dia_del_dos - 1)
    _lead(conn, 2, dias=200, crm_status="presupuesto_enviado")
    _envio_estado(conn, 2, "presupuesto_enviado", 1, dias_atras=dia_del_dos + 1)
    conn.commit()
    conn.close()

    # Al 1 todavia no le toca; al 2 si, y con el numero 2 de SU secuencia.
    assert [(x["id"], x["numero"]) for x in leads_a_seguir(db)] == [(2, 2)]


def test_la_migracion_marca_las_filas_viejas_como_sin_contactar(tmp_path):
    """La tabla vieja no tenia estado. Esos mails salieron cuando el filtro solo

    dejaba pasar 'sin_contactar', asi que ese es su estado, y el token tiene que
    sobrevivir: esta publicado dentro de mails que la gente ya recibio.
    """
    ruta = str(tmp_path / "vieja.db")
    conn = sqlite3.connect(ruta)
    conn.execute("""
        CREATE TABLE meta_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            numero INTEGER NOT NULL DEFAULT 1,
            token TEXT NOT NULL UNIQUE,
            sent_at TEXT NOT NULL,
            unsubscribed_at TEXT,
            UNIQUE (business_id, numero)
        )
    """)
    conn.execute("INSERT INTO meta_reminders (business_id, numero, token, sent_at) "
                 "VALUES (7, 3, 'token-publicado', '2026-08-01 10:00:00')")
    conn.commit()
    conn.close()

    init_db(ruta)

    conn = sqlite3.connect(ruta)
    fila = conn.execute(
        "SELECT estado, numero, token FROM meta_reminders WHERE business_id = 7"
    ).fetchone()
    conn.close()
    assert fila == ("sin_contactar", 3, "token-publicado")


def test_el_texto_del_mail_cambia_segun_el_estado():
    from services.email_service import _cuerpo_por_estado

    asunto_ppto, cuerpo_ppto = _cuerpo_por_estado(
        "presupuesto_enviado", 1, "Marejada", "crear_mi_ecommerce")
    asunto_llamar, cuerpo_llamar = _cuerpo_por_estado(
        "llamar_despues", 1, "Marejada", "crear_mi_ecommerce")

    assert "presupuesto" in " ".join(cuerpo_ppto).lower()
    assert "llamamos" in " ".join(cuerpo_llamar).lower()
    assert asunto_ppto != asunto_llamar
    # Un estado sin secuencia cae al texto del lead frio en vez de reventar.
    assert _cuerpo_por_estado("no_interesa", 1, "Marejada", "") is None
