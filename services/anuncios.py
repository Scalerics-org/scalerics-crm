"""Los anuncios que estan corriendo, con que hacer con cada uno.

El panel sabia que campana trajo cada lead y cuanto costo, pero la campana no
es la unidad sobre la que se decide: adentro de una campana conviven cinco
anuncios y uno se puede estar llevando la mitad de la plata sin traer a nadie.
Esto baja al grano del anuncio, que es donde esta la palanca.

**La recomendacion la calculan reglas, no un modelo.** La IA esta apagada a
pedido de Juan, y ademas una regla se puede discutir: cada una cita los numeros
que la sostienen y se puede ir a comprobarlos. Un parrafo generado no.

**Los umbrales son el todo, y la evidencia se mide en plata.** Una
recomendacion que se dispara con dos leads es ruido con tono de autoridad; una
que nunca se dispara tampoco sirve. La primera version media la evidencia en
leads y, contra la cuenta real, dejo 17 de 19 anuncios en "todavia no alcanza
para opinar" — incluido uno que habia gastado 26 centavos, al que le decia que
"faltan unos 5 leads".

Se mide en `oportunidad`: cuantos leads DEBERIA haber comprado lo que gasto el
anuncio, al costo habitual de la cuenta. Con eso, "gasto poco" y "gasto de
sobra y no trajo nada" se separan solos, sin un umbral en dolares que haya que
recalibrar cada vez que cambia el rubro.

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

# CUANTA EVIDENCIA HACE FALTA PARA OPINAR.
#
# La primera version media esto en leads: "menos de 5 leads, no opino". Corrida
# contra la cuenta real dio 17 de 19 anuncios en "todavia no alcanza", y a uno
# que habia gastado 26 centavos le decia "faltan unos 5 leads". Inutil: a 16
# dolares el lead, con 26 centavos no se compra ni la centesima parte de uno.
#
# El error era medir la evidencia en leads. Se mide en plata, y en plata
# relativa al costo de la cuenta: `oportunidad` es cuantos leads DEBERIA haber
# comprado lo que gasto este anuncio, al costo habitual. Eso sirve igual en una
# cuenta de 5 dolares el lead que en una de 50, y responde la pregunta de
# verdad: "¿ya gasto lo suficiente como para que la ausencia de leads
# signifique algo?".
#
# Por debajo de 1, el anuncio no gasto ni lo que cuesta un lead: no hay nada
# que decir, y decir "faltan 5 leads" es ruido con tono de autoridad.
OPORTUNIDAD_PARA_ARRANCAR = 1.0

# Con 3 leads de oportunidad desperdiciados, cero leads ya no es mala suerte.
OPORTUNIDAD_PARA_JUZGAR = 3.0

# Para comparar CPL contra CPL sigue haciendo falta un minimo de leads: con 1
# lead el CPL del anuncio es un numero con demasiado ruido. Pero ahora esto NO
# bloquea las otras reglas, solo las que comparan costos.
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
    """Un renglon por anuncio en curso, con dos juegos de numeros.

    Los del PERIODO salen del `JOIN` acotado por fechas y son los que se
    muestran arriba de la tarjeta. Los de TODA LA VIDA salen de las subconsultas
    y no miran el periodo.

    **Entran los que gastaron en el PERIODO, prendidos o no.** Al principio
    filtraba por `effective_status = ACTIVE` y el resultado era un hibrido sin
    sentido: eligiendo mayo mostraba "lo que corre hoy y ademas gasto en mayo",
    o sea escondia justo las publicidades que estaban al aire en mayo. Lo
    pregunto Juan: "si pongo en mayo no me aparecen las publicidades que se
    corrian en esos dias".

    Los que siguen prendidos van primero, y cada uno trae `corriendo` para que
    el panel los pueda distinguir.

    Los dos juegos de numeros hacen falta y no son intercambiables:

    * `desde` tiene que ser cuando el anuncio arranco de verdad. Sacado del
      `MIN` acotado daba el borde del periodo: mirando setiembre decia "desde
      el 3/9" de anuncios que venian corriendo desde junio, y la misma pantalla
      contestaba distinto segun la ventana elegida.
    * La pregunta "¿lo apago?" no es sobre un mes del calendario, es sobre el
      anuncio. Un anuncio que se llevo 300 sin traer a nadie merece el mismo
      veredicto se lo mire en setiembre o en el trimestre.
    """
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
                   MAX(i.currency)    AS moneda,
                   (SELECT MIN(date)  FROM meta_ad_insights t
                     WHERE t.ad_id = a.ad_id AND (t.spend > 0 OR t.leads > 0))
                     AS desde,
                   (SELECT SUM(spend) FROM meta_ad_insights t
                     WHERE t.ad_id = a.ad_id) AS gasto_total,
                   (SELECT SUM(leads) FROM meta_ad_insights t
                     WHERE t.ad_id = a.ad_id) AS leads_total,
                   (SELECT SUM(impressions) FROM meta_ad_insights t
                     WHERE t.ad_id = a.ad_id) AS impresiones_total,
                   (SELECT SUM(clicks) FROM meta_ad_insights t
                     WHERE t.ad_id = a.ad_id) AS clics_total
              FROM meta_ads a
              JOIN meta_ad_insights i ON i.ad_id = a.ad_id
             WHERE i.date BETWEEN ? AND ?
             GROUP BY a.ad_id
             HAVING SUM(i.spend) > 0 OR SUM(i.leads) > 0
             ORDER BY (a.effective_status = ?) DESC, SUM(i.spend) DESC
        """, (desde, hasta, ESTADO_EN_CURSO)).fetchall()
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


def _recomendar(a: dict, referencia_cpl, ctr_rec, ctr_viejo) -> dict:
    """Que hacer con este anuncio. La primera regla que aplica, manda.

    El orden es por gravedad, no por elegancia: un anuncio que gasto de sobra y
    no trajo a nadie no necesita que le ajusten el presupuesto, necesita que lo
    apaguen.

    `referencia_cpl` es contra que se mide lo caro: la mediana de lo que esta
    corriendo, no un numero fijo. 20 dolares por lead es carisimo en un rubro y
    regalado en otro, y el panel no tiene por que saber en cual esta.
    """
    cita = [f"anuncio.{a['ad_id']}.gasto", f"anuncio.{a['ad_id']}.leads"]
    plata = f"{a['gasto_total']:.2f}"

    # A uno que ya esta apagado no se le dice "apagalo". Lo util de verlo es
    # otra cosa: darse cuenta de que se apago uno que venia rindiendo.
    if not a.get("corriendo", True):
        if (referencia_cpl and a["cpl_total"]
                and a["leads_total"] >= LEADS_MINIMOS
                and a["cpl_total"] <= referencia_cpl * _BARATO):
            return {
                "accion": "revivir",
                "texto": (f"Está apagado y era de los que mejor rendían: "
                          f"{a['cpl_total']:.2f} por lead contra "
                          f"{referencia_cpl:.2f} de la mediana. Si volvés a "
                          "pautar algo parecido, empezá por acá."),
                "metricas_citadas": cita + [f"anuncio.{a['ad_id']}.cpl"],
            }
        return {
            "accion": "apagado",
            "texto": (f"Está apagado. Mientras corrió se llevó {plata} y trajo "
                      f"{a['leads_total']} lead"
                      f"{'s' if a['leads_total'] != 1 else ''}"
                      + (f", a {a['cpl_total']:.2f} cada uno."
                         if a["cpl_total"] else ".")),
            "metricas_citadas": cita,
        }

    # Cuantos leads deberia haber comprado esta plata al costo habitual de la
    # cuenta. Es la medida de cuanta evidencia hay, y no depende de la escala.
    oportunidad = (a["gasto_total"] / referencia_cpl) if referencia_cpl else None

    if oportunidad is not None and oportunidad < OPORTUNIDAD_PARA_ARRANCAR:
        return {
            "accion": "esperar",
            "texto": (f"Recién arranca: lleva {plata} gastados y al costo "
                      "habitual de la cuenta eso todavía no alcanza ni para "
                      "un lead. Dejalo correr antes de mirarlo."),
            "metricas_citadas": cita,
        }

    if not a["leads_total"]:
        if oportunidad is not None and oportunidad >= OPORTUNIDAD_PARA_JUZGAR:
            return {
                "accion": "apagar",
                "texto": (f"Se llevó {plata} y no trajo un solo lead. Al costo "
                          "habitual eso ya tendría que haber traído "
                          f"{oportunidad:.0f}. Es plata que no está comprando "
                          "nada: apagalo y pasá ese presupuesto a otro."),
                "metricas_citadas": cita,
            }
        return {
            "accion": "esperar",
            "texto": (f"Todavía no trajo ninguno, pero con {plata} gastados "
                      "tampoco alcanza para decir que no funciona. Miralo de "
                      "nuevo cuando haya gastado el doble."),
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

    if referencia_cpl and a["cpl_total"]:
        # Para decir "esta caro" alcanza con UNA de las dos: o ya trajo
        # suficientes leads como para que su CPL sea confiable, o gasto
        # suficiente como para que la diferencia no sea casualidad.
        hay_con_que = (a["leads_total"] >= LEADS_MINIMOS
                       or (oportunidad or 0) >= OPORTUNIDAD_PARA_JUZGAR)
        if hay_con_que and a["cpl_total"] >= referencia_cpl * _CARO:
            return {
                "accion": "ajustar",
                "texto": (f"Cada lead te sale {a['cpl_total']:.2f} y la mitad de los "
                          f"que están corriendo salen {referencia_cpl:.2f} o "
                          "menos. Bajale el presupuesto y miralo una semana, "
                          "o cambiale el público."),
                "metricas_citadas": cita + [f"anuncio.{a['ad_id']}.cpl"],
            }
        # Para "subile" se piden las DOS condiciones: recomendar poner mas
        # plata sobre poca evidencia es el mas caro de los dos errores.
        if a["leads_total"] >= LEADS_MINIMOS and a["cpl_total"] <= referencia_cpl * _BARATO:
            return {
                "accion": "subir",
                "texto": (f"Es de los que mejor rinden: {a['cpl_total']:.2f} por "
                          f"lead contra {referencia_cpl:.2f} de la mediana. "
                          "Subile el presupuesto."),
                "metricas_citadas": cita + [f"anuncio.{a['ad_id']}.cpl"],
            }

    if a["leads_total"] < LEADS_MINIMOS:
        # Sin juicio de valor: con 1 lead, tanto un costo bueno como uno malo
        # pueden ser suerte. Decir "va bien" de un lead que salio el triple de
        # la mediana seria peor que no decir nada.
        return {
            "accion": "esperar",
            "texto": (f"Todavía no alcanza para compararlo: {a['leads_total']} "
                      f"lead{'s' if a['leads_total'] != 1 else ''} a "
                      f"{a['cpl_total']:.2f} puede ser suerte para cualquiera de los "
                      "dos lados. Dejalo correr."),
            "metricas_citadas": cita,
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
        gasto_total = round(float(f["gasto_total"] or 0), 2)
        leads_total = int(f["leads_total"] or 0)
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
            # Si sigue al aire hoy. Meta llama ACTIVE solo al anuncio cuyo
            # conjunto y campana tambien estan prendidos, que es justo lo que
            # significa "se esta pautando".
            "corriendo": f["effective_status"] == ESTADO_EN_CURSO,
            "estado": f["effective_status"],
            # Lo del periodo elegido: es lo que se muestra arriba de la tarjeta.
            "gasto": gasto,
            "leads": leads,
            "impresiones": impresiones,
            "clics": clics,
            "cpl": _costo(gasto, leads),
            "ctr": _tasa(clics, impresiones),
            "tasa_lead": _tasa(leads, clics),
            # Y lo de toda la vida del anuncio, que no depende de la ventana.
            # `desde` es cuando arranco de verdad: sacado del periodo daba el
            # borde de la ventana y la misma pantalla contestaba distinto
            # segun que rango estuviera elegido.
            "desde": f["desde"],
            "gasto_total": gasto_total,
            "leads_total": leads_total,
            "cpl_total": _costo(gasto_total, leads_total),
            "ctr_total": _tasa(int(f["clics_total"] or 0),
                               int(f["impresiones_total"] or 0)),
        })

    # La mediana sale de los numeros de toda la vida, igual que las reglas: con
    # los del periodo, un mes flojo correria la vara para todos a la vez y
    # nadie quedaria "caro" nunca.
    # La vara sale de lo que esta corriendo hoy: es contra eso que tiene
    # sentido comparar. Meter los apagados de hace meses la correria hacia un
    # pasado que ya no es la referencia de nadie.
    mediana_cpl = _mediana([a["cpl_total"] for a in salida
                            if a["corriendo"] and a["leads_total"] >= LEADS_MINIMOS])

    # Si ninguno llego a esa cantidad de leads no hay mediana, y sin referencia
    # todas las reglas se caen a "dejar" — justo cuando la cuenta viene floja,
    # que es cuando mas falta hace opinar. El respaldo es el CPL del conjunto.
    if mediana_cpl is None:
        vivos = [a for a in salida if a["corriendo"]] or salida
        mediana_cpl = _costo(sum(a["gasto_total"] for a in vivos),
                             sum(a["leads_total"] for a in vivos))

    for a in salida:
        rec, viejo = _ctr_reciente(db_path, a["ad_id"], hoy)
        # La recomendacion mira toda la vida del anuncio, no el periodo: "¿lo
        # apago?" no es una pregunta sobre un mes del calendario. Los numeros
        # que cita tambien se muestran en la tarjeta, asi que se pueden
        # comprobar.
        a["recomendacion"] = _recomendar(a, mediana_cpl, rec, viejo)
        a["mediana_cpl"] = mediana_cpl
        # Cuantos leads deberia haber comprado lo que gasto, al costo habitual.
        # Va al dossier porque es el numero que sostiene la recomendacion.
        a["oportunidad"] = (round(a["gasto_total"] / mediana_cpl, 2)
                            if mediana_cpl else None)
    return salida


def resumen_en_curso(db_path: str, desde: str, hasta: str) -> dict:
    """El encabezado de la seccion, con las cuentas separadas.

    `gasto` es lo del periodo elegido y cuenta TODOS los anuncios que gastaron,
    prendidos o no: es la plata que se puso en esas fechas. `corriendo` dice
    cuantos de esos siguen al aire hoy.

    `gasto_total` y `desde` miran toda la vida de esos anuncios, que es la
    respuesta a "cuanto se va pautando desde tal fecha".

    Estaban mezclados: el numero era del periodo y la fecha era el borde de la
    ventana, asi que juntos decian algo que no era cierto en ninguna de las dos
    lecturas.
    """
    filas = _filas(db_path, desde, hasta)
    gasto = round(sum(float(f["gasto"] or 0) for f in filas), 2)
    leads = sum(int(f["leads"] or 0) for f in filas)
    gasto_total = round(sum(float(f["gasto_total"] or 0) for f in filas), 2)
    leads_total = sum(int(f["leads_total"] or 0) for f in filas)
    fechas = [f["desde"] for f in filas if f["desde"]]
    corriendo = sum(1 for f in filas
                    if f["effective_status"] == ESTADO_EN_CURSO)
    return {
        "anuncios": len(filas),
        "corriendo": corriendo,
        "apagados": len(filas) - corriendo,
        "gasto": gasto,
        "leads": leads,
        "cpl": _costo(gasto, leads),
        "gasto_total": gasto_total,
        "leads_total": leads_total,
        "cpl_total": _costo(gasto_total, leads_total),
        "desde": min(fechas) if fechas else None,
        "moneda": filas[0]["moneda"] if filas else None,
    }
