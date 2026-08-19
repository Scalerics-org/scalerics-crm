# Discovery, parte 1: conseguir las direcciones — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el CRM pueda cargar comercios uruguayos **con** sitio web y sacarles la dirección de mail de su propio sitio, dejando un padrón listo para una campaña de discovery.

**Architecture:** El scraper de Google Maps gana un modo que invierte su criterio actual —hoy descarta los negocios que tienen web, y en el modo nuevo se queda solo con esos— y persiste el dominio en una columna `website` que hoy no existe. Un job aparte recorre esos sitios, extrae la dirección de contacto y la guarda. Las dos piezas están desacopladas a propósito: el scrape contra Maps es lento y frágil, y la extracción de mails tiene que poder volver a correrse sin repetirlo.

**Tech Stack:** Python 3, SQLite, Playwright, pytest.

**Spec:** `docs/superpowers/specs/2026-08-19-discovery-mails-comercios-con-web-design.md`

## Global Constraints

- Los comercios de esta campaña entran con **`source='discovery'`**. El padrón sin web es `source IS NULL` y los leads de Meta son `source='meta'`: son tres cohortes separadas y ninguna automatización puede alcanzar al público de otra.
- **El modo por defecto del scraper no cambia.** Sigue quedándose con los negocios SIN web. El modo nuevo es opt-in.
- El extractor **descarta direcciones basura**: de ejemplo (`usuario@dominio.com`, `@example`, `@tudominio`), `noreply@`/`no-reply@`, las de proveedores de plantillas (Wix, Squarespace, GoDaddy) y los falsos positivos sobre nombres de archivo (`.png`, `.jpg`).
- Un sitio que ya se visitó y no dio mail **no se reintenta**.
- Los `.py` del repo están en **CRLF**. No convertir finales de línea ni reformatear archivos enteros: un cambio de EOL genera un diff falso de miles de líneas.
- Sin emojis. Comentarios y textos en español rioplatense, con voseo y con tildes.
- La suite completa se corre con `python -m pytest tests/ -q --ignore=tests/test_main.py` y hoy da **317 passed**. No puede bajar.

## Estructura de archivos

| Archivo | Responsabilidad |
|---|---|
| `database.py` | Migración de la columna `website` y que `insert_business` la persista. |
| `scraper.py` | Decisión pura de si un negocio se guarda, y el modo `solo_con_web`. |
| `main.py` | Bandera de CLI para el modo nuevo. |
| `services/email_finder.py` (nuevo) | Extracción de mails: núcleo puro (regex + filtro) y el job sobre la base. |
| `tests/test_database.py` | Que la columna sobreviva al insert y al update. |
| `tests/test_scraper_seleccion.py` (nuevo) | La decisión de guardar/descartar en los dos modos. |
| `tests/test_email_finder.py` (nuevo) | Filtro de basura, extracción de HTML, y el job. |

---

### Task 1: La columna `website` y que el insert no la pierda

**Files:**
- Modify: `database.py:106-116` (bloque de `_add_column` de `businesses`)
- Modify: `database.py:520-527` (`ALLOWED_COLUMNS`)
- Modify: `database.py:533-565` (`insert_business`)
- Test: `tests/test_database.py`

**Interfaces:**
- Produces: la columna `website TEXT` en `businesses`, escribible desde `insert_business(db_path, {"website": ...})` y desde `update_business(db_path, id, {"website": ...})`.

**OJO — esta tarea existe por un bug que ya pasó en este repo.** `insert_business` arma el INSERT a mano con una lista de columnas escrita a mano. En su momento **no incluía `email`**, así que los 216 leads de Meta entraron con la dirección enterrada dentro de `form_data` y hubo que escribir un backfill (`scripts/backfill_meta_emails.py`) para rescatarlas. Agregar la columna con `_add_column` **no alcanza**: si no se agrega también a la lista del INSERT, el scraper va a extraer el sitio web y la base lo va a tirar en silencio.

- [ ] **Step 1: Escribir los tests que fallan**

En `tests/test_database.py`, al final:

```python
def test_insert_business_guarda_website(db_path):
    """El sitio web tiene que sobrevivir al INSERT.

    insert_business arma la lista de columnas a mano: una columna que existe
    en la tabla pero no en esa lista se pierde sin dar error. Ya paso con
    `email` y los leads de Meta.
    """
    from database import insert_business, get_all_businesses

    insert_business(db_path, {
        "name": "Inmobiliaria Ejemplo",
        "phone": "+598 2900 1111",
        "maps_url": "https://maps.google.com/?cid=1",
        "website": "https://inmobiliariaejemplo.com.uy",
        "source": "discovery",
    })

    negocio = get_all_businesses(db_path)[0]
    assert negocio["website"] == "https://inmobiliariaejemplo.com.uy"
    assert negocio["source"] == "discovery"


def test_update_business_puede_setear_website(db_path):
    """`website` tiene que estar en ALLOWED_COLUMNS o update_business la rechaza."""
    from database import insert_business, update_business, get_all_businesses

    bid = insert_business(db_path, {
        "name": "Sin Sitio",
        "phone": "+598 2900 2222",
        "maps_url": "https://maps.google.com/?cid=2",
    })

    update_business(db_path, bid, {"website": "https://aparecio.com.uy"})

    assert get_all_businesses(db_path)[0]["website"] == "https://aparecio.com.uy"


def test_website_es_nulo_cuando_no_se_manda(db_path):
    from database import insert_business, get_all_businesses

    insert_business(db_path, {
        "name": "Sin Web",
        "phone": "+598 2900 3333",
        "maps_url": "https://maps.google.com/?cid=3",
    })

    assert get_all_businesses(db_path)[0]["website"] is None
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_database.py -k "website" -v`
Expected: FAIL. Los tres fallan con `sqlite3.OperationalError: no such column: website` o `KeyError: 'website'`.

- [ ] **Step 3: Agregar la migración**

En `database.py`, junto a las otras `_add_column` de `businesses` (después de la línea de `interest`):

```python
    # El sitio web que Google Maps muestra para el negocio. Hasta la campana de
    # discovery el scraper lo extraia solo para descartar al negocio que lo
    # tenia, y no se guardaba en ningun lado.
    _add_column(conn, "businesses", "website", "TEXT")
```

- [ ] **Step 4: Sumarla a `ALLOWED_COLUMNS`**

En `database.py`, dentro del set `ALLOWED_COLUMNS`, agregar `"website"` junto a `"interest"` y `"form_data"`.

- [ ] **Step 5: Sumarla al INSERT de `insert_business`**

Tres lugares en la misma función, y hay que tocar los tres:

1. La lista de columnas del `INSERT INTO businesses (...)`: agregar `website` después de `email`.
2. La lista de `VALUES (...)`: agregar `:website` en la misma posición.
3. El diccionario de parámetros: agregar `"website": data.get("website"),`.

- [ ] **Step 6: Correr los tests**

Run: `python -m pytest tests/test_database.py -v`
Expected: PASS, incluidos los tres nuevos.

- [ ] **Step 7: Suite completa y commit**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
git add database.py tests/test_database.py
git commit -m "feat(discovery): columna website que sobrevive al insert"
```

---

### Task 2: Modo "con web" en el scraper

**Files:**
- Modify: `scraper.py:254` (firma de `scrape_google_maps`), `scraper.py:330-370` (bloque de decisión), `scraper.py:417` (firma de `run`)
- Modify: `main.py:37-40` (subcomando `scrape`) y `main.py:63` (`cmd_scrape`)
- Test: `tests/test_scraper_seleccion.py` (nuevo)

**Interfaces:**
- Consumes: la columna `website` de la Task 1.
- Produces:
  - `_debe_guardar(data: dict, solo_con_web: bool, skip_branded: bool) -> tuple[bool, str]` — `(guardar, motivo)`. `motivo` es `""` cuando `guardar` es `True`.
  - `run(query, max_results, db_path, verify_web=False, default_category="", skip_branded=False, solo_con_web=False) -> int`

**El problema de testear esto:** el bucle de scraping es Playwright contra Google Maps y no se puede probar en la suite. Por eso la decisión de guardar o descartar se saca a una función pura, que es la parte que esta tarea cambia y la única que puede equivocarse en silencio.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_scraper_seleccion.py`:

```python
"""La decision de guardar o descartar un negocio, en los dos modos del scraper."""

import pytest

from scraper import _debe_guardar


def _negocio(**extra):
    base = {"name": "Inmobiliaria Ejemplo", "phone": "+598 2900 1111"}
    base.update(extra)
    return base


def test_sin_telefono_nunca_se_guarda():
    """Sin telefono no hay forma de contactarlo, en ningun modo."""
    for solo_con_web in (False, True):
        guardar, motivo = _debe_guardar(
            _negocio(phone="", maps_website_url="https://x.com.uy"),
            solo_con_web=solo_con_web, skip_branded=False)
        assert guardar is False
        assert "teléfono" in motivo


def test_modo_por_defecto_descarta_al_que_tiene_web():
    guardar, motivo = _debe_guardar(
        _negocio(maps_website_url="https://inmobiliaria.com.uy"),
        solo_con_web=False, skip_branded=False)
    assert guardar is False
    assert "web" in motivo


def test_modo_por_defecto_guarda_al_que_no_tiene_web():
    guardar, motivo = _debe_guardar(
        _negocio(maps_website_url=None), solo_con_web=False, skip_branded=False)
    assert guardar is True
    assert motivo == ""


def test_modo_discovery_guarda_al_que_tiene_web():
    guardar, motivo = _debe_guardar(
        _negocio(maps_website_url="https://inmobiliaria.com.uy"),
        solo_con_web=True, skip_branded=False)
    assert guardar is True
    assert motivo == ""


def test_modo_discovery_descarta_al_que_no_tiene_web():
    """Es el criterio invertido: sin web no hay de donde sacar el mail."""
    guardar, motivo = _debe_guardar(
        _negocio(maps_website_url=None), solo_con_web=True, skip_branded=False)
    assert guardar is False
    assert "web" in motivo


def test_franquicia_se_descarta_solo_si_se_pidio():
    """skip_branded sigue valiendo en los dos modos, y sigue siendo opt-in."""
    chevrolet = _negocio(name="Chevrolet Montevideo",
                         maps_website_url="https://chevrolet.com.uy")

    guardar, motivo = _debe_guardar(chevrolet, solo_con_web=True, skip_branded=True)
    assert guardar is False
    assert "franquicia" in motivo

    guardar, _ = _debe_guardar(chevrolet, solo_con_web=True, skip_branded=False)
    assert guardar is True
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_scraper_seleccion.py -v`
Expected: FAIL con `ImportError: cannot import name '_debe_guardar' from 'scraper'`.

- [ ] **Step 3: Escribir la función pura**

En `scraper.py`, arriba de `scrape_google_maps`:

```python
def _debe_guardar(data: dict, solo_con_web: bool, skip_branded: bool) -> tuple[bool, str]:
    """Decide si un negocio se guarda. Devuelve (guardar, motivo_del_descarte).

    Es una funcion aparte y no un if adentro del bucle porque el bucle corre
    contra Google Maps con Playwright y no se puede probar; esto si.

    `solo_con_web` invierte el criterio: el modo por defecto junta negocios SIN
    sitio web (a esos se les vende una pagina), y el modo discovery junta los
    que SI lo tienen (a esos se les vende automatizacion, y su sitio es de
    donde se saca la direccion de mail).
    """
    if not data.get("phone"):
        return False, "sin teléfono"

    tiene_web = bool(data.get("maps_website_url"))
    if solo_con_web and not tiene_web:
        return False, "sin web en Maps"
    if not solo_con_web and tiene_web:
        return False, "con web en Maps"

    if skip_branded and _is_brand_franchise(data.get("name", "")):
        return False, "franquicia de marca"

    return True, ""
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_scraper_seleccion.py -v`
Expected: PASS, los seis.

- [ ] **Step 5: Cablear la función en el bucle**

En `scraper.py`, reemplazar los tres bloques de descarte del bucle (el de "sin teléfono", el de "web en Maps" y el de "franquicia de marca", líneas ~338-357) por uno solo:

```python
                        guardar, motivo = _debe_guardar(data, solo_con_web, skip_branded)
                        if not guardar:
                            logger.info(f"Saltando ({motivo}): {data['name']}")
                            page.goto(maps_list_url, wait_until="domcontentloaded", timeout=30000)
                            random_delay()
                            break
```

Justo después, antes del insert, poblar los campos de la cohorte:

```python
                        if solo_con_web:
                            data["website"] = data.get("maps_website_url")
                            data["source"] = "discovery"
```

- [ ] **Step 6: Saltear la verificación de Bing en modo discovery**

`verify_no_website` sirve para confirmar que un negocio **no** tiene web. En modo discovery no tiene sentido y sólo agrega minutos. Cambiar la condición del bloque de `verify_web` (línea ~359) a:

```python
                        if verify_web and not solo_con_web:
```

- [ ] **Step 7: Pasar el parámetro por las dos firmas**

En `scrape_google_maps`, agregar `solo_con_web: bool = False` al final de la firma. En `run`, agregarlo también al final y pasarlo en la llamada:

```python
def run(query: str, max_results: int, db_path: str, verify_web: bool = False,
        default_category: str = "", skip_branded: bool = False,
        solo_con_web: bool = False) -> int:
    init_db(db_path)
    return scrape_google_maps(query, max_results, db_path, verify_web=verify_web,
                              default_category=default_category,
                              skip_branded=skip_branded, solo_con_web=solo_con_web)
```

- [ ] **Step 8: Bandera de CLI**

En `main.py`, en el subcomando `scrape`:

```python
    scrape_p.add_argument("--con-web", action="store_true",
                          help="Modo discovery: junta los negocios que SI tienen sitio web")
```

Y en `cmd_scrape` (`main.py:66`), agregar `solo_con_web=args.con_web` a la llamada a `run(...)`, que hoy es:

```python
    return run(args.query, args.max, DB_PATH,
               verify_web=not getattr(args, 'no_verify_web', False),
               default_category=default_cat, solo_con_web=args.con_web)
```

- [ ] **Step 9: Suite completa y commit**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
git add scraper.py main.py tests/test_scraper_seleccion.py
git commit -m "feat(discovery): modo del scraper que junta los comercios con sitio web"
```

---

### Task 3: Extracción de mails, el núcleo puro

**Files:**
- Create: `services/email_finder.py`
- Test: `tests/test_email_finder.py` (nuevo)

**Interfaces:**
- Produces:
  - `es_mail_basura(mail: str) -> bool`
  - `extraer_mails(html: str) -> list[str]` — devuelve las direcciones buenas, sin repetir (comparando en minúsculas), con las de `mailto:` primero.

Esta tarea no toca la base ni el browser. Es sólo texto entra, direcciones salen, y es donde vive la regla que evita mandarle correo a una dirección de ejemplo.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_email_finder.py`:

```python
"""Extraccion de direcciones de mail del HTML de un sitio."""

import pytest

from services.email_finder import es_mail_basura, extraer_mails


@pytest.mark.parametrize("mail", [
    "usuario@dominio.com",
    "tuemail@tudominio.com",
    "ejemplo@example.com",
    "noreply@inmobiliaria.com.uy",
    "no-reply@inmobiliaria.com.uy",
    "info@sentry.wixpress.com",
    "logo@2x.png",
])
def test_direcciones_basura(mail):
    """Mandarle a una direccion de ejemplo es dano puro a la reputacion.

    `usuario@dominio.com` no es hipotetico: aparecio en el sondeo, dejado en
    la plantilla de una inmobiliaria real.
    """
    assert es_mail_basura(mail) is True


@pytest.mark.parametrize("mail", [
    "info@imas.uy",
    "hola@acsa.uy",
    "contacto@diegoalfonso.com.uy",
    "ferraripropiedades@adinet.com.uy",
    "inmobiliaria.uru@gmail.com",
])
def test_direcciones_buenas(mail):
    """Salidas reales del sondeo: ninguna se puede descartar."""
    assert es_mail_basura(mail) is False


def test_extrae_del_mailto():
    html = '<a href="mailto:info@inmobiliaria.com.uy">Escribinos</a>'
    assert extraer_mails(html) == ["info@inmobiliaria.com.uy"]


def test_el_mailto_va_antes_que_el_texto_suelto():
    """Un mailto es una direccion que el dueno puso para que le escriban.
    Una suelta en el HTML puede ser cualquier cosa."""
    html = """
      <p>Escribile al contador: contador@estudio.com.uy</p>
      <a href="mailto:info@inmobiliaria.com.uy">Contacto</a>
    """
    assert extraer_mails(html)[0] == "info@inmobiliaria.com.uy"


def test_filtra_la_basura_del_html():
    html = """
      <a href="mailto:usuario@dominio.com">Mail</a>
      <p>info@inmobiliaria.com.uy</p>
    """
    assert extraer_mails(html) == ["info@inmobiliaria.com.uy"]


def test_no_repite_la_misma_direccion_con_otra_capitalizacion():
    html = """
      <a href="mailto:Info@Inmobiliaria.com.uy">Mail</a>
      <p>info@inmobiliaria.com.uy</p>
    """
    assert len(extraer_mails(html)) == 1


def test_sitio_sin_direcciones():
    assert extraer_mails("<html><body><p>Llamanos al 2900 1111</p></body></html>") == []


def test_ignora_el_query_del_mailto():
    html = '<a href="mailto:info@x.com.uy?subject=Consulta">Mail</a>'
    assert extraer_mails(html) == ["info@x.com.uy"]
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_email_finder.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'services.email_finder'`.

- [ ] **Step 3: Escribir el módulo**

Crear `services/email_finder.py`:

```python
"""Busca la direccion de contacto de un comercio en su propio sitio web.

El nucleo es puro a proposito —HTML entra, direcciones salen— porque la parte
que decide si una direccion sirve es la que puede hacer dano: mandarle a una
direccion de ejemplo que quedo en la plantilla de la web es reputacion
quemada sin ninguna chance de venta.
"""

import re

# Local part que delata una direccion que nadie lee.
_LOCALES_BASURA = {
    "noreply", "no-reply", "donotreply", "usuario", "tuemail", "tucorreo",
    "ejemplo", "example", "email", "correo",
}

# Dominios de plantilla y de proveedores, no del comercio.
_DOMINIOS_BASURA = {
    "dominio.com", "tudominio.com", "midominio.com", "sitio.com", "tusitio.com",
    "example.com", "example.org", "example.net", "dominio.com.uy",
    "sentry.io", "sentry.wixpress.com", "wix.com", "squarespace.com",
    "godaddy.com", "wordpress.com",
}

_RE_MAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RE_MAILTO = re.compile(r'href\s*=\s*["\']mailto:([^"\'?]+)', re.I)
_RE_IMAGEN = re.compile(r"\.(png|jpe?g|gif|webp|svg)$", re.I)


def es_mail_basura(mail: str) -> bool:
    """True si la direccion no sirve para escribirle a un comercio."""
    mail = (mail or "").strip().lower()
    if not mail or "@" not in mail:
        return True
    if _RE_IMAGEN.search(mail):
        return True

    local, _, dominio = mail.partition("@")
    if local in _LOCALES_BASURA:
        return True
    if dominio in _DOMINIOS_BASURA:
        return True
    if any(dominio.endswith("." + d) for d in _DOMINIOS_BASURA):
        return True
    return False


def extraer_mails(html: str) -> list[str]:
    """Direcciones buenas del HTML, las de `mailto:` primero y sin repetir.

    El orden importa: una direccion en un `mailto:` la puso el dueno del sitio
    para que le escriban. Una suelta en el texto puede ser la del contador, la
    del que hizo la web, o cualquier cosa.
    """
    html = html or ""
    candidatas = [m.strip() for m in _RE_MAILTO.findall(html)]
    candidatas += _RE_MAIL.findall(html)

    salida: list[str] = []
    vistas: set[str] = set()
    for mail in candidatas:
        clave = mail.lower()
        if clave in vistas or es_mail_basura(mail):
            continue
        vistas.add(clave)
        salida.append(mail)
    return salida
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_email_finder.py -v`
Expected: PASS.

- [ ] **Step 5: Suite completa y commit**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
git add services/email_finder.py tests/test_email_finder.py
git commit -m "feat(discovery): extraccion de mails del HTML con filtro de basura"
```

---

### Task 4: El job que recorre los sitios

**Files:**
- Modify: `services/email_finder.py`
- Modify: `main.py` (subcomando nuevo)
- Test: `tests/test_email_finder.py`

**Interfaces:**
- Consumes: `extraer_mails` de la Task 3; la columna `website` de la Task 1; `source='discovery'` de la Task 2.
- Produces:
  - `RUTAS_CONTACTO: list[str]`
  - `buscar_mail_del_sitio(abrir, website: str) -> str | None`
  - `procesar_pendientes(db_path: str, abrir, limite: int = 50) -> dict` — devuelve `{"revisados": int, "con_mail": int, "sin_mail": int}`
  - `abrir_con_playwright(page)` — devuelve un `abrir` listo para usar contra un browser real.

**`abrir` es una función `(url) -> str | None`** que devuelve el HTML de esa URL, o `None` si no se pudo abrir. Se pasa como parámetro en vez de usar Playwright adentro para que el job se pueda probar entero sin browser: los tests le pasan un diccionario falso.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_email_finder.py`:

```python
import sqlite3

from database import init_db, insert_business
from services.email_finder import (RUTAS_CONTACTO, buscar_mail_del_sitio,
                                   procesar_pendientes)


def _abrir_falso(paginas: dict):
    """Devuelve un `abrir` que sirve HTML de un diccionario url -> html."""
    def abrir(url):
        return paginas.get(url)
    return abrir


def test_encuentra_el_mail_en_la_home():
    abrir = _abrir_falso({
        "https://inmo.com.uy": '<a href="mailto:info@inmo.com.uy">Mail</a>',
    })
    assert buscar_mail_del_sitio(abrir, "https://inmo.com.uy") == "info@inmo.com.uy"


def test_sigue_a_la_pagina_de_contacto_si_la_home_no_tiene():
    abrir = _abrir_falso({
        "https://inmo.com.uy": "<p>Bienvenidos</p>",
        "https://inmo.com.uy/contacto": '<a href="mailto:hola@inmo.com.uy">Mail</a>',
    })
    assert buscar_mail_del_sitio(abrir, "https://inmo.com.uy") == "hola@inmo.com.uy"


def test_le_agrega_el_esquema_al_dominio_pelado():
    abrir = _abrir_falso({
        "https://inmo.com.uy": '<a href="mailto:info@inmo.com.uy">Mail</a>',
    })
    assert buscar_mail_del_sitio(abrir, "inmo.com.uy") == "info@inmo.com.uy"


def test_sitio_que_no_abre_no_revienta():
    """Un dominio caido es lo normal en un padron raspado, no una excepcion."""
    abrir = _abrir_falso({})
    assert buscar_mail_del_sitio(abrir, "https://caido.com.uy") is None


def test_corta_apenas_encuentra_una_direccion():
    visitadas = []

    def abrir(url):
        visitadas.append(url)
        return '<a href="mailto:info@inmo.com.uy">Mail</a>'

    buscar_mail_del_sitio(abrir, "https://inmo.com.uy")
    assert len(visitadas) == 1, "con la home alcanzo, no hay que seguir visitando"


def _db_con(tmp_path, negocios):
    path = str(tmp_path / "discovery.db")
    init_db(path)
    for n in negocios:
        insert_business(path, n)
    return path


def test_guarda_el_mail_y_marca_el_negocio(tmp_path):
    db = _db_con(tmp_path, [{
        "name": "Inmo Uno", "phone": "+598 2900 0001",
        "maps_url": "https://maps.google.com/?cid=1",
        "website": "https://uno.com.uy", "source": "discovery",
    }])
    abrir = _abrir_falso({
        "https://uno.com.uy": '<a href="mailto:info@uno.com.uy">Mail</a>',
    })

    res = procesar_pendientes(db, abrir)

    assert res == {"revisados": 1, "con_mail": 1, "sin_mail": 0}
    conn = sqlite3.connect(db)
    fila = conn.execute("SELECT email, status FROM businesses").fetchone()
    conn.close()
    assert fila == ("info@uno.com.uy", "email_found")


def test_el_que_no_da_mail_queda_marcado_y_no_se_reintenta(tmp_path):
    db = _db_con(tmp_path, [{
        "name": "Inmo Dos", "phone": "+598 2900 0002",
        "maps_url": "https://maps.google.com/?cid=2",
        "website": "https://dos.com.uy", "source": "discovery",
    }])
    abrir = _abrir_falso({"https://dos.com.uy": "<p>Solo telefono</p>"})

    primera = procesar_pendientes(db, abrir)
    assert primera == {"revisados": 1, "con_mail": 0, "sin_mail": 1}

    segunda = procesar_pendientes(db, abrir)
    assert segunda["revisados"] == 0, "un sitio que no dio mail no se reintenta"


def test_no_toca_a_los_que_no_son_de_discovery(tmp_path):
    """El padron sin web y los leads de Meta no son asunto de este job."""
    db = _db_con(tmp_path, [
        {"name": "Sin Web", "phone": "+598 2900 0003",
         "maps_url": "https://maps.google.com/?cid=3"},
        {"name": "Lead Meta", "phone": "+598 2900 0004",
         "maps_url": "https://maps.google.com/?cid=4",
         "website": "https://meta.com.uy", "source": "meta"},
    ])

    assert procesar_pendientes(db, _abrir_falso({}))["revisados"] == 0


def test_no_pisa_un_mail_que_ya_estaba(tmp_path):
    db = _db_con(tmp_path, [{
        "name": "Inmo Tres", "phone": "+598 2900 0005",
        "maps_url": "https://maps.google.com/?cid=5",
        "website": "https://tres.com.uy", "source": "discovery",
        "email": "yalotenia@tres.com.uy",
    }])

    assert procesar_pendientes(db, _abrir_falso({}))["revisados"] == 0


def test_respeta_el_limite(tmp_path):
    db = _db_con(tmp_path, [
        {"name": f"Inmo {i}", "phone": f"+598 2900 10{i:02d}",
         "maps_url": f"https://maps.google.com/?cid=1{i}",
         "website": f"https://inmo{i}.com.uy", "source": "discovery"}
        for i in range(5)
    ])

    assert procesar_pendientes(db, _abrir_falso({}), limite=2)["revisados"] == 2
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_email_finder.py -v`
Expected: FAIL con `ImportError: cannot import name 'RUTAS_CONTACTO'`.

- [ ] **Step 3: Implementar**

Agregar a `services/email_finder.py`:

```python
import logging

from database import _connect, update_business

logger = logging.getLogger(__name__)

# En que paginas buscar, en orden. La home primero porque muchos comercios
# chicos ponen el mail en el pie de todas las paginas.
RUTAS_CONTACTO = ["", "/contacto", "/contact", "/contactanos", "/nosotros"]


def buscar_mail_del_sitio(abrir, website: str) -> str | None:
    """Primera direccion buena del sitio, o None.

    `abrir` es una funcion (url) -> html | None. Se pasa por parametro para que
    esto se pueda probar sin browser: en produccion la arma
    `abrir_con_playwright`, en los tests es un diccionario.
    """
    if not website:
        return None
    base = website.strip()
    if not base.startswith("http"):
        base = "https://" + base
    base = base.rstrip("/")

    for ruta in RUTAS_CONTACTO:
        html = abrir(base + ruta)
        if not html:
            continue
        mails = extraer_mails(html)
        if mails:
            return mails[0]
    return None


def procesar_pendientes(db_path: str, abrir, limite: int = 50) -> dict:
    """Busca el mail de los negocios de discovery que todavia no lo tienen.

    Un sitio que ya se visito queda marcado (`email_found` o `no_email`) y no
    se vuelve a visitar: si no publica direccion, insistir cuesta minutos y no
    cambia el resultado.
    """
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            """
            SELECT id, website FROM businesses
             WHERE source = 'discovery'
               AND website IS NOT NULL AND TRIM(website) <> ''
               AND (email IS NULL OR TRIM(email) = '')
               AND COALESCE(status, '') NOT IN ('email_found', 'no_email')
             ORDER BY id
             LIMIT ?
            """,
            (int(limite),),
        ).fetchall()
    finally:
        conn.close()

    res = {"revisados": 0, "con_mail": 0, "sin_mail": 0}
    for fila in filas:
        res["revisados"] += 1
        mail = buscar_mail_del_sitio(abrir, fila["website"])
        if mail:
            update_business(db_path, fila["id"], {"email": mail, "status": "email_found"})
            res["con_mail"] += 1
            logger.info(f"[{fila['id']}] {fila['website']} -> {mail}")
        else:
            update_business(db_path, fila["id"], {"status": "no_email"})
            res["sin_mail"] += 1
            logger.info(f"[{fila['id']}] {fila['website']} -> sin mail")

    logger.info(f"Busqueda de mails: {res}")
    return res


def abrir_con_playwright(page):
    """Arma el `abrir` que usa produccion, contra una page de Playwright."""
    def abrir(url):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            return page.content()
        except Exception as e:
            logger.debug(f"No se pudo abrir {url}: {type(e).__name__}")
            return None
    return abrir
```

**Sobre `_connect`:** es la función interna de `database.py` (línea 69) y ya setea `conn.row_factory = sqlite3.Row`, así que `fila["website"]` funciona sin hacer nada más. Verificado al escribir el plan.

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_email_finder.py -v`
Expected: PASS.

- [ ] **Step 5: Subcomando de CLI**

En `main.py`, agregar el subcomando:

```python
    buscar_p = subparsers.add_parser("buscar-mails",
                                     help="Buscar el mail de los comercios de discovery en su sitio")
    buscar_p.add_argument("--limite", type=int, default=50)
```

Y la función que lo ejecuta, junto a las otras `cmd_*`:

```python
def cmd_buscar_mails(args):
    from playwright.sync_api import sync_playwright
    from services.email_finder import abrir_con_playwright, procesar_pendientes

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(20000)
        try:
            res = procesar_pendientes(DB_PATH, abrir_con_playwright(page), limite=args.limite)
        finally:
            browser.close()
    print(res)
```

Registrarlo en el despacho de subcomandos siguiendo el patrón que ya usan `cmd_scrape` y las demás. `DB_PATH` es la constante del módulo (`main.py:24`, `os.environ.get("DB_PATH", "leads.db")`), la misma que usan todos los otros `cmd_*`.

- [ ] **Step 6: Suite completa y commit**

```bash
python -m pytest tests/ -q --ignore=tests/test_main.py
git add services/email_finder.py main.py tests/test_email_finder.py
git commit -m "feat(discovery): job que busca el mail de cada comercio en su sitio"
```

---

## Cómo se usa cuando está terminado

> **Precondición con `CRM_URL` seteado: esta rama tiene que estar mergeada y
> deployada antes de correr `--con-web`, o los sitios web se pierden en
> silencio.**
>
> En producción el scraper no inserta local: postea a `/api/leads` del CRM
> desplegado (`scraper.py:389-393` → `routes/leads.py:91-102`). Ese CRM corre
> desde `main`, y hasta que esta rama esté deployada su `insert_business` no
> tiene `website` en la lista de columnas: el scraper extrae el sitio, el CRM
> lo tira sin avisar, y `buscar-mails` local no ve ninguna fila porque están
> todas del otro lado. Es exactamente el bug que la Task 1 existe para evitar.

```bash
# 1. Juntar inmobiliarias CON sitio web (rubro confirmado al 90% en el sondeo)
python main.py scrape --query "inmobiliaria Montevideo" --max 200 --con-web

# 2. Buscarles el mail en su propio sitio
python main.py buscar-mails --limite 200
```

Rendimiento esperado, medido en el sondeo del 19-8-2026: de cada 20 negocios vistos en Maps, 18 tienen web y 14 dan dirección. El scraping va a unos 4,4 negocios por minuto.

## Self-Review

**Cobertura de la spec:** la columna `website` y el modo del scraper (Task 1 y 2), `source='discovery'` separando las tres cohortes (Task 2), el extractor con filtro de basura obligatorio (Task 3), el "no se reintenta" y el job sobre la base (Task 4). Los criterios de verificación 1 a 4 de la spec tienen test. Los criterios 5 a 10 son de la parte 2 (envío) y no corresponden a este plan.

**Sin placeholders:** cada paso trae el código o el comando concreto. La única indicación condicional es la del nombre de la constante de la base en `main.py`, que se resuelve mirando las otras funciones `cmd_*` del mismo archivo.

**Consistencia de tipos:** `_debe_guardar(data, solo_con_web, skip_branded) -> (bool, str)`, `extraer_mails(html) -> list[str]`, `buscar_mail_del_sitio(abrir, website) -> str | None` y `procesar_pendientes(db_path, abrir, limite) -> dict` se usan con esas mismas firmas en las tareas que las consumen. `abrir` es `(url) -> str | None` en los tres lugares donde aparece.
