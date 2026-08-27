"""Borradores de post para la pagina de LinkedIn de Scalerics.

Redacta con Claude y valida el texto contra las reglas de voz. No publica
nada: el resultado se manda por mail para que Juan lo revise.

Dos modos: el mail programado, que sale de la tabla de temas educativos, y el
post pedido a mano sobre trabajo real, que se arma con lo que Juan escriba.
"""

import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta

import anthropic

from database import (
    create_linkedin_post,
    get_temas_disponibles,
    marcar_tema_usado,
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


def elegir_tema(db_path: str, ahora: datetime, excluir_ids: set) -> tuple:
    """Devuelve (tema, en_cooldown).

    en_cooldown=True significa que no quedaba ningun tema fuera del cooldown
    y se reuso el mas viejo. El mail lo dice, para que se note que hay que
    sembrar temas nuevos.
    """
    limite = (ahora - timedelta(days=COOLDOWN_DIAS)).isoformat()

    libres = [t for t in get_temas_disponibles(db_path, limite)
              if t["id"] not in excluir_ids]
    if libres:
        return libres[0], False

    # Fallback: el mas viejo de todos, aunque no haya cumplido el cooldown.
    todos = get_temas_disponibles(db_path, ahora.isoformat())
    todos = [t for t in todos if t["id"] not in excluir_ids]
    if not todos:
        raise RuntimeError("No hay temas de LinkedIn sembrados")
    return todos[0], True


MODELO = "claude-opus-5"

SYSTEM_PROMPT = """Escribís posts para la página de empresa de Scalerics en \
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


def contexto_educativo(tema: dict) -> str:
    return f"""Post educativo para quien toma decisiones en una empresa. No \
hay un cliente ni un proyecto detrás: es algo que Scalerics ve seguido.

- Tema: {tema['titulo']}
- Lo que hay que dejar dicho: {tema['angulo']}

Podés usar ejemplos genéricos y verosímiles del mercado uruguayo, y conviene \
variarlos: una distribuidora, un estudio contable, una empresa de logística, un \
taller, un comercio. No caigas siempre en el mismo ejemplo ni des por sentado \
que el lector tiene un negocio chico. No inventes clientes de Scalerics ni \
cifras de resultados que no tenés."""


def contexto_manual(descripcion: str) -> str:
    """Contexto de un post pedido a mano, sobre trabajo real.

    Lo que se cuenta lo escribe Juan. El CRM no guarda nada de los proyectos
    entregados, asi que esta es la unica via para un post sobre trabajo propio.
    """
    return f"""Post sobre trabajo real de Scalerics. Esto lo escribió Juan a \
mano y es todo lo que hay:

{descripcion.strip()}

No agregues ningún dato que no esté ahí arriba: ni plazos, ni tecnologías, ni \
resultados, ni nombres. Si algo no está, no se menciona. Preferí un post corto \
y concreto antes que uno largo que rellena."""


def redactar(contexto: str, angulo: str):
    """Un post validado, o None si el modelo no logro uno en dos intentos."""
    instruccion_angulo = _ANGULOS.get(angulo, _ANGULOS["concreto"])
    mensajes = [{
        "role": "user",
        "content": f"{contexto}\n\nAngulo del post: {instruccion_angulo}",
    }]

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    except KeyError:
        logger.error("Falta ANTHROPIC_API_KEY; no se puede redactar")
        return None

    for intento in (1, 2):
        try:
            respuesta = client.messages.create(
                model=MODELO,
                max_tokens=4000,
                thinking={"type": "adaptive"},
                system=SYSTEM_PROMPT,
                messages=mensajes,
            )
        except Exception:
            logger.exception("Fallo la llamada a Claude (intento %s)", intento)
            return None

        texto = "".join(
            b.text for b in respuesta.content if getattr(b, "type", None) == "text"
        ).strip()

        violaciones = validar_borrador(texto)
        if not violaciones:
            return texto

        logger.warning("Borrador rechazado (intento %s): %s", intento, violaciones)
        if intento == 2:
            return None

        # El reintento lleva el texto rechazado y la lista de lo que rompio.
        mensajes = mensajes + [
            {"role": "assistant", "content": texto},
            {"role": "user", "content":
                "Ese texto rompe las reglas. Problemas: "
                + "; ".join(violaciones)
                + ". Reescribilo entero corrigiendo eso y respetando todo lo demas."},
        ]

    return None


MAX_FRASE = 70

_PROMPT_FRASE = """Te paso un post de LinkedIn ya escrito. Devolvé la línea que \
va en la imagen que lo acompaña.

Tiene que ser la frase más fuerte del post: la que hace que alguien pare de \
scrollear. Entre cuatro y diez palabras. Preferí una frase textual del post; si \
ninguna funciona sola, escribí uná que diga lo mismo.

No pongas comillas, ni punto final, ni emojis, ni guiones largos. Devolvé \
solamente la frase, nada más."""


def frase_tarjeta(texto: str):
    """La línea que va en la imagen, o None si no salió una usable.

    Se pide sobre el post ya validado, así que la frase refleja el texto final
    y no una versión que después se descartó. Si devuelve None, quien llama
    cae al título del tema: la tarjeta nunca sale vacía.
    """
    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    except KeyError:
        logger.error("Falta ANTHROPIC_API_KEY; no se puede armar la frase")
        return None

    try:
        respuesta = client.messages.create(
            model=MODELO,
            # Opus 5 piensa por defecto: con max_tokens chico el pensamiento se
            # come el presupuesto y la frase vuelve cortada por la mitad. Esta
            # tarea no necesita pensar mucho, asi que va con effort bajo y con
            # aire de sobra.
            max_tokens=2000,
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
            system=_PROMPT_FRASE,
            messages=[{"role": "user", "content": texto}],
        )
    except Exception:
        logger.exception("Fallo la llamada a Claude para la frase de la tarjeta")
        return None

    if respuesta.stop_reason == "max_tokens":
        logger.warning("La frase de la tarjeta se corto por max_tokens")
        return None

    frase = "".join(
        b.text for b in respuesta.content if getattr(b, "type", None) == "text"
    ).strip().strip('"').strip("'").rstrip(".").strip()

    if not frase or len(frase) > MAX_FRASE:
        logger.warning("Frase de tarjeta descartada (%s caracteres)", len(frase))
        return None
    if _EMOJI.search(frase) or "—" in frase or "–" in frase:
        logger.warning("Frase de tarjeta con emoji o guion largo, descartada")
        return None
    return frase


def _borrador_manual(db_path: str, job_id, lote: str, descripcion: str,
                     imagen_url: str = ""):
    """Un post sobre trabajo real, a partir de lo que escribio Juan."""
    texto = redactar(contexto_manual(descripcion), "concreto")
    if texto is None:
        return None

    # Con URL se muestra lo que se hizo; sin URL, la frase del post en la
    # tarjeta de marca. Un post sin imagen rinde bastante menos en LinkedIn.
    if imagen_url:
        imagen_tipo = "screenshot"
        imagen_spec = json.dumps({"url": imagen_url})
    else:
        frase = frase_tarjeta(texto)
        imagen_tipo = "tarjeta" if frase else "ninguna"
        imagen_spec = json.dumps({"frase": frase or ""})

    token = secrets.token_urlsafe(16)
    resumen = " ".join(descripcion.split())[:90]
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


def _borrador_educativo(db_path: str, job_id, lote: str, tema: dict, angulo: str):
    texto = redactar(contexto_educativo(tema), angulo)
    if texto is None:
        return None

    # La tarjeta lleva la frase mas fuerte del post, no el titulo del tema: ese
    # titulo ya esta en el mail y en el texto del post. El titulo queda como
    # respaldo para que la imagen nunca salga vacia.
    imagen_spec = json.dumps({"frase": frase_tarjeta(texto) or tema["titulo"]})
    token = secrets.token_urlsafe(16)
    fuente_desc = f"Tema educativo: {tema['titulo']}"

    post_id = create_linkedin_post(
        db_path,
        job_id=job_id,
        lote=lote,
        tipo="educativo",
        texto=texto,
        angulo=angulo,
        fuente_tipo="tema",
        fuente_id=tema["id"],
        imagen_tipo="tarjeta",
        imagen_spec=imagen_spec,
        fuente_desc=fuente_desc,
        aviso="",
        marcar_token=token,
    )
    return {
        "id": post_id,
        "tipo": "educativo",
        "texto": texto,
        "fuente_desc": fuente_desc,
        "aviso": "",
        "marcar_token": token,
        "imagen_tipo": "tarjeta",
        "imagen_spec": json.loads(imagen_spec),
        "fuente_id": tema["id"],
    }


def linkedin_job_handler(payload: dict) -> dict:
    """Handler del JobWorker. Genera los borradores y los guarda.

    Dos modos. Sin `contexto_manual` arma los dos educativos del mail
    programado. Con `contexto_manual` arma un solo post sobre trabajo real, a
    partir de lo que escribio Juan.

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
                             payload.get("imagen_url") or "")
        if b:
            borradores.append(b)
    else:
        usados_ids = set()
        for i in range(2):
            tema, en_cooldown = elegir_tema(db_path, ahora, usados_ids)
            aviso_cooldown = aviso_cooldown or en_cooldown
            usados_ids.add(tema["id"])
            angulo = "concreto" if i == 0 else "implicancia"
            b = _borrador_educativo(db_path, job_id, lote, tema, angulo)
            if b:
                marcar_tema_usado(db_path, tema["id"], ahora.isoformat())
                borradores.append(b)

    if not borradores:
        destino = os.environ.get("LINKEDIN_MAIL_TO", "scalerics@gmail.com")
        send_linkedin_failure(
            destino, "ningun borrador paso la validacion de las reglas de voz"
        )

    return {"lote": lote, "borradores": borradores, "aviso_cooldown": aviso_cooldown}
