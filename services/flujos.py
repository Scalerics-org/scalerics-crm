"""La lógica del bloque Flujos (pantalla Ausencias, Recursos Humanos).

Cómo trabaja la empresa, paso a paso, con el ROL responsable de cada etapa.
Nunca nombres de personas: el flujo tiene que seguir siendo correcto si
cambia quién ocupa cada puesto (los nombres están en el organigrama).

Los pasos viven en la base (`flujos`, `flujo_pasos`, `flujo_paso_cobros`);
acá solo se valida lo que llega y se arma lo que pinta la pantalla.
"""

import math
import re

from database import (listar_cobros_pasos_flujo, listar_flujos,
                      listar_pasos_flujos)

ROLES = ("Marketing", "Comercial", "Project manager", "Desarrollo",
         "Administración", "Soporte")

# El color y el nombre visible de cada rol, en un solo lugar (pedido de Juan,
# 15/9). `color` es la familia CSS: tokens `--rol-<color>` y
# `--rol-<color>-tinte`, clase `.eq-rol-<color>`. "Marketing" se sigue
# guardando así en la base y se muestra como "Líder marketing digital": cambia
# la etiqueta, no el valor, así la lista cerrada de ROLES y los pasos cargados
# no se tocan.
ROL_ESTILOS = {
    "Marketing": {"color": "rojo", "etiqueta": "Líder marketing digital"},
    "Comercial": {"color": "verde", "etiqueta": "Comercial"},
    "Project manager": {"color": "naranja", "etiqueta": "Project manager"},
    "Desarrollo": {"color": "azul", "etiqueta": "Desarrollo"},
    "Administración": {"color": "violeta", "etiqueta": "Administración"},
    "Soporte": {"color": "teal", "etiqueta": "Soporte"},
}


def estilos_roles() -> list[dict]:
    """[{rol, color, etiqueta}] en el orden de ROLES, para la pantalla."""
    return [{"rol": r, **ROL_ESTILOS[r]} for r in ROLES]


# Quien no participa de Flujos (organigrama, pedido de Juan 16/9): un color que
# no es de ningún rol. Tokens `--rol-rosa` y `--rol-rosa-tinte`.
FUERA_DE_FLUJOS = {"color": "rosa", "etiqueta": "Fuera de Flujos"}


def estilo_de_persona(rol_flujo) -> dict:
    """{rol, color, etiqueta} de una persona según su rol en Flujos. Un rol que
    no es de la lista cerrada cuenta como "fuera de Flujos"."""
    if isinstance(rol_flujo, str) and rol_flujo in ROL_ESTILOS:
        return {"rol": rol_flujo, **ROL_ESTILOS[rol_flujo]}
    return {"rol": None, **FUERA_DE_FLUJOS}


def validar_rol_flujo(datos):
    """{'rol_flujo': uno de ROLES, o null / '' para "no participa"}."""
    if not isinstance(datos, dict) or "rol_flujo" not in datos:
        return None, "falta rol_flujo"
    valor = datos.get("rol_flujo")
    if valor is None or valor == "":
        return {"rol_flujo": None}, None
    if not isinstance(valor, str) or valor not in ROLES:
        return None, "el rol en Flujos tiene que ser uno de: " + ", ".join(ROLES) + ", o ninguno"
    return {"rol_flujo": valor}, None

# Id de panel del CRM (`showPanel`): minúsculas y guion bajo.
_PANTALLA = re.compile(r"^[a-z][a-z_]{0,39}$")

MAX_TITULO = 120
MAX_DETALLE = 240
MAX_COBROS = 10


def _texto(valor, nombre: str, maximo: int, obligatorio: bool):
    if valor is None:
        valor = ""
    if not isinstance(valor, str):
        return None, f"{nombre} tiene que ser texto"
    valor = valor.strip()
    if obligatorio and not valor:
        return None, f"falta {nombre}"
    if "\n" in valor or "\r" in valor:
        return None, f"{nombre} va en una línea"
    if len(valor) > maximo:
        return None, f"{nombre} tiene más de {maximo} caracteres"
    return valor, None


def _porcentaje(valor):
    if isinstance(valor, bool):
        return None
    if isinstance(valor, str):
        valor = valor.strip().replace(",", ".")
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def validar_cobros(crudo):
    """Momentos de cobro de un paso: [{porcentaje, descripcion}].

    Un paso puede tener varios (50 % al inicio y 50 % contra entrega) aunque
    hoy se cargue uno solo. Entre todos no pueden pasar del 100 %.
    """
    if not isinstance(crudo, list):
        return None, "los momentos de cobro tienen que ser una lista"
    if len(crudo) > MAX_COBROS:
        return None, f"no más de {MAX_COBROS} momentos de cobro"
    cobros = []
    for i, item in enumerate(crudo, start=1):
        if not isinstance(item, dict):
            return None, f"el momento de cobro {i} no es válido"
        pct = _porcentaje(item.get("porcentaje"))
        if pct is None or not 0 < pct <= 100:
            return None, f"el porcentaje del momento {i} tiene que estar entre 0 y 100"
        desc, error = _texto(item.get("descripcion"), f"la descripción del momento {i}", 120, True)
        if error:
            return None, error
        cobros.append({"porcentaje": round(pct, 2), "descripcion": desc})
    if sum(c["porcentaje"] for c in cobros) > 100 + 1e-9:
        return None, "los momentos de cobro suman más del 100 %"
    return cobros, None


def validar_paso(datos):
    """Devuelve (campos, error). `cobros` queda en None si no vino: así una
    edición que no los manda no borra los que había."""
    if not isinstance(datos, dict):
        return None, "faltan los datos del paso"
    titulo, error = _texto(datos.get("titulo"), "el título", MAX_TITULO, True)
    if error:
        return None, error
    rol = datos.get("rol")
    if rol not in ROLES:
        return None, "el rol tiene que ser uno de: " + ", ".join(ROLES)
    detalle, error = _texto(datos.get("detalle"), "el detalle", MAX_DETALLE, False)
    if error:
        return None, error
    pantalla = datos.get("pantalla")
    if pantalla in (None, ""):
        pantalla = None
    elif not isinstance(pantalla, str) or not _PANTALLA.match(pantalla):
        return None, "la pantalla no es un id de sección válido"
    destacado = datos.get("destacado", False)
    if destacado not in (True, False, 0, 1):
        return None, "destacado tiene que ser sí o no"
    cobros = None
    if "cobros" in datos:
        cobros, error = validar_cobros(datos.get("cobros"))
        if error:
            return None, error
    return {"titulo": titulo, "rol": rol, "detalle": detalle, "pantalla": pantalla,
            "destacado": bool(destacado), "cobros": cobros}, None


def estado_flujos(db_path: str) -> list[dict]:
    """Los flujos en su orden, cada uno con sus pasos por número y cada paso
    con sus momentos de cobro."""
    cobros_por_paso: dict[int, list[dict]] = {}
    for c in listar_cobros_pasos_flujo(db_path):
        cobros_por_paso.setdefault(c["paso_id"], []).append(
            {"id": c["id"], "porcentaje": c["porcentaje"], "descripcion": c["descripcion"]})
    pasos_por_flujo: dict[int, list[dict]] = {}
    for p in listar_pasos_flujos(db_path):
        pasos_por_flujo.setdefault(p["flujo_id"], []).append({
            "id": p["id"], "numero": p["numero"], "titulo": p["titulo"],
            "rol": p["rol"], "detalle": p["detalle"] or "", "pantalla": p["pantalla"],
            "destacado": bool(p["destacado"]),
            "cobros": cobros_por_paso.get(p["id"], []),
        })
    return [{"id": f["id"], "nombre": f["nombre"], "descripcion": f["descripcion"] or "",
             "orden": f["orden"], "pasos": pasos_por_flujo.get(f["id"], [])}
            for f in listar_flujos(db_path)]
