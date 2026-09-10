"""El motor que le pide al modelo que lea el dossier y escriba los hallazgos.

La regla que sostiene todo el diseño: **el modelo nunca ve filas.** Recibe
únicamente el dossier ya calculado, donde cada métrica trae su id, su valor, su
`n` y su intervalo. De ahí salen dos cosas:

  - No puede inventar un número, porque no tiene de dónde sacarlo. Y si igual lo
    hace, `validar()` lo agarra: cada número del texto tiene que existir en el
    dossier. «No inventa» deja de ser una promesa del prompt y pasa a ser una
    propiedad verificable, con tests.
  - No salen datos personales de 238 personas hacia un servicio externo.

**Nace apagado.** `RADIOGRAFIA_IA_ACTIVA` en `false` por defecto. Con la bandera
apagada no se llama a nadie y el snapshot queda con `status='sin_ia'`: el panel
funciona igual, porque los gráficos siempre salieron del dossier y no del informe.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

# El trabajo es razonamiento analítico sobre datos y corre una vez por semana.
# Degradar el modelo para ahorrar centavos no tiene sentido acá: el dossier son
# ~31.000 tokens, o sea ~USD 0,31 por corrida.
MODELO = "claude-opus-5"

# Cuántas veces se reintenta cuando el informe no valida. Una: si el modelo
# vuelve a inventar un número después de que se le dijo cuál, el problema no se
# arregla insistiendo.
REINTENTOS = 1

# Bloques del dossier que el modelo no necesita. `serie_semanal` son 28 puntos
# por métrica que se dibujan pero no se analizan, y ocupaban la mitad del JSON.
_BLOQUES_QUE_NO_VAN = ("serie_semanal",)

# Lo único que se manda de cada métrica. Explícito y no "todo menos X": si
# mañana alguien agrega un campo con datos personales al dossier, no sale de acá.
_CAMPOS_DE_METRICA = ("id", "etiqueta", "valor", "formato", "numerador",
                      "denominador", "n", "ic95", "delta_periodo_anterior",
                      "fuente", "muestra_chica")


def ia_activa() -> bool:
    """Si se puede gastar en la API.

    Solo un sí explícito. Prender esto cuesta plata, así que cualquier otra
    cosa —vacío, "0", "no", una cadena rara— se lee como apagado.
    """
    return os.environ.get("RADIOGRAFIA_IA_ACTIVA", "").strip().lower() in (
        "true", "1", "si", "sí", "yes")


def _metricas_de(dossier: dict):
    """Cada métrica del dossier, venga del bloque que venga."""
    for bloque in dossier.get("campanas") or []:
        for m in bloque.get("metricas") or []:
            yield m
    for bloque in dossier.get("segmentos") or []:
        for valor in bloque.get("valores") or []:
            for m in valor.get("metricas") or []:
                yield m
    for clave in ("conciliacion", "tiempos", "recordatorios"):
        for m in dossier.get(clave) or []:
            yield m


def indice_de_metricas(dossier: dict) -> dict:
    """{id: métrica}, para que el validador pueda buscar por id."""
    return {m["id"]: m for m in _metricas_de(dossier) if m.get("id")}


def _podar(m: dict) -> dict:
    return {k: m[k] for k in _CAMPOS_DE_METRICA if k in m}


def preparar(dossier: dict) -> dict:
    """El dossier tal como lo va a ver el modelo.

    Se arma campo por campo a propósito: nada que no esté acá listado llega a
    la API, aunque alguien lo agregue al dossier mañana.
    """
    salida = {}
    if dossier.get("periodo"):
        salida["periodo"] = dossier["periodo"]

    if dossier.get("campanas"):
        salida["campanas"] = [
            {"campana": b.get("campana"), "moneda": b.get("moneda"),
             "metricas": [_podar(m) for m in b.get("metricas") or []]}
            for b in dossier["campanas"]]

    if dossier.get("segmentos"):
        salida["segmentos"] = [
            {"pregunta": b.get("pregunta"), "etiqueta": b.get("etiqueta"),
             "n": b.get("n"), "valores_distintos": b.get("valores_distintos"),
             "valores": [
                 {"valor_declarado": v.get("valor_declarado"), "n": v.get("n"),
                  "metricas": [_podar(m) for m in v.get("metricas") or []]}
                 for v in b.get("valores") or []]}
            for b in dossier["segmentos"]]

    for clave in ("conciliacion", "tiempos", "recordatorios"):
        if dossier.get(clave):
            salida[clave] = [_podar(m) for m in dossier[clave]]

    for clave in _BLOQUES_QUE_NO_VAN:
        salida.pop(clave, None)

    return salida


# ── El validador anti-invención ───────────────────────────────────────────────
#
# Cuatro reglas, y las cuatro se chequean por código. Que el informe no invente
# no es una promesa del prompt: es una propiedad con tests.

# Números escritos en es-UY (1.234,56) o en simple (9.38). El orden de las
# alternativas importa: primero la forma con separador de miles.
_NUMERO = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[,.]\d+)?")

# Frases que afirman causalidad entre métricas. Correlación no es causa, y con
# 238 leads menos todavía. Es aviso y no rechazo: a veces la causa la sabe el
# equipo y el modelo la está repitiendo, no inventando.
_CAUSALIDAD = re.compile(
    r"\b(porque|se debe a|debido a|causa(?:do)?|provoc[óo]|genera(?:ron)? que"
    r"|a raíz de|gracias a)\b", re.I)


def _a_float(texto: str):
    """Un número del informe, en cualquiera de los dos formatos."""
    t = texto.strip()
    if "." in t and "," in t:          # 1.234,56 → miles con punto
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:                     # 9,38
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _valores_legitimos(dossier: dict) -> set:
    """Todo número que el informe puede escribir sin estar inventándolo.

    No es solo el `valor` de cada métrica: el `n`, el numerador, el denominador
    y los extremos del intervalo también salen del dossier, y el informe tiene
    todo el derecho de citarlos. Los porcentajes entran dos veces —como
    proporción y como porcentaje— porque el dossier guarda 0,229 y el informe
    escribe 22,9%.
    """
    valores = set()

    def _sumar(v, como_porcentaje=False):
        if isinstance(v, bool) or v is None:
            return
        if not isinstance(v, (int, float)):
            return
        valores.add(round(float(v), 2))
        # El ×100 solo para proporciones. Aplicarlo a cualquier número traía un
        # falso negativo feo: el mes «09» del período generaba 900 como valor
        # legítimo, y el validador dejaba pasar un 900 inventado.
        if como_porcentaje and 0 <= float(v) <= 1:
            valores.add(round(float(v) * 100, 1))

    for m in _metricas_de(dossier):
        _sumar(m.get("valor"), como_porcentaje=True)
        for clave in ("n", "numerador", "denominador"):
            _sumar(m.get(clave))
        for extremo in m.get("ic95") or []:
            _sumar(extremo, como_porcentaje=True)

    # Los años del período no son métricas, pero aparecen en cualquier informe
    # que diga "entre marzo y septiembre de 2026".
    periodo = dossier.get("periodo") or {}
    for fecha in (periodo.get("desde"), periodo.get("hasta")):
        if fecha:
            for parte in str(fecha).split("-"):
                _sumar(_a_float(parte))
    return valores


def _numeros_inventados(texto: str, legitimos: set) -> list:
    """Los números del texto que no salen del dossier."""
    sueltos = []
    for crudo in _NUMERO.findall(texto or ""):
        n = _a_float(crudo)
        if n is None:
            continue
        # Un año de cuatro dígitos no es una métrica.
        if 1900 <= n <= 2100 and float(n).is_integer():
            continue
        if any(abs(n - ok) <= 0.1 for ok in legitimos):
            continue
        sueltos.append(crudo)
    return sueltos


def validar(informe: dict, dossier: dict) -> list:
    """Los problemas del informe. Lista vacía significa que se puede publicar.

    Un problema que empieza con "aviso:" no bloquea: es la regla de causalidad,
    que es más blanda que las otras tres a propósito.
    """
    problemas = []
    indice = indice_de_metricas(dossier)
    legitimos = _valores_legitimos(dossier)

    hallazgos = informe.get("hallazgos") or []
    if not hallazgos:
        problemas.append("el informe no trae ningún hallazgo")

    # Regla 1: todo número del texto tiene que existir en el dossier.
    textos = [("resumen", informe.get("resumen", ""))]
    for i, h in enumerate(hallazgos):
        for campo in ("titulo", "cuerpo", "recomendacion"):
            textos.append((f"hallazgo {i} · {campo}", h.get(campo, "")))
    for donde, texto in textos:
        for crudo in _numeros_inventados(texto, legitimos):
            problemas.append(
                f"{donde}: el número {crudo} no existe en el dossier")

    for i, h in enumerate(hallazgos):
        citadas = h.get("metricas_citadas") or []

        # Regla 2: hay que citar, y hay que citar algo que exista.
        if not citadas:
            problemas.append(f"hallazgo {i}: no cita ninguna métrica")
        for mid in citadas:
            if mid not in indice:
                problemas.append(
                    f"hallazgo {i}: cita la métrica {mid}, que no existe")

        # Regla 3: si la muestra es chica, se dice.
        chicas = [mid for mid in citadas
                  if indice.get(mid, {}).get("muestra_chica")]
        if chicas and not h.get("advertencia_muestra"):
            problemas.append(
                f"hallazgo {i}: cita {', '.join(chicas)} —muestra chica— "
                "sin marcar advertencia_muestra")

        # Regla 4: causalidad. Avisa, no bloquea.
        for campo in ("cuerpo", "recomendacion"):
            if _CAUSALIDAD.search(h.get(campo, "") or ""):
                problemas.append(
                    f"aviso: hallazgo {i} · {campo} afirma causalidad; "
                    "el dossier solo puede mostrar asociación")

    return problemas


def bloqueantes(problemas: list) -> list:
    """Los problemas que impiden publicar. Los avisos no cuentan."""
    return [p for p in problemas if not p.startswith("aviso:")]


# ── La llamada ────────────────────────────────────────────────────────────────

_INSTRUCCIONES = """Sos un analista comercial. Te doy un dossier de métricas ya
calculadas sobre las campañas de Meta Ads de una software factory chica de
Uruguay, y escribís un informe corto para el equipo comercial.

Reglas que no se negocian:

1. **No inventes ningún número.** Todo número que escribas tiene que estar en el
   dossier. No estimes, no proyectes, no redondees hacia un número "lindo". Si
   necesitás un dato que no está, decí que falta.
2. **Citá los ids.** Cada hallazgo lista en `metricas_citadas` los ids exactos
   de las métricas que lo sostienen.
3. **Si la muestra es chica, decilo.** Toda métrica con `muestra_chica: true`
   obliga a poner `advertencia_muestra: true` en ese hallazgo. Una diferencia
   sobre 5 leads no es una diferencia.
4. **No afirmes causalidad.** El dossier muestra asociación. Escribí "los leads
   que declararon más presupuesto llegaron a demo menos seguido", no "declarar
   más presupuesto hace que lleguen menos".
5. **Mirá los intervalos.** Si los `ic95` de dos grupos se superponen, la
   diferencia entre ellos no significa nada y no es un hallazgo.
6. "No hay evidencia de X" es una conclusión válida y a veces la más honesta.
   No fuerces hallazgos donde los datos no dan.

Escribí en español rioplatense, directo, sin fórmulas de consultoría. El equipo
son tres personas que van a leer esto un lunes a la mañana."""

_ESQUEMA = {
    "type": "object",
    "properties": {
        "resumen": {"type": "string"},
        "hallazgos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string"},
                    "tipo": {"type": "string",
                             "enum": ["oportunidad", "riesgo", "anomalia",
                                      "contexto"]},
                    "cuerpo": {"type": "string"},
                    "metricas_citadas": {"type": "array",
                                         "items": {"type": "string"}},
                    "confianza": {"type": "string",
                                  "enum": ["alta", "media", "baja"]},
                    "recomendacion": {"type": "string"},
                    "advertencia_muestra": {"type": "boolean"},
                },
                "required": ["titulo", "tipo", "cuerpo", "metricas_citadas",
                             "confianza", "recomendacion",
                             "advertencia_muestra"],
                "additionalProperties": False,
            },
        },
        "cambios_desde_la_ultima": {"type": "array",
                                    "items": {"type": "string"}},
    },
    "required": ["resumen", "hallazgos", "cambios_desde_la_ultima"],
    "additionalProperties": False,
}


def _llamar_a_la_api(dossier: dict, correccion=None):
    """Le pide el informe al modelo. Devuelve (informe, tokens_in, tokens_out).

    El import va acá adentro: `anthropic` son 19,6 MB medidos dentro del
    contenedor y la máquina de Fly tiene 256 MB. Mismo criterio que
    services/budget_ai.py.
    """
    import json

    import anthropic

    cliente = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    mensaje = ("Este es el dossier:\n\n"
               + json.dumps(dossier, ensure_ascii=False, indent=1))
    if correccion:
        mensaje += ("\n\nTu respuesta anterior fue rechazada por el validador:\n"
                    + correccion
                    + "\n\nCorregí eso. Los números tienen que salir del "
                      "dossier de arriba, tal como están.")

    r = cliente.messages.create(
        model=MODELO,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=_INSTRUCCIONES,
        output_config={"format": {"type": "json_schema", "schema": _ESQUEMA}},
        messages=[{"role": "user", "content": mensaje}],
    )
    texto = next((b.text for b in r.content if b.type == "text"), "{}")
    return (json.loads(texto), r.usage.input_tokens, r.usage.output_tokens)


def redactar(dossier: dict, llamar=None) -> dict:
    """El informe sobre el dossier, ya validado.

    Devuelve siempre un dict con `status`. Nunca levanta: una corrida semanal
    que explota deja al panel sin nada, y el panel tiene que seguir andando
    aunque la IA falle.

    `llamar` existe para los tests: recibe (dossier_preparado, correccion) y
    devuelve (informe, tokens_in, tokens_out).
    """
    vacio = {"informe": None, "status": "sin_ia", "error": None,
             "avisos": [], "tokens_in": 0, "tokens_out": 0}

    if not ia_activa():
        logger.info("radiografía: la IA está apagada (RADIOGRAFIA_IA_ACTIVA)")
        return vacio

    if llamar is None:
        llamar = _llamar_a_la_api

    preparado = preparar(dossier)
    correccion, tin, tout, ultimo_error = None, 0, 0, None

    for intento in range(REINTENTOS + 1):
        try:
            informe, i, o = llamar(preparado, correccion)
        except Exception as e:
            logger.error(f"radiografía: la API falló ({e})")
            return {**vacio, "status": "error_ia", "error": str(e),
                    "tokens_in": tin, "tokens_out": tout}

        tin += i or 0
        tout += o or 0

        problemas = validar(informe, dossier)
        duros = bloqueantes(problemas)
        if not duros:
            avisos = [p for p in problemas if p.startswith("aviso:")]
            if avisos:
                logger.warning(f"radiografía: {len(avisos)} aviso(s): {avisos}")
            return {"informe": informe, "status": "ok", "error": None,
                    "avisos": avisos, "tokens_in": tin, "tokens_out": tout}

        ultimo_error = " · ".join(duros)
        logger.warning(f"radiografía: intento {intento + 1} rechazado: "
                       f"{ultimo_error}")
        correccion = ultimo_error

    # Nunca se publica un informe sin validar.
    return {**vacio, "status": "error_validacion", "error": ultimo_error,
            "tokens_in": tin, "tokens_out": tout}
