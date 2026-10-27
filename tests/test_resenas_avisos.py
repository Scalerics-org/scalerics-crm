"""Avisos de quejas al comercio (services/resenas_avisos.py)."""

import sqlite3
from datetime import datetime, timedelta

import pytest

from services import resenas as rs
from services import resenas_avisos as ra
from tests.test_resenas import _login, _negocio, app, db, reloj  # noqa: F401

SECRETO = "test"
BASE = "https://crm.test"


class Buzon:
    def __init__(self, estado="ok"):
        self.estado = estado
        self.mails = []

    def __call__(self, to, asunto, cuerpo_html, texto):
        self.mails.append({"to": to, "asunto": asunto, "html": cuerpo_html, "texto": texto})
        return self.estado


def _queja(db, nid, puntaje=2, comentario="Llegó fría", **kw):
    o = rs.opinar(db, nid, puntaje, ip=str(id(kw)) + str(puntaje))
    if comentario:
        rs.comentar(db, o["codigo"], {"comentario": comentario, **kw})
    return o


def _revisar(db, reloj, buzon):
    return ra.revisar(db, BASE, SECRETO, enviar=buzon, ahora=reloj["ahora"])


def test_guardar_preferencias_valida_y_no_manda_lo_viejo(db, reloj):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    vieja = _queja(db, n["id"])
    with pytest.raises(rs.NoSePuede):
        ra.guardar_preferencias(db, a["id"], "no-es-mail", "al_momento")
    with pytest.raises(rs.NoSePuede):
        ra.guardar_preferencias(db, a["id"], "", "al_momento")
    otro = rs.crear_negocio(db, {"nombre": "Otro"}, "t")
    with pytest.raises(rs.NoSePuede):
        ra.guardar_preferencias(db, a["id"], "m@x.com", "al_momento", nid=otro["id"])
    reloj["ahora"] += timedelta(minutes=1)
    p = ra.guardar_preferencias(db, a["id"], "marcela@gmail.com", "al_momento")
    assert p["aviso"] == "al_momento" and p["aviso_desde"]
    buzon = Buzon()
    reloj["ahora"] += timedelta(minutes=30)
    _revisar(db, reloj, buzon)
    assert buzon.mails == []  # la queja de antes de prender no se manda
    nueva = _queja(db, n["id"], comentario="El mozo me trató mal")
    _revisar(db, reloj, buzon)
    assert len(buzon.mails) == 1 and "El mozo me trató mal" in buzon.mails[0]["html"]
    assert "Llegó fría" not in buzon.mails[0]["html"] and vieja["id"] != nueva["id"]


def test_una_queja_se_avisa_una_sola_vez_y_se_agrupan(db, reloj):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    b = rs.crear_acceso(db, n["id"], "Martín", "t")
    ra.guardar_preferencias(db, a["id"], "marcela@gmail.com", "al_momento")
    ra.guardar_preferencias(db, b["id"], "martin@gmail.com", "al_momento")
    _queja(db, n["id"], 1, "Uno")
    _queja(db, n["id"], 3, "Dos", contacto="099 1", quiere_contacto="1")
    rs.opinar(db, n["id"], 5, ip="feliz")  # un 5 sin pedido de contacto no es queja
    buzon = Buzon()
    r = _revisar(db, reloj, buzon)
    assert r["quejas"] == 2 and sorted(m["to"] for m in buzon.mails) == ["marcela@gmail.com", "martin@gmail.com"]
    assert "2 quejas nuevas" in buzon.mails[0]["asunto"] and "Uno" in buzon.mails[0]["html"]
    assert "quiere que lo contacten" in buzon.mails[0]["html"] and "Abrí tu panel" in buzon.mails[0]["texto"]
    reloj["ahora"] += timedelta(hours=1)
    _revisar(db, reloj, buzon)
    assert len(buzon.mails) == 2


def test_espera_el_comentario_antes_de_avisar_una_queja_sin_texto(db, reloj):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    ra.guardar_preferencias(db, a["id"], "marcela@gmail.com", "al_momento")
    _queja(db, n["id"], 2, comentario=None)
    buzon = Buzon()
    _revisar(db, reloj, buzon)
    assert buzon.mails == []
    reloj["ahora"] += timedelta(minutes=ra.ESPERA_COMENTARIO)
    _revisar(db, reloj, buzon)
    assert len(buzon.mails) == 1 and "Sin comentario" in buzon.mails[0]["html"]


def test_una_queja_ya_tomada_no_se_avisa(db, reloj):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    ra.guardar_preferencias(db, a["id"], "marcela@gmail.com", "al_momento")
    o = _queja(db, n["id"])
    rs.atender(db, o["id"], {"estado": "en_curso"}, "Martín", nid=n["id"])
    buzon = Buzon()
    _revisar(db, reloj, buzon)
    assert buzon.mails == []


def test_de_noche_no_sale_nada_y_a_las_8_sale_junto(db, reloj):
    reloj["ahora"] = datetime(2026, 10, 26, 23, 10)
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    ra.guardar_preferencias(db, a["id"], "marcela@gmail.com", "al_momento")
    _queja(db, n["id"], 1, "Noche 1")
    reloj["ahora"] = datetime(2026, 10, 27, 2, 0)
    _queja(db, n["id"], 2, "Noche 2")
    buzon = Buzon()
    assert _revisar(db, reloj, buzon)["en_espera"] == 1 and buzon.mails == []
    reloj["ahora"] = datetime(2026, 10, 27, 8, 0)
    _revisar(db, reloj, buzon)
    assert len(buzon.mails) == 1 and "Noche 1" in buzon.mails[0]["html"] and "Noche 2" in buzon.mails[0]["html"]


def test_topes_entre_mails_y_por_dia(db, reloj, monkeypatch):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    ra.guardar_preferencias(db, a["id"], "marcela@gmail.com", "al_momento")
    buzon = Buzon()
    _queja(db, n["id"], 1, "Primera")
    _revisar(db, reloj, buzon)
    reloj["ahora"] += timedelta(minutes=5)
    _queja(db, n["id"], 1, "Segunda")
    assert _revisar(db, reloj, buzon)["en_espera"] == 1 and len(buzon.mails) == 1
    reloj["ahora"] += timedelta(minutes=ra.MINUTOS_ENTRE_MAILS)
    _revisar(db, reloj, buzon)
    assert len(buzon.mails) == 2 and "Segunda" in buzon.mails[1]["html"]
    monkeypatch.setattr(ra, "MAX_POR_DIA", 2)
    reloj["ahora"] += timedelta(hours=1)
    _queja(db, n["id"], 1, "Tercera")
    assert _revisar(db, reloj, buzon)["en_espera"] == 1 and len(buzon.mails) == 2


def test_un_envio_fallido_cuenta_para_el_tope_y_no_se_repite(db, reloj):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    ra.guardar_preferencias(db, a["id"], "marcela@gmail.com", "al_momento")
    _queja(db, n["id"])
    malo = Buzon("fallo")
    assert _revisar(db, reloj, malo)["fallos"] == 1
    reloj["ahora"] += timedelta(hours=1)
    _revisar(db, reloj, malo)
    assert len(malo.mails) == 1
    assert ra.preferencias(db, a["id"])["ultimo"]["estado"] == "fallo"
    assert ra.avisos(db, n["id"])[0]["nombre"] == "Marcela"


def test_revocado_o_apagado_no_recibe(db, reloj):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    b = rs.crear_acceso(db, n["id"], "Martín", "t")
    ra.guardar_preferencias(db, a["id"], "marcela@gmail.com", "al_momento")
    ra.guardar_preferencias(db, b["id"], "martin@gmail.com", "al_momento")
    rs.revocar_acceso(db, n["id"], a["id"])
    ra.guardar_preferencias(db, b["id"], "martin@gmail.com", "no")
    _queja(db, n["id"])
    buzon = Buzon()
    _revisar(db, reloj, buzon)
    assert buzon.mails == []


def test_resumen_diario_a_las_9_una_vez(db, reloj):
    reloj["ahora"] = datetime(2026, 10, 26, 12, 0)
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    ra.guardar_preferencias(db, a["id"], "marcela@gmail.com", "diario")
    _queja(db, n["id"], 2, "Tardó mucho")
    rs.opinar(db, n["id"], 5, ip="z")
    buzon = Buzon()
    reloj["ahora"] = datetime(2026, 10, 27, 8, 30)
    _revisar(db, reloj, buzon)
    assert buzon.mails == []
    reloj["ahora"] = datetime(2026, 10, 27, 9, 5)
    assert _revisar(db, reloj, buzon)["resumenes"] == 1
    m = buzon.mails[0]
    assert "del 26/10" in m["asunto"] and "Tardó mucho" in m["html"] and "Opiniones: 2" in m["texto"]
    reloj["ahora"] = datetime(2026, 10, 27, 15, 0)
    _revisar(db, reloj, buzon)
    assert len(buzon.mails) == 1


def test_link_firmado_entra_al_panel_vence_y_se_corta_al_revocar(db, reloj):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    t = ra.firmar(a["id"], SECRETO, reloj["ahora"])
    assert rs.por_token(db, t, SECRETO)["id"] == a["id"]
    assert rs.por_token(db, t) is None  # sin secreto no vale
    assert rs.por_token(db, t, "otro-secreto") is None
    assert rs.por_token(db, t[:-1] + ("0" if t[-1] != "0" else "1"), SECRETO) is None
    falso = f"m{a['id'] + 1}" + t[len(f"m{a['id']}"):]
    assert rs.por_token(db, falso, SECRETO) is None
    reloj["ahora"] += timedelta(days=ra.DIAS_LINK_FIRMADO + 1)
    assert ra.acceso_firmado(t, SECRETO, reloj["ahora"]) is None
    dev = "scalerics-dev-key-change-in-prod"
    assert rs.por_token(db, ra.firmar(a["id"], dev, reloj["ahora"]), dev) is None
    assert ra.revisar(db, BASE, dev, enviar=Buzon(), ahora=reloj["ahora"])["quejas"] == 0
    t2 = ra.firmar(a["id"], SECRETO, reloj["ahora"])
    rs.revocar_acceso(db, n["id"], a["id"])
    assert rs.por_token(db, t2, SECRETO) is None


def test_el_mail_lleva_un_link_firmado_que_abre_el_panel(app, db, reloj):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    ra.guardar_preferencias(db, a["id"], "marcela@gmail.com", "al_momento")
    _queja(db, n["id"], 1, "<b>hola</b>")
    buzon = Buzon()
    ra.revisar(db, BASE, app.secret_key, enviar=buzon, ahora=reloj["ahora"])
    m = buzon.mails[0]
    assert "&lt;b&gt;hola&lt;/b&gt;" in m["html"] and "<b>hola</b>" not in m["html"]
    link = next(p for p in m["texto"].split() if p.startswith(BASE + "/comercio/"))
    assert a["token"] not in m["html"]
    r = app.test_client().get(link[len(BASE):])
    assert r.status_code == 200 and "hola, Marcela" in r.get_data(as_text=True)


def test_el_comercio_prende_sus_avisos_desde_el_panel(app, db):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    c = app.test_client()
    pagina = c.get(f"/comercio/{a['token']}?v=avisos").get_data(as_text=True)
    assert "Enterate de las quejas" in pagina and "<script" not in pagina and " style=" not in pagina
    r = c.post(f"/comercio/{a['token']}/avisos", data={"email": "mal", "aviso": "al_momento"})
    assert "error=" in r.headers["Location"]
    r = c.post(f"/comercio/{a['token']}/avisos", data={"email": "marcela@gmail.com", "aviso": "diario"})
    assert "ok=1" in r.headers["Location"]
    p = ra.preferencias(db, a["id"])
    assert p["email"] == "marcela@gmail.com" and p["aviso"] == "diario"
    assert "checked" in c.get(f"/comercio/{a['token']}?v=avisos").get_data(as_text=True)


def test_el_equipo_configura_avisos_y_ve_el_mail_de_ejemplo(app, db):
    n = _negocio(db)
    c = _login(app, db)
    r = c.post(f"/api/resenas/negocios/{n['id']}/accesos", json={"nombre": "Marcela", "email": "nope"})
    assert r.status_code == 400 and rs.accesos(db, n["id"]) == []
    r = c.post(f"/api/resenas/negocios/{n['id']}/accesos", json={"nombre": "Marcela", "email": "m@gmail.com"})
    assert r.status_code == 201 and r.get_json()["acceso"]["aviso"] == "al_momento"
    aid = r.get_json()["acceso"]["id"]
    r = c.put(f"/api/resenas/negocios/{n['id']}/accesos/{aid}/avisos", json={"email": "m@gmail.com", "aviso": "no"})
    assert r.status_code == 200 and r.get_json()["avisos"]["aviso"] == "no"
    otro = rs.crear_negocio(db, {"nombre": "Otro"}, "t")
    r = c.put(f"/api/resenas/negocios/{otro['id']}/accesos/{aid}/avisos", json={"email": "", "aviso": "no"})
    assert r.status_code == 404
    det = c.get(f"/api/resenas/negocios/{n['id']}").get_json()
    assert det["accesos"][0]["email"] == "m@gmail.com" and det["avisos"] == []
    ej = c.get(f"/resenas/aviso-ejemplo/{n['id']}")
    assert ej.status_code == 200 and "Parrilla El Fogón" in ej.get_data(as_text=True)
    assert "llegó fría" in ej.get_data(as_text=True)
    assert c.get(f"/resenas/aviso-ejemplo/{n['id']}?tipo=resumen").status_code == 200
    assert app.test_client().get(f"/resenas/aviso-ejemplo/{n['id']}").status_code in (302, 401)


def test_init_es_idempotente_y_migra(db):
    conn = sqlite3.connect(db)
    rs.init_resenas(conn)
    rs.init_resenas(conn)
    cols = {c[1] for c in conn.execute("PRAGMA table_info(rs_accesos)")}
    conn.close()
    assert {"email", "aviso", "aviso_desde"} <= cols


# ── reseñas malas de Google en el aviso al momento ───────────────────────────

def _google(db, nid, *resenas, publicada="2026-10-26T15:30:00Z"):
    from services import resenas_google as rg
    from tests.test_resenas_google import PID, Google, _datos
    datos = _datos(*resenas)
    for x in datos["reviews"]:
        x["publishTime"] = publicada
    rs.editar_negocio(db, nid, {"place_id": PID})
    return rg.leer(db, nid, "manual", pedir=Google(datos))


def test_una_resena_mala_de_google_llega_con_la_respuesta_y_una_sola_vez(db, reloj):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    ra.guardar_preferencias(db, a["id"], "marcela@gmail.com", "al_momento")
    reloj["ahora"] += timedelta(minutes=1)
    _google(db, n["id"], ("a", 2, "Sofía", "Tardaron una hora y <b>nadie</b> avisó"), ("b", 5, "Lu", "Riquísimo"))
    buzon = Buzon()
    r = _revisar(db, reloj, buzon)
    assert r["quejas"] == 1 and len(buzon.mails) == 1
    m = buzon.mails[0]
    assert "una reseña mala en Google para responder" in m["asunto"]
    assert "Tardaron una hora y &lt;b&gt;nadie&lt;/b&gt;" in m["html"] and "<b>nadie</b>" not in m["html"]
    assert "Respuesta sugerida" in m["html"] and "Responder en Google" in m["html"]
    assert "https://www.google.com/maps/reviews/a" in m["html"] and "Riquísimo" not in m["html"]
    assert "Responder: https://www.google.com/maps/reviews/a" in m["texto"]
    reloj["ahora"] += timedelta(hours=1)
    _revisar(db, reloj, buzon)
    assert len(buzon.mails) == 1  # no se repite


def test_queja_y_resena_de_google_van_en_el_mismo_mail(db, reloj):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    ra.guardar_preferencias(db, a["id"], "marcela@gmail.com", "al_momento")
    reloj["ahora"] += timedelta(minutes=1)
    _queja(db, n["id"], 1, "Llegó cruda")
    _google(db, n["id"], ("a", 3, "Sofía", "Caro para lo que es"))
    buzon = Buzon()
    _revisar(db, reloj, buzon)
    assert len(buzon.mails) == 1
    m = buzon.mails[0]
    assert "2 opiniones para atender, 1 en Google" in m["asunto"]
    assert "Llegó cruda" in m["html"] and "Caro para lo que es" in m["html"]
    assert ra.avisos(db, n["id"])[0]["cantidad"] == 2


def test_de_google_no_se_avisan_las_viejas_ni_las_respondidas_ni_las_buenas(db, reloj):
    from services import resenas_google as rg
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    # Leídas antes de prender el aviso: no se mandan.
    _google(db, n["id"], ("antes", 1, "X", "Horrible"))
    reloj["ahora"] += timedelta(minutes=1)
    ra.guardar_preferencias(db, a["id"], "marcela@gmail.com", "al_momento")
    reloj["ahora"] += timedelta(minutes=1)
    # La primera lectura trae reseñas de hace meses: tampoco.
    _google(db, n["id"], ("vieja", 1, "Y", "Malísimo"), publicada="2026-06-01T10:00:00Z")
    reloj["ahora"] += timedelta(hours=6)  # pasa el tope de lecturas a mano
    _google(db, n["id"], ("buena", 4, "Z", "Bien"), ("resp", 2, "W", "Lento"))
    rid = [x for x in rg.resenas(db, n["id"], "todas") if x["texto"] == "Lento"][0]["id"]
    rg.marcar(db, n["id"], rid, "respondida", "Gracias", "Marcela")
    buzon = Buzon()
    _revisar(db, reloj, buzon)
    assert buzon.mails == []


def test_el_resumen_y_el_mail_de_ejemplo_muestran_la_de_google(app, db):
    n = _negocio(db)
    _google(db, n["id"], ("a", 2, "Sofía", "Tardaron una hora"))
    c = _login(app, db)
    html = c.get(f"/resenas/aviso-ejemplo/{n['id']}").get_data(as_text=True)
    assert "Tardaron una hora" in html and "Responder en Google" in html


def test_init_crea_la_tabla_de_avisos_de_google(db):
    conn = sqlite3.connect(db)
    rs.init_resenas(conn)
    tablas = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert "rs_avisos_gr" in tablas
