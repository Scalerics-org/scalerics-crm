# Meta Ads: datos y dossier — Plan de implementación (Fase 1 de 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el CRM sepa cuánto costó cada campaña de Meta, qué pasó con cada
lead que trajo, y exponga todo eso como un dossier de métricas con tamaño de
muestra e intervalo de confianza — sin una sola línea de IA y sin gastar un token.

**Architecture:** Un servicio sincroniza los Insights de la Marketing API a una
tabla propia. Otro cruza esos datos con el historial del CRM y produce un JSON
determinista donde cada métrica trae su id, su numerador, su denominador, su `n`
y su intervalo de Wilson. Un blueprint nuevo lo sirve. El embudo no se redefine:
se extrae a un módulo compartido que Finanzas y este módulo importan.

**Tech Stack:** Python 3.11, Flask (blueprints), SQLite, `requests`, pytest.
Sin dependencias nuevas — Wilson se calcula con `math`.

**Spec:** `docs/superpowers/specs/2026-09-10-inteligencia-comercial-meta-design.md`

**Fase 2** (panel y gráficos SVG) y **Fase 3** (motor de IA) tienen sus propios
planes. Esta fase termina con una API probada y con datos reales; no tiene UI.

## Global Constraints

- **Nada llama a la API de Anthropic.** Ni el código, ni los tests, ni un paso
  manual. El saldo de la cuenta quedó en cero el 28/8 y la restricción está
  vigente. Esta fase no importa `anthropic` en ningún lado.
- **Leer `COORDINACION.md` antes de empezar.** Somos varias sesiones en el repo.
  La sesión G está anotada; `services/finanzas.py` es territorio de otra sesión.
- **Nunca deployar con cambios sin commitear.** El `Dockerfile` hace `COPY . .`,
  así que `flyctl deploy` sube el árbol entero, incluido lo que otra sesión dejó
  a medio hacer. Esta fase no deploya.
- **Todo cambio de esquema es aditivo.** Ninguna migración altera ni borra una
  tabla, columna o consulta existente.
- **Los tests no mandan correo ni tocan la red.** `tests/conftest.py` ya borra las
  credenciales; no reintroducirlas.
- **Todo automatismo nuevo nace con su propio tope rodante** (regla 3 de
  `COORDINACION.md`): cada deploy reinicia la máquina, y sin tope un reinicio se
  convierte en una tanda de llamadas a la API de Meta.
- **Imports pesados van diferidos dentro de la función.** La máquina de Fly tiene
  256 MB.
- **Vocabulario de estados:** el nuevo (`demo_agendada`, `demo_1`,
  `follow_up_1`, `cerrado`). `lead_events` mezcla las dos épocas, así que **todo
  evento se normaliza antes de compararlo**. Nunca escribir un nombre de estado
  a mano: usar `FUNNEL` y `alcanzo()` de `services/embudo.py`.
- **La moneda no se convierte.** Meta devuelve el gasto en la moneda de la
  cuenta; se guarda `currency` y se muestra tal cual.
- **Una división sin denominador devuelve `None`, nunca `0`.** Un mes sin ventas
  no tiene un costo por venta de cero: no tiene costo por venta.

---

### Task 1: Extraer el embudo a un módulo compartido

Finanzas define hoy qué es una demo y qué es una venta. Este módulo necesita las
mismas definiciones, y si las reescribe habrá dos números distintos para la
misma pregunta en dos paneles del mismo CRM. Se mueven a un módulo común **sin
cambiar comportamiento**: los tests de Finanzas tienen que pasar idénticos.

**Files:**
- Create: `services/embudo.py`
- Modify: `services/finanzas.py` (borrar las definiciones movidas, importarlas)
- Test: `tests/test_embudo.py`

**Interfaces:**
- Consumes: `database._MAPA_ESTADOS_VIEJOS`
- Produces:
  - `FUNNEL: list[str]` — 14 etapas ordenadas
  - `EXCLUIDOS_DEL_FUNNEL: dict[str, str]`
  - `normalizar_estado(estado: str) -> str`
  - `alcanzo(eventos: set[str], etapa: str) -> bool`
  - `dividir(numerador: float, denominador: float) -> float | None`
  - `costo(inversion: float, cantidad: float) -> float | None`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_embudo.py`:

```python
"""Las definiciones del embudo viven en un solo lugar.

Finanzas y el modulo de marketing tienen que contar una demo igual. Si cada uno
define lo suyo, el CRM muestra dos numeros distintos para la misma pregunta en
dos paneles y nadie sabe a cual creerle.
"""

from services.embudo import (FUNNEL, EXCLUIDOS_DEL_FUNNEL, alcanzo, costo,
                             dividir, normalizar_estado)


def test_el_orden_del_embudo_no_cambia():
    assert FUNNEL[0] == "sin_contactar"
    assert FUNNEL[-1] == "finalizado"
    assert FUNNEL.index("demo_agendada") < FUNNEL.index("demo_1")
    assert FUNNEL.index("demo_1") < FUNNEL.index("presupuesto_enviado")
    assert FUNNEL.index("presupuesto_enviado") < FUNNEL.index("cerrado")


def test_en_espera_y_rechazo_no_son_avances():
    assert set(EXCLUIDOS_DEL_FUNNEL) == {"en_espera", "rechazo"}
    assert "en_espera" not in FUNNEL
    assert "rechazo" not in FUNNEL


def test_normalizar_traduce_el_vocabulario_viejo():
    assert normalizar_estado("reunion_agendada") == "demo_agendada"
    assert normalizar_estado("reunion_hecha") == "demo_1"
    assert normalizar_estado("cliente_cerrado") == "cerrado"
    assert normalizar_estado("agendo") == "demo_agendada"
    assert normalizar_estado("firmo") == "cerrado"


def test_normalizar_deja_pasar_lo_que_ya_es_nuevo():
    assert normalizar_estado("demo_1") == "demo_1"
    assert normalizar_estado("cualquier_cosa") == "cualquier_cosa"


def test_alcanzo_cuenta_una_etapa_posterior():
    """Llegar a cerrado implica haber alcanzado demo_1."""
    assert alcanzo({"cerrado"}, "demo_1") is True


def test_alcanzo_no_cuenta_una_etapa_anterior():
    assert alcanzo({"interesado"}, "demo_1") is False


def test_alcanzo_mezcla_las_dos_epocas():
    """lead_events trae nombres viejos y nuevos mezclados en la misma base."""
    assert alcanzo({"reunion_hecha"}, "demo_1") is True


def test_alcanzo_cuenta_al_lead_que_despues_se_cayo():
    """El que llego a demo y hoy figura en no_interesa hizo la demo igual."""
    assert alcanzo({"demo_1", "no_interesa"}, "demo_1") is True


def test_alcanzo_sin_eventos():
    assert alcanzo(set(), "demo_1") is False


def test_dividir_sin_denominador_es_none():
    assert dividir(10, 0) is None
    assert dividir(10, 4) == 2.5


def test_costo_sin_cantidad_es_none():
    """Un mes sin ventas no tiene costo por venta de cero: no tiene."""
    assert costo(300.0, 0) is None


def test_costo_sin_inversion_es_none():
    """Sin campaña que costear tampoco es un costo de cero."""
    assert costo(0.0, 5) is None


def test_costo_normal():
    assert costo(300.0, 4) == 75.0
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_embudo.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'services.embudo'`

- [ ] **Step 3: Crear `services/embudo.py`**

Copiar el contenido **tal cual** desde `services/finanzas.py` (las definiciones
están alrededor de las líneas 424-486). No reescribir la lógica ni "mejorarla":
el objetivo es que el comportamiento sea idéntico.

```python
"""Las etapas del embudo, definidas una sola vez.

Esto vivia dentro de `services/finanzas.py`. Se saco a un modulo propio el
10/9/2026, cuando el modulo de marketing necesito contar las mismas etapas: dos
definiciones del embudo son dos numeros distintos para la misma pregunta en dos
paneles del mismo CRM, y eso es peor que no tener el segundo panel.

Se movio sin tocar el comportamiento. Los tests de finanzas pasan identicos.
"""

FUNNEL = ["sin_contactar", "interesado", "contactado",
          "demo_agendada", "demo_1", "demo_2", "demo_3",
          "presupuesto_enviado", "follow_up_1", "follow_up_2", "acepto",
          "cerrado", "en_desarrollo", "finalizado"]

# Fuera del orden a proposito, aunque formen parte de ETAPAS_PRECLIENTE /
# ETAPAS_CLIENTE.
EXCLUIDOS_DEL_FUNNEL = {
    "en_espera": "pausa del cliente, no un avance",
    "rechazo": "salida tras haber avanzado, no una etapa",
}


def normalizar_estado(estado: str) -> str:
    """Traduce un `crm_status` viejo al vocabulario nuevo, si corresponde.

    `lead_events` mezcla las dos epocas: la migracion de `database.py`
    reescribe `businesses.crm_status` pero no toca el historial de eventos, asi
    que todo lo de antes de la migracion quedo con los nombres viejos
    (`reunion_agendada`, `reunion_hecha`, `negociacion`, `cliente_cerrado`, y
    los alias `agendo`/`firmo`) y todo lo de despues ya nace con los nuevos.
    Sin esto, `alcanzo` dejaria de contar cualquier lead viejo.
    """
    from database import _MAPA_ESTADOS_VIEJOS

    return _MAPA_ESTADOS_VIEJOS.get(estado, estado)


def alcanzo(eventos: set, etapa: str) -> bool:
    """Si el lead paso por `etapa` o por cualquiera posterior, alguna vez.

    Se mira contra el historial de `lead_events`, no contra el `crm_status` de
    hoy: un lead que llego a demo y despues se cayo a `no_interesa` figura hoy
    como `no_interesa`, y contarlo por el estado actual lo perderia.
    """
    objetivo = FUNNEL.index(etapa)
    normalizados = {normalizar_estado(e) for e in eventos}
    return any(e in FUNNEL and FUNNEL.index(e) >= objetivo for e in normalizados)


def dividir(numerador: float, denominador: float):
    """Division simple, o None si el denominador es cero."""
    if not denominador:
        return None
    return round(numerador / denominador, 2)


def costo(inversion: float, cantidad: float):
    """Costo unitario, o None si no hay de que dividir.

    Sin `cantidad` la division no esta definida: un mes sin ventas no tiene un
    costo por venta de cero, no tiene costo por venta — la planilla mostraba
    #DIV/0! y esa era la lectura correcta. Sin `inversion` tampoco: no hubo
    campana que costear, asi que no es un costo de cero.
    """
    if not inversion:
        return None
    return dividir(inversion, cantidad)
```

- [ ] **Step 4: Correr el test nuevo**

Run: `python -m pytest tests/test_embudo.py -v`
Expected: PASS, 13 tests

- [ ] **Step 5: Guardar la línea base de los tests de Finanzas**

Antes de tocar `services/finanzas.py`, dejar registro de que pasan:

Run: `python -m pytest tests/test_finanzas_pauta.py tests/test_finanzas_service.py -v`
Expected: PASS. Anotar cuántos tests son — el número tiene que ser el mismo en
el Step 8.

- [ ] **Step 6: Hacer que `services/finanzas.py` importe del módulo nuevo**

Borrar de `services/finanzas.py` las definiciones de `FUNNEL`,
`EXCLUIDOS_DEL_FUNNEL`, `_normalizar_estado`, `alcanzo`, `_dividir` y `_costo`,
y poner en su lugar, cerca de los otros imports del archivo:

```python
# El embudo se define en services/embudo.py, no aca: el modulo de marketing
# cuenta las mismas etapas y dos definiciones darian dos numeros distintos.
from services.embudo import (FUNNEL, EXCLUIDOS_DEL_FUNNEL, alcanzo,  # noqa: F401
                             normalizar_estado)
from services.embudo import costo as _costo
from services.embudo import dividir as _dividir
from services.embudo import normalizar_estado as _normalizar_estado  # noqa: F401
```

Los alias con guion bajo existen para no tener que tocar ninguna de las llamadas
internas de Finanzas ni sus tests. `alcanzo` y `FUNNEL` ya se llamaban sin guion
bajo, así que quedan igual.

- [ ] **Step 7: Verificar que no quedó ninguna definición duplicada**

Run: `grep -n "^FUNNEL\|^def alcanzo\|^def _dividir\|^def _costo\|^def _normalizar_estado\|^EXCLUIDOS_DEL_FUNNEL" services/finanzas.py`
Expected: sin resultados. Si alguna aparece, quedó una definición vieja que va a
pisar al import.

- [ ] **Step 8: Correr los tests de Finanzas y comparar contra la línea base**

Run: `python -m pytest tests/ -k finanzas -v`
Expected: PASS, exactamente la misma cantidad que en el Step 5. Un solo test
distinto significa que el movimiento cambió comportamiento — revertir y volver a
mover sin editar.

- [ ] **Step 9: Commit**

```bash
git add services/embudo.py services/finanzas.py tests/test_embudo.py
git commit -m "refactor(embudo): las etapas se definen en un solo modulo

Finanzas define que es una demo y que es una venta leyendo lead_events
acumulado. El modulo de marketing necesita contar lo mismo, y si lo
reescribe el CRM muestra dos numeros distintos para la misma pregunta en
dos paneles.

Movido sin tocar comportamiento: finanzas conserva sus alias privados y
sus tests pasan identicos."
```

---

### Task 2: Migraciones de esquema

**Files:**
- Modify: `database.py` (dentro de `init_db`, después de la creación de tablas
  existentes)
- Test: `tests/test_marketing_db.py`

**Interfaces:**
- Consumes: `database._add_column`, `database._grant_panel_to_existing_roles`,
  `database._connect`
- Produces: columnas `businesses.meta_campaign_id`, `meta_campaign_name`,
  `meta_adset_id`, `meta_ad_id`, `meta_ad_name`; tablas `meta_insights` y
  `radiografias`; el panel `marketing` en `roles.panel_access`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_marketing_db.py`:

```python
"""El esquema del modulo de marketing.

Todo aditivo: ninguna migracion altera una tabla, columna o consulta que ya
existia. La prueba de eso es que los leads siguen leyendose igual despues de
migrar.
"""

import json
import sqlite3

import pytest

from database import _connect, init_db


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _columnas(db, tabla):
    conn = _connect(db)
    try:
        return {c[1] for c in conn.execute(f"PRAGMA table_info({tabla})")}
    finally:
        conn.close()


def test_businesses_tiene_las_columnas_de_meta(db):
    cols = _columnas(db, "businesses")
    assert {"meta_campaign_id", "meta_campaign_name", "meta_adset_id",
            "meta_ad_id", "meta_ad_name"} <= cols


def test_no_se_perdio_ninguna_columna_de_businesses(db):
    """La migracion es aditiva: lo que ya estaba sigue estando."""
    cols = _columnas(db, "businesses")
    assert {"id", "name", "crm_status", "source", "notes", "form_data",
            "scraped_at", "email", "website"} <= cols


def test_meta_insights_existe_con_su_unique(db):
    cols = _columnas(db, "meta_insights")
    assert {"date", "campaign_id", "campaign_name", "spend", "currency",
            "impressions", "clicks", "reach", "leads", "synced_at"} <= cols


def test_meta_insights_no_admite_dos_filas_del_mismo_dia_y_campana(db):
    conn = _connect(db)
    try:
        conn.execute("INSERT INTO meta_insights (date, campaign_id, spend) "
                     "VALUES ('2026-03-01', '123', 10.0)")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO meta_insights (date, campaign_id, spend) "
                         "VALUES ('2026-03-01', '123', 20.0)")
            conn.commit()
    finally:
        conn.close()


def test_radiografias_existe(db):
    cols = _columnas(db, "radiografias")
    assert {"generated_at", "period_start", "period_end", "dossier_json",
            "report_json", "model", "tokens_in", "tokens_out", "status",
            "error_message"} <= cols


def test_el_panel_marketing_le_llega_a_los_roles_que_ya_existian(db):
    """Un panel nuevo no le llega a nadie salvo admins si no se migra."""
    conn = _connect(db)
    try:
        filas = conn.execute("SELECT panel_access FROM roles").fetchall()
    finally:
        conn.close()
    assert filas, "la siembra de roles no corrio"
    for fila in filas:
        assert "marketing" in json.loads(fila["panel_access"])


def test_init_db_es_idempotente(db):
    """Correrla dos veces no rompe ni duplica nada."""
    init_db(db)
    init_db(db)
    assert {"meta_campaign_id"} <= _columnas(db, "businesses")
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_marketing_db.py -v`
Expected: FAIL — `test_businesses_tiene_las_columnas_de_meta` con `KeyError` o
assert, y `test_meta_insights_existe_con_su_unique` con
`sqlite3.OperationalError: no such table: meta_insights`

- [ ] **Step 3: Agregar las migraciones en `init_db`**

En `database.py`, dentro de `init_db`, junto a las otras llamadas a
`_add_column` y `_grant_panel_to_existing_roles` (buscar la línea
`_grant_panel_to_existing_roles(conn, "meta")` como referencia de ubicación):

```python
        # ── Marketing / Meta Ads ──────────────────────────────────────────────
        # La campana del lead vivia embebida en `notes` como
        # "Meta Lead Ad · <campana>". Un LIKE sobre notes no agrupa ni escala, y
        # el nombre de una campana puede cambiar en Meta mientras el id no.
        _add_column(conn, "businesses", "meta_campaign_id", "TEXT")
        _add_column(conn, "businesses", "meta_campaign_name", "TEXT")
        _add_column(conn, "businesses", "meta_adset_id", "TEXT")
        _add_column(conn, "businesses", "meta_ad_id", "TEXT")
        _add_column(conn, "businesses", "meta_ad_name", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_biz_meta_campaign "
                     "ON businesses(meta_campaign_id)")

        # Gasto y performance que devuelve la Marketing API, al grano de
        # campana x dia. Solo metricas crudas y contables: CPM, CPC, CTR y CPL
        # se derivan al calcular. Una tasa guardada se desincroniza de sus
        # componentes y despues nadie sabe cual manda.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_insights (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                date          TEXT NOT NULL,
                campaign_id   TEXT NOT NULL,
                campaign_name TEXT,
                spend         REAL DEFAULT 0,
                currency      TEXT,
                impressions   INTEGER DEFAULT 0,
                clicks        INTEGER DEFAULT 0,
                reach         INTEGER DEFAULT 0,
                leads         INTEGER DEFAULT 0,
                synced_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, campaign_id)
            )
        """)

        # Cada corrida guarda el dossier entero, no solo el informe: es lo que
        # permite auditar despues por que se dijo lo que se dijo, y comparar
        # contra el periodo anterior sin recalcular el pasado.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS radiografias (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                period_start  TEXT NOT NULL,
                period_end    TEXT NOT NULL,
                dossier_json  TEXT NOT NULL,
                report_json   TEXT,
                model         TEXT,
                tokens_in     INTEGER,
                tokens_out    INTEGER,
                status        TEXT DEFAULT 'ok',
                error_message TEXT
            )
        """)

        _grant_panel_to_existing_roles(conn, "marketing")
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_marketing_db.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Verificar que no se rompió nada del resto**

Run: `python -m pytest tests/ -q`
Expected: PASS. Si algo falla acá, la migración no era aditiva.

- [ ] **Step 6: Commit**

```bash
git add database.py tests/test_marketing_db.py
git commit -m "feat(marketing): esquema para insights de Meta y radiografias

Cinco columnas en businesses para sacar la campana de adentro de notes,
la tabla meta_insights al grano de campana x dia, y radiografias para los
snapshots. Todo aditivo.

meta_insights guarda solo metricas contables: CPM, CPC, CTR y CPL se
derivan al calcular, porque una tasa guardada se desincroniza de sus
componentes."
```

---

### Task 3: Backfill de la campaña desde `notes`

Los 238 leads históricos tienen la campaña embebida en `notes`. Los ids no se
pueden recuperar del texto: quedan `NULL` y el emparejamiento cae al nombre.

**Files:**
- Create: `services/meta_campanas.py`
- Test: `tests/test_meta_campanas.py`

**Interfaces:**
- Consumes: `database._connect`
- Produces:
  - `parsear_campana(notes: str) -> str | None`
  - `backfill_campanas(db_path: str) -> dict` → `{"revisados": int, "escritos": int, "sin_campana": int}`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_meta_campanas.py`:

```python
"""Sacar la campana de adentro de `notes` a su columna.

Los 238 leads historicos la tienen como texto: "Meta Lead Ad · <campana>". Los
nombres reales medidos en la base son "Leads - Form - 2026", "Leads - UY - 2026",
"Leads - ARG - CH - 2026", "Leads - ARG - 2026" y "Leads - Abril 2026". Trece
leads no traen campana ninguna.
"""

import pytest

from database import _connect, init_db
from services.meta_campanas import backfill_campanas, parsear_campana


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _lead(db, nombre, notes, source="meta", telefono=None):
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO businesses (name, notes, source, phone) VALUES (?,?,?,?)",
            (nombre, notes, source, telefono or nombre))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _campana_de(db, lead_id):
    conn = _connect(db)
    try:
        fila = conn.execute("SELECT meta_campaign_name FROM businesses WHERE id=?",
                            (lead_id,)).fetchone()
        return fila["meta_campaign_name"]
    finally:
        conn.close()


@pytest.mark.parametrize("notes,esperado", [
    ("Meta Lead Ad · Leads - UY - 2026", "Leads - UY - 2026"),
    ("Meta Lead Ad · Leads - ARG - CH - 2026", "Leads - ARG - CH - 2026"),
    ("Meta Lead Ad · Leads - Abril 2026", "Leads - Abril 2026"),
])
def test_parsea_los_nombres_reales(notes, esperado):
    assert parsear_campana(notes) == esperado


def test_solo_mira_la_primera_linea():
    """El vendedor escribe notas debajo; no son parte del nombre."""
    notes = "Meta Lead Ad · Leads - UY - 2026\nLlame el martes, no atendio."
    assert parsear_campana(notes) == "Leads - UY - 2026"


def test_sin_campana_devuelve_none():
    assert parsear_campana("Meta Lead Ad") is None
    assert parsear_campana("Meta Lead Ad · ") is None
    assert parsear_campana("") is None
    assert parsear_campana(None) is None


def test_una_nota_que_no_es_de_meta_devuelve_none():
    assert parsear_campana("Lo llamo Juan, pidio presupuesto") is None


def test_backfill_escribe_la_columna(db):
    lead = _lead(db, "Peluqueria Ana", "Meta Lead Ad · Leads - UY - 2026")
    r = backfill_campanas(db)
    assert r["escritos"] == 1
    assert _campana_de(db, lead) == "Leads - UY - 2026"


def test_backfill_cuenta_los_que_no_tienen_campana(db):
    _lead(db, "Sin campana", "Meta Lead Ad")
    r = backfill_campanas(db)
    assert r["sin_campana"] == 1
    assert r["escritos"] == 0


def test_backfill_no_toca_leads_que_no_son_de_meta(db):
    """Discovery y el padron tienen sus propias notas; no son campanas."""
    lead = _lead(db, "Ferreteria", "Meta Lead Ad · Leads - UY - 2026",
                 source="discovery")
    backfill_campanas(db)
    assert _campana_de(db, lead) is None


def test_backfill_es_idempotente(db):
    """Correrlo dos veces no reescribe nada la segunda."""
    _lead(db, "Peluqueria Ana", "Meta Lead Ad · Leads - UY - 2026")
    backfill_campanas(db)
    r = backfill_campanas(db)
    assert r["escritos"] == 0


def test_backfill_no_pisa_una_campana_ya_cargada(db):
    """La ingesta nueva escribe el id y el nombre; el backfill no los toca."""
    lead = _lead(db, "Nuevo", "Meta Lead Ad · Leads - UY - 2026")
    conn = _connect(db)
    try:
        conn.execute("UPDATE businesses SET meta_campaign_name=?, meta_campaign_id=? "
                     "WHERE id=?", ("Nombre de la API", "12345", lead))
        conn.commit()
    finally:
        conn.close()
    backfill_campanas(db)
    assert _campana_de(db, lead) == "Nombre de la API"
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_meta_campanas.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'services.meta_campanas'`

- [ ] **Step 3: Escribir `services/meta_campanas.py`**

```python
"""La campana del lead, sacada de `notes` a su columna.

Hasta el 10/9/2026 la unica huella de que campana trajo a un lead era el texto
"Meta Lead Ad · <campana>" que `routes/meta.py` escribe en `notes`. Sirve para
que un humano lo lea, no para agrupar: un LIKE sobre notes no escala, y no hay
forma de juntar el lead con el gasto de su campana.

El backfill recupera lo que se pueda de los leads viejos. Los ids de campana no
estan en el texto y no hay de donde sacarlos: quedan en NULL y esos leads se
emparejan con los Insights por nombre, que es fragil si la campana se renombro
en Meta. Los leads nuevos si traen id.
"""

import logging
import re

from database import _connect

logger = logging.getLogger(__name__)

# El separador es un punto medio (·), no un guion. Lo escribe routes/meta.py.
_PATRON = re.compile(r"^Meta Lead Ad\s*·\s*(.+)$")


def parsear_campana(notes):
    """El nombre de la campana que hay en `notes`, o None.

    Solo la primera linea: el vendedor escribe sus notas debajo y no son parte
    del nombre.
    """
    if not notes:
        return None
    m = _PATRON.match(notes.split("\n")[0].strip())
    if not m:
        return None
    nombre = m.group(1).strip()
    return nombre or None


def backfill_campanas(db_path: str) -> dict:
    """Escribe `meta_campaign_name` en los leads de Meta que no lo tengan.

    Idempotente: solo toca filas con la columna vacia, asi que correrlo dos
    veces no escribe nada la segunda y nunca pisa lo que escribio la ingesta.
    """
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT id, notes FROM businesses "
            "WHERE source = 'meta' "
            "  AND (meta_campaign_name IS NULL OR meta_campaign_name = '')"
        ).fetchall()

        escritos, sin_campana = 0, 0
        for fila in filas:
            nombre = parsear_campana(fila["notes"])
            if not nombre:
                sin_campana += 1
                continue
            conn.execute("UPDATE businesses SET meta_campaign_name = ? WHERE id = ?",
                         (nombre, fila["id"]))
            escritos += 1
        conn.commit()
    finally:
        conn.close()

    logger.info(f"backfill de campanas: {escritos} escritos, "
                f"{sin_campana} sin campana, sobre {len(filas)} revisados")
    return {"revisados": len(filas), "escritos": escritos,
            "sin_campana": sin_campana}
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_meta_campanas.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Probarlo contra una copia del backup real**

Nunca contra producción. Copiar el backup y correrlo:

> **Ojo con `/tmp`.** En Git Bash `/tmp` es `%LOCALAPPDATA%\Temp`, pero Python en
> Windows lee `/tmp/x.db` como `C:\tmp\x.db`. Copiar con `cp` a `/tmp` y abrir esa
> ruta desde Python crea una base vacia en otro lado, y el paso "verificar contra
> datos reales" pasa en verde sin haber leido nada. Usar una ruta absoluta de
> Windows, o el scratchpad de la sesion, en `$SCRATCH`.

```bash
SCRATCH="C:/Users/juant/AppData/Local/Temp"
cp backups/leads_pre_preclientes_8sep.db "$SCRATCH/backfill_prueba.db"
python -c "
from database import init_db
from services.meta_campanas import backfill_campanas
import sqlite3
db = r'$SCRATCH/backfill_prueba.db'
init_db(db)
print(backfill_campanas(db))
c = sqlite3.connect(db)
for r in c.execute('SELECT meta_campaign_name, COUNT(*) FROM businesses '
                   \"WHERE source='meta' GROUP BY 1 ORDER BY 2 DESC\"):
    print(r)
"
```

Expected: `escritos` 225, `sin_campana` 13, y la distribución de campañas igual a
la del spec §4 — Leads - Form - 2026 con 85, Leads - UY - 2026 con 83,
Leads - ARG - CH - 2026 con 51, Leads - ARG - 2026 con 5, Leads - Abril 2026 con 1.
Si los números no dan, el patrón no cubre alguna variante real: mirar las filas
que quedaron en `NULL` antes de seguir.

- [ ] **Step 6: Commit**

```bash
git add services/meta_campanas.py tests/test_meta_campanas.py
git commit -m "feat(marketing): backfill de la campana desde notes

Los 238 leads historicos tienen la campana como texto dentro de notes.
El backfill la pasa a columna: 225 se recuperan, 13 no traen campana.

Los ids no estan en el texto y quedan en NULL. Esos leads se emparejan
con los Insights por nombre, que se rompe si la campana se renombro en
Meta — por eso la ingesta nueva guarda el id."
```

---

### Task 4: La ingesta guarda campaña e ids

`routes/meta.py` ya le pide `campaign_name` y `ad_name` al Graph. Falta pedir los
ids y escribir las columnas nuevas. **`notes` se sigue escribiendo igual**: nada
que ya lo lea se rompe.

> **Territorio de la sesión D.** Está anotado en `COORDINACION.md`. El cambio es
> aditivo y no toca la lógica de deduplicación ni de notificación.

**Files:**
- Modify: `routes/meta.py` — los `fields` de las cuatro llamadas que traen leads
  (líneas 223, 390, 519, 708) y el `_fetch_and_store_lead`
- Test: `tests/test_meta_ingesta_campana.py`

**Interfaces:**
- Consumes: `services.meta_campanas.parsear_campana`
- Produces: leads nuevos con `meta_campaign_id`, `meta_campaign_name`,
  `meta_adset_id`, `meta_ad_id`, `meta_ad_name` poblados

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_meta_ingesta_campana.py`:

```python
"""La ingesta de leads de Meta guarda la campana en columna, no solo en notes.

`notes` se sigue escribiendo igual que siempre: hay codigo y hay gente que lo
lee. Esto es aditivo.
"""

import pytest

from database import _connect, init_db


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def test_los_fields_del_graph_piden_los_ids():
    """Sin pedirlos, la API no los manda y las columnas quedan vacias."""
    import io
    fuente = io.open("routes/meta.py", encoding="utf-8").read()
    # Cada llamada que trae leads tiene que pedir los tres ids.
    assert fuente.count("campaign_id") >= 4, "falta campaign_id en algun fields"
    assert "adset_id" in fuente
    assert "ad_id" in fuente


def test_guardar_lead_escribe_las_columnas(db):
    from routes.meta import _guardar_campana_del_lead

    conn = _connect(db)
    try:
        cur = conn.execute("INSERT INTO businesses (name, phone, source) "
                           "VALUES ('Test', '+59899', 'meta')")
        conn.commit()
        lead_id = cur.lastrowid
    finally:
        conn.close()

    _guardar_campana_del_lead(db, lead_id, {
        "campaign_id": "120", "campaign_name": "Leads - UY - 2026",
        "adset_id": "121", "ad_id": "122", "ad_name": "Video corto",
    })

    conn = _connect(db)
    try:
        f = conn.execute("SELECT * FROM businesses WHERE id=?", (lead_id,)).fetchone()
    finally:
        conn.close()
    assert f["meta_campaign_id"] == "120"
    assert f["meta_campaign_name"] == "Leads - UY - 2026"
    assert f["meta_adset_id"] == "121"
    assert f["meta_ad_id"] == "122"
    assert f["meta_ad_name"] == "Video corto"


def test_un_lead_sin_campana_no_rompe(db):
    """Meta no siempre manda todo. Ausencia no es error."""
    from routes.meta import _guardar_campana_del_lead

    conn = _connect(db)
    try:
        cur = conn.execute("INSERT INTO businesses (name, phone, source) "
                           "VALUES ('Test', '+59898', 'meta')")
        conn.commit()
        lead_id = cur.lastrowid
    finally:
        conn.close()

    _guardar_campana_del_lead(db, lead_id, {})

    conn = _connect(db)
    try:
        f = conn.execute("SELECT meta_campaign_id FROM businesses WHERE id=?",
                         (lead_id,)).fetchone()
    finally:
        conn.close()
    assert f["meta_campaign_id"] is None
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_meta_ingesta_campana.py -v`
Expected: FAIL — `test_los_fields_del_graph_piden_los_ids` con assert, y los
otros dos con `ImportError: cannot import name '_guardar_campana_del_lead'`

- [ ] **Step 3: Agregar el helper en `routes/meta.py`**

Cerca de las otras funciones auxiliares del archivo:

```python
def _guardar_campana_del_lead(db: str, lead_id: int, lead_data: dict) -> None:
    """Escribe la campana del lead en sus columnas propias.

    Aditivo: `notes` se sigue escribiendo igual en el llamador. Esto existe
    para poder agrupar y para juntar el lead con el gasto de su campana, que
    con la campana metida adentro de un texto no se puede.

    Meta no siempre manda los cinco campos. Lo que no venga queda en NULL: la
    ausencia de un dato no es un error.
    """
    from database import _connect

    conn = _connect(db)
    try:
        conn.execute(
            "UPDATE businesses SET meta_campaign_id = ?, meta_campaign_name = ?, "
            "meta_adset_id = ?, meta_ad_id = ?, meta_ad_name = ? WHERE id = ?",
            (lead_data.get("campaign_id") or None,
             lead_data.get("campaign_name") or None,
             lead_data.get("adset_id") or None,
             lead_data.get("ad_id") or None,
             lead_data.get("ad_name") or None,
             lead_id))
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Pedirle los ids al Graph**

En las cuatro llamadas que traen leads, agregar los tres ids al `fields`. Las
ubicaciones y el reemplazo exacto:

- Línea ~223: `"fields": "field_data,created_time,ad_name,campaign_name,form_id"`
  → `"fields": "field_data,created_time,ad_name,ad_id,adset_id,campaign_name,campaign_id,form_id"`
- Línea ~390: `"fields": "id,created_time,field_data,ad_name,campaign_name"`
  → `"fields": "id,created_time,field_data,ad_name,ad_id,adset_id,campaign_name,campaign_id"`
- Línea ~519: mismo reemplazo que la 390
- Línea ~708: mismo reemplazo que la 390

- [ ] **Step 5: Llamar al helper desde `_fetch_and_store_lead`**

En `_fetch_and_store_lead`, justo después de que se crea el lead nuevo (donde ya
se tiene `biz_id` y `lead_data`), y también en la rama del lead que ya existía
(donde se tiene `existente_id`):

```python
                    _guardar_campana_del_lead(db, biz_id, lead_data)
```

- [ ] **Step 6: Correr los tests**

Run: `python -m pytest tests/test_meta_ingesta_campana.py -v`
Expected: PASS, 3 tests

- [ ] **Step 7: Verificar que no se rompió la ingesta existente**

Run: `python -m pytest tests/ -k meta -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add routes/meta.py tests/test_meta_ingesta_campana.py
git commit -m "feat(marketing): la ingesta guarda campana y anuncio en columnas

Los fields del Graph ahora piden campaign_id, adset_id y ad_id, que antes
no se pedian y por eso no llegaban. notes se sigue escribiendo igual: hay
codigo y hay gente que lo lee.

El id importa porque el nombre de una campana puede cambiar en Meta y
romper el emparejamiento historico con el gasto."
```

---

### Task 5: Sincronizar los Insights de Meta

**Files:**
- Create: `services/meta_insights.py`
- Test: `tests/test_meta_insights.py`

**Interfaces:**
- Consumes: `database._connect`, `routes.meta.GRAPH`, `services.corridas`
- Produces:
  - `hay_credenciales() -> bool`
  - `sincronizar(db_path: str, desde: str, hasta: str, fetch=None) -> dict` →
    `{"filas": int, "campanas": int, "salteado": str | None}`
  - `DIAS_A_RESINCRONIZAR: int = 7`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_meta_insights.py`:

```python
"""Sincronizacion del gasto de Meta.

Ningun test toca la red: `sincronizar` recibe la funcion que trae los datos.
"""

import pytest

from database import _connect, init_db
from services.meta_insights import hay_credenciales, sincronizar


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _respuesta(spend="100.50", leads=3):
    """Una fila de Insights con la forma real que devuelve el Graph."""
    return [{
        "date_start": "2026-03-01",
        "campaign_id": "120",
        "campaign_name": "Leads - UY - 2026",
        "spend": spend,
        "account_currency": "UYU",
        "impressions": "4500",
        "clicks": "180",
        "reach": "3900",
        "actions": [
            {"action_type": "post_engagement", "value": "40"},
            {"action_type": "lead", "value": str(leads)},
        ],
    }]


def _filas(db):
    conn = _connect(db)
    try:
        return conn.execute("SELECT * FROM meta_insights ORDER BY date").fetchall()
    finally:
        conn.close()


def test_guarda_una_fila_por_campana_y_dia(db):
    r = sincronizar(db, "2026-03-01", "2026-03-01", fetch=lambda *a, **k: _respuesta())
    assert r["filas"] == 1
    fila = _filas(db)[0]
    assert fila["date"] == "2026-03-01"
    assert fila["campaign_id"] == "120"
    assert fila["spend"] == 100.5
    assert fila["impressions"] == 4500
    assert fila["clicks"] == 180
    assert fila["reach"] == 3900


def test_saca_los_leads_de_actions(db):
    """Los leads no son un campo: hay que buscarlos entre las acciones."""
    sincronizar(db, "2026-03-01", "2026-03-01", fetch=lambda *a, **k: _respuesta(leads=7))
    assert _filas(db)[0]["leads"] == 7


def test_sin_accion_de_lead_guarda_cero(db):
    sinlead = [{**_respuesta()[0], "actions": [
        {"action_type": "post_engagement", "value": "40"}]}]
    sincronizar(db, "2026-03-01", "2026-03-01", fetch=lambda *a, **k: sinlead)
    assert _filas(db)[0]["leads"] == 0


def test_sin_actions_no_rompe(db):
    """Una campana sin ninguna accion no trae la clave."""
    sinactions = [{k: v for k, v in _respuesta()[0].items() if k != "actions"}]
    sincronizar(db, "2026-03-01", "2026-03-01", fetch=lambda *a, **k: sinactions)
    assert _filas(db)[0]["leads"] == 0


def test_guarda_la_moneda_sin_convertir(db):
    """Convertir gasto de marzo con la cotizacion de hoy da un numero que
    parece preciso y no lo es."""
    sincronizar(db, "2026-03-01", "2026-03-01", fetch=lambda *a, **k: _respuesta())
    assert _filas(db)[0]["currency"] == "UYU"


def test_es_idempotente(db):
    """Correrlo dos veces deja una fila, no dos."""
    f = lambda *a, **k: _respuesta()
    sincronizar(db, "2026-03-01", "2026-03-01", fetch=f)
    sincronizar(db, "2026-03-01", "2026-03-01", fetch=f)
    assert len(_filas(db)) == 1


def test_resincronizar_actualiza_el_valor(db):
    """Meta ajusta cifras hacia atras: la ultima corrida manda."""
    sincronizar(db, "2026-03-01", "2026-03-01", fetch=lambda *a, **k: _respuesta("100.50"))
    sincronizar(db, "2026-03-01", "2026-03-01", fetch=lambda *a, **k: _respuesta("133.00"))
    filas = _filas(db)
    assert len(filas) == 1
    assert filas[0]["spend"] == 133.0


def test_sin_credenciales_se_saltea_sin_romper(db, monkeypatch):
    """El resto del modulo tiene que funcionar sin gasto."""
    monkeypatch.delenv("META_ADS_TOKEN", raising=False)
    monkeypatch.delenv("META_AD_ACCOUNT_ID", raising=False)
    assert hay_credenciales() is False
    r = sincronizar(db, "2026-03-01", "2026-03-01")
    assert r["salteado"] == "sin_credenciales"
    assert r["filas"] == 0


def test_una_respuesta_vacia_no_rompe(db):
    r = sincronizar(db, "2026-03-01", "2026-03-01", fetch=lambda *a, **k: [])
    assert r["filas"] == 0


def test_usa_la_misma_version_de_graph_que_el_resto():
    """Una version propia se desincroniza en silencio. Ya paso con el webhook
    de leadgen, que quedo fijado en v25.0 y Meta dejo de entregar."""
    import io
    fuente = io.open("services/meta_insights.py", encoding="utf-8").read()
    assert "from routes.meta import GRAPH" in fuente
    assert "graph.facebook.com" not in fuente
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_meta_insights.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'services.meta_insights'`

- [ ] **Step 3: Escribir `services/meta_insights.py`**

```python
"""El gasto de Meta Ads, sincronizado a una tabla propia.

El CRM sabia que campana trajo cada lead pero no cuanto costo. Sin eso no hay
costo por lead ni costo por demo, que es la pregunta central de donde invertir.

**Credencial aparte.** El META_PAGE_TOKEN no sirve: los Insights de ads piden
`ads_read` sobre la cuenta publicitaria. Van META_ADS_TOKEN y META_AD_ACCOUNT_ID
en los secrets de Fly. Si falta alguna, el sync se saltea con un warning y el
resto del modulo funciona igual, sin las metricas de costo.

**No se convierte la moneda.** Meta devuelve el gasto en la moneda de la cuenta.
Convertir gasto de marzo con la cotizacion de hoy produce un numero que parece
preciso y no lo es.

**Version de la Graph API:** la misma constante que routes/meta.py, nunca una
propia. En 2026 el webhook de leadgen quedo fijado en v25.0, Meta dejo de
entregar en silencio y se busco el problema en el token durante horas.
"""

import logging
import os

from database import _connect

logger = logging.getLogger(__name__)

# Meta ajusta las cifras de los ultimos dias hacia atras, asi que la ventana
# reciente se vuelve a pedir siempre en vez de darla por cerrada.
DIAS_A_RESINCRONIZAR = 7

# Los action_type con los que Meta reporta un lead de formulario. Son dos
# porque el nombre cambio entre versiones y conviven en cuentas viejas.
_ACCIONES_DE_LEAD = ("lead", "leadgen_grouped", "onsite_conversion.lead_grouped")


def hay_credenciales() -> bool:
    return bool(os.environ.get("META_ADS_TOKEN")
                and os.environ.get("META_AD_ACCOUNT_ID"))


def _traer_de_la_api(desde: str, hasta: str) -> list:
    """Pide los Insights al Graph. Import diferido: la maquina tiene 256 MB."""
    import json

    import requests

    from routes.meta import GRAPH

    cuenta = os.environ["META_AD_ACCOUNT_ID"]
    params = {
        "access_token": os.environ["META_ADS_TOKEN"],
        "level": "campaign",
        "time_increment": 1,
        "fields": ("campaign_id,campaign_name,spend,account_currency,"
                   "impressions,clicks,reach,actions"),
        "time_range": json.dumps({"since": desde, "until": hasta}),
        "limit": 500,
    }

    filas, url = [], f"{GRAPH}/{cuenta}/insights"
    while url:
        r = requests.get(url, params=params, timeout=30)
        if not r.ok:
            logger.error(f"Insights: la API contesto {r.status_code}")
            break
        d = r.json()
        if "error" in d:
            logger.error(f"Insights: {d['error']}")
            break
        filas.extend(d.get("data", []))
        url = d.get("paging", {}).get("next")
        params = {}   # el `next` ya trae todo adentro
    return filas


def _leads_de(fila: dict) -> int:
    """Los leads no son un campo: hay que buscarlos entre las acciones."""
    for accion in fila.get("actions") or []:
        if accion.get("action_type") in _ACCIONES_DE_LEAD:
            try:
                return int(float(accion.get("value") or 0))
            except (TypeError, ValueError):
                return 0
    return 0


def _entero(valor) -> int:
    try:
        return int(float(valor or 0))
    except (TypeError, ValueError):
        return 0


def sincronizar(db_path: str, desde: str, hasta: str, fetch=None) -> dict:
    """Trae los Insights del periodo y los deja en `meta_insights`.

    Idempotente por (date, campaign_id): correrlo dos veces no duplica, y
    resincronizar un dia ya guardado lo actualiza — la ultima corrida manda,
    porque Meta corrige cifras hacia atras.

    `fetch` existe para los tests: recibe (desde, hasta) y devuelve la lista de
    filas crudas. En produccion se usa el default, que va a la API.
    """
    if fetch is None:
        if not hay_credenciales():
            logger.warning("Insights: sin META_ADS_TOKEN o META_AD_ACCOUNT_ID, "
                           "se saltea el sync")
            return {"filas": 0, "campanas": 0, "salteado": "sin_credenciales"}
        fetch = _traer_de_la_api

    crudas = fetch(desde, hasta)

    conn = _connect(db_path)
    try:
        campanas = set()
        for fila in crudas:
            campana = fila.get("campaign_id")
            if not campana:
                continue
            campanas.add(campana)
            conn.execute("""
                INSERT INTO meta_insights
                    (date, campaign_id, campaign_name, spend, currency,
                     impressions, clicks, reach, leads, synced_at)
                VALUES (?,?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)
                ON CONFLICT(date, campaign_id) DO UPDATE SET
                    campaign_name = excluded.campaign_name,
                    spend         = excluded.spend,
                    currency      = excluded.currency,
                    impressions   = excluded.impressions,
                    clicks        = excluded.clicks,
                    reach         = excluded.reach,
                    leads         = excluded.leads,
                    synced_at     = CURRENT_TIMESTAMP
            """, (
                fila.get("date_start"),
                campana,
                fila.get("campaign_name"),
                float(fila.get("spend") or 0),
                fila.get("account_currency"),
                _entero(fila.get("impressions")),
                _entero(fila.get("clicks")),
                _entero(fila.get("reach")),
                _leads_de(fila),
            ))
        conn.commit()
    finally:
        conn.close()

    logger.info(f"Insights: {len(crudas)} filas, {len(campanas)} campanas, "
                f"{desde} a {hasta}")
    return {"filas": len(crudas), "campanas": len(campanas), "salteado": None}
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_meta_insights.py -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Verificar el nombre real del action_type**

`_ACCIONES_DE_LEAD` tiene tres candidatos porque el nombre cambió entre
versiones. Cuál usa la cuenta de Scalerics **no está verificado**. Cuando haya
credenciales, correr una vez a mano y mirar la respuesta cruda:

```bash
python -c "
from services.meta_insights import _traer_de_la_api
for f in _traer_de_la_api('2026-08-01', '2026-08-07'):
    print(f.get('campaign_name'), [a['action_type'] for a in f.get('actions', [])])
"
```

Si el `action_type` que aparece no está en la tupla, agregarlo y anotar en el
docstring cuál usa la cuenta. **Sin esto, la columna `leads` va a quedar en cero
y todo el CPL va a dar `None`.** No es un bug del código: es un dato que no se
puede saber sin la cuenta.

- [ ] **Step 6: Commit**

```bash
git add services/meta_insights.py tests/test_meta_insights.py
git commit -m "feat(marketing): sincronizar el gasto de Meta a meta_insights

El CRM sabia que campana trajo cada lead pero no cuanto costo. Sin gasto
no hay costo por lead ni costo por demo.

Idempotente por (date, campaign_id), y resincroniza los ultimos 7 dias
siempre porque Meta corrige cifras hacia atras. Sin credenciales se
saltea con un warning en vez de romper: el resto del modulo funciona
sin las metricas de costo.

Usa la constante GRAPH de routes/meta.py, no una version propia. Un test
lo verifica sobre el fuente."
```

---

### Task 6: Wilson y la forma de una métrica

El ladrillo del dossier. Se aísla porque es la pieza más fácil de equivocar y la
que más se reusa.

**Files:**
- Create: `services/dossier.py`
- Test: `tests/test_dossier_metrica.py`

**Interfaces:**
- Consumes: nada
- Produces:
  - `wilson(exitos: int, total: int, z: float = 1.96) -> tuple[float, float]`
  - `MUESTRA_CHICA: int = 30`
  - `metrica(id, etiqueta, valor, fuente, formato="numero", numerador=None, denominador=None, anterior=None) -> dict`
  - `proporcion(id, etiqueta, exitos, total, fuente, anterior=None) -> dict`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_dossier_metrica.py`:

```python
"""El ladrillo del dossier: una metrica que se puede auditar.

Cada una trae su numerador, su denominador, su n y su intervalo. Sin eso, un
5% sobre 20 leads y un 5% sobre 2000 se leen igual, y no son lo mismo.
"""

import pytest

from services.dossier import MUESTRA_CHICA, metrica, proporcion, wilson


def test_wilson_sobre_una_muestra_conocida():
    bajo, alto = wilson(9, 83)
    assert bajo == pytest.approx(0.0581, abs=0.0005)
    assert alto == pytest.approx(0.1934, abs=0.0005)


def test_wilson_no_se_va_de_cero_a_uno():
    """Con 0 exitos el limite inferior no puede ser negativo."""
    bajo, alto = wilson(0, 20)
    assert bajo == 0.0
    assert alto < 1.0
    bajo, alto = wilson(20, 20)
    assert alto == 1.0
    assert bajo > 0.0


def test_wilson_sin_muestra_es_cero_a_uno():
    """Sin datos no se sabe nada: el intervalo es todo el rango."""
    assert wilson(0, 0) == (0.0, 1.0)


def test_wilson_se_angosta_con_mas_muestra():
    chico = wilson(5, 10)
    grande = wilson(500, 1000)
    assert (grande[1] - grande[0]) < (chico[1] - chico[0])


def test_proporcion_trae_todo_lo_que_hace_falta_para_auditarla():
    m = proporcion("campana.tasa_demo.uy", "Tasa de demo — UY", 9, 83, "crm")
    assert m["id"] == "campana.tasa_demo.uy"
    assert m["valor"] == pytest.approx(0.1084, abs=0.0001)
    assert m["numerador"] == 9
    assert m["denominador"] == 83
    assert m["n"] == 83
    assert m["fuente"] == "crm"
    assert m["formato"] == "porcentaje"
    assert len(m["ic95"]) == 2


def test_una_muestra_chica_se_marca():
    """Con 51 leads, cinco puntos de diferencia no significan nada."""
    assert proporcion("x", "X", 2, 20, "crm")["muestra_chica"] is True
    assert proporcion("x", "X", 20, 200, "crm")["muestra_chica"] is False
    assert MUESTRA_CHICA == 30


def test_proporcion_sin_denominador_vale_none_no_cero():
    m = proporcion("x", "X", 0, 0, "crm")
    assert m["valor"] is None
    assert m["muestra_chica"] is True


def test_metrica_calcula_el_delta_contra_el_periodo_anterior():
    m = metrica("x", "X", 120.0, "meta_insights", anterior=100.0)
    assert m["delta_periodo_anterior"] == 20.0


def test_metrica_sin_periodo_anterior_no_inventa_un_delta():
    """La primera corrida no tiene contra que comparar. Eso es None, no cero."""
    assert metrica("x", "X", 120.0, "meta_insights")["delta_periodo_anterior"] is None


def test_la_fuente_es_obligatoria_y_acotada():
    """No existe la fuente 'inferencia': las inferencias son del modelo y van
    en el informe, nunca en el dossier."""
    with pytest.raises(ValueError):
        metrica("x", "X", 1.0, "inferencia")
    with pytest.raises(ValueError):
        metrica("x", "X", 1.0, "")
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_dossier_metrica.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'services.dossier'`

- [ ] **Step 3: Escribir la base de `services/dossier.py`**

```python
"""El dossier: metricas calculadas, cada una auditable.

Esto es lo unico que el motor de IA va a ver. Nunca ve filas, ni nombres, ni
telefonos. Dos consecuencias: no puede inventar un numero que no este aca, y no
salen datos personales de 238 personas hacia un servicio externo.

Cada metrica trae numerador, denominador, n e intervalo. Sin eso, un 5% sobre 20
leads y un 5% sobre 2000 se leen igual en un grafico, y no son lo mismo.
"""

import math

# Debajo de esto, una diferencia entre dos grupos es ruido. Con 51 leads en la
# campana de ARG, cinco puntos contra la de UY no significan nada.
MUESTRA_CHICA = 30

# `inferencia` no esta a proposito: las inferencias son del modelo y viven en el
# informe, separadas de los hechos.
FUENTES = ("meta_insights", "crm", "derivada")


def wilson(exitos: int, total: int, z: float = 1.96):
    """Intervalo de confianza de Wilson para una proporcion.

    Wilson y no el normal: con muestras chicas o proporciones cerca de 0 o de 1
    —justo el caso aca— el intervalo normal se va abajo de cero o arriba de uno
    y deja de querer decir algo. Ademas no necesita ninguna dependencia nueva.
    """
    if total <= 0:
        return (0.0, 1.0)      # sin datos no se sabe nada
    p = exitos / total
    d = 1 + z * z / total
    centro = (p + z * z / (2 * total)) / d
    margen = z / d * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (round(max(0.0, centro - margen), 4),
            round(min(1.0, centro + margen), 4))


def metrica(id: str, etiqueta: str, valor, fuente: str, formato: str = "numero",
            numerador=None, denominador=None, anterior=None) -> dict:
    """Una metrica del dossier, con todo lo que hace falta para auditarla."""
    if fuente not in FUENTES:
        raise ValueError(
            f"fuente invalida: {fuente!r}. Tiene que ser una de {FUENTES}. "
            "Las inferencias no son metricas: van en el informe.")
    delta = None
    if anterior is not None and valor is not None:
        delta = round(valor - anterior, 4)
    return {
        "id": id,
        "etiqueta": etiqueta,
        "valor": valor,
        "formato": formato,
        "numerador": numerador,
        "denominador": denominador,
        "n": denominador,
        "ic95": None,
        "delta_periodo_anterior": delta,
        "fuente": fuente,
        "muestra_chica": False,
    }


def proporcion(id: str, etiqueta: str, exitos: int, total: int, fuente: str,
               anterior=None) -> dict:
    """Una metrica que es una tasa, con su intervalo y su marca de muestra."""
    valor = round(exitos / total, 4) if total else None
    m = metrica(id, etiqueta, valor, fuente, formato="porcentaje",
                numerador=exitos, denominador=total, anterior=anterior)
    m["ic95"] = list(wilson(exitos, total))
    m["muestra_chica"] = total < MUESTRA_CHICA
    return m
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_dossier_metrica.py -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add services/dossier.py tests/test_dossier_metrica.py
git commit -m "feat(marketing): la metrica del dossier, con Wilson

Cada metrica trae numerador, denominador, n e intervalo de confianza. Sin
eso un 5% sobre 20 leads y un 5% sobre 2000 se leen igual en un grafico.

Wilson y no el intervalo normal: con muestras chicas o proporciones cerca
de 0 o de 1 el normal se va afuera del rango.

La fuente es obligatoria y solo puede ser meta_insights, crm o derivada.
'inferencia' no existe a proposito: las inferencias son del modelo y van
en el informe, nunca mezcladas con los hechos."
```

---

### Task 7: El dossier por campaña

**Files:**
- Modify: `services/dossier.py`
- Test: `tests/test_dossier_campanas.py`

**Interfaces:**
- Consumes: `services.embudo.alcanzo`, `services.embudo.costo`,
  `services.dossier.metrica`, `services.dossier.proporcion`, `database._connect`
- Produces: `por_campana(db_path: str, desde: str, hasta: str) -> list[dict]` —
  una entrada por campaña, cada una con `{"campana": str, "campaign_id": str|None, "metricas": list[dict]}`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_dossier_campanas.py`:

```python
"""El dossier por campana: gasto de Meta cruzado con el destino del lead.

Las etapas se cuentan con `alcanzo` sobre lead_events, igual que Finanzas. Si
este modulo contara desde crm_status, el CRM mostraria dos numeros distintos
para la misma pregunta en dos paneles.
"""

import pytest

from database import _connect, init_db
from services.dossier import por_campana


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _lead(db, nombre, campana, entro="2026-03-05", eventos=()):
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO businesses (name, phone, source, scraped_at, "
            "meta_campaign_name, meta_campaign_id) VALUES (?,?,?,?,?,?)",
            (nombre, nombre, "meta", entro, campana, "120"))
        lead_id = cur.lastrowid
        for estado in eventos:
            conn.execute("INSERT INTO lead_events (lead_id, new_status) VALUES (?,?)",
                         (lead_id, estado))
        conn.commit()
        return lead_id
    finally:
        conn.close()


def _gasto(db, fecha, spend, leads=0, impresiones=0, clicks=0):
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO meta_insights (date, campaign_id, campaign_name, spend, "
            "currency, impressions, clicks, leads) VALUES (?,?,?,?,?,?,?,?)",
            (fecha, "120", "Leads - UY - 2026", spend, "UYU", impresiones,
             clicks, leads))
        conn.commit()
    finally:
        conn.close()


def _buscar(entrada, sufijo):
    for m in entrada["metricas"]:
        if m["id"].endswith(sufijo):
            return m
    raise AssertionError(f"no hay metrica que termine en {sufijo!r}: "
                         f"{[m['id'] for m in entrada['metricas']]}")


def test_cuenta_los_leads_de_la_campana(db):
    _lead(db, "a", "Leads - UY - 2026")
    _lead(db, "b", "Leads - UY - 2026")
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".leads_crm")["valor"] == 2


def test_una_demo_se_cuenta_aunque_el_lead_hoy_este_caido(db):
    """Llego a demo y despues se cayo a no_interesa. La demo paso igual."""
    _lead(db, "a", "Leads - UY - 2026", eventos=("demo_1", "no_interesa"))
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".demos")["valor"] == 1


def test_el_vocabulario_viejo_de_lead_events_se_cuenta_igual(db):
    """lead_events mezcla las dos epocas: reunion_hecha es demo_1."""
    _lead(db, "a", "Leads - UY - 2026", eventos=("reunion_hecha",))
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".demos")["valor"] == 1


def test_llegar_a_cerrado_implica_haber_pasado_por_demo(db):
    _lead(db, "a", "Leads - UY - 2026", eventos=("cerrado",))
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".demos")["valor"] == 1
    assert _buscar(entrada, ".cierres")["valor"] == 1


def test_el_cpl_sale_del_gasto_sobre_los_leads(db):
    _lead(db, "a", "Leads - UY - 2026")
    _lead(db, "b", "Leads - UY - 2026")
    _gasto(db, "2026-03-05", 100.0)
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".cpl")["valor"] == 50.0


def test_sin_gasto_el_cpl_es_none_no_cero(db):
    """Sin campana que costear no es un costo de cero."""
    _lead(db, "a", "Leads - UY - 2026")
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".cpl")["valor"] is None


def test_sin_demos_el_costo_por_demo_es_none(db):
    """Un periodo sin demos no tiene un costo por demo de cero: no tiene."""
    _lead(db, "a", "Leads - UY - 2026")
    _gasto(db, "2026-03-05", 100.0)
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".costo_demo")["valor"] is None


def test_la_tasa_de_demo_trae_intervalo_y_marca_de_muestra(db):
    for i in range(10):
        _lead(db, f"l{i}", "Leads - UY - 2026",
              eventos=("demo_1",) if i < 2 else ())
    m = _buscar(por_campana(db, "2026-03-01", "2026-03-31")[0], ".tasa_demo")
    assert m["valor"] == 0.2
    assert m["n"] == 10
    assert m["muestra_chica"] is True
    assert m["ic95"][0] < 0.2 < m["ic95"][1]


def test_los_leads_sin_campana_van_a_su_propio_bucket(db):
    """Trece leads no traen campana. No se reparten ni se esconden."""
    _lead(db, "a", None)
    campanas = {e["campana"] for e in por_campana(db, "2026-03-01", "2026-03-31")}
    assert "(sin campaña)" in campanas


def test_hay_una_entrada_total(db):
    _lead(db, "a", "Leads - UY - 2026")
    _lead(db, "b", "Leads - ARG - 2026")
    entrada = [e for e in por_campana(db, "2026-03-01", "2026-03-31")
               if e["campana"] == "todas"][0]
    assert _buscar(entrada, ".leads_crm")["valor"] == 2


def test_el_periodo_recorta(db):
    """Un lead de febrero no cuenta en el dossier de marzo."""
    _lead(db, "a", "Leads - UY - 2026", entro="2026-02-15")
    _lead(db, "b", "Leads - UY - 2026", entro="2026-03-15")
    entrada = [e for e in por_campana(db, "2026-03-01", "2026-03-31")
               if e["campana"] == "todas"][0]
    assert _buscar(entrada, ".leads_crm")["valor"] == 1


def test_la_discrepancia_entre_meta_y_el_crm_es_una_metrica(db):
    """Si Meta dice 5 leads y el CRM tiene 2, algo se pierde en la ingesta."""
    _lead(db, "a", "Leads - UY - 2026")
    _lead(db, "b", "Leads - UY - 2026")
    _gasto(db, "2026-03-05", 100.0, leads=5)
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".leads_meta")["valor"] == 5
    assert _buscar(entrada, ".discrepancia_leads")["valor"] == 3
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_dossier_campanas.py -v`
Expected: FAIL con `ImportError: cannot import name 'por_campana'`

- [ ] **Step 3: Agregar `por_campana` a `services/dossier.py`**

```python
SIN_CAMPANA = "(sin campaña)"

# Las etapas que se reportan por campana, en orden. `contactado` no entra: en la
# cohorte de Meta el contacto se registra pintando la planilla de semaforo, no
# como evento, asi que contarlo daria siempre cero y pareceria un problema.
_ETAPAS = [
    ("interesados",  "interesado",           "Interesados"),
    ("agendadas",    "demo_agendada",        "Demos agendadas"),
    ("demos",        "demo_1",               "Demos hechas"),
    ("presupuestos", "presupuesto_enviado",  "Presupuestos enviados"),
    ("cierres",      "cerrado",              "Cierres"),
]


def _slug(texto: str) -> str:
    """Un id estable para meter en el id de la metrica."""
    import re
    import unicodedata

    limpio = unicodedata.normalize("NFKD", (texto or "").lower())
    limpio = "".join(c for c in limpio if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", limpio).strip("_") or "sin_campana"


def por_campana(db_path: str, desde: str, hasta: str) -> list:
    """Un bloque de metricas por campana, mas uno con el total.

    Las etapas se cuentan con `alcanzo` sobre lead_events —no desde
    crm_status— para dar el mismo numero que el panel de Finanzas.
    """
    from services.embudo import alcanzo, costo

    conn = _connect(db_path)
    try:
        leads = conn.execute(
            "SELECT id, meta_campaign_name, meta_campaign_id FROM businesses "
            "WHERE source = 'meta' AND substr(scraped_at, 1, 10) BETWEEN ? AND ?",
            (desde, hasta)).fetchall()
        eventos_filas = conn.execute(
            "SELECT lead_id, new_status FROM lead_events").fetchall()
        gasto_filas = conn.execute(
            "SELECT campaign_name, SUM(spend) AS spend, SUM(impressions) AS impr, "
            "       SUM(clicks) AS clicks, SUM(leads) AS leads, "
            "       MAX(currency) AS currency "
            "FROM meta_insights WHERE date BETWEEN ? AND ? GROUP BY campaign_name",
            (desde, hasta)).fetchall()
    finally:
        conn.close()

    eventos = {}
    for fila in eventos_filas:
        eventos.setdefault(fila["lead_id"], set()).add(fila["new_status"])

    gasto = {f["campaign_name"]: f for f in gasto_filas}

    grupos = {}
    for lead in leads:
        clave = lead["meta_campaign_name"] or SIN_CAMPANA
        grupos.setdefault(clave, []).append(lead)

    bloques = []
    for campana in sorted(grupos) + ["todas"]:
        if campana == "todas":
            del_grupo = leads
            g = {k: sum(float(f[k] or 0) for f in gasto_filas)
                 for k in ("spend", "impr", "clicks", "leads")}
            moneda = gasto_filas[0]["currency"] if gasto_filas else None
            campaign_id = None
        else:
            del_grupo = grupos[campana]
            fila = gasto.get(campana)
            g = {"spend": float(fila["spend"] or 0) if fila else 0.0,
                 "impr": float(fila["impr"] or 0) if fila else 0.0,
                 "clicks": float(fila["clicks"] or 0) if fila else 0.0,
                 "leads": float(fila["leads"] or 0) if fila else 0.0}
            moneda = fila["currency"] if fila else None
            campaign_id = del_grupo[0]["meta_campaign_id"] if del_grupo else None

        pref = f"campana.{_slug(campana)}"
        n = len(del_grupo)
        conteo = {}
        for clave, etapa, _ in _ETAPAS:
            conteo[clave] = sum(1 for l in del_grupo
                                if alcanzo(eventos.get(l["id"], set()), etapa))

        ms = [
            metrica(f"{pref}.gasto", f"Gasto — {campana}", round(g["spend"], 2),
                    "meta_insights", formato="moneda"),
            metrica(f"{pref}.impresiones", f"Impresiones — {campana}",
                    int(g["impr"]), "meta_insights"),
            metrica(f"{pref}.clics", f"Clics — {campana}", int(g["clicks"]),
                    "meta_insights"),
            metrica(f"{pref}.leads_meta", f"Leads según Meta — {campana}",
                    int(g["leads"]), "meta_insights"),
            metrica(f"{pref}.leads_crm", f"Leads en el CRM — {campana}", n, "crm"),
            # Si Meta dice 5 y el CRM tiene 2, algo se pierde en la ingesta.
            metrica(f"{pref}.discrepancia_leads",
                    f"Leads que Meta reporta y el CRM no tiene — {campana}",
                    int(g["leads"]) - n, "derivada"),
            metrica(f"{pref}.cpl", f"Costo por lead — {campana}",
                    costo(g["spend"], n), "derivada", formato="moneda"),
            metrica(f"{pref}.costo_demo", f"Costo por demo — {campana}",
                    costo(g["spend"], conteo["demos"]), "derivada", formato="moneda"),
            metrica(f"{pref}.costo_presupuesto",
                    f"Costo por presupuesto — {campana}",
                    costo(g["spend"], conteo["presupuestos"]), "derivada",
                    formato="moneda"),
            proporcion(f"{pref}.ctr", f"CTR — {campana}",
                       int(g["clicks"]), int(g["impr"]), "derivada"),
        ]
        for clave, _etapa, etiqueta in _ETAPAS:
            ms.append(metrica(f"{pref}.{clave}", f"{etiqueta} — {campana}",
                              conteo[clave], "crm"))
            ms.append(proporcion(f"{pref}.tasa_{clave.rstrip('s')}",
                                 f"Tasa de {etiqueta.lower()} — {campana}",
                                 conteo[clave], n, "crm"))

        bloques.append({"campana": campana, "campaign_id": campaign_id,
                        "moneda": moneda, "metricas": ms})
    return bloques
```

Agregar arriba del archivo, junto a los otros imports:

```python
from database import _connect
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_dossier_campanas.py -v`
Expected: PASS, 12 tests

- [ ] **Step 5: Verificar que los ids de métrica son únicos**

Dos métricas con el mismo id romperían la citación del informe en la Fase 3.

```bash
python -c "
from database import init_db
from services.dossier import por_campana
db = r'C:/Users/juant/AppData/Local/Temp/ids.db'
init_db(db)
ids = [m['id'] for b in por_campana(db,'2026-01-01','2026-12-31') for m in b['metricas']]
assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]
print('ok, ids unicos')
"
```

Expected: `ok, ids unicos`

- [ ] **Step 6: Correr el dossier contra el backup real**

> **Ojo con `/tmp`.** En Git Bash `/tmp` es `%LOCALAPPDATA%\Temp`, pero Python en
> Windows lee `/tmp/x.db` como `C:\tmp\x.db`. Copiar con `cp` a `/tmp` y abrir esa
> ruta desde Python crea una base vacia en otro lado, y el paso "verificar contra
> datos reales" pasa en verde sin haber leido nada. Usar una ruta absoluta de
> Windows, o el scratchpad de la sesion, en `$SCRATCH`.

```bash
SCRATCH="C:/Users/juant/AppData/Local/Temp"
cp backups/leads_pre_preclientes_8sep.db "$SCRATCH/dossier_prueba.db"
python -c "
from database import init_db
from services.meta_campanas import backfill_campanas
from services.dossier import por_campana
db = r'$SCRATCH/dossier_prueba.db'
init_db(db)
backfill_campanas(db)
for b in por_campana(db,'2026-03-01','2026-09-30'):
    leads = [m for m in b['metricas'] if m['id'].endswith('.leads_crm')][0]['valor']
    demos = [m for m in b['metricas'] if m['id'].endswith('.demos')][0]['valor']
    print(f\"{b['campana']:28s} leads={leads:4d} demos={demos:3d}\")
"
```

Expected: los totales cuadran con el spec §4 — 238 leads en total. Sin gasto
cargado, todos los CPL van a dar `None`, que es correcto.

- [ ] **Step 7: Commit**

```bash
git add services/dossier.py tests/test_dossier_campanas.py
git commit -m "feat(marketing): dossier por campana

Cruza el gasto de Meta con lo que paso despues del clic. Las etapas se
cuentan con alcanzo() sobre lead_events, igual que Finanzas: contarlas
desde crm_status daria dos numeros distintos para la misma pregunta en
dos paneles del mismo CRM.

Incluye la discrepancia entre los leads que Meta reporta y los que el CRM
tiene. Si no cuadran, algo se pierde en la ingesta, y eso vale como
hallazgo propio.

Los 13 leads sin campana van a su bucket, no se reparten ni se esconden."
```

---

### Task 8: Segmentos declarados en el formulario

La variable más valiosa y la que nadie usa. Requiere normalizar dos formularios
distintos y claves con el encoding roto.

**Files:**
- Modify: `services/dossier.py`
- Test: `tests/test_dossier_segmentos.py`

**Interfaces:**
- Consumes: `services.embudo.alcanzo`, y de la Task 7: `services.dossier.proporcion`,
  `_ETAPAS`, `_slug`
- Produces:
  - `normalizar_clave(clave: str) -> str`
  - `PREGUNTAS: dict[str, tuple[str, ...]]` — fragmentos sin acentos
  - `PREGUNTAS_EXACTAS: dict[str, tuple[str, ...]]`
  - `por_segmento(db_path: str, desde: str, hasta: str) -> list[dict]`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_dossier_segmentos.py`:

```python
"""Los segmentos que el lead declara en el formulario de Meta.

Es la mejor variable de segmentacion que hay y ningun reporte la usa. Tiene dos
trampas medidas en los datos reales: las claves llegan con el encoding roto, y
hay dos versiones del formulario con preguntas distintas.
"""

import json

import pytest

from database import _connect, init_db
from services.dossier import normalizar_clave, por_segmento


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _lead(db, nombre, form, eventos=(), entro="2026-03-05"):
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO businesses (name, phone, source, scraped_at, form_data) "
            "VALUES (?,?,?,?,?)",
            (nombre, nombre, "meta", entro, json.dumps(form)))
        lead_id = cur.lastrowid
        for estado in eventos:
            conn.execute("INSERT INTO lead_events (lead_id, new_status) VALUES (?,?)",
                         (lead_id, estado))
        conn.commit()
    finally:
        conn.close()


def test_normalizar_saca_acentos_signos_y_mayusculas():
    assert (normalizar_clave("¿Qué_es_lo_que_buscás_para_tu_negocio?")
            == normalizar_clave("que_es_lo_que_buscas_para_tu_negocio"))


def test_normalizar_aguanta_el_encoding_roto():
    """Las claves reales llegan asi de la base."""
    roto = "�que_es_lo_que_busc�s_para_tu_negocio?"
    assert normalizar_clave(roto).endswith("para_tu_negocio")


def test_agrupa_por_lo_que_busca(db):
    _lead(db, "a", {"¿que_es_lo_que_buscás_para_tu_negocio?": "automatizaciones"})
    _lead(db, "b", {"¿que_es_lo_que_buscás_para_tu_negocio?": "automatizaciones"})
    _lead(db, "c", {"¿que_es_lo_que_buscás_para_tu_negocio?": "crear_mi_ecommerce"})

    bloque = [b for b in por_segmento(db, "2026-03-01", "2026-03-31")
              if b["pregunta"] == "que_busca"][0]
    valores = {v["valor_declarado"]: v for v in bloque["valores"]}
    assert valores["automatizaciones"]["metricas"][0]["denominador"] == 2
    assert valores["crear_mi_ecommerce"]["metricas"][0]["denominador"] == 1


def test_la_tasa_de_demo_por_presupuesto_declarado(db):
    """La pregunta del modulo: ¿los que declaran mas plata avanzan mas?"""
    _lead(db, "rico", {"¿contás_con_un_presupuesto_para_este_proyecto?":
                       "más_de_usd_1.000"}, eventos=("demo_1",))
    _lead(db, "duda", {"¿contás_con_un_presupuesto_para_este_proyecto?":
                       "aún_no_lo_se"})

    bloque = [b for b in por_segmento(db, "2026-03-01", "2026-03-31")
              if b["pregunta"] == "presupuesto"][0]
    valores = {v["valor_declarado"]: v for v in bloque["valores"]}
    tasa = [m for m in valores["más_de_usd_1.000"]["metricas"]
            if m["id"].endswith(".tasa_demo")][0]
    assert tasa["valor"] == 1.0
    assert tasa["muestra_chica"] is True


def test_las_dos_versiones_del_formulario_no_se_mezclan(db):
    """191 leads contestan un juego de preguntas y 47 contestan otro. Dos
    preguntas distintas nunca van bajo la misma etiqueta."""
    _lead(db, "nuevo", {"¿cuál_es_tu_objetivo_para_este_año?": "crecer"})
    _lead(db, "viejo", {"¿cuál_es_el_objetivo_que_tenes_en_este_2026?": "crecer"})

    bloques = {b["pregunta"]: b for b in por_segmento(db, "2026-03-01", "2026-03-31")}
    assert "objetivo" in bloques
    assert "objetivo_v2" in bloques
    assert bloques["objetivo"]["n"] == 1
    assert bloques["objetivo_v2"]["n"] == 1


def test_un_form_data_roto_no_rompe_el_dossier(db):
    conn = _connect(db)
    try:
        conn.execute("INSERT INTO businesses (name, phone, source, scraped_at, "
                     "form_data) VALUES ('x','x','meta','2026-03-05','no es json')")
        conn.commit()
    finally:
        conn.close()
    assert por_segmento(db, "2026-03-01", "2026-03-31") == []


def test_un_lead_sin_form_data_no_cuenta(db):
    _lead(db, "a", {})
    assert por_segmento(db, "2026-03-01", "2026-03-31") == []
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_dossier_segmentos.py -v`
Expected: FAIL con `ImportError: cannot import name 'normalizar_clave'`

- [ ] **Step 3: Agregar la normalización y `por_segmento` a `services/dossier.py`**

```python
# Las preguntas del formulario de Meta, normalizadas. Hay dos versiones del
# form conviviendo: 191 leads contestan una y 47 la otra. Las preguntas
# equivalentes se unifican; las que no tienen equivalente van por separado con
# su propio `n`. Dos preguntas distintas NUNCA van bajo la misma etiqueta: eso
# seria sumar peras con manzanas y no se notaria en el grafico.
# Los fragmentos son a proposito trozos SIN letras acentuadas. La clave real
# llega con el encoding roto —`�que_es_lo_que_busc�s_para_tu_negocio?`—
# y cualquier caracter roto se normaliza a "_", asi que comparar contra la
# pregunta entera falla justo con los datos de produccion. Se compara por
# fragmento contenido, no por igualdad.
PREGUNTAS = {
    "que_busca":    ("para_tu_negocio",),
    "presupuesto":  ("presupuesto_para_este_proyecto",),
    "objetivo":     ("tu_objetivo_para_este",),
    "objetivo_v2":  ("objetivo_que_tenes_en_este_2026",),
    "establecido":  ("establecido_o_es_un_proyecto_a_lanzar",),
}

# `city` va aparte: es tan corto que como fragmento matchearia cualquier clave
# que lo contenga.
PREGUNTAS_EXACTAS = {"ciudad": ("city",)}

_ETIQUETAS_PREGUNTA = {
    "que_busca":   "Qué busca para su negocio",
    "presupuesto": "Presupuesto declarado",
    "objetivo":    "Objetivo del año",
    "objetivo_v2": "Objetivo del año (formulario viejo)",
    "establecido": "Negocio establecido o a lanzar",
    "ciudad":      "Ciudad",
}


def normalizar_clave(clave: str) -> str:
    """Una clave de form_data comparable.

    Las claves reales vienen con el encoding roto: la base guarda cosas como
    `\\ufffdque_es_lo_que_busc\\ufffds_para_tu_negocio?`. Depender de la clave
    literal no funciona. Se baja a minusculas, se sacan acentos y se colapsa
    todo lo que no sea alfanumerico.
    """
    import re
    import unicodedata

    limpio = unicodedata.normalize("NFKD", (clave or "").lower())
    limpio = "".join(c for c in limpio if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", limpio).strip("_")


def _pregunta_de(clave_normalizada: str):
    """A que pregunta del mapa corresponde una clave, o None.

    Primero las exactas, despues por fragmento contenido. El orden importa:
    `objetivo_v2` tiene que probarse antes que `objetivo` para que la pregunta
    del formulario viejo no caiga en el bucket del nuevo.
    """
    for pregunta, claves in PREGUNTAS_EXACTAS.items():
        if clave_normalizada in claves:
            return pregunta
    for pregunta in ("objetivo_v2", "que_busca", "presupuesto", "objetivo",
                     "establecido"):
        for fragmento in PREGUNTAS[pregunta]:
            if fragmento in clave_normalizada:
                return pregunta
    return None


def por_segmento(db_path: str, desde: str, hasta: str) -> list:
    """Tasas de avance por lo que el lead declaro en el formulario.

    Contesta la pregunta que ningun reporte contesta hoy: los que declararon
    mas de USD 1.000, ¿avanzan mas que los que dijeron "aun no lo se"?
    """
    import json as _json

    from services.embudo import alcanzo

    conn = _connect(db_path)
    try:
        leads = conn.execute(
            "SELECT id, form_data FROM businesses WHERE source = 'meta' "
            "AND substr(scraped_at, 1, 10) BETWEEN ? AND ? "
            "AND form_data IS NOT NULL AND form_data != ''",
            (desde, hasta)).fetchall()
        eventos_filas = conn.execute(
            "SELECT lead_id, new_status FROM lead_events").fetchall()
    finally:
        conn.close()

    eventos = {}
    for fila in eventos_filas:
        eventos.setdefault(fila["lead_id"], set()).add(fila["new_status"])

    # {pregunta: {valor_declarado: [lead_id, ...]}}
    grupos = {}
    for lead in leads:
        try:
            datos = _json.loads(lead["form_data"])
        except (ValueError, TypeError):
            continue          # un form_data roto no puede tumbar el dossier
        if not isinstance(datos, dict):
            continue
        for clave, valor in datos.items():
            pregunta = _pregunta_de(normalizar_clave(clave))
            if not pregunta:
                continue
            valor = str(valor).strip()
            if not valor:
                continue
            grupos.setdefault(pregunta, {}).setdefault(valor, []).append(lead["id"])

    bloques = []
    for pregunta in sorted(grupos):
        valores = []
        for declarado, ids in sorted(grupos[pregunta].items(),
                                     key=lambda kv: -len(kv[1])):
            n = len(ids)
            pref = f"segmento.{pregunta}.{_slug(declarado)}"
            ms = []
            for clave, etapa, etiqueta in _ETAPAS:
                exitos = sum(1 for i in ids if alcanzo(eventos.get(i, set()), etapa))
                ms.append(proporcion(
                    f"{pref}.tasa_{clave.rstrip('s')}",
                    f"Tasa de {etiqueta.lower()} — {declarado}",
                    exitos, n, "crm"))
            valores.append({"valor_declarado": declarado, "n": n, "metricas": ms})
        bloques.append({
            "pregunta": pregunta,
            "etiqueta": _ETIQUETAS_PREGUNTA.get(pregunta, pregunta),
            "n": sum(v["n"] for v in valores),
            "valores": valores,
        })
    return bloques
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_dossier_segmentos.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Verificar contra los datos reales**

Este paso es el que confirma que la normalización agarra las claves de verdad.

```bash
python -c "
from services.dossier import por_segmento
db = r'C:/Users/juant/AppData/Local/Temp/dossier_prueba.db'
for b in por_segmento(db,'2026-03-01','2026-09-30'):
    print(f\"{b['etiqueta']} (n={b['n']})\")
    for v in b['valores'][:5]:
        print(f\"   {v['n']:4d}  {v['valor_declarado']}\")
"
```

Expected: aparecen las cuatro preguntas del spec §4 con sus distribuciones —
"Qué busca" con n=191 (automatizaciones 55, web 48, software 44, ecommerce 43) y
"Presupuesto declarado" con n=191 (aún no lo sé 104, menos de 500 → 50, 500-1000
→ 19, más de 1000 → 17). **Si algún bloque sale con n=0, la normalización no
agarró esa clave**: imprimir las claves crudas y agregar el sinónimo a
`PREGUNTAS` antes de seguir.

- [ ] **Step 6: Commit**

```bash
git add services/dossier.py tests/test_dossier_segmentos.py
git commit -m "feat(marketing): segmentar por lo que el lead declara en el form

El formulario de Meta guarda que busca, cuanto presupuesto tiene y cual es
su objetivo —191 respuestas— y ningun reporte lo usaba. Es la mejor
variable de segmentacion que hay y ya estaba en la base.

Dos trampas medidas en los datos: las claves vienen con el encoding roto,
asi que se comparan normalizadas; y hay dos versiones del formulario, asi
que las preguntas sin equivalente van por separado con su propio n. Dos
preguntas distintas nunca van bajo la misma etiqueta."
```

---

### Task 9: Los endpoints

**Files:**
- Create: `routes/marketing.py`
- Modify: `dashboard.py` — el import de blueprints (líneas 12-21) y la tupla de
  `create_app` (línea 7584)
- Test: `tests/test_marketing_rutas.py`

**Interfaces:**
- Consumes: `services.dossier.por_campana`, `services.dossier.por_segmento`,
  `services.meta_insights.sincronizar`, `services.meta_campanas.backfill_campanas`
- Produces: blueprint `marketing_bp` con `GET /api/marketing/dossier`,
  `POST /api/marketing/sync-insights`, `POST /api/marketing/backfill-campanas`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_marketing_rutas.py`:

```python
"""Las rutas del modulo de marketing.

Los GET piden sesion porque el panel muestra gasto publicitario. Los POST van
por x-admin-token, igual que /api/linkedin/generar, porque los llama el cron.
"""

import pytest

from dashboard import create_app
from database import _connect, init_db


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-prueba")
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    aplicacion = create_app(ruta)
    aplicacion.config["TESTING"] = True
    return aplicacion


@pytest.fixture
def cliente(app):
    return app.test_client()


def _lead(app, nombre, campana="Leads - UY - 2026"):
    conn = _connect(app.config["DB_PATH"])
    try:
        conn.execute("INSERT INTO businesses (name, phone, source, scraped_at, "
                     "meta_campaign_name) VALUES (?,?,?,?,?)",
                     (nombre, nombre, "meta", "2026-03-05", campana))
        conn.commit()
    finally:
        conn.close()


def test_el_dossier_sin_sesion_no_se_puede_ver(cliente):
    """Muestra gasto publicitario: no es publico."""
    r = cliente.get("/api/marketing/dossier")
    assert r.status_code in (302, 401, 403)


def test_el_dossier_con_admin_token_contesta(app, cliente):
    _lead(app, "a")
    r = cliente.get("/api/marketing/dossier?desde=2026-03-01&hasta=2026-03-31",
                    headers={"x-admin-token": "token-de-prueba"})
    assert r.status_code == 200
    datos = r.get_json()
    assert "campanas" in datos
    assert "segmentos" in datos
    assert datos["periodo"] == {"desde": "2026-03-01", "hasta": "2026-03-31"}


def test_el_dossier_tiene_un_periodo_por_defecto(app, cliente):
    """Sin parametros, los ultimos 90 dias."""
    r = cliente.get("/api/marketing/dossier",
                    headers={"x-admin-token": "token-de-prueba"})
    assert r.status_code == 200
    assert r.get_json()["periodo"]["desde"]


def test_una_fecha_invalida_da_400_y_no_500(app, cliente):
    r = cliente.get("/api/marketing/dossier?desde=ayer&hasta=hoy",
                    headers={"x-admin-token": "token-de-prueba"})
    assert r.status_code == 400


def test_sync_insights_sin_token_no_corre(cliente):
    r = cliente.post("/api/marketing/sync-insights")
    assert r.status_code in (302, 401, 403)


def test_sync_insights_sin_credenciales_avisa_y_no_rompe(app, cliente):
    r = cliente.post("/api/marketing/sync-insights",
                     headers={"x-admin-token": "token-de-prueba"})
    assert r.status_code == 200
    assert r.get_json()["salteado"] == "sin_credenciales"


def test_backfill_de_campanas_por_ruta(app, cliente):
    conn = _connect(app.config["DB_PATH"])
    try:
        conn.execute("INSERT INTO businesses (name, phone, source, notes) "
                     "VALUES ('a','a','meta','Meta Lead Ad · Leads - UY - 2026')")
        conn.commit()
    finally:
        conn.close()
    r = cliente.post("/api/marketing/backfill-campanas",
                     headers={"x-admin-token": "token-de-prueba"})
    assert r.status_code == 200
    assert r.get_json()["escritos"] == 1


def test_el_dossier_es_json_serializable(app, cliente):
    """Un Decimal o un sqlite3.Row adentro rompe el jsonify en produccion."""
    _lead(app, "a")
    r = cliente.get("/api/marketing/dossier",
                    headers={"x-admin-token": "token-de-prueba"})
    assert r.status_code == 200
    assert r.is_json
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_marketing_rutas.py -v`
Expected: FAIL con `ImportError: cannot import name 'marketing_bp'`

- [ ] **Step 3: Escribir `routes/marketing.py`**

```python
"""Las rutas del modulo de inteligencia comercial sobre Meta Ads.

Los GET piden sesion: el panel muestra cuanto se gasta en publicidad. Los POST
van por x-admin-token porque los llama el cron de GitHub Actions, igual que
/api/linkedin/generar.
"""

import logging
import os
import re
from datetime import date, timedelta

from flask import Blueprint, current_app, jsonify, request

from services.auth import require_panel

logger = logging.getLogger(__name__)

marketing_bp = Blueprint("marketing", __name__)

_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Sin parametros, la ventana por defecto. Tres meses dan varias campanas y
# muestras usables sin que el dossier se vaya de tamano.
DIAS_POR_DEFECTO = 90


def _db() -> str:
    return current_app.config["DB_PATH"]


def _token_admin_ok() -> bool:
    esperado = os.environ.get("ADMIN_TOKEN", "")
    return bool(esperado) and request.headers.get("x-admin-token", "") == esperado


@marketing_bp.before_request
def _candado():
    """Una linea, cubre todo el blueprint.

    Con un decorador por ruta, agregar un endpoint el mes que viene y olvidarse
    del candado deja el gasto publicitario abierto a cualquier usuario del CRM.

    El token pasa sin sesion porque los POST los llama el cron de GitHub
    Actions, que no tiene con que loguearse. El `require_login` global de
    `create_app` ya valido ese mismo token antes de llegar aca; esto agrega el
    permiso de panel para las personas.
    """
    if _token_admin_ok():
        return None
    return require_panel(_db(), "marketing")


def _periodo():
    """(desde, hasta) validados, o (None, None) si vinieron mal."""
    hasta = request.args.get("hasta") or date.today().isoformat()
    desde = (request.args.get("desde")
             or (date.today() - timedelta(days=DIAS_POR_DEFECTO)).isoformat())
    if not _FECHA.match(desde) or not _FECHA.match(hasta):
        return None, None
    return desde, hasta


@marketing_bp.route("/api/marketing/dossier")
def api_dossier():
    from services.dossier import por_campana, por_segmento

    desde, hasta = _periodo()
    if not desde:
        return jsonify({"error": "fechas invalidas, se espera YYYY-MM-DD"}), 400

    return jsonify({
        "periodo": {"desde": desde, "hasta": hasta},
        "campanas": por_campana(_db(), desde, hasta),
        "segmentos": por_segmento(_db(), desde, hasta),
    })


@marketing_bp.route("/api/marketing/sync-insights", methods=["POST"])
def api_sync_insights():
    from services.meta_insights import DIAS_A_RESINCRONIZAR, sincronizar

    hasta = date.today()
    desde = hasta - timedelta(days=int(request.args.get("dias")
                                       or DIAS_A_RESINCRONIZAR))
    return jsonify(sincronizar(_db(), desde.isoformat(), hasta.isoformat()))


@marketing_bp.route("/api/marketing/backfill-campanas", methods=["POST"])
def api_backfill_campanas():
    from services.meta_campanas import backfill_campanas

    return jsonify(backfill_campanas(_db()))
```

- [ ] **Step 4: Registrar el blueprint en `dashboard.py`**

Junto a los otros imports de blueprints (líneas 12-21):

```python
from routes.marketing import marketing_bp
```

Y agregarlo a la tupla de `create_app` (línea ~7584), al final:

```python
    for bp in (leads_bp, demos_bp, calendar_bp, wa_bp, pipeline_bp, tasks_bp, budgets_bp, tokens_bp, meta_bp, calendly_bp, notion_bp, projects_bp, preclientes_bp,
                notion_clients_bp, resend_bp, linkedin_bp, web_bp, finanzas_bp, marketing_bp):
```

- [ ] **Step 5: Correr los tests**

Run: `python -m pytest tests/test_marketing_rutas.py -v`
Expected: PASS, 8 tests

- [ ] **Step 6: Correr la suite entera**

Run: `python -m pytest tests/ -q`
Expected: PASS. Es la última verificación de que nada de esto rompió otro módulo.

- [ ] **Step 7: Verificar que la fase no importa `anthropic`**

Run: `grep -rn "anthropic" services/dossier.py services/meta_insights.py services/meta_campanas.py services/embudo.py routes/marketing.py`
Expected: sin resultados. Esta fase no gasta un token.

- [ ] **Step 8: Commit**

```bash
git add routes/marketing.py dashboard.py tests/test_marketing_rutas.py
git commit -m "feat(marketing): endpoints del dossier

GET /api/marketing/dossier devuelve las metricas por campana y por
segmento declarado. Los POST de sync y backfill van por x-admin-token
porque los llama el cron.

Cierra la fase 1: el CRM ya sabe cuanto costo cada campana y que paso con
cada lead que trajo. Sin UI y sin IA todavia."
```

---

### Task 10: Serie semanal y conciliación del gasto

Dos cosas que el panel de la Fase 2 necesita sí o sí. La serie va **semanal**:
238 leads en 178 días son 1,3 por día, y un gráfico diario sería picos al
infinito con días en cero.

**Files:**
- Modify: `services/dossier.py`
- Test: `tests/test_dossier_series.py`

**Interfaces:**
- Consumes: `database._connect`, `services.dossier.metrica`,
  `services.embudo.costo`
- Produces:
  - `serie_semanal(db_path: str, desde: str, hasta: str) -> list[dict]` — una
    entrada por semana con las claves `semana`, `inicio`, `gasto`,
    `impresiones`, `clics`, `leads_meta`, `leads_crm`, `cpl`
  - `conciliacion(db_path: str, desde: str, hasta: str) -> list[dict]` — tres
    métricas por mes: `gasto_meta`, `gasto_cargado`, `brecha`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_dossier_series.py`:

```python
"""La serie semanal y la conciliacion del gasto.

Semanal y no diaria: 238 leads en 178 dias son 1,3 por dia. En dias solo se ve
ruido; la senal aparece recien por semana.
"""

import pytest

from database import _connect, init_db
from services.dossier import conciliacion, serie_semanal


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _gasto(db, fecha, spend, leads=0, impresiones=1000, clicks=40):
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO meta_insights (date, campaign_id, campaign_name, spend, "
            "currency, impressions, clicks, reach, leads) VALUES (?,?,?,?,?,?,?,?,?)",
            (fecha, "120", "Leads - UY - 2026", spend, "UYU", impresiones,
             clicks, 900, leads))
        conn.commit()
    finally:
        conn.close()


def _lead(db, nombre, entro):
    conn = _connect(db)
    try:
        conn.execute("INSERT INTO businesses (name, phone, source, scraped_at) "
                     "VALUES (?,?,?,?)", (nombre, nombre, "meta", entro))
        conn.commit()
    finally:
        conn.close()


def _movimiento(db, fecha, monto):
    """Un egreso de publicidad cargado a mano en Finanzas.

    `periodo` y `concepto` son NOT NULL en esa tabla: omitirlos da IntegrityError.
    """
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO finanzas_movimientos (tipo, fecha, periodo, concepto, "
            "categoria, monto, moneda, monto_usd, anulado) "
            "VALUES ('egreso',?,?,'Pauta Meta','publicidad',?,'USD',?,0)",
            (fecha, fecha[:7], monto, monto))
        conn.commit()
    finally:
        conn.close()


def test_agrupa_los_dias_de_la_misma_semana(db):
    _gasto(db, "2026-03-02", 10.0)   # lunes
    _gasto(db, "2026-03-04", 15.0)   # miercoles
    semanas = serie_semanal(db, "2026-03-01", "2026-03-31")
    primera = [s for s in semanas if s["inicio"] == "2026-03-02"][0]
    assert primera["gasto"] == 25.0


def test_separa_semanas_distintas(db):
    _gasto(db, "2026-03-02", 10.0)
    _gasto(db, "2026-03-09", 20.0)
    inicios = {s["inicio"] for s in serie_semanal(db, "2026-03-01", "2026-03-31")}
    assert {"2026-03-02", "2026-03-09"} <= inicios


def test_la_semana_arranca_en_lunes(db):
    """Un domingo cae en la semana que empezo el lunes anterior."""
    _gasto(db, "2026-03-08", 10.0)   # domingo
    semanas = serie_semanal(db, "2026-03-01", "2026-03-31")
    assert [s for s in semanas if s["gasto"] == 10.0][0]["inicio"] == "2026-03-02"


def test_cuenta_los_leads_del_crm_en_su_semana(db):
    _lead(db, "a", "2026-03-03 10:00:00")
    _lead(db, "b", "2026-03-05 10:00:00")
    _lead(db, "c", "2026-03-10 10:00:00")
    semanas = {s["inicio"]: s for s in serie_semanal(db, "2026-03-01", "2026-03-31")}
    assert semanas["2026-03-02"]["leads_crm"] == 2
    assert semanas["2026-03-09"]["leads_crm"] == 1


def test_el_cpl_semanal_sale_del_gasto_sobre_los_leads_del_crm(db):
    _gasto(db, "2026-03-02", 100.0)
    _lead(db, "a", "2026-03-03 10:00:00")
    _lead(db, "b", "2026-03-04 10:00:00")
    semanas = {s["inicio"]: s for s in serie_semanal(db, "2026-03-01", "2026-03-31")}
    assert semanas["2026-03-02"]["cpl"] == 50.0


def test_una_semana_con_gasto_y_sin_leads_no_tiene_cpl(db):
    _gasto(db, "2026-03-02", 100.0)
    semanas = {s["inicio"]: s for s in serie_semanal(db, "2026-03-01", "2026-03-31")}
    assert semanas["2026-03-02"]["cpl"] is None


def test_las_semanas_salen_ordenadas(db):
    _gasto(db, "2026-03-16", 10.0)
    _gasto(db, "2026-03-02", 10.0)
    inicios = [s["inicio"] for s in serie_semanal(db, "2026-03-01", "2026-03-31")]
    assert inicios == sorted(inicios)


def test_sin_datos_la_serie_es_vacia(db):
    assert serie_semanal(db, "2026-03-01", "2026-03-31") == []


def test_la_conciliacion_compara_meta_contra_lo_cargado_a_mano(db):
    _gasto(db, "2026-03-05", 300.0)
    _movimiento(db, "2026-03-31", 250.0)
    m = {x["id"]: x for x in conciliacion(db, "2026-03-01", "2026-03-31")}
    assert m["conciliacion.gasto_meta.2026_03"]["valor"] == 300.0
    assert m["conciliacion.gasto_cargado.2026_03"]["valor"] == 250.0
    assert m["conciliacion.brecha.2026_03"]["valor"] == 50.0


def test_la_conciliacion_sin_gasto_cargado_marca_todo_como_brecha(db):
    """Si Meta cobro y nadie lo registro, la brecha es el gasto entero."""
    _gasto(db, "2026-03-05", 300.0)
    m = {x["id"]: x for x in conciliacion(db, "2026-03-01", "2026-03-31")}
    assert m["conciliacion.brecha.2026_03"]["valor"] == 300.0
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_dossier_series.py -v`
Expected: FAIL con `ImportError: cannot import name 'serie_semanal'`

- [ ] **Step 3: Agregar las dos funciones a `services/dossier.py`**

```python
def _lunes_de(iso_fecha: str) -> str:
    """El lunes de la semana a la que pertenece esa fecha."""
    from datetime import date, timedelta

    y, m, d = (int(x) for x in iso_fecha[:10].split("-"))
    dia = date(y, m, d)
    return (dia - timedelta(days=dia.weekday())).isoformat()


def serie_semanal(db_path: str, desde: str, hasta: str) -> list:
    """Gasto, alcance y leads por semana.

    Semanal y no diaria a proposito: 238 leads en 178 dias son 1,3 por dia. En
    grano diario el grafico son picos y ceros; la senal aparece por semana.
    """
    from datetime import date as _date

    from services.embudo import costo

    conn = _connect(db_path)
    try:
        gasto_filas = conn.execute(
            "SELECT date, spend, impressions, clicks, leads FROM meta_insights "
            "WHERE date BETWEEN ? AND ?", (desde, hasta)).fetchall()
        lead_filas = conn.execute(
            "SELECT scraped_at FROM businesses WHERE source = 'meta' "
            "AND substr(scraped_at, 1, 10) BETWEEN ? AND ?",
            (desde, hasta)).fetchall()
    finally:
        conn.close()

    semanas = {}

    def _slot(inicio):
        return semanas.setdefault(inicio, {
            "semana": None, "inicio": inicio, "gasto": 0.0, "impresiones": 0,
            "clics": 0, "leads_meta": 0, "leads_crm": 0, "cpl": None})

    for fila in gasto_filas:
        s = _slot(_lunes_de(fila["date"]))
        s["gasto"] += float(fila["spend"] or 0)
        s["impresiones"] += int(fila["impressions"] or 0)
        s["clics"] += int(fila["clicks"] or 0)
        s["leads_meta"] += int(fila["leads"] or 0)

    for fila in lead_filas:
        _slot(_lunes_de(fila["scraped_at"]))["leads_crm"] += 1

    salida = []
    for inicio in sorted(semanas):
        s = semanas[inicio]
        s["gasto"] = round(s["gasto"], 2)
        s["cpl"] = costo(s["gasto"], s["leads_crm"])
        y, m, d = (int(x) for x in inicio.split("-"))
        s["semana"] = "%d-W%02d" % _date(y, m, d).isocalendar()[:2]
        salida.append(s)
    return salida


def conciliacion(db_path: str, desde: str, hasta: str) -> list:
    """Lo que Meta cobro contra lo que se cargo a mano en Finanzas.

    No se unifican y no se pisan: cada uno guarda lo suyo y aca se muestra la
    brecha. Vale por si sola — dice si se esta registrando en la contabilidad
    todo lo que Meta efectivamente cobro.

    Este modulo nunca escribe en finanzas_movimientos.
    """
    import sqlite3

    conn = _connect(db_path)
    try:
        meta_filas = conn.execute(
            "SELECT substr(date, 1, 7) AS periodo, SUM(spend) AS total "
            "FROM meta_insights WHERE date BETWEEN ? AND ? GROUP BY periodo",
            (desde, hasta)).fetchall()
        try:
            cargado_filas = conn.execute(
                # `periodo` es una columna propia de la tabla, no hay que
                # recortarla de la fecha: Finanzas la escribe al crear el
                # movimiento y es la que usa para sus propios cortes.
                "SELECT periodo, SUM(monto_usd) AS total "
                "FROM finanzas_movimientos WHERE tipo = 'egreso' "
                "AND categoria = 'publicidad' AND anulado = 0 "
                "AND fecha BETWEEN ? AND ? GROUP BY periodo",
                (desde, hasta)).fetchall()
        except sqlite3.Error:
            # Finanzas es de otra sesion. Si su tabla no esta, la conciliacion
            # se degrada a "todo es brecha" en vez de tumbar el dossier entero.
            cargado_filas = []
    finally:
        conn.close()

    meta = {f["periodo"]: float(f["total"] or 0) for f in meta_filas}
    cargado = {f["periodo"]: float(f["total"] or 0) for f in cargado_filas}

    ms = []
    for periodo in sorted(set(meta) | set(cargado)):
        suf = periodo.replace("-", "_")
        m_val = round(meta.get(periodo, 0.0), 2)
        c_val = round(cargado.get(periodo, 0.0), 2)
        ms.append(metrica(f"conciliacion.gasto_meta.{suf}",
                          f"Gasto según Meta — {periodo}", m_val,
                          "meta_insights", formato="moneda"))
        ms.append(metrica(f"conciliacion.gasto_cargado.{suf}",
                          f"Gasto cargado en Finanzas — {periodo}", c_val,
                          "crm", formato="moneda"))
        ms.append(metrica(f"conciliacion.brecha.{suf}",
                          f"Gasto de Meta sin registrar — {periodo}",
                          round(m_val - c_val, 2), "derivada", formato="moneda"))
    return ms
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_dossier_series.py -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Sumarlas al endpoint**

En `routes/marketing.py`, dentro de `api_dossier`, cambiar el import y agregar
las dos claves al `jsonify`:

```python
    from services.dossier import (conciliacion, por_campana, por_segmento,
                                  serie_semanal)
```

```python
        "serie_semanal": serie_semanal(_db(), desde, hasta),
        "conciliacion": conciliacion(_db(), desde, hasta),
```

- [ ] **Step 6: Correr los tests de rutas**

Run: `python -m pytest tests/test_marketing_rutas.py -v`
Expected: PASS, 8 tests

- [ ] **Step 7: Commit**

```bash
git add services/dossier.py routes/marketing.py tests/test_dossier_series.py
git commit -m "feat(marketing): serie semanal y conciliacion del gasto

Semanal y no diaria: 238 leads en 178 dias son 1,3 por dia, y en grano
diario el grafico son picos y ceros.

La conciliacion compara lo que Meta cobro contra lo que se cargo a mano en
Finanzas. No se unifican las dos fuentes: se muestra la brecha, que dice
si se esta registrando todo lo que Meta cobro. Este modulo nunca escribe
en finanzas_movimientos."
```

---

### Task 11: Tiempos de reacción y la secuencia de recordatorios

**Files:**
- Modify: `services/dossier.py`
- Test: `tests/test_dossier_tiempos.py`

**Interfaces:**
- Consumes: `database._connect`, `services.dossier.metrica`,
  `services.dossier.proporcion`, `services.embudo.normalizar_estado`
- Produces:
  - `VENTANA_RECORDATORIO_DIAS: int = 7`
  - `tiempos(db_path: str, desde: str, hasta: str) -> list[dict]`
  - `recordatorios(db_path: str, desde: str, hasta: str) -> list[dict]`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_dossier_tiempos.py`:

```python
"""Cuanto se tarda en reaccionar, y si los recordatorios mueven algo.

La mediana y no el promedio: un lead contactado a los 60 dias arrastra el
promedio y hace parecer lento a un equipo que contesta el mismo dia.
"""

import pytest

from database import _connect, init_db
from services.dossier import recordatorios, tiempos


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _lead(db, nombre, entro="2026-03-01 09:00:00", eventos=()):
    conn = _connect(db)
    try:
        cur = conn.execute("INSERT INTO businesses (name, phone, source, scraped_at) "
                           "VALUES (?,?,?,?)", (nombre, nombre, "meta", entro))
        lead_id = cur.lastrowid
        for estado, cuando in eventos:
            conn.execute("INSERT INTO lead_events (lead_id, new_status, created_at) "
                         "VALUES (?,?,?)", (lead_id, estado, cuando))
        conn.commit()
        return lead_id
    finally:
        conn.close()


def _recordatorio(db, lead_id, numero, cuando):
    conn = _connect(db)
    try:
        conn.execute("INSERT INTO meta_reminders (business_id, estado, numero, "
                     "token, sent_at) VALUES (?,?,?,?,?)",
                     (lead_id, "sin_contactar", numero,
                      f"tok{lead_id}-{numero}", cuando))
        conn.commit()
    finally:
        conn.close()


def _buscar(metricas, sufijo):
    for m in metricas:
        if m["id"].endswith(sufijo):
            return m
    raise AssertionError(f"no hay metrica que termine en {sufijo!r}")


def test_mediana_de_dias_hasta_el_primer_evento(db):
    _lead(db, "a", eventos=[("interesado", "2026-03-03 09:00:00")])   # 2 dias
    _lead(db, "b", eventos=[("interesado", "2026-03-05 09:00:00")])   # 4 dias
    _lead(db, "c", eventos=[("interesado", "2026-03-07 09:00:00")])   # 6 dias
    m = _buscar(tiempos(db, "2026-03-01", "2026-03-31"), ".dias_a_primer_contacto")
    assert m["valor"] == 4.0


def test_la_mediana_aguanta_un_valor_extremo(db):
    """Con promedio, el lead contactado a los 60 dias arruina el numero."""
    _lead(db, "a", eventos=[("interesado", "2026-03-02 09:00:00")])
    _lead(db, "b", eventos=[("interesado", "2026-03-02 09:00:00")])
    _lead(db, "c", eventos=[("interesado", "2026-04-30 09:00:00")])
    m = _buscar(tiempos(db, "2026-03-01", "2026-05-31"), ".dias_a_primer_contacto")
    assert m["valor"] == 1.0


def test_mediana_de_dias_hasta_la_demo(db):
    _lead(db, "a", eventos=[("demo_1", "2026-03-11 09:00:00")])   # 10 dias
    m = _buscar(tiempos(db, "2026-03-01", "2026-03-31"), ".dias_a_demo")
    assert m["valor"] == 10.0


def test_la_demo_se_reconoce_con_el_vocabulario_viejo(db):
    """lead_events mezcla las dos epocas: reunion_hecha es demo_1."""
    _lead(db, "a", eventos=[("reunion_hecha", "2026-03-11 09:00:00")])
    m = _buscar(tiempos(db, "2026-03-01", "2026-03-31"), ".dias_a_demo")
    assert m["valor"] == 10.0


def test_un_lead_sin_eventos_no_cuenta_en_la_mediana(db):
    """Nunca contactado no es "tardo mucho": es que no esta el dato."""
    _lead(db, "a", eventos=[("interesado", "2026-03-03 09:00:00")])
    _lead(db, "sin_tocar")
    m = _buscar(tiempos(db, "2026-03-01", "2026-03-31"), ".dias_a_primer_contacto")
    assert m["n"] == 1


def test_sin_ningun_lead_con_eventos_el_tiempo_es_none(db):
    _lead(db, "a")
    m = _buscar(tiempos(db, "2026-03-01", "2026-03-31"), ".dias_a_primer_contacto")
    assert m["valor"] is None


def test_un_recordatorio_que_movio_el_estado_cuenta(db):
    lead = _lead(db, "a", eventos=[("interesado", "2026-03-10 09:00:00")])
    _recordatorio(db, lead, 1, "2026-03-08 09:00:00")     # 2 dias antes
    m = _buscar(recordatorios(db, "2026-03-01", "2026-03-31"), ".tasa_movio_1")
    assert m["numerador"] == 1
    assert m["denominador"] == 1


def test_un_evento_muy_posterior_no_se_le_atribuye_al_recordatorio(db):
    """La ventana son 7 dias. Un cambio 20 dias despues no fue por el mail."""
    lead = _lead(db, "a", eventos=[("interesado", "2026-03-28 09:00:00")])
    _recordatorio(db, lead, 1, "2026-03-08 09:00:00")
    m = _buscar(recordatorios(db, "2026-03-01", "2026-03-31"), ".tasa_movio_1")
    assert m["numerador"] == 0


def test_un_evento_anterior_al_recordatorio_tampoco_cuenta(db):
    lead = _lead(db, "a", eventos=[("interesado", "2026-03-02 09:00:00")])
    _recordatorio(db, lead, 1, "2026-03-08 09:00:00")
    m = _buscar(recordatorios(db, "2026-03-01", "2026-03-31"), ".tasa_movio_1")
    assert m["numerador"] == 0


def test_cada_numero_de_la_secuencia_se_mide_aparte(db):
    a = _lead(db, "a", eventos=[("interesado", "2026-03-10 09:00:00")])
    _recordatorio(db, a, 1, "2026-03-08 09:00:00")
    b = _lead(db, "b")
    _recordatorio(db, b, 3, "2026-03-08 09:00:00")
    ids = {m["id"] for m in recordatorios(db, "2026-03-01", "2026-03-31")}
    assert any(i.endswith(".tasa_movio_1") for i in ids)
    assert any(i.endswith(".tasa_movio_3") for i in ids)


def test_sin_recordatorios_no_hay_metricas(db):
    assert recordatorios(db, "2026-03-01", "2026-03-31") == []
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_dossier_tiempos.py -v`
Expected: FAIL con `ImportError: cannot import name 'tiempos'`

- [ ] **Step 3: Agregar las dos funciones a `services/dossier.py`**

```python
# Cuantos dias despues de un recordatorio se le puede atribuir un cambio de
# estado. Mas alla de eso, el mail no fue lo que lo movio.
VENTANA_RECORDATORIO_DIAS = 7


def _dias_entre(desde_iso, hasta_iso):
    """Dias enteros entre dos timestamps de SQLite, o None si no se puede."""
    from datetime import datetime

    def _parse(v):
        v = (v or "").strip().replace("T", " ")
        if not v:
            return None
        for formato in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(v[:19] if len(v) >= 19 else v, formato)
            except ValueError:
                continue
        return None

    a, b = _parse(desde_iso), _parse(hasta_iso)
    if not a or not b:
        return None
    return (b - a).days


def _mediana(valores):
    if not valores:
        return None
    ordenados = sorted(valores)
    mitad = len(ordenados) // 2
    if len(ordenados) % 2:
        return float(ordenados[mitad])
    return round((ordenados[mitad - 1] + ordenados[mitad]) / 2, 2)


def tiempos(db_path: str, desde: str, hasta: str) -> list:
    """Cuanto se tarda en reaccionarle a un lead.

    La mediana y no el promedio: un lead contactado a los 60 dias arrastra el
    promedio y hace parecer lento a un equipo que contesta el mismo dia.

    Un lead sin ningun evento no entra en la cuenta. "Nunca contactado" no es
    "tardo mucho": es que el dato no esta, y meterlo como un numero grande
    seria inventarlo.
    """
    from services.embudo import normalizar_estado

    conn = _connect(db_path)
    try:
        leads = conn.execute(
            "SELECT id, scraped_at FROM businesses WHERE source = 'meta' "
            "AND substr(scraped_at, 1, 10) BETWEEN ? AND ?",
            (desde, hasta)).fetchall()
        eventos = conn.execute(
            "SELECT lead_id, new_status, created_at FROM lead_events "
            "ORDER BY created_at").fetchall()
    finally:
        conn.close()

    primero, primera_demo = {}, {}
    for e in eventos:
        primero.setdefault(e["lead_id"], e["created_at"])
        if normalizar_estado(e["new_status"]) == "demo_1":
            primera_demo.setdefault(e["lead_id"], e["created_at"])

    a_contacto, a_demo = [], []
    for lead in leads:
        d = _dias_entre(lead["scraped_at"], primero.get(lead["id"]))
        if d is not None and d >= 0:
            a_contacto.append(d)
        d = _dias_entre(lead["scraped_at"], primera_demo.get(lead["id"]))
        if d is not None and d >= 0:
            a_demo.append(d)

    return [
        metrica("tiempos.dias_a_primer_contacto",
                "Días hasta el primer contacto (mediana)",
                _mediana(a_contacto), "crm", denominador=len(a_contacto)),
        metrica("tiempos.dias_a_demo", "Días hasta la demo (mediana)",
                _mediana(a_demo), "crm", denominador=len(a_demo)),
    ]


def recordatorios(db_path: str, desde: str, hasta: str) -> list:
    """Si cada mail de la secuencia movio el estado del lead.

    Atribucion por ventana, no causalidad: se cuenta si hubo un cambio de
    estado dentro de los 7 dias siguientes al envio. Pudo haber pasado otra
    cosa en el medio —una llamada, por ejemplo— y el informe tiene prohibido
    decir que el mail lo causo.
    """
    conn = _connect(db_path)
    try:
        envios = conn.execute(
            "SELECT business_id, numero, sent_at FROM meta_reminders "
            "WHERE substr(sent_at, 1, 10) BETWEEN ? AND ?",
            (desde, hasta)).fetchall()
        eventos = conn.execute(
            "SELECT lead_id, created_at FROM lead_events").fetchall()
    finally:
        conn.close()

    por_lead = {}
    for e in eventos:
        por_lead.setdefault(e["lead_id"], []).append(e["created_at"])

    conteo = {}
    for envio in envios:
        slot = conteo.setdefault(envio["numero"], {"enviados": 0, "movio": 0})
        slot["enviados"] += 1
        for cuando in por_lead.get(envio["business_id"], []):
            dias = _dias_entre(envio["sent_at"], cuando)
            if dias is not None and 0 <= dias <= VENTANA_RECORDATORIO_DIAS:
                slot["movio"] += 1
                break

    return [
        proporcion(
            f"recordatorios.tasa_movio_{n}",
            f"Recordatorio {n}: movió el estado en {VENTANA_RECORDATORIO_DIAS} días",
            conteo[n]["movio"], conteo[n]["enviados"], "crm")
        for n in sorted(conteo)
    ]
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_dossier_tiempos.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Sumarlas al endpoint**

En `routes/marketing.py`, agregar `recordatorios` y `tiempos` al import de
`services.dossier` y al `jsonify`:

```python
        "tiempos": tiempos(_db(), desde, hasta),
        "recordatorios": recordatorios(_db(), desde, hasta),
```

- [ ] **Step 6: Commit**

```bash
git add services/dossier.py routes/marketing.py tests/test_dossier_tiempos.py
git commit -m "feat(marketing): tiempos de reaccion y secuencia de recordatorios

La mediana y no el promedio: un lead contactado a los 60 dias arrastra el
promedio y hace parecer lento a un equipo que contesta el mismo dia. Un
lead sin eventos no entra: 'nunca contactado' no es 'tardo mucho', es que
el dato no esta.

Los recordatorios se miden por ventana de 7 dias. Es atribucion, no
causalidad, y el docstring lo dice para que nadie lo lea como que el mail
fue la causa."
```

---

### Task 12: Guardar el snapshot y calcular los deltas

Sin snapshot no hay "esto cambió respecto de la semana pasada", que es la mitad
de contestar cómo venimos. **La tabla `radiografias` se llena en esta fase
aunque la IA no exista todavía**: el dossier se guarda con `status='sin_ia'` y
`report_json` en `NULL`.

**Files:**
- Create: `services/radiografia.py`
- Modify: `routes/marketing.py`
- Test: `tests/test_radiografia_snapshot.py`

**Interfaces:**
- Consumes: todas las funciones públicas de `services/dossier.py`
- Produces:
  - `construir_dossier(db_path: str, desde: str, hasta: str) -> dict`
  - `guardar_snapshot(db_path: str, dossier: dict) -> int`
  - `ultimo_snapshot(db_path: str) -> dict | None`
  - `aplicar_deltas(dossier: dict, anterior: dict | None) -> dict`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_radiografia_snapshot.py`:

```python
"""El snapshot semanal y la comparacion contra el anterior.

En esta fase no hay IA: el snapshot se guarda con status 'sin_ia' y report_json
en NULL. El panel funciona igual, porque los graficos salen del dossier y no
del informe.
"""

import pytest

from database import _connect, init_db
from services.radiografia import (aplicar_deltas, construir_dossier,
                                  guardar_snapshot, ultimo_snapshot)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def test_el_dossier_trae_todos_los_bloques(db):
    d = construir_dossier(db, "2026-03-01", "2026-03-31")
    assert set(d) >= {"periodo", "campanas", "segmentos", "serie_semanal",
                      "conciliacion", "tiempos", "recordatorios"}
    assert d["periodo"] == {"desde": "2026-03-01", "hasta": "2026-03-31"}


def test_guardar_deja_status_sin_ia(db):
    """Esta fase no llama a la API. El snapshot se guarda igual."""
    guardar_snapshot(db, construir_dossier(db, "2026-03-01", "2026-03-31"))
    conn = _connect(db)
    try:
        fila = conn.execute("SELECT * FROM radiografias").fetchone()
    finally:
        conn.close()
    assert fila["status"] == "sin_ia"
    assert fila["report_json"] is None
    assert fila["dossier_json"]


def test_ultimo_snapshot_devuelve_el_mas_reciente(db):
    guardar_snapshot(db, construir_dossier(db, "2026-03-01", "2026-03-31"))
    guardar_snapshot(db, construir_dossier(db, "2026-04-01", "2026-04-30"))
    assert ultimo_snapshot(db)["periodo"]["desde"] == "2026-04-01"


def test_sin_snapshots_previos_devuelve_none(db):
    assert ultimo_snapshot(db) is None


def test_los_deltas_salen_del_snapshot_anterior(db):
    anterior = {"campanas": [{"campana": "x", "metricas": [
        {"id": "campana.x.gasto", "valor": 100.0}]}]}
    ahora = {"campanas": [{"campana": "x", "metricas": [
        {"id": "campana.x.gasto", "valor": 130.0,
         "delta_periodo_anterior": None}]}]}
    r = aplicar_deltas(ahora, anterior)
    assert r["campanas"][0]["metricas"][0]["delta_periodo_anterior"] == 30.0


def test_sin_anterior_el_delta_queda_en_none(db):
    """La primera corrida no tiene contra que comparar. Eso no es cero."""
    ahora = {"campanas": [{"campana": "x", "metricas": [
        {"id": "campana.x.gasto", "valor": 130.0,
         "delta_periodo_anterior": None}]}]}
    r = aplicar_deltas(ahora, None)
    assert r["campanas"][0]["metricas"][0]["delta_periodo_anterior"] is None


def test_una_metrica_nueva_no_tiene_delta(db):
    """Una campana que arranco esta semana no existia en el snapshot anterior."""
    anterior = {"campanas": [{"campana": "x", "metricas": [
        {"id": "campana.x.gasto", "valor": 100.0}]}]}
    ahora = {"campanas": [{"campana": "y", "metricas": [
        {"id": "campana.y.gasto", "valor": 50.0,
         "delta_periodo_anterior": None}]}]}
    r = aplicar_deltas(ahora, anterior)
    assert r["campanas"][0]["metricas"][0]["delta_periodo_anterior"] is None


def test_una_metrica_sin_valor_no_inventa_delta(db):
    anterior = {"campanas": [{"campana": "x", "metricas": [
        {"id": "campana.x.cpl", "valor": 20.0}]}]}
    ahora = {"campanas": [{"campana": "x", "metricas": [
        {"id": "campana.x.cpl", "valor": None,
         "delta_periodo_anterior": None}]}]}
    r = aplicar_deltas(ahora, anterior)
    assert r["campanas"][0]["metricas"][0]["delta_periodo_anterior"] is None


def test_los_deltas_alcanzan_a_los_segmentos(db):
    anterior = {"segmentos": [{"pregunta": "p", "valores": [
        {"valor_declarado": "v", "metricas": [
            {"id": "segmento.p.v.tasa_demo", "valor": 0.1}]}]}]}
    ahora = {"segmentos": [{"pregunta": "p", "valores": [
        {"valor_declarado": "v", "metricas": [
            {"id": "segmento.p.v.tasa_demo", "valor": 0.25,
             "delta_periodo_anterior": None}]}]}]}
    r = aplicar_deltas(ahora, anterior)
    assert r["segmentos"][0]["valores"][0]["metricas"][0]["delta_periodo_anterior"] == 0.15


def test_el_modulo_no_importa_anthropic():
    """La fase 1 no gasta un token. Se verifica sobre el fuente, igual que en
    tests/test_linkedin_sin_api.py."""
    import io
    fuente = io.open("services/radiografia.py", encoding="utf-8").read()
    assert "anthropic" not in fuente.lower()
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_radiografia_snapshot.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'services.radiografia'`

- [ ] **Step 3: Escribir `services/radiografia.py`**

```python
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
from services.dossier import (conciliacion, por_campana, por_segmento,
                              recordatorios, serie_semanal, tiempos)

logger = logging.getLogger(__name__)


def construir_dossier(db_path: str, desde: str, hasta: str) -> dict:
    """Todo lo que se puede medir del periodo, en un solo objeto."""
    return {
        "periodo": {"desde": desde, "hasta": hasta},
        "campanas": por_campana(db_path, desde, hasta),
        "segmentos": por_segmento(db_path, desde, hasta),
        "serie_semanal": serie_semanal(db_path, desde, hasta),
        "conciliacion": conciliacion(db_path, desde, hasta),
        "tiempos": tiempos(db_path, desde, hasta),
        "recordatorios": recordatorios(db_path, desde, hasta),
    }


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


def guardar_snapshot(db_path: str, dossier: dict) -> int:
    """Guarda el dossier. Sin informe: en esta fase no hay IA."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO radiografias (period_start, period_end, dossier_json, "
            "report_json, status) VALUES (?,?,?,NULL,'sin_ia')",
            (dossier["periodo"]["desde"], dossier["periodo"]["hasta"],
             json.dumps(dossier, ensure_ascii=False)))
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
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_radiografia_snapshot.py -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Que el endpoint use el módulo, y agregar el de generar**

En `routes/marketing.py`, reemplazar `api_dossier` y agregar `api_generar`:

```python
@marketing_bp.route("/api/marketing/dossier")
def api_dossier():
    from services.radiografia import (aplicar_deltas, construir_dossier,
                                      ultimo_snapshot)

    desde, hasta = _periodo()
    if not desde:
        return jsonify({"error": "fechas invalidas, se espera YYYY-MM-DD"}), 400

    return jsonify(aplicar_deltas(construir_dossier(_db(), desde, hasta),
                                  ultimo_snapshot(_db())))


@marketing_bp.route("/api/marketing/generar", methods=["POST"])
def api_generar():
    """Calcula el dossier del periodo y lo guarda como snapshot.

    En esta fase termina aca: no hay informe. Cuando exista el motor de IA se
    encadena despues de guardar.
    """
    from services.radiografia import (aplicar_deltas, construir_dossier,
                                      guardar_snapshot, ultimo_snapshot)

    desde, hasta = _periodo()
    if not desde:
        return jsonify({"error": "fechas invalidas, se espera YYYY-MM-DD"}), 400

    dossier = aplicar_deltas(construir_dossier(_db(), desde, hasta),
                             ultimo_snapshot(_db()))
    return jsonify({"id": guardar_snapshot(_db(), dossier), "status": "sin_ia"})
```

- [ ] **Step 6: Correr la suite entera**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 7: Verificar que la fase entera no importa `anthropic`**

Run: `grep -rln "anthropic" services/dossier.py services/radiografia.py services/meta_insights.py services/meta_campanas.py services/embudo.py routes/marketing.py`
Expected: sin resultados. Esta fase no gasta un token.

- [ ] **Step 8: Commit**

```bash
git add services/radiografia.py routes/marketing.py tests/test_radiografia_snapshot.py
git commit -m "feat(marketing): snapshot del dossier y deltas

Sin snapshot no hay 'esto cambio respecto de la semana pasada', que es la
mitad de contestar como venimos. Se guarda el dossier entero, no un
resumen: es lo que va a permitir auditar despues.

En esta fase el snapshot va con status 'sin_ia' y report_json en NULL. El
panel funciona igual porque los graficos salen del dossier.

Una metrica que no existia en el snapshot anterior se queda con delta en
None. No es un cambio de cero: no habia contra que comparar."
```

---

## Cuando termine esta fase

1. **Anotar en `COORDINACION.md`** qué quedó hecho, que `services/finanzas.py`
   ahora importa de `services/embudo.py`, y que `routes/meta.py` escribe
   columnas nuevas. La sesión que siga solo va a ver eso.
   Al terminar las 12 tareas, `GET /api/marketing/dossier` devuelve métricas por
   campaña, por segmento declarado, la serie semanal, la conciliación del gasto,
   los tiempos y la secuencia de recordatorios — todo con `n` e intervalo, y
   comparado contra el snapshot anterior.
2. **No deployar todavía.** El sync de Insights necesita `META_ADS_TOKEN` y
   `META_AD_ACCOUNT_ID` en los secrets de Fly, y el `action_type` de lead sigue
   sin verificar (Task 5, Step 5). Deployar antes de eso deja el CPL en `None` y
   parece un bug del código cuando es un dato que falta.
3. **Fase 2:** el panel y los gráficos SVG, sobre estos mismos endpoints.
4. **Fase 3:** el motor de IA, cuando Juan diga que se puede volver a gastar.
