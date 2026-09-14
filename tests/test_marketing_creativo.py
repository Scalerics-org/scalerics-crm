"""La ruta que sirve la foto del anuncio.

No se redirige a la URL de Meta a proposito: viene firmada, caduca en dias y
despues deja imagenes rotas sin que nada avise. El archivo es nuestro y sale de
la carpeta del volumen.

La mitad de este archivo es sobre que NO se puede servir. Una ruta que toma un
id de la URL y lo convierte en una ruta de disco es exactamente la forma de
regalar archivos del servidor si no se la ata corto.
"""

import os

import pytest

import dashboard
from database import _connect, init_db


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-prueba")
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    app = dashboard.create_app(ruta)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def cliente(app):
    return app.test_client()


CAB = {"x-admin-token": "token-de-prueba"}


def _anuncio(db, ad_id, archivo):
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO meta_ads (ad_id, ad_name, effective_status, "
            "imagen_archivo) VALUES (?,?,?,?)",
            (ad_id, "Un anuncio", "ACTIVE", archivo))
        conn.commit()
    finally:
        conn.close()


def _creativo(tmp_path, ad_id):
    carpeta = tmp_path / "creativos"
    carpeta.mkdir(exist_ok=True)
    f = carpeta / f"{ad_id}.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0falsa")
    return str(f)


def test_sirve_la_imagen_del_anuncio(cliente, app, tmp_path):
    archivo = _creativo(tmp_path, "123")
    _anuncio(app.config["DB_PATH"], "123", archivo)
    r = cliente.get("/api/marketing/creativo/123", headers=CAB)
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"
    assert r.data.startswith(b"\xff\xd8")


def test_un_anuncio_sin_foto_da_404(cliente, app):
    """Los de video no tienen: Meta no da el still con los permisos de la app.
    404 y no un 500, que es lo que pasaria si se intentara abrir None."""
    _anuncio(app.config["DB_PATH"], "123", None)
    assert cliente.get("/api/marketing/creativo/123",
                       headers=CAB).status_code == 404


def test_un_anuncio_que_no_existe_da_404(cliente):
    assert cliente.get("/api/marketing/creativo/999",
                       headers=CAB).status_code == 404


def test_si_el_archivo_se_borro_da_404(cliente, app, tmp_path):
    """La fila puede quedar apuntando a un archivo que ya no esta."""
    archivo = _creativo(tmp_path, "123")
    _anuncio(app.config["DB_PATH"], "123", archivo)
    os.remove(archivo)
    assert cliente.get("/api/marketing/creativo/123",
                       headers=CAB).status_code == 404


# ── Lo que no se puede servir ──────────────────────────────────────────────

@pytest.mark.parametrize("id_malo", [
    "../../etc/passwd",
    "..%2f..%2fetc%2fpasswd",
    "1/../../secreto",
    "abc",
    "",
    "1" * 40,
])
def test_un_id_que_no_es_un_numero_no_llega_al_disco(cliente, id_malo):
    """Un id de Meta es un numero largo y nada mas. Sin esta guarda, un id con
    `..` serviria cualquier archivo del servidor."""
    r = cliente.get(f"/api/marketing/creativo/{id_malo}", headers=CAB)
    assert r.status_code in (400, 404, 308)
    assert r.mimetype != "image/jpeg"


def test_una_ruta_fuera_de_la_carpeta_no_se_sirve(cliente, app, tmp_path):
    """Doble llave: aunque en la base quedara guardada una ruta a otro lado
    —por un bug del sync o por una base tocada a mano— no se sirve."""
    afuera = tmp_path / "secreto.jpg"
    afuera.write_bytes(b"\xff\xd8nada")
    _anuncio(app.config["DB_PATH"], "123", str(afuera))
    assert cliente.get("/api/marketing/creativo/123",
                       headers=CAB).status_code == 404


def test_sin_permiso_no_se_ve(app, tmp_path):
    """El gasto publicitario esta detras del panel `marketing`: la foto del
    anuncio es parte de lo mismo."""
    archivo = _creativo(tmp_path, "123")
    _anuncio(app.config["DB_PATH"], "123", archivo)
    r = app.test_client().get("/api/marketing/creativo/123")
    assert r.status_code in (401, 403, 302)
