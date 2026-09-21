"""Respuesta automatica a comentarios de Instagram: una vez cada uno y sin red."""

import sqlite3
from datetime import datetime, timezone

import pytest

from database import init_db
from services import ig_comentarios as ic

AHORA = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


class ApiFalsa:
    def __init__(self, comentarios, conteos=None, falla=False):
        self._comentarios = comentarios
        self._conteos = conteos
        self.respuestas, self.falla = [], falla

    def cuenta(self):
        return "IG", "scalerics_"

    def medias(self, ig):
        return {"M1": ("anuncio", "UGC 1 - Junio 2026"), "M2": ("publicacion", "Publicación")}

    def conteos(self, ids):
        return self._conteos or {m: len(self._comentarios.get(m, [])) for m in ids}

    def comentarios(self, mid):
        return self._comentarios.get(mid, [])

    def responder(self, cid, texto):
        if self.falla:
            raise RuntimeError("code=10 sin permiso")
        self.respuestas.append((cid, texto))
        return f"r-{cid}"


def _c(cid, texto, usuario="cliente1", ts="2026-09-20T10:00:00+0000", respuestas=()):
    return {"id": cid, "text": texto, "username": usuario, "timestamp": ts,
            "replies": {"data": [{"username": u} for u in respuestas]}}


def _avisos():
    lista = []
    return lista, (lambda *a: lista.append(a))


def test_responde_con_el_whatsapp_y_avisa(db):
    api = ApiFalsa({"M1": [_c("c1", "Info?"), _c("c2", "Precio")]})
    avisos, avisar = _avisos()
    r = ic.revisar(db, AHORA, api, avisar)
    assert r == {"respondidos": 2, "omitidos": 0, "errores": 0}
    assert [cid for cid, _ in api.respuestas] == ["c1", "c2"]
    assert all("097 250 713" in t for _, t in api.respuestas)
    assert api.respuestas[0][1] != api.respuestas[1][1], "las frases se turnan"
    assert len(avisos) == 2 and avisos[0][0] == "cliente1" and avisos[0][4] == "anuncio"


def test_cada_comentario_se_responde_una_sola_vez(db):
    api = ApiFalsa({"M1": [_c("c1", "Info?")]})
    ic.revisar(db, AHORA, api, lambda *a: None)
    api2 = ApiFalsa({"M1": [_c("c1", "Info?")]}, conteos={"M1": 5, "M2": 0})
    ic.revisar(db, AHORA, api2, lambda *a: None)
    assert api2.respuestas == []


def test_no_responde_propios_ya_respondidos_ni_viejos(db):
    api = ApiFalsa({"M1": [
        _c("c1", "Gracias!", usuario="scalerics_"),
        _c("c2", "Demo", respuestas=("scalerics_",)),
        _c("c3", "Info", ts="2026-05-01T10:00:00+0000"),
        _c("c4", "Me interesa"),
    ]})
    avisos, avisar = _avisos()
    r = ic.revisar(db, AHORA, api, avisar)
    assert [cid for cid, _ in api.respuestas] == ["c4"]
    assert r == {"respondidos": 1, "omitidos": 3, "errores": 0}
    assert len(avisos) == 1
    lista = ic.listar(db)
    assert {c["comment_id"] for c in lista} == {"c2", "c3", "c4"}, "los propios no se listan"


def test_si_meta_falla_queda_en_error_y_avisa_para_responder_a_mano(db):
    api = ApiFalsa({"M1": [_c("c1", "Info?")]}, falla=True)
    avisos, avisar = _avisos()
    r = ic.revisar(db, AHORA, api, avisar)
    assert r["errores"] == 1 and avisos[0][3] == "error"
    [c] = ic.listar(db)
    assert c["estado"] == "error" and "sin permiso" in c["error"]


def test_solo_mira_las_que_tienen_comentarios_nuevos(db):
    llamadas = []

    class Api(ApiFalsa):
        def comentarios(self, mid):
            llamadas.append(mid)
            return super().comentarios(mid)

    api = Api({"M1": [_c("c1", "Info?")]})
    ic.revisar(db, AHORA, api, lambda *a: None)
    ic.revisar(db, AHORA, api, lambda *a: None)
    assert llamadas == ["M1"]


def test_tope_por_vuelta(db, monkeypatch):
    monkeypatch.setattr(ic, "TOPE_POR_VUELTA", 2)
    api = ApiFalsa({"M1": [_c(f"c{i}", "Info") for i in range(5)]})
    ic.revisar(db, AHORA, api, lambda *a: None)
    assert len(api.respuestas) == 2
    # la vuelta siguiente sigue con los que faltaban
    api2 = ApiFalsa({"M1": [_c(f"c{i}", "Info") for i in range(5)]})
    ic.revisar(db, AHORA, api2, lambda *a: None)
    assert [c for c, _ in api2.respuestas] == ["c2", "c3"]


def test_numero_configurable_y_apagado(db, monkeypatch):
    monkeypatch.setenv("IG_WHATSAPP", "099 000 000")
    assert "099 000 000" in ic.frase(0)
    monkeypatch.setenv("IG_RESPUESTAS", "off")
    assert ic.corrida(db, AHORA, ApiFalsa({})) is None


def test_corrida_una_vez_por_hora_y_sin_credenciales(db, monkeypatch):
    api = ApiFalsa({"M1": [_c("c1", "Info?")]})
    assert ic.corrida(db, AHORA, api, lambda *a: None)["respondidos"] == 1
    assert ic.corrida(db, AHORA, api, lambda *a: None) is None
    monkeypatch.delenv("META_ADS_TOKEN", raising=False)
    c = sqlite3.connect(db)
    c.execute("DELETE FROM corridas")
    c.commit()
    c.close()
    assert ic.corrida(db, AHORA) is None


def test_el_mail_del_comentario_se_arma(monkeypatch):
    from services import email_service as es
    with es.capturar_envio() as capturados:
        es.send_instagram_comentario("cliente1", "<b>Info</b>", ic.frase(0), "respondido",
                                     "anuncio", "UGC 1")
    html = capturados[0]["html"]
    assert "@cliente1" in capturados[0]["subject"] and "&lt;b&gt;Info" in html and "097 250 713" in html


def test_los_conteos_se_piden_uno_por_uno_sin_el_parametro_ids():
    llamadas = []

    class Fake(ic.Api):
        def _get(self, ruta, **params):
            llamadas.append((ruta, params))
            if ruta == "MALO":
                raise RuntimeError("borrada")
            return {"comments_count": 2}

    assert Fake().conteos(["A", "MALO", "B"]) == {"A": 2, "B": 2}
    assert all("ids" not in p and r for r, p in llamadas)
