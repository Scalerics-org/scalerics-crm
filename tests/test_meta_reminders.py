import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from database import init_db
from services.meta_reminders import (
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
    token = registrar_envio(db, 42)
    assert token, "tiene que devolver un token"

    with pytest.raises(sqlite3.IntegrityError):
        registrar_envio(db, 42)


def test_la_baja_marca_al_lead(db):
    token = registrar_envio(db, 7)
    assert esta_dado_de_baja(db, 7) is False

    assert dar_de_baja(db, token) is True
    assert esta_dado_de_baja(db, 7) is True


def test_un_token_que_no_existe_no_rompe(db):
    assert dar_de_baja(db, "token-inventado") is False


def test_las_fechas_se_guardan_en_el_formato_que_compara(db):
    """sent_at y unsubscribed_at tienen que usar '%Y-%m-%d %H:%M:%S' UTC, igual
    que scraped_at: es el formato con el que datetime('now', ...) compara."""
    token = registrar_envio(db, 500)
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
    token = registrar_envio(ruta, 99)

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
    token = registrar_envio(ruta, 98)

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
    token = registrar_envio(ruta, 97)

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
    _lead(conn, 3, dias=5, crm_status="reunion_hecha")      # ya lo contactaron
    _lead(conn, 4, dias=5, email=None)                      # sin mail
    _lead(conn, 5, dias=5, source="google")                 # no es de Meta
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
    registrar_envio(db, 10)
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

    registrar_envio(db, 511)

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


def test_la_cuota_del_dia_es_lo_que_queda(db):
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


def test_el_mail_sale_sin_espacios_alrededor(db):
    """La dedup compara con TRIM, asi que un mail con espacios entra igual al
    grupo; si despues lo mandamos crudo, Resend lo rechaza, eso cuenta como
    fallo, se borra la fila y el lead se reintenta todos los dias para siempre."""
    conn = sqlite3.connect(db)
    _lead(conn, 91, dias=5, email="  Ana@Ejemplo.com  ")
    conn.commit()
    conn.close()

    assert [x["email"] for x in leads_a_recordar(db)] == ["Ana@Ejemplo.com"]
