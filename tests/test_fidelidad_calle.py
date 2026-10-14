"""Cambios pedidos por Lucas desde el uso del CRM en la calle (14/10).

Lo que se pidió, cada cosa con su test:
1. motivo de no contacto obligatorio en "No atendió", con lista cerrada, y el
   contador de intentos por local
2. resultado «Pidió info por WhatsApp»: número, persona y cargo, fecha de envío
   y estado; a los X días sin respuesta pide insistir
3. mails dentro de la ficha (varios por local), con a nombre de quién, qué se
   envió y estado, y la vista de todos los mails
4. listas separadas por estado, con su total, filtro por zona o barrio y orden
   por nombre, último contacto o intentos
5. reunión y demo: fecha, con quién, demo con la marca, qué se envió, etapa,
   próximo paso con fecha y aviso cuando se pasa. TNT Burger, Los Sopranos y
   Rodelú entran solos.

Hoy está fijo en el miércoles 14/10/2026, 10:30 de Montevideo.
"""

import re
import sqlite3
from datetime import datetime

import pytest

import dashboard
import routes.fidelidad as rutas
from database import create_user, init_db
from services import fidelidad as fid
from werkzeug.security import generate_password_hash

AHORA = datetime(2026, 10, 14, 10, 30)          # miércoles


@pytest.fixture
def db(tmp_path, monkeypatch):
    ruta = str(tmp_path / "calle.db")
    init_db(ruta)
    monkeypatch.setattr(fid, "ahora", lambda: AHORA)
    return ruta


@pytest.fixture
def cli(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(rutas, "_ahora", lambda: AHORA)
    app = dashboard.create_app(db)
    uid = create_user(db, name="jefe", email="jefe@scalerics.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Lucas"
    return c


_TEL = iter(range(100, 999))


def _p(db, nombre, **extra):
    datos = {"nombre": nombre, "zona": "Municipio CH", "barrio": "Pocitos", "tipo": "Pizzería",
             "telefono": f"2708 1{next(_TEL)}", "rating": 4.4, "resenas": 400}
    datos.update(extra)
    pid, que = fid.crear_prospecto(db, datos)
    assert que == "creado", que
    return pid


def _vista(db, vista, **kw):
    return fid.armar_lista(db, ciudad="Montevideo", rubro="restaurante", cuando=AHORA, vista=vista, **kw)


def _nombres(d):
    return [p["nombre"] for p in d["items"]]


# ── 1. motivo de no contacto ─────────────────────────────────────────────────

def test_no_atendio_sin_motivo_no_se_guarda(db):
    pid = _p(db, "Pizza Nostra")
    p, lid, err = fid.registrar_accion(db, pid, "no_atendio", "Lucas", cuando=AHORA)
    assert err and "por qué" in err and lid is None
    _, _, err = fid.registrar_accion(db, pid, "no_atendio", "Lucas", cuando=AHORA, motivo="cualquiera")
    assert err


def test_los_siete_motivos_del_pedido():
    assert list(fid.MOTIVOS_NO_CONTACTO.values()) == [
        "Buzón de voz", "Suena y no contesta", "Ocupado", "Número inexistente / fuera de servicio",
        "Número equivocado", "Cortaron la llamada", "Cerrado por horario"]


def test_el_motivo_queda_en_la_llamada_y_propone_cuando_reintentar(db):
    pid = _p(db, "Pizza Nostra")
    p, _, err = fid.registrar_accion(db, pid, "no_atendio", "Lucas", cuando=AHORA, motivo="ocupado")
    assert not err
    assert p["llamadas"][0]["motivo_no_contacto"] == "ocupado"
    assert p["proxima_llamada"] == "2026-10-14 10:45"          # ocupado: en el momento
    p, _, _ = fid.registrar_accion(db, _p(db, "Burger Lab"), "no_atendio", "Lucas", cuando=AHORA, motivo="no_contesta")
    assert p["proxima_llamada"] == "2026-10-14 12:30"          # el mismo día, más tarde
    p, _, _ = fid.registrar_accion(db, _p(db, "Sushi Go"), "no_atendio", "Lucas", cuando=AHORA, motivo="buzon")
    assert p["proxima_llamada"] == "2026-10-15 16:00"          # otro horario
    p, _, _ = fid.registrar_accion(db, _p(db, "Café"), "no_atendio", "Lucas", cuando=AHORA, motivo="cerrado_horario")
    assert p["proxima_llamada"] == "2026-10-15 12:00"


def test_las_otras_acciones_no_piden_motivo(db):
    p, _, err = fid.registrar_accion(db, _p(db, "Pizza Nostra"), "otro_dia", "Lucas", cuando=AHORA, motivo="buzon")
    assert not err and p["llamadas"][0]["motivo_no_contacto"] is None


def test_contador_de_intentos_y_ultimo_motivo_en_la_lista(db):
    pid = _p(db, "Pizza Nostra")
    fid.registrar_accion(db, pid, "no_atendio", "Lucas", cuando=datetime(2026, 10, 10, 11, 0), motivo="buzon")
    fid.registrar_accion(db, pid, "no_atendio", "Lucas", cuando=datetime(2026, 10, 13, 11, 0), motivo="inexistente")
    f = _vista(db, "no_atendieron")["items"][0]
    assert f["intentos"] == 2 and f["motivo_no_contacto"] == "inexistente"


def test_el_motivo_viaja_por_la_api(cli, db):
    pid = _p(db, "Pizza Nostra")
    r = cli.post(f"/api/fidelidad/prospectos/{pid}/accion", json={"accion": "no_atendio"})
    assert r.status_code == 400
    r = cli.post(f"/api/fidelidad/prospectos/{pid}/accion", json={"accion": "no_atendio", "motivo": "cortaron"})
    assert r.status_code == 201
    assert r.get_json()["prospecto"]["llamadas"][0]["motivo_no_contacto"] == "cortaron"


# ── 2. pidió info por WhatsApp ───────────────────────────────────────────────

def test_pidio_whatsapp_deja_el_envio_pendiente_con_el_celular(db):
    pid = _p(db, "Burger Lab", contacto="Martín", contacto_tel="099 123 456")
    p, lid, err = fid.registrar_accion(db, pid, "info_whatsapp", "Lucas", cuando=AHORA)
    assert not err
    x = p["propuestas"][0]
    assert (x["canal"], x["destino"], x["persona"], x["estado"], x["llamada_id"]) == \
        ("whatsapp", "099 123 456", "Martín", "pendiente", lid)
    assert p["estado"] == "contactado" and p["llamadas"][0]["efectivo"] == 1


def test_un_fijo_no_se_sugiere_como_whatsapp(db):
    p, _, _ = fid.registrar_accion(db, _p(db, "Burger Lab", telefono="2708 0000"), "info_whatsapp", "Lucas", cuando=AHORA)
    assert p["propuestas"][0]["destino"] is None


def test_deshacer_la_llamada_borra_el_envio(db):
    p, lid, _ = fid.registrar_accion(db, _p(db, "Burger Lab"), "info_whatsapp", "Lucas", cuando=AHORA)
    p, err = fid.deshacer_llamada(db, lid)
    assert not err and p["propuestas"] == []


def test_enviada_sin_respuesta_pide_insistir_a_los_x_dias(db):
    pid = _p(db, "Burger Lab")
    p, _, _ = fid.registrar_accion(db, pid, "info_whatsapp", "Lucas", cuando=AHORA)
    prop = p["propuestas"][0]["id"]
    _, err = fid.guardar_propuesta(db, None, {"estado": "enviada"}, "Lucas", prop_id=prop, cuando=AHORA)
    assert err == "falta el número de WhatsApp"
    p, err = fid.guardar_propuesta(db, None, {"destino": "099 555 666", "persona": "Ana", "cargo": "Gerencia",
                                              "contenido": "Propuesta general", "estado": "enviada",
                                              "enviado_en": "2026-10-09"}, "Lucas", prop_id=prop, cuando=AHORA)
    assert not err
    x = p["propuestas"][0]
    assert x["enviado_en"] == "2026-10-09" and x["insistir"]   # 9/10 + 3 días = 12/10, ya pasó
    # La llamada del segundo toque queda en la cola, y el celular en la ficha.
    assert p["proxima_llamada"] == "2026-10-12 11:00" and p["contacto_tel"] == "099 555 666"
    d = _vista(db, "whatsapp")
    assert _nombres(d) == ["Burger Lab"] and d["kpis"]["insistir"] == 1


def test_recien_enviada_no_pide_insistir_y_respondio_sale_de_la_lista(db):
    p, _, _ = fid.registrar_accion(db, _p(db, "Burger Lab", contacto_tel="099 123 456"), "info_whatsapp", "Lucas",
                                   cuando=AHORA)
    prop = p["propuestas"][0]["id"]
    p, _ = fid.guardar_propuesta(db, None, {"estado": "enviada"}, "Lucas", prop_id=prop, cuando=AHORA)
    assert p["propuestas"][0]["enviado_en"] == "2026-10-14" and not p["propuestas"][0]["insistir"]
    assert p["proxima_llamada"] == "2026-10-19 11:00"          # 17/10 es sábado: el lunes
    fid.guardar_propuesta(db, None, {"estado": "respondio"}, "Lucas", prop_id=prop, cuando=AHORA)
    assert _vista(db, "whatsapp")["items"] == []


def test_los_dias_para_insistir_se_configuran(db):
    fid.set_config(db, {"dias_insistir": 10})
    p, _, _ = fid.registrar_accion(db, _p(db, "Burger Lab", contacto_tel="099 123 456"), "info_whatsapp", "Lucas",
                                   cuando=AHORA)
    p, _ = fid.guardar_propuesta(db, None, {"estado": "enviada", "enviado_en": "2026-10-09"}, "Lucas",
                                 prop_id=p["propuestas"][0]["id"], cuando=AHORA)
    assert not p["propuestas"][0]["insistir"]


# ── 3. mails con seguimiento ─────────────────────────────────────────────────

def test_varios_mails_por_local_con_a_nombre_de_quien(cli, db):
    pid = _p(db, "Empanadas Doña Ana")
    r = cli.post(f"/api/fidelidad/prospectos/{pid}/propuestas",
                 json={"canal": "mail", "destino": "dueno@ana.uy", "cargo": "Dueño", "contenido": "Propuesta general"})
    assert r.status_code == 201
    r = cli.post(f"/api/fidelidad/prospectos/{pid}/propuestas",
                 json={"canal": "mail", "destino": "admin@ana.uy", "cargo": "Administración",
                       "contenido": "Presupuesto específico", "estado": "enviada", "enviado_en": "2026-10-13"})
    p = r.get_json()["prospecto"]
    assert [(x["destino"], x["cargo"], x["estado"]) for x in p["propuestas"]] == [
        ("dueno@ana.uy", "Dueño", "pendiente"), ("admin@ana.uy", "Administración", "enviada")]
    assert p["email"] == "dueno@ana.uy"   # completa la ficha, no la pisa
    r = cli.post(f"/api/fidelidad/prospectos/{pid}/propuestas", json={"canal": "mail", "destino": "no-es-mail"})
    assert r.status_code == 400


def test_la_vista_de_mails_pone_arriba_los_que_hay_que_mandar(db):
    a, b, c = _p(db, "Esperando"), _p(db, "A mandar"), _p(db, "Segundo toque")
    fid.guardar_propuesta(db, a, {"canal": "mail", "destino": "a@x.uy", "estado": "enviada", "enviado_en": "2026-10-13"}, "L", cuando=AHORA)
    fid.guardar_propuesta(db, b, {"canal": "mail", "destino": "b@x.uy"}, "L", cuando=AHORA)
    fid.guardar_propuesta(db, c, {"canal": "mail", "destino": "c@x.uy", "estado": "sin_respuesta", "enviado_en": "2026-10-01"}, "L", cuando=AHORA)
    d = _vista(db, "mail")
    assert _nombres(d) == ["A mandar", "Segundo toque", "Esperando"]
    assert d["kpis"]["a_enviar"] == 1 and d["kpis"]["insistir"] == 1


def test_mandar_el_mail_desde_el_crm_lo_marca_enviado(db):
    pid = _p(db, "Empanadas Doña Ana")
    fid.guardar_propuesta(db, pid, {"canal": "mail", "destino": "Admin@Ana.uy"}, "L", cuando=AHORA)
    p = fid.registrar_mail(db, pid, "Lucas", "lucas@scalerics.com", "admin@ana.uy", "Hola", cuando=AHORA)
    assert p["propuestas"][0]["estado"] == "enviada" and p["propuestas"][0]["enviado_en"] == "2026-10-14"


def test_borrar_un_envio(cli, db):
    pid = _p(db, "Empanadas Doña Ana")
    p, _ = fid.guardar_propuesta(db, pid, {"canal": "mail", "destino": "a@ana.uy"}, "L", cuando=AHORA)
    r = cli.delete(f"/api/fidelidad/propuestas/{p['propuestas'][0]['id']}")
    assert r.status_code == 200 and r.get_json()["prospecto"]["propuestas"] == []
    assert cli.delete("/api/fidelidad/propuestas/999").status_code == 404


# ── 4. listas por estado ─────────────────────────────────────────────────────

def test_cada_local_en_su_lista_con_los_totales(db):
    nuevo = _p(db, "Nuevo")
    no_at = _p(db, "No atendió")
    wa = _p(db, "Pidió WhatsApp")
    mail = _p(db, "Pidió mail")
    desc = _p(db, "Descartado")
    reu = _p(db, "Con reunión")
    fid.registrar_accion(db, no_at, "no_atendio", "L", cuando=AHORA, motivo="buzon")
    fid.registrar_accion(db, wa, "info_whatsapp", "L", cuando=AHORA)
    fid.registrar_accion(db, mail, "info_mail", "L", cuando=AHORA)
    fid.registrar_accion(db, desc, "no_interesa", "L", cuando=AHORA)
    fid.registrar_accion(db, reu, "reunion", "L", cuando=AHORA)
    d = _vista(db, "todos")
    assert d["conteos"] == {"todos": 6, "sin_llamar": 1, "llamados": 5, "no_atendieron": 1, "whatsapp": 1,
                            "mail": 1, "reunion": 1, "descartados": 1}
    assert _nombres(_vista(db, "sin_llamar")) == ["Nuevo"]
    assert _nombres(_vista(db, "no_atendieron")) == ["No atendió"]
    assert _nombres(_vista(db, "whatsapp")) == ["Pidió WhatsApp"]
    assert _nombres(_vista(db, "mail")) == ["Pidió mail"]
    assert _nombres(_vista(db, "reunion")) == ["Con reunión"]
    assert _nombres(_vista(db, "descartados")) == ["Descartado"]
    assert set(_nombres(_vista(db, "llamados"))) == {"No atendió", "Pidió WhatsApp", "Pidió mail", "Descartado", "Con reunión"}
    assert nuevo


def test_filtro_por_zona_o_barrio(db):
    _p(db, "En Pocitos")
    _p(db, "En Cordón", zona=None, barrio="Cordón")
    d = _vista(db, "todos", barrio="Cordón")
    assert _nombres(d) == ["En Cordón"] and d["conteos"]["todos"] == 1
    assert {"nombre": "Cordón", "n": 1} in d["zonas"] and {"nombre": "Pocitos", "n": 1} in d["zonas"]
    # Por zona agrupada también: Pocitos es Municipio CH.
    assert _nombres(_vista(db, "todos", barrio="Municipio CH")) == ["En Pocitos"]


def test_ordenes_por_nombre_ultimo_contacto_e_intentos(db):
    a, b, c = _p(db, "Bravo"), _p(db, "Alfa"), _p(db, "Charlie")
    fid.registrar_accion(db, a, "no_atendio", "L", cuando=datetime(2026, 10, 8, 11, 0), motivo="buzon")
    fid.registrar_accion(db, a, "no_atendio", "L", cuando=datetime(2026, 10, 9, 11, 0), motivo="buzon")
    fid.registrar_accion(db, a, "no_atendio", "L", cuando=datetime(2026, 10, 10, 11, 0), motivo="buzon")
    fid.registrar_accion(db, c, "no_atendio", "L", cuando=datetime(2026, 10, 13, 11, 0), motivo="buzon")
    assert _nombres(_vista(db, "todos", orden="nombre")) == ["Alfa", "Bravo", "Charlie"]
    assert _nombres(_vista(db, "todos", orden="intentos")) == ["Bravo", "Charlie", "Alfa"]
    # Los que nunca se contactaron van al final en los dos sentidos.
    assert _nombres(_vista(db, "todos", orden="contacto_viejo")) == ["Bravo", "Charlie", "Alfa"]
    assert _nombres(_vista(db, "todos", orden="contacto_nuevo")) == ["Charlie", "Bravo", "Alfa"]
    assert b


def test_la_api_de_la_lista_acepta_vista_barrio_y_orden(cli, db):
    _p(db, "Alfa")
    d = cli.get("/api/fidelidad/lista?ciudad=Montevideo&rubro=restaurante&vista=sin_llamar&orden=nombre&barrio=Pocitos").get_json()
    assert d["vista"] == "sin_llamar" and d["orden"] == "nombre" and d["total"] == 1 and d["conteos"]["sin_llamar"] == 1


# ── 5. reunión y demo ────────────────────────────────────────────────────────

def test_la_ficha_de_reunion_y_demo(cli, db):
    pid = _p(db, "TNT Burger")
    r = cli.put(f"/api/fidelidad/prospectos/{pid}/seguimiento", json={
        "fecha_reunion": "2026-10-09T16:00", "con_quien": "Pablo", "cargo": "Dueño", "demo_marca": True,
        "enviado": "Presupuesto", "etapa": "negociando", "proximo_paso": "Cerrar precio",
        "proximo_paso_fecha": "2026-10-16"})
    assert r.status_code == 200
    p = r.get_json()["prospecto"]
    s = p["seguimiento"]
    assert (s["fecha_reunion"], s["con_quien"], s["demo_marca"], s["etapa"], s["proximo_paso_fecha"], s["vencido"]) == \
        ("2026-10-09 16:00", "Pablo", 1, "negociando", "2026-10-16", False)
    # El próximo paso queda como la próxima llamada: aparece en la cola ese día.
    assert p["proxima_llamada"] == "2026-10-16 11:00" and p["contacto"] == "Pablo"
    assert _nombres(_vista(db, "reunion")) == ["TNT Burger"]
    assert cli.put(f"/api/fidelidad/prospectos/{pid}/seguimiento", json={"etapa": "otra"}).status_code == 400


def test_avisa_cuando_el_proximo_paso_se_paso(db):
    pid = _p(db, "Los Sopranos")
    fid.guardar_seguimiento(db, pid, {"etapa": "demo_mostrada", "proximo_paso": "Llamar",
                                      "proximo_paso_fecha": "2026-10-13"}, "L", cuando=AHORA)
    d = _vista(db, "reunion")
    assert d["items"][0]["seguimiento"]["vencido"] and d["kpis"]["pasos_vencidos"] == 1


def test_cerrado_y_perdido_mueven_la_etapa_del_local(db):
    a, b = _p(db, "Cierra"), _p(db, "Se pierde")
    p, _ = fid.guardar_seguimiento(db, a, {"etapa": "cerrado"}, "L", cuando=AHORA)
    assert p["estado"] == "cerrado" and p["mensual_usd"] == 150
    p, _ = fid.guardar_seguimiento(db, b, {"etapa": "perdido"}, "L", cuando=AHORA)
    assert p["estado"] == "descartado" and p["motivo_descarte"] == "Perdido después de la demo"
    assert _vista(db, "reunion")["items"] == []
    assert _nombres(_vista(db, "descartados")) == ["Se pierde"]


def test_tnt_sopranos_y_rodelu_entran_solos_una_vez(db):
    ids = [_p(db, n) for n in ("TNT Burger", "Sopranos", "Rodelú", "Otro")]
    c = sqlite3.connect(db)
    fid.init_seguimiento(c)
    c.commit()
    c.close()
    assert _nombres(_vista(db, "reunion", orden="nombre")) == ["Rodelú", "Sopranos", "TNT Burger"]
    # Si lo sacan de la lista, el próximo arranque no lo vuelve a poner.
    fid.borrar_seguimiento(db, ids[0])
    c = sqlite3.connect(db)
    fid.init_seguimiento(c)
    c.commit()
    c.close()
    assert "TNT Burger" not in _nombres(_vista(db, "reunion"))


# ── la pantalla ──────────────────────────────────────────────────────────────

def _js_lista(nombre):
    m = re.search(r"const " + nombre + r" = \[(.*?)\];", dashboard.FID_JS, re.S)
    return re.findall(r"\['(\w+)','", m.group(1))


def test_la_pantalla_usa_las_mismas_claves_que_el_servidor():
    assert _js_lista("FID_MOTIVOS") == list(fid.MOTIVOS_NO_CONTACTO)
    assert _js_lista("FID_VISTAS") == list(fid.VISTAS)
    assert _js_lista("FID_ETAPAS") == list(fid.ETAPAS_SEGUIMIENTO)
    assert _js_lista("FID_ORDENES") == list(fid.ORDENES)
    assert set(_js_lista("FID_ACC")) == set(fid.ACCIONES)
