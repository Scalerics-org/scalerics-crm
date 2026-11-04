"""Publicacion automatica de LinkedIn (services/linkedin_publicador.py).

Decision de Juan (4/11/2026): los posteos salen solos. Y "tenemos que ser muy
profesionales con la publicacion del contenido": estos tests cuidan que salga
uno por turno, en horario, en orden, nunca dos veces, siempre con imagen y
nunca un texto que no pase la revision. Nada llama a LinkedIn de verdad.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import services.linkedin_borradores as lb
import services.linkedin_publicador as pub
from database import create_linkedin_post, init_db, seed_linkedin_banco
from services.linkedin_api import LinkedInError, little_text, versiones

# Martes 3/11/2026 08:00 en Montevideo: el cron arma dos borradores.
ARMADO = datetime(2026, 11, 3, 11, 0, tzinfo=timezone.utc)
CONECTADO = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
# Martes 3/11 10:30 en Montevideo: dentro del turno.
TURNO_MARTES = datetime(2026, 11, 3, 13, 30, tzinfo=timezone.utc)

TEXTO = ("La planilla de stock de un taller se actualizaba los viernes a la tarde, cuando "
         "alguien tenía un rato libre entre un auto y otro.\n\n"
         "Los martes ya no servía para nada: había repuestos vendidos que seguían figurando y "
         "pedidos al proveedor que nadie había anotado.\n\n#Automatizacion")


class FakeCliente:
    def __init__(self, falla=None):
        self.publicados = []
        self.falla = falla

    def subir_imagen(self, org, png):
        assert png.startswith(b"\x89PNG")
        return "urn:li:image:1"

    def publicar(self, org, texto, imagen, alt=""):
        if self.falla:
            raise LinkedInError(self.falla, 500)
        self.publicados.append((org, texto, imagen))
        return f"urn:li:share:{len(self.publicados)}"

    def paginas_admin(self):
        return [pub.ORG_SCALERICS, "urn:li:organization:101588795"]

    def nombre_pagina(self, org):
        return "Scalerics"


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "pub.db")
    init_db(ruta)
    seed_linkedin_banco(ruta)
    return ruta


@pytest.fixture
def avisos():
    return []


def _avisar(lista):
    return lambda tipo, **kw: lista.append((tipo, kw))


def _sql(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        filas = [dict(f) for f in conn.execute(sql, params).fetchall()]
        conn.commit()
        return filas
    finally:
        conn.close()


def _borrador(db, texto=TEXTO, ahora=ARMADO, tema="Planillas"):
    pid = create_linkedin_post(
        db, job_id=None, lote="l", tipo="educativo", texto=texto, angulo="concreto", fuente_tipo="banco",
        fuente_id=None, imagen_tipo="tarjeta", imagen_spec="{}", fuente_desc=f"Tema educativo: {tema}",
        aviso="", marcar_token=None)
    lb.guardar_borradores(db, [{"id": pid, "texto": texto, "fuente_desc": f"Tema educativo: {tema}",
                                "imagen_tipo": "tarjeta", "imagen_spec": {"frase": "Una frase corta"}}], ahora)
    return _sql(db, "SELECT id FROM linkedin_borradores WHERE post_id = ?", (pid,))[0]["id"]


def _conectar(db, ahora=CONECTADO, expira_dias=60):
    pub.guardar_conexion(db, {"access_token": "tok", "expires_in": expira_dias * 86400},
                         pub.ORG_SCALERICS, "Scalerics", "Juan", ahora)


def _estado(db, bid):
    return _sql(db, "SELECT * FROM linkedin_borradores WHERE id = ?", (bid,))[0]


# ── turnos ───────────────────────────────────────────────────────────────────

def test_turnos_lunes_martes_jueves_viernes_a_las_10():
    lunes = datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc)
    dias = [(lunes + timedelta(days=d)) for d in range(7)]
    con_turno = [lb.a_montevideo(d).weekday() for d in dias if pub.turno_de(d)]
    assert con_turno == [0, 1, 3, 4]
    assert lb.a_montevideo(pub.turno_de(lunes)).hour == 10


def test_fuera_de_la_ventana_no_hay_turno():
    assert pub.turno_abierto(datetime(2026, 11, 3, 12, 59, tzinfo=timezone.utc)) is None  # 09:59
    assert pub.turno_abierto(datetime(2026, 11, 3, 13, 0, tzinfo=timezone.utc))  # 10:00
    assert pub.turno_abierto(datetime(2026, 11, 3, 16, 0, tzinfo=timezone.utc)) is None  # 13:00
    assert pub.turno_abierto(datetime(2026, 11, 4, 13, 30, tzinfo=timezone.utc)) is None  # miercoles


def test_proximo_turno_desde_el_miercoles_es_el_jueves():
    t = pub.proximo_turno(datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc))
    assert pub.describir_turno(t) == "jueves 05/11 a las 10:00"


# ── rutina ───────────────────────────────────────────────────────────────────

def test_sin_conectar_no_publica(db, avisos):
    _borrador(db)
    r = pub.rutina(db, TURNO_MARTES, FakeCliente(), _avisar(avisos))
    assert r["estado"] == "sin_conectar"


def test_publica_uno_por_turno_en_orden_y_con_imagen(db, avisos):
    _conectar(db)
    b1 = _borrador(db, TEXTO, tema="Uno")
    b2 = _borrador(db, TEXTO.replace("viernes", "lunes"), tema="Dos")
    cli = FakeCliente()
    r = pub.rutina(db, TURNO_MARTES, cli, _avisar(avisos))
    assert r["estado"] == "publicado" and r["id"] == b1
    assert len(cli.publicados) == 1
    org, texto, imagen = cli.publicados[0]
    assert org == pub.ORG_SCALERICS and imagen == "urn:li:image:1"
    fila = _estado(db, b1)
    assert fila["estado"] == "publicado" and fila["linkedin_urn"] == "urn:li:share:1"
    assert fila["imagen_png"] is not None
    # Mismo turno, otra vuelta: no sale el segundo.
    r = pub.rutina(db, TURNO_MARTES + timedelta(minutes=10), cli, _avisar(avisos))
    assert r["estado"] == "turno_hecho"
    assert _estado(db, b2)["estado"] == "borrador"
    # Jueves: sale el segundo.
    jueves = TURNO_MARTES + timedelta(days=2)
    assert pub.rutina(db, jueves, cli, _avisar(avisos))["id"] == b2
    assert avisos == []


def test_lo_anterior_a_conectar_no_sale(db, avisos):
    _borrador(db, ahora=CONECTADO - timedelta(days=5))
    _conectar(db)
    assert pub.rutina(db, TURNO_MARTES, FakeCliente(), _avisar(avisos))["estado"] == "sin_borradores"


def test_pausado_no_publica(db, avisos):
    _conectar(db)
    _borrador(db)
    pub.pausar(db, True)
    assert pub.rutina(db, TURNO_MARTES, FakeCliente(), _avisar(avisos))["estado"] == "pausado"
    pub.pausar(db, False)
    assert pub.rutina(db, TURNO_MARTES, FakeCliente(), _avisar(avisos))["estado"] == "publicado"


def test_token_vencido_no_publica(db, avisos):
    _conectar(db, expira_dias=0)
    _borrador(db)
    assert pub.rutina(db, TURNO_MARTES, FakeCliente(), _avisar(avisos))["estado"] == "vencido"
    assert avisos and avisos[0][0] == "vence"


def test_texto_que_no_pasa_la_revision_se_saltea_y_avisa(db, avisos):
    _conectar(db)
    malo = _borrador(db, "Yo creo que esto anda — o no.", tema="Malo")
    bueno = _borrador(db, TEXTO, tema="Bueno")
    cli = FakeCliente()
    r = pub.rutina(db, TURNO_MARTES, cli, _avisar(avisos))
    assert r["id"] == bueno
    assert _estado(db, malo)["error_pub"]
    assert [a[0] for a in avisos] == ["salteado"]


def test_correccion_pendiente_espera_sin_marcar_error(db, avisos):
    _conectar(db)
    b = _borrador(db)
    lb.pedir_correccion(db, b, "más corto", "Juan")
    assert pub.rutina(db, TURNO_MARTES, FakeCliente(), _avisar(avisos))["estado"] == "sin_borradores"
    assert _estado(db, b)["error_pub"] is None
    assert avisos == []


def test_texto_ya_publicado_no_se_repite(db, avisos):
    _conectar(db)
    viejo = _borrador(db)
    lb.cambiar_estado(db, viejo, "publicado")
    copia = _borrador(db)
    cli = FakeCliente()
    assert pub.rutina(db, TURNO_MARTES, cli, _avisar(avisos))["estado"] == "sin_borradores"
    assert cli.publicados == []
    assert "ya se publicó" in _estado(db, copia)["error_pub"]


def test_si_linkedin_falla_queda_con_error_y_no_reintenta(db, avisos):
    _conectar(db)
    b = _borrador(db)
    r = pub.rutina(db, TURNO_MARTES, FakeCliente(falla="caido"), _avisar(avisos))
    assert r["estado"] == "error"
    fila = _estado(db, b)
    assert fila["estado"] == "borrador" and "caido" in fila["error_pub"]
    assert avisos[0][0] == "error"
    cli = FakeCliente()
    assert pub.rutina(db, TURNO_MARTES + timedelta(days=2), cli, _avisar(avisos))["estado"] == "sin_borradores"
    assert cli.publicados == []
    # Desde el panel se vuelve a poner en fila.
    assert lb.limpiar_error(db, b)
    assert pub.rutina(db, TURNO_MARTES + timedelta(days=3), cli, _avisar(avisos))["estado"] == "publicado"


def test_publicar_uno_no_publica_dos_veces(db):
    _conectar(db)
    b = _borrador(db)
    con = pub.conexion(db)
    cand = pub.candidatos(db, con["activado_en"])[0]
    cli = FakeCliente()
    assert pub.publicar_uno(db, cand, con, cli, TURNO_MARTES)["estado"] == "publicado"
    assert pub.publicar_uno(db, cand, con, cli, TURNO_MARTES)["estado"] == "ya_tomado"
    assert len(cli.publicados) == 1
    assert _estado(db, b)["estado"] == "publicado"


def test_reconectar_no_mueve_la_activacion(db):
    _conectar(db)
    antes = pub.conexion(db)["activado_en"]
    _conectar(db, ahora=CONECTADO + timedelta(days=30))
    assert pub.conexion(db)["activado_en"] == antes


def test_conectar_exige_admin_de_scalerics(db, monkeypatch):
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "id")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "s")

    class Http:
        @staticmethod
        def post(url, data, timeout):
            class R:
                status_code = 200
                text = ""

                @staticmethod
                def json():
                    return {"access_token": "nuevo", "expires_in": 5184000}
            return R()

    assert pub.conectar(db, "code", "https://x/cb", "Juan", http=Http, cliente=FakeCliente()) == "Scalerics"
    assert pub.conexion(db)["org_urn"] == pub.ORG_SCALERICS

    class Ajeno(FakeCliente):
        def paginas_admin(self):
            return ["urn:li:organization:101588795"]

    with pytest.raises(LinkedInError):
        pub.conectar(db, "code", "https://x/cb", "Juan", http=Http, cliente=Ajeno())


def test_estado_panel(db):
    assert pub.estado_panel(db, TURNO_MARTES)["conectado"] is False
    _conectar(db)
    _borrador(db, tema="Uno")
    e = pub.estado_panel(db, datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc))
    assert e["conectado"] and e["pagina"] == "Scalerics" and not e["pausado"]
    assert e["proximo_turno"] == "jueves 05/11 a las 10:00"
    assert e["proximo"]["tema"] == "Uno"


# ── api ──────────────────────────────────────────────────────────────────────

def test_little_text_escapa_y_arma_hashtags():
    assert little_text("Hola (prueba) a_b #SEOLocal.") == "Hola \\(prueba\\) a\\_b {hashtag|\\#|SEOLocal}."
    assert little_text("sin nada raro, 100%") == "sin nada raro, 100%"


def test_versiones_van_hacia_atras():
    from datetime import date
    assert versiones(date(2026, 1, 15))[:2] == ["202511", "202510"]
