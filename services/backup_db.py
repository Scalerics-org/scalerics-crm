"""Backup diario de la base del CRM: copia local en el volumen y copia en R2.

Antes de esto la unica red eran los snapshots de Fly, que viven cinco dias y
viven en Fly. Esto agrega una copia fuera de Fly, con 30 dias de historia.

Una corrida:

  1. Copia consistente en caliente con la API de backup de SQLite
     (`sqlite3.connect(origen).backup(destino)`), NO copiando el archivo: con
     la app escribiendo, un `cp` de `leads.db` puede salir con paginas de dos
     momentos distintos, y encima en WAL lo ultimo vive en `leads.db-wal`.
  2. `PRAGMA integrity_check` sobre la copia. Si no da "ok", se aborta: subir
     una copia rota es peor que no subir nada, porque desplaza a una buena.
  3. gzip, con nombre `crm-leads-AAAA-MM-DD.db.gz` (fecha de Montevideo).
  4. Subida a R2 bajo `crm/` (el bot de WhatsApp usa `wa-service/` en el mismo
     bucket).
  5. Retencion: en R2 se borran los `crm/crm-leads-*.db.gz` de mas de 30 dias;
     en `/data/backups` quedan los ultimos 3 (el volumen es de 1 GB).

R2 se habla con S3 firmado a mano (SigV4 con hmac/hashlib) sobre `requests`,
que ya es dependencia. Son tres operaciones (PUT, GET de listado y DELETE) y
boto3 + botocore agregan ~80 MB a la imagen para eso.

Sin los secrets R2_* hace solo la copia local, lo loguea una vez por arranque
y no falla.
"""

import datetime as _dt
import gzip
import hashlib
import hmac
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

import pytz
import requests

from services import email_service
from services.corridas import marcar_corrida, puede_correr, ultima_corrida

logger = logging.getLogger(__name__)

MVD = pytz.timezone("America/Montevideo")

PREFIJO_R2 = "crm/"
RETENCION_R2_DIAS = 30
LOCALES_A_GUARDAR = 3
NOMBRE_JOB = "backup_db"
NOMBRE_AVISO = "backup_db_aviso"
_DEMORA_ARRANQUE_S = 300
_CADA_24_HORAS = 24 * 60 * 60
_VARIABLES_R2 = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")

_PATRON_NOMBRE = re.compile(r"^crm-leads-(\d{4}-\d{2}-\d{2})\.db\.gz$")

# Dos corridas a la vez (la diaria y el boton) pisarian el mismo temporal.
_candado = threading.Lock()
# "backup a R2 desactivado" se dice una vez por proceso, no en cada corrida.
_ya_se_aviso_sin_r2 = False


# ── fechas ───────────────────────────────────────────────────────────────────

def hoy_mvd(ahora_utc: _dt.datetime | None = None) -> _dt.date:
    """El dia en Montevideo. El servidor corre en UTC: de 21 a 24 de
    Montevideo en UTC ya es manana, y el backup quedaria con la fecha que no es."""
    base = ahora_utc or _dt.datetime.now(pytz.utc)
    if base.tzinfo is None:
        base = pytz.utc.localize(base)
    return base.astimezone(MVD).date()


def nombre_backup(ahora_utc: _dt.datetime | None = None) -> str:
    return f"crm-leads-{hoy_mvd(ahora_utc).isoformat()}.db.gz"


def fecha_de_nombre(nombre: str) -> _dt.date | None:
    """La fecha de un `crm-leads-AAAA-MM-DD.db.gz` (con o sin prefijo), o None."""
    m = _PATRON_NOMBRE.match(nombre.rsplit("/", 1)[-1])
    if not m:
        return None
    try:
        return _dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None


# ── R2 (S3 con SigV4 a mano) ─────────────────────────────────────────────────

def _hmac(clave: bytes, texto: str) -> bytes:
    return hmac.new(clave, texto.encode("utf-8"), hashlib.sha256).digest()


def _enc(valor: str, seguro: str = "-_.~") -> str:
    return quote(valor, safe=seguro)


def firmar_sigv4(metodo: str, host: str, ruta: str, query: dict, headers: dict,
                 hash_cuerpo: str, access_key: str, secret_key: str,
                 amz_date: str, region: str = "auto", servicio: str = "s3") -> str:
    """Devuelve el header Authorization de AWS Signature V4.

    `headers` son los que se firman, ademas de host (x-amz-date y
    x-amz-content-sha256 tienen que venir adentro). `amz_date` en formato
    AAAAMMDDTHHMMSSZ. Es la receta de la documentacion de AWS al pie de la
    letra; hay un test contra el ejemplo publicado por AWS.
    """
    fecha = amz_date[:8]
    firmados = {k.lower(): " ".join(str(v).strip().split()) for k, v in headers.items()}
    firmados["host"] = host
    nombres = sorted(firmados)
    headers_canonicos = "".join(f"{k}:{firmados[k]}\n" for k in nombres)
    lista_firmados = ";".join(nombres)
    query_canonica = "&".join(
        f"{_enc(str(k))}={_enc(str(v))}" for k, v in sorted(query.items())
    )
    uri_canonica = _enc(ruta, seguro="/-_.~")
    pedido_canonico = "\n".join([
        metodo.upper(), uri_canonica, query_canonica,
        headers_canonicos, lista_firmados, hash_cuerpo,
    ])
    alcance = f"{fecha}/{region}/{servicio}/aws4_request"
    a_firmar = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, alcance,
        hashlib.sha256(pedido_canonico.encode("utf-8")).hexdigest(),
    ])
    k = _hmac(("AWS4" + secret_key).encode("utf-8"), fecha)
    k = _hmac(k, region)
    k = _hmac(k, servicio)
    k = _hmac(k, "aws4_request")
    firma = hmac.new(k, a_firmar.encode("utf-8"), hashlib.sha256).hexdigest()
    return (f"AWS4-HMAC-SHA256 Credential={access_key}/{alcance}, "
            f"SignedHeaders={lista_firmados}, Signature={firma}")


_HASH_VACIO = hashlib.sha256(b"").hexdigest()
_NS_S3 = "{http://s3.amazonaws.com/doc/2006-03-01/}"


class ErrorR2(RuntimeError):
    pass


class ClienteR2:
    """Lo minimo de S3 que hace falta contra R2: subir, listar y borrar."""

    def __init__(self, account_id: str, access_key: str, secret_key: str, bucket: str,
                 http=None, timeout: float = 120):
        self.host = f"{account_id}.r2.cloudflarestorage.com"
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.http = http or requests
        self.timeout = timeout

    def _pedido(self, metodo: str, clave: str = "", query: dict | None = None,
                cuerpo=None, hash_cuerpo: str = _HASH_VACIO, extra: dict | None = None):
        query = query or {}
        ruta = f"/{self.bucket}" + (f"/{clave}" if clave else "")
        amz_date = _dt.datetime.now(pytz.utc).strftime("%Y%m%dT%H%M%SZ")
        headers = {"x-amz-content-sha256": hash_cuerpo, "x-amz-date": amz_date}
        headers["Authorization"] = firmar_sigv4(
            metodo, self.host, ruta, query, headers, hash_cuerpo,
            self.access_key, self.secret_key, amz_date,
        )
        if extra:
            headers.update(extra)
        url = f"https://{self.host}{_enc(ruta, seguro='/-_.~')}"
        if query:
            url += "?" + "&".join(f"{_enc(str(k))}={_enc(str(v))}" for k, v in sorted(query.items()))
        r = self.http.request(metodo, url, headers=headers, data=cuerpo, timeout=self.timeout)
        if r.status_code >= 300:
            raise ErrorR2(f"R2 {metodo} {clave or '(listado)'}: HTTP {r.status_code} {r.text[:300]}")
        return r

    def subir(self, clave: str, ruta_local: str) -> None:
        h = hashlib.sha256()
        with open(ruta_local, "rb") as f:
            for bloque in iter(lambda: f.read(1024 * 1024), b""):
                h.update(bloque)
        tam = os.path.getsize(ruta_local)
        with open(ruta_local, "rb") as f:
            self._pedido("PUT", clave, cuerpo=f, hash_cuerpo=h.hexdigest(),
                         extra={"Content-Length": str(tam),
                                "Content-Type": "application/gzip"})

    def listar(self, prefijo: str) -> list[dict]:
        salida, token = [], None
        while True:
            query = {"list-type": "2", "prefix": prefijo}
            if token:
                query["continuation-token"] = token
            raiz = ET.fromstring(self._pedido("GET", query=query).content)
            for c in raiz.iter(f"{_NS_S3}Contents"):
                salida.append({
                    "clave": c.findtext(f"{_NS_S3}Key") or "",
                    "tamano": int(c.findtext(f"{_NS_S3}Size") or 0),
                    "modificado": c.findtext(f"{_NS_S3}LastModified") or "",
                })
            if (raiz.findtext(f"{_NS_S3}IsTruncated") or "").lower() != "true":
                return salida
            token = raiz.findtext(f"{_NS_S3}NextContinuationToken")
            if not token:
                return salida

    def borrar(self, clave: str) -> None:
        self._pedido("DELETE", clave)


def r2_configurado() -> bool:
    return all(os.environ.get(v, "").strip() for v in _VARIABLES_R2)


def cliente_desde_entorno() -> ClienteR2 | None:
    if not r2_configurado():
        return None
    return ClienteR2(
        os.environ["R2_ACCOUNT_ID"].strip(), os.environ["R2_ACCESS_KEY_ID"].strip(),
        os.environ["R2_SECRET_ACCESS_KEY"].strip(), os.environ["R2_BUCKET"].strip(),
    )


def _avisar_sin_r2_una_vez() -> None:
    global _ya_se_aviso_sin_r2
    if not _ya_se_aviso_sin_r2:
        _ya_se_aviso_sin_r2 = True
        logger.warning("backup a R2 desactivado: faltan R2_* (se hace solo la copia local)")


# ── pasos ────────────────────────────────────────────────────────────────────

def dir_backups_de(db_path: str) -> str:
    """`/data/leads.db` -> `/data/backups`."""
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")


def copiar_consistente(origen: str, destino: str) -> str:
    """Copia en caliente con la API de backup y devuelve el integrity_check.

    `backup()` con pages=-1 copia todo en un paso bajo un lock de lectura: la
    copia es una foto de un unico momento aunque otra conexion este
    escribiendo. La copia se pasa a journal DELETE para que sea un solo
    archivo (la base de produccion esta en WAL).
    """
    src = sqlite3.connect(origen, timeout=30)
    dst = sqlite3.connect(destino)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
        filas = dst.execute("PRAGMA integrity_check").fetchall()
    finally:
        dst.close()
        src.close()
    return "\n".join(str(f[0]) for f in filas)


def comprimir(origen: str, destino: str) -> int:
    with open(origen, "rb") as fi, gzip.open(destino, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo, length=1024 * 1024)
    return os.path.getsize(destino)


def retener_locales(carpeta: str, cuantos: int = LOCALES_A_GUARDAR) -> list[str]:
    """Deja los `cuantos` backups mas nuevos (por la fecha del nombre)."""
    try:
        nombres = [n for n in os.listdir(carpeta) if fecha_de_nombre(n)]
    except FileNotFoundError:
        return []
    nombres.sort(key=fecha_de_nombre, reverse=True)
    borrados = []
    for n in nombres[cuantos:]:
        try:
            os.remove(os.path.join(carpeta, n))
            borrados.append(n)
        except OSError as e:
            logger.warning(f"backup: no se pudo borrar el local {n}: {e}")
    return borrados


def retener_r2(cliente, hoy: _dt.date, dias: int = RETENCION_R2_DIAS) -> list[str]:
    """Borra de `crm/` los backups con fecha de hace mas de `dias` dias.

    Solo toca archivos con el nombre de este job: cualquier otra cosa que
    alguien deje bajo `crm/` no se borra.
    """
    limite = hoy - _dt.timedelta(days=dias)
    borrados = []
    for obj in cliente.listar(PREFIJO_R2):
        clave = obj["clave"]
        if not clave.startswith(PREFIJO_R2) or "/" in clave[len(PREFIJO_R2):]:
            continue
        fecha = fecha_de_nombre(clave)
        if fecha and fecha < limite:
            cliente.borrar(clave)
            borrados.append(clave)
    return borrados


def listar_backups_r2(cliente, limite: int = 30) -> list[dict]:
    objetos = [o for o in cliente.listar(PREFIJO_R2) if fecha_de_nombre(o["clave"])]
    objetos.sort(key=lambda o: o["clave"], reverse=True)
    return [{"nombre": o["clave"], "tamano": o["tamano"], "fecha": o["modificado"]}
            for o in objetos[:limite]]


def listar_backups_locales(db_path: str) -> list[dict]:
    carpeta = dir_backups_de(db_path)
    try:
        nombres = [n for n in os.listdir(carpeta) if fecha_de_nombre(n)]
    except FileNotFoundError:
        return []
    nombres.sort(reverse=True)
    return [{"nombre": n, "tamano": os.path.getsize(os.path.join(carpeta, n))} for n in nombres]


# ── aviso ────────────────────────────────────────────────────────────────────

def _destinatarios(db_path: str) -> list[str]:
    # Los mismos que la alerta del token de Meta (`send_meta_token_alert`).
    from routes.meta import _get_admin_emails
    return _get_admin_emails(db_path)


def avisar_falla(db_path: str, detalle: str) -> bool:
    """Manda el aviso de falla, como mucho uno por dia. True si salio."""
    if not puede_correr(db_path, NOMBRE_AVISO, cada_horas=24):
        logger.info(f"backup: aviso de falla silenciado, ya se mando uno el "
                    f"{ultima_corrida(db_path, NOMBRE_AVISO)}")
        return False
    marcar_corrida(db_path, NOMBRE_AVISO)
    enviado = False
    for mail in _destinatarios(db_path):
        try:
            enviado = email_service.send_backup_alert(mail, detalle) or enviado
        except Exception as e:
            logger.error(f"backup: no se pudo mandar el aviso a {mail}: {e}")
    return enviado


# ── corrida ──────────────────────────────────────────────────────────────────

_DEL_ENTORNO = object()


def hacer_backup(db_path: str, carpeta: str | None = None, cliente=_DEL_ENTORNO,
                 ahora_utc: _dt.datetime | None = None) -> dict:
    """Una corrida completa. Nunca levanta: devuelve el resumen.

    `cliente` es para los tests; por defecto se arma desde R2_*. `None`
    fuerza solo copia local.
    """
    if not _candado.acquire(blocking=False):
        return {"ok": False, "en_curso": True, "error": "ya hay un backup en curso"}
    try:
        return _hacer_backup(db_path, carpeta, cliente, ahora_utc)
    finally:
        _candado.release()


def _hacer_backup(db_path, carpeta, cliente, ahora_utc) -> dict:
    inicio = time.monotonic()
    carpeta = carpeta or dir_backups_de(db_path)
    if cliente is _DEL_ENTORNO:
        cliente = cliente_desde_entorno()
    nombre = nombre_backup(ahora_utc)
    res = {
        "ok": False, "nombre": nombre, "tamano_db": 0, "tamano": 0,
        "integridad": None, "r2_activo": cliente is not None, "subido": False,
        "clave_r2": None, "borrados_r2": [], "borrados_locales": [], "error": None,
    }
    if cliente is None:
        _avisar_sin_r2_una_vez()

    temporal = os.path.join(carpeta, nombre[:-len(".gz")] + ".tmp")
    final = os.path.join(carpeta, nombre)
    parcial = final + ".part"
    try:
        os.makedirs(carpeta, exist_ok=True)
        for resto in (temporal, temporal + "-journal", parcial):
            if os.path.exists(resto):
                os.remove(resto)
        res["tamano_db"] = os.path.getsize(db_path)

        integridad = copiar_consistente(db_path, temporal)
        res["integridad"] = integridad
        if integridad.strip().lower() != "ok":
            raise _FallaBackup(f"integrity_check de la copia dio: {integridad[:500]}")

        # Se comprime a .part y se renombra: un gz a medio escribir no puede
        # pasar por un backup valido ni desplazar a uno bueno en la retencion.
        res["tamano"] = comprimir(temporal, parcial)
        os.replace(parcial, final)
        os.remove(temporal)
        res["borrados_locales"] = retener_locales(carpeta)

        if cliente is not None:
            clave = PREFIJO_R2 + nombre
            try:
                cliente.subir(clave, final)
            except Exception as e:
                raise _FallaBackup(f"fallo la subida a R2 de {clave}: {e}") from e
            res["subido"] = True
            res["clave_r2"] = clave
            try:
                res["borrados_r2"] = retener_r2(cliente, hoy_mvd(ahora_utc))
            except Exception as e:
                raise _FallaBackup(f"subido, pero fallo la retencion en R2: {e}") from e
        res["ok"] = True
    except _FallaBackup as e:
        res["error"] = str(e)
    except Exception as e:
        res["error"] = f"excepcion: {type(e).__name__}: {e}"
        logger.exception("backup: excepcion inesperada")
    finally:
        for resto in (temporal, temporal + "-journal", parcial):
            try:
                if os.path.exists(resto):
                    os.remove(resto)
            except OSError:
                pass

    res["duracion_s"] = round(time.monotonic() - inicio, 2)
    if res["ok"]:
        logger.info(
            f"backup OK: {nombre}, {res['tamano']} bytes (base {res['tamano_db']}), "
            f"subido a R2: {'si' if res['subido'] else 'no'}, "
            f"borrados R2: {len(res['borrados_r2'])}, locales: {len(res['borrados_locales'])}"
        )
    else:
        logger.error(f"backup FALLO: {res['error']}")
        try:
            avisar_falla(db_path, res["error"])
        except Exception as e:
            logger.error(f"backup: tambien fallo el aviso por mail: {e}")
    return res


class _FallaBackup(Exception):
    pass


def corrida_diaria(db_path: str, cliente=_DEL_ENTORNO):
    """El backup de hoy, o None si ya corrio dentro del plazo (arranque por deploy)."""
    if not puede_correr(db_path, NOMBRE_JOB):
        logger.info(
            f"Backup: ya corrio el {ultima_corrida(db_path, NOMBRE_JOB)}, "
            f"se saltea esta corrida (arranque por deploy)"
        )
        return None
    marcar_corrida(db_path, NOMBRE_JOB)
    return hacer_backup(db_path, cliente=cliente)


def backup_activo() -> bool:
    return os.environ.get("BACKUP_DB", "on").strip().lower() not in ("off", "0", "false", "no")


def start_backup_db(app) -> None:
    """Corre una vez por dia, la primera `_DEMORA_ARRANQUE_S` s despues del boot.

    Prendido por defecto (BACKUP_DB=off lo apaga): no le manda nada a terceros,
    y la marca en `corridas` evita que cada deploy haga otra copia.
    """
    if not backup_activo():
        logger.info("Backup diario apagado por BACKUP_DB=off")
        return
    if not r2_configurado():
        _avisar_sin_r2_una_vez()

    def _loop():
        time.sleep(_DEMORA_ARRANQUE_S)
        while True:
            try:
                corrida_diaria(app.config["DB_PATH"])
            except Exception as e:
                logger.warning(f"Backup: {e}")
            time.sleep(_CADA_24_HORAS)

    threading.Thread(target=_loop, daemon=True, name="backup-db").start()
    logger.info(
        f"Backup diario ACTIVO: una corrida por dia, la primera {_DEMORA_ARRANQUE_S}s "
        f"despues de este arranque (R2: {'si' if r2_configurado() else 'no'})"
    )


# ── restauracion al arrancar ─────────────────────────────────────────────────

NOMBRE_RESTAURACION = "restore.db"


def aplicar_restauracion_pendiente(db_path: str, ahora_utc: _dt.datetime | None = None) -> dict | None:
    """Si hay `restore.db` al lado de la base, la pone en su lugar. None si no hay.

    La llama `start.sh` ANTES de levantar gunicorn: es el unico momento en que
    nadie tiene la base abierta, asi que "parar, reemplazar, arrancar" es
    subir el archivo y reiniciar la maquina. Reemplazar `leads.db` con la app
    corriendo deja al proceso viejo escribiendo en el archivo desplazado (y en
    su -wal) y lo ultimo se pierde.

    - Si `restore.db` no pasa `integrity_check`, no se toca nada y se renombra
      a `restore.db.rechazado-<fecha>`, para que no se reintente en cada boot.
    - La base actual no se borra: queda como `leads.db.antes-restore-<fecha>`,
      con su WAL volcado adentro antes de moverla.
    """
    carpeta = os.path.dirname(os.path.abspath(db_path))
    pendiente = os.path.join(carpeta, NOMBRE_RESTAURACION)
    if not os.path.exists(pendiente):
        return None
    marca = (ahora_utc or _dt.datetime.now(pytz.utc)).strftime("%Y%m%d-%H%M%S")
    try:
        conn = sqlite3.connect(pendiente)
        try:
            integridad = "\n".join(str(f[0]) for f in conn.execute("PRAGMA integrity_check"))
            conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        integridad = f"no abre como SQLite: {e}"
    if integridad.strip().lower() != "ok":
        rechazado = f"{pendiente}.rechazado-{marca}"
        os.replace(pendiente, rechazado)
        logger.error(f"restauracion RECHAZADA, la base no se toco: {integridad[:300]} "
                     f"(el archivo quedo en {rechazado})")
        return {"ok": False, "error": integridad[:500], "rechazado": rechazado}

    anterior = None
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path, timeout=30)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                conn.close()
        except sqlite3.DatabaseError as e:
            logger.warning(f"restauracion: no se pudo volcar el WAL de la base actual ({e})")
        anterior = f"{db_path}.antes-restore-{marca}"
        os.replace(db_path, anterior)
        for sufijo in ("-wal", "-shm"):
            if os.path.exists(db_path + sufijo):
                os.replace(db_path + sufijo, anterior + sufijo)
    os.replace(pendiente, db_path)
    logger.warning(f"restauracion APLICADA: {db_path} reemplazada por restore.db "
                   f"(la anterior quedo en {anterior})")
    return {"ok": True, "anterior": anterior}


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--aplicar-restauracion":
        print(json.dumps(aplicar_restauracion_pendiente(args[1]), ensure_ascii=False))
        raise SystemExit(0)
    if len(args) != 1:
        print("uso: python -m services.backup_db <db_path>\n"
              "     python -m services.backup_db --aplicar-restauracion <db_path>")
        raise SystemExit(2)
    print(json.dumps(hacer_backup(args[0]), indent=2, ensure_ascii=False))
