"""Publicacion automatica de los borradores de LinkedIn en la pagina de Scalerics.

Decision de Juan (4/11/2026): los posteos de LinkedIn salen solos, sin
aprobacion, como los reels. Y "tenemos que ser muy profesionales con la
publicacion del contenido". De ahi las reglas:

- Horario fijo de oficina: lunes, martes, jueves y viernes a las 10:00 de
  Montevideo, uno por vez. El cron de GitHub arma dos borradores el martes y
  dos el viernes a las 08:00: el martes sale el 1, el jueves el 2, el viernes
  el 3 y el lunes siguiente el 4. Si el CRM estuvo caido y se pasa la ventana
  (3 horas), ese turno no sale: nada de publicar a la noche.
- Sale en orden, el mas viejo primero, y solo lo que entro despues de conectar
  la pagina: lo de semanas anteriores pudo haberse subido a mano.
- Antes de publicar se revisa todo y, si algo no esta bien, ese borrador no
  sale (y se avisa por mail): reglas de voz (validar_borrador), largo de
  LinkedIn, imagen (siempre con tarjeta), sin un pedido de correccion en
  espera y sin repetir un texto ya publicado.
- Nunca dos veces: la fila se toma (estado 'publicando') antes de llamar a
  LinkedIn. Si LinkedIn falla, el borrador queda con el error y fuera de la
  publicacion automatica; no se reintenta solo, porque un corte a mitad de
  camino puede haber publicado igual.
- Se puede pausar desde el panel, y LINKEDIN_PUBLICAR=off apaga el hilo.

La conexion es un token de un admin de la pagina (OAuth, boton "Conectar
LinkedIn" del panel). Dura unos 60 dias: si LinkedIn da refresh token se
renueva solo; si no, se avisa por mail una semana antes de que venza.
"""

import logging
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests

from services import linkedin_borradores as lb
from services.corridas import marcar_corrida, puede_correr
from services.linkedin_api import Cliente, LinkedInError, url_del_post

logger = logging.getLogger(__name__)

DIAS_DE_PUBLICACION = (0, 1, 3, 4)  # lunes, martes, jueves, viernes
HORA_DE_PUBLICACION = 10
VENTANA_HORAS = 3
AVISO_VENCE_DIAS = 7
RENOVAR_DIAS = 10
_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_FORMATO = "%Y-%m-%d %H:%M:%S"

# La pagina de Scalerics. El token de Juan tambien es admin de otras paginas
# (Discountsuy): por eso se fija y no se elige "la primera".
ORG_SCALERICS = "urn:li:organization:139414000"
SCOPES = "w_organization_social r_organization_social rw_organization_admin"
AUTORIZAR = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN = "https://www.linkedin.com/oauth/v2/accessToken"

def activo() -> bool:
    return os.environ.get("LINKEDIN_PUBLICAR", "on").strip().lower() not in ("off", "0", "no")


def org_urn() -> str:
    return os.environ.get("LINKEDIN_ORG", "").strip() or ORG_SCALERICS


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _txt(momento: datetime) -> str:
    return momento.astimezone(timezone.utc).strftime(_FORMATO)


def _de_txt(texto) -> datetime | None:
    try:
        return datetime.strptime(str(texto), _FORMATO).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# ── conexion ─────────────────────────────────────────────────────────────────

def conexion(db_path: str) -> dict | None:
    conn = _conn(db_path)
    try:
        fila = conn.execute("SELECT * FROM linkedin_conexion WHERE id = 1").fetchone()
    finally:
        conn.close()
    return dict(fila) if fila else None


def guardar_conexion(db_path: str, token: dict, org: str, org_nombre: str, quien: str,
                     ahora: datetime | None = None) -> None:
    """Guarda el token. La fecha de activacion queda la primera: reconectar no la mueve."""
    ahora = ahora or lb.ahora_utc()
    vence = ahora + timedelta(seconds=int(token.get("expires_in") or 0))
    refresh_vence = (ahora + timedelta(seconds=int(token["refresh_token_expires_in"]))
                     if token.get("refresh_token_expires_in") else None)
    conn = _conn(db_path)
    try:
        conn.execute(
            "INSERT INTO linkedin_conexion (id, access_token, vence_en, refresh_token, refresh_vence_en, "
            "org_urn, org_nombre, conectado_por, conectado_en, activado_en) VALUES (1,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET access_token = excluded.access_token, vence_en = excluded.vence_en, "
            "refresh_token = COALESCE(excluded.refresh_token, linkedin_conexion.refresh_token), "
            "refresh_vence_en = COALESCE(excluded.refresh_vence_en, linkedin_conexion.refresh_vence_en), "
            "org_urn = excluded.org_urn, org_nombre = excluded.org_nombre, "
            "conectado_por = excluded.conectado_por, conectado_en = excluded.conectado_en",
            (token["access_token"], _txt(vence), token.get("refresh_token"),
             _txt(refresh_vence) if refresh_vence else None, org, org_nombre, quien,
             _txt(ahora), _txt(ahora)))
        conn.commit()
    finally:
        conn.close()


def pausar(db_path: str, pausado: bool) -> bool:
    conn = _conn(db_path)
    try:
        n = conn.execute("UPDATE linkedin_conexion SET pausado = ? WHERE id = 1", (1 if pausado else 0,)).rowcount
        conn.commit()
    finally:
        conn.close()
    return bool(n)


def _credenciales() -> tuple[str, str]:
    return (os.environ.get("LINKEDIN_CLIENT_ID", "").strip(),
            os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip())


def url_autorizar(redirect_uri: str, state: str) -> str:
    client_id, _ = _credenciales()
    return AUTORIZAR + "?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "state": state, "scope": SCOPES})


def nuevo_state() -> str:
    return secrets.token_urlsafe(24)


def canjear_codigo(code: str, redirect_uri: str, http=requests) -> dict:
    client_id, secret = _credenciales()
    if not (client_id and secret):
        raise LinkedInError("Falta LINKEDIN_CLIENT_ID o LINKEDIN_CLIENT_SECRET en el servidor")
    r = http.post(TOKEN, data={"grant_type": "authorization_code", "code": code,
                               "redirect_uri": redirect_uri, "client_id": client_id,
                               "client_secret": secret}, timeout=30)
    if r.status_code >= 400:
        raise LinkedInError(f"LinkedIn no acepto el codigo: {r.status_code} {r.text[:300]}", r.status_code)
    return r.json()


def conectar(db_path: str, code: str, redirect_uri: str, quien: str, http=requests,
             cliente=None, ahora: datetime | None = None) -> str:
    """Canjea el codigo, confirma que el usuario es admin de la pagina y guarda. Devuelve el nombre."""
    token = canjear_codigo(code, redirect_uri, http)
    cli = cliente or Cliente(token["access_token"])
    org = org_urn()
    if org not in cli.paginas_admin():
        raise LinkedInError("Esa cuenta de LinkedIn no es administradora de la página de Scalerics")
    nombre = cli.nombre_pagina(org)
    guardar_conexion(db_path, token, org, nombre, quien, ahora)
    return nombre


def _renovar(db_path: str, con: dict, http=requests, ahora: datetime | None = None) -> dict:
    """Si LinkedIn dio refresh token y faltan menos de RENOVAR_DIAS, se renueva."""
    ahora = ahora or lb.ahora_utc()
    vence = _de_txt(con["vence_en"])
    if not con.get("refresh_token") or not vence or vence - ahora > timedelta(days=RENOVAR_DIAS):
        return con
    client_id, secret = _credenciales()
    if not (client_id and secret):
        return con
    try:
        r = http.post(TOKEN, data={"grant_type": "refresh_token", "refresh_token": con["refresh_token"],
                                   "client_id": client_id, "client_secret": secret}, timeout=30)
        r.raise_for_status()
        guardar_conexion(db_path, r.json(), con["org_urn"], con.get("org_nombre") or "",
                         con.get("conectado_por") or "", ahora)
        logger.info("LinkedIn: token renovado")
        return conexion(db_path) or con
    except Exception as e:
        logger.warning(f"LinkedIn: no se pudo renovar el token ({e})")
        return con


# ── turnos ───────────────────────────────────────────────────────────────────

def turno_de(momento: datetime) -> datetime:
    """El turno de publicacion de ese dia (en UTC), o None si ese dia no se publica."""
    local = lb.a_montevideo(momento)
    if local.weekday() not in DIAS_DE_PUBLICACION:
        return None
    return lb.MVD.localize(datetime(local.year, local.month, local.day,
                                    HORA_DE_PUBLICACION)).astimezone(timezone.utc)


def turno_abierto(ahora: datetime) -> datetime | None:
    turno = turno_de(ahora)
    if turno and turno <= ahora < turno + timedelta(hours=VENTANA_HORAS):
        return turno
    return None


def proximo_turno(ahora: datetime) -> datetime:
    for dias in range(0, 8):
        turno = turno_de(ahora + timedelta(days=dias))
        if turno and turno + timedelta(hours=VENTANA_HORAS) > ahora:
            return turno
    raise AssertionError("siempre hay un turno en la semana")


def describir_turno(turno: datetime) -> str:
    local = lb.a_montevideo(turno)
    return f"{_DIAS[local.weekday()]} {local:%d/%m} a las {local:%H:%M}"


# ── que sale ─────────────────────────────────────────────────────────────────

def candidatos(db_path: str, activado_en: str) -> list[dict]:
    """Borradores en fila para salir, el mas viejo primero."""
    conn = _conn(db_path)
    try:
        filas = conn.execute(
            "SELECT id, semana, orden, tema, texto, frase, imagen_png IS NOT NULL AS tiene_imagen "
            "FROM linkedin_borradores WHERE estado = 'borrador' AND error_pub IS NULL "
            "AND created_at >= ? ORDER BY created_at, semana, orden", (activado_en,)).fetchall()
    finally:
        conn.close()
    return [dict(f) for f in filas]


def problemas(db_path: str, b: dict) -> list[str]:
    """Por que este borrador no puede salir. Lista vacia = esta listo."""
    from services.linkedin_posts import validar_borrador

    texto = b.get("texto") or ""
    salida = []
    if not texto.strip():
        return ["el texto está vacío"]
    if len(texto) > lb.LIMITE_LINKEDIN:
        salida.append(f"tiene {len(texto)} caracteres y LinkedIn acepta {lb.LIMITE_LINKEDIN}")
    salida += validar_borrador(texto)
    conn = _conn(db_path)
    try:
        if conn.execute("SELECT 1 FROM linkedin_correcciones WHERE borrador_id = ? AND estado = 'pendiente'",
                        (b["id"],)).fetchone():
            salida.append("tiene un pedido de corrección sin resolver")
        if conn.execute("SELECT 1 FROM linkedin_borradores WHERE estado = 'publicado' AND id <> ? "
                        "AND trim(texto) = trim(?)", (b["id"], texto)).fetchone():
            salida.append("ese mismo texto ya se publicó")
    finally:
        conn.close()
    return salida


def siguiente(db_path: str, activado_en: str) -> tuple[dict | None, list[tuple[dict, list[str]]]]:
    """El proximo que puede salir y los que se saltean por algun problema.

    Uno con un pedido de correccion en espera no se marca como error: se
    saltea esta vez y sale cuando Claude lo resuelva.
    """
    salteados = []
    for b in candidatos(db_path, activado_en):
        motivos = problemas(db_path, b)
        if not motivos:
            return b, salteados
        salteados.append((b, motivos))
    return None, salteados


def _imagen(db_path: str, b: dict) -> bytes | None:
    if not b.get("tiene_imagen"):
        try:
            lb.dibujar_faltantes(db_path, b["semana"])
        except Exception as e:
            logger.warning(f"LinkedIn: no se pudo dibujar la tarjeta de {b['id']} ({e})")
    hay = lb.imagen_de(db_path, b["id"])
    return hay[0] if hay else None


def _marcar_error(db_path: str, borrador_id: int, motivo: str) -> None:
    conn = _conn(db_path)
    try:
        conn.execute("UPDATE linkedin_borradores SET estado = 'borrador', error_pub = ? WHERE id = ?",
                     (motivo[:500], borrador_id))
        conn.commit()
    finally:
        conn.close()


def publicar_uno(db_path: str, b: dict, con: dict, cliente=None, ahora: datetime | None = None) -> dict:
    """Publica un borrador ya revisado. Devuelve {'id', 'estado', 'url'|'error'}."""
    ahora = ahora or lb.ahora_utc()
    conn = _conn(db_path)
    try:
        tomada = conn.execute("UPDATE linkedin_borradores SET estado = 'publicando' "
                              "WHERE id = ? AND estado = 'borrador' AND error_pub IS NULL", (b["id"],)).rowcount
        conn.commit()
    finally:
        conn.close()
    if not tomada:
        return {"id": b["id"], "estado": "ya_tomado"}

    png = _imagen(db_path, b)
    if not png:
        _marcar_error(db_path, b["id"], "no tiene imagen y no se pudo dibujar")
        return {"id": b["id"], "estado": "error", "error": "no tiene imagen y no se pudo dibujar"}
    cli = cliente or Cliente(con["access_token"])
    try:
        imagen_urn = cli.subir_imagen(con["org_urn"], png)
        frase = (b.get("frase") or b.get("tema") or "").strip()
        post_urn = cli.publicar(con["org_urn"], b["texto"], imagen_urn, alt=frase)
    except Exception as e:
        motivo = str(e)[:500]
        logger.error(f"LinkedIn: no se pudo publicar el borrador {b['id']}: {motivo}")
        _marcar_error(db_path, b["id"], motivo)
        return {"id": b["id"], "estado": "error", "error": motivo}

    conn = _conn(db_path)
    try:
        conn.execute("UPDATE linkedin_borradores SET linkedin_urn = ? WHERE id = ?", (post_urn, b["id"]))
        conn.commit()
    finally:
        conn.close()
    lb.cambiar_estado(db_path, b["id"], "publicado", ahora=ahora)
    logger.info(f"LinkedIn: publicado el borrador {b['id']} ({post_urn})")
    return {"id": b["id"], "estado": "publicado", "url": url_del_post(post_urn) if post_urn else ""}


# ── rutina ───────────────────────────────────────────────────────────────────

def _avisar_vencimiento(db_path: str, con: dict, ahora: datetime, avisar) -> None:
    vence = _de_txt(con["vence_en"])
    if not vence or vence - ahora > timedelta(days=AVISO_VENCE_DIAS):
        return
    if puede_correr(db_path, "linkedin_pub_aviso_vence", 24 * 2):
        marcar_corrida(db_path, "linkedin_pub_aviso_vence")
        avisar("vence", vence=vence)


def rutina(db_path: str, ahora: datetime | None = None, cliente=None, avisar=None, http=requests) -> dict:
    """Lo que hace el hilo en cada vuelta."""
    from services.email_service import send_linkedin_publicacion

    ahora = ahora or lb.ahora_utc()
    avisar = avisar or send_linkedin_publicacion
    con = conexion(db_path)
    if not con:
        return {"estado": "sin_conectar"}
    con = _renovar(db_path, con, http, ahora)
    _avisar_vencimiento(db_path, con, ahora, avisar)
    if con["pausado"]:
        return {"estado": "pausado"}
    vence = _de_txt(con["vence_en"])
    if vence and vence <= ahora:
        return {"estado": "vencido"}

    turno = turno_abierto(ahora)
    if not turno:
        return {"estado": "fuera_de_turno"}
    marca = f"linkedin_pub_{lb.a_montevideo(turno):%Y-%m-%d}"
    if not puede_correr(db_path, marca, 24 * 3):
        return {"estado": "turno_hecho"}

    b, salteados = siguiente(db_path, con["activado_en"])
    for s, motivos in salteados:
        if any("pedido de corrección" in m for m in motivos) and len(motivos) == 1:
            continue
        _marcar_error(db_path, s["id"], "; ".join(motivos))
        try:
            avisar("salteado", borrador=s, motivo="; ".join(motivos))
        except Exception:
            pass
    if not b:
        # Sin nada para publicar el turno queda abierto: si un borrador entra
        # dentro de la ventana, sale en la proxima vuelta.
        return {"estado": "sin_borradores"}

    marcar_corrida(db_path, marca)
    r = publicar_uno(db_path, b, con, cliente, ahora)
    if r["estado"] == "error":
        try:
            avisar("error", borrador=b, motivo=r["error"])
        except Exception:
            pass
    return r


def estado_panel(db_path: str, ahora: datetime | None = None) -> dict:
    """Lo que muestra el panel arriba de los borradores."""
    ahora = ahora or lb.ahora_utc()
    con = conexion(db_path)
    salida = {"activo": activo(), "conectado": bool(con), "dias": "lunes, martes, jueves y viernes",
              "hora": f"{HORA_DE_PUBLICACION:02d}:00"}
    if not con:
        return salida
    vence = _de_txt(con["vence_en"])
    salida.update({
        "pagina": con.get("org_nombre") or "",
        "pausado": bool(con["pausado"]),
        "vence_en": lb.a_montevideo(vence).date().isoformat() if vence else None,
        "vencido": bool(vence and vence <= ahora),
        "se_renueva": bool(con.get("refresh_token")),
        "proximo_turno": describir_turno(proximo_turno(ahora)),
    })
    b, _ = siguiente(db_path, con["activado_en"])
    salida["proximo"] = {"id": b["id"], "orden": b["orden"], "tema": b["tema"]} if b else None
    return salida


def start_linkedin_publicador(app) -> None:
    if not activo():
        logger.info("LinkedIn: publicacion automatica apagada por LINKEDIN_PUBLICAR=off")
        return
    db_path = app.config["DB_PATH"]

    def _loop():
        time.sleep(240)
        while True:
            try:
                r = rutina(db_path)
                if r.get("estado") in ("publicado", "error"):
                    logger.info(f"LinkedIn: {r}")
            except Exception as e:
                logger.warning(f"LinkedIn publicador: {e}")
            time.sleep(600)

    threading.Thread(target=_loop, daemon=True, name="linkedin-publicador").start()
    logger.info("LinkedIn ACTIVO: publica lun/mar/jue/vie a las 10:00 si la pagina esta conectada")
