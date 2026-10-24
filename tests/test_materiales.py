"""Portal de materiales del cliente (services/materiales.py, routes/materiales.py)."""

import io
import json
import os
import sqlite3
import zipfile
from datetime import timedelta

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import materiales as mt


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    monkeypatch.setenv("MATERIALES_DIR", str(tmp_path / "archivos"))
    r = str(tmp_path / "mat.db")
    init_db(r)
    return r


def _portal(db, tipo="web", **kw):
    return mt.crear(db, {"nombre": "Web de Bambú", "tipo": tipo, **kw}, "test")


def _items(db, pid):
    return {it["titulo"]: it for it in mt.items(db, pid)}


def _f(nombre, datos=b"hola"):
    return (nombre, io.BytesIO(datos))


def _envejecer(db, pid, dias):
    conn = sqlite3.connect(db)
    conn.execute("UPDATE mat_portales SET creado_en = ? WHERE id = ?",
                 (mt._txt(mt._ahora() - timedelta(days=dias)), pid))
    conn.commit()
    conn.close()


# ── servicio ─────────────────────────────────────────────────────────────────

def test_init_db_crea_las_tablas(db):
    conn = sqlite3.connect(db)
    nombres = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"mat_portales", "mat_items", "mat_archivos", "mat_eventos"} <= nombres


@pytest.mark.parametrize("tipo", mt.TIPOS)
def test_cada_tipo_arma_su_checklist(db, tipo):
    p = _portal(db, tipo)
    its = mt.items(db, p["id"])
    assert [i["titulo"] for i in its] == [t[0] for t in mt.PLANTILLAS[tipo][1]]
    assert all(i["estado"] == "pendiente" for i in its)
    assert p["estado"] == "esperando" and p["pct"] == 0 and len(p["codigo"]) >= 20


def test_las_plantillas_no_piden_contrasenas():
    for _, (_, its) in mt.PLANTILLAS.items():
        for titulo, desc, clase, _ in its:
            assert clase in mt.CLASES
            texto = (titulo + " " + desc).lower()
            if "contraseña" in texto:
                assert "no escribas contraseñas" in texto


def test_crear_valida(db):
    with pytest.raises(mt.NoSePuede):
        mt.crear(db, {"nombre": "  "}, "test")
    with pytest.raises(mt.NoSePuede):
        mt.crear(db, {"nombre": "x", "tipo": "nave"}, "test")
    with pytest.raises(mt.NoSePuede):
        mt.crear(db, {"nombre": "x", "fecha_limite": "mañana"}, "test")
    with pytest.raises(mt.NoSePuede):
        mt.crear(db, {"nombre": "x", "business_id": 999}, "test")


def test_el_cliente_manda_y_el_equipo_revisa(db):
    p = _portal(db)
    its = _items(db, p["id"])
    logo, quienes = its["Logo"]["id"], its["Quiénes somos"]["id"]
    it = mt.enviar(db, p["id"], logo, "cliente", archivos=[_f("logo.svg", b"<svg/>"), _f("logo.png", b"png")])
    assert it["estado"] == "enviado"
    mt.enviar(db, p["id"], quienes, "cliente", texto="Somos una tienda de ropa de Colonia.")
    p2 = mt.portal(db, p["id"])
    assert p2["estado"] == "revisar" and p2["conteo"]["enviado"] == 2 and p2["ultimo_cliente"]
    assert mt.revisar(db, logo, "aprobar", "", "Ana")["estado"] == "aprobado"
    with pytest.raises(mt.NoSePuede):
        mt.revisar(db, quienes, "pedir", "", "Ana")  # sin comentario no
    it = mt.revisar(db, quienes, "pedir", "Contanos también desde cuándo están.", "Ana")
    assert it["estado"] == "cambios" and it["comentario"].startswith("Contanos")
    p3 = mt.portal(db, p["id"])
    assert p3["hechos"] == 1 and p3["estado"] == "esperando"
    acciones = [e["accion"] for e in mt.detalle(db, p["id"])["eventos"]]
    assert {"creado", "enviado", "aprobado", "cambios_pedidos"} <= set(acciones)


def test_completo_cuando_todo_lo_obligatorio_esta_aprobado(db):
    p = _portal(db, "otro")
    for it in mt.items(db, p["id"]):
        mt.enviar(db, p["id"], it["id"], "cliente", texto="dato" if it["clase"] != "archivo" else None,
                  archivos=[_f("a.pdf")] if it["clase"] == "archivo" else ())
        mt.revisar(db, it["id"], "aprobar", "", "Ana")
    assert mt.portal(db, p["id"])["estado"] == "completo"
    assert mt.editar(db, p["id"], {"cerrado": True}, "Ana")["estado"] == "cerrado"
    assert mt.editar(db, p["id"], {"cerrado": False}, "Ana")["estado"] == "completo"


def test_trabado_y_vencido(db):
    p = _portal(db, fecha_limite="2020-01-01")
    _envejecer(db, p["id"], mt.DIAS_TRABADO + 1)
    p = mt.portal(db, p["id"])
    assert p["estado"] == "trabado" and p["vencido"] and p["dias_sin_cliente"] >= mt.DIAS_TRABADO
    r = mt.resumen([p])
    assert r["trabados"] == 1 and r["vencidos"] == 1
    # Que el cliente mande algo lo destraba.
    mt.enviar(db, p["id"], _items(db, p["id"])["Logo"]["id"], "cliente", archivos=[_f("l.png")])
    assert mt.portal(db, p["id"])["estado"] == "revisar"


def test_archivos_rechazados_no_dejan_nada(db, monkeypatch):
    p = _portal(db)
    logo = _items(db, p["id"])["Logo"]["id"]
    with pytest.raises(mt.NoSePuede, match="tipo de archivo"):
        mt.enviar(db, p["id"], logo, "cliente", archivos=[_f("bien.png"), _f("virus.exe")])
    monkeypatch.setattr(mt, "MAX_ARCHIVO", 10)
    with pytest.raises(mt.NoSePuede, match="pesa más"):
        mt.enviar(db, p["id"], logo, "cliente", archivos=[_f("chico.png", b"1"), _f("grande.png", b"x" * 11)])
    with pytest.raises(mt.NoSePuede, match="vacío"):
        mt.enviar(db, p["id"], logo, "cliente", archivos=[_f("vacio.png", b"")])
    with pytest.raises(mt.NoSePuede, match="al menos un archivo"):
        mt.enviar(db, p["id"], logo, "cliente")
    assert mt.items(db, p["id"])[0]["archivos"] == []
    assert os.listdir(os.path.join(mt.carpeta(db), str(p["id"]))) == []
    assert mt.portal(db, p["id"])["estado"] == "esperando"


def test_tope_de_archivos_por_item(db, monkeypatch):
    monkeypatch.setattr(mt, "MAX_POR_ITEM", 2)
    p = _portal(db)
    fotos = _items(db, p["id"])["Fotos"]["id"]
    mt.enviar(db, p["id"], fotos, "cliente", archivos=[_f("1.jpg"), _f("2.jpg")])
    with pytest.raises(mt.NoSePuede, match="Como mucho"):
        mt.enviar(db, p["id"], fotos, "cliente", archivos=[_f("3.jpg")])


def test_un_item_de_texto_no_acepta_archivos_y_pide_texto(db):
    p = _portal(db)
    quienes = _items(db, p["id"])["Quiénes somos"]["id"]
    with pytest.raises(mt.NoSePuede, match="con texto"):
        mt.enviar(db, p["id"], quienes, "cliente", archivos=[_f("a.pdf")])
    with pytest.raises(mt.NoSePuede, match="Escribí"):
        mt.enviar(db, p["id"], quienes, "cliente", texto="   ")


def test_no_se_manda_a_un_item_de_otro_portal_ni_a_uno_cerrado_o_aprobado(db):
    a, b = _portal(db), _portal(db)
    logo_b = _items(db, b["id"])["Logo"]["id"]
    with pytest.raises(mt.NoSePuede):
        mt.enviar(db, a["id"], logo_b, "cliente", archivos=[_f("l.png")])
    mt.enviar(db, b["id"], logo_b, "cliente", archivos=[_f("l.png")])
    mt.revisar(db, logo_b, "aprobar", "", "Ana")
    with pytest.raises(mt.NoSePuede, match="aprobado"):
        mt.enviar(db, b["id"], logo_b, "cliente", archivos=[_f("otro.png")])
    # El equipo sí puede sumar a un ítem aprobado (lo que llegó por WhatsApp).
    assert mt.enviar(db, b["id"], logo_b, "Ana", archivos=[_f("otro.png")], cliente=False)["estado"] == "aprobado"
    mt.editar(db, a["id"], {"cerrado": True}, "Ana")
    with pytest.raises(mt.NoSePuede, match="cerrado"):
        mt.enviar(db, a["id"], _items(db, a["id"])["Logo"]["id"], "cliente", archivos=[_f("l.png")])


def test_quitar_archivo(db):
    p = _portal(db)
    logo = _items(db, p["id"])["Logo"]["id"]
    mt.enviar(db, p["id"], logo, "cliente", archivos=[_f("l.png")])
    a = mt.items(db, p["id"])[0]["archivos"][0]
    otro = _portal(db)
    with pytest.raises(mt.NoSePuede):
        mt.quitar_archivo(db, a["id"], "cliente", pid=otro["id"])
    mt.quitar_archivo(db, a["id"], "cliente", pid=p["id"])
    it = mt.items(db, p["id"])[0]
    assert it["archivos"] == [] and it["estado"] == "pendiente"
    assert os.listdir(os.path.join(mt.carpeta(db), str(p["id"]))) == []


def test_zip_entrega_ordena_por_item_y_suma_los_textos(db):
    p = _portal(db)
    its = _items(db, p["id"])
    mt.enviar(db, p["id"], its["Logo"]["id"], "cliente", archivos=[_f("logo.png", b"A"), _f("logo.png", b"B")])
    mt.enviar(db, p["id"], its["Fotos"]["id"], "cliente", archivos=[_f("../../etc/passwd.jpg", b"C")])
    mt.enviar(db, p["id"], its["Quiénes somos"]["id"], "cliente", texto="Desde 1998.")
    mt.revisar(db, its["Logo"]["id"], "aprobar", "", "Ana")
    tmp, nombre = mt.zip_entrega(db, p["id"])
    z = zipfile.ZipFile(tmp)
    nombres = z.namelist()
    assert nombre == "materiales-web-de-bambu.zip"
    assert "01 Logo/logo.png" in nombres and "01 Logo/logo (2).png" in nombres
    assert all(".." not in n and not n.startswith("/") for n in nombres)
    assert "Desde 1998." in z.read("textos.md").decode()
    tmp, _ = mt.zip_entrega(db, p["id"], solo_aprobados=True)
    assert [n for n in zipfile.ZipFile(tmp).namelist() if n != "textos.md"] == ["01 Logo/logo.png",
                                                                                 "01 Logo/logo (2).png"]


def test_agregar_y_borrar_item_y_borrar_portal(db):
    p = _portal(db)
    it = mt.agregar_item(db, p["id"], {"titulo": "Menú con precios", "clase": "archivo", "obligatorio": False}, "Ana")
    assert it["obligatorio"] == 0 and it["orden"] == len(mt.PLANTILLAS["web"][1])
    with pytest.raises(mt.NoSePuede):
        mt.agregar_item(db, p["id"], {"titulo": "x", "clase": "video"}, "Ana")
    mt.enviar(db, p["id"], it["id"], "cliente", archivos=[_f("menu.pdf")])
    mt.borrar_item(db, it["id"])
    assert "Menú con precios" not in _items(db, p["id"])
    mt.enviar(db, p["id"], _items(db, p["id"])["Logo"]["id"], "cliente", archivos=[_f("l.png")])
    mt.borrar(db, p["id"])
    assert mt.listar(db) == []
    assert not os.path.exists(os.path.join(mt.carpeta(db), str(p["id"])))


def test_la_visita_se_cuenta_una_vez_por_rato(db):
    p = _portal(db)
    mt.visita(db, p["id"])
    mt.visita(db, p["id"])
    abrio = [e for e in mt.detalle(db, p["id"])["eventos"] if e["accion"] == "abrio"]
    assert len(abrio) == 1


def test_nombre_seguro():
    assert mt.nombre_seguro("Catálogo / 2026.xlsx") == "Catalogo _ 2026.xlsx"
    assert mt.nombre_seguro("../..") == "archivo"
    assert mt.nombre_seguro("") == "archivo"


def test_la_ruta_del_archivo_no_sale_de_la_carpeta(db):
    with pytest.raises(mt.NoSePuede):
        mt.ruta_archivo(db, {"portal_id": 1, "guardado": "../../leads.db"})


# ── rutas del equipo ─────────────────────────────────────────────────────────

def _usuario(db, email, paneles=None):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if paneles is not None:
        conn = sqlite3.connect(db)
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                           ("rol-" + email, json.dumps(paneles))).lastrowid
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
        conn.commit()
        conn.close()
    return uid


def _app(db):
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    return app


def _equipo(db, email="dev@scalerics.com", paneles=("materiales",)):
    uid = _usuario(db, email, list(paneles))
    c = _app(db).test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Dev"
    return c


def test_sin_el_panel_da_403(db):
    c = _equipo(db, paneles=("clientes",))
    assert c.get("/api/materiales").status_code == 403
    assert c.post("/api/materiales", json={"nombre": "x"}).status_code == 403
    p = _portal(db)
    mt.enviar(db, p["id"], _items(db, p["id"])["Logo"]["id"], "cliente", archivos=[_f("l.png")])
    aid = mt.items(db, p["id"])[0]["archivos"][0]["id"]
    assert c.get(f"/api/materiales/archivos/{aid}").status_code == 403
    assert c.get(f"/api/materiales/{p['id']}/zip").status_code == 403


def test_sin_sesion_la_api_no_responde(db):
    r = _app(db).test_client().get("/api/materiales")
    assert r.status_code in (302, 401, 403)


def test_api_alta_revision_y_bajada(db):
    c = _equipo(db)
    r = c.post("/api/materiales", json={"nombre": "Tienda Bambú", "tipo": "tienda", "fecha_limite": "2026-11-30"})
    assert r.status_code == 201
    p = r.get_json()["portal"]
    assert p["link"] == f"https://crm.test/m/{p['codigo']}"
    d = c.get("/api/materiales").get_json()
    assert d["resumen"]["abiertos"] == 1 and d["tipos"]["tienda"] == "Tienda online"
    det = c.get(f"/api/materiales/{p['id']}").get_json()["portal"]
    logo = det["items"][0]
    # El equipo sube por el cliente.
    r = c.post(f"/api/materiales/items/{logo['id']}/archivos",
               data={"archivos": [(io.BytesIO(b"<svg onload=alert(1)/>"), "logo.svg")]},
               content_type="multipart/form-data")
    assert r.get_json()["ok"]
    aid = c.get(f"/api/materiales/{p['id']}").get_json()["portal"]["items"][0]["archivos"][0]["id"]
    r = c.get(f"/api/materiales/archivos/{aid}")
    assert r.status_code == 200 and r.data == b"<svg onload=alert(1)/>"
    assert "attachment" in r.headers["Content-Disposition"]
    assert r.mimetype == "application/octet-stream" and r.headers["X-Content-Type-Options"] == "nosniff"
    r = c.post(f"/api/materiales/items/{logo['id']}/revision", json={"accion": "pedir"})
    assert r.status_code == 400
    r = c.post(f"/api/materiales/items/{logo['id']}/revision", json={"accion": "aprobar"})
    assert r.get_json()["item"]["estado"] == "aprobado"
    r = c.get(f"/api/materiales/{p['id']}/zip")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    assert "01 Logo/logo.svg" in zipfile.ZipFile(io.BytesIO(r.data)).namelist()
    r = c.post(f"/api/materiales/{p['id']}/items", json={"titulo": "Menú", "clase": "texto"})
    assert r.status_code == 201
    assert c.delete(f"/api/materiales/items/{r.get_json()['item']['id']}").get_json()["ok"]
    assert c.patch(f"/api/materiales/{p['id']}", json={"cerrado": True}).get_json()["portal"]["estado"] == "cerrado"
    assert c.patch(f"/api/materiales/{p['id']}", json={"fecha_limite": "x"}).status_code == 400
    assert c.delete(f"/api/materiales/archivos/{aid}").get_json()["ok"]
    assert c.delete(f"/api/materiales/{p['id']}").get_json()["ok"]
    assert c.get(f"/api/materiales/{p['id']}").status_code == 404


def test_api_clientes_sugiere_los_del_crm(db):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO businesses (name, crm_status, phone) VALUES ('Cliente Uno', 'en_desarrollo', '1')")
    conn.execute("INSERT INTO businesses (name, crm_status, phone) VALUES ('Lead Frío', 'sin_contactar', '2')")
    conn.commit()
    conn.close()
    c = _equipo(db)
    nombres = [x["name"] for x in c.get("/api/materiales/clientes").get_json()["clientes"]]
    assert nombres == ["Cliente Uno"]


# ── página del cliente ───────────────────────────────────────────────────────

def test_la_pagina_del_cliente_abre_sin_sesion_y_no_muestra_la_ficha(db):
    conn = sqlite3.connect(db)
    bid = conn.execute("INSERT INTO businesses (name, crm_status, phone, notes) "
                       "VALUES ('Bambú SRL', 'en_desarrollo', '099123456', 'NOTA INTERNA')").lastrowid
    conn.commit()
    conn.close()
    p = _portal(db, business_id=bid, mensaje="¡Hola <b>Ana</b>!")
    c = _app(db).test_client()
    r = c.get(f"/m/{p['codigo']}")
    assert r.status_code == 200
    cuerpo = r.get_data(as_text=True)
    assert "Web de Bambú" in cuerpo and "¡Hola &lt;b&gt;Ana&lt;/b&gt;!" in cuerpo
    assert "099123456" not in cuerpo and "NOTA INTERNA" not in cuerpo and "Bambú SRL" not in cuerpo
    assert "<script" not in cuerpo and " style=" not in cuerpo
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "form-action 'self'" in csp and "script-src" not in csp
    assert r.headers["X-Robots-Tag"].startswith("noindex")
    assert "#0088CC" in cuerpo  # paleta Scalerics
    assert c.get("/m/no-existe").status_code == 404
    assert mt.portal(db, p["id"])["ultima_visita"]


def test_el_cliente_sube_desde_la_pagina(db):
    p = _portal(db)
    its = _items(db, p["id"])
    c = _app(db).test_client()
    r = c.post(f"/m/{p['codigo']}/i/{its['Logo']['id']}",
               data={"archivos": [(io.BytesIO(b"png"), "logo.png")], "texto": "El de fondo blanco"},
               content_type="multipart/form-data")
    assert r.status_code == 303 and r.headers["Location"].endswith(f"?ok=1#i{its['Logo']['id']}")
    pagina = c.get(r.headers["Location"]).get_data(as_text=True)
    assert "Recibido" in pagina and "logo.png" in pagina
    r = c.post(f"/m/{p['codigo']}/i/{its['Quiénes somos']['id']}", data={"texto": "Somos <script>x</script>"})
    assert r.status_code == 303
    pagina = c.get(f"/m/{p['codigo']}").get_data(as_text=True)
    assert "&lt;script&gt;x&lt;/script&gt;" in pagina and "<script>x" not in pagina
    r = c.post(f"/m/{p['codigo']}/i/{its['Logo']['id']}",
               data={"archivos": [(io.BytesIO(b"MZ"), "programa.exe")]}, content_type="multipart/form-data")
    assert "error=" in r.headers["Location"]
    assert "no es un tipo de archivo" in c.get(r.headers["Location"]).get_data(as_text=True)
    aid = mt.items(db, p["id"])[0]["archivos"][0]["id"]
    r = c.post(f"/m/{p['codigo']}/a/{aid}/quitar")
    assert r.status_code == 303 and "ok=quitado" in r.headers["Location"]
    assert mt.items(db, p["id"])[0]["archivos"] == []


def test_el_cliente_no_toca_otro_portal(db):
    a, b = _portal(db), _portal(db)
    logo_b = _items(db, b["id"])["Logo"]["id"]
    mt.enviar(db, b["id"], logo_b, "cliente", archivos=[_f("l.png")])
    aid = mt.items(db, b["id"])[0]["archivos"][0]["id"]
    c = _app(db).test_client()
    r = c.post(f"/m/{a['codigo']}/i/{logo_b}", data={"archivos": [(io.BytesIO(b"x"), "x.png")]},
               content_type="multipart/form-data")
    assert "error=" in r.headers["Location"]
    r = c.post(f"/m/{a['codigo']}/a/{aid}/quitar")
    assert "error=" in r.headers["Location"]
    assert len(mt.items(db, b["id"])[0]["archivos"]) == 1
    # Y la página pública no baja archivos.
    assert c.get(f"/api/materiales/archivos/{aid}").status_code in (302, 401, 403)


def test_portal_cerrado_queda_de_solo_lectura(db):
    p = _portal(db)
    mt.editar(db, p["id"], {"cerrado": True}, "Ana")
    pagina = _app(db).test_client().get(f"/m/{p['codigo']}").get_data(as_text=True)
    assert "Ya tenemos todo" in pagina and "<form" not in pagina


def test_el_menu_tiene_materiales_en_operacion():
    html = dashboard.DASHBOARD_HTML
    assert 'id="nav-materiales"' in html and 'id="materiales-panel"' in html
    assert "if (name === 'materiales') matCargar();" in html
    # El editor de roles (otra plantilla) también lo ofrece.
    fuente = open(dashboard.__file__, encoding="utf-8").read()
    assert fuente.count("'sombra','agentes','materiales'];") == 2
    assert "materiales:'Materiales de clientes'" in fuente
