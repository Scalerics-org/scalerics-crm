"""Los anuncios que estan corriendo, con que hacer con cada uno.

El panel sabia que campana trajo cada lead y cuanto costo, pero la campana no
es la unidad sobre la que se decide: adentro de una campana conviven cinco
anuncios y uno se puede estar llevando la mitad de la plata sin traer a nadie.
Esto baja al grano del anuncio, que es donde esta la palanca.

**La recomendacion la calculan reglas, no un modelo.** La IA esta apagada a
pedido de Juan, y ademas una regla se puede discutir: cada una cita los numeros
que la sostienen y se puede ir a comprobarlos. Un parrafo generado no.

**Los umbrales son el todo.** Una recomendacion que se dispara con dos leads no
es una recomendacion, es ruido con tono de autoridad. Ya paso en `hallazgos.py`,
donde "se traba en cierres" salia siempre porque se comparaba contra una campana
de un solo lead. Por eso `LEADS_MINIMOS` y `GASTO_MINIMO_PARA_OPINAR`: por
debajo de ahi la respuesta honesta es "todavia no se sabe".

**Lo caro es relativo a la cuenta.** El corte no es un CPL fijo sino la mediana
de lo que esta corriendo: 20 dolares por lead es carisimo en un rubro y regalado
en otro, y el panel no tiene por que saber en cual esta.
"""

import logging

from database import _connect

logger = logging.getLogger(__name__)

# Meta llama ACTIVE solo al anuncio cuyo conjunto y campana tambien estan
# prendidos. Los otros tres estados (PAUSED, ADSET_PAUSED, CAMPAIGN_PAUSED)
# significan apagado por algun lado, y no son decisiones de hoy.
ESTADO_EN_CURSO = "ACTIVE"

# Por debajo de esto no hay opinion. Son ~2 CPL historicos de la cuenta: con
# menos plata gastada, cero leads entra comodo dentro de la mala suerte.
GASTO_MINIMO_PARA_OPINAR = 30.0

# Y por debajo de esta cantidad de leads, el CPL del anuncio es un numero con
# demasiado ruido para compararlo contra nadie.
LEADS_MINIMOS = 5

# Cuanto tiene que alejarse de la mediana para que valga la pena decir algo.
_CARO = 1.5
_BARATO = 0.7

# La ventana con la que se mira si un anuncio se quemo, y cuanto tiene que
# haber caido su CTR contra el propio historial para llamarlo asi.
DIAS_RECIENTES = 7
_CAIDA_DE_CTR = 0.6
_IMPRESIONES_MINIMAS_RECIENTES = 500


def _tasa(numerador, denominador):
    if not denominador:
        return None
    return round(numerador / denominador, 4)


def _costo(gasto, cantidad):
    if not cantidad:
        return None
    return round(gasto / cantidad, 2)


def _mediana(valores):
    vivos = sorted(v for v in valores if v is not None)
    if not vivos:
        return None
    medio = len(vivos) // 2
    if len(vivos) % 2:
        return vivos[medio]
    return (vivos[medio - 1] + vivos[medio]) / 2


def _restar_dias(iso_fecha: str, dias: int) -> str:
    from datetime import date, timedelta

    y, m, d = (int(x) for x in iso_fecha[:10].split("-"))
    return (date(y, m, d) - timedelta(days=dias)).isoformat()


def _filas(db_path: str, desde: str, hasta: str) -> list:
    """Un renglon por anuncio en curso, con su gasto del periodo sumado."""
    conn = _connect(db_path)
    try:
        return conn.execute("""
            SELECT a.ad_id, a.ad_name, a.campaign_name, a.adset_name,
                   a.object_type, a.titulo, a.cuerpo, a.imagen_archivo,
                   a.effective_status,
                   SUM(i.spend)       AS gasto,
                   SUM(i.leads)       AS leads,
                   SUM(i.impressions) AS impresiones,
                   SUM(i.clicks)      AS clics,
                   MIN(i.date)        AS desde,
                   MAX(i.date)        AS hasta,
                   MAX(i.currency)    AS moneda
              FROM meta_ads a
              JOIN meta_ad_insights i ON i.ad_id = a.ad_id
             WHERE a.effective_status = ?
               AND i.date BETWEEN ? AND ?
             GROUP BY a.ad_id
             HAVING SUM(i.spend) > 0 OR SUM(i.leads) > 0
             ORDER BY SUM(i.spend) DESC
        """, (ESTADO_EN_CURSO, desde, hasta)).fetchall()
    finally:
        conn.close()


def _ctr_reciente(db_path: str, ad_id: str, hoy: str) -> tuple:
    """CTR de los ultimos dias y CTR de toda la vida del anuncio.

    Se compara el anuncio contra SI MISMO y no contra los demas: un video y una
    imagen tienen CTR distintos por naturaleza, asi que cruzarlos diria que
    todos los videos estan quemados.
    """
    corte = _restar_dias(hoy, DIAS_RECIENTES)
    conn = _connect(db_path)
    try:
        rec = conn.execute(
            "SELECT SUM(impressions) i, SUM(clicks) c FROM meta_ad_insights "
            "WHERE ad_id = ? AND date > ?", (ad_id, corte)).fetchone()
        viejo = conn.execute(
            "SELECT SUM(impressions) i, SUM(clicks) c FROM meta_ad_insights "
            "WHERE ad_id = ? AND date <= ?", (ad_id, corte)).fetchone()
    finally:
        conn.close()
    return (
        (int(rec["i"] or 0), _tasa(int(rec["c"] or 0), int(rec["i"] or 0))),
        (int(viejo["i"] or 0), _tasa(int(viejo["c"] or 0), int(viejo["i"] or 0))),
    )


def _recomendar(a: dict, mediana_cpl, ctr_rec, ctr_viejo) -> dict:
    """Que hacer con este anuncio. La primera regla que aplica, manda.

    El orden es por gravedad, no por elegancia: un anuncio que gasto de sobra y
    no trajo a nadie no necesita que le ajusten el presupuesto, necesita que lo
    apaguen.
    """
    cita = [f"anuncio.{a['ad_id']}.gasto", f"anuncio.{a['ad_id']}.leads"]

    if a["gasto"] >= GASTO_MINIMO_PARA_OPINAR and not a["leads"]:
        return {
            "accion": "apagar",
            "texto": (f"Se llevó {a['gasto']:.2f} y no trajo un solo lead. "
                      "Es plata que no está comprando nada: apagalo y pasá "
                      "ese presupuesto a otro."),
            "metricas_citadas": cita,
        }

    if a["leads"] < LEADS_MINIMOS:
        falta = LEADS_MINIMOS - a["leads"]
        return {
            "accion": "esperar",
            "texto": (f"Todavía no alcanza para opinar: {a['leads']} "
                      f"lead{'s' if a['leads'] != 1 else ''} "
                      f"{'son' if a['leads'] != 1 else 'es'} muy poco para "
                      f"saber si el costo es real o suerte. Faltan unos "
                      f"{falta} para poder compararlo."),
            "metricas_citadas": cita,
        }

    # Quemado: sigue apareciendo y dejo de interesar. Va antes que el precio
    # porque explica POR QUE se puso caro, que es lo accionable.
    impr_rec, ctr_r = ctr_rec
    _, ctr_v = ctr_viejo
    if (impr_rec >= _IMPRESIONES_MINIMAS_RECIENTES and ctr_r is not None
            and ctr_v and ctr_r < ctr_v * _CAIDA_DE_CTR):
        return {
            "accion": "renovar",
            "texto": (f"Se está gastando: de cada 100 que lo ven, lo clickean "
                      f"{ctr_r * 100:.1f} contra {ctr_v * 100:.1f} que lo "
                      "clickeaban antes. La gente ya lo vio. Cambiale la "
                      "imagen o el texto antes de que se ponga más caro."),
            "metricas_citadas": cita + [f"anuncio.{a['ad_id']}.ctr"],
        }

    if mediana_cpl and a["cpl"]:
        if a["cpl"] >= mediana_cpl * _CARO:
            return {
                "accion": "ajustar",
                "texto": (f"Cada lead te sale {a['cpl']:.2f} y la mitad de los "
                          f"que están corriendo salen {mediana_cpl:.2f} o "
                          "menos. Bajale el presupuesto y miralo una semana, "
                          "o cambiale el público."),
                "metricas_citadas": cita + [f"anuncio.{a['ad_id']}.cpl"],
            }
        if a["cpl"] <= mediana_cpl * _BARATO:
            return {
                "accion": "subir",
                "texto": (f"Es de los que mejor rinden: {a['cpl']:.2f} por "
                          f"lead contra {mediana_cpl:.2f} de la mediana. "
                          "Subile el presupuesto."),
                "metricas_citadas": cita + [f"anuncio.{a['ad_id']}.cpl"],
            }

    return {
        "accion": "dejar",
        "texto": ("Viene en línea con el resto de lo que está corriendo. "
                  "Dejalo como está."),
        "metricas_citadas": cita,
    }


def anuncios_en_curso(db_path: str, desde: str, hasta: str, hoy=None) -> list:
    """Los anuncios prendidos hoy que gastaron en el periodo, con que hacer.

    `hoy` existe para los tests: la ventana de "se quemo" se mide contra la
    fecha de hoy, y un test con fechas fijas no puede depender del reloj.
    """
    from datetime import date

    hoy = hoy or date.today().isoformat()
    filas = _filas(db_path, desde, hasta)

    salida = []
    for f in filas:
        gasto = round(float(f["gasto"] or 0), 2)
        leads = int(f["leads"] or 0)
        impresiones = int(f["impresiones"] or 0)
        clics = int(f["clics"] or 0)
        salida.append({
            "ad_id": f["ad_id"],
            "nombre": f["ad_name"],
            "campana": f["campaign_name"],
            "conjunto": f["adset_name"],
            "tipo": f["object_type"],
            "titulo": f["titulo"],
            "cuerpo": f["cuerpo"],
            "imagen_archivo": f["imagen_archivo"],
            "moneda": f["moneda"],
            "gasto": gasto,
            "leads": leads,
            "impresiones": impresiones,
            "clics": clics,
            "cpl": _costo(gasto, leads),
            "ctr": _tasa(clics, impresiones),
            "tasa_lead": _tasa(leads, clics),
            "desde": f["desde"],
            "hasta": f["hasta"],
        })

    # La mediana se saca de los que tienen suficientes leads: meter los de 1
    # lead la correria hacia donde mande el ruido, y despues el resto se
    # compararia contra esa correccion.
    mediana_cpl = _mediana([a["cpl"] for a in salida
                            if a["leads"] >= LEADS_MINIMOS])

    for a in salida:
        rec, viejo = _ctr_reciente(db_path, a["ad_id"], hoy)
        a["recomendacion"] = _recomendar(a, mediana_cpl, rec, viejo)
        a["mediana_cpl"] = mediana_cpl
    return salida


def resumen_en_curso(db_path: str, desde: str, hasta: str) -> dict:
    """El encabezado de la seccion: cuanto se lleva puesto y desde cuando."""
    filas = _filas(db_path, desde, hasta)
    gasto = round(sum(float(f["gasto"] or 0) for f in filas), 2)
    leads = sum(int(f["leads"] or 0) for f in filas)
    fechas = [f["desde"] for f in filas if f["desde"]]
    return {
        "anuncios": len(filas),
        "gasto": gasto,
        "leads": leads,
        "cpl": _costo(gasto, leads),
        "desde": min(fechas) if fechas else None,
        "moneda": filas[0]["moneda"] if filas else None,
    }
