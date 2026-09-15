"""Borradores de post para la pagina de LinkedIn de Scalerics.

Los posts educativos ya estan escritos: viven en
services/linkedin_banco_semilla.py y se cargan en la tabla `linkedin_banco`.
Este modulo elige cual sale en cada corrida y arma la fila del borrador. No
llama a ningun modelo y no publica nada: el resultado se manda por mail para
que Juan lo revise.

Hasta agosto de 2026 cada corrida del cron le pedia el texto a un modelo. Eso
gastaba credito de API dos veces por semana para siempre, y era practicamente
todo el gasto de la cuenta. Los textos se escribieron una vez y quedaron
guardados. Si vas a agregar posts nuevos, las reglas que tienen que cumplir
estan en REGLAS_DE_VOZ mas abajo y las chequea validar_borrador().

Dos modos: el mail programado, que sale del banco, y el post pedido a mano
sobre trabajo real, que llega ya escrito en el pedido.
"""

import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta

from database import (
    create_linkedin_post,
    get_banco_disponible,
    marcar_banco_usado,
)
from services.email_service import send_linkedin_failure

logger = logging.getLogger(__name__)

COOLDOWN_DIAS = 180

# El mail programado es solo educativo. Hubo una rama que sacaba material de la
# base (demos deployadas, clientes cerrados) y se saco el 26-8-2026: el CRM no
# guarda nada de los proyectos entregados. Los 7 clientes cerrados tenian el
# `client_info` vacio y el nombre era el de la persona que lleno el formulario
# de Meta, asi que los posts salian sobre proyectos que no existian. Los posts
# sobre trabajo real ahora se piden a mano con `contexto_manual`: esa
# informacion vive en la cabeza de Juan, no en la base.

MAX_CARACTERES = 1300
MIN_CARACTERES = 200
MAX_HASHTAGS = 2

# Rangos de emoji. No es exhaustivo pero cubre todo lo que un modelo pone
# en un post: emoticones, simbolos, transporte, banderas, dingbats y las
# flechas decorativas.
_EMOJI = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002190-\U000021FF"
    "\U0000FE0F"
    "\U00002B00-\U00002BFF"
    "]"
)


# Los posts salen de la pagina de empresa, no de un perfil personal. Se chequean
# los marcadores de primera persona del singular que de verdad aparecen: los
# pronombres y los posesivos. Las desinencias verbales no se chequean porque
# distinguir "hago" de "hagon" a fuerza de regex da falsos positivos.
_PRIMERA_PERSONA = re.compile(
    r"(?<![\w])(yo|me|mi|mis|mí|mío|mía|míos|mías|conmigo)(?![\w])",
    re.IGNORECASE,
)


def validar_borrador(texto: str) -> list[str]:
    """Violaciones de las reglas de voz. Lista vacia = el texto pasa."""
    violaciones = []
    texto = texto or ""

    if "—" in texto:
        violaciones.append("usa guion largo, que esta prohibido")
    if "–" in texto:
        violaciones.append("usa guion medio, que esta prohibido")
    if _EMOJI.search(texto):
        violaciones.append("tiene al menos un emoji, y no se permiten emojis")

    primera = _PRIMERA_PERSONA.findall(texto)
    if primera:
        violaciones.append(
            "usa primera persona del singular (" + ", ".join(sorted(set(
                p.lower() for p in primera))[:4]) + "); los posts los publica la "
            "empresa, no una persona"
        )

    hashtags = re.findall(r"#\w+", texto)
    if len(hashtags) > MAX_HASHTAGS:
        violaciones.append(
            f"tiene {len(hashtags)} hashtags y el maximo es {MAX_HASHTAGS}"
        )

    largo = len(texto)
    if largo > MAX_CARACTERES:
        violaciones.append(
            f"es demasiado largo: {largo} caracteres, el maximo es {MAX_CARACTERES}"
        )
    if largo < MIN_CARACTERES:
        violaciones.append(
            f"es demasiado corto: {largo} caracteres, el minimo es {MIN_CARACTERES}"
        )

    return violaciones



def elegir_del_banco(db_path: str, ahora: datetime, excluir_temas: set) -> tuple:
    """Devuelve (fila del banco, en_cooldown).

    Se excluye por tema y no por fila. Cada tema tiene sus dos angulos en el
    banco, y si se excluyera solo la fila ya usada el segundo borrador de la
    corrida saldria del mismo tema: el mail llegaria con dos posts sobre lo
    mismo, que es justo lo que el par concreto/implicancia evita.

    en_cooldown=True significa que no quedaba ningun post fuera del cooldown y
    se reuso el mas viejo. El mail lo dice, para que se note que hay que
    escribir posts nuevos en la semilla.
    """
    limite = (ahora - timedelta(days=COOLDOWN_DIAS)).isoformat()

    libres = [f for f in get_banco_disponible(db_path, limite)
              if f["tema"] not in excluir_temas]
    if libres:
        return libres[0], False

    # Fallback: el mas viejo de todos, aunque no haya cumplido el cooldown.
    todos = get_banco_disponible(db_path, ahora.isoformat())
    todos = [f for f in todos if f["tema"] not in excluir_temas]
    if not todos:
        raise RuntimeError("No hay posts en el banco de LinkedIn")
    return todos[0], True


# Las reglas que cumple cada post del banco. Ya no las lee ningun modelo: son
# la referencia para quien escriba posts nuevos a mano. Las que se pueden
# chequear con codigo estan en validar_borrador(), y hay un test que corre el
# validador sobre toda la semilla.
REGLAS_DE_VOZ = """Escribís posts para la página de empresa de Scalerics en \
LinkedIn, una software factory uruguaya que construye software a medida, \
sistemas de gestión, tiendas online y automatizaciones. Los publica la \
empresa, no una persona: no es el perfil personal de nadie.

Prohibido:
- La primera persona del singular. Nada de "me parece", "lo que hago", "sigo \
con el que viene", "yo", "mi", "mis clientes". Habla la empresa: usá plural \
("armamos", "entregamos", "vemos seguido") o construcciones impersonales \
("pasa todo el tiempo", "el problema aparece siempre en el mismo lugar").
- Encasillar al lector por tamaño. Nada de "PyME", "negocio chico" ni "pequeña \
empresa", y nada del hashtag #PyMEs. Lo que contás le sirve igual a una \
distribuidora, a un estudio contable o a un comercio de barrio: el texto tiene \
que funcionar para los tres sin nombrar a ninguno como público objetivo.
- El guion largo y el guion medio. Usá coma, punto o dos puntos.
- Los emojis. Ninguno, en ningún lado.
- La estructura típica del post de LinkedIn: párrafos todos del mismo largo, \
listas con flechas o viñetas, enumeraciones de tres, la fórmula "no es X, es Y", \
y los cierres del tipo "Excited for what's ahead" o "seguimos construyendo".
- Encuadrar el trabajo como esfuerzo o sacrificio. Nada de "agotador", "brutal" \
o "lo más difícil que hicimos". En Scalerics programar gusta; el encuadre es \
disfrute y ambición.
- Las frases construidas para sonar bien pero que no dicen nada.
- Inventar datos. Solo podés usar lo que aparece en el contexto que te paso.

Obligatorio:
- Español rioplatense, voseo.
- Ortografía correcta, con todas las tildes y las eñes que correspondan. Esto \
se publica: un texto sin tildes queda mal escrito. Escribí "gestión", no \
"gestion"; "después", no "despues"; "diseña", no "disena".
- Frases cortas y directas. Datos concretos: cantidades, tiempos, nombres, plazos.
- Tono seguro, sin falsa modestia y sin pedir permiso.
- Entre 400 y 1200 caracteres.
- Como máximo dos hashtags, al final, sin espacios raros, elegidos por el tema \
de este post y no siempre los mismos.
- Arrancá con algo concreto. La primera línea es lo único que se ve antes del \
"ver más", así que tiene que valer sola.

Devolvés solamente el texto del post. Sin título, sin comillas, sin \
explicaciones ni comentarios sobre lo que escribiste."""

_ANGULOS = {
    "concreto": (
        "Contá el hecho: qué se hizo, para quién, cuánto llevó, qué problema "
        "concreto resolvió. Que se note el detalle de alguien que estuvo ahí."
    ),
    "implicancia": (
        "Arrancá del hecho, pero el post es sobre lo que ese hecho revela de "
        "cómo trabajan las empresas. Una idea, no una lista."
    ),
}

MAX_FRASE = 70


def _borrador_manual(db_path: str, job_id, lote: str, texto: str,
                     imagen_url: str = "", frase: str = ""):
    """Un post sobre trabajo real. El texto llega ya escrito en el pedido.

    Antes aca se le pasaba una descripcion a un modelo para que la convirtiera
    en post. Ahora el post lo escribis vos y esto solo lo guarda: el CRM no
    tiene nada que agregarle a algo que ya esta escrito, y no hay razon para
    gastar API en reformatear texto propio.
    """
    texto = (texto or "").strip()
    if not texto:
        return None

    # Con URL se muestra lo que se hizo; sin URL, una frase en la tarjeta de
    # marca. Un post sin imagen rinde bastante menos en LinkedIn.
    if imagen_url:
        imagen_tipo = "screenshot"
        imagen_spec = json.dumps({"url": imagen_url})
    else:
        frase = (frase or "").strip() or _primera_frase(texto)
        imagen_tipo = "tarjeta" if frase else "ninguna"
        imagen_spec = json.dumps({"frase": frase})

    token = secrets.token_urlsafe(16)
    resumen = " ".join(texto.split())[:90]
    fuente_desc = f"Pedido a mano: {resumen}"

    post_id = create_linkedin_post(
        db_path, job_id=job_id, lote=lote, tipo="manual", texto=texto,
        angulo="concreto", fuente_tipo="manual", fuente_id=None,
        imagen_tipo=imagen_tipo, imagen_spec=imagen_spec,
        fuente_desc=fuente_desc, aviso="", marcar_token=token,
    )
    return {
        "id": post_id, "tipo": "manual", "texto": texto,
        "fuente_desc": fuente_desc, "aviso": "", "marcar_token": token,
        "imagen_tipo": imagen_tipo, "imagen_spec": json.loads(imagen_spec),
        "fuente_id": None,
    }


def _primera_frase(texto: str) -> str:
    """La primera oracion, si entra en la tarjeta. Si no entra, cadena vacia.

    Respaldo para el post manual sin frase propia: mejor la primera linea del
    post que una tarjeta vacia. Recortar a la mitad una oracion queda peor que
    no poner tarjeta, asi que si no entra entera no se usa.
    """
    primera = texto.strip().splitlines()[0].split(". ")[0].strip().rstrip(".")
    return primera if 0 < len(primera) <= MAX_FRASE else ""


def _borrador_educativo(db_path: str, job_id, lote: str, fila: dict):
    """Arma el borrador a partir de una fila del banco.

    El texto y la frase de la tarjeta ya vienen escritos: aca solo se guardan.
    """
    imagen_spec = json.dumps({"frase": fila["frase"]})
    token = secrets.token_urlsafe(16)
    fuente_desc = f"Tema educativo: {fila['tema']}"

    post_id = create_linkedin_post(
        db_path,
        job_id=job_id,
        lote=lote,
        tipo="educativo",
        texto=fila["texto"],
        angulo=fila["angulo"],
        fuente_tipo="banco",
        fuente_id=fila["id"],
        imagen_tipo="tarjeta",
        imagen_spec=imagen_spec,
        fuente_desc=fuente_desc,
        aviso="",
        marcar_token=token,
    )
    return {
        "id": post_id,
        "tipo": "educativo",
        "texto": fila["texto"],
        "fuente_desc": fuente_desc,
        "aviso": "",
        "marcar_token": token,
        "imagen_tipo": "tarjeta",
        "imagen_spec": json.loads(imagen_spec),
        "fuente_id": fila["id"],
    }


def linkedin_job_handler(payload: dict) -> dict:
    """Handler del JobWorker. Arma los borradores y los guarda.

    Dos modos. Sin `contexto_manual` saca los dos educativos del banco. Con
    `contexto_manual` guarda un solo post sobre trabajo real, ya escrito.

    No manda el mail: eso pasa en /api/linkedin/enviar, despues de que el
    runner de Actions renderice las imagenes. El `lote` viene armado desde el
    endpoint, asi que las filas nacen ya identificadas.
    """
    db_path = payload["db_path"]
    lote = payload["lote"]
    job_id = payload.get("job_id")
    ahora = datetime.fromisoformat(payload["ahora"])
    manual = (payload.get("contexto_manual") or "").strip()

    borradores = []
    aviso_cooldown = False

    if manual:
        b = _borrador_manual(db_path, job_id, lote, manual,
                             payload.get("imagen_url") or "",
                             payload.get("frase") or "")
        if b:
            borradores.append(b)
    else:
        usados_temas = set()
        for _ in range(2):
            fila, en_cooldown = elegir_del_banco(db_path, ahora, usados_temas)
            aviso_cooldown = aviso_cooldown or en_cooldown
            usados_temas.add(fila["tema"])
            b = _borrador_educativo(db_path, job_id, lote, fila)
            if b:
                marcar_banco_usado(db_path, fila["id"], ahora.isoformat())
                borradores.append(b)

    if not borradores:
        destino = os.environ.get("LINKEDIN_MAIL_TO", "scalerics@gmail.com")
        send_linkedin_failure(destino, "no se pudo armar ningun borrador")
    else:
        # Ademas del mail, que no cambia, quedan en el panel LinkedIn. Si
        # guardarlos falla, el job sigue: el mail vale mas que la pantalla.
        try:
            from services.linkedin_borradores import guardar_borradores
            guardar_borradores(db_path, borradores)
        except Exception as e:
            logger.error(f"LinkedIn: no se pudieron guardar los borradores en el panel ({type(e).__name__}: {e})")

    return {"lote": lote, "borradores": borradores, "aviso_cooldown": aviso_cooldown}
