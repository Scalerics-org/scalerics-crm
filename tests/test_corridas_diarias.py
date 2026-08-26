"""La marca de ultima corrida: que un deploy no dispare una tanda.

Los hilos de las dos campanas arrancan N segundos despues de CADA boot, y Fly
reinicia la maquina en cada deploy. El 26/8/2026 hubo cinco releases en 42
minutos y discovery salio en dos tandas (6 y 24) el mismo dia.

Ahi el tope rodante de 24 horas hizo bien su trabajo: la segunda tanda mando 24
y no 30 porque descontó lo ya enviado. Pero ese tope es lo UNICO que separa un
deploy de una tanda repetida, y es poco para algo que le manda correo a gente
real. Esto es el segundo guard, independiente del primero: si uno falla, el
otro tapa.
"""

import sqlite3

import pytest

from database import init_db
from services.corridas import marcar_corrida, puede_correr, ultima_corrida


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


def _atrasar(db_path, nombre, horas):
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE corridas SET ultima = datetime('now', ?) WHERE nombre = ?",
                 (f"-{horas} hours", nombre))
    conn.commit()
    conn.close()


def test_la_primera_vez_puede_correr(db):
    assert puede_correr(db, "discovery") is True


def test_despues_de_marcar_no_puede_correr_de_nuevo(db):
    """El caso que motiva todo: deploy, tanda, deploy, y la segunda no sale."""
    marcar_corrida(db, "discovery")
    assert puede_correr(db, "discovery") is False


def test_pasadas_las_horas_vuelve_a_poder(db):
    marcar_corrida(db, "discovery")
    _atrasar(db, "discovery", 21)
    assert puede_correr(db, "discovery") is True


def test_justo_antes_del_plazo_todavia_no(db):
    marcar_corrida(db, "discovery")
    _atrasar(db, "discovery", 19)
    assert puede_correr(db, "discovery") is False


def test_el_plazo_por_defecto_es_20_horas(db):
    """20 y no 24 a proposito: con 24, si la tanda de hoy salio 18:11 y manana
    la maquina arranca 17:00, el job no corre y se pierde el dia entero."""
    marcar_corrida(db, "discovery")
    _atrasar(db, "discovery", 20.5)
    assert puede_correr(db, "discovery") is True


def test_cada_job_lleva_su_propia_marca(db):
    """Meta y discovery corren por separado: que uno haya corrido no puede
    frenar al otro."""
    marcar_corrida(db, "discovery")
    assert puede_correr(db, "meta") is True
    assert puede_correr(db, "discovery") is False


def test_marcar_dos_veces_actualiza_en_vez_de_duplicar(db):
    marcar_corrida(db, "discovery")
    primera = ultima_corrida(db, "discovery")
    marcar_corrida(db, "discovery")
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM corridas WHERE nombre='discovery'").fetchone()[0]
    conn.close()
    assert n == 1
    assert ultima_corrida(db, "discovery") >= primera


def test_se_puede_pedir_otro_plazo(db):
    marcar_corrida(db, "discovery")
    _atrasar(db, "discovery", 2)
    assert puede_correr(db, "discovery", cada_horas=1) is True
    assert puede_correr(db, "discovery", cada_horas=3) is False


def test_sin_marca_previa_ultima_corrida_es_none(db):
    assert ultima_corrida(db, "discovery") is None


def test_un_error_leyendo_la_marca_deja_correr(db, monkeypatch):
    """Ante la duda, correr: el tope rodante de 24 horas sigue estando detras y
    es el que evita el dano. Fallar cerrado aca dejaria la campana muda por un
    problema de la tabla de marcas, que es peor."""
    import services.corridas as c

    def _explota(*a, **k):
        raise sqlite3.OperationalError("no such table: corridas")

    monkeypatch.setattr(c, "_conn", _explota)
    assert puede_correr(db, "discovery") is True


# ─── El caso real: dos deploys seguidos ───────────────────────────────────────

from unittest.mock import patch  # noqa: E402

from database import insert_business  # noqa: E402


def _comercio(db, n):
    return insert_business(db, {
        "name": f"Comercio {n}", "phone": f"09900{n:04}", "email": f"info@c{n}.uy",
        "website": f"https://c{n}.uy", "source": "discovery",
        "maps_url": f"https://maps.google.com/?cid={n}"})


def test_dos_arranques_seguidos_mandan_una_sola_tanda(db):
    """Lo que paso el 26/8: cinco releases en 42 minutos. Antes cada arranque
    disparaba una tanda y solo el tope rodante la frenaba a medias."""
    from services.discovery_emails import tanda_diaria

    for n in range(60):
        _comercio(db, n)

    with patch("services.discovery_emails.send_discovery_email", return_value="ok"):
        primera = tanda_diaria(db, "https://crm")
        segunda = tanda_diaria(db, "https://crm")

    assert primera["enviados"] > 0, "la primera tanda tiene que salir"
    assert segunda is None, "la segunda no tiene que ni intentarlo"


def test_pasado_el_plazo_la_tanda_vuelve_a_salir(db):
    from services.discovery_emails import tanda_diaria

    for n in range(60):
        _comercio(db, n)

    with patch("services.discovery_emails.send_discovery_email", return_value="ok"):
        tanda_diaria(db, "https://crm")
        _atrasar(db, "discovery", 21)
        # Y se corre el reloj de los envios, que si no lo frena el tope diario.
        conn = sqlite3.connect(db)
        conn.execute("UPDATE discovery_reminders SET sent_at = datetime('now','-2 days')")
        conn.commit()
        conn.close()
        segunda = tanda_diaria(db, "https://crm")

    assert segunda is not None and segunda["enviados"] > 0


def test_meta_tambien_se_saltea_el_segundo_arranque(db):
    """Las dos campanas comparten el problema y comparten el guard, pero cada
    una con su propia marca."""
    from services.meta_reminders import tanda_diaria as tanda_meta

    for n in range(20):
        insert_business(db, {"name": f"Lead {n}", "phone": f"098000{n:03}",
                             "email": f"lead{n}@x.uy", "source": "meta",
                             "scraped_at": "2020-01-01 00:00:00",
                             "maps_url": f"https://maps.google.com/?cid=m{n}"})

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok"):
        primera = tanda_meta(db, "https://crm")
        segunda = tanda_meta(db, "https://crm")

    assert primera is not None and primera["enviados"] > 0
    assert segunda is None
