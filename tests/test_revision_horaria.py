"""Las campanas revisan cada hora si les toca, y una revision vacia no gasta el dia.

Paso de verdad: discovery no mando nada desde el 13/9/2026 22:21 hora UY. La
tanda del 14/9 salio a las 01:21 UTC con 50 envios. Un deploy a las 22:12 UTC
—casi 21 horas despues— paso la marca de 20 horas, MARCO la corrida, se encontro
el cupo de 24 horas lleno, no mando nada y el hilo se durmio 24 horas. El dia
se perdio, y la marca nueva ademas bloqueaba los deploys siguientes. En los
datos: 50/dia el 13 y el 14, y 0 el 28/8, 1/9, 4/9, 7/9, 10/9, 12/9 y 15/9.
Meta tenia huecos casi los mismos dias: ya miraba el cupo antes de marcar, pero
el hilo tambien dormia 24 horas despues del intento vacio.

Los dos guards (marca de 20 horas y tope rodante de 24) no cambian. Lo que
cambia es que el hilo vuelve a mirar cada hora, y que una revision que no va a
mandar no toca nada: ni la marca, ni Gmail, ni el aviso de cola baja al admin.
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from database import init_db, insert_business
from services.corridas import REVISAR_CADA_S, siguiente_revision, ultima_corrida


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


def _hace(horas):
    return (datetime.now(timezone.utc) - timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M:%S")


def _comercio(db, n):
    return insert_business(db, {
        "name": f"Comercio {n}", "phone": f"09900{n:04}", "email": f"info@c{n}.uy",
        "website": f"https://c{n}.uy", "source": "discovery",
        "maps_url": f"https://maps.google.com/?cid={n}"})


def _tanda_de_discovery_hace(db, horas, cuantos=50):
    """La tanda anterior tal cual la deja el codigo: la marca en `corridas` y
    una fila por envio en `discovery_reminders`, todas a la misma hora."""
    ids = [_comercio(db, 1000 + i) for i in range(cuantos)]
    conn = sqlite3.connect(db)
    for i, bid in enumerate(ids):
        conn.execute(
            "INSERT INTO discovery_reminders (business_id, numero, token, sent_at) "
            "VALUES (?, 1, ?, ?)", (bid, f"tok-viejo-{i}", _hace(horas)))
    conn.execute("INSERT INTO corridas (nombre, ultima) VALUES ('discovery', ?)", (_hace(horas),))
    conn.commit()
    conn.close()


def _correr_el_reloj(db, horas):
    """Pasan `horas` para todo lo guardado: la marca y los envios."""
    conn = sqlite3.connect(db)
    conn.execute("UPDATE corridas SET ultima = datetime(ultima, ?)", (f"-{horas} hours",))
    conn.execute("UPDATE discovery_reminders SET sent_at = datetime(sent_at, ?)", (f"-{horas} hours",))
    conn.commit()
    conn.close()


# ─── Discovery: el caso del 14/9 ─────────────────────────────────────────────

def test_discovery_con_el_cupo_lleno_no_marca_la_corrida_ni_manda(db):
    """21 horas despues de una tanda de 50: la marca deja pasar, el cupo no."""
    from services.discovery_emails import tanda_diaria

    _tanda_de_discovery_hace(db, 21)
    for n in range(60):
        _comercio(db, n)
    marca_antes = ultima_corrida(db, "discovery")

    with patch("services.discovery_emails.send_discovery_email", return_value="ok") as envio, \
         patch("services.discovery_emails.sincronizar_desde_gmail", return_value={}), \
         patch("services.discovery_emails.avisar_si_la_cola_esta_baja", return_value=False):
        res = tanda_diaria(db, "https://crm")

    assert res is None, "una revision que no puede mandar nada no es una tanda"
    assert envio.call_count == 0
    assert ultima_corrida(db, "discovery") == marca_antes, (
        "no se marca: si no, el dia se pierde y los deploys siguientes quedan bloqueados")


def test_discovery_cuando_se_libera_el_cupo_la_siguiente_revision_manda(db):
    """El mismo escenario, tres horas despues: 24 horas desde la tanda vieja."""
    from services.discovery_emails import tanda_diaria

    _tanda_de_discovery_hace(db, 21)
    for n in range(60):
        _comercio(db, n)

    with patch("services.discovery_emails.send_discovery_email", return_value="ok") as envio, \
         patch("services.discovery_emails.sincronizar_desde_gmail", return_value={}), \
         patch("services.discovery_emails.avisar_si_la_cola_esta_baja", return_value=False), \
         patch("services.discovery_emails.time.sleep"):
        assert tanda_diaria(db, "https://crm") is None
        _correr_el_reloj(db, 3.5)
        res = tanda_diaria(db, "https://crm")

    assert res is not None and res["enviados"] == 50
    assert envio.call_count == 50
    assert ultima_corrida(db, "discovery") > _hace(1), "esta si es una tanda y se marca"


def test_discovery_una_revision_que_no_corre_no_consulta_gmail_ni_avisa(db):
    """Con el bucle horario, Gmail y el aviso de cola baja no pueden ir en cada
    vuelta: serian 24 consultas y hasta 24 mails al admin por dia."""
    from services.discovery_emails import tanda_diaria

    with patch("services.discovery_emails.send_discovery_email", return_value="ok"), \
         patch("services.discovery_emails.sincronizar_desde_gmail", return_value={}) as gmail, \
         patch("services.discovery_emails.avisar_si_la_cola_esta_baja", return_value=True) as aviso:
        # Caso 1: ya corrio hace un rato (la marca frena).
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO corridas (nombre, ultima) VALUES ('discovery', ?)", (_hace(2),))
        conn.commit()
        conn.close()
        assert tanda_diaria(db, "https://crm") is None

    assert gmail.call_count == 0
    assert aviso.call_count == 0


def test_discovery_sin_cupo_tampoco_consulta_gmail_ni_avisa(db):
    from services.discovery_emails import tanda_diaria

    _tanda_de_discovery_hace(db, 21)
    with patch("services.discovery_emails.send_discovery_email", return_value="ok"), \
         patch("services.discovery_emails.sincronizar_desde_gmail", return_value={}) as gmail, \
         patch("services.discovery_emails.avisar_si_la_cola_esta_baja", return_value=True) as aviso:
        assert tanda_diaria(db, "https://crm") is None

    assert gmail.call_count == 0
    assert aviso.call_count == 0


def test_discovery_cuando_corre_mira_respuestas_antes_y_avisa_despues(db):
    """El orden de siempre: respuestas antes de mandar (para frenar el
    seguimiento de quien contesto ayer) y el aviso despues (lo que importa es
    cuanto queda una vez descontada la tanda)."""
    from services.discovery_emails import tanda_diaria

    for n in range(3):
        _comercio(db, n)
    orden = []

    with patch("services.discovery_emails.send_discovery_email",
               side_effect=lambda *a, **k: orden.append("envio") or "ok"), \
         patch("services.discovery_emails.sincronizar_desde_gmail",
               side_effect=lambda *a, **k: orden.append("gmail") or {}), \
         patch("services.discovery_emails.avisar_si_la_cola_esta_baja",
               side_effect=lambda *a, **k: orden.append("aviso") or False), \
         patch("services.discovery_emails.time.sleep"):
        res = tanda_diaria(db, "https://crm")

    assert res["enviados"] == 3
    assert orden == ["gmail", "envio", "envio", "envio", "aviso"]


def test_discovery_si_gmail_falla_la_tanda_sale_igual(db):
    from services.discovery_emails import tanda_diaria

    _comercio(db, 1)
    with patch("services.discovery_emails.send_discovery_email", return_value="ok"), \
         patch("services.discovery_emails.sincronizar_desde_gmail", side_effect=RuntimeError("token")), \
         patch("services.discovery_emails.avisar_si_la_cola_esta_baja", return_value=False), \
         patch("services.discovery_emails.time.sleep"):
        res = tanda_diaria(db, "https://crm")

    assert res["enviados"] == 1


def test_discovery_si_el_aviso_revienta_la_tanda_igual_devuelve_lo_que_mando(db):
    from services.discovery_emails import tanda_diaria

    _comercio(db, 1)
    with patch("services.discovery_emails.send_discovery_email", return_value="ok"), \
         patch("services.discovery_emails.sincronizar_desde_gmail", return_value={}), \
         patch("services.discovery_emails.avisar_si_la_cola_esta_baja", side_effect=RuntimeError("x")), \
         patch("services.discovery_emails.time.sleep"):
        res = tanda_diaria(db, "https://crm")

    assert res["enviados"] == 1


# ─── Meta ────────────────────────────────────────────────────────────────────

def _lead_meta(conn, bid, dias):
    conn.execute(
        "INSERT INTO businesses (id, name, email, source, crm_status, form_data, scraped_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (bid, f"Lead {bid}", f"lead{bid}@ejemplo.com", "meta", "sin_contactar",
         json.dumps({"¿cómo_se_llama_tu_negocio?": f"Negocio {bid}"}),
         _hace(dias * 24)))


def test_meta_cuando_se_libera_el_cupo_la_siguiente_revision_manda(db):
    """Meta ya no marcaba sin cupo, pero el hilo dormia 24 horas despues del
    intento vacio: el reintento 'en el proximo arranque' era manana."""
    from services.meta_reminders import _TOPE_DIARIO, tanda_diaria

    conn = sqlite3.connect(db)
    _lead_meta(conn, 1, dias=30)
    for i in range(_TOPE_DIARIO):
        _lead_meta(conn, 900 + i, dias=40)
        conn.execute(
            "INSERT INTO meta_reminders (business_id, estado, numero, token, sent_at) "
            "VALUES (?, 'sin_contactar', 1, ?, ?)", (900 + i, f"tok-{i}", _hace(21)))
    conn.execute("INSERT INTO corridas (nombre, ultima) VALUES ('meta', ?)", (_hace(21),))
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok") as envio, \
         patch("services.discovery_respuestas.sincronizar_desde_gmail", return_value={}) as gmail:
        assert tanda_diaria(db, "https://crm") is None
        assert gmail.call_count == 0, "sin cupo no se consulta Gmail"

        conn = sqlite3.connect(db)
        conn.execute("UPDATE corridas SET ultima = datetime(ultima, '-4 hours')")
        conn.execute("UPDATE meta_reminders SET sent_at = datetime(sent_at, '-4 hours')")
        conn.commit()
        conn.close()
        res = tanda_diaria(db, "https://crm")

    assert res is not None and res["enviados"] == 1
    assert envio.call_count == 1
    assert gmail.call_count == 1


def test_meta_una_revision_que_ya_corrio_no_consulta_gmail(db):
    from services.meta_reminders import tanda_diaria

    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO corridas (nombre, ultima) VALUES ('meta', ?)", (_hace(2),))
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok"), \
         patch("services.discovery_respuestas.sincronizar_desde_gmail", return_value={}) as gmail:
        assert tanda_diaria(db, "https://crm") is None
    assert gmail.call_count == 0


# ─── El log no se llena ──────────────────────────────────────────────────────

def test_las_revisiones_que_se_saltean_no_escriben_info(db, caplog):
    """Una revision por hora son 24 lineas por dia por campana si el salteo
    escribe en INFO. Tiene que ser silencioso: INFO queda para cuando manda."""
    from services.discovery_emails import tanda_diaria as tanda_discovery
    from services.meta_reminders import _TOPE_DIARIO, tanda_diaria as tanda_meta

    conn = sqlite3.connect(db)
    for i in range(_TOPE_DIARIO):
        conn.execute(
            "INSERT INTO meta_reminders (business_id, estado, numero, token, sent_at) "
            "VALUES (?, 'sin_contactar', 1, ?, ?)", (900 + i, f"tok-{i}", _hace(21)))
    conn.execute("INSERT INTO corridas (nombre, ultima) VALUES ('meta', ?)", (_hace(21),))
    conn.commit()
    conn.close()
    _tanda_de_discovery_hace(db, 2)

    with caplog.at_level(logging.INFO), \
         patch("services.discovery_emails.sincronizar_desde_gmail", return_value={}), \
         patch("services.discovery_respuestas.sincronizar_desde_gmail", return_value={}):
        assert tanda_meta(db, "https://crm") is None       # sin cupo
        assert tanda_discovery(db, "https://crm") is None  # ya corrio
        _correr_el_reloj(db, 19)                           # 21 h: pasa la marca, sin cupo
        assert tanda_discovery(db, "https://crm") is None

    ruidosos = [r for r in caplog.records
                if r.levelno >= logging.INFO
                and r.name in ("services.discovery_emails", "services.meta_reminders")]
    assert ruidosos == [], [r.getMessage() for r in ruidosos]


# ─── La grilla de revisiones ─────────────────────────────────────────────────

def test_la_revision_es_cada_hora_y_no_cada_dia():
    assert 55 * 60 <= REVISAR_CADA_S <= 65 * 60


def test_veinticuatro_revisiones_caen_despues_de_que_la_tanda_de_ayer_salio_de_la_ventana():
    """Con 3600 s exactos, la revision que cae 24 horas despues de una tanda
    llega unos segundos ANTES de que salgan de la ventana los mails de esa
    tanda (se mandan despues de la revision, no en el mismo instante): el cupo
    da 0 y la tanda se corre a la hora siguiente. Una hora de atraso por dia es
    un dia perdido cada 25. Con el margen, a las 24 revisiones la tanda de ayer
    ya salio entera, siempre que haya durado menos que el margen."""
    margen = 24 * REVISAR_CADA_S - 24 * 60 * 60
    duracion_de_una_tanda_real = 90  # 50 mails a 0.6 s mas la peticion, con Gmail antes
    assert margen > duracion_de_una_tanda_real
    assert margen < 60 * 60, "no puede comerse una revision entera por dia"


def test_la_siguiente_revision_sigue_la_grilla_aunque_la_tanda_tarde():
    """La grilla no se corre con lo que tarda cada vuelta: si se corriera, el
    desfase entre Meta y discovery se iria achicando hasta que manden a la vez."""
    inicio = 1000.0
    assert siguiente_revision(inicio, ahora=inicio + 0.01) == inicio + REVISAR_CADA_S
    assert siguiente_revision(inicio, ahora=inicio + 95) == inicio + REVISAR_CADA_S


def test_si_se_salteo_una_revision_no_se_recuperan_de_golpe():
    """Una vuelta que tardo mas de una hora (Gmail colgado) no dispara tres
    revisiones seguidas: salta a la proxima de la grilla."""
    inicio = 1000.0
    ahora = inicio + 2.5 * REVISAR_CADA_S
    assert siguiente_revision(inicio, ahora=ahora) == inicio + 3 * REVISAR_CADA_S


class _Basta(Exception):
    pass


class _AppFalsa:
    config = {"DB_PATH": "x.db"}

    @contextmanager
    def app_context(self):
        yield


def _correr_bucle(modulo, arrancar, variable, vueltas):
    """Corre el `_loop` del hilo en este mismo hilo, con un reloj falso.

    Devuelve las esperas que pidio y los momentos en que reviso. Cada revision
    tarda 40 s de reloj, para ver que la grilla no se corre con eso.
    """
    reloj = {"t": 0.0}
    esperas, revisiones = [], []

    def dormir(s):
        esperas.append(s)
        if len(esperas) > vueltas:
            raise _Basta()
        reloj["t"] += s

    def revisar(*a, **k):
        revisiones.append(reloj["t"])
        reloj["t"] += 40

    capturado = {}

    class _Hilo:
        def __init__(self, target, **k):
            capturado["target"] = target

        def start(self):
            pass

    with patch.dict("os.environ", {variable: "on"}), \
         patch(f"{modulo.__name__}.threading.Thread", _Hilo), \
         patch(f"{modulo.__name__}.time.sleep", dormir), \
         patch(f"{modulo.__name__}.time.monotonic", lambda: reloj["t"]), \
         patch(f"{modulo.__name__}.tanda_diaria", revisar):
        arrancar(_AppFalsa())
        with pytest.raises(_Basta):
            capturado["target"]()
    return esperas, revisiones


def test_el_bucle_de_discovery_revisa_cada_hora_sobre_la_grilla():
    from services import discovery_emails

    esperas, revisiones = _correr_bucle(
        discovery_emails, discovery_emails.start_discovery_emails, "DISCOVERY_EMAILS", 4)

    assert esperas[0] == 600
    assert revisiones == [600 + k * REVISAR_CADA_S for k in range(4)]
    assert max(esperas) < 24 * 60 * 60 / 2, "ya no duerme un dia entero"


def test_el_bucle_de_meta_revisa_cada_hora_sobre_la_grilla():
    from services import meta_reminders

    esperas, revisiones = _correr_bucle(
        meta_reminders, meta_reminders.start_meta_reminders, "META_RECORDATORIOS", 4)

    assert esperas[0] == 180
    assert revisiones == [180 + k * REVISAR_CADA_S for k in range(4)]


def test_meta_y_discovery_quedan_desfasados_siete_minutos_para_siempre():
    """Los dos pausan 0.6 s entre envios: si mandan a la vez superan las 2
    peticiones por segundo de Resend, y un 429 se lee como fallo. Con la misma
    grilla y arranques a 180 s y 600 s, nunca revisan a menos de 7 minutos."""
    from services import discovery_emails, meta_reminders

    _, de_discovery = _correr_bucle(
        discovery_emails, discovery_emails.start_discovery_emails, "DISCOVERY_EMAILS", 30)
    _, de_meta = _correr_bucle(
        meta_reminders, meta_reminders.start_meta_reminders, "META_RECORDATORIOS", 30)

    for d, m in zip(de_discovery, de_meta):
        assert d - m == 420
