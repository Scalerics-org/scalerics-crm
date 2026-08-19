"""Registro de recordatorios enviados a leads de Meta y su baja de la lista."""

import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone

from services.email_service import send_meta_lead_reminder

CLAVE_NEGOCIO = "¿cómo_se_llama_tu_negocio?"
CLAVE_RUBRO = "¿que_es_lo_que_buscás_para_tu_negocio?"

logger = logging.getLogger(__name__)

# Resend free permite 2 envios por segundo. A 15 por dia sobra, pero el codigo
# no tiene que depender de que el volumen sea bajo.
_PAUSA_ENTRE_ENVIOS = 0.6
_CADA_24_HORAS = 24 * 60 * 60
# Tope de mails por dia, no por corrida: la maquina se reinicia sola (un secret
# nuevo en Fly la reinicia) y sin este tope cada reinicio dispara otros 15.
_TOPE_DIARIO = 15
# Un lead que entro hace entre 3 y 7 dias todavia se acuerda de que dejo sus
# datos: va primero, aunque haya backlog de meses esperando. Sin esto, el lead
# mas caliente es el ultimo en recibir el mail.
_VENTANA_RECIEN_ELEGIBLE_DIAS = 7

# Un lead de Meta sigue en la secuencia mientras nadie lo haya contactado (el
# crm_status es la UNICA senal de que alguien contesto el mail) y tenga
# direccion. Un solo lugar de verdad: si mañana se ajusta aca, vale para
# leads_a_recordar y leads_a_seguir por igual, no hace falta acordarse de la
# otra funcion.
_FILTRO_LEAD_ELEGIBLE = (
    "b.source = 'meta' AND b.crm_status = 'sin_contactar' "
    "AND b.email IS NOT NULL AND LENGTH(TRIM(b.email)) > 3"
)

# Dias desde el PRIMER envio de cada lead. El ancla es el primer contacto y no
# el anterior a proposito: asi el atraso de una tanda no se acumula sobre los
# que siguen. Son 7 y el septimo es el ultimo de la vida de ese lead.
_DIAS_DE_CADA_CONTACTO = [0, 10, 25, 115, 205, 295, 365]
_TOTAL_CONTACTOS = len(_DIAS_DE_CADA_CONTACTO)

# El resto de la base guarda las fechas asi (scraped_at, entre otras) y las
# compara contra datetime('now', ...) de SQLite, que devuelve este mismo
# formato. Un isoformat() con 'T' y offset no compara: rompe lexicograficamente
# en la posicion 10.
_FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"


def _ahora() -> str:
    """UTC naive en el formato de la casa, comparable con datetime('now')."""
    return datetime.now(timezone.utc).strftime(_FORMATO_FECHA)


def _conn(db_path: str) -> sqlite3.Connection:
    # timeout=10 como database._connect: worker, import diario y webhooks
    # escriben en la misma base, y los 5 segundos por defecto se quedan cortos
    # justo en los caminos que hacen dano (el registro de envio y su limpieza).
    return sqlite3.connect(db_path, timeout=10)


def registrar_envio(db_path: str, business_id: int, numero: int) -> str:
    """Deja constancia del contacto `numero` y devuelve su token de baja.

    Lanza sqlite3.IntegrityError si ese lead ya recibio ese contacto: es la red
    que impide mandar dos veces, y tiene que fallar ruidosamente. Cada contacto
    lleva su propio token, asi que el link de baja de cada mail funciona por
    separado.
    """
    token = secrets.token_urlsafe(24)
    ahora = _ahora()
    conn = _conn(db_path)
    try:
        conn.execute(
            "INSERT INTO meta_reminders (business_id, numero, token, sent_at) "
            "VALUES (?, ?, ?, ?)",
            (business_id, int(numero), token, ahora),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def contactos_enviados(db_path: str, business_id: int) -> int:
    """Cuantos contactos de la secuencia ya recibio este lead."""
    conn = _conn(db_path)
    try:
        (cuantos,) = conn.execute(
            "SELECT COUNT(*) FROM meta_reminders WHERE business_id = ?",
            (business_id,),
        ).fetchone()
    finally:
        conn.close()
    return int(cuantos or 0)


def dar_de_baja(db_path: str, token: str) -> bool:
    """Marca la baja. Devuelve False si el token no existe."""
    ahora = _ahora()
    conn = _conn(db_path)
    try:
        cur = conn.execute(
            "UPDATE meta_reminders SET unsubscribed_at = ? WHERE token = ? AND unsubscribed_at IS NULL",
            (ahora, token),
        )
        conn.commit()
        if cur.rowcount:
            return True
        existe = conn.execute(
            "SELECT 1 FROM meta_reminders WHERE token = ?", (token,)
        ).fetchone()
        return bool(existe)
    finally:
        conn.close()


def esta_dado_de_baja(db_path: str, business_id: int) -> bool:
    """True si el lead pidio no recibir mas, en CUALQUIER contacto.

    La baja es sobre la persona, no sobre el mail que la disparo: quien se da
    de baja en el contacto 2 no puede recibir el 3 ni ningun trimestral.
    """
    conn = _conn(db_path)
    try:
        fila = conn.execute(
            "SELECT 1 FROM meta_reminders "
            "WHERE business_id = ? AND unsubscribed_at IS NOT NULL LIMIT 1",
            (business_id,),
        ).fetchone()
    finally:
        conn.close()
    return bool(fila)


def _texto(campos: dict, clave: str) -> str:
    """Los valores de Meta vienen como 'una_nueva_página_web'."""
    return (campos.get(clave) or "").replace("_", " ").strip()


def enviados_ultimas_24h(db_path: str) -> int:
    """Cuantos recordatorios salieron en el ultimo dia, para no pasarse del tope.

    Compara contra datetime('now','-1 day'), asi que depende de que sent_at se
    guarde en el formato de la casa (ver _ahora).
    """
    conn = _conn(db_path)
    try:
        (cuantos,) = conn.execute(
            "SELECT COUNT(*) FROM meta_reminders WHERE sent_at >= datetime('now','-1 day')"
        ).fetchone()
    finally:
        conn.close()
    return int(cuantos or 0)


def leads_a_recordar(db_path: str, dias_minimos: int = 3, limite: int = _TOPE_DIARIO) -> list[dict]:
    """Leads de Meta que corresponde recordar hoy.

    Primero los recien elegibles (los que entraron hace entre `dias_minimos` y
    `_VENTANA_RECIEN_ELEGIBLE_DIAS` dias) y despues se completa el cupo con los
    mas viejos del backlog.

    Se deduplica por direccion de mail, no por fila de businesses: dos envios
    del mismo formulario con el telefono escrito distinto no fusionan, y la
    garantia de "un solo mail" es sobre la persona. El GROUP BY evita repetirla
    dentro de la tanda (con MIN(scraped_at) para quedarse con la fila mas
    vieja, que es la que el orden usa) y el NOT EXISTS, entre tandas.

    Las guardas viven todas en el WHERE a proposito: que un lead quede fuera
    no puede depender de que el llamador se acuerde de filtrarlo.
    """
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        filas = conn.execute(
            f"""
            SELECT b.id, b.name, TRIM(b.email) AS email, b.form_data,
                   MIN(b.scraped_at) AS primero
              FROM businesses b
         LEFT JOIN meta_reminders r ON r.business_id = b.id
             WHERE {_FILTRO_LEAD_ELEGIBLE}
               AND r.id IS NULL
               AND NOT EXISTS (
                     SELECT 1 FROM meta_reminders r2
                       JOIN businesses b2 ON b2.id = r2.business_id
                      WHERE LOWER(TRIM(b2.email)) = LOWER(TRIM(b.email))
                   )
               AND b.scraped_at IS NOT NULL
               AND b.scraped_at <= datetime('now', ?)
          GROUP BY LOWER(TRIM(b.email))
          ORDER BY (primero >= datetime('now', ?)) DESC, primero ASC
             LIMIT ?
            """,
            (f"-{int(dias_minimos)} days",
             f"-{int(_VENTANA_RECIEN_ELEGIBLE_DIAS)} days",
             int(limite)),
        ).fetchall()
    finally:
        conn.close()

    salida = []
    for f in filas:
        try:
            campos = json.loads(f["form_data"] or "{}")
        except (ValueError, TypeError):
            campos = {}
        if not isinstance(campos, dict):
            campos = {}
        salida.append({
            "id": f["id"],
            "name": f["name"] or "",
            "email": f["email"],
            "negocio": _texto(campos, CLAVE_NEGOCIO),
            "rubro": _texto(campos, CLAVE_RUBRO),
        })
    return salida


def leads_a_seguir(db_path: str, limite: int = _TOPE_DIARIO) -> list[dict]:
    """Leads que ya recibieron algun contacto y a los que hoy les toca el siguiente.

    No hace falta deduplicar por mail como en `leads_a_recordar`: para tener
    fila, el lead ya paso por ese filtro, asi que hay una sola por direccion.

    El salto que corresponde depende de MAX(numero), no de COUNT(*): un
    borrado manual de una fila intermedia (la forma documentada de reintentar
    un envio) deja huecos, y contar filas asignaria de nuevo un numero que ya
    existe. Eso chocaria contra el UNIQUE(business_id, numero) y ese choque,
    en enviar_recordatorios, se trata como "otra corrida se adelanto": el lead
    quedaria trabado en ese contacto para siempre, sin rastro en el log. El
    tope de la secuencia (7) usa la misma logica: importa el numero mas alto
    ya mandado, no cuantas filas hay, asi que un lead cuyo contacto 7 ya salio
    no vuelve a entrar aunque le falten filas intermedias por un reintento
    viejo.

    El salto que corresponde depende de cuantos contactos lleva, asi que el CASE
    se arma desde `_DIAS_DE_CADA_CONTACTO` para que la tabla de dias tenga un
    solo lugar de verdad.
    """
    casos = " ".join(
        f"WHEN {n} THEN {_DIAS_DE_CADA_CONTACTO[n]}"
        for n in range(1, _TOTAL_CONTACTOS)
    )
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        filas = conn.execute(
            f"""
            SELECT b.id, b.name, TRIM(b.email) AS email, b.form_data,
                   MAX(r.numero) AS ultimo_numero,
                   MIN(r.sent_at) AS primer_envio
              FROM businesses b
              JOIN meta_reminders r ON r.business_id = b.id
             WHERE {_FILTRO_LEAD_ELEGIBLE}
               AND NOT EXISTS (
                     SELECT 1 FROM meta_reminders u
                      WHERE u.business_id = b.id AND u.unsubscribed_at IS NOT NULL
                   )
          GROUP BY b.id
            HAVING ultimo_numero < {_TOTAL_CONTACTOS}
               AND primer_envio <= datetime('now', '-' || (CASE ultimo_numero {casos} END) || ' days')
          ORDER BY primer_envio ASC
             LIMIT ?
            """,
            (int(limite),),
        ).fetchall()
    finally:
        conn.close()

    salida = []
    for f in filas:
        try:
            campos = json.loads(f["form_data"] or "{}")
        except (ValueError, TypeError):
            campos = {}
        if not isinstance(campos, dict):
            campos = {}
        salida.append({
            "id": f["id"],
            "name": f["name"] or "",
            "email": f["email"],
            "negocio": _texto(campos, CLAVE_NEGOCIO),
            "rubro": _texto(campos, CLAVE_RUBRO),
            "numero": int(f["ultimo_numero"]) + 1,
        })
    return salida


def enviar_recordatorios(db_path: str, base_url: str, dry_run: bool = False) -> dict:
    ya_enviados = enviados_ultimas_24h(db_path)
    cupo = max(0, _TOPE_DIARIO - ya_enviados)
    if cupo == 0:
        logger.info(
            f"Recordatorios Meta: no se manda nada, ya salieron {ya_enviados} en las "
            f"ultimas 24 horas (tope diario {_TOPE_DIARIO})"
        )
        return {"candidatos": 0, "enviados": 0, "fallidos": 0, "inciertos": 0}

    # Los seguimientos primero: uno a destiempo pierde sentido, mientras que un
    # primer contacto puede esperar un dia sin costo.
    seguimientos = leads_a_seguir(db_path, limite=cupo)
    faltan = cupo - len(seguimientos)
    nuevos = leads_a_recordar(db_path, limite=faltan) if faltan > 0 else []
    candidatos = seguimientos + nuevos
    res = {"candidatos": len(candidatos), "enviados": 0, "fallidos": 0, "inciertos": 0}

    # Valvula para probar el camino completo contra una casilla propia: si esta
    # seteada, TODOS los mails van ahi y ninguno al lead. El registro en
    # meta_reminders se hace igual, que es justamente lo que se quiere probar.
    # En produccion tiene que quedar vacia.
    override = os.environ.get("META_NOTIFY_OVERRIDE", "").strip().lower()
    if override and candidatos and not dry_run:
        logger.warning(
            f"Recordatorios Meta: META_NOTIFY_OVERRIDE activo, los {len(candidatos)} "
            f"mails de esta tanda van a {override} y ninguno a los leads"
        )

    ya_hubo_intento = False
    for lead in candidatos:
        numero = lead.get("numero", 1)
        if dry_run:
            logger.info(f"[dry-run] contacto {numero} a {lead['email']} (lead {lead['id']})")
            continue
        try:
            token = registrar_envio(db_path, lead["id"], numero)
        except sqlite3.IntegrityError:
            # Otra corrida se le adelanto. No es un error: es la guarda haciendo
            # su trabajo.
            continue
        except sqlite3.OperationalError as e:
            # Base bloqueada por otro escritor. Sin registro no se manda (si
            # mandaramos igual, manana no habria nada que impida el segundo
            # mail). Se saltea este lead y sigue la tanda: un lock no puede
            # llevarse puesto el dia entero.
            logger.warning(
                f"Recordatorios Meta: no se pudo registrar el envio del lead {lead['id']} "
                f"(business_id={lead['id']}): {e}. No se le manda nada hoy; sigue elegible."
            )
            continue
        if ya_hubo_intento:
            # La pausa es para no pasarse del rate limit de Resend, asi que va
            # entre dos intentos reales: no despues del ultimo, ni despues de un
            # lead que se salteo sin llegar a mandar nada.
            time.sleep(_PAUSA_ENTRE_ENVIOS)

        destino = lead["email"]
        if override:
            logger.warning(
                f"META_NOTIFY_OVERRIDE activo: el recordatorio del lead {lead['id']} "
                f"({lead['email']}) se manda a {override} en vez de a esa direccion"
            )
            destino = override
        estado = send_meta_lead_reminder(
            destino, lead["negocio"], lead["rubro"],
            f"{base_url.rstrip('/')}/baja/{token}",
            numero,
        )
        ya_hubo_intento = True
        if estado == "ok":
            res["enviados"] += 1
        elif estado == "fallo":
            # Solo aca se borra, y solo porque sabemos que el mail NO salio. La
            # rama destructiva tiene que ser la explicita: si fuera el `else`,
            # cualquier valor inesperado (None, un mock sin configurar, un cuarto
            # estado que alguien agregue) terminaria mandando un segundo mail,
            # que es justo lo que este bloque existe para evitar.
            # Se borra el registro para que manana se reintente: dejarlo puesto
            # significaria que ese lead nunca recibe nada. Solo ese contacto: un
            # fallo en el N no puede llevarse puestos los contactos 1..N-1 ya
            # mandados (perderia sus tokens de baja publicados) ni hacer que el
            # lead reciba de nuevo el contacto 1.
            conn = _conn(db_path)
            try:
                try:
                    conn.execute(
                        "DELETE FROM meta_reminders WHERE business_id = ? AND numero = ?",
                        (lead["id"], numero),
                    )
                    conn.commit()
                except Exception:
                    # Si esto tambien falla (ej. "database is locked"), el lead
                    # queda registrado como si hubiera recibido el mail sin
                    # haberlo recibido. No podemos arreglarlo solos: que quede
                    # gritado en el log, con el id, para desmarcarlo a mano. Y
                    # sobre todo, que no aborte la tanda de hoy por un lead.
                    logger.error(
                        f"Recordatorios Meta: el mail al lead {lead['id']} fallo y ademas "
                        f"no se pudo limpiar el registro de envio (business_id={lead['id']}). "
                        f"Va a quedar marcado como contactado sin haber recibido nada: "
                        f"revisar/borrar manualmente el registro en meta_reminders.",
                        exc_info=True,
                    )
            finally:
                conn.close()
            res["fallidos"] += 1
        else:
            # "desconocido", y tambien cualquier estado que no reconozcamos: ante
            # la duda no se toca la fila. Si la borraramos, manana el lead vuelve
            # a ser elegible y le llega un segundo mail; y si con el link del
            # primero se dio de baja, el token se fue con la fila y la baja no lo
            # protege. Se queda puesta: el silencio se arregla a mano.
            res["inciertos"] += 1
            logger.error(
                f"Recordatorios Meta: envio incierto al lead {lead['id']} "
                f"(business_id={lead['id']}, {lead['email']}, estado={estado!r}): la "
                f"peticion a Resend no confirmo ni fallo, pudo haber salido. Se DEJA el "
                f"registro en meta_reminders para no mandarle dos veces; si se confirma "
                f"que no llego, borrar la fila a mano para reintentar."
            )

    logger.info(f"Recordatorios Meta: {res}")
    return res


def start_meta_reminders(app) -> None:
    """Corre una vez por dia. Arranca SOLO con META_RECORDATORIOS=on.

    El default es apagado a proposito: esto le manda mail a terceros reales, y
    el hilo corre 180 segundos despues de cada boot. Fly reinicia la maquina
    para aplicar un secret, asi que un default encendido convierte cualquier
    deploy en una tanda de mails que nadie pidio.
    """
    if os.environ.get("META_RECORDATORIOS", "").strip().lower() != "on":
        logger.info(
            "Recordatorios de Meta apagados (hace falta META_RECORDATORIOS=on para arrancarlos)"
        )
        return

    def _loop():
        time.sleep(180)  # dejar que la app termine de levantar
        while True:
            try:
                with app.app_context():
                    enviar_recordatorios(
                        app.config["DB_PATH"],
                        os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev"),
                    )
            except Exception as e:
                logger.warning(f"Recordatorios Meta: {e}")
            time.sleep(_CADA_24_HORAS)

    threading.Thread(target=_loop, daemon=True, name="meta-reminders").start()
    logger.info(
        f"Recordatorios de Meta ACTIVOS por META_RECORDATORIOS=on: una corrida por dia, "
        f"hasta {_TOPE_DIARIO} mails, la primera 180s despues de este arranque"
    )


_USO = """uso: python -m services.meta_reminders <db_path> [--dry-run]

  --dry-run   lista a quien le tocaria el recordatorio y no manda ni registra nada.

Variables que cambian lo que hace:
  META_NOTIFY_OVERRIDE   si tiene una direccion, todos los mails van ahi y
                         ninguno a los leads (el registro se hace igual).
  CRM_URL                base para armar el link de baja.

Correrlo con `python -m` y no `python services/meta_reminders.py`: el modulo
importa `services.email_service` y necesita la raiz del repo en el path.
"""


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    argumentos = sys.argv[1:]
    dry_run = "--dry-run" in argumentos
    rutas = [a for a in argumentos if not a.startswith("-")]
    desconocidos = [a for a in argumentos if a.startswith("-") and a != "--dry-run"]

    if len(rutas) != 1 or desconocidos:
        print(_USO)
        raise SystemExit(2)

    resultado = enviar_recordatorios(
        rutas[0],
        os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev"),
        dry_run=dry_run,
    )
    print(resultado)
