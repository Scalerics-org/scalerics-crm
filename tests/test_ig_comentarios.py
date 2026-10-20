"""Comentarios de Instagram: quedan para Claude, una vez cada uno y sin red."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from database import init_db
from services import ig_comentarios as ic

AHORA = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)
DESPUES = AHORA + timedelta(hours=25)


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

    def texto_de(self, mid):
        return f"Texto de {mid}: tu web en 10 días"

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


def test_los_nuevos_quedan_esperando_a_claude_con_el_texto_del_anuncio(db):
    api = ApiFalsa({"M1": [_c("c1", "Info?"), _c("c2", "Precio")]})
    avisos, avisar = _avisos()
    r = ic.revisar(db, AHORA, api, avisar)
    assert r == {"nuevos": 2, "respondidos": 0, "omitidos": 0, "errores": 0}
    assert api.respuestas == [] and avisos == [], "el servidor no contesta ni avisa todavia"
    pend = ic.pendientes(db)
    assert [p["comment_id"] for p in pend] == ["c1", "c2"]
    assert pend[0]["contexto"].startswith("Texto de M1") and pend[0]["whatsapp"] == "097 250 713"
    assert pend[0]["origen"] == "anuncio" and pend[0]["texto"] == "Info?"


def test_claude_responde_y_sale_el_mail(db):
    ic.revisar(db, AHORA, ApiFalsa({"M1": [_c("c1", "¿Hacen tiendas online?")]}), lambda *a: None)
    api = ApiFalsa({})
    avisos, avisar = _avisos()
    hecho = ic.responder(db, "c1", "¡Sí! Armamos tiendas online. Escribinos al 097 250 713.", api, avisar)
    assert hecho["estado"] == "respondido"
    assert api.respuestas == [("c1", "¡Sí! Armamos tiendas online. Escribinos al 097 250 713.")]
    assert avisos[0][3] == "respondido" and "tiendas" in avisos[0][2]
    [c] = ic.listar(db)
    assert c["por"] == "claude" and c["reply_id"] == "r-c1"
    assert ic.pendientes(db) == []


def test_no_se_puede_responder_dos_veces_ni_vacio_ni_largo(db):
    ic.revisar(db, AHORA, ApiFalsa({"M1": [_c("c1", "Info?")]}), lambda *a: None)
    api = ApiFalsa({})
    with pytest.raises(ic.NoSePuede):
        ic.responder(db, "c1", "   ", api, lambda *a: None)
    with pytest.raises(ic.NoSePuede):
        ic.responder(db, "c1", "x" * (ic.MAX_LARGO + 1), api, lambda *a: None)
    ic.responder(db, "c1", "Hola", api, lambda *a: None)
    with pytest.raises(ic.NoSePuede):
        ic.responder(db, "c1", "Hola otra vez", api, lambda *a: None)
    with pytest.raises(ic.NoSePuede):
        ic.responder(db, "no-existe", "Hola", api, lambda *a: None)
    assert len(api.respuestas) == 1


def test_derivar_no_publica_y_avisa_con_el_motivo(db):
    ic.revisar(db, AHORA, ApiFalsa({"M1": [_c("c1", "Pésimo servicio")]}), lambda *a: None)
    avisos, avisar = _avisos()
    ic.derivar(db, "c1", "es una queja", avisar)
    assert avisos[0][3] == "derivado" and avisos[0][6] == "es una queja"
    [c] = ic.listar(db)
    assert c["estado"] == "derivado" and c["error"] == "es una queja"
    # y el respaldo no lo contesta despues
    api = ApiFalsa({})
    ic.revisar(db, DESPUES, api, lambda *a: None)
    assert api.respuestas == []


def test_si_meta_rechaza_la_respuesta_queda_para_contestar_a_mano(db):
    ic.revisar(db, AHORA, ApiFalsa({"M1": [_c("c1", "Info?")]}), lambda *a: None)
    avisos, avisar = _avisos()
    hecho = ic.responder(db, "c1", "Hola", ApiFalsa({}, falla=True), avisar)
    assert hecho["estado"] == "error" and avisos[0][3] == "error"
    [c] = ic.listar(db)
    assert c["estado"] == "error" and "sin permiso" in c["error"]


def test_respaldo_con_frase_si_nadie_contesto_en_24_horas(db):
    ic.revisar(db, AHORA, ApiFalsa({"M1": [_c("c1", "Info?"), _c("c2", "Precio")]}), lambda *a: None)
    api = ApiFalsa({})
    ic.revisar(db, AHORA + timedelta(hours=23), api, lambda *a: None)
    assert api.respuestas == [], "todavia esta a tiempo Claude"
    avisos, avisar = _avisos()
    r = ic.revisar(db, DESPUES, api, avisar)
    assert r["respondidos"] == 2 and len(avisos) == 2
    assert all("097 250 713" in t for _, t in api.respuestas)
    assert api.respuestas[0][1] != api.respuestas[1][1], "las frases se turnan"
    assert {c["por"] for c in ic.listar(db)} == {"frase"}


def test_respaldo_apagado_y_con_tope(db, monkeypatch):
    ic.revisar(db, AHORA, ApiFalsa({"M1": [_c(f"c{i}", "Info") for i in range(5)]}), lambda *a: None)
    monkeypatch.setenv("IG_RESPUESTA_ESPERA_HORAS", "0")
    api = ApiFalsa({})
    ic.revisar(db, DESPUES, api, lambda *a: None)
    assert api.respuestas == []
    monkeypatch.delenv("IG_RESPUESTA_ESPERA_HORAS")
    monkeypatch.setattr(ic, "TOPE_POR_VUELTA", 2)
    ic.revisar(db, DESPUES, api, lambda *a: None)
    assert [c for c, _ in api.respuestas] == ["c0", "c1"]


def test_cada_comentario_se_anota_una_sola_vez(db):
    ic.revisar(db, AHORA, ApiFalsa({"M1": [_c("c1", "Info?")]}), lambda *a: None)
    ic.revisar(db, AHORA, ApiFalsa({"M1": [_c("c1", "Info?")]}, conteos={"M1": 5, "M2": 0}),
               lambda *a: None)
    assert len(ic.pendientes(db)) == 1


def test_no_anota_propios_ya_respondidos_ni_viejos(db):
    api = ApiFalsa({"M1": [
        _c("c1", "Gracias!", usuario="scalerics_"),
        _c("c2", "Demo", respuestas=("scalerics_",)),
        _c("c3", "Info", ts="2026-05-01T10:00:00+0000"),
        _c("c4", "Me interesa"),
    ]})
    r = ic.revisar(db, AHORA, api, lambda *a: None)
    assert r == {"nuevos": 1, "respondidos": 0, "omitidos": 3, "errores": 0}
    assert [p["comment_id"] for p in ic.pendientes(db)] == ["c4"]
    assert {c["comment_id"] for c in ic.listar(db)} == {"c2", "c3", "c4"}, "los propios no se listan"


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


def test_la_tabla_vieja_gana_las_columnas_nuevas(tmp_path):
    ruta = str(tmp_path / "vieja.db")
    c = sqlite3.connect(ruta)
    c.execute("CREATE TABLE ig_comentarios (comment_id TEXT PRIMARY KEY, media_id TEXT NOT NULL, "
              "origen TEXT, origen_nombre TEXT, usuario TEXT, texto TEXT, creado_en TEXT, "
              "estado TEXT NOT NULL, respuesta TEXT, reply_id TEXT, error TEXT, "
              "procesado_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    c.execute("INSERT INTO ig_comentarios (comment_id, media_id, estado) VALUES ('v1', 'M', 'respondido')")
    c.commit()
    c.close()
    [fila] = ic.listar(ruta)
    assert "contexto" in fila and "por" in fila and fila["estado"] == "respondido"


def test_numero_configurable_y_apagado(db, monkeypatch):
    monkeypatch.setenv("IG_WHATSAPP", "099 000 000")
    assert "099 000 000" in ic.frase(0)
    monkeypatch.setenv("IG_RESPUESTAS", "off")
    assert ic.corrida(db, AHORA, ApiFalsa({})) is None


def test_corrida_una_vez_por_hora_y_sin_credenciales(db, monkeypatch):
    api = ApiFalsa({"M1": [_c("c1", "Info?")]})
    assert ic.corrida(db, AHORA, api, lambda *a: None)["nuevos"] == 1
    assert ic.corrida(db, AHORA, api, lambda *a: None) is None
    monkeypatch.delenv("META_ADS_TOKEN", raising=False)
    c = sqlite3.connect(db)
    c.execute("DELETE FROM corridas")
    c.commit()
    c.close()
    assert ic.corrida(db, AHORA) is None


def test_los_mails_del_comentario_se_arman(monkeypatch):
    from services import email_service as es
    with es.capturar_envio() as capturados:
        es.send_instagram_comentario("cliente1", "<b>Info</b>", ic.frase(0), "respondido",
                                     "anuncio", "UGC 1")
        es.send_instagram_comentario("cliente2", "Malísimo", None, "derivado",
                                     "publicacion", "Publicación", "es una <queja>")
    html = capturados[0]["html"]
    assert "@cliente1" in capturados[0]["subject"] and "&lt;b&gt;Info" in html and "097 250 713" in html
    assert "contestes vos" in capturados[1]["html"] and "es una &lt;queja&gt;" in capturados[1]["html"]


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


def test_rutas_del_bot_con_token(db, monkeypatch):
    from flask import Flask
    from routes.instagram import instagram_pub_bp

    monkeypatch.setenv("IG_BOT_TOKEN", "t" * 40)
    monkeypatch.setattr(ic, "Api", lambda: ApiFalsa({}))
    monkeypatch.setattr("services.email_service.send_instagram_comentario", lambda *a: True)
    ic.revisar(db, AHORA, ApiFalsa({"M1": [_c("c1", "Info?"), _c("c2", "Queja")]}), lambda *a: None)
    app = Flask(__name__)
    app.config["DB_PATH"] = db
    app.register_blueprint(instagram_pub_bp)
    cli = app.test_client()
    h = {"x-ig-token": "t" * 40}
    assert cli.get("/api/instagram-bot/comentarios/pendientes").status_code == 403
    pend = cli.get("/api/instagram-bot/comentarios/pendientes", headers=h).get_json()["pendientes"]
    assert [p["comment_id"] for p in pend] == ["c1", "c2"]
    r = cli.post("/api/instagram-bot/comentarios/c1/responder", headers=h, json={"respuesta": "Hola!"})
    assert r.status_code == 200 and r.get_json()["ok"]
    r = cli.post("/api/instagram-bot/comentarios/c1/responder", headers=h, json={"respuesta": "Hola!"})
    assert r.status_code == 400
    r = cli.post("/api/instagram-bot/comentarios/c2/derivar", headers=h, json={"motivo": "queja"})
    assert r.status_code == 200
    assert cli.get("/api/instagram-bot/comentarios/pendientes", headers=h).get_json()["pendientes"] == []
