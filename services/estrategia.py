"""Estrategia mensual de marketing: el PDF que sube el de marketing (o Juan).

Cada mes se sube un PDF con la estrategia: piezas, colores y objetivos. Se guarda
en la base (con el volumen de Fly, sobrevive a los deploys) y la sesion de Claude
Code lo lee por `/api/instagram-bot/estrategias/` (ver routes/instagram.py), arma
un resumen —linea de color, piezas, objetivos— y lo devuelve como `plan`. Con ese
plan el agente rehace las piezas que fallan siguiendo la misma linea.

No se extrae texto en el servidor: Claude lee el PDF entero, con imagenes.
"""

import json
import re
from datetime import datetime

from database import _connect

MAX_BYTES = 15 * 1024 * 1024
MAX_PLAN = 30_000
_MES = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class NoSePuede(Exception):
    pass


def guardar(db_path: str, mes: str, nombre: str, datos: bytes, usuario: str) -> int:
    if not _MES.match(mes or ""):
        raise NoSePuede("El mes tiene que ser AAAA-MM.")
    if not datos.startswith(b"%PDF-"):
        raise NoSePuede("El archivo tiene que ser un PDF.")
    if len(datos) > MAX_BYTES:
        raise NoSePuede("El PDF pesa más de 15 MB.")
    nombre = re.sub(r"[^\w.\- ]", "_", (nombre or "estrategia.pdf"))[:120]
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO estrategias_mensuales (mes, nombre_archivo, pdf, tamano, subido_por, subido_en) "
            "VALUES (?,?,?,?,?,?)",
            (mes, nombre, datos, len(datos), usuario, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def listar(db_path: str, limite: int = 12) -> list[dict]:
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT id, mes, nombre_archivo, tamano, subido_por, subido_en, estado, plan_json, plan_en "
            "FROM estrategias_mensuales ORDER BY mes DESC, id DESC LIMIT ?", (limite,)).fetchall()
    finally:
        conn.close()
    salida = []
    for f in filas:
        d = dict(f)
        crudo = d.pop("plan_json")
        d["plan"] = json.loads(crudo) if crudo else None
        salida.append(d)
    return salida


def pdf_de(db_path: str, est_id: int) -> tuple[str, bytes] | None:
    conn = _connect(db_path)
    try:
        f = conn.execute("SELECT nombre_archivo, pdf FROM estrategias_mensuales WHERE id = ?",
                         (est_id,)).fetchone()
    finally:
        conn.close()
    return (f["nombre_archivo"], bytes(f["pdf"])) if f else None


def pendientes(db_path: str) -> list[dict]:
    """Los PDF que el agente todavia no leyo."""
    return [{k: e[k] for k in ("id", "mes", "nombre_archivo", "tamano", "subido_por", "subido_en")}
            for e in listar(db_path, 50) if e["estado"] == "nueva"]


def guardar_plan(db_path: str, est_id: int, plan: dict) -> None:
    if not isinstance(plan, dict) or not plan:
        raise NoSePuede("El plan tiene que ser un objeto con contenido.")
    crudo = json.dumps(plan, ensure_ascii=False)
    if len(crudo) > MAX_PLAN:
        raise NoSePuede("El plan es demasiado largo.")
    conn = _connect(db_path)
    try:
        cur = conn.execute("UPDATE estrategias_mensuales SET plan_json = ?, plan_en = ?, estado = 'leida' "
                           "WHERE id = ?", (crudo, datetime.now().strftime("%Y-%m-%d %H:%M"), est_id))
        conn.commit()
        if not cur.rowcount:
            raise NoSePuede("No existe esa estrategia.")
    finally:
        conn.close()


def vigente(db_path: str, mes: str) -> dict | None:
    """La estrategia mas reciente del mes (con su plan, si el agente ya la leyo)."""
    return next((e for e in listar(db_path, 50) if e["mes"] == mes), None)
