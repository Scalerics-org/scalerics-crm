"""Lo que salta del dossier, sin pedirle nada a un modelo.

El módulo prometía «una reflexión de cómo estamos y hacia dónde apuntar». Hoy
eso solo lo da el informe de IA, que está apagado y cuesta plata cada vez. Pero
la mayor parte de esa reflexión no necesita un modelo: es mirar el dossier y
aplicar reglas. Lo que sí necesita un modelo es *redactarla* bien.

**Acá no hay nada que validar.** El informe de IA tiene un validador entero
detrás porque un modelo puede inventar un número; estas reglas no pueden, porque
los números los leen del dossier por construcción. Si un hallazgo cita una
métrica, es porque la sacó de ahí.

Qué entra y qué no
------------------
Entra lo que se puede accionar. «Tenés 242 leads» no es un hallazgo: es un KPI,
y ya está arriba en el panel. «Form gastó 981 y no cerró a nadie en 85 leads» sí,
porque de ahí sale una decisión.

Y son pocos a propósito. Veinte hallazgos son cero hallazgos: nadie los lee, y
el que importaba queda perdido entre los otros diecinueve.
"""

import re

# Seis es lo que se lee de un vistazo. Si alguna vez hay más candidatos, el
# orden de `_REGLAS` decide cuáles sobreviven, no el azar.
TOPE = 6

# Abajo de esto, la ausencia de cierres es la muestra y no la campaña: con 3
# leads, cero cierres es lo más probable aunque la campaña sea excelente.
MINIMO_LEADS = 15

# Una caída es un escalón cuando es marcadamente peor que las otras del mismo
# embudo. Un embudo que cae parejo no tiene una etapa para arreglar.
_ESCALON_RELATIVO = 0.55

# Una brecha contable molesta cuando es grande en plata Y en proporción: 10
# dólares sobre 600 es redondeo, 600 sobre 600 es un mes sin cargar.
_BRECHA_MINIMA_USD = 100.0
_BRECHA_MINIMA_PROPORCION = 0.15


def _valor(bloque, sufijo):
    for m in bloque.get("metricas") or []:
        if m["id"].endswith(sufijo):
            return m["valor"]
    return None


def _id(bloque, sufijo):
    for m in bloque.get("metricas") or []:
        if m["id"].endswith(sufijo):
            return m["id"]
    return None


def _campanas_reales(dossier):
    """Las campañas comparables: sin el total y sin las que no son campañas.

    `todas` es la suma y compararla contra sus partes no dice nada. El bucket
    «(sin campaña)» tampoco: son leads que perdieron el origen, así que sus
    tasas describen a los leads que más se trabajaron, no a una campaña.
    """
    return [b for b in dossier.get("campanas") or []
            if b.get("campana") not in ("todas", "(sin campaña)")]


# ── Las reglas ───────────────────────────────────────────────────────────────

def _sin_cierres(dossier):
    """Plata que se fue sin volver, con muestra suficiente para decirlo."""
    salida = []
    for b in _campanas_reales(dossier):
        gasto = _valor(b, ".gasto") or 0
        leads = _valor(b, ".leads_crm") or 0
        cierres = _valor(b, ".cierres")
        if gasto <= 0 or leads < MINIMO_LEADS or cierres is None or cierres > 0:
            continue
        demos = _valor(b, ".demos") or 0
        salida.append({
            "tipo": "sin_cierres",
            "severidad": "alta",
            "titulo": f"{b['campana']} gastó y todavía no cerró a nadie",
            "cuerpo": (
                f"Lleva {_plata(gasto)} y {leads} leads, con {demos} "
                f"{'demo' if demos == 1 else 'demos'} hechas, y ningún cierre. "
                "Con esta muestra ya no alcanza con decir que es mala suerte: "
                "o el lead que trae no compra, o se cae después de la demo."),
            "metricas_citadas": [x for x in (
                _id(b, ".gasto"), _id(b, ".leads_crm"), _id(b, ".demos"),
                _id(b, ".cierres")) if x],
            # Ordena entre sí por plata: la que más se llevó, primero.
            "_peso": gasto,
        })
    return salida


def _escalon(dossier):
    """La etapa donde más gente se cae, cuando se cae marcadamente más ahí."""
    salida = []
    for b in dossier.get("embudo_campanas") or []:
        etapas = b.get("etapas") or []
        if not etapas or (etapas[0].get("n") or 0) < MINIMO_LEADS:
            continue
        # La ULTIMA etapa queda afuera. Cerrar es siempre lo mas dificil, asi
        # que "se traba en cierres" es verdad en casi todos los embudos y no
        # informa nada; y cuando los cierres son cero ya lo dice `sin_cierres`,
        # que ademas trae la plata. Lo que este hallazgo busca es el escalon del
        # MEDIO: gente que ya estaba enganchada y se cayo antes de llegar.
        con_tasa = [e for e in etapas[:-1] if e.get("tasa") is not None]
        if len(con_tasa) < 2:
            continue
        peor = min(con_tasa, key=lambda e: e["tasa"])
        otras = [e["tasa"] for e in con_tasa if e is not peor]
        promedio = sum(otras) / len(otras) if otras else None
        # Marcadamente peor que el resto, no solo la más baja: en un embudo
        # siempre hay una más baja.
        if promedio is None or peor["tasa"] > promedio * _ESCALON_RELATIVO:
            continue
        pasaron = peor.get("n") or 0
        salida.append({
            "tipo": "escalon",
            "severidad": "media",
            "titulo": f"{b['campana']} se traba en «{peor['etiqueta']}»",
            "cuerpo": (
                f"De la etapa anterior pasa solo el {_pct(peor['tasa'])} "
                f"({pasaron}), contra un {_pct(promedio)} promedio en el resto "
                f"del embudo. Es la etapa donde mejorar mueve el total; en las "
                f"otras ya pasa casi todo el mundo. Clave: {peor['clave']}."),
            "metricas_citadas": [f"embudo.{b['campana']}.{peor['clave']}"],
            "_peso": (1 - peor["tasa"]) * (etapas[0].get("n") or 0),
        })
    return salida


def _orden_invertido(dossier):
    """La campaña más barata por lead no es la más barata por demo.

    Es el hallazgo que motivó el módulo: el Administrador de anuncios muestra
    el costo por lead y nada más, así que optimizar por lo que se ve ahí puede
    ir en contra de conseguir reuniones.
    """
    # Con pocos leads el costo por demo es ruido: una campana de 1 lead y 1
    # demo tiene el mejor costo por demo del panel por definicion, y ponerla
    # como referencia produce una conclusion falsa con cara de dato. Es
    # exactamente el error que este modulo existe para no cometer.
    comparables = [b for b in _campanas_reales(dossier)
                   if _valor(b, ".cpl") is not None
                   and _valor(b, ".costo_demo") is not None
                   and (_valor(b, ".leads_crm") or 0) >= MINIMO_LEADS]
    if len(comparables) < 2:
        return []
    barata_lead = min(comparables, key=lambda b: _valor(b, ".cpl"))
    barata_demo = min(comparables, key=lambda b: _valor(b, ".costo_demo"))
    if barata_lead is barata_demo:
        return []
    return [{
        "tipo": "orden_invertido",
        "severidad": "alta",
        "titulo": "La campaña más barata por lead no es la más barata por demo",
        "cuerpo": (
            f"{barata_lead['campana']} trae leads a "
            f"{_plata(_valor(barata_lead, '.cpl'))} —lo más barato— pero cada "
            f"demo le sale {_plata(_valor(barata_lead, '.costo_demo'))}. "
            f"{barata_demo['campana']} paga más por lead "
            f"({_plata(_valor(barata_demo, '.cpl'))}) y consigue demos a "
            f"{_plata(_valor(barata_demo, '.costo_demo'))}. El Administrador de "
            "anuncios solo muestra la primera columna."),
        "metricas_citadas": [x for x in (
            _id(barata_lead, ".cpl"), _id(barata_lead, ".costo_demo"),
            _id(barata_demo, ".cpl"), _id(barata_demo, ".costo_demo")) if x],
        "_peso": 10 ** 6,   # siempre arriba: es la lectura que cambia decisiones
    }]


def _brecha_finanzas(dossier):
    """Meses donde lo que Meta cobró y lo que Finanzas registró no coinciden."""
    por_mes = {}
    for m in dossier.get("conciliacion") or []:
        cal = re.match(r"conciliacion\.(gasto_meta|gasto_cargado|brecha)\.(.+)",
                       m["id"])
        if cal:
            por_mes.setdefault(cal.group(2), {})[cal.group(1)] = m

    salida = []
    for suf, partes in sorted(por_mes.items()):
        brecha = partes.get("brecha")
        meta = partes.get("gasto_meta")
        if not brecha or not meta or brecha["valor"] is None:
            continue
        base = max(abs(meta["valor"] or 0), 1.0)
        if (abs(brecha["valor"]) < _BRECHA_MINIMA_USD
                or abs(brecha["valor"]) / base < _BRECHA_MINIMA_PROPORCION):
            continue
        periodo = suf.replace("_", "-")
        cargado = partes.get("gasto_cargado", {}).get("valor")
        falta = brecha["valor"] > 0
        salida.append({
            "tipo": "brecha_finanzas",
            "severidad": "media",
            "titulo": ("Un mes de pauta sin registrar" if falta
                       else "Un mes con más pauta cargada de la que Meta cobró"),
            "cuerpo": (
                f"En {periodo} Meta cobró {_plata(meta['valor'])} y en Finanzas "
                f"hay {_plata(cargado)}. "
                + ("Falta cargarlo, o el resultado del mes se ve mejor de lo que "
                   "fue." if falta else
                   "Sobra, así que el mes se ve peor de lo que fue.")),
            "metricas_citadas": [meta["id"], brecha["id"]]
                                + ([partes["gasto_cargado"]["id"]]
                                   if "gasto_cargado" in partes else []),
            "_peso": abs(brecha["valor"]),
        })
    return salida


# El orden importa: define qué sobrevive al tope cuando hay muchos candidatos.
_REGLAS = (_orden_invertido, _sin_cierres, _escalon, _brecha_finanzas)


def buscar(dossier: dict) -> list:
    """Los hallazgos del dossier, los más importantes primero y acotados."""
    salida = []
    for regla in _REGLAS:
        encontrados = regla(dossier) or []
        encontrados.sort(key=lambda h: -h.get("_peso", 0))
        salida.extend(encontrados)
    for h in salida:
        h.pop("_peso", None)
    return salida[:TOPE]


# ── Formato ──────────────────────────────────────────────────────────────────
#
# El panel formatea del lado del navegador, pero estos textos se arman acá, asi
# que necesitan su propia version. Mismo criterio es-UY: coma decimal y punto de
# miles.

def _plata(valor):
    if valor is None:
        return "sin datos"
    entero, dec = f"{abs(valor):.2f}".split(".")
    miles = ""
    while len(entero) > 3:
        miles = "." + entero[-3:] + miles
        entero = entero[:-3]
    return ("−" if valor < 0 else "") + entero + miles + "," + dec


def _pct(valor):
    return "sin datos" if valor is None else f"{valor * 100:.0f}%".replace(".", ",")
