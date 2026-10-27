"""Google solo: puntaje, reseñas y respuestas sugeridas (services/resenas_google.py)."""

import sqlite3
from datetime import timedelta

import pytest

from services import resenas as rs
from services import resenas_google as rg
from tests.test_resenas import AHORA, _login, _negocio, app, db, reloj  # noqa: F401

PID = "ChIJN1t_tDeuEmsRUsoyG83frY4"


def _datos(*resenas, rating=4.3, cantidad=120):
    return {"rating": rating, "userRatingCount": cantidad, "reviews": [
        {"name": f"places/{PID}/reviews/{gid}", "rating": estrellas, "originalText": {"text": texto},
         "authorAttribution": {"displayName": autor}, "publishTime": "2026-10-26T15:30:00Z",
         "googleMapsUri": f"https://www.google.com/maps/reviews/{gid}"}
        for gid, estrellas, autor, texto in resenas]}


class Google:
    """Hace de la Places API: cuenta las llamadas y devuelve lo que se le diga."""

    def __init__(self, datos=None, error=None):
        self.datos = datos or _datos()
        self.error = error
        self.pedidos = []

    def __call__(self, place_id):
        self.pedidos.append(place_id)
        if self.error:
            raise self.error
        return self.datos


def _con_pid(db, **kw):
    return _negocio(db, place_id=PID, **kw)


def test_place_id_del_link_o_cargado(db):
    n = _negocio(db, google_url=PID)  # el Place ID suelto se vuelve el link writereview
    assert rg.place_id_de(n) == PID
    n2 = rs.crear_negocio(db, {"nombre": "Otro", "google_url": "https://g.page/r/x/review"}, "t")
    assert rg.place_id_de(n2) == ""
    n2 = rs.editar_negocio(db, n2["id"], {"place_id": PID})
    assert rg.place_id_de(n2) == PID
    for malo in ("corto", "ChIJ<script>alert(1)</script>xxxxxx", "a" * 400):
        with pytest.raises(rs.NoSePuede):
            rs.editar_negocio(db, n2["id"], {"place_id": malo})


def test_lectura_guarda_foto_del_dia_y_resenas_nuevas_una_vez(db, reloj):
    n = _con_pid(db)
    g = Google(_datos(("a", 5, "Martín Pereira", "Excelente atención"), ("b", 2, "Sofía", "Tardaron una hora")))
    r = rg.leer(db, n["id"], "manual", pedir=g)
    assert r["nuevas"] == 2 and g.pedidos == [PID]
    fotos = rs.negocio(db, n["id"])["google"]
    assert [(f["rating"], f["cantidad"], f["cargado_por"]) for f in fotos] == [(4.3, 120, "Google")]
    # Otra lectura el mismo día pisa la foto y no duplica reseñas.
    g.datos = _datos(("a", 5, "Martín Pereira", "Excelente atención"), ("c", 4, "Lu", "Rico"), rating=4.4, cantidad=122)
    reloj["ahora"] += timedelta(hours=1)
    assert rg.leer(db, n["id"], "manual", pedir=g)["nuevas"] == 1
    fotos = rs.negocio(db, n["id"])["google"]
    assert [(f["rating"], f["cantidad"]) for f in fotos] == [(4.4, 122)]
    lista = rg.resenas(db, n["id"], "todas")
    assert len(lista) == 3
    assert lista[0]["rating"] == 2  # la mala primero
    assert lista[0]["publicada_en"] == "2026-10-26 12:30"  # UTC a Montevideo
    assert all(x["sugerida"] and x["sugerida_por"] == "plantilla" for x in lista)
    # La métrica de Google del panel sale de la foto automática.
    assert rs.metricas(db, n["id"])["google_evolucion"]["ultimo"]["rating"] == 4.4


def test_topes_de_lectura(db, reloj):
    n = _con_pid(db)
    g = Google()
    for _ in range(rg.MAX_MANUALES_DIA):
        rg.leer(db, n["id"], "manual", pedir=g)
    with pytest.raises(rs.NoSePuede, match="hoy"):
        rg.leer(db, n["id"], "manual", pedir=g)
    assert len(g.pedidos) == rg.MAX_MANUALES_DIA
    # La automática no lee si ya se leyó en las últimas horas.
    assert rg.revisar(db, pedir=g, ahora=reloj["ahora"])["leidos"] == 0
    reloj["ahora"] += timedelta(hours=rg.HORAS_ENTRE_LECTURAS + 1)
    assert rg.revisar(db, pedir=g, ahora=reloj["ahora"])["leidos"] == 1
    assert rg.revisar(db, pedir=g, ahora=reloj["ahora"])["leidos"] == 0
    assert len(g.pedidos) == rg.MAX_MANUALES_DIA + 1


def test_un_fallo_cuenta_y_no_rompe_la_pasada(db, reloj):
    n = _con_pid(db)
    otro = rs.crear_negocio(db, {"nombre": "Otro", "place_id": "ChIJotroComercioDeEjemplo"}, "t")
    rs.crear_negocio(db, {"nombre": "Sin Google"}, "t")
    g = Google(error=RuntimeError("timeout"))
    r = rg.revisar(db, pedir=g, ahora=reloj["ahora"])
    assert r["fallos"] == 2 and r["leidos"] == 0 and len(g.pedidos) == 2
    ult = rg.resumen(db, n["id"])["ultima"]
    assert ult["estado"] == "fallo" and ult["detalle"] == "No se pudo hablar con Google."
    # El fallo cuenta: la próxima pasada no reintenta enseguida.
    assert rg.revisar(db, pedir=g, ahora=reloj["ahora"] + timedelta(hours=1))["fallos"] == 0
    assert len(g.pedidos) == 2 and otro


def test_tope_por_hora_global(db, reloj, monkeypatch):
    monkeypatch.setattr(rg, "MAX_POR_HORA", 2)
    for i in range(3):
        rs.crear_negocio(db, {"nombre": f"C{i}", "place_id": f"ChIJcomercioNumero{i}xxxx"}, "t")
    g = Google()
    r = rg.revisar(db, pedir=g, ahora=reloj["ahora"])
    assert r["leidos"] == 2 and len(g.pedidos) == 2


def test_sin_place_id_ni_clave(db, monkeypatch):
    n = rs.crear_negocio(db, {"nombre": "Sin Google"}, "t")
    with pytest.raises(rs.NoSePuede, match="Place ID"):
        rg.leer(db, n["id"], "manual", pedir=Google())
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    assert not rg.activo()
    con = _negocio(db, place_id=PID)
    with pytest.raises(rs.NoSePuede, match="GOOGLE_PLACES_API_KEY"):
        rg.leer(db, con["id"], "manual")
    assert rg.resumen(db, con["id"])["ultima"] is None  # no gastó cupo
    with pytest.raises(rs.NoSePuede, match="GOOGLE_PLACES_API_KEY"):
        rg.pedir_places(PID)


def test_datos_raros_de_google_se_descartan(db, reloj):
    n = _con_pid(db)
    datos = {"rating": "mucho", "userRatingCount": 10, "reviews": [
        {"name": "x1", "rating": 9}, {"rating": 4}, {"name": "x2", "rating": "dos"},
        {"name": "x3", "rating": 1, "text": {"text": "Mal"}, "googleMapsUri": "https://evil.com/google.com"},
        {"name": "x4", "rating": 5, "googleMapsUri": "https://maps.google.com/x"}]}
    assert rg.leer(db, n["id"], "manual", pedir=Google(datos))["nuevas"] == 2
    assert rs.negocio(db, n["id"])["google"] == []  # sin puntaje válido no hay foto
    por_id = {r["google_id"]: r for r in rg.resenas(db, n["id"], "todas")}
    assert por_id["x3"]["link"] is None and por_id["x3"]["texto"] == "Mal"
    assert por_id["x4"]["link"] == "https://maps.google.com/x"
    assert rg.link_responder(n, por_id["x3"]).endswith("placeid=" + PID)


def test_plantillas_por_tramo_y_tema():
    n = {"nombre": "Parrilla El Fogón", "whatsapp": "59899111222"}
    cinco = rg.sugerir_plantilla(n, {"google_id": "a", "rating": 5, "autor": "martín pereira",
                                     "texto": "Excelente atención de los mozos"})
    assert "Martín" in cinco and "atendido bien" in cinco and cinco.endswith("Equipo de Parrilla El Fogón")
    dos = rg.sugerir_plantilla(n, {"google_id": "b", "rating": 1, "autor": "Sofía", "texto": "Una hora de demora"})
    assert "099 111 222" in dos and "espera" in dos and "Sofía" in dos
    assert rg.temas("Esperamos una hora, el asado frío y el mozo ni saludó") == ["demora", "comida", "atencion"]
    tres = rg.sugerir_plantilla({"nombre": "X"}, {"google_id": "c", "rating": 3, "autor": None, "texto": None})
    assert "mejorar" in tres and "None" not in tres
    # Siempre la misma para la misma reseña, y ninguna rompe sus propias reglas.
    assert cinco == rg.sugerir_plantilla(n, {"google_id": "a", "rating": 5, "autor": "martín pereira",
                                             "texto": "Excelente atención de los mozos"})
    for t in (cinco, dos, tres):
        assert rg.validar_respuesta(t, n) or rg.validar_respuesta(t, {"nombre": "X"})


def test_validar_respuesta_de_la_ia():
    n = {"nombre": "Fogón", "whatsapp": "59899111222"}
    ok = "¡Gracias Martín! Nos alegra que te haya gustado. Te esperamos pronto.\nEquipo de Fogón"
    assert rg.validar_respuesta('"' + ok + '"', n) == ok
    assert rg.validar_respuesta("Perdón Sofía. Escribinos al 099 111 222 y lo vemos.\nEquipo de Fogón", n)
    for malo in ("Te damos un 20% de descuento en tu próxima visita, perdón.\nEquipo de Fogón",
                 "Llamanos al 2901 2345 y lo charlamos, perdón por todo.\nEquipo de Fogón",
                 "Mirá nuestra carta en https://fogon.uy y volvé cuando quieras.\nEquipo",
                 "Escribinos a fogon@gmail.com y lo solucionamos enseguida.\nEquipo",
                 "Gracias", "x" * 800, None):
        assert rg.validar_respuesta(malo, n) is None


def test_sugerir_usa_la_ia_solo_si_valida(db):
    n = {"nombre": "Fogón", "whatsapp": None}
    r = {"google_id": "a", "rating": 2, "autor": "Sofía", "texto": "Frío"}
    t, de = rg.sugerir(n, r, redactar=lambda n, r: "Perdón Sofía, lamentamos que haya llegado frío. "
                                                    "Escribinos por privado y lo vemos.\nEquipo de Fogón")
    assert de == "ia" and "frío" in t
    t, de = rg.sugerir(n, r, redactar=lambda n, r: "Te regalamos la cena, 50% off.")
    assert de == "plantilla" and "Fogón" in t

    def roto(n, r):
        raise RuntimeError("sin red")
    assert rg.sugerir(n, r, redactar=roto)[1] == "plantilla"


def test_ia_nace_apagada(monkeypatch):
    monkeypatch.delenv("RESENAS_IA", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert not rg.ia_activa()
    monkeypatch.setenv("RESENAS_IA", "on")
    assert rg.ia_activa()
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert not rg.ia_activa()


def test_marcar_y_resumen(db, reloj):
    n = _con_pid(db)
    rg.leer(db, n["id"], "manual", pedir=Google(_datos(("a", 5, "M", "Bien"), ("b", 2, "S", "Mal"))))
    ids = {r["google_id"].rsplit("/", 1)[-1]: r["id"] for r in rg.resenas(db, n["id"], "todas")}
    reloj["ahora"] += timedelta(hours=2)
    r = rg.marcar(db, n["id"], ids["b"], "respondida", "  Perdón, escribinos.  ", "Marcela · comercio")
    assert r["respuesta"] == "Perdón, escribinos." and r["respondida_por"] == "Marcela · comercio"
    r = rg.marcar(db, n["id"], ids["a"], "respondida", "", "Juan")
    assert r["respuesta"] == r["sugerida"]  # sin texto, queda la sugerida
    s = rg.resumen(db, n["id"])
    assert s["sin_responder"] == 0 and s["respondidas_pct"] == 100 and s["horas_respuesta"] is not None
    rg.marcar(db, n["id"], ids["a"], "sin_responder", None, "Juan")
    assert rg.resumen(db, n["id"])["sin_responder"] == 1
    otro = rs.crear_negocio(db, {"nombre": "Otro"}, "t")
    with pytest.raises(rs.NoSePuede, match="no existe"):
        rg.marcar(db, otro["id"], ids["a"], "respondida", "x", "t")
    with pytest.raises(rs.NoSePuede):
        rg.marcar(db, n["id"], ids["a"], "borrada", "x", "t")


def test_purga_el_texto_a_los_30_dias(db, reloj):
    n = _con_pid(db)
    rg.leer(db, n["id"], "manual", pedir=Google(_datos(("a", 5, "Martín", "Excelente"))))
    rid = rg.resenas(db, n["id"], "todas")[0]["id"]
    rg.marcar(db, n["id"], rid, "respondida", "Gracias Martín", "t")
    assert rg.purgar(db, reloj["ahora"] + timedelta(days=rg.DIAS_TEXTO - 1)) == 0
    assert rg.purgar(db, reloj["ahora"] + timedelta(days=rg.DIAS_TEXTO + 1)) == 1
    r = rg.resena(db, n["id"], rid)
    assert r["texto"] is None and r["autor"] is None and r["link"] is None
    assert r["rating"] == 5 and r["respuesta"] == "Gracias Martín"


# ── rutas ────────────────────────────────────────────────────────────────────

def test_api_del_equipo(app, db, reloj, monkeypatch):
    c = _login(app, db)
    n = _con_pid(db)
    monkeypatch.setattr(rg, "pedir_places", Google(_datos(("a", 2, "Sofía", "Frío"))))
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "clave-de-test")
    r = c.post(f"/api/resenas/negocios/{n['id']}/google/leer")
    assert r.status_code == 200 and r.get_json()["nuevas"] == 1
    d = c.get(f"/api/resenas/negocios/{n['id']}").get_json()
    assert d["negocio"]["place_id_efectivo"] == PID
    assert d["google_resumen"]["sin_responder"] == 1
    res = d["google_resenas"][0]
    assert res["responder"].startswith("https://www.google.com/maps/reviews/")
    r = c.patch(f"/api/resenas/negocios/{n['id']}/google/resenas/{res['id']}",
                json={"estado": "respondida", "respuesta": "Perdón"})
    assert r.get_json()["resena"]["estado"] == "respondida"
    assert c.patch(f"/api/resenas/negocios/{n['id']}/google/resenas/9999", json={"estado": "respondida"}).status_code == 404
    sin = rs.crear_negocio(db, {"nombre": "Sin"}, "t")
    r = c.post(f"/api/resenas/negocios/{sin['id']}/google/leer")
    assert r.status_code == 400 and "Place ID" in r.get_json()["error"]


def test_api_pide_sesion(app, db):
    n = _con_pid(db)
    c = app.test_client()
    r = c.post(f"/api/resenas/negocios/{n['id']}/google/leer")
    assert r.status_code in (302, 401, 403)


def test_panel_del_comercio_responde_sus_resenas(app, db, reloj):
    n = _con_pid(db)
    rg.leer(db, n["id"], "manual", pedir=Google(_datos(("a", 2, "Sofía <b>", "Llegó <script>frío</script>"))))
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    otro = rs.crear_negocio(db, {"nombre": "Otro", "place_id": "ChIJotroComercioDeEjemplo"}, "t")
    rg.leer(db, otro["id"], "manual", pedir=Google(_datos(("z", 1, "X", "ajeno"))))
    c = app.test_client()
    html = c.get(f"/comercio/{a['token']}?v=google").get_data(as_text=True)
    assert "Respondé tus reseñas de Google" in html and "Sofía" in html
    assert "<script>frío" not in html and "&lt;script&gt;" in html
    assert "ajeno" not in html
    assert "<span class='n'>1</span>" in html  # el pendiente en la pestaña
    rid = rg.resenas(db, n["id"], "todas")[0]["id"]
    r = c.post(f"/comercio/{a['token']}/g/{rid}", data={"accion": "respondida", "respuesta": "Perdón, Sofía"})
    assert r.status_code == 303 and "ok=1" in r.headers["Location"]
    hecha = rg.resena(db, n["id"], rid)
    assert hecha["respuesta"] == "Perdón, Sofía" and hecha["respondida_por"] == "Marcela · comercio"
    # No puede tocar la reseña de otro comercio.
    ajena = rg.resenas(db, otro["id"], "todas")[0]["id"]
    assert c.post(f"/comercio/{a['token']}/g/{ajena}", data={"accion": "respondida"}).status_code == 404
    assert rg.resena(db, otro["id"], ajena)["estado"] == "sin_responder"
    r = c.post(f"/comercio/{a['token']}/g/{rid}", data={"accion": "otra"})
    assert r.status_code == 303 and "error=" in r.headers["Location"]
    # Revocado, no entra.
    rs.revocar_acceso(db, n["id"], a["id"])
    assert c.post(f"/comercio/{a['token']}/g/{rid}", data={"accion": "sin_responder"}).status_code == 404


def test_tablas_y_migracion(db):
    conn = sqlite3.connect(db)
    cols = {c[1] for c in conn.execute("PRAGMA table_info(rs_negocios)")}
    tablas = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.row_factory = sqlite3.Row
    rg.init_google(conn)  # idempotente
    conn.close()
    assert "place_id" in cols and {"rs_google_resenas", "rs_google_lecturas"} <= tablas
    assert AHORA
