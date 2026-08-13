"""Tests de presupuestos.

El presupuesto tenia dos mitades desconectadas: la tabla `budgets` guardaba monto
y estado, pero su ruta de generacion quedaba TAPADA por otra en leads_bp con la
misma URL (las reglas solo diferian en el nombre de la variable, biz_id vs
client_id, asi que un chequeo por string no la detectaba). Ganaba la de leads_bp,
que solo creaba un adjunto HTML sin monto ni estado.

Consecuencia visible en la base productiva: 3 presupuestos, todos en 'draft', y la
meta 'presupuestos_enviados' sin avanzar nunca — porque mark-sent tambien vivia en
el blueprint inalcanzable.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (connect, create_task, create_user, get_budget_for_client,
                      get_business, get_task_by_id, init_db, insert_business)

_RESPUESTA_IA = {
    "hero_title": "Sitio web para Ana",
    "hero_description": "Una web moderna",
    "intro": "Propuesta",
    "dev_price": 1500,
    "monthly_price": 90,
    "payment_terms": "50% adelanto",
    "sections": [{"title": "Objetivos", "items": ["Vender más"], "text": "Detalle"}],
}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ALLOW_INSECURE_DEV_KEY", "1")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    db = str(tmp_path / "b.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


@pytest.fixture
def cli(app):
    db = app.config["_DB"]
    uid = create_user(db, name="Jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    c._uid = uid
    return c


def _mock_ia(payload=None):
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(payload or _RESPUESTA_IA))]
    cliente = MagicMock()
    cliente.messages.create.return_value = msg
    return cliente


def _lead(db):
    return insert_business(db, {"name": "Ana", "phone": "+59899000001", "city": "Montevideo"})


def _adjuntos(db, lead_id):
    conn = connect(db)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM lead_attachments WHERE lead_id=? AND section='budget'", (lead_id,))]
    finally:
        conn.close()


# ── la ruta tapada ───────────────────────────────────────────────────────────

def test_la_ruta_de_generar_llega_al_handler_correcto(app):
    """Dos blueprints declaraban POST /api/leads/<id>/budget/generate. Ganaba
    leads_bp y dejaba muerta la unica que guarda monto y estado."""
    endpoint = app.url_map.bind("x").match("/api/leads/1/budget/generate", method="POST")[0]
    assert endpoint == "budgets.api_generate_budget"


def test_no_quedan_urls_duplicadas_entre_blueprints(app):
    """Comparando la url que matchea, no el string de la regla: las dos rutas
    diferian solo en el nombre de la variable y por eso pasaban desapercibidas."""
    import re
    vistas = {}
    for r in app.url_map.iter_rules():
        normalizada = re.sub(r"<[^:>]+:[^>]+>", "<var>", r.rule)
        normalizada = re.sub(r"<[^>]+>", "<var>", normalizada)
        for m in r.methods - {"HEAD", "OPTIONS"}:
            clave = (normalizada, m)
            assert clave not in vistas, f"{clave} duplicada: {vistas.get(clave)} y {r.endpoint}"
            vistas[clave] = r.endpoint


# ── generacion unificada ─────────────────────────────────────────────────────

def test_generar_guarda_el_estructurado_y_el_documento(app, cli):
    db = app.config["_DB"]
    lid = _lead(db)
    with patch("anthropic.Anthropic", return_value=_mock_ia()):
        r = cli.post(f"/api/leads/{lid}/budget/generate", json={"requirements": "web"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] and d["budget_id"] and d["attachment_id"]

    # el dato estructurado, que es el que permite medir
    budget = get_budget_for_client(db, lid)
    assert budget["total_amount"] == 1500
    assert budget["status"] == "draft"
    # y el documento para el PDF
    adj = _adjuntos(db, lid)
    assert len(adj) == 1
    assert adj[0]["mime_type"] == "text/html"
    assert b"Sitio web para Ana" in adj[0]["file_data"]


def test_regenerar_reemplaza_el_documento_no_acumula(app, cli):
    db = app.config["_DB"]
    lid = _lead(db)
    with patch("anthropic.Anthropic", return_value=_mock_ia()):
        cli.post(f"/api/leads/{lid}/budget/generate", json={"requirements": "web"})
        cli.post(f"/api/leads/{lid}/budget/generate", json={"requirements": "otra cosa"})
    assert len(_adjuntos(db, lid)) == 1


def test_acepta_instructions_ademas_de_requirements(app, cli):
    """Los dos accesos del frontend mandan parametros distintos a la misma URL."""
    db = app.config["_DB"]
    lid = _lead(db)
    ia = _mock_ia()
    with patch("anthropic.Anthropic", return_value=ia):
        r = cli.post(f"/api/leads/{lid}/budget/generate", json={"instructions": "sumale tienda"})
    assert r.get_json()["ok"]
    assert "sumale tienda" in ia.messages.create.call_args.kwargs["messages"][0]["content"]


def test_el_documento_escapa_el_html_del_modelo(app, cli):
    """El contenido lo genera un modelo a partir de texto de leads, que viene de
    scraping y de webhooks publicos."""
    db = app.config["_DB"]
    lid = _lead(db)
    malicioso = dict(_RESPUESTA_IA, hero_title='<script>alert(1)</script>')
    with patch("anthropic.Anthropic", return_value=_mock_ia(malicioso)):
        cli.post(f"/api/leads/{lid}/budget/generate", json={})
    html = _adjuntos(db, lid)[0]["file_data"].decode("utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_la_vista_previa_se_sirve_en_sandbox(app, cli):
    db = app.config["_DB"]
    lid = _lead(db)
    with patch("anthropic.Anthropic", return_value=_mock_ia()):
        cli.post(f"/api/leads/{lid}/budget/generate", json={})
    r = cli.get(f"/api/leads/{lid}/budget/preview")
    assert r.status_code == 200
    assert r.headers["Content-Security-Policy"] == "sandbox"
    assert r.headers["X-Content-Type-Options"] == "nosniff"


# ── marcar enviado ───────────────────────────────────────────────────────────

def test_marcar_enviado_mueve_el_lead_y_avanza_la_meta(app, cli):
    db = app.config["_DB"]
    lid = _lead(db)
    tid = create_task(db, title="Meta", assignee_id=cli._uid, created_by_id=cli._uid,
                      goal=5, goal_type="presupuestos_enviados")
    with patch("anthropic.Anthropic", return_value=_mock_ia()):
        bid = cli.post(f"/api/leads/{lid}/budget/generate", json={}).get_json()["budget_id"]

    r = cli.post(f"/api/budgets/{bid}/mark-sent")
    assert r.status_code == 200

    assert get_budget_for_client(db, lid)["status"] == "sent"
    assert get_business(db, lid)["crm_status"] == "presupuesto_enviado"
    assert get_task_by_id(db, tid)["progress"] == 1


def test_marcar_enviado_dos_veces_no_suma_dos_veces(app, cli):
    db = app.config["_DB"]
    lid = _lead(db)
    tid = create_task(db, title="Meta", assignee_id=cli._uid, created_by_id=cli._uid,
                      goal=5, goal_type="presupuestos_enviados")
    with patch("anthropic.Anthropic", return_value=_mock_ia()):
        bid = cli.post(f"/api/leads/{lid}/budget/generate", json={}).get_json()["budget_id"]

    cli.post(f"/api/budgets/{bid}/mark-sent")
    r2 = cli.post(f"/api/budgets/{bid}/mark-sent")
    assert r2.get_json().get("ya_estaba") is True
    assert get_task_by_id(db, tid)["progress"] == 1


def test_marcar_enviado_no_pisa_un_estado_mas_avanzado(app, cli):
    """Si el lead ya esta en negociacion o cerrado, marcar el presupuesto no lo
    hace retroceder."""
    db = app.config["_DB"]
    lid = _lead(db)
    with patch("anthropic.Anthropic", return_value=_mock_ia()):
        bid = cli.post(f"/api/leads/{lid}/budget/generate", json={}).get_json()["budget_id"]
    from database import update_business
    update_business(db, lid, crm_status="cliente_cerrado")

    cli.post(f"/api/budgets/{bid}/mark-sent")
    assert get_business(db, lid)["crm_status"] == "cliente_cerrado"


def test_marcar_enviado_de_un_presupuesto_inexistente_da_404(app, cli):
    assert cli.post("/api/budgets/99999/mark-sent").status_code == 404
