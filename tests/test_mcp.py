"""Las herramientas del conector MCP y el endpoint /mcp.

La base se siembra a mano con leads en distintas etapas. Los tests de las
herramientas llaman a `services.mcp_tools` directo; los del transporte pasan
por el test_client de Flask con un Bearer real.
"""

import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import mcp_tools as mt
from services import oauth_tokens as ot


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    monkeypatch.setenv("ADMIN_EMAIL", "nadie@scalerics.invalid")
    r = str(tmp_path / "leads.db")
    init_db(r)
    return r


@pytest.fixture
def ana(db):
    return create_user(db, name="Ana", email="ana@scalerics.com", phone="099",
                       password_hash=generate_password_hash("x" * 10))


@pytest.fixture
def juan(db):
    return create_user(db, name="Juan", email="juan@scalerics.com", phone="098",
                       password_hash=generate_password_hash("x" * 10))


@pytest.fixture
def sembrada(db, ana, juan):
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO businesses (id, name, phone, email, city, category, crm_status, source, "
        "encargado_id, last_event_at, archivado_en, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "Panadería La Espiga", "099111222", "espiga@mail.com", "Montevideo", "panaderia",
             "demo_1", "meta", ana, "2026-10-01 10:00:00", None, "Quiere web con pedidos"),
            (2, "Ferretería Norte", "099333444", None, "Salto", "ferreteria",
             "presupuesto_enviado", None, juan, "2026-09-20 10:00:00", None, None),
            (3, "Bar Espiga", "099555666", "bar@mail.com", "Montevideo", "bar",
             "sin_contactar", None, None, None, None, None),
            (4, "Archivado Viejo", "099777888", "viejo@mail.com", "Rivera", "bar",
             "demo_1", None, None, None, "2026-09-23", None),
            (5, "Cliente Cerrado", "099999000", "c@mail.com", "Rivera", "bar",
             "cerrado", None, ana, "2026-08-01 10:00:00", None, None),
        ])
    conn.execute("INSERT INTO client_info (client_id, lead_name, rubro, needs) VALUES (1,'Lucía','panadería','pedidos online')")
    # created_at explícito: get_lead_events ordena por esa columna y dos
    # inserts en el mismo segundo dejarían el orden librado a SQLite.
    conn.executemany("INSERT INTO lead_events (lead_id, new_status, note, created_by, created_at) VALUES (?,?,?,?,?)",
                     [(1, "demo_agendada", "agendada para el martes", "Ana", "2026-09-25 10:00:00"),
                      (1, "demo_1", "demo dada", "Ana", "2026-10-01 10:00:00")])
    conn.execute("INSERT INTO call_logs (lead_id, outcome, notes, created_by) VALUES (1,'contestó','habló Lucía','Ana')")
    conn.commit()
    conn.close()
    return db


# ── registro de herramientas ─────────────────────────────────────────────────

def test_init_db_crea_mcp_llamadas(db):
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='mcp_llamadas'").fetchone()
    conn.close()


def test_listar_herramientas_tiene_las_tres_con_schema():
    hs = {h["name"]: h for h in mt.listar_herramientas()}
    assert set(hs) == {"buscar_leads", "ver_lead", "pipeline"}
    for h in hs.values():
        assert h["description"]
        assert h["inputSchema"]["type"] == "object"
        assert h["inputSchema"]["additionalProperties"] is False
        assert "fn" not in h
    assert hs["buscar_leads"]["inputSchema"]["required"] == ["texto"]
    assert hs["ver_lead"]["inputSchema"]["required"] == ["lead_id"]
    assert "demo_agendada" in hs["pipeline"]["description"]


@pytest.mark.parametrize("args", [
    {},                                  # falta texto
    {"texto": "a"},                      # muy corto
    {"texto": "ab", "limite": 0},        # fuera de rango
    {"texto": "ab", "limite": 51},
    {"texto": "ab", "limite": "5"},      # tipo
    {"texto": 5},
    {"texto": "ab", "otro": 1},          # additionalProperties
])
def test_validar_argumentos_rechaza(args):
    schema = {h["name"]: h for h in mt.listar_herramientas()}["buscar_leads"]["inputSchema"]
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.validar_argumentos(schema, args)


def test_validar_argumentos_completa_defaults():
    schema = {h["name"]: h for h in mt.listar_herramientas()}["buscar_leads"]["inputSchema"]
    assert mt.validar_argumentos(schema, {"texto": "espiga"}) == {"texto": "espiga", "limite": 10}


def test_validar_enum_de_etapa():
    schema = {h["name"]: h for h in mt.listar_herramientas()}["pipeline"]["inputSchema"]
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.validar_argumentos(schema, {"etapa": "inventada"})


def test_ejecutar_herramienta_desconocida(sembrada, ana):
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "borrar_todo", {})


def test_ejecutar_registra_la_llamada(sembrada, ana):
    r = mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads", {"texto": "espiga"})
    assert r["isError"] is False
    conn = sqlite3.connect(sembrada)
    fila = conn.execute("SELECT user_id, user_name, herramienta, argumentos, ok, ms FROM mcp_llamadas").fetchone()
    conn.close()
    assert fila[0] == ana and fila[1] == "Ana" and fila[2] == "buscar_leads"
    assert json.loads(fila[3]) == {"texto": "espiga", "limite": 10}
    assert fila[4] == 1 and fila[5] >= 0


# ── buscar_leads ─────────────────────────────────────────────────────────────

def _texto(r):
    return json.loads(r["content"][0]["text"])


def test_buscar_por_nombre_sin_archivados(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads", {"texto": "espiga"}))
    assert [l["id"] for l in r["leads"]] == [1, 3]
    assert r["leads"][0] == {"id": 1, "nombre": "Panadería La Espiga", "etapa": "demo_1",
                             "telefono": "099111222", "email": "espiga@mail.com",
                             "ciudad": "Montevideo", "rubro": "panaderia", "origen": "meta",
                             "ultimo_evento_at": "2026-10-01 10:00:00"}


def test_buscar_por_mail_y_telefono(sembrada, ana):
    u = {"id": ana, "name": "Ana"}
    assert [l["id"] for l in _texto(mt.ejecutar(sembrada, u, "buscar_leads", {"texto": "BAR@mail"}))["leads"]] == [3]
    assert [l["id"] for l in _texto(mt.ejecutar(sembrada, u, "buscar_leads", {"texto": "099 333-444"}))["leads"]] == [2]


def test_buscar_respeta_limite_y_lo_dice(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads", {"texto": "espiga", "limite": 1}))
    assert len(r["leads"]) == 1 and r["hay_mas"] is True


def test_buscar_sin_resultados(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads", {"texto": "zzz"}))
    assert r == {"leads": [], "hay_mas": False}


# ── ver_lead ─────────────────────────────────────────────────────────────────

def test_ver_lead_trae_ficha_completa(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "ver_lead", {"lead_id": 1}))
    assert r["id"] == 1 and r["nombre"] == "Panadería La Espiga" and r["etapa"] == "demo_1"
    assert r["notas"] == "Quiere web con pedidos" and r["origen"] == "meta"
    assert r["responsables"] == {"encargado": "Ana", "mantenimiento": None, "cobros": None}
    assert r["relevamiento"]["lead_name"] == "Lucía" and r["relevamiento"]["needs"] == "pedidos online"
    assert [e["new_status"] for e in r["eventos"]] == ["demo_1", "demo_agendada"]
    assert r["llamadas"][0]["outcome"] == "contestó"
    # Nada interno.
    for interno in ("form_data", "meta_lead_id", "semaforo_origen", "demo_html_path", "encargado_id"):
        assert interno not in r


def test_ver_lead_sin_relevamiento(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "ver_lead", {"lead_id": 2}))
    assert r["relevamiento"] == {} and r["eventos"] == [] and r["llamadas"] == []
    assert r["responsables"]["encargado"] == "Juan"


def test_ver_lead_inexistente_es_error_de_herramienta(sembrada, ana):
    r = mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "ver_lead", {"lead_id": 999})
    assert r["isError"] is True
    assert "999" in r["content"][0]["text"]
    conn = sqlite3.connect(sembrada)
    assert conn.execute("SELECT ok, error FROM mcp_llamadas ORDER BY id DESC LIMIT 1").fetchone() == (0, "No existe el lead 999")
    conn.close()


# ── pipeline ─────────────────────────────────────────────────────────────────

def test_pipeline_agrupa_en_orden_y_excluye_archivados_y_clientes(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {}))
    assert [e["etapa"] for e in r["etapas"]] == ["demo_1", "presupuesto_enviado"]
    assert r["etapas"][0]["total"] == 1
    lead = r["etapas"][0]["leads"][0]
    assert lead["id"] == 1 and lead["encargado"] == "Ana" and lead["nombre"] == "Panadería La Espiga"
    assert isinstance(lead["dias_sin_movimiento"], int)
    assert r["total"] == 2 and r["truncado"] is False


def test_pipeline_filtra_por_etapa(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"etapa": "presupuesto_enviado"}))
    assert [e["etapa"] for e in r["etapas"]] == ["presupuesto_enviado"]
    assert r["etapas"][0]["leads"][0]["id"] == 2


def test_pipeline_filtra_por_responsable(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"responsable": "jua"}))
    assert r["total"] == 1 and r["etapas"][0]["leads"][0]["encargado"] == "Juan"


def test_pipeline_incluye_clientes(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"incluir_clientes": True}))
    assert [e["etapa"] for e in r["etapas"]] == ["demo_1", "presupuesto_enviado", "cerrado"]


def test_pipeline_etapa_de_cliente_sin_incluir_clientes_igual_la_trae(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"etapa": "cerrado"}))
    assert r["total"] == 1


def test_pipeline_vacio(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"etapa": "rechazo"}))
    assert r == {"etapas": [], "total": 0, "truncado": False}


def test_pipeline_trunca_a_200(sembrada, ana):
    conn = sqlite3.connect(sembrada)
    conn.executemany("INSERT INTO businesses (name, phone, crm_status) VALUES (?,?,?)",
                     [(f"L{i}", f"09{i:07d}", "follow_up_1") for i in range(205)])
    conn.commit()
    conn.close()
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"etapa": "follow_up_1"}))
    assert r["truncado"] is True and r["total"] == 200
    assert "Filtr" in r["aviso"]


# ── acotar argumentos y escapar comodines ────────────────────────────────────

def _schema(nombre):
    return {h["name"]: h for h in mt.listar_herramientas()}[nombre]["inputSchema"]


def _ids(db, usuario, texto, **extra):
    r = mt.ejecutar(db, usuario, "buscar_leads", {"texto": texto, **extra})
    return [l["id"] for l in _texto(r)["leads"]]


def test_buscar_porcentaje_no_es_comodin(sembrada, ana):
    assert _ids(sembrada, {"id": ana, "name": "Ana"}, "%%") == []


def test_buscar_solo_espacios_es_argumento_invalido(sembrada, ana):
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads", {"texto": "  "})


def test_buscar_guion_bajo_no_es_comodin(sembrada, ana):
    conn = sqlite3.connect(sembrada)
    conn.executemany("INSERT INTO businesses (id, name, email, crm_status) VALUES (?,?,?,?)",
                     [(6, "Zeta Uno", "xzy@mail.com", "sin_contactar"),
                      (7, "Zeta Dos", "x_y@mail.com", "sin_contactar")])
    conn.commit()
    conn.close()
    assert _ids(sembrada, {"id": ana, "name": "Ana"}, "x_y") == [7]


def test_buscar_telefono_con_parentesis_y_puntos(sembrada, ana):
    conn = sqlite3.connect(sembrada)
    conn.execute("UPDATE businesses SET phone = '(099) 333.444' WHERE id = 2")
    conn.commit()
    conn.close()
    assert _ids(sembrada, {"id": ana, "name": "Ana"}, "099333444") == [2]


def test_buscar_texto_de_mas_de_100_caracteres_es_invalido(sembrada, ana):
    with pytest.raises(mt.ArgumentosInvalidos) as e:
        mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads", {"texto": "a" * 101})
    assert "como mucho 100" in str(e.value)


def test_buscar_hay_mas_es_false_con_exactamente_limite_resultados(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads",
                           {"texto": "espiga", "limite": 2}))
    assert len(r["leads"]) == 2 and r["hay_mas"] is False


def test_pipeline_responsable_con_comodines_no_matchea_todo(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"responsable": "%%"}))
    assert r["total"] == 0


def test_toda_etapa_tiene_descripcion():
    assert set(mt.ETAPAS_DESCRIPCION) == set(mt._ETAPAS_TODAS)


def test_ejecutar_excepcion_generica_es_error_de_herramienta_y_no_loguea_valores(
        sembrada, ana, monkeypatch, caplog):
    def explota(db, a):
        raise RuntimeError("boom")

    monkeypatch.setitem(mt._POR_NOMBRE["ver_lead"], "fn", explota)
    r = mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "ver_lead", {"lead_id": 4242})
    assert r["isError"] is True
    assert r["content"][0]["text"] == "La herramienta falló por un error interno del CRM. Quedó registrado."
    conn = sqlite3.connect(sembrada)
    assert conn.execute("SELECT ok, error FROM mcp_llamadas ORDER BY id DESC LIMIT 1").fetchone() == (0, "excepción")
    conn.close()
    # Se loguean los nombres de los argumentos, no sus valores.
    assert "lead_id" in caplog.text and "4242" not in caplog.text


def test_si_registrar_falla_la_llamada_sale_igual(sembrada, ana, monkeypatch):
    conexion_real = mt._connect
    llamadas = []

    def connect_que_falla_al_registrar(db):
        # La primera conexión es la de la herramienta; la segunda, la del registro.
        llamadas.append(db)
        if len(llamadas) > 1:
            raise sqlite3.OperationalError("disco lleno")
        return conexion_real(db)

    monkeypatch.setattr(mt, "_connect", connect_que_falla_al_registrar)
    r = mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads", {"texto": "espiga"})
    assert r["isError"] is False and len(llamadas) == 2
    assert [l["id"] for l in _texto(r)["leads"]] == [1, 3]


def test_validar_rechaza_bool_como_integer_e_integer_como_boolean():
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.validar_argumentos(_schema("ver_lead"), {"lead_id": True})
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.validar_argumentos(_schema("pipeline"), {"incluir_clientes": 1})


def test_validar_null_explicito():
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.validar_argumentos(_schema("ver_lead"), {"lead_id": None})
    assert mt.validar_argumentos(_schema("buscar_leads"), {"texto": "espiga", "limite": None}) == {
        "texto": "espiga", "limite": 10}


def test_validar_guarda_el_texto_sin_espacios_de_los_bordes():
    assert mt.validar_argumentos(_schema("buscar_leads"), {"texto": "  espiga "})["texto"] == "espiga"


# ── POST /mcp ────────────────────────────────────────────────────────────────

WWW_AUTH = ('Bearer resource_metadata="https://crm.test/.well-known/oauth-protected-resource/mcp", '
            'scope="crm:leer"')


@pytest.fixture
def app(sembrada):
    a = dashboard.create_app(sembrada)
    a.config["TESTING"] = True
    return a


def _token_de(db, uid):
    import base64, hashlib, secrets
    verifier = secrets.token_urlsafe(40)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    code = ot.emitir_codigo(db, uid, "cli", "https://r/cb", challenge, "crm:leer", "https://crm.test/mcp")
    return ot.canjear_codigo(db, code, "cli", "https://r/cb", verifier)["access_token"]


@pytest.fixture
def claude(app, sembrada, ana):
    """Un test_client que manda el Bearer de Ana en cada request."""
    token = _token_de(sembrada, ana)
    c = app.test_client()
    def post(cuerpo, raw=None):
        return c.post("/mcp", data=raw if raw is not None else json.dumps(cuerpo),
                      content_type="application/json",
                      headers={"Authorization": f"Bearer {token}",
                               "Accept": "application/json, text/event-stream"})
    return post


def _rpc(metodo, params=None, id_=1):
    cuerpo = {"jsonrpc": "2.0", "method": metodo, "id": id_}
    if params is not None:
        cuerpo["params"] = params
    return cuerpo


def test_sin_bearer_es_401_con_www_authenticate(app):
    r = app.test_client().post("/mcp", json=_rpc("initialize"))
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == WWW_AUTH
    assert r.get_json()["error"] == "invalid_token"


def test_bearer_invalido_es_401(app):
    r = app.test_client().post("/mcp", json=_rpc("ping"), headers={"Authorization": "Bearer nada"})
    assert r.status_code == 401 and r.headers["WWW-Authenticate"] == WWW_AUTH


def test_get_y_delete_son_405(app):
    assert app.test_client().get("/mcp").status_code == 405
    assert app.test_client().delete("/mcp").status_code == 405


def test_vendedor_de_fidelidad_es_403(app, sembrada, juan):
    from services.fidelidad import ROL_VENDEDOR
    conn = sqlite3.connect(sembrada)
    # init_fidelidad ya crea el rol: se busca, no se inserta.
    rid = conn.execute("SELECT id FROM roles WHERE name = ?", (ROL_VENDEDOR,)).fetchone()[0]
    conn.execute("UPDATE users SET role_id = ? WHERE id = ?", (rid, juan))
    conn.commit()
    conn.close()
    token = _token_de(sembrada, juan)
    r = app.test_client().post("/mcp", json=_rpc("ping"), headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_initialize(claude):
    r = claude(_rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "claude", "version": "1"}}))
    assert r.status_code == 200
    res = r.get_json()["result"]
    assert res["protocolVersion"] == "2025-06-18"
    assert res["capabilities"] == {"tools": {}}
    assert res["serverInfo"]["name"] == "scalerics-crm"
    assert "instructions" in res


def test_initialize_version_desconocida_cae_a_la_nuestra(claude):
    r = claude(_rpc("initialize", {"protocolVersion": "2099-01-01", "capabilities": {},
                                   "clientInfo": {"name": "x", "version": "1"}}))
    assert r.get_json()["result"]["protocolVersion"] == "2025-06-18"


def test_notificacion_es_202_sin_cuerpo(claude):
    r = claude({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202 and r.get_data() == b""


def test_ping(claude):
    r = claude(_rpc("ping", id_="p1"))
    assert r.get_json() == {"jsonrpc": "2.0", "id": "p1", "result": {}}


def test_tools_list(claude):
    r = claude(_rpc("tools/list"))
    nombres = [t["name"] for t in r.get_json()["result"]["tools"]]
    assert nombres == ["buscar_leads", "ver_lead", "pipeline"]


def test_tools_call(claude):
    r = claude(_rpc("tools/call", {"name": "ver_lead", "arguments": {"lead_id": 1}}))
    res = r.get_json()["result"]
    assert res["isError"] is False
    assert json.loads(res["content"][0]["text"])["nombre"] == "Panadería La Espiga"


def test_tools_call_argumentos_invalidos_es_32602(claude):
    r = claude(_rpc("tools/call", {"name": "ver_lead", "arguments": {"lead_id": "1"}}))
    assert r.status_code == 200
    err = r.get_json()["error"]
    assert err["code"] == -32602 and "lead_id" in err["message"]


def test_tools_call_herramienta_desconocida_es_32602(claude):
    r = claude(_rpc("tools/call", {"name": "borrar", "arguments": {}}))
    assert r.get_json()["error"]["code"] == -32602


def test_metodo_desconocido_es_32601(claude):
    r = claude(_rpc("resources/list"))
    assert r.get_json()["error"]["code"] == -32601


def test_batch_es_400(claude):
    r = claude(None, raw=json.dumps([_rpc("ping")]))
    assert r.status_code == 400 and r.get_json()["error"]["code"] == -32600


def test_cuerpo_no_json_es_400(claude):
    r = claude(None, raw="{no es json")
    assert r.status_code == 400 and r.get_json()["error"]["code"] == -32700


def test_cada_call_queda_registrada_con_el_usuario(claude, sembrada, ana):
    claude(_rpc("tools/call", {"name": "buscar_leads", "arguments": {"texto": "norte"}}))
    conn = sqlite3.connect(sembrada)
    fila = conn.execute("SELECT user_id, user_name, herramienta FROM mcp_llamadas ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert fila == (ana, "Ana", "buscar_leads")
