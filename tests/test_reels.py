"""Panel Reels: el CRM no guarda nada, le pregunta a reels.scalerics.com."""

import json
import sqlite3

import pytest
import requests
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from routes import reels as rl


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("CRM_REELS_TOKEN", "t" * 64)
    r = str(tmp_path / "rl.db")
    init_db(r)
    return r


def _cliente(db, email, paneles):
    uid = create_user(db, name="Ana", email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    conn = sqlite3.connect(db)
    rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                       ("rol-" + email, json.dumps(paneles))).lastrowid
    conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
    conn.commit()
    conn.close()
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Ana"
    return c


class _Resp:
    def __init__(self, status=200, datos=None, headers=None):
        self.status_code = status
        self._datos = datos or {}
        self.headers = headers or {}

    def json(self):
        return self._datos

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_sin_panel_es_403(db):
    c = _cliente(db, "vende@scalerics.com", ["cola"])
    assert c.get("/api/reels").status_code == 403


def test_lista_pasa_el_token_y_devuelve_los_reels(db, monkeypatch):
    visto = {}

    def falso_get(url, params=None, headers=None, timeout=None, **kw):
        visto.update(url=url, headers=headers, params=params)
        return _Resp(datos={"reels": [{"slug": "el-juicio", "estado": "por_revisar"}]})

    monkeypatch.setattr(rl.requests, "get", falso_get)
    c = _cliente(db, "mkt@scalerics.com", ["reels"])
    d = c.get("/api/reels").get_json()
    assert d["ok"] and d["reels"][0]["slug"] == "el-juicio"
    assert visto["url"].endswith("/api/reels") and visto["params"] == {"todos": "1"}
    assert visto["headers"]["x-crm-token"] == "t" * 64


def test_estado_manda_quien_y_valida(db, monkeypatch):
    enviado = {}

    def falso_post(url, headers=None, json=None, timeout=None, **kw):
        enviado.update(url=url, json=json)
        return _Resp()

    monkeypatch.setattr(rl.requests, "post", falso_post)
    c = _cliente(db, "mkt@scalerics.com", ["reels"])
    assert c.post("/api/reels/el-juicio/estado", json={"estado": "publicado"}).status_code == 400
    assert c.post("/api/reels/El_Juicio/estado", json={"estado": "en_cola"}).status_code == 400
    r = c.post("/api/reels/el-juicio/estado", json={"estado": "en_cola"})
    assert r.status_code == 200
    assert enviado["url"].endswith("/api/revision")
    assert enviado["json"] == {"slug": "el-juicio", "estado": "en_cola", "quien": "Ana"}


def test_video_redirige_a_github_sin_bajarlo(db, monkeypatch):
    def falso_get(url, params=None, headers=None, timeout=None, allow_redirects=True, **kw):
        assert allow_redirects is False
        return _Resp(302, headers={"Location": "https://raw.githubusercontent.com/x.mp4?token=abc"})

    monkeypatch.setattr(rl.requests, "get", falso_get)
    c = _cliente(db, "mkt@scalerics.com", ["reels"])
    r = c.get("/api/reels/el-juicio/video")
    assert r.status_code == 302
    assert r.headers["Location"].startswith("https://raw.githubusercontent.com/")


def test_panel_caido_da_502(db, monkeypatch):
    def caido(*a, **kw):
        raise requests.ConnectionError("no")

    monkeypatch.setattr(rl.requests, "get", caido)
    c = _cliente(db, "mkt@scalerics.com", ["reels"])
    assert c.get("/api/reels").status_code == 502


def test_el_panel_llega_a_los_roles_de_marketing(tmp_path):
    r = str(tmp_path / "roles.db")
    init_db(r)
    conn = sqlite3.connect(r)
    conn.execute("INSERT INTO roles (name, panel_access) VALUES ('mkt', ?)", (json.dumps(["marketing"]),))
    conn.execute("INSERT INTO roles (name, panel_access) VALUES ('ventas', ?)", (json.dumps(["cola"]),))
    # Como en producción: los roles ya existen cuando arranca por primera vez
    # el código con el panel nuevo (el reparto corre una sola vez por panel).
    conn.execute("DELETE FROM panel_grants_aplicados WHERE panel = 'reels'")
    conn.commit()
    conn.close()
    init_db(r)
    conn = sqlite3.connect(r)
    filas = dict(conn.execute("SELECT name, panel_access FROM roles WHERE name IN ('mkt','ventas')").fetchall())
    conn.close()
    assert "reels" in json.loads(filas["mkt"])
    assert "reels" not in json.loads(filas["ventas"])


def test_horarios_valida_y_reenvia(db, monkeypatch):
    enviado = {}

    def falso_post(url, headers=None, json=None, timeout=None, **kw):
        enviado.update(url=url, json=json, headers=headers)
        return _Resp()

    monkeypatch.setattr(rl.requests, "post", falso_post)
    c = _cliente(db, "mkt@scalerics.com", ["reels"])
    for malo in ({"instagram": [12, 12], "tiktok": []}, {"instagram": [24], "tiktok": []},
                 {"instagram": ["15"], "tiktok": []}, {"instagram": [True], "tiktok": []}, {"instagram": [15]}):
        assert c.put("/api/reels/horarios", json=malo).status_code == 400
    r = c.put("/api/reels/horarios", json={"instagram": [21, 12, 15, 18], "tiktok": [12]})
    assert r.status_code == 200
    assert enviado["url"].endswith("/api/horarios")
    assert enviado["json"] == {"instagram": [12, 15, 18, 21], "tiktok": [12], "quien": "Ana"}
    assert enviado["headers"]["x-crm-token"] == "t" * 64


def test_orden_de_la_cola(db, monkeypatch):
    enviado = {}

    def falso_post(url, headers=None, json=None, timeout=None, **kw):
        enviado.update(url=url, json=json)
        return _Resp()

    monkeypatch.setattr(rl.requests, "post", falso_post)
    c = _cliente(db, "mkt@scalerics.com", ["reels"])
    assert c.put("/api/reels/orden", json={"orden": ["ok", "Mal Slug"]}).status_code == 400
    assert c.put("/api/reels/orden", json={"orden": ["el-juicio", "el-golpe"]}).status_code == 200
    assert enviado["json"] == {"orden": ["el-juicio", "el-golpe"]}
