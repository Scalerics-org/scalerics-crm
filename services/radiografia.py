"""Arma el dossier completo y lo guarda como snapshot.

El snapshot no es un lujo: sin el no hay "esto cambio respecto de la semana
pasada", que es la mitad de contestar como venimos. Y guardar el dossier entero
—no solo un resumen— es lo que va a permitir auditar despues por que se dijo lo
que se dijo.

**Esta fase no llama a ninguna API de IA.** El snapshot se guarda con
status='sin_ia' y report_json en NULL. El panel funciona igual: los graficos
salen del dossier, no del informe.
"""

import json
import logging

from database import _connect
from services.dossier import (conciliacion, embudo_por_campana,
                              llegada_de_leads, por_campana, por_segmento,
                              recordatorios, serie_mensual,
                              serie_por_campana, serie_semanal, tiempos)

logger = logging.getLogger(__name__)


def construir_dossier(db_path: str, desde: str, hasta: str) -> dict:
    """Todo lo que se puede medir del periodo, en un solo objeto."""
    d = {
        "periodo": {"desde": desde, "hasta": hasta},
        "campanas": por_campana(db_path, desde, hasta),
        "embudo_campanas": embudo_por_campana(db_path, desde, hasta),
        "serie_campanas": serie_por_campana(db_path, desde, hasta),
        "segmentos": por_segmento(db_path, desde, hasta),
        "serie_semanal": serie_semanal(db_path, desde, hasta),
        "conciliacion": conciliacion(db_path, desde, hasta),
        "tiempos": tiempos(db_path, desde, hasta),
        "recordatorios": recordatorios(db_path, desde, hasta),
        "serie_mensual": serie_mensual(db_path, desde, hasta),
        "llegada": llegada_de_leads(db_path, desde, hasta),
    }
    # Va al final porque lee los otros bloques, no la base: los hallazgos son
    # una lectura del dossier, no una consulta mas.
    from services.hallazgos import buscar
    d["hallazgos"] = buscar(d)
    return d


def _todas_las_metricas(dossier: dict):
    """Recorre el dossier y devuelve cada metrica, venga del bloque que venga."""
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


def aplicar_deltas(dossier: dict, anterior) -> dict:
    """Le pone a cada metrica cuanto cambio contra el snapshot anterior.

    Una metrica que no existia antes —una campana que arranco esta semana— se
    queda en None. Eso no es un cambio de cero: es que no habia contra que
    comparar, y son cosas distintas.
    """
    if not anterior:
        return dossier

    previos = {m["id"]: m.get("valor") for m in _todas_las_metricas(anterior)}
    for m in _todas_las_metricas(dossier):
        antes, ahora = previos.get(m["id"]), m.get("valor")
        if antes is None or ahora is None:
            continue
        try:
            m["delta_periodo_anterior"] = round(ahora - antes, 4)
        except TypeError:
            continue      # valores no numericos: no hay delta que calcular
    return dossier


def guardar_snapshot(db_path: str, dossier: dict, resultado=None) -> int:
    """Guarda el dossier, con el informe si lo hay.

    `resultado` es lo que devuelve `services.radiografia_ia.redactar`. Sin él
    —o con la IA apagada— el snapshot queda con `status='sin_ia'` y
    `report_json` en NULL, y el panel funciona igual: los graficos siempre
    salieron del dossier y no del informe.

    Un informe que no valido NO se guarda. Queda el status del error y el
    motivo, para poder mirar despues por que se rechazo.
    """
    r = resultado or {}
    informe = r.get("informe")
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO radiografias (period_start, period_end, dossier_json, "
            "report_json, model, tokens_in, tokens_out, status, error_message) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (dossier["periodo"]["desde"], dossier["periodo"]["hasta"],
             json.dumps(dossier, ensure_ascii=False),
             json.dumps(informe, ensure_ascii=False) if informe else None,
             r.get("model"), r.get("tokens_in"), r.get("tokens_out"),
             r.get("status") or "sin_ia", r.get("error")))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def ultimo_snapshot(db_path: str):
    """El dossier de la corrida anterior, o None si es la primera."""
    conn = _connect(db_path)
    try:
        fila = conn.execute("SELECT dossier_json FROM radiografias "
                            "ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    if not fila:
        return None
    try:
        return json.loads(fila["dossier_json"])
    except (ValueError, TypeError):
        logger.warning("el ultimo snapshot tiene un dossier_json ilegible")
        return None
