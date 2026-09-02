"""El audio de una nota de voz, servido por el CRM.

El bot no tiene IP publica —vive solo en la red privada de Fly, a proposito—
asi que el navegador no puede pedirle el archivo. El camino es
navegador -> CRM -> bot -> archivo, y este endpoint es el del medio.

Va aparte de _bot_req porque eso devuelve JSON parseado y aca lo que viaja son
bytes: pasarlos por json() los rompe.
"""

from unittest.mock import MagicMock, patch

import pytest

import dashboard
from database import init_db


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-test")
    monkeypatch.setenv("BOT_API_URL", "http://bot.internal:8080")
    monkeypatch.setenv("BOT_ADMIN_TOKEN", "token-del-bot")
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    app = dashboard.create_app(ruta)
    app.config["TESTING"] = True
    return app.test_client()


_AUTH = {"x-admin-token": "token-de-test"}


def _respuesta(status=200, contenido=b"", tipo="audio/ogg"):
    r = MagicMock()
    r.status_code = status
    r.content = contenido
    r.headers = {"Content-Type": tipo}
    return r


def test_devuelve_el_audio_tal_cual(cliente):
    with patch("routes.wa.http_requests.request", return_value=_respuesta(contenido=b"ogg-crudo")) as req:
        r = cliente.get("/api/wa/media/42/0", headers=_AUTH)

    assert r.status_code == 200
    assert r.data == b"ogg-crudo"
    assert r.headers["Content-Type"] == "audio/ogg"

    # Le pide al bot el mensaje y el indice que vinieron en la URL, sin inventar.
    url = req.call_args[0][1]
    assert url.endswith("/api/messages/42/media/0")


def test_manda_la_credencial_del_bot(cliente):
    # Sin el header, el bot contesta 401 y en el panel el audio no suena, sin
    # ninguna pista de por que.
    with patch("routes.wa.http_requests.request", return_value=_respuesta(contenido=b"x")) as req:
        cliente.get("/api/wa/media/1/0", headers=_AUTH)

    assert req.call_args[1]["headers"]["x-admin-token"] == "token-del-bot"


def test_si_el_audio_ya_no_esta_devuelve_404(cliente):
    # Se borran a los 90 dias: pedir uno viejo tiene que dar 404, no 200 vacio,
    # o el navegador muestra un reproductor que no suena.
    with patch("routes.wa.http_requests.request", return_value=_respuesta(status=404, contenido=b"{}")):
        r = cliente.get("/api/wa/media/1/0", headers=_AUTH)

    assert r.status_code == 404


def test_un_id_que_no_es_numero_no_llega_al_bot(cliente):
    with patch("routes.wa.http_requests.request") as req:
        r = cliente.get("/api/wa/media/abc/0", headers=_AUTH)

    assert r.status_code == 404
    req.assert_not_called()
