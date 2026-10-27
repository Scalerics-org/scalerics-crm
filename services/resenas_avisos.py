"""Aviso al comercio cuando entra una queja (Reseñas, 27/10).

El panel del comercio (/comercio/<token>) sirve si el dueño entra. Una queja
vale sobre todo en la primera hora: el cliente todavía está cerca, se lo puede
llamar y se evita la estrella pública. Por eso, como Birdeye o Podium, cada
persona con link puede dejar su mail y elegir cómo quiere enterarse:

- `al_momento`: un mail cuando entra una queja (1 a 3 estrellas, o un pedido
  de contacto) o cuando la lectura diaria trae una reseña de 1 a 3 estrellas
  en Google, con la respuesta sugerida y el botón para responderla. Si entran
  varias juntas van en el mismo mail, como las notificaciones agrupadas de
  Linear;
- `diario`: un resumen a las 9 con lo de ayer y lo que sigue sin atender;
- `no`: nada. El panel sigue andando igual.

El mail trae un link firmado al panel: el token de la persona no se puede
poner porque en la base solo está su hash. La firma (HMAC con la SECRET_KEY)
dice qué acceso es y hasta cuándo vale; si el acceso se revoca, el link firmado
se corta igual que el otro.

Topes, porque esto le manda correo a gente de afuera y corre en un hilo que
arranca en cada boot (regla 3 de COORDINACION.md):
- una queja se avisa una sola vez por persona (tabla rs_avisos_op, con clave
  primaria: dos revisiones a la vez no la mandan dos veces); una reseña de
  Google, igual (rs_avisos_gr);
- de Google solo las publicadas en los últimos DIAS_GOOGLE días y leídas
  después de prender el aviso: la primera lectura de un comercio trae
  reseñas viejas y esas no son una urgencia;
- solo quejas que entraron después de que esa persona prendió los avisos y de
  las últimas 48 horas: prender el aviso no manda el historial;
- se espera ESPERA_COMENTARIO minutos al comentario antes de avisar una queja
  sin texto, para no mandar "2 estrellas" y a los dos minutos el porqué;
- un mail por persona cada MINUTOS_ENTRE_MAILS como mínimo y MAX_POR_DIA por
  día; MAX_POR_HORA en total. Los fallidos también cuentan, así un error no
  se convierte en un reintento cada cinco minutos;
- de 22 a 8 (hora de Uruguay) no sale nada: se junta y sale a las 8.

RESENAS_AVISOS=off apaga el hilo. Sin RESEND_API_KEY, email_service no manda
(solo lo anota en el log), y en la vista previa y en los tests no hay hilo.
"""

import hashlib
import hmac
import html
import logging
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

from services import resenas as rs

logger = logging.getLogger(__name__)

MODOS = {"al_momento": "Al momento, con cada queja", "diario": "Un resumen por día, a las 9",
         "no": "No avisarme por mail"}

ESPERA_COMENTARIO = 15
VENTANA_HORAS = 48
MINUTOS_ENTRE_MAILS = 20
MAX_POR_DIA = 8
MAX_POR_HORA = 60
SILENCIO_DESDE = 22
SILENCIO_HASTA = 8
HORA_RESUMEN = 9
DIAS_LINK_FIRMADO = 30
DIAS_GOOGLE = 3
LARGO_EMAIL = 160

REVISAR_CADA_S = 300
_DEMORA_ARRANQUE_S = 90

EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_FIRMADO = re.compile(r"m(\d{1,9})-(\d{10})-([0-9a-f]{32})")
_lock = threading.Lock()
# La clave por defecto de dashboard.create_app está en el repo (que es
# público): con ella cualquiera podría firmar un link. Si el CRM corre sin
# SECRET_KEY, los links de los mails no valen y el hilo no manda nada.
_SECRETO_DE_DESARROLLO = "scalerics-dev-key-change-in-prod"


def secreto_usable(secreto: Optional[str]) -> bool:
    return bool(secreto) and secreto != _SECRETO_DE_DESARROLLO


def init_avisos(conn: sqlite3.Connection) -> None:
    columnas = {c[1] for c in conn.execute("PRAGMA table_info(rs_accesos)")}
    for col, tipo in (("email", "TEXT"), ("aviso", "TEXT NOT NULL DEFAULT 'no'"), ("aviso_desde", "TEXT")):
        if col not in columnas:
            conn.execute(f"ALTER TABLE rs_accesos ADD COLUMN {col} {tipo}")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS rs_avisos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            acceso_id INTEGER NOT NULL REFERENCES rs_accesos(id) ON DELETE CASCADE,
            tipo TEXT NOT NULL,
            cantidad INTEGER NOT NULL DEFAULT 0,
            estado TEXT NOT NULL DEFAULT 'enviando',
            creado_en TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_rs_avisos_acc ON rs_avisos (acceso_id, creado_en);
        CREATE TABLE IF NOT EXISTS rs_avisos_op (
            acceso_id INTEGER NOT NULL REFERENCES rs_accesos(id) ON DELETE CASCADE,
            opinion_id INTEGER NOT NULL REFERENCES rs_opiniones(id) ON DELETE CASCADE,
            aviso_id INTEGER NOT NULL REFERENCES rs_avisos(id) ON DELETE CASCADE,
            PRIMARY KEY (acceso_id, opinion_id)
        );
        CREATE TABLE IF NOT EXISTS rs_avisos_gr (
            acceso_id INTEGER NOT NULL REFERENCES rs_accesos(id) ON DELETE CASCADE,
            resena_id INTEGER NOT NULL REFERENCES rs_google_resenas(id) ON DELETE CASCADE,
            aviso_id INTEGER NOT NULL REFERENCES rs_avisos(id) ON DELETE CASCADE,
            PRIMARY KEY (acceso_id, resena_id)
        );
    """)


# ── preferencias ─────────────────────────────────────────────────────────────

def preferencias(db: str, aid: int) -> dict:
    conn = rs._con(db)
    try:
        f = conn.execute("SELECT email, aviso, aviso_desde FROM rs_accesos WHERE id = ?", (aid,)).fetchone()
        ult = conn.execute("SELECT creado_en, estado, tipo, cantidad FROM rs_avisos WHERE acceso_id = ? "
                           "ORDER BY id DESC LIMIT 1", (aid,)).fetchone()
    finally:
        conn.close()
    if not f:
        raise rs.NoSePuede("El acceso no existe.")
    return {"email": f["email"] or "", "aviso": f["aviso"] or "no", "aviso_desde": f["aviso_desde"],
            "ultimo": dict(ult) if ult else None}


def guardar_preferencias(db: str, aid: int, email, aviso, nid: Optional[int] = None) -> dict:
    """Guarda mail y modo. Prender (o cambiar de mail) corre `aviso_desde` a
    ahora: no se manda lo que entró antes."""
    email = (email or "").strip()[:LARGO_EMAIL]
    aviso = aviso if aviso in MODOS else "no"
    if email and not EMAIL.fullmatch(email):
        raise rs.NoSePuede("Ese mail no parece válido.")
    if aviso != "no" and not email:
        raise rs.NoSePuede("Para recibir avisos poné un mail.")
    conn = rs._con(db)
    try:
        f = conn.execute("SELECT negocio_id, email, aviso, aviso_desde FROM rs_accesos WHERE id = ? "
                         "AND revocado_en IS NULL", (aid,)).fetchone()
        if not f or (nid is not None and f["negocio_id"] != nid):
            raise rs.NoSePuede("El acceso no existe.")
        desde = f["aviso_desde"]
        if aviso != "no" and (f["aviso"] == "no" or (f["email"] or "") != email or not desde):
            desde = rs._txt(rs.ahora_uy())
        conn.execute("UPDATE rs_accesos SET email = ?, aviso = ?, aviso_desde = ? WHERE id = ?",
                     (email or None, aviso, desde if aviso != "no" else None, aid))
        conn.commit()
    finally:
        conn.close()
    return preferencias(db, aid)


# ── link firmado ─────────────────────────────────────────────────────────────

def _firma(aid: int, vence: int, secreto: str) -> str:
    return hmac.new(secreto.encode(), f"rs-aviso|{aid}|{vence}".encode(), hashlib.sha256).hexdigest()[:32]


def firmar(aid: int, secreto: str, ahora: Optional[datetime] = None) -> str:
    """Un token para el link del mail: `m<id>-<vence>-<firma>`, entra en [\\w-]."""
    ahora = ahora or rs.ahora_uy()
    vence = int((ahora + timedelta(days=DIAS_LINK_FIRMADO)).replace(tzinfo=None).timestamp())
    return f"m{aid}-{vence}-{_firma(aid, vence, secreto)}"


def acceso_firmado(token: str, secreto: str, ahora: Optional[datetime] = None) -> Optional[int]:
    """El id del acceso si la firma es buena y no venció; si no, None."""
    m = _FIRMADO.fullmatch(token or "")
    if not m or not secreto_usable(secreto):
        return None
    aid, vence = int(m.group(1)), int(m.group(2))
    if not hmac.compare_digest(m.group(3), _firma(aid, vence, secreto)):
        return None
    if (ahora or rs.ahora_uy()).replace(tzinfo=None).timestamp() > vence:
        return None
    return aid


# ── qué hay para avisar ──────────────────────────────────────────────────────

def _en_silencio(ahora: datetime) -> bool:
    return ahora.hour >= SILENCIO_DESDE or ahora.hour < SILENCIO_HASTA


def quejas_para_avisar(conn, a: dict, ahora: datetime) -> list:
    """Las quejas abiertas de su comercio que esa persona todavía no recibió."""
    desde = max(a["aviso_desde"] or "9999", rs._txt(ahora - timedelta(hours=VENTANA_HORAS)))
    sin_texto_hasta = rs._txt(ahora - timedelta(minutes=ESPERA_COMENTARIO))
    return [dict(f) for f in conn.execute(
        "SELECT o.id, o.puntaje, o.comentario, o.nombre, o.contacto, o.quiere_contacto, o.creado_en, "
        "f.nombre fuente FROM rs_opiniones o LEFT JOIN rs_fuentes f ON f.id = o.fuente_id "
        "WHERE o.negocio_id = ? AND o.estado = 'abierta' AND o.creado_en >= ? "
        "AND (o.comentario IS NOT NULL OR o.contacto IS NOT NULL OR o.creado_en <= ?) "
        "AND NOT EXISTS (SELECT 1 FROM rs_avisos_op x WHERE x.acceso_id = ? AND x.opinion_id = o.id) "
        "ORDER BY o.creado_en", (a["negocio_id"], desde, sin_texto_hasta, a["id"]))]


def google_para_avisar(conn, a: dict, ahora: datetime) -> list:
    """Las reseñas de Google de 1 a 3 estrellas, sin responder, que esa persona
    todavía no recibió. Leídas después de prender el aviso y publicadas hace
    poco: la primera lectura de un comercio trae reseñas de hace meses."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'rs_google_resenas'").fetchone():
        return []
    desde = max(a["aviso_desde"] or "9999", rs._txt(ahora - timedelta(hours=VENTANA_HORAS)))
    return [dict(f) for f in conn.execute(
        "SELECT g.id, g.rating, g.texto, g.autor, g.link, g.publicada_en, g.leida_en, g.sugerida "
        "FROM rs_google_resenas g WHERE g.negocio_id = ? AND g.estado = 'sin_responder' AND g.rating <= ? "
        "AND g.leida_en >= ? AND IFNULL(g.publicada_en, g.leida_en) >= ? "
        "AND NOT EXISTS (SELECT 1 FROM rs_avisos_gr x WHERE x.acceso_id = ? AND x.resena_id = g.id) "
        "ORDER BY IFNULL(g.publicada_en, g.leida_en)",
        (a["negocio_id"], rs.QUEJA_HASTA, desde, rs._txt(ahora - timedelta(days=DIAS_GOOGLE)), a["id"]))]


def resumen_de_ayer(conn, nid: int, ahora: datetime) -> dict:
    hoy = ahora.date().isoformat()
    ayer = (ahora.date() - timedelta(days=1)).isoformat()
    f = conn.execute("SELECT COUNT(*) c, AVG(puntaje) p, SUM(puntaje <= ?) q, SUM(fue_a_google_en IS NOT NULL) g "
                     "FROM rs_opiniones WHERE negocio_id = ? AND creado_en >= ? AND creado_en < ?",
                     (rs.QUEJA_HASTA, nid, ayer, hoy)).fetchone()
    pend = [dict(x) for x in conn.execute(
        "SELECT o.id, o.puntaje, o.comentario, o.nombre, o.contacto, o.quiere_contacto, o.creado_en, "
        "f.nombre fuente FROM rs_opiniones o LEFT JOIN rs_fuentes f ON f.id = o.fuente_id "
        "WHERE o.negocio_id = ? AND o.estado IN ('abierta', 'en_curso') ORDER BY o.creado_en DESC LIMIT 5", (nid,))]
    abiertas = conn.execute("SELECT COUNT(*) FROM rs_opiniones WHERE negocio_id = ? "
                            "AND estado IN ('abierta', 'en_curso')", (nid,)).fetchone()[0]
    return {"dia": ayer, "opiniones": f["c"] or 0, "promedio": round(f["p"], 1) if f["p"] else None,
            "quejas": f["q"] or 0, "google": f["g"] or 0, "pendientes": abiertas, "ultimas": pend}


def _cupo(conn, aid: int, ahora: datetime) -> bool:
    if conn.execute("SELECT COUNT(*) FROM rs_avisos WHERE creado_en >= ?",
                    (rs._txt(ahora - timedelta(hours=1)),)).fetchone()[0] >= MAX_POR_HORA:
        return False
    hoy = ahora.date().isoformat()
    if conn.execute("SELECT COUNT(*) FROM rs_avisos WHERE acceso_id = ? AND creado_en >= ?",
                    (aid, hoy)).fetchone()[0] >= MAX_POR_DIA:
        return False
    ult = conn.execute("SELECT MAX(creado_en) FROM rs_avisos WHERE acceso_id = ?", (aid,)).fetchone()[0]
    return not ult or rs._dt(ult) <= ahora - timedelta(minutes=MINUTOS_ENTRE_MAILS)


# ── el mail ──────────────────────────────────────────────────────────────────

def _fecha(s: str) -> str:
    return f"{s[8:10]}/{s[5:7]} {s[11:16]}"


def _tarjeta(o: dict) -> str:
    e = html.escape
    p = int(o["puntaje"])
    color = "#dc2626" if p <= 2 else "#d97706" if p == 3 else "#0088cc"
    origen = "QR " + e(o["fuente"]) if o.get("fuente") else "link directo"
    quien = " · ".join(e(x) for x in (o.get("nombre"), o.get("contacto")) if x)
    return (f'<div style="border:1px solid #e2e8f0;border-left:4px solid {color};border-radius:8px;'
            f'padding:12px 14px;margin:0 0 10px">'
            f'<div style="font-size:15px;color:{color};letter-spacing:1px">{"★" * p}'
            f'<span style="color:#cbd5e1">{"★" * (5 - p)}</span>'
            f'<span style="font-size:12px;color:#64748b;letter-spacing:0"> · {e(_fecha(o["creado_en"]))} · {origen}'
            f'</span></div>'
            + (f'<p style="margin:8px 0 0;font-size:14px;color:#0f172a">“{e(o["comentario"])}”</p>'
               if o.get("comentario") else '<p style="margin:8px 0 0;font-size:13px;color:#64748b">Sin comentario.</p>')
            + (f'<p style="margin:6px 0 0;font-size:13px;color:#334155">{quien}'
               + (' · <b>quiere que lo contacten</b>' if o.get("quiere_contacto") else '') + '</p>' if quien else '')
            + '</div>')


def _tarjeta_google(n: dict, g: dict) -> str:
    from services.resenas_google import link_responder
    e = html.escape
    p = int(g["rating"])
    color = "#dc2626" if p <= 2 else "#d97706"
    cuando = _fecha(g.get("publicada_en") or g["leida_en"])
    quien = (" · " + e(g["autor"])) if g.get("autor") else ""
    return (f'<div style="border:1px solid #e2e8f0;border-left:4px solid {color};border-radius:8px;'
            f'padding:12px 14px;margin:0 0 10px">'
            f'<div style="font-size:15px;color:{color};letter-spacing:1px">{"★" * p}'
            f'<span style="color:#cbd5e1">{"★" * (5 - p)}</span>'
            f'<span style="font-size:12px;color:#64748b;letter-spacing:0"> · {e(cuando)} · en Google{quien}'
            f'</span></div>'
            + (f'<p style="margin:8px 0 0;font-size:14px;color:#0f172a">“{e(g["texto"])}”</p>'
               if g.get("texto") else '<p style="margin:8px 0 0;font-size:13px;color:#64748b">Sin texto, solo estrellas.</p>')
            + (f'<p style="margin:10px 0 4px;font-size:12px;color:#64748b">Respuesta sugerida (ajustala si querés):</p>'
               f'<p style="margin:0;padding:8px 10px;background:#f1f5f9;border-radius:6px;font-size:13px;'
               f'color:#334155;white-space:pre-wrap">{e(g["sugerida"])}</p>' if g.get("sugerida") else '')
            + f'<p style="margin:10px 0 0;font-size:13px"><a href="{e(link_responder(n, g))}" '
              f'style="color:#0088cc;font-weight:600">Responder en Google</a></p>'
            + '</div>')


def _texto_plano(lineas: list, link: str) -> str:
    return "\n".join(lineas + ["", f"Abrí tu panel: {link}", "",
                               "Para dejar de recibir estos mails, cambialo en tu panel, en Avisos."])


def armar_mail_quejas(n: dict, a: dict, quejas: list, link: str, google: tuple = ()) -> dict:
    """Las quejas privadas y, si hay, las reseñas malas que entraron en Google."""
    from services.email_service import _layout
    from services.resenas_google import link_responder
    cant, cg = len(quejas), len(google)
    if not cg:
        asunto = (f"{n['nombre']}: una queja nueva para atender" if cant == 1
                  else f"{n['nombre']}: {cant} quejas nuevas para atender")
        titulo = "Hay que atender una queja" if cant == 1 else f"Hay {cant} quejas para atender"
    elif not cant:
        asunto = (f"{n['nombre']}: una reseña mala en Google para responder" if cg == 1
                  else f"{n['nombre']}: {cg} reseñas malas en Google para responder")
        titulo = "Hay una reseña para responder" if cg == 1 else f"Hay {cg} reseñas para responder"
    else:
        asunto = f"{n['nombre']}: {cant + cg} opiniones para atender, {cg} en Google"
        titulo = f"Hay {cant + cg} opiniones para atender"
    cuerpo = f'<p style="margin:0 0 16px;font-size:14px;color:#334155">Hola {html.escape(a["nombre"])}, '
    if cant:
        cuerpo += (("entró una opinión" if cant == 1 else f"entraron {cant} opiniones")
                   + " que conviene atender hoy. Llamar o escribir en la primera hora es lo que más evita "
                     "que termine en una reseña mala en Google.</p>"
                   + "".join(_tarjeta(o) for o in quejas))
    if cg:
        cuerpo += (('<p style="margin:20px 0 10px;font-size:14px;color:#334155">' if cant else "")
                   + ("En" if cant else "en")
                   + (" Google entró una reseña" if cg == 1 else f" Google entraron {cg} reseñas")
                   + " de pocas estrellas. Una respuesta amable y rápida la ve todo el que lea la reseña: "
                     "te dejamos una escrita para que la publiques.</p>"
                   + "".join(_tarjeta_google(n, g) for g in google))
    pie = ("Te llega porque pediste avisos de quejas en tu panel de opiniones. "
           f'<a href="{html.escape(link)}&amp;v=avisos" style="color:#64748b">Cambiar o apagar los avisos</a>.')
    lineas = [asunto, ""] + [f"- {o['puntaje']}/5 · {_fecha(o['creado_en'])}"
                             + (f": {o['comentario']}" if o.get("comentario") else "")
                             + (f" ({o['contacto']})" if o.get("contacto") else "") for o in quejas]
    lineas += [f"- Google {g['rating']}/5 · {_fecha(g.get('publicada_en') or g['leida_en'])}"
               + (f": {g['texto']}" if g.get("texto") else "") + f"\n  Responder: {link_responder(n, g)}"
               for g in google]
    return {"asunto": asunto, "texto": _texto_plano(lineas, link),
            "html": _layout("Opiniones · " + html.escape(n["nombre"]), titulo, cuerpo, link, "Abrir mi panel",
                            footer=pie)}


def armar_mail_resumen(n: dict, a: dict, r: dict, link: str) -> dict:
    from services.email_service import _info_card, _layout
    dia = r["dia"][8:10] + "/" + r["dia"][5:7]
    asunto = f"{n['nombre']}: tus opiniones del {dia}"
    filas = [("Opiniones", str(r["opiniones"])),
             ("Promedio", f"{str(r['promedio']).replace('.', ',')} ★" if r["promedio"] else "—"),
             ("Fueron a Google", str(r["google"])), ("Quejas nuevas", str(r["quejas"])),
             ("Para atender", str(r["pendientes"]))]
    cuerpo = (f'<p style="margin:0 0 16px;font-size:14px;color:#334155">Hola {html.escape(a["nombre"])}, '
              f"así te fue ayer.</p>" + _info_card(filas)
              + ('<p style="margin:20px 0 10px;font-size:14px;font-weight:600;color:#0f172a">Sin atender</p>'
                 + "".join(_tarjeta(o) for o in r["ultimas"]) if r["ultimas"] else ""))
    pie = ("Te llega porque pediste el resumen diario en tu panel de opiniones. "
           f'<a href="{html.escape(link)}&amp;v=avisos" style="color:#64748b">Cambiar o apagar los avisos</a>.')
    lineas = [asunto, ""] + [f"{k}: {v}" for k, v in filas]
    return {"asunto": asunto, "texto": _texto_plano(lineas, link),
            "html": _layout("Opiniones · " + html.escape(n["nombre"]), f"Tus opiniones del {dia}", cuerpo, link,
                            "Abrir mi panel", footer=pie)}


def link_panel(base: str, aid: int, secreto: str, ahora: Optional[datetime] = None) -> str:
    return f"{base.rstrip('/')}/comercio/{firmar(aid, secreto, ahora)}?desde=mail"


# ── la revisión ──────────────────────────────────────────────────────────────

Enviar = Callable[[str, str, str, str], str]


def _mandar_por_resend(to: str, asunto: str, cuerpo_html: str, texto: str) -> str:
    from services.email_service import _send_estado
    return _send_estado(to, asunto, cuerpo_html, text=texto)


def revisar(db: str, base: str, secreto: str, enviar: Optional[Enviar] = None,
            ahora: Optional[datetime] = None) -> dict:
    """Una pasada: arma y manda lo que corresponde. Devuelve qué hizo.

    Se reserva antes de mandar (BEGIN IMMEDIATE + rs_avisos_op): si dos
    pasadas se cruzan, la segunda no encuentra nada. Si el envío falla queda
    `fallo` y cuenta para el tope; la queja no se vuelve a mandar.
    """
    enviar = enviar or _mandar_por_resend
    ahora = ahora or rs.ahora_uy()
    hechos = {"quejas": 0, "resumenes": 0, "fallos": 0, "en_espera": 0}
    if not secreto_usable(secreto):
        logger.warning("Reseñas avisos: sin SECRET_KEY propia no se mandan (el link del mail no andaría)")
        return hechos
    conn = rs._con(db)
    try:
        accesos = [dict(f) for f in conn.execute(
            "SELECT a.id, a.negocio_id, a.nombre, a.email, a.aviso, a.aviso_desde FROM rs_accesos a "
            "JOIN rs_negocios n ON n.id = a.negocio_id WHERE a.revocado_en IS NULL AND a.aviso != 'no' "
            "AND a.email IS NOT NULL ORDER BY a.id")]
    finally:
        conn.close()
    for a in accesos:
        n = rs.negocio(db, a["negocio_id"])
        conn = rs._con(db)
        try:
            conn.execute("BEGIN IMMEDIATE")
            if a["aviso"] == "al_momento":
                quejas = quejas_para_avisar(conn, a, ahora)
                google = google_para_avisar(conn, a, ahora)
                if not quejas and not google:
                    conn.rollback()
                    continue
                if _en_silencio(ahora) or not _cupo(conn, a["id"], ahora):
                    hechos["en_espera"] += 1
                    conn.rollback()
                    continue
                avid = conn.execute("INSERT INTO rs_avisos (acceso_id, tipo, cantidad, creado_en) VALUES (?,?,?,?)",
                                    (a["id"], "quejas", len(quejas) + len(google), rs._txt(ahora))).lastrowid
                conn.executemany("INSERT INTO rs_avisos_op (acceso_id, opinion_id, aviso_id) VALUES (?,?,?)",
                                 [(a["id"], o["id"], avid) for o in quejas])
                conn.executemany("INSERT INTO rs_avisos_gr (acceso_id, resena_id, aviso_id) VALUES (?,?,?)",
                                 [(a["id"], g["id"], avid) for g in google])
                mail = armar_mail_quejas(n, a, quejas, link_panel(base, a["id"], secreto, ahora), google)
                tipo = "quejas"
            else:
                if ahora.hour < HORA_RESUMEN or ahora.hour >= SILENCIO_DESDE or conn.execute(
                        "SELECT 1 FROM rs_avisos WHERE acceso_id = ? AND tipo = 'resumen' AND creado_en >= ?",
                        (a["id"], ahora.date().isoformat())).fetchone():
                    conn.rollback()
                    continue
                r = resumen_de_ayer(conn, n["id"], ahora)
                if not r["opiniones"] and not r["pendientes"]:
                    conn.rollback()
                    continue
                if not _cupo(conn, a["id"], ahora):
                    hechos["en_espera"] += 1
                    conn.rollback()
                    continue
                avid = conn.execute("INSERT INTO rs_avisos (acceso_id, tipo, cantidad, creado_en) VALUES (?,?,?,?)",
                                    (a["id"], "resumen", r["opiniones"], rs._txt(ahora))).lastrowid
                mail = armar_mail_resumen(n, a, r, link_panel(base, a["id"], secreto, ahora))
                tipo = "resumen"
            conn.commit()
        finally:
            conn.close()
        try:
            estado = enviar(a["email"], mail["asunto"], mail["html"], mail["texto"])
        except Exception:
            logger.warning("Reseñas: falló el aviso del acceso %s", a["id"], exc_info=True)
            estado = "desconocido"
        conn = rs._con(db)
        try:
            conn.execute("UPDATE rs_avisos SET estado = ? WHERE id = ?",
                         ("enviado" if estado == "ok" else "fallo" if estado == "fallo" else "incierto", avid))
            conn.commit()
        finally:
            conn.close()
        if estado == "ok":
            hechos["resumenes" if tipo == "resumen" else "quejas"] += 1
        else:
            hechos["fallos"] += 1
    return hechos


def avisos(db: str, nid: int, limite: int = 20) -> list:
    """Los últimos mails que salieron para el comercio, para el equipo."""
    conn = rs._con(db)
    try:
        return [dict(f) for f in conn.execute(
            "SELECT v.id, v.tipo, v.cantidad, v.estado, v.creado_en, a.nombre FROM rs_avisos v "
            "JOIN rs_accesos a ON a.id = v.acceso_id WHERE a.negocio_id = ? ORDER BY v.id DESC LIMIT ?",
            (nid, limite))]
    finally:
        conn.close()


def activo() -> bool:
    import os
    return os.environ.get("RESENAS_AVISOS", "on").strip().lower() not in ("off", "0", "false", "no")


def start_avisos_resenas(app) -> None:
    """Revisa cada 5 minutos. Los topes viven en la base, no en el hilo: un
    deploy no repite nada (rs_avisos_op) ni regala cupo (rs_avisos)."""
    if not activo():
        logger.info("Avisos de Reseñas apagados por RESENAS_AVISOS=off")
        return
    import os

    def _loop():
        time.sleep(_DEMORA_ARRANQUE_S)
        while True:
            try:
                with _lock:
                    base = os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev")
                    r = revisar(app.config["DB_PATH"], base, app.secret_key)
                if r["quejas"] or r["resumenes"] or r["fallos"]:
                    logger.info("Reseñas avisos: %s", r)
            except Exception as e:
                logger.warning(f"Reseñas avisos: {e}")
            time.sleep(REVISAR_CADA_S)

    threading.Thread(target=_loop, daemon=True, name="resenas-avisos").start()
