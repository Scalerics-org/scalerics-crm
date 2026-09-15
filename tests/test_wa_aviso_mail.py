"""Aviso por mail a contacto@ cuando alguien escribe al WhatsApp.

Pedido de Juan (14/9). Lo que se fija aca:

- el primer mensaje de un numero avisa;
- los que siguen enseguida no (una charla son diez mensajes: diez mails
  inundan la casilla);
- despues de 30 minutos sin mensajes de ese numero, vuelve a avisar;
- la ventana vive en la base, asi que un deploy no la borra;
- si el mail o la base fallan, el bot igual recibe su 200.

Ningun test manda correo: `_send_estado` se reemplaza por una lista.
"""

from datetime import datetime, timedelta, timezone

import pytest

import dashboard
import routes.wa as wa
import services.email_service as es
from database import init_db
from services import wa_aviso_mail

TOKEN = "token-de-test"
AUTH = {"x-admin-token": TOKEN}
# 15:00 UTC son las 12:00 en Montevideo.
T0 = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def reloj(monkeypatch):
    estado = {"ahora": T0}
    monkeypatch.setattr(wa, "_ahora", lambda: estado["ahora"])
    return estado


@pytest.fixture
def enviados(monkeypatch):
    """Corre el aviso en el mismo hilo y guarda cada mail en vez de mandarlo."""
    lista = []
    monkeypatch.setattr(wa, "_en_segundo_plano", lambda funcion, *args: funcion(*args))

    def falso(to, subject, html, from_email=None, headers=None, text=None, attachments=None):
        lista.append({"to": to, "subject": subject, "html": html, "text": text})
        return "ok"

    monkeypatch.setattr(es, "_send_estado", falso)
    return lista


@pytest.fixture
def cliente(db, monkeypatch, reloj, enviados):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    monkeypatch.delenv("WA_AVISO_MAIL", raising=False)
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    return app.test_client()


def escribir(cliente, phone="+598 99 123 456", text="Hola, quiero una página web",
             name="Ana Pérez", **extra):
    cuerpo = {"phone": phone, "name": name, "text": text, "direction": "in", **extra}
    return cliente.post("/api/bot/mensaje-entrante", headers=AUTH, json=cuerpo)


# ── cuándo avisa ─────────────────────────────────────────────────────────────


def test_el_primer_mensaje_avisa(cliente, enviados):
    r = escribir(cliente)

    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "aviso": True}
    assert len(enviados) == 1
    mail = enviados[0]
    assert mail["to"] == "contacto@scalerics.com"
    assert "Ana Pérez" in mail["subject"]
    for fragmento in ("Ana Pérez", "+598 99 123 456", "Hola, quiero una página web",
                      "14/09/2026 12:00"):
        assert fragmento in mail["html"], fragmento
    # El link abre esa conversación en el CRM.
    assert "/?panel=wa&amp;chat=59899123456" in mail["html"]
    assert "/?panel=wa&chat=59899123456" in mail["text"]


def test_el_segundo_mensaje_enseguida_no_avisa(cliente, enviados, reloj):
    escribir(cliente)
    reloj["ahora"] = T0 + timedelta(minutes=1)
    r = escribir(cliente, text="¿Cuánto sale?")

    assert r.status_code == 200
    assert r.get_json()["aviso"] is False
    assert len(enviados) == 1


def test_despues_de_30_minutos_sin_mensajes_vuelve_a_avisar(cliente, enviados, reloj):
    escribir(cliente)
    reloj["ahora"] = T0 + timedelta(minutes=30)
    r = escribir(cliente, text="Sigo acá")

    assert r.get_json()["aviso"] is True
    assert len(enviados) == 2
    assert "Sigo acá" in enviados[1]["html"]


def test_la_ventana_se_cuenta_desde_el_ultimo_mensaje_no_desde_el_aviso(cliente, enviados, reloj):
    """Una charla larga avisa una sola vez: cada mensaje corre la ventana."""
    for minuto in (0, 20, 40, 60):
        reloj["ahora"] = T0 + timedelta(minutes=minuto)
        escribir(cliente, text=f"mensaje {minuto}")
    assert len(enviados) == 1

    reloj["ahora"] = T0 + timedelta(minutes=95)
    escribir(cliente, text="volví")
    assert len(enviados) == 2


def test_cada_numero_tiene_su_propia_ventana(cliente, enviados, reloj):
    escribir(cliente, phone="59899123456")
    reloj["ahora"] = T0 + timedelta(minutes=2)
    escribir(cliente, phone="59898765432", name="Bruno")

    assert len(enviados) == 2


def test_el_mismo_numero_escrito_distinto_es_el_mismo_contacto(cliente, enviados, reloj):
    escribir(cliente, phone="+598 99 123 456")
    reloj["ahora"] = T0 + timedelta(minutes=3)
    escribir(cliente, phone="59899123456")

    assert len(enviados) == 1


def test_la_ventana_sobrevive_un_reinicio(db, cliente, enviados, reloj):
    """Guardado en la base, no en memoria: cada deploy reinicia la máquina."""
    escribir(cliente)

    otra_app = dashboard.create_app(db)
    otra_app.config["TESTING"] = True
    reloj["ahora"] = T0 + timedelta(minutes=5)
    escribir(otra_app.test_client())

    assert len(enviados) == 1


def test_los_mensajes_que_salen_no_avisan_ni_corren_la_ventana(cliente, enviados, reloj):
    r = escribir(cliente, direction="out")
    assert r.get_json() == {"ok": True, "aviso": False}
    assert enviados == []

    reloj["ahora"] = T0 + timedelta(minutes=1)
    assert escribir(cliente).get_json()["aviso"] is True


def test_el_destinatario_se_configura(cliente, enviados, monkeypatch):
    monkeypatch.setenv("WA_AVISO_MAIL", "ventas@scalerics.com")
    escribir(cliente)
    assert enviados[0]["to"] == "ventas@scalerics.com"


# ── que nada rompa el webhook ────────────────────────────────────────────────


def test_si_el_mail_falla_el_bot_igual_recibe_200(cliente, monkeypatch, reloj):
    def revienta(*a, **k):
        raise RuntimeError("Resend caído")

    monkeypatch.setattr(es, "_send_estado", revienta)
    r = escribir(cliente)

    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_si_resend_contesta_que_no_el_bot_igual_recibe_200(cliente, monkeypatch):
    monkeypatch.setattr(es, "_send_estado", lambda *a, **k: "fallo")
    r = escribir(cliente)
    assert r.status_code == 200


def test_si_el_mail_falla_en_el_hilo_de_verdad_tampoco_rompe(db, monkeypatch, reloj):
    """Sin reemplazar `_en_segundo_plano`: el envío corre en su hilo."""
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    llamado = []

    def revienta(*a, **k):
        llamado.append(True)
        raise RuntimeError("Resend caído")

    monkeypatch.setattr(es, "_send_estado", revienta)
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    r = escribir(app.test_client())

    assert r.status_code == 200


def test_si_la_base_falla_el_bot_igual_recibe_200_y_no_sale_mail(cliente, enviados, monkeypatch):
    def revienta(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(wa_aviso_mail, "registrar_mensaje", revienta)
    r = escribir(cliente)

    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "aviso": False}
    assert enviados == []


# ── entrada ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("headers", [{}, {"x-admin-token": "otro"}])
def test_sin_la_credencial_del_bot_no_entra(cliente, enviados, headers):
    r = cliente.post("/api/bot/mensaje-entrante", headers=headers,
                     json={"phone": "59899123456", "text": "hola"})
    assert r.status_code in (401, 302)
    assert enviados == []


def test_sin_telefono_es_400(cliente, enviados):
    r = cliente.post("/api/bot/mensaje-entrante", headers=AUTH, json={"text": "hola"})
    assert r.status_code == 400
    assert enviados == []


# ── el mail ──────────────────────────────────────────────────────────────────


def test_el_texto_largo_va_recortado(cliente, enviados):
    escribir(cliente, text="a" * 2000)
    html = enviados[0]["html"]
    assert "a" * 500 in html
    assert "a" * 501 not in html
    assert "…" in html


def test_lo_que_escribe_el_contacto_va_escapado(cliente, enviados):
    escribir(cliente, name='<a href="https://phishing">Ana</a>', text="<script>alert(1)</script>")
    html = enviados[0]["html"]
    assert "<script>" not in html
    assert '<a href="https://phishing">' not in html
    assert "&lt;script&gt;" in html
    assert "\n" not in enviados[0]["subject"]


def test_un_mensaje_sin_texto_lo_dice(cliente, enviados):
    escribir(cliente, text="")
    assert "sin texto" in enviados[0]["html"]


def test_sin_nombre_el_mail_usa_el_telefono(cliente, enviados):
    escribir(cliente, name="")
    assert "+598 99 123 456" in enviados[0]["subject"]


def test_la_hora_es_la_de_montevideo():
    assert wa_aviso_mail.hora_montevideo(T0) == "14/09/2026 12:00"
    # Pasada la medianoche UTC todavía es el día anterior en Montevideo.
    assert wa_aviso_mail.hora_montevideo(datetime(2026, 9, 15, 1, 30, tzinfo=timezone.utc)) == "14/09/2026 22:30"


def test_registrar_mensaje_con_fechas_sin_zona_no_rompe(db):
    assert wa_aviso_mail.registrar_mensaje(db, "59899123456", datetime(2026, 9, 14, 15, 0)) is True
    assert wa_aviso_mail.registrar_mensaje(db, "59899123456", datetime(2026, 9, 14, 15, 10)) is False
    assert wa_aviso_mail.registrar_mensaje(db, "", T0) is False
