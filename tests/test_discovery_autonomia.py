"""La alarma de cola baja de discovery.

Existe por un hueco real: entre el 23 y el 26 de agosto de 2026 la campana no
mando un solo mail porque se quedo sin direcciones, y nos enteramos tres dias
despues mirando el panel de Resend. El enviador hacia lo correcto —una tanda
por dia, cero candidatos— y no habia forma de saberlo sin ir a buscarlo.

La autonomia se mide SOLO con los que nunca recibieron nada. Los seguimientos
no cuentan: son finitos y se acaban solos, asi que una cola con 200
seguimientos pendientes y ningun comercio nuevo tiene autonomia cero, que es
exactamente lo que se quiere saber.
"""

import pytest

from database import init_db, insert_business
from services.discovery_emails import (_TOPE_DIARIO, dias_de_autonomia,
                                       registrar_envio)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


def _comercio(db, n, **extra):
    datos = {"name": f"Comercio {n}", "phone": f"09900{n:04}",
             "website": f"https://c{n}.uy", "email": f"info@c{n}.uy",
             "source": "discovery", "maps_url": f"https://maps.google.com/?cid={n}"}
    datos.update(extra)
    return insert_business(db, datos)


def test_sin_nadie_en_la_cola_la_autonomia_es_cero(db):
    assert dias_de_autonomia(db) == 0


def test_un_tope_diario_exacto_es_un_dia(db):
    for n in range(_TOPE_DIARIO):
        _comercio(db, n)
    assert dias_de_autonomia(db) == 1


def test_dos_topes_son_dos_dias(db):
    for n in range(_TOPE_DIARIO * 2):
        _comercio(db, n)
    assert dias_de_autonomia(db) == 2


def test_se_redondea_para_abajo(db):
    """Media tanda no es medio dia de tranquilidad: es que manana te quedas
    sin nada. Redondear para arriba seria mentirse."""
    for n in range(_TOPE_DIARIO + 1):
        _comercio(db, n)
    assert dias_de_autonomia(db) == 1


def test_los_ya_contactados_no_cuentan(db):
    """Un comercio que ya recibio el primero no vuelve a la cola de nuevos: su
    seguimiento es finito y no da autonomia."""
    ids = [_comercio(db, n) for n in range(_TOPE_DIARIO * 2)]
    for bid in ids[:_TOPE_DIARIO]:
        registrar_envio(db, bid, 1)
    assert dias_de_autonomia(db) == 1


def test_los_que_no_tienen_mail_no_cuentan(db):
    """Estan en el padron pero no se les puede escribir: contarlos seria decir
    que hay autonomia que no existe."""
    for n in range(_TOPE_DIARIO):
        _comercio(db, n, email=None)
    assert dias_de_autonomia(db) == 0


def test_las_otras_cohortes_no_cuentan(db):
    for n in range(_TOPE_DIARIO):
        _comercio(db, n, source="meta")
    for n in range(100, 100 + _TOPE_DIARIO):
        _comercio(db, n, source=None)
    assert dias_de_autonomia(db) == 0


def test_dos_comercios_con_la_misma_direccion_cuentan_una_vez(db):
    """La tanda deduplica por direccion, asi que contar filas inflaria la
    autonomia con mails que nunca van a salir."""
    for n in range(_TOPE_DIARIO * 2):
        _comercio(db, n, email="mismo@sitio.uy")
    # Una sola direccion distinta: ni siquiera una tanda entera.
    assert dias_de_autonomia(db) == 0


# ─── El aviso ─────────────────────────────────────────────────────────────────

from unittest.mock import patch  # noqa: E402

from services.discovery_emails import (_DIAS_PARA_AVISAR,
                                       avisar_si_la_cola_esta_baja)


def _llenar(db, dias):
    for n in range(_TOPE_DIARIO * dias):
        _comercio(db, n)


def test_con_cola_de_sobra_no_avisa(db, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "juan@scalerics.com")
    _llenar(db, _DIAS_PARA_AVISAR + 3)
    with patch("services.discovery_emails.send_discovery_queue_alert") as avisar:
        assert avisar_si_la_cola_esta_baja(db) is False
        avisar.assert_not_called()


def test_justo_en_el_umbral_todavia_no_avisa(db, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "juan@scalerics.com")
    _llenar(db, _DIAS_PARA_AVISAR)
    with patch("services.discovery_emails.send_discovery_queue_alert") as avisar:
        assert avisar_si_la_cola_esta_baja(db) is False
        avisar.assert_not_called()


def test_por_debajo_del_umbral_avisa(db, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "juan@scalerics.com")
    _llenar(db, _DIAS_PARA_AVISAR - 1)
    with patch("services.discovery_emails.send_discovery_queue_alert") as avisar:
        assert avisar_si_la_cola_esta_baja(db) is True
        avisar.assert_called_once()
        assert avisar.call_args.args[0] == "juan@scalerics.com"


def test_con_la_cola_vacia_tambien_avisa(db, monkeypatch):
    """El caso que motivo todo esto: cero candidatos, tres dias en silencio."""
    monkeypatch.setenv("ADMIN_EMAIL", "juan@scalerics.com")
    with patch("services.discovery_emails.send_discovery_queue_alert") as avisar:
        assert avisar_si_la_cola_esta_baja(db) is True
        assert avisar.call_args.kwargs["dias"] == 0


def test_el_aviso_lleva_los_numeros(db, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "juan@scalerics.com")
    _llenar(db, 2)
    with patch("services.discovery_emails.send_discovery_queue_alert") as avisar:
        avisar_si_la_cola_esta_baja(db)
    kw = avisar.call_args.kwargs
    assert kw["dias"] == 2
    assert kw["pendientes"] == _TOPE_DIARIO * 2
    assert kw["tope"] == _TOPE_DIARIO


def test_sin_ADMIN_EMAIL_no_intenta_mandar(db, monkeypatch):
    """Sin destinatario no hay aviso posible; que no explote la tanda."""
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    with patch("services.discovery_emails.send_discovery_queue_alert") as avisar:
        assert avisar_si_la_cola_esta_baja(db) is False
        avisar.assert_not_called()


def test_si_el_aviso_falla_no_tumba_la_tanda(db, monkeypatch):
    """El aviso es lo menos importante que hace este job: que reviente no puede
    impedir que salgan los mails."""
    monkeypatch.setenv("ADMIN_EMAIL", "juan@scalerics.com")
    with patch("services.discovery_emails.send_discovery_queue_alert",
               side_effect=RuntimeError("resend caido")):
        assert avisar_si_la_cola_esta_baja(db) is False
