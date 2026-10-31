"""Uruguay, Argentina y Chile (pedido de Juan, 29/10: se pauta en los tres).

Meta Ads, Inteligencia marketing y Finanzas se miran de a un país, con las
banderitas de arriba de cada panel. El resto del CRM (calendario, Instagram,
mails, outbound) sigue siendo uno solo.

De qué país es cada cosa:

- Una campaña de Meta: por el nombre. La pauta de los tres países sale de la
  misma cuenta publicitaria, así que la campaña lleva el país como palabra
  suelta ("AR - Restaurantes", "Chile | Fidelidad"). Sin país en el nombre es
  de Uruguay, que es todo lo que había hasta noviembre de 2026.
- Un lead: el país fijo de la ficha si alguien lo puso (los leads que se
  cargan a mano desde el panel de un país), si no el de su campaña, si no el
  prefijo del teléfono (+54, +56). Si nada dice otra cosa, Uruguay.
- Un movimiento de Finanzas: su columna `pais`, que toma el país del panel
  desde el que se cargó. Todo lo anterior al 29/10 es de Uruguay.

El país elegido viaja en la cookie `crm_pais`, que pone el JS de las
banderitas. Sin cookie (los crons, los scripts) no se filtra nada.
"""

import re

PAISES = {
    "UY": {"nombre": "Uruguay", "bandera": "🇺🇾", "prefijo": "598", "moneda": "UYU"},
    "AR": {"nombre": "Argentina", "bandera": "🇦🇷", "prefijo": "54", "moneda": "ARS"},
    "CL": {"nombre": "Chile", "bandera": "🇨🇱", "prefijo": "56", "moneda": "CLP"},
}
POR_DEFECTO = "UY"
COOKIE = "crm_pais"

# Las palabras del nombre de una campaña que dicen el país.
_PALABRAS = {
    "UY": "UY", "URU": "UY", "URUGUAY": "UY",
    "AR": "AR", "ARG": "AR", "ARGENTINA": "AR",
    "CL": "CL", "CHI": "CL", "CHL": "CL", "CHILE": "CL",
}


def valido(pais) -> str | None:
    """El código si es uno de los tres, si no None."""
    codigo = str(pais or "").strip().upper()
    return codigo if codigo in PAISES else None


def de_campana(nombre) -> str | None:
    """El país que nombra la campaña, o None si no nombra ninguno."""
    for palabra in re.split(r"[^0-9A-Za-zÁÉÍÓÚáéíóú]+", str(nombre or "")):
        pais = _PALABRAS.get(palabra.upper())
        if pais:
            return pais
    return None


def de_telefono(telefono) -> str | None:
    """Por el prefijo internacional. Un número sin prefijo (099 123 456) no
    dice nada: lo decide el resto."""
    crudo = str(telefono or "").strip()
    digitos = re.sub(r"\D", "", crudo)
    if not digitos or not (crudo.startswith("+") or crudo.startswith("00") or len(digitos) > 9):
        return None
    digitos = digitos[2:] if crudo.startswith("00") else digitos
    for codigo in ("UY", "AR", "CL"):   # 598 antes que cualquier otro
        if digitos.startswith(PAISES[codigo]["prefijo"]):
            return codigo
    return None


def de_lead(pais_fijo=None, campana=None, telefono=None) -> str:
    return valido(pais_fijo) or de_campana(campana) or de_telefono(telefono) or POR_DEFECTO


def por_pais(bp) -> None:
    """Que las rutas del blueprint vean solo el país de la cookie: los GET leen
    sus filas y lo que se crea desde el resto queda de ese país (ver
    database.aplicar_pais)."""
    from flask import g, request

    from database import poner_pais, sacar_pais

    @bp.before_request
    def _poner_pais():
        if g.get("_pais_fijo"):
            return   # el socio de un país: ya lo puso require_login, sin cookie
        pais = valido(request.cookies.get(COOKIE))
        if pais:
            g._pais_token = poner_pais(pais, "leer" if request.method in ("GET", "HEAD") else "escribir")

    @bp.teardown_request
    def _sacar_pais(_error=None):
        token = g.pop("_pais_token", None)
        if token is not None:
            sacar_pais(token)
