"""Backup diario de la base (services/backup_db.py) y sus rutas de admin.

Sin red: R2 se reemplaza por un cliente falso inyectable, y el cliente real se
prueba con un `http` falso y contra los ejemplos de firma publicados por AWS.
"""

import datetime as dt
import gzip
import logging
import os
import sqlite3
import threading

import pytest
import pytz
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import backup_db as bk


# ── fixtures ─────────────────────────────────────────────────────────────────

def _utc(*a):
    return dt.datetime(*a, tzinfo=pytz.utc)


HOY = _utc(2026, 9, 15, 15, 0)  # 12:00 en Montevideo


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    monkeypatch.setattr(bk, "_ya_se_aviso_sin_r2", False)
    for v in bk._VARIABLES_R2:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.delenv("BACKUP_DB", raising=False)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    conn = sqlite3.connect(ruta)
    conn.execute("CREATE TABLE prueba (id INTEGER PRIMARY KEY, lote INTEGER, texto TEXT)")
    conn.executemany("INSERT INTO prueba (lote, texto) VALUES (?, ?)",
                     [(i // 10, "x" * 50) for i in range(500)])
    conn.commit()
    conn.close()
    return ruta


class R2Falso:
    def __init__(self, objetos=None, falla_subida=False):
        self.objetos = dict(objetos or {})
        self.falla_subida = falla_subida
        self.subidas, self.borradas = [], []

    def subir(self, clave, ruta):
        if self.falla_subida:
            raise RuntimeError("HTTP 500 InternalError")
        with open(ruta, "rb") as f:
            self.objetos[clave] = f.read()
        self.subidas.append(clave)

    def listar(self, prefijo):
        return [{"clave": k, "tamano": len(v), "modificado": "2026-09-15T12:00:00.000Z"}
                for k, v in self.objetos.items() if k.startswith(prefijo)]

    def borrar(self, clave):
        self.objetos.pop(clave)
        self.borradas.append(clave)


@pytest.fixture
def avisos(monkeypatch):
    enviados = []
    monkeypatch.setattr(bk, "_destinatarios", lambda db_path: ["admin@scalerics.com"])
    monkeypatch.setattr(bk.email_service, "send_backup_alert",
                        lambda to, detalle: enviados.append((to, detalle)) or True)
    return enviados


def _tablas_y_filas(ruta):
    conn = sqlite3.connect(ruta)
    try:
        tablas = sorted(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tablas}
    finally:
        conn.close()


# ── copia ────────────────────────────────────────────────────────────────────

def test_la_copia_es_consistente_mientras_otra_conexion_escribe(db, tmp_path):
    """El escritor inserta de a 10 filas por transaccion: una copia que mezcle
    dos momentos, o que agarre una transaccion a medias, no da multiplo de 10."""
    parar = threading.Event()
    escritas = []

    def _escritor():
        conn = sqlite3.connect(db, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        lote = 1000
        while not parar.is_set():
            with conn:
                conn.executemany("INSERT INTO prueba (lote, texto) VALUES (?, ?)",
                                 [(lote, "y" * 200)] * 10)
            escritas.append(lote)
            lote += 1
        conn.close()

    hilo = threading.Thread(target=_escritor)
    hilo.start()
    try:
        cuentas = []
        for i in range(6):
            destino = str(tmp_path / f"copia{i}.db")
            assert bk.copiar_consistente(db, destino) == "ok"
            conn = sqlite3.connect(destino)
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            (n,) = conn.execute("SELECT COUNT(*) FROM prueba").fetchone()
            conn.close()
            assert n % 10 == 0 and n >= 500
            cuentas.append(n)
    finally:
        parar.set()
        hilo.join()
    assert escritas, "el escritor no llego a escribir: el test no probo nada"
    assert cuentas == sorted(cuentas)


def test_el_gzip_se_abre_como_sqlite_con_las_mismas_tablas_y_filas(db, tmp_path):
    res = bk.hacer_backup(db, cliente=None, ahora_utc=HOY)
    assert res["ok"] is True, res
    gz = tmp_path / "backups" / "crm-leads-2026-09-15.db.gz"
    assert gz.exists() and res["tamano"] == gz.stat().st_size
    restaurada = tmp_path / "restaurada.db"
    with gzip.open(gz, "rb") as fi:
        restaurada.write_bytes(fi.read())
    assert _tablas_y_filas(str(restaurada)) == _tablas_y_filas(db)
    assert _tablas_y_filas(str(restaurada))["prueba"] == 500
    # Un solo archivo: sin -wal colgando al lado ni temporales.
    assert sorted(os.listdir(tmp_path / "backups")) == ["crm-leads-2026-09-15.db.gz"]


def test_el_nombre_usa_la_fecha_de_montevideo():
    # 01:30 UTC del 16 = 22:30 del 15 en Montevideo.
    assert bk.nombre_backup(_utc(2026, 9, 16, 1, 30)) == "crm-leads-2026-09-15.db.gz"
    assert bk.nombre_backup(_utc(2026, 9, 16, 3, 30)) == "crm-leads-2026-09-16.db.gz"


def test_el_archivo_del_backup_lleva_la_fecha_de_montevideo(db, tmp_path):
    res = bk.hacer_backup(db, cliente=None, ahora_utc=_utc(2026, 9, 16, 1, 30))
    assert res["nombre"] == "crm-leads-2026-09-15.db.gz"
    assert (tmp_path / "backups" / "crm-leads-2026-09-15.db.gz").exists()


# ── retencion ────────────────────────────────────────────────────────────────

def test_la_retencion_en_r2_borra_lo_de_mas_de_30_dias(db, avisos):
    r2 = R2Falso({
        "crm/crm-leads-2026-08-01.db.gz": b"viejo",
        "crm/crm-leads-2026-08-15.db.gz": b"31 dias",
        "crm/crm-leads-2026-08-16.db.gz": b"30 dias justos",
        "crm/crm-leads-2026-09-14.db.gz": b"ayer",
        "crm/notas.txt": b"no es un backup",
        "wa-service/wa-2026-01-01.tar.gz": b"del bot",
    })
    res = bk.hacer_backup(db, cliente=r2, ahora_utc=HOY)
    assert res["ok"] and res["subido"]
    assert sorted(res["borrados_r2"]) == ["crm/crm-leads-2026-08-01.db.gz",
                                          "crm/crm-leads-2026-08-15.db.gz"]
    assert sorted(r2.objetos) == [
        "crm/crm-leads-2026-08-16.db.gz", "crm/crm-leads-2026-09-14.db.gz",
        "crm/crm-leads-2026-09-15.db.gz", "crm/notas.txt",
        "wa-service/wa-2026-01-01.tar.gz",
    ]
    assert avisos == []


def test_quedan_solo_los_ultimos_3_locales(db, tmp_path):
    carpeta = tmp_path / "backups"
    carpeta.mkdir()
    for d in ("2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14"):
        (carpeta / f"crm-leads-{d}.db.gz").write_bytes(b"x")
    (carpeta / "otra-cosa.txt").write_bytes(b"x")
    res = bk.hacer_backup(db, cliente=None, ahora_utc=HOY)
    assert res["ok"]
    assert sorted(os.listdir(carpeta)) == [
        "crm-leads-2026-09-13.db.gz", "crm-leads-2026-09-14.db.gz",
        "crm-leads-2026-09-15.db.gz", "otra-cosa.txt",
    ]
    assert len(res["borrados_locales"]) == 3


# ── sin secrets ──────────────────────────────────────────────────────────────

def test_sin_secrets_hace_solo_la_copia_local_y_lo_dice_una_vez(db, tmp_path, avisos, caplog):
    caplog.set_level(logging.INFO, logger="services.backup_db")
    r1 = bk.hacer_backup(db, ahora_utc=HOY)
    r2 = bk.hacer_backup(db, ahora_utc=_utc(2026, 9, 16, 15, 0))
    for r in (r1, r2):
        assert r["ok"] is True and r["subido"] is False and r["r2_activo"] is False
    assert (tmp_path / "backups" / "crm-leads-2026-09-16.db.gz").exists()
    mensajes = [m for m in caplog.messages if "backup a R2 desactivado: faltan R2_*" in m]
    assert len(mensajes) == 1
    assert avisos == []


def test_con_secrets_arma_el_cliente_real(monkeypatch):
    for v in bk._VARIABLES_R2:
        monkeypatch.setenv(v, "valor")
    c = bk.cliente_desde_entorno()
    assert isinstance(c, bk.ClienteR2)
    assert c.host == "valor.r2.cloudflarestorage.com"
    monkeypatch.setenv("R2_BUCKET", " ")
    assert bk.cliente_desde_entorno() is None


# ── avisos ───────────────────────────────────────────────────────────────────

def test_una_falla_de_integridad_manda_un_solo_aviso(db, tmp_path, avisos, monkeypatch):
    monkeypatch.setattr(bk, "copiar_consistente",
                        lambda o, d: "*** in database main ***\nPage 7: btreeInitPage() returns error code 11")
    r2 = R2Falso()
    for _ in range(3):
        res = bk.hacer_backup(db, cliente=r2, ahora_utc=HOY)
        assert res["ok"] is False and "integrity_check" in res["error"]
    assert len(avisos) == 1 and "integrity_check" in avisos[0][1]
    assert r2.subidas == []
    assert not (tmp_path / "backups" / "crm-leads-2026-09-15.db.gz").exists()


def test_una_falla_de_subida_manda_un_solo_aviso(db, avisos):
    r2 = R2Falso(falla_subida=True)
    for _ in range(2):
        res = bk.hacer_backup(db, cliente=r2, ahora_utc=HOY)
        assert res["ok"] is False and res["subido"] is False
        assert "subida a R2" in res["error"]
    assert len(avisos) == 1


def test_una_excepcion_tambien_avisa_y_no_deja_temporales(db, tmp_path, avisos, monkeypatch):
    def _explota(o, d):
        raise OSError("No space left on device")
    monkeypatch.setattr(bk, "comprimir", _explota)
    res = bk.hacer_backup(db, cliente=None, ahora_utc=HOY)
    assert res["ok"] is False and "No space left" in res["error"]
    assert len(avisos) == 1
    assert os.listdir(tmp_path / "backups") == []


def test_el_aviso_vuelve_a_salir_al_dia_siguiente(db, avisos):
    assert bk.avisar_falla(db, "uno") is True
    assert bk.avisar_falla(db, "dos") is False
    conn = sqlite3.connect(db)
    conn.execute("UPDATE corridas SET ultima = datetime('now','-25 hours') WHERE nombre = ?",
                 (bk.NOMBRE_AVISO,))
    conn.commit()
    conn.close()
    assert bk.avisar_falla(db, "tres") is True
    assert [d for _, d in avisos] == ["uno", "tres"]


def test_send_backup_alert_sin_clave_queda_en_stub():
    from services.email_service import send_backup_alert
    assert send_backup_alert("admin@scalerics.com", "<b>fallo</b>") is True


# ── programacion ─────────────────────────────────────────────────────────────

def test_ya_corrio_hoy_no_repite(db, caplog):
    caplog.set_level(logging.INFO, logger="services.backup_db")
    r2 = R2Falso()
    assert bk.corrida_diaria(db, cliente=r2)["ok"] is True
    assert bk.corrida_diaria(db, cliente=r2) is None
    assert len(r2.subidas) == 1
    assert any("se saltea esta corrida (arranque por deploy)" in m for m in caplog.messages)


def test_backup_db_off_no_arranca_el_hilo(monkeypatch):
    arrancados = []
    monkeypatch.setattr(bk.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda s: arrancados.append(k)})())
    monkeypatch.setenv("BACKUP_DB", "off")
    bk.start_backup_db(object())
    assert arrancados == []
    monkeypatch.delenv("BACKUP_DB")
    bk.start_backup_db(object())
    assert len(arrancados) == 1 and arrancados[0]["daemon"] is True


def test_dos_corridas_a_la_vez_no_se_pisan(db):
    assert bk._candado.acquire(blocking=False)
    try:
        res = bk.hacer_backup(db, cliente=None, ahora_utc=HOY)
    finally:
        bk._candado.release()
    assert res["en_curso"] is True and res["ok"] is False


# ── cliente R2 real, sin red ─────────────────────────────────────────────────

def test_firma_sigv4_contra_el_ejemplo_de_aws_get_object():
    vacio = bk._HASH_VACIO
    auth = bk.firmar_sigv4(
        "GET", "examplebucket.s3.amazonaws.com", "/test.txt", {},
        {"Range": "bytes=0-9", "x-amz-content-sha256": vacio, "x-amz-date": "20130524T000000Z"},
        vacio, "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "20130524T000000Z", region="us-east-1",
    )
    assert auth == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_firma_sigv4_contra_el_ejemplo_de_aws_list_objects():
    vacio = bk._HASH_VACIO
    auth = bk.firmar_sigv4(
        "GET", "examplebucket.s3.amazonaws.com", "/", {"max-keys": "2", "prefix": "J"},
        {"x-amz-content-sha256": vacio, "x-amz-date": "20130524T000000Z"},
        vacio, "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "20130524T000000Z", region="us-east-1",
    )
    assert auth.endswith(
        "Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7")


class _Resp:
    def __init__(self, status=200, content=b""):
        self.status_code, self.content = status, content
        self.text = content.decode("utf-8", "replace")


class _HttpFalso:
    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.pedidos = []

    def request(self, metodo, url, headers=None, data=None, timeout=None):
        cuerpo = data.read() if hasattr(data, "read") else data
        self.pedidos.append({"metodo": metodo, "url": url, "headers": headers, "cuerpo": cuerpo})
        return self.respuestas.pop(0)


_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>b</Name><IsTruncated>{trunc}</IsTruncated>{token}
  <Contents><Key>{clave}</Key><LastModified>2026-09-15T12:00:00.000Z</LastModified><Size>{tam}</Size></Contents>
</ListBucketResult>"""


def test_cliente_r2_sube_lista_con_paginas_y_borra(tmp_path):
    archivo = tmp_path / "crm-leads-2026-09-15.db.gz"
    archivo.write_bytes(b"contenido")
    http = _HttpFalso([
        _Resp(200),
        _Resp(200, _XML.format(trunc="true", token="<NextContinuationToken>t2</NextContinuationToken>",
                               clave="crm/crm-leads-2026-09-14.db.gz", tam=10).encode()),
        _Resp(200, _XML.format(trunc="false", token="",
                               clave="crm/crm-leads-2026-09-15.db.gz", tam=9).encode()),
        _Resp(204),
        _Resp(403, b"<Error>AccessDenied</Error>"),
    ])
    c = bk.ClienteR2("cuenta", "AK", "SK", "bucket", http=http)

    c.subir("crm/crm-leads-2026-09-15.db.gz", str(archivo))
    put = http.pedidos[0]
    assert put["metodo"] == "PUT"
    assert put["url"] == "https://cuenta.r2.cloudflarestorage.com/bucket/crm/crm-leads-2026-09-15.db.gz"
    assert put["cuerpo"] == b"contenido"
    assert put["headers"]["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AK/")
    assert "/auto/s3/aws4_request" in put["headers"]["Authorization"]
    import hashlib
    assert put["headers"]["x-amz-content-sha256"] == hashlib.sha256(b"contenido").hexdigest()

    lista = c.listar("crm/")
    assert [o["clave"] for o in lista] == ["crm/crm-leads-2026-09-14.db.gz",
                                          "crm/crm-leads-2026-09-15.db.gz"]
    assert "continuation-token=t2" in http.pedidos[2]["url"]
    assert "prefix=crm%2F" in http.pedidos[1]["url"]

    c.borrar("crm/crm-leads-2026-09-14.db.gz")
    assert http.pedidos[3]["metodo"] == "DELETE"

    with pytest.raises(bk.ErrorR2, match="403"):
        c.borrar("crm/x")


# ── restauracion al arrancar ─────────────────────────────────────────────────

def test_la_restauracion_pendiente_reemplaza_la_base_y_guarda_la_anterior(db, tmp_path):
    otra = tmp_path / "otra.db"
    conn = sqlite3.connect(otra)
    conn.execute("CREATE TABLE solo_restore (x)")
    conn.execute("INSERT INTO solo_restore VALUES (1)")
    conn.commit()
    conn.close()
    os.replace(otra, tmp_path / "restore.db")

    res = bk.aplicar_restauracion_pendiente(db, ahora_utc=HOY)
    assert res["ok"] is True
    assert _tablas_y_filas(db) == {"solo_restore": 1}
    assert not (tmp_path / "restore.db").exists()
    anterior = tmp_path / "leads.db.antes-restore-20260915-150000"
    assert _tablas_y_filas(str(anterior))["prueba"] == 500


def test_la_restauracion_rechaza_un_archivo_roto_y_no_toca_la_base(db, tmp_path):
    (tmp_path / "restore.db").write_bytes(b"esto no es una base sqlite " * 200)
    antes = _tablas_y_filas(db)
    res = bk.aplicar_restauracion_pendiente(db, ahora_utc=HOY)
    assert res["ok"] is False
    assert _tablas_y_filas(db) == antes
    assert not (tmp_path / "restore.db").exists()
    assert (tmp_path / "restore.db.rechazado-20260915-150000").exists()


def test_sin_restauracion_pendiente_no_hace_nada(db):
    assert bk.aplicar_restauracion_pendiente(db) is None


def test_start_sh_aplica_la_restauracion_antes_de_levantar_la_app():
    from pathlib import Path
    texto = (Path(__file__).resolve().parents[1] / "start.sh").read_text(encoding="utf-8")
    assert texto.index("--aplicar-restauracion") < texto.index("exec gunicorn")


# ── rutas ────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _cli(app, email):
    uid = create_user(app.config["_DB"], name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "test"
    return c


def test_las_rutas_de_backup_dan_403_a_quien_no_es_admin(app, tmp_path):
    c = _cli(app, "ventas@scalerics.com")
    assert c.post("/api/admin/backup-ahora").status_code == 403
    assert c.get("/api/admin/backups").status_code == 403
    assert not (tmp_path / "backups").exists()


def test_backup_ahora_da_200_a_admin_y_devuelve_el_resumen(app, tmp_path):
    c = _cli(app, "raiz@scalerics.com")
    r = c.post("/api/admin/backup-ahora")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True and d["subido"] is False
    assert d["nombre"].startswith("crm-leads-") and d["tamano"] > 0

    r = c.get("/api/admin/backups")
    assert r.status_code == 200
    d2 = r.get_json()
    assert d2["r2_activo"] is False and d2["backups"] == []
    assert [x["nombre"] for x in d2["locales"]] == [d["nombre"]]


def test_listar_backups_de_r2(app, monkeypatch):
    r2 = R2Falso({"crm/crm-leads-2026-09-14.db.gz": b"12", "crm/crm-leads-2026-09-15.db.gz": b"123",
                  "crm/otra.txt": b"x"})
    monkeypatch.setattr(bk, "cliente_desde_entorno", lambda: r2)
    c = _cli(app, "raiz@scalerics.com")
    d = c.get("/api/admin/backups").get_json()
    assert [(b["nombre"], b["tamano"]) for b in d["backups"]] == [
        ("crm/crm-leads-2026-09-15.db.gz", 3), ("crm/crm-leads-2026-09-14.db.gz", 2)]


def test_la_pagina_principal_y_la_de_administracion_siguen_en_200(app):
    c = _cli(app, "raiz@scalerics.com")
    assert c.get("/").status_code == 200
    r = c.get("/admin/users")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Hacer backup ahora" in html and "/api/admin/backup-ahora" in html
