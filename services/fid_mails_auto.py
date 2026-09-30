"""Mails automáticos en frío a los restaurantes de la lista de Fidelidad (30/9).

Juan: mandarles por Resend, como a los comercios de discovery, ofreciendo el
sistema de puntos «estilo McDonald's» y pidiendo una videollamada. Firma Juan,
sin link a la demo (se la hacemos en la llamada), ofreciendo armarles un
prototipo, y la llamada se coordina respondiendo el mail.

Copia el molde de discovery_emails a propósito y no lo generaliza: allá la
cohorte vive en `businesses` y acá en `fid_prospectos`, y tocar la única
campaña en frío que anda para meterle esta es arriesgar las dos.

Las guardas son las mismas:
- dos contactos, el segundo a los `DIAS_SEGUNDO` días y solo si nadie lo tocó;
- tope rodante de `TOPE_DIARIO` en 24 horas y marca de corrida (`corridas`),
  para que un deploy no sea una tanda;
- apagado salvo `FID_MAILS_AUTO=on`;
- las respuestas se leen de Gmail ANTES de mandar, para no escribirle encima a
  quien ya contestó;
- lo que está en `mails_vedados`, las bajas de cualquier campaña y los leads y
  clientes de la agencia no reciben nada.

Quien responde pasa a «Contactado» con la llamada para hoy, así aparece en la
lista del vendedor para coordinar la videollamada.
"""

import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

from services.corridas import marcar_corrida, puede_correr, siguiente_revision
from services.email_service import (ASUNTO_FID_1, ASUNTO_FID_2, contexto_envio,
                                    send_fidelidad_email)

logger = logging.getLogger(__name__)

_FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"
USUARIO = "Mail automático"
NOMBRE_CORRIDA = "fid_mails_auto"

# Juan (30/9): arrancar con lo que queda libre de la cuota gratis de Resend
# (100 por día: 50 de discovery, ~15 de Meta y las notificaciones del CRM).
# Además es un subdominio que ya tiene historia: sumarle 25 no es un salto.
TOPE_DIARIO = 25
TOTAL_CONTACTOS = 2
DIAS_SEGUNDO = 4
CIUDADES = ("Montevideo", "Buenos Aires")
_PAUSA_ENTRE_ENVIOS = 0.6
# Meta arranca a los 180 s y discovery a los 600 s de cada boot; esta va a los
# 900 para no mandar a la vez que ellas contra el límite de 2 por segundo de
# Resend. Las tres usan la misma grilla horaria, así que el desfase se mantiene.
_RETRASO_INICIAL_S = 900


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime(_FORMATO_FECHA)


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ─── A quién le toca ─────────────────────────────────────────────────────────

# Nadie del equipo lo tocó: ni una llamada, ni una visita, ni un mail a mano.
# Si el vendedor ya está hablando con el local, un mail automático lo pisa.
_SIN_TOCAR = """
    p.archivado = 0
    AND p.estado = 'sin_contactar'
    AND p.rubro = 'restaurante'
    AND p.ciudad IN ({ciudades})
    AND p.email LIKE '%_@_%._%' AND p.email NOT LIKE '% %'
    AND NOT EXISTS (SELECT 1 FROM fid_llamadas l WHERE l.prospecto_id = p.id)
    AND NOT EXISTS (SELECT 1 FROM fid_visitas v WHERE v.prospecto_id = p.id)
    AND NOT EXISTS (SELECT 1 FROM fid_mails m WHERE m.prospecto_id = p.id
                     AND COALESCE(m.usuario, '') <> '{usuario}')
""".format(ciudades=", ".join(f"'{c}'" for c in CIUDADES), usuario=USUARIO)

# Direcciones que no reciben nada de esta campaña:
#   1. las vedadas (rebotes, spam, bajas pedidas por mail);
#   2. las de los leads y clientes de la agencia, que tienen su propio correo;
#   3. las que ya recibieron el mail en frío de discovery: dos campañas en frío
#      de Scalerics a la misma casilla es lo que termina en «spam»;
#   4. las de quien se dio de baja de cualquier campaña.
_VEDADAS = """
    SELECT LOWER(TRIM(email)) FROM mails_vedados
    UNION
    SELECT LOWER(TRIM(b.email)) FROM businesses b
     WHERE b.email IS NOT NULL AND COALESCE(b.source, '') <> 'discovery'
    UNION
    SELECT LOWER(TRIM(b.email)) FROM businesses b
      JOIN discovery_reminders dr ON dr.business_id = b.id
     WHERE b.email IS NOT NULL
    UNION
    SELECT LOWER(TRIM(b.email)) FROM businesses b
      JOIN meta_reminders mr ON mr.business_id = b.id
     WHERE mr.unsubscribed_at IS NOT NULL AND b.email IS NOT NULL
    UNION
    SELECT LOWER(TRIM(email)) FROM fid_mails_auto WHERE unsubscribed_at IS NOT NULL
"""


def enviados_ultimas_24h(db_path: str) -> int:
    conn = _conn(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM fid_mails_auto "
                            "WHERE sent_at >= datetime('now', '-1 day')").fetchone()[0]
    finally:
        conn.close()


def a_contactar(db_path: str, limite: int) -> list[dict]:
    """Los que nunca recibieron nada. Los de más reseñas primero: son los que
    más clientes tienen para fidelizar.

    El GROUP BY por dirección es para las cadenas: dos locales con la misma
    casilla reciben un solo mail.
    """
    conn = _conn(db_path)
    try:
        filas = conn.execute(f"""
            SELECT p.id, p.nombre, LOWER(TRIM(p.email)) AS email,
                   MAX(COALESCE(p.resenas, 0)) AS resenas
              FROM fid_prospectos p
             WHERE {_SIN_TOCAR}
               AND NOT EXISTS (SELECT 1 FROM fid_mails_auto a WHERE a.prospecto_id = p.id)
               AND LOWER(TRIM(p.email)) NOT IN (SELECT email FROM fid_mails_auto)
               AND LOWER(TRIM(p.email)) NOT IN ({_VEDADAS})
          GROUP BY LOWER(TRIM(p.email))
          ORDER BY resenas DESC, p.id ASC
             LIMIT ?""", (int(limite),)).fetchall()
    finally:
        conn.close()
    return [{"id": f["id"], "nombre": f["nombre"] or "", "email": f["email"], "numero": 1}
            for f in filas]


def a_seguir(db_path: str, limite: int) -> list[dict]:
    """Los que recibieron el primero hace `DIAS_SEGUNDO` días y no contestaron.

    La veda se mira de nuevo: entre un mail y otro pueden haber pedido la baja
    o haber rebotado.
    """
    conn = _conn(db_path)
    try:
        filas = conn.execute(f"""
            SELECT p.id, p.nombre, a.email
              FROM fid_prospectos p
              JOIN fid_mails_auto a ON a.prospecto_id = p.id AND a.numero = 1
             WHERE {_SIN_TOCAR}
               AND a.sent_at <= datetime('now', '-{DIAS_SEGUNDO} days')
               AND a.respondio_at IS NULL AND a.unsubscribed_at IS NULL
               AND NOT EXISTS (SELECT 1 FROM fid_mails_auto x
                                WHERE x.prospecto_id = p.id AND x.numero >= 2)
               AND a.email NOT IN ({_VEDADAS})
          ORDER BY a.sent_at ASC
             LIMIT ?""", (int(limite),)).fetchall()
    finally:
        conn.close()
    return [{"id": f["id"], "nombre": f["nombre"] or "", "email": f["email"], "numero": 2}
            for f in filas]


# ─── Registro y baja ─────────────────────────────────────────────────────────

def registrar_envio(db_path: str, pid: int, numero: int, email: str) -> str:
    """Deja la fila ANTES de mandar (un corte en el medio no puede terminar en
    un mail repetido) y devuelve el token de la baja."""
    token = uuid.uuid4().hex + uuid.uuid4().hex[:8]
    conn = _conn(db_path)
    try:
        conn.execute("INSERT INTO fid_mails_auto (prospecto_id, numero, email, token, sent_at) "
                     "VALUES (?, ?, ?, ?, ?)", (pid, int(numero), email, token, _ahora()))
        conn.commit()
    finally:
        conn.close()
    return token


def _anotar_en_historial(db_path: str, pid: int, email: str, numero: int, nombre: str) -> None:
    """El mail queda en el historial del local, como los que se mandan a mano,
    pero SIN la llamada de seguimiento a los dos días: con 25 por día le
    llenaría la lista al vendedor de locales que no contestaron."""
    asunto = (ASUNTO_FID_1 if numero <= 1 else ASUNTO_FID_2).format(n=nombre or "tu restaurante")
    de = (os.environ.get("FID_FROM_EMAIL") or os.environ.get("DISCOVERY_FROM_EMAIL") or "").strip()
    conn = _conn(db_path)
    try:
        conn.execute("INSERT INTO fid_mails (prospecto_id, enviado_en, usuario, de, para, asunto) "
                     "VALUES (?,?,?,?,?,?)",
                     (pid, datetime.now().strftime("%Y-%m-%d %H:%M"), USUARIO, de, email, asunto))
        conn.commit()
    finally:
        conn.close()


def dar_de_baja(db_path: str, token: str) -> bool:
    """La baja por link. Veda la dirección para todas las campañas: quien dice
    basta no distingue por cuál le escribíamos."""
    conn = _conn(db_path)
    try:
        fila = conn.execute("SELECT email FROM fid_mails_auto WHERE token = ?", (token,)).fetchone()
        if not fila:
            return False
        conn.execute("UPDATE fid_mails_auto SET unsubscribed_at = ? "
                     "WHERE email = ? AND unsubscribed_at IS NULL", (_ahora(), fila["email"]))
        conn.commit()
    finally:
        conn.close()
    from services.mails_vedados import vedar
    vedar(db_path, fila["email"], "baja_pedida", "link de baja de Fidelidad")
    return True


# ─── Respuestas ──────────────────────────────────────────────────────────────

_PATRONES_ASUNTO = tuple(
    re.compile("^" + re.escape(a.lower()).replace(re.escape("{n}"), "(?P<n>.+)") + "$")
    for a in (ASUNTO_FID_1, ASUNTO_FID_2)
)
# Gmail no entiende regex: los pedazos fijos de cada asunto, para encontrar al
# dueño que contesta desde otra casilla.
_FRAGMENTOS = ("sistema de puntos como el de McDonald", "Último mail sobre los puntos para")
_PREFIJOS = ("re:", "rv:", "fwd:", "fw:")


def negocio_del_asunto(asunto: str | None) -> str:
    texto = " ".join((asunto or "").split()).lower()
    cambio = True
    while cambio:
        cambio = False
        for p in _PREFIJOS:
            if texto.startswith(p):
                texto, cambio = texto[len(p):].strip(), True
    for patron in _PATRONES_ASUNTO:
        if m := patron.match(texto):
            return m.group("n").strip()
    return ""


def _contactados(db_path: str) -> tuple[dict, dict, dict]:
    """({mail: pid}, {nombre: pid}, {pid: último envío}) de los que esperan respuesta.
    Un nombre repetido no se usa: marcar al local equivocado es peor que no marcar."""
    conn = _conn(db_path)
    try:
        filas = conn.execute("""
            SELECT a.prospecto_id AS pid, a.email, LOWER(TRIM(p.nombre)) AS nombre,
                   MAX(a.sent_at) AS ultimo
              FROM fid_mails_auto a JOIN fid_prospectos p ON p.id = a.prospecto_id
             WHERE a.sent_at >= datetime('now', '-30 days')
          GROUP BY a.prospecto_id
            HAVING MAX(a.respondio_at) IS NULL AND MAX(a.unsubscribed_at) IS NULL
        """).fetchall()
    finally:
        conn.close()
    por_mail = {f["email"]: f["pid"] for f in filas}
    cuenta, por_nombre = {}, {}
    for f in filas:
        if f["nombre"]:
            cuenta[f["nombre"]] = cuenta.get(f["nombre"], 0) + 1
            por_nombre[f["nombre"]] = f["pid"]
    return (por_mail, {n: p for n, p in por_nombre.items() if cuenta[n] == 1},
            {f["pid"]: f["ultimo"] for f in filas})


def marcar_respuesta(db_path: str, pid: int, direccion: str, asunto: str) -> str:
    """Frena la secuencia. Si pide la baja, la ejecuta; si no, pasa a Contactado
    con la llamada para hoy, para que el vendedor coordine la videollamada."""
    from services import fidelidad as fid
    from services.discovery_respuestas import pide_la_baja
    conn = _conn(db_path)
    try:
        conn.execute("UPDATE fid_mails_auto SET respondio_at = ? WHERE prospecto_id = ?",
                     (_ahora(), pid))
        conn.commit()
    finally:
        conn.close()
    if pide_la_baja(asunto):
        from services.mails_vedados import vedar
        p = fid.get_prospecto(db_path, pid) or {}
        vedar(db_path, direccion or p.get("email"), "baja_pedida", (asunto or "")[:200])
        fid.agregar_nota(db_path, pid, "Pidió que no le escribamos más (respondió el mail automático).", USUARIO)
        return "baja"
    p = fid.get_prospecto(db_path, pid) or {}
    if p.get("estado") == "sin_contactar":
        fid.mover_estado(db_path, pid, "contactado", USUARIO)
    if p.get("estado") in ("sin_contactar", "contactado"):
        conn = _conn(db_path)
        try:
            conn.execute("UPDATE fid_prospectos SET proxima_llamada = ? WHERE id = ?",
                         (fid.fmt(fid.ahora()), pid))
            conn.commit()
        finally:
            conn.close()
    fid.agregar_nota(db_path, pid, f"Respondió el mail automático desde {direccion or 'otra casilla'}: "
                                   f"coordinar la videollamada.", USUARIO)
    return "respondio"


def sincronizar_respuestas(db_path: str, buscar, days_back: int = 30) -> dict:
    """`buscar(query)` devuelve mensajes {"from", "subject", "date", "headers"},
    igual que en discovery_respuestas (y en producción es la misma función)."""
    from services.discovery_respuestas import es_respuesta_automatica, partir_en_consultas
    por_mail, por_nombre, ultimo = _contactados(db_path)
    res = {"revisados": len(por_mail), "respondieron": 0, "bajas": 0}
    if not por_mail:
        return res
    consultas = partir_en_consultas(sorted(por_mail), days_back=days_back) + [
        f'in:anywhere newer_than:{days_back}d subject:"{f}" -from:scalerics.com' for f in _FRAGMENTOS]
    hechos = set()
    for consulta in consultas:
        try:
            mensajes = buscar(consulta) or []
        except Exception as e:
            logger.warning(f"Fidelidad respuestas: falló una consulta: {e}")
            continue
        for msg in mensajes:
            direccion = (msg.get("from") or "").strip().lower()
            if direccion.endswith("scalerics.com"):
                continue
            asunto = msg.get("subject") or ""
            pid = por_mail.get(direccion) or por_nombre.get(negocio_del_asunto(asunto))
            if pid is None or pid in hechos or es_respuesta_automatica(msg.get("headers"), asunto):
                continue
            fecha = (msg.get("date") or "").strip()
            if fecha and ultimo.get(pid) and fecha < ultimo[pid]:
                continue    # nos escribió antes del último mail: no es respuesta
            hechos.add(pid)
            que = marcar_respuesta(db_path, pid, direccion, asunto)
            res["bajas" if que == "baja" else "respondieron"] += 1
    return res


def sincronizar_desde_gmail(db_path: str) -> dict:
    """Contra la casilla de verdad: contacto@ reenvía a la misma Gmail que lee
    discovery, con las mismas credenciales."""
    from google.oauth2.credentials import Credentials
    from services.discovery_respuestas import buscar_con_gmail
    from services.google_api import get_service
    ids = [os.environ.get(k, "") for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")]
    if not all(ids):
        raise RuntimeError("Faltan GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET o GMAIL_REFRESH_TOKEN")
    creds = Credentials(None, refresh_token=ids[2], token_uri="https://oauth2.googleapis.com/token",
                        client_id=ids[0], client_secret=ids[1])
    return sincronizar_respuestas(db_path, buscar_con_gmail(get_service("gmail", "v1", creds, account="gmail")))


# ─── La tanda ────────────────────────────────────────────────────────────────

def enviar(db_path: str, base_url: str, dry_run: bool = False) -> dict:
    """Una tanda: primero los segundos contactos, después los nuevos."""
    res = {"candidatos": 0, "enviados": 0, "fallidos": 0, "inciertos": 0, "seguimientos": 0, "nuevos": 0}
    cupo = TOPE_DIARIO if dry_run else max(0, TOPE_DIARIO - enviados_ultimas_24h(db_path))
    if cupo <= 0:
        return res
    seguir = a_seguir(db_path, cupo)
    nuevos = a_contactar(db_path, cupo - len(seguir)) if cupo > len(seguir) else []
    res.update(candidatos=len(seguir) + len(nuevos), seguimientos=len(seguir), nuevos=len(nuevos))
    if dry_run:
        res["lista"] = seguir + nuevos
        return res

    for p in seguir + nuevos:
        if enviados_ultimas_24h(db_path) >= TOPE_DIARIO:
            break
        try:
            token = registrar_envio(db_path, p["id"], p["numero"], p["email"])
        except sqlite3.Error as e:
            logger.warning(f"Fidelidad: no se pudo registrar {p['id']} contacto {p['numero']}: {e}")
            continue
        with contexto_envio(numero=p["numero"]):
            estado = send_fidelidad_email(p["email"], p["nombre"],
                                          f"{base_url.rstrip('/')}/baja/{token}", p["numero"])
        if estado == "fallo":
            # Sabemos que no salió: se borra esa fila puntual para reintentar.
            conn = _conn(db_path)
            try:
                conn.execute("DELETE FROM fid_mails_auto WHERE token = ?", (token,))
                conn.commit()
            finally:
                conn.close()
            res["fallidos"] += 1
        else:
            # "desconocido" pudo haber salido: la fila se queda, reintentar sería mandar dos veces.
            res["enviados" if estado == "ok" else "inciertos"] += 1
            try:
                _anotar_en_historial(db_path, p["id"], p["email"], p["numero"], p["nombre"])
            except sqlite3.Error as e:
                logger.warning(f"Fidelidad: no se anotó el mail en el historial de {p['id']}: {e}")
        time.sleep(_PAUSA_ENTRE_ENVIOS)
    logger.info(f"Fidelidad mails: {res}")
    return res


def tanda_diaria(db_path: str, base_url: str):
    """Una revisión del hilo: la tanda de hoy, o None si no le toca."""
    if not puede_correr(db_path, NOMBRE_CORRIDA):
        return None
    if enviados_ultimas_24h(db_path) >= TOPE_DIARIO:
        return None
    # Las respuestas antes que los envíos: si alguien contestó ayer y su
    # segundo mail vence hoy, hay que frenarlo antes de que salga.
    try:
        logger.info(f"Fidelidad respuestas: {sincronizar_desde_gmail(db_path)}")
    except Exception as e:
        logger.warning(f"Fidelidad respuestas: {e}")
    marcar_corrida(db_path, NOMBRE_CORRIDA)
    return enviar(db_path, base_url)


def estado(db_path: str) -> dict:
    """Para el panel y para mirar antes de prender: cómo va y a quién le toca."""
    conn = _conn(db_path)
    try:
        f = conn.execute("""
            SELECT COUNT(DISTINCT prospecto_id) AS locales,
                   SUM(numero = 1) AS primeros, SUM(numero = 2) AS segundos,
                   COUNT(DISTINCT CASE WHEN respondio_at IS NOT NULL THEN prospecto_id END) AS respondieron,
                   COUNT(DISTINCT CASE WHEN unsubscribed_at IS NOT NULL THEN email END) AS bajas
              FROM fid_mails_auto""").fetchone()
    finally:
        conn.close()
    pendientes = a_contactar(db_path, 100000)
    return {"activo": os.environ.get("FID_MAILS_AUTO", "").strip().lower() == "on",
            "tope_diario": TOPE_DIARIO, "ultimas_24h": enviados_ultimas_24h(db_path),
            **{k: f[k] or 0 for k in f.keys()},
            "sin_contactar_con_mail": len(pendientes),
            "proximos": [{"nombre": p["nombre"], "email": p["email"]} for p in pendientes[:10]]}


# ─── La sección de Captación (Juan, 30/9) ────────────────────────────────────
# «Email marketing» dentro de Captación: solo los mails a restaurantes. El
# estado de Resend (entregado, abierto, rebote) sale de `emails_enviados`, que
# llena el webhook; se cruza por dirección y número de contacto, que en esta
# campaña no se repiten.

# Montevideo no tiene horario de verano desde 2015: siempre UTC-3.
_A_MVD = "'-3 hours'"


def _estado_de(f) -> tuple[str, str]:
    """(clave, texto) de un envío: lo más importante que le pasó."""
    if f["respondio_at"]:
        return "respondio", "Respondió"
    if f["unsubscribed_at"]:
        return "baja", "Pidió la baja"
    if f["spam_at"]:
        return "spam", "Marcó spam"
    if f["rebotado_at"]:
        return "rebote", "Rebotó"
    if f["abierto_at"] or f["clic_at"]:
        return "abierto", "Abierto"
    if f["entregado_at"]:
        return "entregado", "Entregado"
    return "enviado", "Enviado"


def panel(db_path: str, mes: str | None = None, ciudad: str | None = None) -> dict:
    """Contadores y lista de envíos del mes (`AAAA-MM`, hora de Montevideo)."""
    if not mes or not re.fullmatch(r"\d{4}-\d{2}", mes):
        mes = datetime.now(timezone.utc).strftime("%Y-%m")
    filtros, args = [f"strftime('%Y-%m', datetime(a.sent_at, {_A_MVD})) = ?"], [mes]
    if ciudad in CIUDADES:
        filtros.append("p.ciudad = ?")
        args.append(ciudad)
    conn = _conn(db_path)
    try:
        filas = conn.execute(f"""
            SELECT a.id, a.numero, a.email, a.respondio_at, a.unsubscribed_at,
                   strftime('%d/%m %H:%M', datetime(a.sent_at, {_A_MVD})) AS fecha,
                   p.id AS pid, p.nombre, p.ciudad,
                   e.entregado_at, e.abierto_at, e.clic_at, e.rebotado_at, e.spam_at
              FROM fid_mails_auto a
              JOIN fid_prospectos p ON p.id = a.prospecto_id
         LEFT JOIN emails_enviados e ON e.id = (
                     SELECT MAX(x.id) FROM emails_enviados x
                      WHERE x.tipo = 'fidelidad' AND x.numero = a.numero
                        AND LOWER(TRIM(x.destinatario)) = a.email)
             WHERE {' AND '.join(filtros)}
          ORDER BY a.sent_at DESC, a.id DESC""", args).fetchall()
    finally:
        conn.close()
    envios, cuenta = [], {"enviados": 0, "abiertos": 0, "respondieron": 0, "bajas_rebotes": 0}
    for f in filas:
        clave, texto = _estado_de(f)
        cuenta["enviados"] += 1
        cuenta["abiertos"] += clave in ("abierto", "respondio") or bool(f["abierto_at"] or f["clic_at"])
        cuenta["respondieron"] += clave == "respondio"
        cuenta["bajas_rebotes"] += clave in ("baja", "spam", "rebote")
        envios.append({"id": f["id"], "fecha": f["fecha"], "restaurante": f["nombre"] or "",
                       "prospecto_id": f["pid"], "ciudad": f["ciudad"] or "", "email": f["email"],
                       "numero": f["numero"], "estado": clave, "estado_texto": texto})
    return {"mes": mes, "ciudad": ciudad if ciudad in CIUDADES else "",
            "activo": os.environ.get("FID_MAILS_AUTO", "").strip().lower() == "on",
            "tope_diario": TOPE_DIARIO, "en_cola": len(a_contactar(db_path, 100000)),
            **cuenta, "envios": envios}


def mail_enviado(db_path: str, envio_id: int, base_url: str) -> dict | None:
    """El mail de un envío tal como salió, rearmado con la misma función."""
    from services.email_service import armar_fidelidad_email
    conn = _conn(db_path)
    try:
        f = conn.execute(f"""
            SELECT a.numero, a.email, a.token, p.id AS pid, p.nombre,
                   strftime('%d/%m/%Y %H:%M', datetime(a.sent_at, {_A_MVD})) AS fecha
              FROM fid_mails_auto a JOIN fid_prospectos p ON p.id = a.prospecto_id
             WHERE a.id = ?""", (int(envio_id),)).fetchone()
    finally:
        conn.close()
    if not f:
        return None
    # El link de baja de la vista no es el real: abrir el mail desde el CRM no
    # puede dar de baja a nadie por un clic distraído.
    asunto, html_mail, texto = armar_fidelidad_email(f["nombre"], f"{base_url.rstrip('/')}/baja/…", f["numero"])
    return {"asunto": asunto, "html": html_mail, "text": texto, "destinatario": f["email"],
            "fecha": f["fecha"], "numero": f["numero"], "prospecto_id": f["pid"], "restaurante": f["nombre"]}


def start_fid_mails_auto(app) -> None:
    """Revisa cada hora si toca la tanda. Arranca SOLO con FID_MAILS_AUTO=on."""
    if os.environ.get("FID_MAILS_AUTO", "").strip().lower() != "on":
        logger.info("Mails automáticos de Fidelidad apagados (FID_MAILS_AUTO=on los prende)")
        return

    def _loop():
        time.sleep(_RETRASO_INICIAL_S)
        turno = time.monotonic()
        while True:
            try:
                with app.app_context():
                    tanda_diaria(app.config["DB_PATH"],
                                 os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev"))
            except Exception as e:
                logger.warning(f"Fidelidad mails: {e}")
            turno = siguiente_revision(turno, time.monotonic())
            time.sleep(max(0.0, turno - time.monotonic()))

    threading.Thread(target=_loop, daemon=True, name="fid-mails-auto").start()
    logger.info(f"Mails automáticos de Fidelidad ACTIVOS: hasta {TOPE_DIARIO} por día")
