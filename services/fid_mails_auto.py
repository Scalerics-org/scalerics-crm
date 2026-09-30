"""Mails automáticos en frío a los restaurantes de la lista de Fidelidad (30/9).

Juan: mandarles por Resend, como a los comercios de discovery, ofreciendo el
sistema de puntos «estilo McDonald's» y pidiendo una videollamada. Firma Juan,
sin link a la demo (se la hacemos en la llamada), ofreciendo armarles un
prototipo, y la llamada se coordina respondiendo el mail.

Copia el molde de discovery_emails a propósito y no lo generaliza: allá la
cohorte vive en `businesses` y acá en `fid_prospectos`, y tocar la única
campaña en frío que anda para meterle esta es arriesgar las dos.

Las guardas son las mismas:
- cuatro contactos (hoy, a los 15 días, al mes y a los tres meses: `ESPERA_ANTES_DE`),
  solo si nadie del equipo lo tocó; sin respuesta, se descarta;
- el cupo que queda libre en el plan gratis de Resend (`cupo_del_dia`) y marca de corrida (`corridas`),
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
from services.email_service import (ASUNTO_FID_1, ASUNTO_FID_2, ASUNTO_FID_MES, ASUNTO_FID_ULTIMO,
                                    contexto_envio,
                                    send_fidelidad_email)

logger = logging.getLogger(__name__)

_FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"
USUARIO = "Mail automático"
NOMBRE_CORRIDA = "fid_mails_auto"

# ─── Cuánto se puede mandar sin pagar (Juan, 30/9) ───────────────────────────
# «La máxima cantidad posible por día sin gastar plata»: el plan gratis de
# Resend da 100 mails por día y 3.000 por mes, compartidos con los
# recordatorios de Meta, discovery y los avisos del CRM. El cupo de esta
# campaña es lo que queda libre, calculado cada día (`cupo_del_dia`):
#   - por día: 100, menos lo que ya salió en 24 h, menos lo que las otras
#     campañas todavía tienen que mandar hoy (su reserva), menos un margen para
#     los avisos del CRM que caen a cualquier hora;
#   - por mes: lo que queda de los 3.000, menos lo que las otras van a gastar
#     el resto del mes, repartido en los días que faltan. Es el que manda: con
#     discovery prendido deja unos 30 por día.
# Y una rampa: el subdominio es el mismo de discovery, y pasar de golpe a 80
# por día es lo que dispara los filtros de spam. Arranca en `RAMPA_INICIAL` y
# sube `RAMPA_POR_DIA` por cada día de envíos.
CUOTA_DIA = 100
CUOTA_MES = 3000
MARGEN_DIA = 8
MARGEN_MES = 150
RAMPA_INICIAL = 25
RAMPA_POR_DIA = 5
# Lo que cada campaña manda en un día normal, para reservárselo aunque todavía
# no haya salido. Discovery solo si está prendida.
_RESERVAS = {"recordatorio_meta": 20, "discovery": 50}
# La secuencia (Juan, 30/9): uno, a los 15 días otro, al mes otro y a los tres
# meses el último. Días de espera ANTES de cada mail, contados desde el
# anterior. Si después del último no contesta, se descarta.
ESPERA_ANTES_DE = {2: 15, 3: 30, 4: 90}
TOTAL_CONTACTOS = 4
# Lo que se le da para contestar el último antes de descartarlo.
DIAS_PARA_DESCARTAR = 15
MOTIVO_DESCARTE = "No respondió los mails"
# Entre el tercer y el cuarto mail pasan 90 días: las respuestas se buscan más
# atrás que eso, o alguien que contestó el tercero recibe igual el último.
DIAS_DE_RESPUESTAS = 120
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


def _reservas() -> dict:
    r = dict(_RESERVAS)
    if os.environ.get("DISCOVERY_EMAILS", "").strip().lower() != "on":
        r.pop("discovery")
    return r


def cupo_del_dia(db_path: str, ahora: datetime | None = None) -> dict:
    """Cuántos mails puede mandar esta campaña por día, y cuántos le quedan hoy.

    Cuenta todo lo que salió por Resend desde `emails_enviados`, que registra
    cada envío del CRM sea de la campaña que sea. Ante la duda se queda corto:
    pasarse de la cuota es que Resend rechace mails de Meta, que son de gente
    que pidió que la contacten.
    """
    ahora = ahora or datetime.now(timezone.utc)
    reservas = _reservas()
    conn = _conn(db_path)
    try:
        por_tipo = dict(conn.execute(
            "SELECT tipo, COUNT(*) FROM emails_enviados "
            "WHERE enviado_at >= datetime(?, '-1 day') GROUP BY tipo",
            (ahora.strftime(_FORMATO_FECHA),)).fetchall())
        mes_total = conn.execute(
            "SELECT COUNT(*) FROM emails_enviados WHERE substr(enviado_at, 1, 7) = ?",
            (ahora.strftime("%Y-%m"),)).fetchone()[0]
        # Lo que gastan las otras en un día, medido en la última semana.
        otros_semana = conn.execute(
            "SELECT COUNT(*) FROM emails_enviados WHERE tipo <> 'fidelidad' "
            "AND enviado_at >= datetime(?, '-7 days')", (ahora.strftime(_FORMATO_FECHA),)).fetchone()[0]
        primero = conn.execute("SELECT MIN(sent_at) FROM fid_mails_auto").fetchone()[0]
        # La reserva de cada campaña es lo más que mandó en un día de la última
        # semana, no un número fijo: el 30/9 la reserva fija de 20 para Meta,
        # que ese día no mandó ninguno, dejaba a esta campaña en 8.
        maximos = dict(conn.execute(
            "SELECT tipo, MAX(n) FROM (SELECT tipo, date(enviado_at) AS d, COUNT(*) AS n "
            "FROM emails_enviados WHERE enviado_at >= datetime(?, '-7 days') GROUP BY tipo, d) "
            "GROUP BY tipo", (ahora.strftime(_FORMATO_FECHA),)).fetchall())
    finally:
        conn.close()
    reservas = {t: min(r, maximos.get(t, 0)) for t, r in reservas.items()}
    fid_24h = enviados_ultimas_24h(db_path)
    otros_24h = sum(n for t, n in por_tipo.items() if t != "fidelidad")
    pendiente_hoy = sum(max(0, r - por_tipo.get(t, 0)) for t, r in reservas.items())
    libre_dia = CUOTA_DIA - MARGEN_DIA - otros_24h - pendiente_hoy

    # El mes de Resend se toma como mes calendario: es lo más conservador.
    if ahora.month == 12:
        fin_mes = datetime(ahora.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        fin_mes = datetime(ahora.year, ahora.month + 1, 1, tzinfo=timezone.utc)
    dias_restantes = max(1, (fin_mes.date() - ahora.date()).days)
    otros_por_dia = max(otros_semana / 7, sum(reservas.values()))
    libre_mes = (CUOTA_MES - MARGEN_MES - mes_total - otros_por_dia * (dias_restantes - 1)) / dias_restantes
    # Lo de fidelidad de hoy ya está adentro de mes_total: se lo devuelvo al día.
    libre_mes += min(fid_24h, mes_total)

    dias_enviando = 0
    if primero:
        try:
            desde = datetime.strptime(primero, _FORMATO_FECHA).replace(tzinfo=timezone.utc)
            dias_enviando = max(0, (ahora - desde).days)
        except ValueError:
            pass
    rampa = RAMPA_INICIAL + RAMPA_POR_DIA * dias_enviando

    # `libre_dia` no descuenta lo de esta campaña (otros_24h la deja afuera): ya
    # es todo lo que le toca en la ventana de 24 horas.
    topes = (("día", libre_dia), ("mes", libre_mes), ("rampa", rampa))
    por_dia = int(max(0, min(t for _, t in topes)))
    return {"por_dia": por_dia, "quedan_hoy": max(0, por_dia - fid_24h), "enviados_24h": fid_24h,
            "limita": min(topes, key=lambda x: x[1])[0]}


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
    """Los que no contestaron y ya les toca el siguiente mail de la secuencia
    (`ESPERA_ANTES_DE`): el 2 a los 15 días del 1, el 3 al mes del 2 y el 4 a
    los tres meses del 3. Los más atrasados primero.

    La veda se mira de nuevo: entre un mail y otro pueden haber pedido la baja
    o haber rebotado.
    """
    casos = " OR ".join(
        f"(MAX(a.numero) = {n - 1} AND MAX(a.sent_at) <= datetime('now', '-{d} days'))"
        for n, d in ESPERA_ANTES_DE.items())
    conn = _conn(db_path)
    try:
        filas = conn.execute(f"""
            SELECT p.id, p.nombre, MAX(a.email) AS email, MAX(a.numero) AS ultimo_numero,
                   MAX(a.sent_at) AS ultimo
              FROM fid_prospectos p
              JOIN fid_mails_auto a ON a.prospecto_id = p.id
             WHERE {_SIN_TOCAR}
          GROUP BY p.id
            HAVING ({casos})
               AND MAX(a.respondio_at) IS NULL AND MAX(a.unsubscribed_at) IS NULL
               AND MAX(a.email) NOT IN ({_VEDADAS})
          ORDER BY ultimo ASC
             LIMIT ?""", (int(limite),)).fetchall()
    finally:
        conn.close()
    return [{"id": f["id"], "nombre": f["nombre"] or "", "email": f["email"],
             "numero": f["ultimo_numero"] + 1} for f in filas]


def a_descartar(db_path: str) -> list[int]:
    """Los que recibieron el último mail hace `DIAS_PARA_DESCARTAR` y no
    contestaron: Juan (30/9), «si no, se descarta»."""
    conn = _conn(db_path)
    try:
        filas = conn.execute(f"""
            SELECT p.id
              FROM fid_prospectos p
              JOIN fid_mails_auto a ON a.prospecto_id = p.id
             WHERE {_SIN_TOCAR}
          GROUP BY p.id
            HAVING MAX(a.numero) >= {TOTAL_CONTACTOS}
               AND MAX(a.sent_at) <= datetime('now', '-{DIAS_PARA_DESCARTAR} days')
               AND MAX(a.respondio_at) IS NULL AND MAX(a.unsubscribed_at) IS NULL""").fetchall()
    finally:
        conn.close()
    return [f["id"] for f in filas]


def descartar_sin_respuesta(db_path: str) -> int:
    from services import fidelidad as fid
    ids = a_descartar(db_path)
    for pid in ids:
        fid.mover_estado(db_path, pid, "descartado", USUARIO, motivo=MOTIVO_DESCARTE)
        fid.agregar_nota(db_path, pid, f"Se descartó: no respondió ninguno de los "
                                       f"{TOTAL_CONTACTOS} mails automáticos.", USUARIO)
    return len(ids)


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
    asunto = (ASUNTO_FID_1 if numero <= 1 else ASUNTO_FID_2 if numero == 2 else
              ASUNTO_FID_MES if numero == 3 else ASUNTO_FID_ULTIMO).format(n=nombre or "tu restaurante")
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
    for a in (ASUNTO_FID_1, ASUNTO_FID_2, ASUNTO_FID_MES, ASUNTO_FID_ULTIMO)
)
# Gmail no entiende regex: los pedazos fijos de cada asunto, para encontrar al
# dueño que contesta desde otra casilla.
_FRAGMENTOS = ("sistema de puntos como el de McDonald", "Sobre los puntos para", "armamos el prototipo para",
              "Último mail sobre los puntos")
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


def _contactados(db_path: str, dias: int = DIAS_DE_RESPUESTAS) -> tuple[dict, dict, dict]:
    """({mail: pid}, {nombre: pid}, {pid: último envío}) de los que esperan respuesta.
    Un nombre repetido no se usa: marcar al local equivocado es peor que no marcar."""
    conn = _conn(db_path)
    try:
        filas = conn.execute("""
            SELECT a.prospecto_id AS pid, a.email, LOWER(TRIM(p.nombre)) AS nombre,
                   MAX(a.sent_at) AS ultimo
              FROM fid_mails_auto a JOIN fid_prospectos p ON p.id = a.prospecto_id
             WHERE a.sent_at >= datetime('now', '-' || ? || ' days')
          GROUP BY a.prospecto_id
            HAVING MAX(a.respondio_at) IS NULL AND MAX(a.unsubscribed_at) IS NULL
        """, (int(dias),)).fetchall()
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


def sincronizar_respuestas(db_path: str, buscar, days_back: int = DIAS_DE_RESPUESTAS) -> dict:
    """`buscar(query)` devuelve mensajes {"from", "subject", "date", "headers"},
    igual que en discovery_respuestas (y en producción es la misma función)."""
    from services.discovery_respuestas import es_respuesta_automatica, partir_en_consultas
    por_mail, por_nombre, ultimo = _contactados(db_path, days_back)
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
    """Una tanda: primero los seguimientos que vencen, después los nuevos.

    Los seguimientos primero porque uno a destiempo pierde sentido. La cuenta:
    cada restaurante recibe cuatro mails, así que con el cupo lleno entran unos
    nuevos por día igual a un cuarto del cupo.
    """
    res = {"candidatos": 0, "enviados": 0, "fallidos": 0, "inciertos": 0,
           "seguimientos": 0, "nuevos": 0}
    c = cupo_del_dia(db_path)
    cupo = c["por_dia"] if dry_run else c["quedan_hoy"]
    res["cupo"] = cupo
    if cupo <= 0:
        return res
    seguir = a_seguir(db_path, cupo)
    resto = cupo - len(seguir)
    nuevos = a_contactar(db_path, resto) if resto > 0 else []
    lista = seguir + nuevos
    res.update(candidatos=len(lista), seguimientos=len(seguir), nuevos=len(nuevos))
    if dry_run:
        res["lista"] = lista
        return res

    for p in lista:
        if enviados_ultimas_24h(db_path) >= c["por_dia"]:
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


# Juan (30/9): «no puede parar». El buscador de GitHub Actions trae
# restaurantes y mails nuevos todos los días; si igual se queda sin a quién
# escribirle, se avisa a los admins, como mucho cada tres días.
_HORAS_ENTRE_AVISOS = 72


def avisar_si_se_quedo_sin_cola(db_path: str, res: dict) -> bool:
    if res.get("candidatos") or res.get("cupo", 0) <= 0:
        return False
    if not puede_correr(db_path, "fid_mails_sin_cola", cada_horas=_HORAS_ENTRE_AVISOS):
        return False
    from services.discovery_respuestas import _admins
    from services.email_service import send_fidelidad_alerta
    marcar_corrida(db_path, "fid_mails_sin_cola")
    webs = len(webs_sin_buscar(db_path, 100000))
    texto = ("Los mails automáticos a restaurantes no tuvieron a quién escribirle hoy: no queda "
             "ningún restaurante sin contactar con mail. "
             f"Hay {webs} restaurantes con web a los que todavía no se les buscó el mail. "
             "El buscador de GitHub Actions («Fidelidad: captación») corre todos los días y trae "
             "más; si esto se repite, fijate si está fallando.")
    avisados = 0
    for direccion in _admins(db_path):
        try:
            avisados += bool(send_fidelidad_alerta(direccion, "Mails a restaurantes: se quedó sin cola", texto))
        except Exception as e:
            logger.warning(f"Fidelidad: no se pudo avisar a {direccion}: {e}")
    return avisados > 0


def tanda_diaria(db_path: str, base_url: str):
    """Una revisión del hilo: la tanda de hoy, o None si no le toca."""
    if not puede_correr(db_path, NOMBRE_CORRIDA):
        return None
    if cupo_del_dia(db_path)["quedan_hoy"] <= 0:
        return None
    # Las respuestas antes que los envíos: si alguien contestó ayer y su
    # segundo mail vence hoy, hay que frenarlo antes de que salga.
    try:
        logger.info(f"Fidelidad respuestas: {sincronizar_desde_gmail(db_path)}")
    except Exception as e:
        logger.warning(f"Fidelidad respuestas: {e}")
    marcar_corrida(db_path, NOMBRE_CORRIDA)
    try:
        descartados = descartar_sin_respuesta(db_path)
        if descartados:
            logger.info(f"Fidelidad: {descartados} descartados por no responder los {TOTAL_CONTACTOS} mails")
    except Exception as e:
        logger.warning(f"Fidelidad: no se pudo descartar a los que no respondieron: {e}")
    res = enviar(db_path, base_url)
    try:
        avisar_si_se_quedo_sin_cola(db_path, res)
    except Exception as e:
        logger.warning(f"Fidelidad: no se pudo revisar la cola: {e}")
    return res


# ─── Lo que usa el buscador de GitHub Actions ────────────────────────────────
# Fly no tiene navegador: buscar mails en las webs y restaurantes en Maps corre
# en GitHub Actions (scripts/fid_captacion.py), que habla con el CRM por la API.

def webs_sin_buscar(db_path: str, limite: int) -> list[dict]:
    """Restaurantes con web y sin mail a los que nunca se les buscó. Los de más
    reseñas primero, que son a los que más conviene escribirles."""
    conn = _conn(db_path)
    try:
        filas = conn.execute(f"""
            SELECT id, web FROM fid_prospectos
             WHERE archivado = 0 AND rubro = 'restaurante' AND ciudad IN ({", ".join("?" * len(CIUDADES))})
               AND COALESCE(web, '') <> '' AND COALESCE(email, '') = '' AND mail_buscado_en IS NULL
          ORDER BY COALESCE(resenas, 0) DESC, id ASC LIMIT ?""", (*CIUDADES, int(limite))).fetchall()
    finally:
        conn.close()
    return [{"id": f["id"], "web": f["web"]} for f in filas]


def guardar_mails_encontrados(db_path: str, resultados: list) -> dict:
    """Lo que trae el buscador: [{"id", "email" o null, "abrio"}]. Igual que
    `fidelidad.buscar_mails`: un sitio que no abrió no se marca, así se
    reintenta; uno que abrió y no tenía mail se marca para no volver."""
    from services import fidelidad as fid
    cuenta = {"encontrados": 0, "sin_mail": 0, "no_abrio": 0, "ignorados": 0}
    conn = _conn(db_path)
    try:
        for r in resultados or []:
            try:
                pid = int(r.get("id"))
            except (TypeError, ValueError, AttributeError):
                cuenta["ignorados"] += 1
                continue
            mail = (r.get("email") or "").strip().lower()
            if mail and not fid.es_mail(mail):
                mail = ""
            if mail:
                cur = conn.execute("UPDATE fid_prospectos SET email = ?, mail_buscado_en = ? "
                                   "WHERE id = ? AND COALESCE(email, '') = ''",
                                   (mail, fid.fmt(fid.ahora()), pid))
                cuenta["encontrados" if cur.rowcount else "ignorados"] += 1
            elif r.get("abrio"):
                conn.execute("UPDATE fid_prospectos SET mail_buscado_en = ? WHERE id = ?",
                             (fid.fmt(fid.ahora()), pid))
                cuenta["sin_mail"] += 1
            else:
                cuenta["no_abrio"] += 1
        conn.commit()
    finally:
        conn.close()
    return cuenta


def estado(db_path: str) -> dict:
    """Para el panel, para mirar antes de prender, y para que el buscador de
    GitHub decida si hace falta traer restaurantes nuevos."""
    conn = _conn(db_path)
    try:
        f = conn.execute("""
            SELECT COUNT(DISTINCT prospecto_id) AS locales,
                   SUM(numero = 1) AS primeros, SUM(numero = 2) AS segundos, SUM(numero >= 3) AS terceros_o_mas,
                   COUNT(DISTINCT CASE WHEN respondio_at IS NOT NULL THEN prospecto_id END) AS respondieron,
                   COUNT(DISTINCT CASE WHEN unsubscribed_at IS NOT NULL THEN email END) AS bajas
              FROM fid_mails_auto""").fetchone()
    finally:
        conn.close()
    pendientes = a_contactar(db_path, 100000)
    cupo = cupo_del_dia(db_path)
    return {"activo": os.environ.get("FID_MAILS_AUTO", "").strip().lower() == "on",
            "tope_diario": cupo["por_dia"], "cupo": cupo, "ultimas_24h": enviados_ultimas_24h(db_path),
            **{k: f[k] or 0 for k in f.keys()},
            "sin_contactar_con_mail": len(pendientes),
            "webs_sin_buscar": len(webs_sin_buscar(db_path, 100000)),
            # Días de envío que quedan con lo que hay: es lo que mira el buscador.
            "dias_de_cola": len(pendientes) // max(1, cupo["por_dia"] or RAMPA_INICIAL),
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
            "tope_diario": cupo_del_dia(db_path)["por_dia"], "en_cola": len(a_contactar(db_path, 100000)),
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
    logger.info("Mails automáticos de Fidelidad ACTIVOS: lo que quede libre del plan gratis de Resend")
