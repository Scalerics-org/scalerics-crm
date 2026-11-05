"""El CRM lee más de una cuenta publicitaria (Juan, 3/11: la de Argentina,
que paga Ciro). Ver meta_config.cuentas_publicitarias."""

from datetime import date

import pytest

import meta_config
from services import alertas_meta
from services import meta_anuncios as m

ARGENTINA = "act_2220090788887344"


def test_la_principal_primero_y_despues_la_de_argentina(monkeypatch):
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "act_1")
    assert meta_config.cuentas_publicitarias() == ["act_1", ARGENTINA]


def test_sin_repetir_y_con_act_aunque_no_lo_traiga(monkeypatch):
    monkeypatch.setenv("META_AD_ACCOUNT_ID", f"1, {ARGENTINA}")
    assert meta_config.cuentas_publicitarias() == ["act_1", ARGENTINA]


def test_anuncios_suma_las_dos_cuentas(monkeypatch):
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "act_1")
    filas = m._de_todas_las_cuentas(lambda cuenta: [{"id": cuenta}])
    assert [f["id"] for f in filas] == ["act_1", ARGENTINA]


def test_si_falla_la_de_argentina_uruguay_sigue(monkeypatch):
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "act_1")

    def traer(cuenta):
        if cuenta == ARGENTINA:
            raise m._ErrorDeMeta("sin permiso")
        return [{"id": cuenta}]
    assert [f["id"] for f in m._de_todas_las_cuentas(traer)] == ["act_1"]


def test_si_falla_la_principal_falla_todo(monkeypatch):
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "act_1")

    def traer(cuenta):
        raise m._ErrorDeMeta("token vencido")
    with pytest.raises(m._ErrorDeMeta):
        m._de_todas_las_cuentas(traer)


def test_las_alertas_miran_las_dos_cuentas(monkeypatch):
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "act_1")
    pedidas = []

    def get(ruta, **params):
        pedidas.append(ruta)
        if ruta.startswith(ARGENTINA):
            raise alertas_meta.ErrorDeMeta("sin permiso")
        return {"data": [{"campaign_name": "Uruguay"}]}
    monkeypatch.setattr(alertas_meta, "_get", get)
    filas = alertas_meta._insights("campaign", date(2026, 11, 1), date(2026, 11, 2), "spend")
    assert pedidas == ["act_1/insights", f"{ARGENTINA}/insights"]
    assert filas == [{"campaign_name": "Uruguay"}]
