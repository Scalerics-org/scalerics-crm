import json
import logging
import time
from unittest.mock import MagicMock, patch

import pytest
import requests as _requests

from dashboard import create_app


@pytest.fixture
def app(tmp_path):
    # create_app(db_path) requiere la ruta de la base — no tiene default.
    application = create_app(str(tmp_path / "test.db"))
    application.config["TESTING"] = True
    return application


def test_fetch_failure_sends_alert(app, monkeypatch):
    """Si Graph falla, tiene que avisar por mail con el leadgen_id, no comerse el error."""
    from routes import meta

    with patch.object(meta.requests, "get", side_effect=RuntimeError("graph caido")), \
         patch.object(meta, "_get_admin_emails", return_value=["juan@scalerics.com"]), \
         patch("services.email_service.send_meta_lead_failure_alert") as alert:
        monkeypatch.setattr(meta, "PAGE_TOKEN", "token-de-prueba")
        meta._fetch_and_store_lead(app, "LEAD-123", "FORM-456")

    assert alert.called, "un fallo de Graph tiene que disparar el mail de alerta"
    args = alert.call_args[0]
    assert "LEAD-123" in args, "la alerta tiene que traer el leadgen_id para recuperarlo a mano"


def test_fetch_failure_does_not_leak_token_in_alert(app, monkeypatch):
    """El HTTPError de Graph trae el access_token en la URL; no puede viajar por mail."""
    from routes import meta

    secret = "SECRET_TOKEN_ABC"
    fake_response = MagicMock()
    fake_response.raise_for_status.side_effect = _requests.exceptions.HTTPError(
        f"400 Client Error: Bad Request for url: "
        f"https://graph.facebook.com/v20.0/LEAD-123?access_token={secret}&fields=field_data"
    )

    with patch.object(meta.requests, "get", return_value=fake_response), \
         patch.object(meta, "_get_admin_emails", return_value=["juan@scalerics.com"]), \
         patch("services.email_service.send_meta_lead_failure_alert") as alert:
        monkeypatch.setattr(meta, "PAGE_TOKEN", secret)
        meta._fetch_and_store_lead(app, "LEAD-123", "FORM-456")

    assert alert.called, "el fallo tiene que disparar el mail de alerta igual"
    error_arg = alert.call_args[0][2]
    assert secret not in error_arg, "el access_token no puede viajar en el mail de alerta"


def test_fetch_failure_without_admins_logs_explicitly(app, monkeypatch, caplog):
    """Si no hay ningun admin configurado, tiene que quedar un log explicito y distinguible."""
    from routes import meta

    with patch.object(meta.requests, "get", side_effect=RuntimeError("graph caido")), \
         patch.object(meta, "_get_admin_emails", return_value=[]), \
         patch("services.email_service.send_meta_lead_failure_alert") as alert:
        monkeypatch.setattr(meta, "PAGE_TOKEN", "token-de-prueba")
        with caplog.at_level(logging.ERROR):
            meta._fetch_and_store_lead(app, "LEAD-789", "FORM-456")

    assert not alert.called, "sin admins no hay a quien mandarle el mail"
    assert any(
        "LEAD-789" in record.message and "admin" in record.message.lower()
        for record in caplog.records
    ), "tiene que quedar un log explicito de que nadie fue avisado, distinguible del caso normal"


def _respuesta_de_graph(nombre: str, telefono: str, email: str = "") -> MagicMock:
    """Una respuesta de Graph con un lead adentro, como la devuelve la API."""
    field_data = [
        {"name": "full_name", "values": [nombre]},
        {"name": "phone_number", "values": [telefono]},
        {"name": "ciudad", "values": ["Montevideo"]},
    ]
    if email:
        field_data.append({"name": "email", "values": [email]})
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {
        "field_data": field_data,
        "created_time": "2026-08-12T10:00:00+0000",
        "ad_name": "Anuncio 1",
        "campaign_name": "Campaña Agosto",
    }
    return r


def test_telefono_repetido_no_descarta_el_lead(app, monkeypatch):
    """Un negocio ya scrapeado que llena el formulario de Meta no se puede perder:
    el INSERT choca con el UNIQUE de phone y antes quedaba solo un logger.info."""
    from database import get_business, init_db, insert_business
    from routes import meta

    db = app.config["DB_PATH"]
    init_db(db)
    telefono = "+598 99 123 456"
    existente_id = insert_business(db, {
        "name": "Barbería del Centro", "phone": telefono,
        "category": "Barbería", "source": "google",
    })
    assert existente_id, "el negocio previo tiene que haberse creado"

    with patch.object(meta.requests, "get", return_value=_respuesta_de_graph("Juan Lead", telefono)), \
         patch.object(meta, "_notify_new_meta_lead") as notificar:
        monkeypatch.setattr(meta, "PAGE_TOKEN", "token-de-prueba")
        meta._fetch_and_store_lead(app, "LEAD-DUP", "FORM-1")
        # el aviso sale en un thread aparte: esperarlo, sin sleep fijo
        limite = time.time() + 5
        while not notificar.called and time.time() < limite:
            time.sleep(0.02)

    fila = get_business(db, existente_id)
    assert fila["source"] == "meta", "al negocio existente hay que devolverle source='meta'"
    assert json.loads(fila["form_data"])["full_name"] == "Juan Lead", \
        "el form_data del lead tiene que quedar guardado en el negocio existente"

    import sqlite3
    conn = sqlite3.connect(db)
    total = conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0]
    conn.close()
    assert total == 1, "no se duplica el negocio"

    assert notificar.called, "el equipo se tiene que enterar igual del lead"
    assert notificar.call_args[0][5] == existente_id, \
        "la notificación tiene que apuntar al id del negocio que ya existía"


def test_telefono_repetido_sube_el_mail_si_no_tenia(app, monkeypatch):
    """El camino de fusión (_merge_lead_into_existing) tenía el mismo bug que
    el webhook: extraía el mail del formulario y nunca lo guardaba, porque
    update_business no recibía la clave. Un negocio sin mail que llena el
    formulario de Meta tiene que terminar con el mail del formulario."""
    from database import get_business, init_db, insert_business
    from routes import meta

    db = app.config["DB_PATH"]
    init_db(db)
    telefono = "+598 99 222 333"
    existente_id = insert_business(db, {
        "name": "Ferretería Sur", "phone": telefono,
        "category": "Ferretería", "source": "google",
    })
    assert existente_id, "el negocio previo tiene que haberse creado"

    respuesta = _respuesta_de_graph("Lead Con Mail", telefono, email="lead@ejemplo.com")
    with patch.object(meta.requests, "get", return_value=respuesta), \
         patch.object(meta, "_notify_new_meta_lead") as notificar:
        monkeypatch.setattr(meta, "PAGE_TOKEN", "token-de-prueba")
        meta._fetch_and_store_lead(app, "LEAD-MAIL-1", "FORM-1")
        limite = time.time() + 5
        while not notificar.called and time.time() < limite:
            time.sleep(0.02)

    fila = get_business(db, existente_id)
    assert fila["email"] == "lead@ejemplo.com", \
        "el mail del formulario tiene que quedar en el negocio existente"


def test_telefono_repetido_no_pisa_el_mail_que_ya_tenia(app, monkeypatch):
    """El criterio del merge es el mismo que el de scripts/backfill_meta_emails.py:
    el mail del formulario solo completa, nunca corrige uno que ya estaba."""
    from database import get_business, init_db, insert_business
    from routes import meta

    db = app.config["DB_PATH"]
    init_db(db)
    telefono = "+598 99 444 555"
    existente_id = insert_business(db, {
        "name": "Panadería Norte", "phone": telefono, "email": "viejo@ejemplo.com",
        "category": "Panadería", "source": "google",
    })
    assert existente_id, "el negocio previo tiene que haberse creado"

    respuesta = _respuesta_de_graph("Lead Con Otro Mail", telefono, email="nuevo@ejemplo.com")
    with patch.object(meta.requests, "get", return_value=respuesta), \
         patch.object(meta, "_notify_new_meta_lead") as notificar:
        monkeypatch.setattr(meta, "PAGE_TOKEN", "token-de-prueba")
        meta._fetch_and_store_lead(app, "LEAD-MAIL-2", "FORM-1")
        limite = time.time() + 5
        while not notificar.called and time.time() < limite:
            time.sleep(0.02)

    fila = get_business(db, existente_id)
    assert fila["email"] == "viejo@ejemplo.com", \
        "un mail que ya estaba cargado no se pisa con el del formulario"


def test_telefono_repetido_deja_warning(app, monkeypatch, caplog):
    """El caso pasó de logger.info a warning: es una colisión real, no ruido."""
    from database import init_db, insert_business
    from routes import meta

    db = app.config["DB_PATH"]
    init_db(db)
    telefono = "+598 99 777 888"
    insert_business(db, {"name": "Kiosco", "phone": telefono, "source": "google"})

    with patch.object(meta.requests, "get", return_value=_respuesta_de_graph("Ana Lead", telefono)), \
         patch.object(meta, "_notify_new_meta_lead"):
        monkeypatch.setattr(meta, "PAGE_TOKEN", "token-de-prueba")
        with caplog.at_level(logging.WARNING, logger="routes.meta"):
            meta._fetch_and_store_lead(app, "LEAD-DUP-2", "FORM-1")

    assert any(
        record.levelno >= logging.WARNING and telefono in record.message
        for record in caplog.records
    ), "la colisión de teléfono tiene que quedar como warning, no como info"


def test_error_no_filtra_token_en_el_log(app, monkeypatch, caplog):
    """flyctl logs muestra los valores: el access_token tampoco puede ir al log."""
    from routes import meta

    secret = "SECRET_TOKEN_EN_EL_LOG"
    fake_response = MagicMock()
    fake_response.raise_for_status.side_effect = _requests.exceptions.HTTPError(
        f"400 Client Error for url: https://graph.facebook.com/v26.0/LEAD-1?access_token={secret}"
    )

    with patch.object(meta.requests, "get", return_value=fake_response), \
         patch.object(meta, "_get_admin_emails", return_value=[]):
        monkeypatch.setattr(meta, "PAGE_TOKEN", secret)
        with caplog.at_level(logging.ERROR, logger="routes.meta"):
            meta._fetch_and_store_lead(app, "LEAD-1", "FORM-1")

    todo = "\n".join(record.getMessage() for record in caplog.records)
    assert secret not in todo, "el access_token no puede quedar escrito en los logs"
    assert "access_token=***" in todo, "el token se reemplaza, el resto del error se conserva"


def test_page_token_sale_del_entorno_no_de_la_constante(app, monkeypatch):
    """En Fly el token se renueva con `flyctl secrets set`, que reinicia el
    proceso con otro entorno. Leer la constante de módulo dejaba el webhook
    usando el token muerto hasta un redeploy."""
    from routes import meta

    monkeypatch.setattr(meta, "PAGE_TOKEN", "token-viejo-muerto")
    monkeypatch.setenv("META_PAGE_TOKEN", "token-nuevo-vivo")

    with patch.object(meta.requests, "get", return_value=_respuesta_de_graph("Lead X", "+598 99 000 111")) as get:
        with patch.object(meta, "_notify_new_meta_lead"):
            meta._fetch_and_store_lead(app, "LEAD-ENV", "FORM-1")

    assert get.called
    assert get.call_args[1]["params"]["access_token"] == "token-nuevo-vivo"


def test_reset_import_ya_no_existe(app):
    """El endpoint borraba `businesses WHERE category='Meta Lead Ad'` y reimportaba
    desde Graph, que solo retiene ~90 días: se llevaba puestos los leads viejos."""
    rutas = {regla.rule for regla in app.url_map.iter_rules()}
    assert "/api/meta/reset-import" not in rutas


def test_import_no_comparte_intervalo_con_el_monitor():
    """El 'import diario' corría cada 10 minutos por compartir la constante."""
    from routes import meta

    assert meta._IMPORT_INTERVAL == 24 * 60 * 60, "el import diario tiene que ser de 24h de verdad"
    assert meta._CHECK_INTERVAL != meta._IMPORT_INTERVAL, "son dos ritmos distintos"


# ── Mails que reciben datos de desconocidos ──────────────────────────────────

def test_el_mail_de_lead_escapa_lo_que_vino_del_formulario():
    """Nombre, teléfono, ciudad y campaña los llena cualquiera en internet:
    sin escapar, un <a href> en el campo nombre le inyecta un link al admin."""
    from services import email_service

    veneno = '<a href="https://phish.example/">Ver ficha</a>'
    with patch.object(email_service, "_send", return_value=True) as enviar:
        email_service.send_new_meta_lead_notification(
            "admin@scalerics.com", veneno, "<b>099</b>", "camp<script>", "ciudad<img>", 42
        )

    cuerpo = enviar.call_args[0][2]
    assert "<a href=\"https://phish.example/\">" not in cuerpo, "no puede quedar un link inyectado"
    assert "&lt;a href=" in cuerpo, "el nombre tiene que ir escapado"
    assert "<script" not in cuerpo
    assert "<b>099</b>" not in cuerpo and "<img>" not in cuerpo


def test_el_mail_de_fallo_escapa_el_lead_id_y_el_error():
    from services import email_service

    with patch.object(email_service, "_send", return_value=True) as enviar:
        email_service.send_meta_lead_failure_alert(
            "admin@scalerics.com", "<script>1</script>", "<img src=x onerror=1>"
        )

    cuerpo = enviar.call_args[0][2]
    assert "<script>" not in cuerpo and "<img" not in cuerpo
    assert "&lt;script&gt;" in cuerpo


def test_la_alerta_de_token_explica_como_se_renueva_en_produccion():
    """El mail mandaba a POST /api/meta/setup-token, que escribe un .env que no
    existe en el contenedor y no cambia el token que usa el webhook."""
    from services import email_service

    with patch.object(email_service, "_send", return_value=True) as enviar:
        email_service.send_meta_token_alert("admin@scalerics.com", "[190] token expirado")

    cuerpo = enviar.call_args[0][2]
    assert "flyctl secrets set META_PAGE_TOKEN" in cuerpo
    assert "/api/meta/setup-token" not in cuerpo, "no publicitar un endpoint que no arregla nada"


def test_signature_fails_closed_without_secret(monkeypatch):
    """Sin APP_SECRET no se puede aceptar cualquier payload: eso es un webhook abierto."""
    from routes import meta

    monkeypatch.setattr(meta, "APP_SECRET", "")
    monkeypatch.setattr(meta, "ALLOW_UNSIGNED", False)
    assert meta._verify_signature(b'{"object":"page"}', "") is False


def test_signature_allows_unsigned_only_when_explicit(monkeypatch):
    """En dev se puede saltear, pero tiene que ser una decisión explícita."""
    from routes import meta

    monkeypatch.setattr(meta, "APP_SECRET", "")
    monkeypatch.setattr(meta, "ALLOW_UNSIGNED", True)
    assert meta._verify_signature(b'{"object":"page"}', "") is True


def test_graph_version_is_single_constant():
    """La versión de Graph vive en meta_config.py, sin arrastrar Flask a los scripts sueltos."""
    import os

    import meta_config
    from routes import meta

    assert meta_config.GRAPH_VERSION == "v26.0"
    assert meta.GRAPH_VERSION == "v26.0"  # routes/meta.py re-exporta desde meta_config, mismo valor

    repo_root = os.path.dirname(os.path.abspath(meta_config.__file__))
    archivos = [
        meta_config.__file__,
        meta.__file__,
        os.path.join(repo_root, "import_meta_leads.py"),
        os.path.join(repo_root, "setup_meta.py"),
    ]
    for path in archivos:
        fuente = open(path, encoding="utf-8-sig").read()
        assert "graph.facebook.com/v" not in fuente, \
            f"no hardcodear la versión en las URLs, usar GRAPH de meta_config ({path})"


# ── Dedup de la alerta de token vencido ──────────────────────────────────────

def _respuesta_token_vencido(subcode: int = 463) -> MagicMock:
    r = MagicMock()
    r.json.return_value = {
        "error": {"code": 190, "error_subcode": subcode, "message": "Session has expired"}
    }
    return r


def test_token_alert_no_se_repite_dentro_del_intervalo(app, monkeypatch):
    """Dos chequeos seguidos del mismo token vencido no pueden mandar dos
    mails: con la máquina despierta 24/7 eso son ~288 mails por día por
    admin, y de paso revientan la cuota de Resend que usa todo el CRM."""
    from database import init_db
    from routes import meta

    db = app.config["DB_PATH"]
    init_db(db)
    monkeypatch.setattr(meta, "PAGE_TOKEN", "token-de-prueba")

    with patch.object(meta.requests, "get", return_value=_respuesta_token_vencido()), \
         patch.object(meta, "_get_admin_emails", return_value=["admin@scalerics.com"]), \
         patch.object(meta, "send_meta_token_alert") as alert:
        meta._check_token_once(db)
        meta._check_token_once(db)

    assert alert.call_count == 1, "el segundo chequeo del mismo problema no puede volver a mandar el mail"


def test_token_alert_distingue_incidente_nuevo(app, monkeypatch):
    """Un código de error distinto (revocado vs. vencido) es un incidente
    nuevo: no lo puede tapar el silencio del incidente anterior."""
    from database import init_db
    from routes import meta

    db = app.config["DB_PATH"]
    init_db(db)
    monkeypatch.setattr(meta, "PAGE_TOKEN", "token-de-prueba")

    with patch.object(meta, "_get_admin_emails", return_value=["admin@scalerics.com"]), \
         patch.object(meta, "send_meta_token_alert") as alert:
        with patch.object(meta.requests, "get", return_value=_respuesta_token_vencido(subcode=463)):
            meta._check_token_once(db)
        with patch.object(meta.requests, "get", return_value=_respuesta_token_vencido(subcode=460)):
            meta._check_token_once(db)

    assert alert.call_count == 2, "un tipo de falla distinto tiene que avisar aunque el anterior siga silenciado"


def test_token_alert_se_repite_pasado_el_intervalo(app, monkeypatch):
    """Un token vencido es un incidente real que no se puede silenciar para
    siempre: pasado el intervalo de re-alerta tiene que volver a avisar."""
    from database import init_db
    from routes import meta

    db = app.config["DB_PATH"]
    init_db(db)
    monkeypatch.setattr(meta, "PAGE_TOKEN", "token-de-prueba")

    with patch.object(meta.requests, "get", return_value=_respuesta_token_vencido()), \
         patch.object(meta, "_get_admin_emails", return_value=["admin@scalerics.com"]), \
         patch.object(meta, "send_meta_token_alert") as alert, \
         patch.object(meta.time, "time") as fake_time:
        fake_time.return_value = 1_000_000
        meta._check_token_once(db)
        fake_time.return_value = 1_000_000 + 10**7  # muy por encima de cualquier intervalo razonable
        meta._check_token_once(db)

    assert alert.call_count == 2, "pasado el intervalo de re-alerta tiene que volver a mandar el mail"


def test_override_reemplaza_los_destinatarios(tmp_path, monkeypatch):
    """META_NOTIFY_OVERRIDE manda el aviso solo a esa direccion, sin sumar a los admins."""
    import sqlite3

    from routes import meta

    db = str(tmp_path / "override.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE roles (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, role_id INTEGER)")
    conn.execute("INSERT INTO roles (id, name) VALUES (1, 'Admin')")
    conn.execute("INSERT INTO users (id, email, role_id) VALUES (1, 'jefe@scalerics.com', 1)")
    conn.commit()
    conn.close()

    monkeypatch.setenv("ADMIN_EMAIL", "otro-admin@scalerics.com")

    monkeypatch.delenv("META_NOTIFY_OVERRIDE", raising=False)
    sin_override = set(meta._get_admin_emails(db))
    assert "jefe@scalerics.com" in sin_override
    assert "otro-admin@scalerics.com" in sin_override

    monkeypatch.setenv("META_NOTIFY_OVERRIDE", "yo@gmail.com")
    con_override = meta._get_admin_emails(db)
    assert con_override == ["yo@gmail.com"], "el override reemplaza, no suma"
