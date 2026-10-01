"""Reunión hecha en el pipeline de Fidelidad → Proceso de venta (Juan, 1/10).

Cuando un comercio pasa a "Reunión hecha", entra solo al tablero de Clientes de
Notion ("Proceso de venta") en "Esperando Confirmación Presupuesto". Si vuelve a
pasar, se mueve la misma ficha. Si Notion falla, el cambio de etapa queda hecho
y la pantalla avisa.

Notion no se llama nunca: `requests` se reemplaza acá.
"""

import pytest

from database import get_notion_client_by_page, init_db
from services import fidelidad as fid
from services import notion_service as ns

ESQUEMA = {"properties": {
    "Name": {"id": "title", "type": "title"},
    "": {"id": "st%3A1", "type": "status"},
    "Descripcion": {"id": "desc", "type": "rich_text"},
}}


class _Resp:
    def __init__(self, status, datos):
        self.status_code, self._d, self.text = status, datos, str(datos)

    def json(self):
        return self._d


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    monkeypatch.setenv("NOTION_CLIENTS_DATA_SOURCE_ID", "ds-clientes")
    ruta = str(tmp_path / "pv.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def notion(monkeypatch):
    pedidos = []

    def get(url, headers=None, timeout=None):
        pedidos.append(("GET", url, None))
        if url.endswith("/data_sources/ds-clientes"):
            return _Resp(200, ESQUEMA)
        return _Resp(200, {"properties": {"": {"id": "st%3A1", "type": "status", "status": {"name": "Demo Agendada"}}}})

    def post(url, headers=None, json=None, timeout=None):
        pedidos.append(("POST", url, json))
        return _Resp(200, {"id": "pagina-1"})

    def patch(url, headers=None, json=None, timeout=None):
        pedidos.append(("PATCH", url, json))
        return _Resp(200, {})

    monkeypatch.setattr(ns.requests, "get", get)
    monkeypatch.setattr(ns.requests, "post", post)
    monkeypatch.setattr(ns.requests, "patch", patch)
    return pedidos


def _p(db, **extra):
    datos = {"nombre": "Rodelú", "barrio": "Pocitos", "tipo": "Pizzería", "telefono": "2708 1234",
             "contacto": "Pablo", "contacto_tel": "099 111 222"}
    datos.update(extra)
    pid, _ = fid.crear_prospecto(db, datos)
    return pid


def test_reunion_hecha_crea_la_ficha_en_esperando_confirmacion(db, notion):
    pid = _p(db)
    p, err = fid.mover_estado(db, pid, "reunion_hecha", "Lucas")
    assert err is None and p["estado"] == "reunion_hecha"
    assert p["proceso_venta"] == {"ok": True, "error": None, "estado": "Esperando Confirmación Presupuesto"}
    metodo, url, cuerpo = notion[-1]
    assert (metodo, url) == ("POST", f"{ns.API}/pages")
    assert cuerpo["parent"] == {"type": "data_source_id", "data_source_id": "ds-clientes"}
    assert cuerpo["properties"]["Name"]["title"][0]["text"]["content"] == "Rodelú"
    assert cuerpo["properties"]["st%3A1"] == {"status": {"name": "Esperando Confirmación Presupuesto"}}
    desc = cuerpo["properties"]["Descripcion"]["rich_text"][0]["text"]["content"]
    assert "Scalerics Fidelidad · Pizzería" in desc and "Dueño: Pablo · 099 111 222" in desc
    # Queda en el espejo del CRM y el comercio guarda su ficha.
    assert get_notion_client_by_page(db, "pagina-1")["status"] == "Esperando Confirmación Presupuesto"
    assert fid.get_prospecto(db, pid)["notion_page_id"] == "pagina-1"


def test_si_vuelve_a_pasar_mueve_la_misma_ficha(db, notion):
    pid = _p(db)
    fid.mover_estado(db, pid, "reunion_hecha", "Lucas")
    fid.mover_estado(db, pid, "reunion_agendada", "Lucas")
    # En Notion la movieron a otra columna mientras tanto.
    ns.set_notion_client_status(db, "pagina-1", "Demo Agendada")
    notion.clear()
    p, _ = fid.mover_estado(db, pid, "reunion_hecha", "Lucas")
    assert p["proceso_venta"]["ok"]
    assert not any(m == "POST" for m, _, _ in notion)                 # no crea otra
    assert notion[-1][0] == "PATCH" and notion[-1][1].endswith("/pages/pagina-1")


def test_otras_etapas_no_tocan_notion(db, notion):
    pid = _p(db)
    for estado in ("contactado", "reunion_agendada", "piloto", "cerrado"):
        p, _ = fid.mover_estado(db, pid, estado, "Lucas")
        assert "proceso_venta" not in p
    assert notion == []


def test_si_notion_falla_la_etapa_queda_y_avisa(db, monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN")
    pid = _p(db)
    p, err = fid.mover_estado(db, pid, "reunion_hecha", "Lucas")
    assert err is None and p["estado"] == "reunion_hecha"
    assert p["proceso_venta"]["ok"] is False and "NOTION_TOKEN" in p["proceso_venta"]["error"]


def test_lo_piensa_despues_de_la_reunion_tambien_pasa(db, notion):
    pid = _p(db)
    fid.mover_estado(db, pid, "reunion_agendada", "Lucas", fecha_reunion="2030-01-10 11:00")
    p, err = fid.registrar_llamada(db, pid, "lo_piensa", "Lucas", fecha="2030-01-15 11:00")
    assert err is None and p["estado"] == "reunion_hecha" and p["notion_page_id"] == "pagina-1"
