"""El sync se dispara solo cuando alguien abre el CRM."""

import dashboard


def test_no_dispara_sin_credenciales(monkeypatch):
    monkeypatch.delenv("GMAIL_REFRESH_TOKEN", raising=False)
    dashboard._calendly_sync_state["at"] = 0.0
    dashboard._maybe_sync_calendly("x.db")
    assert dashboard._calendly_sync_state["at"] == 0.0


def test_el_throttle_evita_pegarle_a_google_en_cada_request(monkeypatch):
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "x")
    llamadas = []
    monkeypatch.setattr(dashboard.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda s: llamadas.append(1)})())
    dashboard._calendly_sync_state["at"] = 0.0

    dashboard._maybe_sync_calendly("x.db")
    dashboard._maybe_sync_calendly("x.db")   # misma ventana
    dashboard._maybe_sync_calendly("x.db")

    assert len(llamadas) == 1


def test_vuelve_a_correr_pasada_la_ventana(monkeypatch):
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "x")
    llamadas = []
    monkeypatch.setattr(dashboard.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda s: llamadas.append(1)})())

    dashboard._calendly_sync_state["at"] = 0.0
    dashboard._maybe_sync_calendly("x.db")
    # simulamos que pasó el intervalo
    dashboard._calendly_sync_state["at"] -= dashboard.CALENDLY_SYNC_EVERY + 1
    dashboard._maybe_sync_calendly("x.db")

    assert len(llamadas) == 2
