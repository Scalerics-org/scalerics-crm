# Discovery, parte 2: mandar la secuencia — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que los comercios de la cohorte `discovery` reciban dos mails en frío —día 0 y día 7— desde un subdominio propio, con link de baja que funcione desde el primer mail y sin que la campaña pueda alcanzar a ningún lead de Meta ni a un cliente.

**Architecture:** Un módulo nuevo `services/discovery_emails.py` con la forma de `services/meta_reminders.py`, y una tabla `discovery_reminders` con una fila por contacto. Se duplican unas 150 líneas del bucle de envío a propósito: el motor de Meta le manda correo real a terceros todos los días y no se toca. La ruta de baja **se comparte**: `/baja/<token>` pasa a intentar las dos campañas, porque los tokens son UUID únicos y así no hay que publicar una URL nueva.

**Tech Stack:** Python 3, SQLite, Flask, Resend. Tests con pytest.

**Spec:** `docs/superpowers/specs/2026-08-19-discovery-mails-comercios-con-web-design.md`

## Global Constraints

- **Dos contactos, no siete:** día 0 y día 7 desde el primer envío. El segundo es el último de la vida de ese comercio en esta campaña. Es correo en frío: siete toques a alguien que nunca pidió nada es el perfil que se marca como spam.
- **Tope diario propio, arrancando en 10**, independiente del de Meta y por ventana de 24 horas rodantes, rechequeado antes de cada envío.
- **La cohorte es `source='discovery'`.** El padrón sin web es `source IS NULL` y los leads de Meta son `source='meta'`. **Ninguna consulta de esta campaña puede alcanzar a las otras dos.**
- **Deduplicación por dirección dentro de la propia cohorte**, en el `WHERE` y no en memoria: dos sucursales de la misma firma comparten sitio y casilla (medido: 13 mails, 12 direcciones únicas).
- **Una baja vale para las dos campañas.** Quien se dio de baja en Meta no recibe discovery, y al revés.
- El remitente sale de `DISCOVERY_FROM_EMAIL`. **No se hardcodea el subdominio**: al escribir este plan todavía no está verificado en Resend.
- `Reply-To` apunta a `contacto@scalerics.com`.
- Fechas en `"%Y-%m-%d %H:%M:%S"` UTC naive. Nunca `isoformat()`.
- Todo valor que venga del scrapeo (`name`, `city`, `category`) se escapa con `html.escape` antes de ir al HTML.
- Sin emojis. El texto que lee una persona va en español rioplatense con voseo y tildes; **los comentarios de código van en ASCII sin tildes**, que es la convención del repo.
- Los `.py` están en **CRLF**. No convertir finales de línea ni reformatear archivos enteros.
- La suite se corre con `python -m pytest tests/ -q --ignore=tests/test_main.py` y al escribir este plan da **435 passed**. No puede bajar.

## Estructura de archivos

| Archivo | Responsabilidad |
|---|---|
| `database.py` | La tabla `discovery_reminders` dentro de `init_db`. |
| `services/discovery_contactos.py` (nuevo) | La tabla de días `[0, 7]`, sin dependencias, como `secuencia_contactos.py`. |
| `services/discovery_emails.py` (nuevo) | Registro de envío, baja, elegibilidad y la tanda. |
| `services/email_service.py` | El armado del mail de discovery, junto al de Meta. |
| `dashboard.py` | Que `/baja/<token>` atienda las dos campañas. |
| `tests/test_discovery_emails.py` (nuevo) | Elegibilidad, supresión, tanda y baja. |
| `tests/test_discovery_email_texto.py` (nuevo) | Los dos textos y las cabeceras. |

---

### Task 1: La tabla, el registro de envío y la baja compartida

**Files:**
- Modify: `database.py` (bloque de `CREATE TABLE` junto al de `meta_reminders`)
- Create: `services/discovery_contactos.py`
- Create: `services/discovery_emails.py`
- Modify: `dashboard.py:5656-5658` (la ruta `/baja/<token>`)
- Test: `tests/test_discovery_emails.py`

**Interfaces:**
- Produces:
  - `DIAS_DE_CADA_CONTACTO = [0, 7]` y `TOTAL_CONTACTOS = 2` en `services/discovery_contactos.py`
  - `registrar_envio(db_path: str, business_id: int, numero: int) -> str` — devuelve el token, levanta `sqlite3.IntegrityError` si ese `(business_id, numero)` ya existe
  - `dar_de_baja(db_path: str, token: str) -> bool`
  - `esta_dado_de_baja(db_path: str, business_id: int) -> bool`

**Por qué la ruta de baja se comparte y no se duplica.** `/baja/<token>` ya existe y llama a `services.meta_reminders.dar_de_baja` (`dashboard.py:5656-5658`). Los tokens son UUID, o sea únicos entre las dos tablas, así que llamar a las dos funciones con el mismo token es seguro: la que no lo tiene devuelve `False` y no hace nada. Eso evita publicar una URL nueva y evita que un lector del código tenga que acordarse de cuál campaña usa cuál ruta.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_discovery_emails.py`:

```python
"""Registro de envio y baja de la campana de discovery."""

import sqlite3

import pytest

from database import init_db, insert_business
from services.discovery_emails import (dar_de_baja, esta_dado_de_baja,
                                       registrar_envio)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "discovery.db")
    init_db(path)
    return path


def _comercio(db, bid_hint, **extra):
    datos = {
        "name": f"Inmobiliaria {bid_hint}",
        "phone": f"+598 2900 {bid_hint:04d}",
        "maps_url": f"https://maps.google.com/?cid={bid_hint}",
        "website": f"https://inmo{bid_hint}.com.uy",
        "email": f"info@inmo{bid_hint}.com.uy",
        "source": "discovery",
    }
    datos.update(extra)
    return insert_business(db, datos)


def test_registrar_envio_devuelve_un_token(db):
    bid = _comercio(db, 1)
    token = registrar_envio(db, bid, 1)
    assert token and len(token) > 20


def test_no_se_puede_mandar_dos_veces_el_mismo_contacto(db):
    """Es la garantia de fondo: la da el UNIQUE, no el codigo que llama."""
    bid = _comercio(db, 2)
    registrar_envio(db, bid, 1)
    with pytest.raises(sqlite3.IntegrityError):
        registrar_envio(db, bid, 1)


def test_el_mismo_comercio_puede_recibir_los_dos_contactos(db):
    bid = _comercio(db, 3)
    t1 = registrar_envio(db, bid, 1)
    t2 = registrar_envio(db, bid, 2)
    assert t1 != t2


def test_la_baja_vale_para_toda_la_secuencia(db):
    """Se da de baja con el token del contacto 1 y no recibe el 2."""
    bid = _comercio(db, 4)
    token = registrar_envio(db, bid, 1)
    assert dar_de_baja(db, token) is True
    assert esta_dado_de_baja(db, bid) is True


def test_un_token_que_no_existe_no_revienta(db):
    assert dar_de_baja(db, "token-inventado") is False


def test_la_baja_es_idempotente(db):
    bid = _comercio(db, 5)
    token = registrar_envio(db, bid, 1)
    assert dar_de_baja(db, token) is True
    assert dar_de_baja(db, token) is True
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_discovery_emails.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'services.discovery_emails'`.

- [ ] **Step 3: La tabla de días**

Crear `services/discovery_contactos.py`:

```python
"""La tabla de la secuencia de discovery.

Vive en un modulo aparte y sin dependencias, igual que
`services/secuencia_contactos.py` para la campana de Meta, porque la necesitan
los dos lados: el que decide a quien le toca y el que escribe el texto.

Son DOS contactos, no siete como en Meta, y la diferencia es deliberada: los
leads de Meta llenaron un formulario pidiendo que los contacten; estos comercios
no pidieron nada. Siete toques en frio es el perfil que se marca como spam.
"""

# Dias desde el PRIMER envio de cada comercio.
DIAS_DE_CADA_CONTACTO = [0, 7]
TOTAL_CONTACTOS = len(DIAS_DE_CADA_CONTACTO)
```

- [ ] **Step 4: La tabla en `init_db`**

En `database.py`, junto al bloque de `meta_reminders`:

```python
        conn.execute("""
            CREATE TABLE IF NOT EXISTS discovery_reminders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id     INTEGER NOT NULL,
                numero          INTEGER NOT NULL DEFAULT 1,
                token           TEXT NOT NULL UNIQUE,
                sent_at         TEXT NOT NULL,
                unsubscribed_at TEXT,
                UNIQUE (business_id, numero)
            )
        """)
```

No hace falta migración: la tabla nace con su forma final, a diferencia de
`meta_reminders`, que tuvo que migrar de una fila por lead a una por contacto.

- [ ] **Step 5: El módulo**

Crear `services/discovery_emails.py`:

```python
"""Secuencia de mails en frio a los comercios de la cohorte `discovery`.

Es un modulo aparte y no una generalizacion de `meta_reminders`: ese le manda
correo real a terceros todos los dias desde el 19-8-2026, y convertirlo en un
motor generico para meterle una segunda campana con reglas distintas es tocar
lo unico que funciona. Se acepta duplicar el bucle de envio.
"""

import logging
import sqlite3
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime(_FORMATO_FECHA)


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def registrar_envio(db_path: str, business_id: int, numero: int) -> str:
    """Deja la fila ANTES de mandar, y devuelve el token de baja.

    El orden importa: si se mandara primero y se registrara despues, un corte
    en el medio dejaria un mail mandado sin fila, y el comercio lo recibiria de
    nuevo. Al reves, el peor caso es una fila sin mail, que es recuperable.
    """
    token = uuid.uuid4().hex + uuid.uuid4().hex[:8]
    conn = _conn(db_path)
    try:
        conn.execute(
            "INSERT INTO discovery_reminders (business_id, numero, token, sent_at) "
            "VALUES (?, ?, ?, ?)",
            (business_id, int(numero), token, _ahora()),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def dar_de_baja(db_path: str, token: str) -> bool:
    """Marca la baja. Devuelve False si el token no es de esta campana.

    Se llama con cualquier token que llegue a /baja/<token>, incluidos los de
    Meta, asi que un token desconocido es lo normal y no un error.
    """
    conn = _conn(db_path)
    try:
        cur = conn.execute(
            "UPDATE discovery_reminders SET unsubscribed_at = ? "
            "WHERE token = ? AND unsubscribed_at IS NULL",
            (_ahora(), token),
        )
        conn.commit()
        if cur.rowcount:
            return True
        existe = conn.execute(
            "SELECT 1 FROM discovery_reminders WHERE token = ?", (token,)
        ).fetchone()
        return existe is not None
    finally:
        conn.close()


def esta_dado_de_baja(db_path: str, business_id: int) -> bool:
    conn = _conn(db_path)
    try:
        fila = conn.execute(
            "SELECT 1 FROM discovery_reminders "
            "WHERE business_id = ? AND unsubscribed_at IS NOT NULL",
            (business_id,),
        ).fetchone()
        return fila is not None
    finally:
        conn.close()
```

- [ ] **Step 6: La ruta de baja atiende las dos campañas**

En `dashboard.py:5656-5658`, reemplazar el cuerpo:

```python
    @app.route("/baja/<token>", methods=["GET", "POST"])
    def baja_recordatorios(token):
        from services.discovery_emails import dar_de_baja as baja_discovery
        from services.meta_reminders import dar_de_baja as baja_meta
        # Los tokens son UUID, o sea unicos entre las dos tablas: llamar a las
        # dos es seguro y evita publicar una URL nueva por campana.
        baja_meta(app.config["DB_PATH"], token)
        baja_discovery(app.config["DB_PATH"], token)
```

El resto de la función —el HTML de respuesta y el comentario sobre no revelar si el token existía— no se toca.

- [ ] **Step 7: Correr los tests**

Run: `python -m pytest tests/test_discovery_emails.py -q`
Expected: PASS.

- [ ] **Step 8: Suite completa y commit**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
git add database.py services/discovery_contactos.py services/discovery_emails.py dashboard.py tests/test_discovery_emails.py
git commit -m "feat(discovery): tabla de envios, baja compartida y tabla de dias"
```

---

### Task 2: A quién le toca hoy, y a quién no

**Files:**
- Modify: `services/discovery_emails.py`
- Test: `tests/test_discovery_emails.py`

**Interfaces:**
- Consumes: `registrar_envio` y `DIAS_DE_CADA_CONTACTO` de la Task 1.
- Produces:
  - `comercios_a_contactar(db_path: str, limite: int) -> list[dict]` — los que nunca recibieron nada. Dicts con `id`, `name`, `city`, `category`, `email`, `website` y `numero` (siempre 1).
  - `comercios_a_seguir(db_path: str, limite: int) -> list[dict]` — los que ya recibieron el contacto 1 y les toca el 2. Mismos campos, `numero=2`.
  - `enviados_ultimas_24h(db_path: str) -> int`

**Esta es la tarea donde un error le escribe a quien no debe.** Las cuatro reglas de supresión no son adornos: cada una corresponde a un caso concreto que ya pasó o que puede pasar. Leelas antes de escribir el SQL.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_discovery_emails.py`:

```python
from services.discovery_emails import (comercios_a_contactar,
                                       comercios_a_seguir,
                                       enviados_ultimas_24h)


def _envio(db, bid, numero, dias_atras=0):
    """Fila de envio con fecha corrida hacia atras."""
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO discovery_reminders (business_id, numero, token, sent_at) "
        "VALUES (?, ?, ?, datetime('now', ?))",
        (bid, numero, f"tok-{bid}-{numero}", f"-{int(dias_atras)} days"),
    )
    conn.commit()
    conn.close()


def test_un_comercio_nuevo_entra_como_contacto_1(db):
    _comercio(db, 10)
    elegidos = comercios_a_contactar(db, limite=10)
    assert len(elegidos) == 1
    assert elegidos[0]["numero"] == 1


def test_no_toca_al_padron_sin_web(db):
    """source IS NULL es el padron de WhatsApp: no es de esta campana."""
    _comercio(db, 11, source=None, website=None)
    assert comercios_a_contactar(db, limite=10) == []


def test_no_toca_a_los_leads_de_meta(db):
    """Reciben una secuencia propia con correo real. Es el error mas caro."""
    _comercio(db, 12, source="meta")
    assert comercios_a_contactar(db, limite=10) == []


def test_no_le_escribe_a_quien_ya_se_toco(db):
    """Cualquier crm_status distinto de sin_contactar significa que alguien
    ya hablo con ese comercio."""
    _comercio(db, 13, crm_status="interesado")
    assert comercios_a_contactar(db, limite=10) == []


def test_no_le_escribe_a_quien_no_tiene_mail(db):
    _comercio(db, 14, email=None)
    _comercio(db, 15, email="   ")
    assert comercios_a_contactar(db, limite=10) == []


def test_dos_sucursales_con_la_misma_direccion_reciben_una_sola_vez(db):
    """Medido en la primera corrida real: 13 mails, 12 direcciones unicas.
    Dos sucursales de la misma firma comparten sitio y casilla, y el UNIQUE de
    phone y maps_url no las fusiona."""
    _comercio(db, 16, email="info@imas.uy")
    _comercio(db, 17, email="INFO@IMAS.UY")

    elegidos = comercios_a_contactar(db, limite=10)

    assert len(elegidos) == 1, "la misma persona recibiria el mismo mail dos veces"


def test_no_le_escribe_a_quien_comparte_direccion_con_un_lead_de_meta(db):
    _comercio(db, 18, source="meta", email="duenio@inmo.com.uy")
    _comercio(db, 19, email="Duenio@Inmo.com.uy")
    assert comercios_a_contactar(db, limite=10) == []


def test_el_que_ya_recibio_no_vuelve_a_entrar_como_contacto_1(db):
    bid = _comercio(db, 20)
    _envio(db, bid, 1)
    assert comercios_a_contactar(db, limite=10) == []


def test_el_seguimiento_espera_los_dias_que_corresponden(db):
    """El corte esta en el dia exacto: a los 6 no, a los 7 si."""
    seis = _comercio(db, 21)
    _envio(db, seis, 1, dias_atras=6)
    assert comercios_a_seguir(db, limite=10) == []

    siete = _comercio(db, 22)
    _envio(db, siete, 1, dias_atras=7)
    elegidos = comercios_a_seguir(db, limite=10)
    assert [e["id"] for e in elegidos] == [siete]
    assert elegidos[0]["numero"] == 2


def test_el_segundo_es_el_ultimo_de_la_vida(db):
    bid = _comercio(db, 23)
    _envio(db, bid, 1, dias_atras=90)
    _envio(db, bid, 2, dias_atras=80)
    assert comercios_a_seguir(db, limite=10) == []


def test_el_que_se_dio_de_baja_no_recibe_el_seguimiento(db):
    bid = _comercio(db, 24)
    token = registrar_envio(db, bid, 1)
    dar_de_baja(db, token)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE discovery_reminders SET sent_at = datetime('now','-30 days')")
    conn.commit()
    conn.close()
    assert comercios_a_seguir(db, limite=10) == []


def test_una_baja_en_meta_saca_al_comercio_de_discovery(db):
    """La baja vale para las dos campanas: quien dijo basta, dijo basta."""
    from services.meta_reminders import dar_de_baja as baja_meta
    from services.meta_reminders import registrar_envio as envio_meta

    bid_meta = _comercio(db, 25, source="meta", email="basta@inmo.com.uy")
    token = envio_meta(db, bid_meta, 1)
    baja_meta(db, token)

    _comercio(db, 26, email="basta@inmo.com.uy")

    assert comercios_a_contactar(db, limite=10) == []


def test_enviados_ultimas_24h_cuenta_solo_la_ventana(db):
    bid = _comercio(db, 27)
    _envio(db, bid, 1, dias_atras=0)
    otro = _comercio(db, 28)
    _envio(db, otro, 1, dias_atras=3)
    assert enviados_ultimas_24h(db) == 1
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_discovery_emails.py -q`
Expected: FAIL con `ImportError: cannot import name 'comercios_a_contactar'`.

- [ ] **Step 3: Implementar**

Agregar a `services/discovery_emails.py`:

```python
from services.discovery_contactos import DIAS_DE_CADA_CONTACTO, TOTAL_CONTACTOS

# Las cuatro guardas viven en el WHERE a proposito: que un comercio quede fuera
# no puede depender de que el llamador se acuerde de filtrarlo.
_FILTRO_ELEGIBLE = (
    "b.source = 'discovery' "
    "AND b.crm_status = 'sin_contactar' "
    "AND b.email IS NOT NULL AND LENGTH(TRIM(b.email)) > 3"
)

# Direcciones que no pueden recibir NADA de esta campana: las que ya estan en
# la cohorte de Meta (les escribe la otra secuencia, con correo real) y las que
# alguna vez se dieron de baja, en cualquiera de las dos campanas.
_DIRECCIONES_VEDADAS = """
    SELECT LOWER(TRIM(b2.email)) FROM businesses b2
     WHERE b2.email IS NOT NULL AND b2.source <> 'discovery'
    UNION
    SELECT LOWER(TRIM(b3.email)) FROM businesses b3
      JOIN discovery_reminders dr ON dr.business_id = b3.id
     WHERE dr.unsubscribed_at IS NOT NULL AND b3.email IS NOT NULL
    UNION
    SELECT LOWER(TRIM(b4.email)) FROM businesses b4
      JOIN meta_reminders mr ON mr.business_id = b4.id
     WHERE mr.unsubscribed_at IS NOT NULL AND b4.email IS NOT NULL
"""

_CAMPOS = "b.id, b.name, b.city, b.category, TRIM(b.email) AS email, b.website"


def enviados_ultimas_24h(db_path: str) -> int:
    conn = _conn(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM discovery_reminders "
            "WHERE sent_at >= datetime('now', '-1 day')"
        ).fetchone()[0]
    finally:
        conn.close()


def _filas_a_dicts(filas, numero: int) -> list[dict]:
    return [{
        "id": f["id"],
        "name": f["name"] or "",
        "city": f["city"] or "",
        "category": f["category"] or "",
        "email": f["email"],
        "website": f["website"] or "",
        "numero": numero,
    } for f in filas]


def comercios_a_contactar(db_path: str, limite: int) -> list[dict]:
    """Los que nunca recibieron nada de esta campana.

    El GROUP BY por direccion es la deduplicacion dentro de la cohorte: dos
    sucursales de la misma firma tienen telefono y ficha de Maps distintos —asi
    que el UNIQUE no las fusiona— pero comparten sitio y casilla. Sin esto, esa
    persona recibe el mismo mail en frio dos veces.
    """
    conn = _conn(db_path)
    try:
        filas = conn.execute(
            f"""
            SELECT {_CAMPOS}, MIN(b.scraped_at) AS primero
              FROM businesses b
         LEFT JOIN discovery_reminders dr ON dr.business_id = b.id
             WHERE {_FILTRO_ELEGIBLE}
               AND dr.id IS NULL
               AND LOWER(TRIM(b.email)) NOT IN ({_DIRECCIONES_VEDADAS})
               AND NOT EXISTS (
                     SELECT 1 FROM discovery_reminders dr2
                       JOIN businesses b5 ON b5.id = dr2.business_id
                      WHERE LOWER(TRIM(b5.email)) = LOWER(TRIM(b.email))
                   )
          GROUP BY LOWER(TRIM(b.email))
          ORDER BY primero ASC
             LIMIT ?
            """,
            (int(limite),),
        ).fetchall()
    finally:
        conn.close()
    return _filas_a_dicts(filas, 1)


def comercios_a_seguir(db_path: str, limite: int) -> list[dict]:
    """Los que ya recibieron el contacto 1 y les toca el 2."""
    dias = DIAS_DE_CADA_CONTACTO[1]
    conn = _conn(db_path)
    try:
        filas = conn.execute(
            f"""
            SELECT {_CAMPOS}, MIN(dr.sent_at) AS primer_envio
              FROM businesses b
              JOIN discovery_reminders dr ON dr.business_id = b.id
             WHERE {_FILTRO_ELEGIBLE}
               AND NOT EXISTS (
                     SELECT 1 FROM discovery_reminders x
                      WHERE x.business_id = b.id AND x.unsubscribed_at IS NOT NULL
                   )
          GROUP BY b.id
            HAVING MAX(dr.numero) < {TOTAL_CONTACTOS}
               AND primer_envio <= datetime('now', '-{dias} days')
          ORDER BY primer_envio ASC
             LIMIT ?
            """,
            (int(limite),),
        ).fetchall()
    finally:
        conn.close()
    return _filas_a_dicts(filas, 2)
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_discovery_emails.py -q`
Expected: PASS.

- [ ] **Step 5: Suite completa y commit**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
git add services/discovery_emails.py tests/test_discovery_emails.py
git commit -m "feat(discovery): elegibilidad y las cuatro reglas de supresion"
```

---

### Task 3: Los dos textos

**Files:**
- Modify: `services/email_service.py`
- Test: `tests/test_discovery_email_texto.py` (nuevo)

**Interfaces:**
- Produces: `send_discovery_email(to_email: str, negocio: str, rubro: str, unsub_url: str, numero: int = 1) -> str` — tri-estado `"ok"`/`"fallo"`/`"desconocido"`.

**El remitente no se hardcodea.** Sale de `DISCOVERY_FROM_EMAIL`; si esa variable no está, la función **no manda** y devuelve `"fallo"`, para que una configuración incompleta no termine mandando correo en frío desde el dominio principal. Al escribir este plan el subdominio todavía no está verificado en Resend.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_discovery_email_texto.py`:

```python
"""Los dos textos de la campana en frio y sus cabeceras."""

import os
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from services.email_service import send_discovery_email


@contextmanager
def _capturar():
    os.environ["DISCOVERY_FROM_EMAIL"] = "Scalerics <hola@novedades.scalerics.com>"
    with patch("services.email_service._send_estado", return_value="ok") as enviar:
        yield enviar


def test_sin_remitente_configurado_no_manda(monkeypatch):
    """Una configuracion a medias no puede terminar mandando correo en frio
    desde el dominio principal."""
    monkeypatch.delenv("DISCOVERY_FROM_EMAIL", raising=False)
    with patch("services.email_service._send_estado") as enviar:
        assert send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t") == "fallo"
        enviar.assert_not_called()


def test_sale_del_subdominio_configurado():
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t")
    assert "novedades.scalerics.com" in enviar.call_args.kwargs["from_email"]


def test_las_respuestas_van_a_contacto():
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t")
    assert enviar.call_args.kwargs["headers"]["Reply-To"] == "contacto@scalerics.com"


@pytest.mark.parametrize("numero,esperado", [(1, "Vimos"), (2, "una sola vez")])
def test_cada_contacto_dice_algo_distinto(numero, esperado):
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t", numero)
    assert esperado in enviar.call_args.args[2]


def test_el_segundo_avisa_que_es_el_ultimo():
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t", 2)
    html = enviar.call_args.args[2]
    assert "no te escribimos mas" in html.lower().replace("á", "a")


def test_los_dos_llevan_baja_y_cabecera_de_baja():
    for numero in (1, 2):
        with _capturar() as enviar:
            send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/tok", numero)
        html = enviar.call_args.args[2]
        assert "https://c/baja/tok" in html, f"contacto {numero} sin link de baja"
        assert enviar.call_args.kwargs["headers"]["List-Unsubscribe"] == "<https://c/baja/tok>"
        assert enviar.call_args.kwargs["headers"]["List-Unsubscribe-Post"] == \
            "List-Unsubscribe=One-Click"


def test_el_nombre_del_comercio_se_escapa():
    """`name` sale de Google Maps: lo escribe cualquiera."""
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", 'Inmo <script>alert(1)</script>', "Inmobiliaria",
                             "https://c/baja/t")
    assert "<script>" not in enviar.call_args.args[2]


def test_los_dos_cuerpos_son_distintos():
    cuerpos = []
    for numero in (1, 2):
        with _capturar() as enviar:
            send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t", numero)
        cuerpos.append(enviar.call_args.args[2])
    assert cuerpos[0] != cuerpos[1]
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_discovery_email_texto.py -q`
Expected: FAIL con `ImportError: cannot import name 'send_discovery_email'`.

- [ ] **Step 3: Implementar**

En `services/email_service.py`, junto a `send_meta_lead_reminder`:

```python
def _cuerpo_discovery(numero: int, negocio: str, rubro: str) -> tuple[str, list[str]]:
    """Devuelve (asunto, [parrafos]) para el contacto `numero`.

    `negocio` y `rubro` ya vienen escapados por el llamador.

    Son dos y el segundo cierra. En frio, el tercer toque rinde poco y cuesta
    reputacion: cada marca de spam se paga con entrega, y la entrega es la
    misma que usa el correo con clientes.
    """
    de = f" de {negocio}" if negocio else ""
    if numero <= 1:
        return (f"Una idea para{de}" if negocio else "Una idea para tu negocio", [
            "Hola,",
            f"Vimos el sitio{de} y nos quedamos pensando en algo.",
            "Somos Scalerics, una software factory uruguaya. No hacemos paginas: "
            "automatizamos lo que hoy se hace a mano —pedidos, seguimientos, "
            "reportes— y armamos software para lo que ningun sistema de estante "
            "resuelve.",
            "Si te interesa, respondeme este mail y lo charlamos en 20 minutos.",
        ])
    return (f"Ultimo mail{de}" if negocio else "Ultimo mail de Scalerics", [
        "Hola,",
        "Te escribi hace una semana y no quiero insistir mas de la cuenta, asi que "
        "este es el ultimo mail: no te escribimos mas.",
        "Si en algun momento te sirve automatizar algo de la operativa, respondeme "
        "y lo vemos. Preguntamos una sola vez.",
        "Gracias por el tiempo.",
    ])


def send_discovery_email(to_email: str, negocio: str, rubro: str,
                         unsub_url: str, numero: int = 1) -> str:
    """Mail en frio a un comercio de la cohorte de discovery.

    Sale del subdominio de `DISCOVERY_FROM_EMAIL`, NO del dominio principal:
    el correo en frio genera quejas por bien hecho que este, y esas quejas no
    pueden degradar la entrega de los recordatorios de Meta ni la del correo
    con clientes. Si la variable no esta, no manda.
    """
    remitente = os.environ.get("DISCOVERY_FROM_EMAIL", "").strip()
    if not remitente:
        logger.warning("DISCOVERY_FROM_EMAIL sin configurar: no se manda nada")
        return "fallo"

    numero = int(numero or 1)
    negocio_txt = (negocio or "").strip()
    rubro_txt = (rubro or "").strip()

    asunto, parrafos = _cuerpo_discovery(numero, html.escape(negocio_txt),
                                         html.escape(rubro_txt))
    asunto_txt, parrafos_txt = _cuerpo_discovery(numero, negocio_txt, rubro_txt)

    estilo_p = "margin:0 0 14px;font-size:15px;line-height:1.6;color:#1c2b40"
    cuerpo_html = "".join(f'<p style="{estilo_p}">{p}</p>' for p in parrafos)
    html_mail = f"""<!DOCTYPE html>
<html lang="es"><body style="margin:0;padding:24px;background:#f1f5f9">
  <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:10px;padding:32px">
    <img src="{_LOGO_FIRMA}" alt="Scalerics" style="height:24px;margin-bottom:20px">
    {cuerpo_html}
    <p style="{estilo_p};margin-top:24px"><strong>Scalerics</strong><br>{_TELEFONO}</p>
    <p style="font-size:12px;color:#94a3b8;margin:24px 0 0">
      Si no querés recibir más, <a href="{unsub_url}" style="color:#94a3b8">dale de baja acá</a>.
    </p>
  </div>
</body></html>"""

    texto = "\n\n".join(parrafos_txt) + f"\n\nScalerics · {_TELEFONO}\n\nBaja: {unsub_url}"

    return _send_estado(
        to_email, asunto_txt, html_mail,
        from_email=remitente,
        headers={
            "Reply-To": "contacto@scalerics.com",
            "List-Unsubscribe": f"<{unsub_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
        text=texto,
    )
```

- [ ] **Step 4: Correr los tests y commitear**

```bash
python -m pytest tests/test_discovery_email_texto.py -q
python -m pytest tests/ -q --ignore=tests/test_main.py
git add services/email_service.py tests/test_discovery_email_texto.py
git commit -m "feat(discovery): los dos textos del correo en frio"
```

---

### Task 4: La tanda diaria

**Files:**
- Modify: `services/discovery_emails.py`
- Modify: `dashboard.py` (junto a `start_meta_reminders`)
- Test: `tests/test_discovery_emails.py`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces:
  - `enviar_discovery(db_path: str, base_url: str, dry_run: bool = False) -> dict` — devuelve `{"candidatos", "enviados", "fallidos", "inciertos", "seguimientos", "nuevos"}`
  - `start_discovery_emails(app) -> None` — arranca **sólo** con `DISCOVERY_EMAILS=on`

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_discovery_emails.py`:

```python
from unittest.mock import patch

from services.discovery_emails import enviar_discovery


def test_el_tope_diario_arranca_en_10(db):
    for i in range(40, 55):
        _comercio(db, i)
    with patch("services.discovery_emails.send_discovery_email", return_value="ok"):
        res = enviar_discovery(db, "https://crm")
    assert res["enviados"] == 10


def test_los_seguimientos_van_antes_que_los_nuevos(db):
    for i in range(60, 75):
        _comercio(db, i)
    viejo = _comercio(db, 90)
    _envio(db, viejo, 1, dias_atras=10)

    mandados = []

    def fake(to, negocio, rubro, url, numero=1):
        mandados.append((to, numero))
        return "ok"

    with patch("services.discovery_emails.send_discovery_email", side_effect=fake):
        enviar_discovery(db, "https://crm")

    assert (f"info@inmo90.com.uy", 2) in mandados


def test_el_dry_run_no_escribe_ni_manda(db):
    _comercio(db, 100)
    with patch("services.discovery_emails.send_discovery_email") as enviar:
        res = enviar_discovery(db, "https://crm", dry_run=True)
        enviar.assert_not_called()
    assert res["candidatos"] == 1 and res["enviados"] == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM discovery_reminders").fetchone()[0] == 0
    conn.close()


def test_un_fallo_borra_solo_ese_contacto(db):
    """Si no salio, la fila no puede quedar: bloquearia el reintento."""
    bid = _comercio(db, 110)
    _envio(db, bid, 1, dias_atras=10)
    with patch("services.discovery_emails.send_discovery_email", return_value="fallo"):
        enviar_discovery(db, "https://crm")
    conn = sqlite3.connect(db)
    numeros = [r[0] for r in conn.execute(
        "SELECT numero FROM discovery_reminders WHERE business_id = ?", (bid,))]
    conn.close()
    assert numeros == [1], "el contacto 1 no se puede perder: su token ya se publico"


def test_el_hilo_no_arranca_sin_el_interruptor(monkeypatch, caplog):
    from services.discovery_emails import start_discovery_emails
    monkeypatch.delenv("DISCOVERY_EMAILS", raising=False)
    start_discovery_emails(object())
    assert "apagad" in caplog.text.lower()
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_discovery_emails.py -q`
Expected: FAIL con `ImportError: cannot import name 'enviar_discovery'`.

- [ ] **Step 3: Implementar**

Agregar a `services/discovery_emails.py`:

```python
import os
import threading
import time

from services.email_service import send_discovery_email

_TOPE_DIARIO = 10        # arranca bajo: el subdominio nace sin reputacion
_PAUSA_ENTRE_ENVIOS = 0.6
_CADA_24_HORAS = 24 * 60 * 60


def enviar_discovery(db_path: str, base_url: str, dry_run: bool = False) -> dict:
    """Una tanda. Los seguimientos primero, despues los contactos nuevos."""
    res = {"candidatos": 0, "enviados": 0, "fallidos": 0, "inciertos": 0,
           "seguimientos": 0, "nuevos": 0}

    cupo = _TOPE_DIARIO if dry_run else max(0, _TOPE_DIARIO - enviados_ultimas_24h(db_path))
    if cupo <= 0:
        logger.info("Discovery: no se manda nada, ya se llego al tope de las ultimas 24 horas")
        return res

    seguimientos = comercios_a_seguir(db_path, limite=cupo)
    faltan = cupo - len(seguimientos)
    nuevos = comercios_a_contactar(db_path, limite=faltan) if faltan > 0 else []
    candidatos = seguimientos + nuevos
    res["candidatos"] = len(candidatos)
    res["seguimientos"] = len(seguimientos)
    res["nuevos"] = len(nuevos)

    for comercio in candidatos:
        numero = comercio.get("numero", 1)
        if dry_run:
            logger.info(f"[dry-run] contacto {numero} a {comercio['email']} "
                        f"(comercio {comercio['id']})")
            continue

        if enviados_ultimas_24h(db_path) >= _TOPE_DIARIO:
            logger.info("Discovery: se llego al tope en el medio de la tanda, corto aca")
            break

        try:
            token = registrar_envio(db_path, comercio["id"], numero)
        except sqlite3.IntegrityError:
            continue

        estado = send_discovery_email(
            comercio["email"], comercio["name"], comercio["category"],
            f"{base_url.rstrip('/')}/baja/{token}", numero,
        )

        if estado == "ok":
            res["enviados"] += 1
        elif estado == "fallo":
            # Sabemos que no salio: se borra SOLO esa fila, para que se
            # reintente. Borrar por business_id se llevaria el contacto 1, cuyo
            # token ya viaja dentro de un mail que alguien recibio.
            conn = _conn(db_path)
            try:
                conn.execute(
                    "DELETE FROM discovery_reminders WHERE business_id = ? AND numero = ?",
                    (comercio["id"], numero),
                )
                conn.commit()
            finally:
                conn.close()
            res["fallidos"] += 1
        else:
            # "desconocido": pudo haber salido. La fila se queda puesta, porque
            # reintentar significaria mandar dos veces.
            res["inciertos"] += 1

        time.sleep(_PAUSA_ENTRE_ENVIOS)

    logger.info(f"Discovery: {res}")
    return res


def start_discovery_emails(app) -> None:
    """Corre una vez por dia. Arranca SOLO con DISCOVERY_EMAILS=on.

    El default es apagado por la misma razon que en Meta: el hilo corre 180
    segundos despues de CADA boot, y Fly reinicia la maquina para aplicar un
    secret, asi que un default encendido convierte cualquier deploy en una
    tanda de correo en frio que nadie pidio.
    """
    if os.environ.get("DISCOVERY_EMAILS", "").strip().lower() != "on":
        logger.info("Discovery apagado (hace falta DISCOVERY_EMAILS=on)")
        return

    def _loop():
        time.sleep(180)
        while True:
            try:
                with app.app_context():
                    enviar_discovery(app.config["DB_PATH"],
                                     os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev"))
            except Exception as e:
                logger.warning(f"Discovery: {e}")
            time.sleep(_CADA_24_HORAS)

    threading.Thread(target=_loop, daemon=True, name="discovery-emails").start()
    logger.info(f"Discovery ACTIVO por DISCOVERY_EMAILS=on: una corrida por dia, "
                f"hasta {_TOPE_DIARIO} mails, la primera 180s despues de este arranque")
```

- [ ] **Step 4: Engancharlo al arranque**

En `dashboard.py`, dentro del bloque `if os.environ.get("CRM_SIN_PROCESOS_DE_FONDO", "").lower() != "true":`, justo después de `start_meta_reminders(app)`:

```python
        from services.discovery_emails import start_discovery_emails
        start_discovery_emails(app)
```

- [ ] **Step 5: Suite completa y commit**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
git add services/discovery_emails.py dashboard.py tests/test_discovery_emails.py
git commit -m "feat(discovery): la tanda diaria detras de su propio interruptor"
```

---

### Task 5: El runbook de puesta en producción

**Files:**
- Create: `docs/puesta-en-produccion-discovery.md`

Esta tarea no toca código. El runbook de Meta (`docs/puesta-en-produccion-recordatorios-meta.md`) es el modelo: **leelo entero antes de escribir éste**, porque las trampas de infraestructura son las mismas máquina y la misma base.

- [ ] **Step 1: Escribirlo**

Tiene que cubrir, además de lo que ya dice el de Meta:

1. **El subdominio primero.** Crear los registros DNS en Cloudflare, esperar propagación, verificar en Resend, y recién ahí setear `DISCOVERY_FROM_EMAIL`. Es lo único con espera que no depende de nosotros, y sin eso la función no manda (devuelve `"fallo"` a propósito).
2. **Calentar antes de soltar.** El subdominio nace sin reputación: el tope arranca en 10 y sube a mano después de ver entrega limpia, no antes.
3. **El interruptor es `DISCOVERY_EMAILS=on`**, y arranca apagado. Mismo procedimiento que el de Meta: apagarlo antes de deployar, verificar con la máquina quieta, prenderlo al final.
4. **Los dos interruptores son independientes.** Apagar `META_RECORDATORIOS` no apaga discovery y al revés. Al operar uno hay que acordarse del otro, porque un `secrets set` reinicia la máquina y dispara el hilo de **los dos**.
5. **La verificación previa que importa acá y no en Meta:** correr el dry-run y **mirar las direcciones a mano** antes de encender. Son direcciones raspadas de sitios web, no tipeadas por su dueño en un formulario.
6. **Qué mirar los primeros días:** rebotes y quejas. En una lista raspada parte de las direcciones están muertas, y insistirle a direcciones muertas es la vía más rápida a que bloqueen el dominio.
7. **Cómo frenar todo rápido:** `flyctl secrets unset DISCOVERY_EMAILS -a scalerics-crm`.

- [ ] **Step 2: Commit**

```bash
git add docs/puesta-en-produccion-discovery.md
git commit -m "docs: runbook de puesta en produccion del discovery"
```

---

## Self-Review

**Cobertura del spec:** los dos contactos y sus días (Tasks 1 y 2), el tope propio arrancando en 10 (Task 4), los seguimientos primero (Task 4), el subdominio con `Reply-To` (Task 3), las cuatro reglas de supresión más la deduplicación dentro de la cohorte (Task 2), la baja que vale para las dos campañas (Tasks 1 y 2), y el runbook (Task 5). Los rebotes duros quedan **fuera de este plan**: el spec los pide, pero necesitan el webhook de Resend, que es un subsistema aparte y merece su propio plan.

**Sin placeholders:** cada paso trae el código o el comando concreto, salvo el runbook, que es prosa y lleva la lista de lo que tiene que cubrir.

**Consistencia de tipos:** `registrar_envio(db, id, numero) -> str`, `comercios_a_contactar/a_seguir(db, limite) -> list[dict]` con clave `numero`, y `send_discovery_email(to, negocio, rubro, unsub_url, numero) -> str` se usan con esas firmas en las tareas que las consumen.

**Lo que este plan NO resuelve, y hay que saberlo antes de empezar:**

- **Los rebotes duros.** Sin el webhook de Resend, una dirección muerta se reintenta en el contacto 2. Con dos contactos el daño está acotado, pero es la primera deuda a pagar después.
- **La marca visual en el CRM.** Los comercios de discovery aparecen mezclados con el padrón de WhatsApp en el listado por defecto, sin nada que los distinga.
- **`city` viene como "Departamento de Montevideo".** Los textos de este plan no usan la ciudad justamente por eso.
