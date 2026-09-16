"""Alertas diarias de la pauta: que avisen lo que importa y no repitan."""

import sqlite3
from datetime import date, datetime, timezone

import pytest

from database import init_db
from services import alertas_meta as am
from services import email_service as es

# Martes 16/9/2026, 9:00 de Montevideo.
MARTES_9 = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
MARTES_7 = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)
LUNES_9 = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


def _fila(gasto, imp, clics, leads, frec=1.5, **extra):
    fila = {"spend": str(gasto), "impressions": str(imp),
            "inline_link_clicks": str(clics), "frequency": str(frec),
            "account_currency": "USD",
            "actions": [{"action_type": "lead", "value": str(leads)},
                        {"action_type": "onsite_conversion.lead_grouped",
                         "value": str(leads)}]}
    fila.update(extra)
    return fila


# Los numeros reales de la cuenta al 16/9/2026: el mes anterior ~USD 15 por
# lead con 8% de los clics convirtiendo; la ultima semana USD 33 y 3,5%.
REFERENCIA = _fila(565.30, 69000, 455, 37, frec=2.1)
SEMANA_MALA = _fila(263.20, 50500, 229, 8, frec=1.8)
SEMANA_NORMAL = _fila(140.0, 17000, 115, 9, frec=1.6)


def _datos(reciente, anuncios=(), ultimo_post="2026-09-15T14:00:00+0000"):
    return {"reciente": reciente, "referencia": REFERENCIA,
            "anuncios": list(anuncios), "ultimo_post": ultimo_post}


def _claves(alertas):
    return {a.clave for a in alertas}


def test_ventanas_no_se_pisan():
    v = am.ventanas(date(2026, 9, 16))
    assert v["reciente"] == (date(2026, 9, 9), date(2026, 9, 15))
    assert v["referencia"] == (date(2026, 8, 12), date(2026, 9, 8))


def test_la_semana_real_de_setiembre_dispara_lo_que_corresponde():
    claves = _claves(am.evaluar(_datos(SEMANA_MALA), date(2026, 9, 16)))
    # Esa semana cayeron los dos: menos clics (0,45% vs 0,66%) y, de los que
    # hicieron clic, muchos menos dejaron sus datos.
    assert {"costo_por_lead", "conversion", "ctr", "gasto"} <= claves
    # Meta no cobro mas caro por mostrar: el CPM bajo.
    assert "cpm" not in claves


def test_la_semana_del_2_de_setiembre_es_solo_conversion():
    """Clics normales (0,86%), conversion en 2,3%: el problema es despues del clic."""
    semana = _fila(167.76, 15400, 132, 3)
    claves = _claves(am.evaluar(_datos(semana), date(2026, 9, 9)))
    assert "conversion" in claves
    assert "ctr" not in claves


def test_una_semana_normal_no_alerta():
    assert am.evaluar(_datos(SEMANA_NORMAL), date(2026, 9, 16)) == []


def test_semana_sin_leads_con_gasto():
    claves = _claves(am.evaluar(_datos(_fila(120, 20000, 150, 0)), date(2026, 9, 16)))
    assert "sin_leads" in claves
    assert "costo_por_lead" not in claves


def test_referencia_chica_no_dispara_costo_por_lead():
    datos = _datos(SEMANA_MALA)
    datos["referencia"] = _fila(40, 5000, 30, 2)
    assert "costo_por_lead" not in _claves(am.evaluar(datos, date(2026, 9, 16)))


def test_pocos_clics_no_dispara_conversion():
    reciente = _fila(140, 17000, 40, 0)
    assert "conversion" not in _claves(am.evaluar(_datos(reciente), date(2026, 9, 16)))


def test_frecuencia_alta():
    alertas = am.evaluar(_datos(_fila(140, 17000, 115, 9, frec=3.4)), date(2026, 9, 16))
    frec = [a for a in alertas if a.clave == "frecuencia"]
    assert frec and "3,4" in frec[0].detalle


def test_anuncio_que_gasta_sin_leads_y_escapa_el_nombre():
    malo = _fila(64.46, 3900, 39, 0, ad_id="99", ad_name="<b>Winners</b>",
                 campaign_name="Leads - Set26")
    bueno = _fila(216.70, 28800, 180, 15, ad_id="1", ad_name="En el celu")
    chico = _fila(5, 700, 3, 0, ad_id="2", ad_name="Prueba")
    alertas = am.evaluar(_datos(SEMANA_NORMAL, [malo, bueno, chico]), date(2026, 9, 16))
    anuncios = [a for a in alertas if a.clave.startswith("anuncio:")]
    assert [a.clave for a in anuncios] == ["anuncio:99"]
    assert "<b>Winners</b>" not in anuncios[0].titulo
    assert "&lt;b&gt;" in anuncios[0].titulo


def test_instagram_quieto():
    datos = _datos(SEMANA_NORMAL, ultimo_post="2026-09-02T15:00:00+0000")
    ig = [a for a in am.evaluar(datos, date(2026, 9, 16)) if a.clave == "instagram"]
    assert ig and "14 días" in ig[0].detalle
    datos["ultimo_post"] = None
    assert "instagram" not in _claves(am.evaluar(datos, date(2026, 9, 16)))


def test_sin_datos_no_rompe():
    vacio = {"reciente": {}, "referencia": {}, "anuncios": [], "ultimo_post": None}
    assert am.evaluar(vacio, date(2026, 9, 16)) == []
    asunto, cuerpo = am.componer(vacio, [])
    assert "sin alertas" in asunto
    assert "—" in cuerpo


# ── corrida ──────────────────────────────────────────────────────────────────

class _Buzon:
    def __init__(self, ok=True):
        self.mails, self.ok = [], ok

    def __call__(self, to, asunto, cuerpo):
        self.mails.append((to, asunto, cuerpo))
        return self.ok


def _traer(datos):
    return lambda hoy: datos


def test_antes_de_las_8_no_hace_nada(db):
    buzon = _Buzon()
    r = am.corrida(db, ahora=MARTES_7, traer=_traer(_datos(SEMANA_MALA)), enviar=buzon)
    assert r["estado"] == "temprano" and buzon.mails == []


def test_manda_una_vez_por_dia_a_contacto(db):
    buzon = _Buzon()
    r = am.corrida(db, ahora=MARTES_9, traer=_traer(_datos(SEMANA_MALA)), enviar=buzon)
    assert r["estado"] == "enviado"
    assert buzon.mails[0][0] == "contacto@scalerics.com"
    assert "alertas" in buzon.mails[0][1]
    # Un deploy reinicia el hilo: la segunda vuelta del mismo dia no manda.
    r = am.corrida(db, ahora=MARTES_9, traer=_traer(_datos(SEMANA_MALA)), enviar=buzon)
    assert r["estado"] == "ya_corrio" and len(buzon.mails) == 1


def test_destino_configurable(db, monkeypatch):
    monkeypatch.setenv("ALERTAS_META_EMAIL", "otro@scalerics.com")
    buzon = _Buzon()
    am.corrida(db, ahora=MARTES_9, traer=_traer(_datos(SEMANA_MALA)), enviar=buzon)
    assert buzon.mails[0][0] == "otro@scalerics.com"


def _pasar_el_dia(db):
    conn = sqlite3.connect(db)
    conn.execute("UPDATE corridas SET ultima = datetime('now', '-21 hours') "
                 "WHERE nombre = 'alertas_meta'")
    conn.commit()
    conn.close()


def test_la_misma_alerta_no_se_repite_al_dia_siguiente(db):
    buzon = _Buzon()
    am.corrida(db, ahora=MARTES_9, traer=_traer(_datos(SEMANA_MALA)), enviar=buzon)
    _pasar_el_dia(db)
    r = am.corrida(db, ahora=MARTES_9, traer=_traer(_datos(SEMANA_MALA)), enviar=buzon)
    assert r["estado"] == "sin_novedades" and len(buzon.mails) == 1


def test_sin_alertas_un_martes_no_manda(db):
    buzon = _Buzon()
    r = am.corrida(db, ahora=MARTES_9, traer=_traer(_datos(SEMANA_NORMAL)), enviar=buzon)
    assert r["estado"] == "sin_novedades" and buzon.mails == []


def test_el_lunes_manda_el_resumen_aunque_no_haya_alertas(db):
    buzon = _Buzon()
    r = am.corrida(db, ahora=LUNES_9, traer=_traer(_datos(SEMANA_NORMAL)), enviar=buzon)
    assert r["estado"] == "enviado"
    assert "sin alertas" in buzon.mails[0][1]


def test_si_el_envio_falla_la_alerta_no_queda_marcada(db):
    am.corrida(db, ahora=MARTES_9, traer=_traer(_datos(SEMANA_MALA)), enviar=_Buzon(ok=False))
    _pasar_el_dia(db)
    buzon = _Buzon()
    r = am.corrida(db, ahora=MARTES_9, traer=_traer(_datos(SEMANA_MALA)), enviar=buzon)
    assert r["estado"] == "enviado" and buzon.mails


def test_error_de_meta_avisa_una_sola_vez(db, monkeypatch):
    avisos = []
    monkeypatch.setattr(es, "send_alertas_meta_error",
                        lambda to, err: avisos.append(err) or True)

    def _falla(hoy):
        raise am.ErrorDeMeta("code=190 token invalido")

    r = am.corrida(db, ahora=MARTES_9, traer=_falla, enviar=_Buzon())
    assert r["estado"] == "error"
    _pasar_el_dia(db)
    am.corrida(db, ahora=MARTES_9, traer=_falla, enviar=_Buzon())
    assert avisos == ["code=190 token invalido"]


def test_sin_credenciales_no_llama_a_meta(db, monkeypatch):
    monkeypatch.delenv("META_ADS_TOKEN", raising=False)
    buzon = _Buzon()
    r = am.corrida(db, ahora=MARTES_9, enviar=buzon)
    assert r["estado"] == "sin_credenciales" and buzon.mails == []


def test_enviar_ahora_tiene_pausa(db, monkeypatch):
    llamadas = []
    monkeypatch.setattr(am, "corrida", lambda db_path, forzar: llamadas.append(forzar)
                        or {"estado": "enviado"})
    assert am.enviar_ahora(db)["estado"] == "enviado"
    assert am.enviar_ahora(db)["estado"] == "esperar"
    assert llamadas == [True]


def test_forzar_manda_aunque_ya_haya_corrido(db):
    buzon = _Buzon()
    am.corrida(db, ahora=MARTES_9, traer=_traer(_datos(SEMANA_MALA)), enviar=buzon)
    r = am.corrida(db, ahora=MARTES_7, traer=_traer(_datos(SEMANA_MALA)),
                   enviar=buzon, forzar=True)
    assert r["estado"] == "enviado" and len(buzon.mails) == 2


def test_apagado_no_arranca_el_hilo(monkeypatch):
    monkeypatch.setenv("ALERTAS_META", "off")
    arrancados = []
    monkeypatch.setattr(am.threading, "Thread",
                        lambda *a, **k: arrancados.append(k) or pytest.fail("arranco"))
    am.start_alertas_meta(object())
    assert arrancados == []


def test_el_mail_completo_se_arma_sin_red():
    asunto, cuerpo = am.componer(_datos(SEMANA_MALA),
                                 am.evaluar(_datos(SEMANA_MALA), date(2026, 9, 16)))
    with es.capturar_envio() as capturados:
        assert es.send_alertas_meta("contacto@scalerics.com", asunto, cuerpo)
    assert capturados[0]["to"] == "contacto@scalerics.com"
    assert "Subió el costo por lead" in capturados[0]["html"]
    assert "USD 32,90" in capturados[0]["html"]


def test_el_endpoint_pide_permiso(tmp_path, monkeypatch):
    from dashboard import create_app

    monkeypatch.setenv("ADMIN_TOKEN", "secreto")
    llamadas = []
    monkeypatch.setattr(am, "enviar_ahora", lambda db_path: llamadas.append(db_path)
                        or {"estado": "enviado"})
    app = create_app(str(tmp_path / "leads.db"))
    cliente = app.test_client()
    assert cliente.post("/api/marketing/alertas/enviar-ahora").status_code in (302, 401, 403)
    r = cliente.post("/api/marketing/alertas/enviar-ahora",
                     headers={"x-admin-token": "secreto"})
    assert r.status_code == 200 and llamadas
