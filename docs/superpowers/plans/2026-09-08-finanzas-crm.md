# Sección financiera del CRM — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un panel Finanzas en el CRM con el libro de ingresos y egresos de Scalerics, gastos fijos que se repiten solos mes a mes, y totales en USD sobre movimientos cargados en dos monedas.

**Architecture:** Dos tablas nuevas en `database.py`. Toda la aritmética que puede dar un número mal (conversión de moneda, materialización de los fijos, agregados por mes) vive en `services/finanzas.py`, testeada sin Flask ni browser. `routes/finanzas.py` valida y serializa. El panel va inline en `DASHBOARD_HTML` como todos los demás, pero su JavaScript solo dibuja lo que le mandan.

**Tech Stack:** Python 3.11, Flask, SQLite (`sqlite3` de stdlib, sin ORM), pytest. Frontend: HTML/CSS/JS vanilla embebido en strings de Python, íconos lucide por CDN. Sin librería de charts.

**Spec:** `docs/superpowers/specs/2026-09-08-finanzas-crm-design.md`

## Global Constraints

- **Monedas:** exactamente dos, `'USD'` y `'UYU'`. `tipo_cambio` se expresa en **pesos por dólar** y solo aplica cuando `moneda = 'UYU'`.
- **Períodos:** siempre strings `'YYYY-MM'`. Fechas siempre `'YYYY-MM-DD'`. Nunca objetos `date` cruzando la frontera de la base.
- **Categorías:** lista fija en `services.finanzas.CATEGORIAS`. Egresos: `infraestructura`, `herramientas`, `publicidad`, `retiros`, `impuestos`, `servicios`, `otros`. Ingresos: `desarrollo_web`, `software_medida`, `mantenimiento`, `marketing`, `otros`.
- **Nombre del panel:** el string `"finanzas"`, idéntico en `panel_access`, en el id del nav (`nav-finanzas`), en el id del div (`finanzas-panel`) y en la ruta (`/api/finanzas/...`).
- **`monto_usd` se congela al guardar.** Ninguna consulta lo recalcula.
- **Todos los agregados y listados filtran `anulado = 0`.**
- **Nada de hilos nuevos al arranque.** Regla 3 de `COORDINACION.md`.
- **Íconos lucide, cero emojis.** El CRM tiene modo claro: todo lo que se agregue al CSS necesita su regla `body.light`.
- **Deploy solo con el árbol limpio.** `git status --short` vacío antes de `flyctl deploy` — el `Dockerfile` hace `COPY . .`.
- **CI:** `pytest --cov-fail-under=57` y `python scripts/check_js.py`. El umbral de cobertura es un trinquete: no puede bajar.
- **Nunca usar números de línea para ubicar código.** Hay otra sesión trabajando en este repo y las líneas se corren. Cada paso que toca un archivo existente trae el `grep` con el que se ubica el punto de inserción.

## Antes de empezar

Al 8/9/2026 hay **otra sesión trabajando en el repo** con cambios sin commitear
en `dashboard.py`, `routes/calendar.py`, `fly.toml` y `COORDINACION.md`, más dos
archivos de test sin trackear (`tests/test_calendar_reprogramar.py`,
`tests/test_calendar_semana_js.py`). Es la sesión B, que tiene declarado
`routes/calendar.py`.

Consecuencias directas:

1. **`dashboard.py` está siendo editado por otro.** Hacer `git pull` y
   `git status` antes de empezar, y anotarse en `COORDINACION.md` (Task 12,
   que conviene hacer **primero** y no último si esos cambios siguen ahí).
2. **No deployar hasta que el árbol esté limpio.** El `Dockerfile` hace
   `COPY . .`: `flyctl deploy` subiría el trabajo a medias de la otra sesión.
   Esto ya pasó y está documentado como la regla 1 de `COORDINACION.md`.
3. Si al terminar la Task 11 el árbol sigue teniendo cambios ajenos, **parar
   antes de la Task 12** y avisarle a Juan en vez de deployar.

## Estructura de archivos

| Archivo | Responsabilidad |
|---|---|
| `database.py` | las dos tablas, sus índices, y accesores CRUD tontos (sin reglas de negocio) |
| `services/finanzas.py` | categorías, conversión a USD, materialización de fijos, agregados |
| `services/auth.py` | `require_panel()` — nuevo, junto a `require_admin()` |
| `routes/finanzas.py` | endpoints `/api/finanzas/*`, validación y serialización |
| `dashboard.py` | el panel: registro en el nav, HTML, CSS y JS de render |
| `tests/test_finanzas_db.py` | tablas, índices y accesores |
| `tests/test_finanzas_service.py` | conversión, materialización, resumen |
| `tests/test_finanzas_rutas.py` | endpoints y permisos |
| `tests/test_finanzas_panel.py` | que el panel esté registrado en los siete lugares |

---

### Task 1: Tablas y accesores

**Files:**
- Modify: `database.py` (dentro de `init_db`, después del bloque de `linkedin_banco`; y accesores al final del archivo)
- Test: `tests/test_finanzas_db.py`

**Interfaces:**
- Consumes: `_connect(db_path)`, `_grant_panel_to_existing_roles(conn, panel)`, `init_db(db_path)` — ya existen.
- Produces:
  - `crear_movimiento(db_path, **fields) -> int`
  - `actualizar_movimiento(db_path, mov_id: int, **fields) -> None`
  - `borrar_movimiento(db_path, mov_id: int) -> None`
  - `get_movimiento(db_path, mov_id: int) -> dict | None`
  - `listar_movimientos(db_path, desde=None, hasta=None, tipo=None, categoria=None, client_id=None, incluir_anulados=False) -> list[dict]`
  - `crear_recurrente(db_path, **fields) -> int`
  - `actualizar_recurrente(db_path, rec_id: int, **fields) -> None`
  - `borrar_recurrente(db_path, rec_id: int) -> None`
  - `get_recurrente(db_path, rec_id: int) -> dict | None`
  - `listar_recurrentes(db_path, solo_activos: bool = False) -> list[dict]`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_finanzas_db.py`:

```python
"""Las dos tablas de finanzas y sus accesores.

El test que más importa acá es el del índice único: es lo único que impide
que cada deploy —que reinicia la máquina y vuelve a materializar los fijos—
duplique los gastos del mes.
"""

import sqlite3

import pytest

from database import (actualizar_movimiento, borrar_movimiento, crear_movimiento,
                      crear_recurrente, get_movimiento, get_recurrente, init_db,
                      listar_movimientos, listar_recurrentes)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "f.db")
    init_db(ruta)
    return ruta


def _mov(db, **extra):
    campos = dict(tipo="egreso", fecha="2026-09-20", periodo="2026-09",
                  concepto="Fly", categoria="infraestructura",
                  monto=4.18, moneda="USD", monto_usd=4.18)
    campos.update(extra)
    return crear_movimiento(db, **campos)


def test_crear_y_leer_un_movimiento(db):
    mid = _mov(db)
    fila = get_movimiento(db, mid)
    assert fila["concepto"] == "Fly"
    assert fila["monto_usd"] == 4.18
    assert fila["anulado"] == 0


def test_un_fijo_no_puede_generar_dos_veces_el_mismo_mes(db):
    rid = crear_recurrente(db, tipo="egreso", concepto="Fly",
                           categoria="infraestructura", monto=4.18,
                           moneda="USD", dia_del_mes=1, desde="2026-09")
    _mov(db, recurrente_id=rid, periodo="2026-09")
    with pytest.raises(sqlite3.IntegrityError):
        _mov(db, recurrente_id=rid, periodo="2026-09")


def test_dos_movimientos_a_mano_del_mismo_mes_conviven(db):
    # recurrente_id NULL: el índice es parcial y no los alcanza.
    _mov(db, concepto="Dominio")
    _mov(db, concepto="Zoho")
    assert len(listar_movimientos(db)) == 2


def test_listar_filtra_por_periodo_y_tipo(db):
    _mov(db, periodo="2026-08", fecha="2026-08-20")
    _mov(db, periodo="2026-09", tipo="ingreso", categoria="desarrollo_web")
    assert len(listar_movimientos(db, desde="2026-09", hasta="2026-09")) == 1
    assert len(listar_movimientos(db, tipo="ingreso")) == 1


def test_los_anulados_no_aparecen_salvo_que_se_pidan(db):
    mid = _mov(db)
    actualizar_movimiento(db, mid, anulado=1)
    assert listar_movimientos(db) == []
    assert len(listar_movimientos(db, incluir_anulados=True)) == 1


def test_borrar_un_movimiento_lo_saca(db):
    mid = _mov(db)
    borrar_movimiento(db, mid)
    assert get_movimiento(db, mid) is None


def test_listar_recurrentes_solo_activos(db):
    crear_recurrente(db, tipo="egreso", concepto="Fly",
                     categoria="infraestructura", monto=4.18,
                     moneda="USD", dia_del_mes=1, desde="2026-09")
    crear_recurrente(db, tipo="egreso", concepto="Viejo",
                     categoria="herramientas", monto=10, moneda="USD",
                     dia_del_mes=1, desde="2026-01", activo=0)
    assert len(listar_recurrentes(db)) == 2
    assert len(listar_recurrentes(db, solo_activos=True)) == 1


def test_el_panel_finanzas_le_llega_a_los_roles_que_ya_existian(tmp_path):
    """Reproduce producción: la tabla `roles` ya tiene filas cuando llega el panel."""
    ruta = str(tmp_path / "vieja.db")
    init_db(ruta)  # siembra Admin/Caller/Ventas
    conn = sqlite3.connect(ruta)
    conn.execute("UPDATE roles SET panel_access = ? WHERE name = 'Ventas'",
                 ('["cola","clientes"]',))
    conn.commit()
    conn.close()

    init_db(ruta)  # segundo arranque: acá tiene que entrar el grant

    conn = sqlite3.connect(ruta)
    fila = conn.execute("SELECT panel_access FROM roles WHERE name='Ventas'").fetchone()
    conn.close()
    assert "finanzas" in fila[0]
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `python -m pytest tests/test_finanzas_db.py -v`
Expected: FAIL con `ImportError: cannot import name 'crear_movimiento' from 'database'`

- [ ] **Step 3: Crear las tablas**

En `database.py`, dentro de `init_db`, después del bloque `CREATE TABLE IF NOT EXISTS linkedin_banco`:

```python
        # ── finanzas ──────────────────────────────────────────────────────────
        # El libro de ingresos y egresos de Scalerics. Un solo libro con una
        # columna `tipo`: un cobro y un gasto tienen los mismos campos.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS finanzas_movimientos (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo            TEXT NOT NULL,
                fecha           TEXT NOT NULL,
                periodo         TEXT NOT NULL,
                concepto        TEXT NOT NULL,
                categoria       TEXT NOT NULL,
                monto           REAL NOT NULL,
                moneda          TEXT NOT NULL,
                tipo_cambio     REAL,
                monto_usd       REAL NOT NULL,
                client_id       INTEGER REFERENCES businesses(id),
                budget_id       INTEGER REFERENCES budgets(id),
                recurrente_id   INTEGER REFERENCES finanzas_recurrentes(id),
                anulado         INTEGER NOT NULL DEFAULT 0,
                notas           TEXT,
                created_by_id   INTEGER,
                created_by_name TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Esta es LA guarda del módulo. Materializar los fijos usa
        # INSERT OR IGNORE contra este índice, así que correrlo mil veces
        # produce exactamente un movimiento por fijo y por mes. Sin él, cada
        # deploy duplicaría los gastos: reiniciar la máquina vuelve a
        # materializar.
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_finanzas_recurrente_periodo
                ON finanzas_movimientos (recurrente_id, periodo)
                WHERE recurrente_id IS NOT NULL
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_finanzas_periodo
                ON finanzas_movimientos (periodo)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS finanzas_recurrentes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo        TEXT NOT NULL,
                concepto    TEXT NOT NULL,
                categoria   TEXT NOT NULL,
                monto       REAL NOT NULL,
                moneda      TEXT NOT NULL,
                tipo_cambio REAL,
                dia_del_mes INTEGER NOT NULL DEFAULT 1,
                desde       TEXT NOT NULL,
                hasta       TEXT,
                activo      INTEGER NOT NULL DEFAULT 1,
                client_id   INTEGER REFERENCES businesses(id),
                notas       TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _grant_panel_to_existing_roles(conn, "finanzas")
```

- [ ] **Step 4: Escribir los accesores**

Al final de `database.py`:

```python
# ─── Finanzas ────────────────────────────────────────────────────────────────

_MOVIMIENTO_COLUMNS = {
    "tipo", "fecha", "periodo", "concepto", "categoria", "monto", "moneda",
    "tipo_cambio", "monto_usd", "client_id", "budget_id", "recurrente_id",
    "anulado", "notas", "created_by_id", "created_by_name",
}

_RECURRENTE_COLUMNS = {
    "tipo", "concepto", "categoria", "monto", "moneda", "tipo_cambio",
    "dia_del_mes", "desde", "hasta", "activo", "client_id", "notas",
}


def _insert(db_path: str, tabla: str, columnas: set, fields: dict,
            obligatorias: tuple) -> int:
    permitidos = {k: v for k, v in fields.items() if k in columnas}
    faltan = [c for c in obligatorias if c not in permitidos]
    if faltan:
        raise ValueError(f"{tabla}: faltan campos obligatorios {faltan}")
    cols = list(permitidos)
    vals = list(permitidos.values())
    marcas = ", ".join("?" for _ in vals)
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            f"INSERT INTO {tabla} ({', '.join(cols)}) VALUES ({marcas})", vals)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _update(db_path: str, tabla: str, columnas: set, fila_id: int,
            fields: dict) -> None:
    invalidos = set(fields) - columnas
    if invalidos:
        raise ValueError(f"{tabla}: columnas inválidas {invalidos}")
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields = {**fields, "id": fila_id}
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE {tabla} SET {set_clause} WHERE id = :id", fields)
        conn.commit()
    finally:
        conn.close()


def _delete(db_path: str, tabla: str, fila_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(f"DELETE FROM {tabla} WHERE id = ?", (fila_id,))
        conn.commit()
    finally:
        conn.close()


def _get_one(db_path: str, tabla: str, fila_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        fila = conn.execute(f"SELECT * FROM {tabla} WHERE id = ?",
                            (fila_id,)).fetchone()
        return dict(fila) if fila else None
    finally:
        conn.close()


def crear_movimiento(db_path: str, **fields) -> int:
    return _insert(db_path, "finanzas_movimientos", _MOVIMIENTO_COLUMNS, fields,
                   ("tipo", "fecha", "periodo", "concepto", "categoria",
                    "monto", "moneda", "monto_usd"))


def actualizar_movimiento(db_path: str, mov_id: int, **fields) -> None:
    _update(db_path, "finanzas_movimientos", _MOVIMIENTO_COLUMNS, mov_id, fields)


def borrar_movimiento(db_path: str, mov_id: int) -> None:
    _delete(db_path, "finanzas_movimientos", mov_id)


def get_movimiento(db_path: str, mov_id: int) -> Optional[dict]:
    return _get_one(db_path, "finanzas_movimientos", mov_id)


def listar_movimientos(db_path: str, desde: Optional[str] = None,
                       hasta: Optional[str] = None, tipo: Optional[str] = None,
                       categoria: Optional[str] = None,
                       client_id: Optional[int] = None,
                       incluir_anulados: bool = False) -> list[dict]:
    """Movimientos ordenados por fecha descendente.

    `desde` y `hasta` son períodos 'YYYY-MM', ambos inclusive. Por defecto no
    devuelve los anulados: un movimiento anulado es un fijo que se borró y que
    solo sigue en la tabla para que la materialización no lo regenere.
    """
    partes: list[str] = []
    params: list = []
    if not incluir_anulados:
        partes.append("anulado = 0")
    if desde is not None:
        partes.append("periodo >= ?")
        params.append(desde)
    if hasta is not None:
        partes.append("periodo <= ?")
        params.append(hasta)
    if tipo is not None:
        partes.append("tipo = ?")
        params.append(tipo)
    if categoria is not None:
        partes.append("categoria = ?")
        params.append(categoria)
    if client_id is not None:
        partes.append("client_id = ?")
        params.append(client_id)
    where = f"WHERE {' AND '.join(partes)}" if partes else ""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            f"SELECT * FROM finanzas_movimientos {where} "
            "ORDER BY fecha DESC, id DESC", params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def crear_recurrente(db_path: str, **fields) -> int:
    return _insert(db_path, "finanzas_recurrentes", _RECURRENTE_COLUMNS, fields,
                   ("tipo", "concepto", "categoria", "monto", "moneda", "desde"))


def actualizar_recurrente(db_path: str, rec_id: int, **fields) -> None:
    _update(db_path, "finanzas_recurrentes", _RECURRENTE_COLUMNS, rec_id, fields)


def borrar_recurrente(db_path: str, rec_id: int) -> None:
    """Borra la definición del fijo.

    Los movimientos que ya generó quedan vivos: son plata que se gastó. Para
    dejar de generar hacia adelante sin borrar nada está `activo = 0`.
    """
    _delete(db_path, "finanzas_recurrentes", rec_id)


def get_recurrente(db_path: str, rec_id: int) -> Optional[dict]:
    return _get_one(db_path, "finanzas_recurrentes", rec_id)


def listar_recurrentes(db_path: str, solo_activos: bool = False) -> list[dict]:
    where = "WHERE activo = 1" if solo_activos else ""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            f"SELECT * FROM finanzas_recurrentes {where} ORDER BY concepto")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
```

- [ ] **Step 5: Correr los tests**

Run: `python -m pytest tests/test_finanzas_db.py -v`
Expected: PASS, 8 tests

- [ ] **Step 6: Correr la suite entera**

Run: `python -m pytest -q`
Expected: PASS. `test_el_panel_finanzas_le_llega_a_los_roles_que_ya_existian` es el que verifica que el grant no rompió nada de lo que ya andaba.

- [ ] **Step 7: Commit**

```bash
git add database.py tests/test_finanzas_db.py
git commit -m "feat(finanzas): tablas de movimientos y fijos"
```

---

### Task 2: Categorías y conversión a USD

**Files:**
- Create: `services/finanzas.py`
- Test: `tests/test_finanzas_service.py`

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces:
  - `CATEGORIAS: dict[str, list[str]]`
  - `MONEDAS: tuple[str, str]`
  - `periodo_de(fecha: str) -> str`
  - `a_usd(monto: float, moneda: str, tipo_cambio: float | None) -> float`
  - `meses_entre(desde: str, hasta: str) -> list[str]`
  - `periodo_anterior(desde: str, hasta: str) -> tuple[str, str]`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_finanzas_service.py`:

```python
"""Conversión de moneda y aritmética de períodos.

Nada de esto toca la base ni Flask: es la parte que, si da un número mal, se
cree. Un total de egresos equivocado en un panel financiero es peor que un
bug de UI.
"""

import pytest

from services.finanzas import (CATEGORIAS, a_usd, meses_entre, periodo_anterior,
                               periodo_de)


def test_un_movimiento_en_dolares_no_se_convierte():
    assert a_usd(4.18, "USD", None) == 4.18


def test_un_movimiento_en_pesos_se_divide_por_el_tipo_de_cambio():
    assert a_usd(40000, "UYU", 40.0) == 1000.0


def test_pesos_sin_tipo_de_cambio_es_un_error():
    # Guardarlo con monto_usd=0 ensuciaría el mes en silencio.
    with pytest.raises(ValueError):
        a_usd(40000, "UYU", None)


def test_pesos_con_tipo_de_cambio_cero_o_negativo_es_un_error():
    with pytest.raises(ValueError):
        a_usd(40000, "UYU", 0)
    with pytest.raises(ValueError):
        a_usd(40000, "UYU", -40)


def test_una_moneda_que_no_existe_es_un_error():
    with pytest.raises(ValueError):
        a_usd(100, "EUR", None)


def test_el_periodo_sale_de_la_fecha():
    assert periodo_de("2026-09-20") == "2026-09"


def test_meses_entre_incluye_las_dos_puntas():
    assert meses_entre("2026-11", "2027-02") == ["2026-11", "2026-12",
                                                 "2027-01", "2027-02"]


def test_meses_entre_un_solo_mes():
    assert meses_entre("2026-09", "2026-09") == ["2026-09"]


def test_meses_entre_al_reves_da_vacio():
    assert meses_entre("2026-09", "2026-08") == []


def test_el_periodo_anterior_tiene_el_mismo_largo():
    # Tres meses (jul-sep) -> los tres anteriores (abr-jun).
    assert periodo_anterior("2026-07", "2026-09") == ("2026-04", "2026-06")


def test_el_periodo_anterior_de_un_mes_es_el_mes_de_antes():
    assert periodo_anterior("2026-01", "2026-01") == ("2025-12", "2025-12")


def test_las_categorias_no_se_pisan_entre_tipos():
    assert "infraestructura" in CATEGORIAS["egreso"]
    assert "desarrollo_web" in CATEGORIAS["ingreso"]
    assert "infraestructura" not in CATEGORIAS["ingreso"]
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `python -m pytest tests/test_finanzas_service.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'services.finanzas'`

- [ ] **Step 3: Escribir la implementación mínima**

Crear `services/finanzas.py`:

```python
"""Las cuentas de la sección financiera.

Todo lo que puede dar un número mal vive acá y se testea sin Flask ni base:
conversión de moneda, aritmética de períodos, materialización de los fijos y
los agregados del panel. Las rutas solo validan y serializan.
"""

import logging

logger = logging.getLogger(__name__)

MONEDAS = ("USD", "UYU")

# Lista fija a propósito, no texto libre: con texto libre alcanza con escribir
# "Infra" una vez en lugar de "Infraestructura" para que el desglose por
# categoría se parta en dos sin que nadie lo note.
CATEGORIAS = {
    "egreso": ["infraestructura", "herramientas", "publicidad",
               "retiros", "impuestos", "servicios", "otros"],
    "ingreso": ["desarrollo_web", "software_medida", "mantenimiento",
                "marketing", "otros"],
}


def periodo_de(fecha: str) -> str:
    """'2026-09-20' -> '2026-09'."""
    return fecha[:7]


def a_usd(monto: float, moneda: str, tipo_cambio: float | None) -> float:
    """Convierte a dólares. El resultado se congela en `monto_usd`.

    Un movimiento en pesos sin tipo de cambio es un error, no un cero: guardarlo
    con monto_usd = 0 lo haría desaparecer de los totales sin avisar.
    """
    if moneda not in MONEDAS:
        raise ValueError(f"moneda desconocida: {moneda!r}")
    if moneda == "USD":
        return round(float(monto), 2)
    if not tipo_cambio or float(tipo_cambio) <= 0:
        raise ValueError("un movimiento en UYU necesita un tipo de cambio > 0")
    return round(float(monto) / float(tipo_cambio), 2)


def _a_indice(periodo: str) -> int:
    anio, mes = periodo.split("-")
    return int(anio) * 12 + (int(mes) - 1)


def _a_periodo(indice: int) -> str:
    return f"{indice // 12:04d}-{indice % 12 + 1:02d}"


def meses_entre(desde: str, hasta: str) -> list[str]:
    """Los períodos de `desde` a `hasta`, ambos inclusive. Al revés, vacío."""
    return [_a_periodo(i) for i in range(_a_indice(desde), _a_indice(hasta) + 1)]


def periodo_anterior(desde: str, hasta: str) -> tuple[str, str]:
    """El bloque inmediatamente anterior, del mismo largo. Para la variación."""
    largo = _a_indice(hasta) - _a_indice(desde) + 1
    fin = _a_indice(desde) - 1
    return _a_periodo(fin - largo + 1), _a_periodo(fin)
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_finanzas_service.py -v`
Expected: PASS, 12 tests

- [ ] **Step 5: Commit**

```bash
git add services/finanzas.py tests/test_finanzas_service.py
git commit -m "feat(finanzas): categorias, conversion a USD y aritmetica de periodos"
```

---

### Task 3: Materializar los fijos

**Files:**
- Modify: `services/finanzas.py`
- Test: `tests/test_finanzas_service.py` (se le agregan casos)

**Interfaces:**
- Consumes: `database.listar_recurrentes`, `database.crear_movimiento` (Task 1); `a_usd`, `meses_entre`, `periodo_de` (Task 2).
- Produces: `materializar_recurrentes(db_path: str, hoy: date | None = None) -> int` — devuelve cuántos movimientos creó.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `tests/test_finanzas_service.py`:

```python
import datetime as _dt

import pytest as _pytest

from database import (actualizar_movimiento, borrar_movimiento, crear_recurrente,
                      init_db, listar_movimientos)
from services.finanzas import materializar_recurrentes

_HOY = _dt.date(2026, 9, 8)


@_pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "f.db")
    init_db(ruta)
    return ruta


def _fijo(db, **extra):
    campos = dict(tipo="egreso", concepto="Fly", categoria="infraestructura",
                  monto=4.18, moneda="USD", dia_del_mes=20, desde="2026-07")
    campos.update(extra)
    return crear_recurrente(db, **campos)


def test_materializa_un_movimiento_por_mes_desde_el_inicio(db):
    _fijo(db)  # desde julio, hoy es septiembre
    assert materializar_recurrentes(db, hoy=_HOY) == 3
    periodos = sorted(m["periodo"] for m in listar_movimientos(db))
    assert periodos == ["2026-07", "2026-08", "2026-09"]


def test_el_mes_en_curso_se_genera_aunque_el_dia_no_haya_llegado(db):
    # Hoy es 8 de septiembre y el fijo cae el 20: se genera igual, para que el
    # mes muestre su costo fijo completo.
    _fijo(db, desde="2026-09")
    materializar_recurrentes(db, hoy=_HOY)
    assert listar_movimientos(db)[0]["fecha"] == "2026-09-20"


def test_correrlo_dos_veces_no_duplica(db):
    """El caso del deploy. Cada deploy reinicia la máquina."""
    _fijo(db)
    materializar_recurrentes(db, hoy=_HOY)
    assert materializar_recurrentes(db, hoy=_HOY) == 0
    assert len(listar_movimientos(db)) == 3


def test_no_genera_periodos_futuros(db):
    _fijo(db, desde="2026-07", hasta="2027-12")
    materializar_recurrentes(db, hoy=_HOY)
    assert max(m["periodo"] for m in listar_movimientos(db)) == "2026-09"


def test_respeta_hasta(db):
    _fijo(db, desde="2026-07", hasta="2026-08")
    assert materializar_recurrentes(db, hoy=_HOY) == 2


def test_un_fijo_apagado_no_genera_nada(db):
    _fijo(db, activo=0)
    assert materializar_recurrentes(db, hoy=_HOY) == 0


def test_un_fijo_en_pesos_usa_su_tipo_de_cambio(db):
    _fijo(db, desde="2026-09", monto=40000, moneda="UYU", tipo_cambio=40.0)
    materializar_recurrentes(db, hoy=_HOY)
    assert listar_movimientos(db)[0]["monto_usd"] == 1000.0


def test_un_fijo_en_pesos_sin_tipo_de_cambio_se_saltea_sin_romper(db):
    """Un fijo mal cargado no puede tumbar la materialización de los demás."""
    _fijo(db, concepto="Roto", desde="2026-09", moneda="UYU", tipo_cambio=None)
    _fijo(db, concepto="Fly", desde="2026-09")
    assert materializar_recurrentes(db, hoy=_HOY) == 1
    assert listar_movimientos(db)[0]["concepto"] == "Fly"


def test_editar_un_movimiento_generado_sobrevive_a_rematerializar(db):
    _fijo(db, desde="2026-09")
    materializar_recurrentes(db, hoy=_HOY)
    mid = listar_movimientos(db)[0]["id"]
    actualizar_movimiento(db, mid, monto=9.99, monto_usd=9.99)
    materializar_recurrentes(db, hoy=_HOY)
    assert listar_movimientos(db)[0]["monto_usd"] == 9.99


def test_borrar_un_fijo_deja_vivos_los_movimientos_que_ya_genero(db):
    """Son plata que se gastó. Borrar la definición no borra la historia."""
    from database import borrar_recurrente
    rid = _fijo(db)  # desde julio
    materializar_recurrentes(db, hoy=_HOY)
    assert len(listar_movimientos(db)) == 3
    borrar_recurrente(db, rid)
    assert len(listar_movimientos(db)) == 3


def test_un_movimiento_generado_y_anulado_no_reaparece(db):
    """Si se borrara de verdad, el fijo lo regeneraría y el gasto volvería solo."""
    _fijo(db, desde="2026-09")
    materializar_recurrentes(db, hoy=_HOY)
    mid = listar_movimientos(db)[0]["id"]
    actualizar_movimiento(db, mid, anulado=1)
    assert materializar_recurrentes(db, hoy=_HOY) == 0
    assert listar_movimientos(db) == []
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python -m pytest tests/test_finanzas_service.py -k materializ -v`
Expected: FAIL con `ImportError: cannot import name 'materializar_recurrentes'`

- [ ] **Step 3: Escribir la implementación**

Agregar a `services/finanzas.py` (arriba, junto a los otros imports):

```python
import sqlite3
from datetime import date
```

Y al final del archivo:

```python
def materializar_recurrentes(db_path: str, hoy: date | None = None) -> int:
    """Crea los movimientos que falten de cada fijo activo. Devuelve cuántos creó.

    Idempotente: se apoya en el índice único (recurrente_id, periodo), así que
    correrlo mil veces produce exactamente un movimiento por fijo y por mes.
    Esa es la guarda que exige la regla 3 de COORDINACION.md — cada deploy
    reinicia la máquina, y sin esto duplicaría los gastos del mes.

    Se llama perezosamente desde GET /api/finanzas/resumen. No hay hilo de
    arranque a propósito: los jobs de boot de este repo ya provocaron una tanda
    de mails reales, y un hilo más es una cosa más que puede fallar sin que
    nadie mire.
    """
    from database import crear_movimiento, listar_recurrentes

    hoy = hoy or date.today()
    mes_actual = f"{hoy.year:04d}-{hoy.month:02d}"
    creados = 0

    for fijo in listar_recurrentes(db_path, solo_activos=True):
        fin = min(fijo["hasta"], mes_actual) if fijo["hasta"] else mes_actual
        try:
            monto_usd = a_usd(fijo["monto"], fijo["moneda"], fijo["tipo_cambio"])
        except ValueError as e:
            # Un fijo mal cargado no puede tumbar la materialización del resto.
            logger.warning("fijo id=%s (%s) se saltea: %s",
                           fijo["id"], fijo["concepto"], e)
            continue

        dia = min(max(int(fijo["dia_del_mes"] or 1), 1), 28)
        for periodo in meses_entre(fijo["desde"], fin):
            try:
                crear_movimiento(
                    db_path,
                    tipo=fijo["tipo"],
                    fecha=f"{periodo}-{dia:02d}",
                    periodo=periodo,
                    concepto=fijo["concepto"],
                    categoria=fijo["categoria"],
                    monto=fijo["monto"],
                    moneda=fijo["moneda"],
                    tipo_cambio=fijo["tipo_cambio"],
                    monto_usd=monto_usd,
                    client_id=fijo["client_id"],
                    recurrente_id=fijo["id"],
                    created_by_name="fijo",
                )
                creados += 1
            except sqlite3.IntegrityError:
                # Ya existía ese (recurrente_id, periodo). Es el camino normal:
                # todas las corridas después de la primera pasan por acá.
                pass

    return creados
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_finanzas_service.py -v`
Expected: PASS, 23 tests

- [ ] **Step 5: Commit**

```bash
git add services/finanzas.py tests/test_finanzas_service.py
git commit -m "feat(finanzas): materializar los gastos fijos mes a mes"
```

---

### Task 4: El resumen del panel

**Files:**
- Modify: `services/finanzas.py`
- Test: `tests/test_finanzas_service.py` (se le agregan casos)

**Interfaces:**
- Consumes: `database.listar_movimientos` (Task 1); `meses_entre`, `periodo_anterior` (Task 2).
- Produces: `resumen(db_path: str, desde: str, hasta: str) -> dict` con esta forma exacta, que el JS del panel consume tal cual:

```python
{
  "desde": "2026-07", "hasta": "2026-09",
  "kpis": {"ingresos_usd": 3200.0, "egresos_usd": 412.5, "neto_usd": 2787.5,
           "ingresos_previos_usd": 1800.0, "egresos_previos_usd": 390.0,
           "neto_previo_usd": 1410.0},
  "serie": [{"periodo": "2026-07", "ingresos_usd": 0.0, "egresos_usd": 137.5,
             "neto_usd": -137.5}, ...],
  "por_categoria": [{"tipo": "egreso", "categoria": "infraestructura",
                     "total_usd": 12.54}, ...],
  "por_cliente": [{"client_id": 104706, "nombre": "Tienda Electrónica",
                   "total_usd": 800.0}, ...],
}
```

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `tests/test_finanzas_service.py`:

```python
from database import crear_movimiento
from services.finanzas import resumen


def _cargar(db, tipo, periodo, monto_usd, categoria="otros", client_id=None):
    return crear_movimiento(db, tipo=tipo, fecha=f"{periodo}-15", periodo=periodo,
                            concepto="x", categoria=categoria, monto=monto_usd,
                            moneda="USD", monto_usd=monto_usd, client_id=client_id)


def test_los_kpis_suman_el_periodo_pedido(db):
    _cargar(db, "ingreso", "2026-09", 800)
    _cargar(db, "egreso", "2026-09", 100)
    _cargar(db, "ingreso", "2026-06", 5000)  # fuera del rango
    r = resumen(db, "2026-09", "2026-09")
    assert r["kpis"]["ingresos_usd"] == 800
    assert r["kpis"]["egresos_usd"] == 100
    assert r["kpis"]["neto_usd"] == 700


def test_los_kpis_traen_el_periodo_anterior_para_la_variacion(db):
    _cargar(db, "ingreso", "2026-09", 800)
    _cargar(db, "ingreso", "2026-08", 500)
    r = resumen(db, "2026-09", "2026-09")
    assert r["kpis"]["ingresos_previos_usd"] == 500


def test_mezcla_monedas_convirtiendo_a_dolares(db):
    crear_movimiento(db, tipo="ingreso", fecha="2026-09-01", periodo="2026-09",
                     concepto="cobro en pesos", categoria="desarrollo_web",
                     monto=40000, moneda="UYU", tipo_cambio=40.0, monto_usd=1000.0)
    crear_movimiento(db, tipo="ingreso", fecha="2026-09-02", periodo="2026-09",
                     concepto="cobro en dolares", categoria="desarrollo_web",
                     monto=500, moneda="USD", monto_usd=500.0)
    assert resumen(db, "2026-09", "2026-09")["kpis"]["ingresos_usd"] == 1500.0


def test_la_serie_trae_todos_los_meses_incluso_los_vacios(db):
    _cargar(db, "egreso", "2026-09", 100)
    serie = resumen(db, "2026-07", "2026-09")["serie"]
    assert [p["periodo"] for p in serie] == ["2026-07", "2026-08", "2026-09"]
    assert serie[0]["egresos_usd"] == 0.0


def test_el_desglose_por_categoria_agrupa(db):
    _cargar(db, "egreso", "2026-09", 10, categoria="infraestructura")
    _cargar(db, "egreso", "2026-09", 5, categoria="infraestructura")
    _cargar(db, "egreso", "2026-09", 20, categoria="herramientas")
    por_cat = resumen(db, "2026-09", "2026-09")["por_categoria"]
    infra = [c for c in por_cat if c["categoria"] == "infraestructura"][0]
    assert infra["total_usd"] == 15


def test_los_anulados_no_cuentan_en_ningun_agregado(db):
    from database import actualizar_movimiento
    mid = _cargar(db, "egreso", "2026-09", 100)
    actualizar_movimiento(db, mid, anulado=1)
    r = resumen(db, "2026-09", "2026-09")
    assert r["kpis"]["egresos_usd"] == 0
    assert r["por_categoria"] == []
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python -m pytest tests/test_finanzas_service.py -k resumen -v`
Expected: FAIL con `ImportError: cannot import name 'resumen'`

- [ ] **Step 3: Escribir la implementación**

Al final de `services/finanzas.py`:

```python
def _totales(movimientos: list[dict]) -> tuple[float, float]:
    ingresos = sum(m["monto_usd"] for m in movimientos if m["tipo"] == "ingreso")
    egresos = sum(m["monto_usd"] for m in movimientos if m["tipo"] == "egreso")
    return round(ingresos, 2), round(egresos, 2)


def resumen(db_path: str, desde: str, hasta: str) -> dict:
    """KPIs, serie mensual y desgloses del período. `desde`/`hasta` inclusive.

    No materializa: eso lo hace la ruta antes de llamar acá, para que el
    servicio se pueda testear sin efectos.
    """
    from database import listar_movimientos

    movs = listar_movimientos(db_path, desde=desde, hasta=hasta)
    ingresos, egresos = _totales(movs)

    prev_desde, prev_hasta = periodo_anterior(desde, hasta)
    prev = listar_movimientos(db_path, desde=prev_desde, hasta=prev_hasta)
    ingresos_prev, egresos_prev = _totales(prev)

    por_periodo: dict[str, list[dict]] = {p: [] for p in meses_entre(desde, hasta)}
    for m in movs:
        por_periodo.setdefault(m["periodo"], []).append(m)

    serie = []
    for periodo in meses_entre(desde, hasta):
        i, e = _totales(por_periodo.get(periodo, []))
        serie.append({"periodo": periodo, "ingresos_usd": i,
                      "egresos_usd": e, "neto_usd": round(i - e, 2)})

    cat: dict[tuple[str, str], float] = {}
    for m in movs:
        clave = (m["tipo"], m["categoria"])
        cat[clave] = cat.get(clave, 0.0) + m["monto_usd"]
    por_categoria = [{"tipo": t, "categoria": c, "total_usd": round(v, 2)}
                     for (t, c), v in cat.items()]
    por_categoria.sort(key=lambda x: x["total_usd"], reverse=True)

    por_cliente = _ingresos_por_cliente(db_path, movs)

    return {
        "desde": desde, "hasta": hasta,
        "kpis": {
            "ingresos_usd": ingresos,
            "egresos_usd": egresos,
            "neto_usd": round(ingresos - egresos, 2),
            "ingresos_previos_usd": ingresos_prev,
            "egresos_previos_usd": egresos_prev,
            "neto_previo_usd": round(ingresos_prev - egresos_prev, 2),
        },
        "serie": serie,
        "por_categoria": por_categoria,
        "por_cliente": por_cliente,
    }


def _ingresos_por_cliente(db_path: str, movs: list[dict]) -> list[dict]:
    """Ingresos agrupados por cliente. Los sin atribuir no aparecen."""
    from database import _connect

    totales: dict[int, float] = {}
    for m in movs:
        if m["tipo"] != "ingreso" or not m["client_id"]:
            continue
        totales[m["client_id"]] = totales.get(m["client_id"], 0.0) + m["monto_usd"]
    if not totales:
        return []

    marcas = ", ".join("?" for _ in totales)
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            f"SELECT id, name FROM businesses WHERE id IN ({marcas})",
            list(totales)).fetchall()
    finally:
        conn.close()
    nombres = {f["id"]: f["name"] for f in filas}

    salida = [{"client_id": cid, "nombre": nombres.get(cid, f"#{cid}"),
               "total_usd": round(v, 2)} for cid, v in totales.items()]
    salida.sort(key=lambda x: x["total_usd"], reverse=True)
    return salida
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_finanzas_service.py -v`
Expected: PASS, 29 tests

- [ ] **Step 5: Commit**

```bash
git add services/finanzas.py tests/test_finanzas_service.py
git commit -m "feat(finanzas): resumen mensual con desgloses por categoria y cliente"
```

---

### Task 5: `require_panel`

**Files:**
- Modify: `services/auth.py` (al final, después de `require_admin`)
- Test: `tests/test_finanzas_rutas.py`

**Interfaces:**
- Consumes: `is_admin(db_path, user_id)`, `get_user_by_id` — ya existen en `services/auth.py`.
- Produces: `require_panel(db_path: str, panel: str)` — devuelve una respuesta `(json, 403)` o `None` si puede seguir. Mismo contrato que `require_admin`.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_finanzas_rutas.py`:

```python
"""Permisos y endpoints de la sección financiera.

El `panel_access` del CRM solo escondía el ítem del menú: nada frenaba un
fetch de un usuario logueado sin ese panel. Para los leads es tolerable; para
la plata no.
"""

import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    db = str(tmp_path / "a.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _rol(db, nombre, paneles):
    conn = sqlite3.connect(db)
    try:
        fila = conn.execute("SELECT id FROM roles WHERE name=?", (nombre,)).fetchone()
        if fila:
            conn.execute("UPDATE roles SET panel_access=? WHERE id=?",
                         (json.dumps(paneles), fila[0]))
            conn.commit()
            return fila[0]
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                           (nombre, json.dumps(paneles))).lastrowid
        conn.commit()
        return rid
    finally:
        conn.close()


def _usuario(db, email, role_id):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET role_id=? WHERE id=?", (role_id, uid))
    conn.commit()
    conn.close()
    return uid


def _cli(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "test"
    return c


def test_sin_el_panel_finanzas_da_403(app):
    db = app.config["_DB"]
    uid = _usuario(db, "caller@scalerics.com", _rol(db, "Caller", ["cola"]))
    assert _cli(app, uid).get("/api/finanzas/resumen").status_code == 403


def test_con_el_panel_finanzas_entra(app):
    db = app.config["_DB"]
    uid = _usuario(db, "socio@scalerics.com",
                   _rol(db, "Socio", ["cola", "finanzas"]))
    assert _cli(app, uid).get("/api/finanzas/resumen").status_code == 200


def test_un_admin_entra_aunque_su_rol_no_lo_liste(app):
    db = app.config["_DB"]
    uid = _usuario(db, "raiz@scalerics.com", _rol(db, "Caller", ["cola"]))
    assert _cli(app, uid).get("/api/finanzas/resumen").status_code == 200


def test_sin_sesion_no_entra(app):
    r = app.test_client().get("/api/finanzas/resumen")
    assert r.status_code in (401, 302)
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `python -m pytest tests/test_finanzas_rutas.py -v`
Expected: FAIL — todas dan 404, la ruta no existe todavía.

- [ ] **Step 3: Escribir `require_panel`**

Al final de `services/auth.py`:

```python
def tiene_panel(db_path: str, user_id, panel: str) -> bool:
    """Si el usuario puede ver ese panel. Un admin ve todos."""
    if is_admin(db_path, user_id):
        return True
    if not user_id:
        return False
    conn = _db_connect(db_path)
    try:
        fila = conn.execute(
            "SELECT r.panel_access FROM users u "
            "LEFT JOIN roles r ON u.role_id = r.id WHERE u.id = ?",
            (user_id,)).fetchone()
    finally:
        conn.close()
    if not fila or not fila["panel_access"]:
        return False
    try:
        paneles = json.loads(fila["panel_access"])
    except (ValueError, TypeError):
        return False
    return isinstance(paneles, list) and panel in paneles


def require_panel(db_path: str, panel: str):
    """403 si el usuario de la sesión no tiene ese panel; None si puede seguir.

    Existe porque `panel_access` solo escondía el ítem del menú: nada frenaba
    un fetch directo a la API. Uso:

        @finanzas_bp.before_request
        def _candado():
            return require_panel(current_app.config["DB_PATH"], "finanzas")
    """
    if not tiene_panel(db_path, session.get("user_id"), panel):
        return jsonify({"ok": False, "error": "No autorizado"}), 403
    return None
```

Verificar que `import json` esté arriba en `services/auth.py`; si no está, agregarlo.

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_finanzas_rutas.py -v`
Expected: siguen fallando con 404 — falta la ruta, que llega en la Task 6. Esto es esperado.

Correr en cambio la suite entera para confirmar que `auth.py` no rompió nada:

Run: `python -m pytest -q`
Expected: PASS salvo los 4 de `test_finanzas_rutas.py`.

- [ ] **Step 5: Commit**

```bash
git add services/auth.py tests/test_finanzas_rutas.py
git commit -m "feat(auth): require_panel para candar una API por panel"
```

---

### Task 6: Las rutas

**Files:**
- Create: `routes/finanzas.py`
- Modify: `dashboard.py` — el import de blueprints y la tupla del `for bp in (...)` dentro de `create_app`. Ubicarlos con `grep -n "linkedin_bp" dashboard.py`
- Test: `tests/test_finanzas_rutas.py` (se le agregan casos)

**Interfaces:**
- Consumes: todo lo de las Tasks 1-5. `database.log_activity(db, user_name, accion, entidad, entidad_id, titulo, detalle, user_id=...)` — ya existe, mismo uso que en `routes/tasks.py`.
- Produces: `finanzas_bp` (Blueprint) y los 10 endpoints de la tabla de la spec.

- [ ] **Step 1: Escribir los tests que faltan**

Agregar al final de `tests/test_finanzas_rutas.py`:

```python
@pytest.fixture
def cli(app):
    db = app.config["_DB"]
    uid = _usuario(db, "socio@scalerics.com",
                   _rol(db, "Socio", ["cola", "finanzas"]))
    return _cli(app, uid)


def test_crear_un_movimiento_en_dolares(cli):
    r = cli.post("/api/finanzas/movimientos", json={
        "tipo": "egreso", "fecha": "2026-09-20", "concepto": "Fly",
        "categoria": "infraestructura", "monto": 4.18, "moneda": "USD"})
    assert r.status_code == 201
    assert r.get_json()["ok"] is True

    lista = cli.get("/api/finanzas/movimientos").get_json()
    assert lista[0]["monto_usd"] == 4.18
    assert lista[0]["periodo"] == "2026-09"


def test_crear_en_pesos_congela_el_monto_en_dolares(cli):
    cli.post("/api/finanzas/movimientos", json={
        "tipo": "ingreso", "fecha": "2026-09-01", "concepto": "Cobro",
        "categoria": "desarrollo_web", "monto": 40000, "moneda": "UYU",
        "tipo_cambio": 40})
    assert cli.get("/api/finanzas/movimientos").get_json()[0]["monto_usd"] == 1000.0


def test_pesos_sin_tipo_de_cambio_da_400(cli):
    r = cli.post("/api/finanzas/movimientos", json={
        "tipo": "ingreso", "fecha": "2026-09-01", "concepto": "Cobro",
        "categoria": "desarrollo_web", "monto": 40000, "moneda": "UYU"})
    assert r.status_code == 400


def test_una_categoria_que_no_existe_da_400(cli):
    r = cli.post("/api/finanzas/movimientos", json={
        "tipo": "egreso", "fecha": "2026-09-01", "concepto": "x",
        "categoria": "inventada", "monto": 10, "moneda": "USD"})
    assert r.status_code == 400


def test_una_categoria_de_ingreso_en_un_egreso_da_400(cli):
    r = cli.post("/api/finanzas/movimientos", json={
        "tipo": "egreso", "fecha": "2026-09-01", "concepto": "x",
        "categoria": "desarrollo_web", "monto": 10, "moneda": "USD"})
    assert r.status_code == 400


def test_borrar_un_movimiento_a_mano_lo_borra(cli):
    cli.post("/api/finanzas/movimientos", json={
        "tipo": "egreso", "fecha": "2026-09-20", "concepto": "Dominio",
        "categoria": "servicios", "monto": 15, "moneda": "USD"})
    mid = cli.get("/api/finanzas/movimientos").get_json()[0]["id"]
    assert cli.delete(f"/api/finanzas/movimientos/{mid}").status_code == 200
    assert cli.get("/api/finanzas/movimientos").get_json() == []


def test_borrar_un_movimiento_de_un_fijo_lo_anula_y_no_reaparece(cli):
    cli.post("/api/finanzas/recurrentes", json={
        "tipo": "egreso", "concepto": "Fly", "categoria": "infraestructura",
        "monto": 4.18, "moneda": "USD", "dia_del_mes": 20, "desde": "2026-09"})
    cli.get("/api/finanzas/resumen")  # materializa
    mid = cli.get("/api/finanzas/movimientos").get_json()[0]["id"]
    cli.delete(f"/api/finanzas/movimientos/{mid}")
    cli.get("/api/finanzas/resumen")  # vuelve a materializar
    assert cli.get("/api/finanzas/movimientos").get_json() == []


def test_el_resumen_materializa_los_fijos(cli):
    cli.post("/api/finanzas/recurrentes", json={
        "tipo": "egreso", "concepto": "Fly", "categoria": "infraestructura",
        "monto": 4.18, "moneda": "USD", "dia_del_mes": 1, "desde": "2026-09"})
    assert cli.get("/api/finanzas/resumen").status_code == 200
    assert cli.get("/api/finanzas/movimientos").get_json() != []


def test_las_categorias_se_sirven_al_front(cli):
    cats = cli.get("/api/finanzas/categorias").get_json()
    assert "infraestructura" in cats["egreso"]
    assert "desarrollo_web" in cats["ingreso"]


def test_un_dia_del_mes_mayor_a_28_da_400(cli):
    r = cli.post("/api/finanzas/recurrentes", json={
        "tipo": "egreso", "concepto": "x", "categoria": "servicios",
        "monto": 10, "moneda": "USD", "dia_del_mes": 31, "desde": "2026-09"})
    assert r.status_code == 400
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python -m pytest tests/test_finanzas_rutas.py -v`
Expected: FAIL, 404 en todo.

- [ ] **Step 3: Escribir las rutas**

Crear `routes/finanzas.py`:

```python
"""Endpoints de la sección financiera.

Las rutas son finas: validan la entrada, llaman a `services/finanzas.py` y
serializan. Ninguna cuenta se hace acá.
"""

from datetime import date

from flask import Blueprint, current_app, jsonify, request, session

from database import (actualizar_movimiento, actualizar_recurrente,
                      borrar_movimiento, borrar_recurrente, crear_movimiento,
                      crear_recurrente, get_movimiento, get_recurrente,
                      listar_movimientos, listar_recurrentes, log_activity)
from services.auth import require_panel
from services.finanzas import (CATEGORIAS, MONEDAS, a_usd,
                               materializar_recurrentes, periodo_de, resumen)

finanzas_bp = Blueprint("finanzas", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@finanzas_bp.before_request
def _candado():
    """Una línea, cubre todo el blueprint.

    Con un decorador por ruta, agregar un endpoint el mes que viene y olvidarse
    del candado deja la plata abierta. Así no hay forma de olvidarse.
    """
    return require_panel(_db(), "finanzas")


def _quien() -> tuple[int | None, str]:
    return session.get("user_id"), session.get("user_name", "sistema")


def _validar_movimiento(data: dict) -> tuple[dict | None, str | None]:
    """Devuelve (campos listos para guardar, None) o (None, mensaje de error)."""
    tipo = data.get("tipo")
    if tipo not in CATEGORIAS:
        return None, "tipo tiene que ser 'ingreso' o 'egreso'"

    categoria = data.get("categoria")
    if categoria not in CATEGORIAS[tipo]:
        return None, f"categoría inválida para un {tipo}: {categoria!r}"

    fecha = (data.get("fecha") or "").strip()
    if len(fecha) != 10 or fecha[4] != "-" or fecha[7] != "-":
        return None, "fecha tiene que ser 'YYYY-MM-DD'"

    concepto = (data.get("concepto") or "").strip()
    if not concepto:
        return None, "concepto es obligatorio"

    moneda = data.get("moneda")
    if moneda not in MONEDAS:
        return None, f"moneda tiene que ser una de {MONEDAS}"

    try:
        monto = float(data.get("monto"))
    except (TypeError, ValueError):
        return None, "monto tiene que ser un número"
    if monto <= 0:
        return None, "monto tiene que ser mayor que cero"

    tipo_cambio = data.get("tipo_cambio")
    try:
        monto_usd = a_usd(monto, moneda, tipo_cambio)
    except ValueError as e:
        return None, str(e)

    return {
        "tipo": tipo, "fecha": fecha, "periodo": periodo_de(fecha),
        "concepto": concepto, "categoria": categoria, "monto": monto,
        "moneda": moneda,
        "tipo_cambio": float(tipo_cambio) if moneda == "UYU" else None,
        "monto_usd": monto_usd,
        "client_id": data.get("client_id") or None,
        "budget_id": data.get("budget_id") or None,
        "notas": (data.get("notas") or "").strip() or None,
    }, None


# ── movimientos ───────────────────────────────────────────────────────────────

@finanzas_bp.route("/api/finanzas/movimientos", methods=["GET"])
def api_listar_movimientos():
    return jsonify(listar_movimientos(
        _db(),
        desde=request.args.get("desde"),
        hasta=request.args.get("hasta"),
        tipo=request.args.get("tipo"),
        categoria=request.args.get("categoria"),
        client_id=request.args.get("client_id", type=int),
    ))


@finanzas_bp.route("/api/finanzas/movimientos", methods=["POST"])
def api_crear_movimiento():
    campos, error = _validar_movimiento(request.get_json() or {})
    if error:
        return jsonify({"ok": False, "error": error}), 400
    uid, nombre = _quien()
    campos["created_by_id"] = uid
    campos["created_by_name"] = nombre
    db = _db()
    mid = crear_movimiento(db, **campos)
    log_activity(db, nombre, "finanzas_movimiento_creado", "finanzas", mid,
                 campos["concepto"],
                 f"{campos['tipo']} {campos['moneda']} {campos['monto']}",
                 user_id=uid)
    return jsonify({"ok": True, "id": mid}), 201


@finanzas_bp.route("/api/finanzas/movimientos/<int:mov_id>", methods=["PUT"])
def api_actualizar_movimiento(mov_id):
    db = _db()
    if not get_movimiento(db, mov_id):
        return jsonify({"ok": False, "error": "no existe"}), 404
    campos, error = _validar_movimiento(request.get_json() or {})
    if error:
        return jsonify({"ok": False, "error": error}), 400
    actualizar_movimiento(db, mov_id, **campos)
    uid, nombre = _quien()
    log_activity(db, nombre, "finanzas_movimiento_editado", "finanzas", mov_id,
                 campos["concepto"], "", user_id=uid)
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/movimientos/<int:mov_id>", methods=["DELETE"])
def api_borrar_movimiento(mov_id):
    db = _db()
    mov = get_movimiento(db, mov_id)
    if not mov:
        return jsonify({"ok": False, "error": "no existe"}), 404
    uid, nombre = _quien()
    if mov["recurrente_id"]:
        # Un DELETE liberaría el par (recurrente_id, periodo) y el fijo lo
        # regeneraría en la próxima materialización: el gasto volvería solo.
        actualizar_movimiento(db, mov_id, anulado=1)
    else:
        borrar_movimiento(db, mov_id)
    log_activity(db, nombre, "finanzas_movimiento_borrado", "finanzas", mov_id,
                 mov["concepto"], "", user_id=uid)
    return jsonify({"ok": True})


# ── fijos ─────────────────────────────────────────────────────────────────────

def _validar_recurrente(data: dict) -> tuple[dict | None, str | None]:
    tipo = data.get("tipo")
    if tipo not in CATEGORIAS:
        return None, "tipo tiene que ser 'ingreso' o 'egreso'"
    if data.get("categoria") not in CATEGORIAS[tipo]:
        return None, f"categoría inválida para un {tipo}"
    concepto = (data.get("concepto") or "").strip()
    if not concepto:
        return None, "concepto es obligatorio"
    moneda = data.get("moneda")
    if moneda not in MONEDAS:
        return None, f"moneda tiene que ser una de {MONEDAS}"
    try:
        monto = float(data.get("monto"))
    except (TypeError, ValueError):
        return None, "monto tiene que ser un número"
    if monto <= 0:
        return None, "monto tiene que ser mayor que cero"
    try:
        a_usd(monto, moneda, data.get("tipo_cambio"))
    except ValueError as e:
        return None, str(e)

    dia = data.get("dia_del_mes", 1)
    try:
        dia = int(dia)
    except (TypeError, ValueError):
        return None, "dia_del_mes tiene que ser un número"
    if not 1 <= dia <= 28:
        # Se topea en 28 a propósito: no hay 30 de febrero, y no hace falta
        # lógica de "último día del mes" para un caso que no existe.
        return None, "dia_del_mes tiene que estar entre 1 y 28"

    desde = (data.get("desde") or "").strip()
    if len(desde) != 7 or desde[4] != "-":
        return None, "desde tiene que ser 'YYYY-MM'"
    hasta = (data.get("hasta") or "").strip() or None
    if hasta and (len(hasta) != 7 or hasta[4] != "-"):
        return None, "hasta tiene que ser 'YYYY-MM'"

    return {
        "tipo": tipo, "concepto": concepto, "categoria": data["categoria"],
        "monto": monto, "moneda": moneda,
        "tipo_cambio": float(data["tipo_cambio"]) if moneda == "UYU" else None,
        "dia_del_mes": dia, "desde": desde, "hasta": hasta,
        "activo": 1 if data.get("activo", 1) else 0,
        "client_id": data.get("client_id") or None,
        "notas": (data.get("notas") or "").strip() or None,
    }, None


@finanzas_bp.route("/api/finanzas/recurrentes", methods=["GET"])
def api_listar_recurrentes():
    return jsonify(listar_recurrentes(_db()))


@finanzas_bp.route("/api/finanzas/recurrentes", methods=["POST"])
def api_crear_recurrente():
    campos, error = _validar_recurrente(request.get_json() or {})
    if error:
        return jsonify({"ok": False, "error": error}), 400
    db = _db()
    rid = crear_recurrente(db, **campos)
    uid, nombre = _quien()
    log_activity(db, nombre, "finanzas_fijo_creado", "finanzas", rid,
                 campos["concepto"], "", user_id=uid)
    return jsonify({"ok": True, "id": rid}), 201


@finanzas_bp.route("/api/finanzas/recurrentes/<int:rec_id>", methods=["PUT"])
def api_actualizar_recurrente(rec_id):
    db = _db()
    if not get_recurrente(db, rec_id):
        return jsonify({"ok": False, "error": "no existe"}), 404
    campos, error = _validar_recurrente(request.get_json() or {})
    if error:
        return jsonify({"ok": False, "error": error}), 400
    actualizar_recurrente(db, rec_id, **campos)
    uid, nombre = _quien()
    log_activity(db, nombre, "finanzas_fijo_editado", "finanzas", rec_id,
                 campos["concepto"], "", user_id=uid)
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/recurrentes/<int:rec_id>", methods=["DELETE"])
def api_borrar_recurrente(rec_id):
    db = _db()
    rec = get_recurrente(db, rec_id)
    if not rec:
        return jsonify({"ok": False, "error": "no existe"}), 404
    borrar_recurrente(db, rec_id)
    uid, nombre = _quien()
    log_activity(db, nombre, "finanzas_fijo_borrado", "finanzas", rec_id,
                 rec["concepto"],
                 "los movimientos que ya generó quedan", user_id=uid)
    return jsonify({"ok": True})


# ── resumen y catálogos ───────────────────────────────────────────────────────

@finanzas_bp.route("/api/finanzas/resumen")
def api_resumen():
    """KPIs, serie y desgloses. Materializa los fijos antes de calcular.

    Acá es donde se materializa, perezosamente. No hay hilo de arranque: los
    jobs de boot de este repo ya provocaron una tanda de mails reales.
    """
    db = _db()
    hoy = date.today()
    mes_actual = f"{hoy.year:04d}-{hoy.month:02d}"
    desde = request.args.get("desde") or mes_actual
    hasta = request.args.get("hasta") or mes_actual
    materializar_recurrentes(db, hoy=hoy)
    return jsonify(resumen(db, desde, hasta))


@finanzas_bp.route("/api/finanzas/categorias")
def api_categorias():
    return jsonify(CATEGORIAS)
```

- [ ] **Step 4: Registrar el blueprint**

En `dashboard.py`, junto a los otros imports de rutas (buscar `from routes.linkedin import linkedin_bp`):

```python
from routes.finanzas import finanzas_bp
```

Y en `create_app`, agregar `finanzas_bp` a la tupla del `for bp in (...)`:

```python
    for bp in (leads_bp, demos_bp, calendar_bp, wa_bp, pipeline_bp, tasks_bp, budgets_bp, tokens_bp, meta_bp, calendly_bp, notion_bp, projects_bp,
                notion_clients_bp, resend_bp, linkedin_bp, finanzas_bp):
```

- [ ] **Step 5: Correr los tests**

Run: `python -m pytest tests/test_finanzas_rutas.py -v`
Expected: PASS, 14 tests

- [ ] **Step 6: Correr la suite entera**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add routes/finanzas.py dashboard.py tests/test_finanzas_rutas.py
git commit -m "feat(finanzas): endpoints de movimientos, fijos y resumen"
```

---

### Task 7: Registrar el panel en el nav

**Files:**
- Modify: `dashboard.py` — siete lugares (seis acá, el de `database.py` ya se hizo en la Task 1)
- Test: `tests/test_finanzas_panel.py`

**Interfaces:**
- Consumes: `/api/finanzas/resumen` (Task 6).
- Produces: la función JS `loadFinanzas()` (por ahora un stub que se completa en la Task 8) y el div `#finanzas-panel`.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_finanzas_panel.py`:

```python
"""El panel Finanzas tiene que estar registrado en los siete lugares.

Dos de los siete fallan en silencio si se olvidan: sin la entrada en el
ALL_PANELS del editor de roles no se le puede asignar a nadie, y sin el
_grant_panel_to_existing_roles en database.py no lo ve nadie en producción,
donde la tabla `roles` ya tiene filas. Es exactamente lo que pasó con `meta`.
"""

import re
from pathlib import Path

import dashboard

HTML = dashboard.DASHBOARD_HTML


def test_el_item_del_nav_existe():
    assert 'id="nav-finanzas"' in HTML
    assert "showPanel('finanzas')" in HTML


def test_el_div_del_panel_existe():
    assert 'id="finanzas-panel"' in HTML


def test_show_panel_llama_a_load_finanzas():
    assert "if (name === 'finanzas') loadFinanzas();" in HTML


def test_esta_en_los_dos_all_panels():
    """Uno es el del dashboard, el otro el del editor de roles."""
    apariciones = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", HTML)
    assert len(apariciones) == 2, "cambió la cantidad de ALL_PANELS"
    for lista in apariciones:
        assert "'finanzas'" in lista


def test_tiene_etiqueta_en_el_editor_de_roles():
    """Sin esto el panel existe pero no se le puede asignar a nadie.

    Busca dentro de PANEL_LABELS, no en todo el HTML: NAV_LABELS también tiene
    una entrada 'finanzas' y haría pasar el test sin que el editor de roles la
    tenga.
    """
    etiquetas = re.search(r"const PANEL_LABELS = \{([^}]*)\}", HTML).group(1)
    assert "finanzas:'Finanzas'" in etiquetas.replace(" ", "")


def test_esta_en_la_navegacion_mobile():
    """NAV_ICONS y NAV_LABELS son objetos distintos: _buildMobileNav usa los dos."""
    prioridad = re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", HTML).group(1)
    assert "'finanzas'" in prioridad
    iconos = re.search(r"const NAV_ICONS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "finanzas:'wallet'" in iconos.replace(" ", "")
    etiquetas = re.search(r"const NAV_LABELS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "finanzas:'Finanzas'" in etiquetas.replace(" ", "")


def test_database_le_da_el_panel_a_los_roles_existentes():
    fuente = Path(__file__).resolve().parents[1] / "database.py"
    assert '_grant_panel_to_existing_roles(conn, "finanzas")' in \
        fuente.read_text(encoding="utf-8")


def test_no_hay_emojis_en_el_panel():
    """El CRM usa lucide. Un emoji suelto se ve distinto en cada sistema."""
    inicio = HTML.index('id="finanzas-panel"')
    trozo = HTML[inicio:inicio + 20000]
    assert not re.search(r"[\U0001F300-\U0001FAFF]", trozo)
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `python -m pytest tests/test_finanzas_panel.py -v`
Expected: FAIL, 8 fallas.

- [ ] **Step 3: Agregar el ítem del nav**

En `dashboard.py`, en el bloque `<div class="nav-section-label">GESTIÓN</div>`, **arriba** de la línea de `nav-metrics`:

```html
  <div class="nav-item" id="nav-finanzas" onclick="showPanel('finanzas')"><i data-lucide="wallet" class="nav-icon"></i> Finanzas</div>
```

- [ ] **Step 4: Agregar el panel a las cinco constantes de JS**

> **No usar números de línea.** Hay otra sesión trabajando en `dashboard.py` y
> las líneas se corren. Ubicar cada constante con `grep -n`.

Run: `grep -n "const ALL_PANELS\|const PANEL_LABELS\|const NAV_PRIORITY\|const NAV_ICONS\|const NAV_LABELS" dashboard.py`

Son **cinco** constantes en **dos** bloques distintos de JS:

| Constante | Apariciones | Qué agregar |
|---|---|---|
| `ALL_PANELS` | **2** — una en el dashboard, otra en el editor de roles | `,'finanzas'` al final de cada lista |
| `PANEL_LABELS` | 1, en el editor de roles | `,finanzas:'Finanzas'` |
| `NAV_PRIORITY` | 1 | `,'finanzas'` al final |
| `NAV_ICONS` | 1 | `,finanzas:'wallet'` |
| `NAV_LABELS` | 1 | `,finanzas:'Finanzas'` |

`NAV_ICONS` y `NAV_LABELS` son objetos distintos y contiguos: el primero mapea
panel → ícono de lucide, el segundo panel → texto corto de la barra mobile.
`_buildMobileNav` usa los dos, así que faltando cualquiera de ellos el ítem
aparece en mobile sin ícono o sin nombre.

Las dos apariciones de `ALL_PANELS` son el error más fácil de cometer: la
segunda vive en el HTML de la página de administración, que es un string
aparte de `DASHBOARD_HTML`. Si falta, el panel existe pero no se le puede
asignar a nadie desde el editor de roles.

- [ ] **Step 5: Agregar el div del panel y el dispatch**

En `showPanel` (ubicarlo con `grep -n "function showPanel" dashboard.py`), después de `if (name === 'notion_clients') loadNotionClients();`:

```javascript
  if (name === 'finanzas') loadFinanzas();
```

Y el shell del panel, junto a los otros `<div class="panel">`:

```html
<div class="panel" id="finanzas-panel">
  <div class="fin-toolbar">
    <select id="fin-rango" onchange="loadFinanzas()">
      <option value="mes">Mes actual</option>
      <option value="3">Últimos 3 meses</option>
      <option value="12" selected>Últimos 12 meses</option>
      <option value="anio">Este año</option>
    </select>
    <div class="fin-toggle">
      <button class="pill active" id="fin-tab-movs" onclick="finVista('movimientos')">Movimientos</button>
      <button class="pill" id="fin-tab-fijos" onclick="finVista('fijos')">Fijos</button>
    </div>
    <button class="btn-primary" onclick="abrirMovimiento()">
      <i data-lucide="plus" class="nav-icon"></i> Movimiento
    </button>
  </div>

  <div id="fin-vista-movimientos">
    <div class="fin-kpis" id="fin-kpis"></div>
    <div class="fin-card"><div class="fin-card-title">Ingresos y egresos por mes</div>
      <div id="fin-serie"></div></div>
    <div class="fin-split">
      <div class="fin-card"><div class="fin-card-title">Egresos por categoría</div>
        <div id="fin-por-categoria"></div></div>
      <div class="fin-card"><div class="fin-card-title">Ingresos por cliente</div>
        <div id="fin-por-cliente"></div></div>
    </div>
    <div class="fin-card"><div class="fin-card-title">Movimientos</div>
      <div id="fin-tabla"></div></div>
  </div>

  <div id="fin-vista-fijos" style="display:none">
    <div class="fin-card"><div class="fin-card-title">Gastos e ingresos fijos</div>
      <div id="fin-fijos"></div></div>
  </div>
</div>
```

Y el stub del JS, que la Task 8 completa:

```javascript
function finVista(cual) {
  const esMovs = cual === 'movimientos';
  document.getElementById('fin-vista-movimientos').style.display = esMovs ? '' : 'none';
  document.getElementById('fin-vista-fijos').style.display = esMovs ? 'none' : '';
  document.getElementById('fin-tab-movs').classList.toggle('active', esMovs);
  document.getElementById('fin-tab-fijos').classList.toggle('active', !esMovs);
  if (!esMovs) loadFijos();
}

async function loadFinanzas() {}
async function loadFijos() {}
function abrirMovimiento() {}
```

- [ ] **Step 6: Correr los tests**

Run: `python -m pytest tests/test_finanzas_panel.py tests/test_dashboard_js.py -v`
Expected: PASS

- [ ] **Step 7: Validar el JS embebido**

Run: `python scripts/check_js.py`
Expected: sin errores

- [ ] **Step 8: Commit**

```bash
git add dashboard.py tests/test_finanzas_panel.py
git commit -m "feat(finanzas): registrar el panel en el nav y en los roles"
```

---

### Task 8: KPIs, serie y desgloses

**Files:**
- Modify: `dashboard.py` (el CSS del panel, y las funciones `loadFinanzas`, `_finKpis`, `_finSerie`, `_finBarras`)
- Test: `tests/test_finanzas_panel.py` (un caso más) + `scripts/check_js.py`

**Interfaces:**
- Consumes: `GET /api/finanzas/resumen` (Task 6), con la forma exacta documentada en la Task 4.
- Produces: `loadFinanzas()` completa, `_finRango()` que traduce el select a `{desde, hasta}`.

- [ ] **Step 1: Escribir el CSS**

En el `<style>` de `DASHBOARD_HTML`, al final:

```css
.fin-toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:18px}
.fin-toggle{display:flex;gap:6px;margin-left:auto}
.fin-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:18px}
.fin-kpi{background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:16px 18px}
.fin-kpi-label{font-size:.7rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.8px}
.fin-kpi-valor{font-size:1.6rem;font-weight:700;margin-top:6px}
.fin-kpi-var{font-size:.75rem;color:#64748b;margin-top:4px}
.fin-verde{color:#10b981}
.fin-rojo{color:#f87171}
.fin-card{background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:18px;margin-bottom:18px}
.fin-card-title{font-size:.75rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.8px;margin-bottom:14px}
.fin-split{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.fin-mes{display:flex;align-items:flex-end;gap:3px;height:90px}
.fin-serie{display:flex;gap:10px;align-items:flex-end;overflow-x:auto;padding-bottom:6px}
.fin-serie-col{display:flex;flex-direction:column;align-items:center;gap:6px;min-width:44px}
.fin-serie-label{font-size:.65rem;color:#64748b;white-space:nowrap}
.fin-barra{width:14px;border-radius:3px 3px 0 0;min-height:2px}
.fin-hbar-fila{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.fin-hbar-nombre{font-size:.78rem;color:#94a3b8;width:130px;flex-shrink:0}
.fin-hbar-pista{flex:1;background:#1e293b;border-radius:3px;height:8px;overflow:hidden}
.fin-hbar-relleno{height:100%;border-radius:3px}
.fin-hbar-monto{font-size:.75rem;color:#e2e8f0;width:74px;text-align:right;flex-shrink:0}
@media (max-width:760px){.fin-split{grid-template-columns:1fr}}
body.light .fin-kpi,body.light .fin-card{background:#fff;border-color:#e2e8f0}
body.light .fin-hbar-pista{background:#e2e8f0}
body.light .fin-hbar-monto{color:#1e293b}
body.light .fin-hbar-nombre{color:#475569}
```

- [ ] **Step 2: Escribir el JS de render**

Reemplazar el stub `async function loadFinanzas() {}` por:

```javascript
const FIN_VERDE = '#10b981';
const FIN_ROJO  = '#f87171';

function _finUsd(n) {
  return 'USD ' + (n || 0).toLocaleString('es-UY', {minimumFractionDigits: 2,
                                                    maximumFractionDigits: 2});
}

function _finRango() {
  const hoy = new Date();
  const mes = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  const cual = document.getElementById('fin-rango').value;
  const hasta = mes(hoy);
  if (cual === 'mes')  return {desde: hasta, hasta};
  if (cual === 'anio') return {desde: `${hoy.getFullYear()}-01`, hasta};
  const atras = new Date(hoy.getFullYear(), hoy.getMonth() - (parseInt(cual, 10) - 1), 1);
  return {desde: mes(atras), hasta};
}

function _finVariacion(actual, previo) {
  if (!previo) return '';
  const pct = Math.round(((actual - previo) / Math.abs(previo)) * 100);
  const signo = pct > 0 ? '+' : '';
  return `${signo}${pct}% vs. período anterior`;
}

function _finKpis(k) {
  const neto = k.neto_usd;
  return `
    <div class="fin-kpi">
      <div class="fin-kpi-label">Ingresos</div>
      <div class="fin-kpi-valor fin-verde">${_finUsd(k.ingresos_usd)}</div>
      <div class="fin-kpi-var">${_finVariacion(k.ingresos_usd, k.ingresos_previos_usd)}</div>
    </div>
    <div class="fin-kpi">
      <div class="fin-kpi-label">Egresos</div>
      <div class="fin-kpi-valor fin-rojo">${_finUsd(k.egresos_usd)}</div>
      <div class="fin-kpi-var">${_finVariacion(k.egresos_usd, k.egresos_previos_usd)}</div>
    </div>
    <div class="fin-kpi">
      <div class="fin-kpi-label">Resultado</div>
      <div class="fin-kpi-valor ${neto >= 0 ? 'fin-verde' : 'fin-rojo'}">${_finUsd(neto)}</div>
      <div class="fin-kpi-var">${_finVariacion(neto, k.neto_previo_usd)}</div>
    </div>`;
}

function _finSerie(serie) {
  if (!serie.length) return '<div class="empty-state">Sin movimientos en el período</div>';
  const tope = Math.max(...serie.map(p => Math.max(p.ingresos_usd, p.egresos_usd)), 1);
  const alto = v => Math.max(Math.round((v / tope) * 80), v > 0 ? 3 : 1);
  return '<div class="fin-serie">' + serie.map(p => `
    <div class="fin-serie-col" title="${p.periodo}: ingresos ${_finUsd(p.ingresos_usd)}, egresos ${_finUsd(p.egresos_usd)}">
      <div class="fin-mes">
        <div class="fin-barra" style="height:${alto(p.ingresos_usd)}px;background:${FIN_VERDE}"></div>
        <div class="fin-barra" style="height:${alto(p.egresos_usd)}px;background:${FIN_ROJO}"></div>
      </div>
      <div class="fin-serie-label">${p.periodo.slice(5)}/${p.periodo.slice(2, 4)}</div>
    </div>`).join('') + '</div>';
}

function _finBarras(filas, color) {
  if (!filas.length) return '<div class="empty-state">Sin datos</div>';
  const tope = Math.max(...filas.map(f => f.total_usd), 1);
  return filas.map(f => `
    <div class="fin-hbar-fila">
      <div class="fin-hbar-nombre">${esc(f.nombre)}</div>
      <div class="fin-hbar-pista">
        <div class="fin-hbar-relleno" style="width:${(f.total_usd / tope) * 100}%;background:${color}"></div>
      </div>
      <div class="fin-hbar-monto">${_finUsd(f.total_usd)}</div>
    </div>`).join('');
}

let _finResumen = null;

async function loadFinanzas() {
  const {desde, hasta} = _finRango();
  const kpisEl = document.getElementById('fin-kpis');
  kpisEl.innerHTML = '<div style="color:#475569;padding:16px;font-size:.85rem">Cargando...</div>';
  try {
    const r = await fetch(`/api/finanzas/resumen?desde=${desde}&hasta=${hasta}`);
    if (!r.ok) throw new Error('no se pudo cargar el resumen');
    const data = await r.json();
    _finResumen = data;

    kpisEl.innerHTML = _finKpis(data.kpis);
    document.getElementById('fin-serie').innerHTML = _finSerie(data.serie);

    const egresos = data.por_categoria
      .filter(c => c.tipo === 'egreso')
      .map(c => ({nombre: c.categoria.replace(/_/g, ' '), total_usd: c.total_usd}));
    document.getElementById('fin-por-categoria').innerHTML = _finBarras(egresos, FIN_ROJO);
    document.getElementById('fin-por-cliente').innerHTML = _finBarras(data.por_cliente, FIN_VERDE);

    await loadMovimientos(desde, hasta);
    if (window.lucide) lucide.createIcons();
  } catch (e) {
    kpisEl.innerHTML = `<div style="color:#f87171;padding:16px">Error: ${esc(e.message)}</div>`;
  }
}
```

`loadMovimientos` llega en la Task 9; por ahora agregar el stub `async function loadMovimientos() {}` para que el JS compile.

- [ ] **Step 3: Agregar el test del formato**

Al final de `tests/test_finanzas_panel.py`:

```python
def test_los_montos_se_muestran_en_dolares():
    """El panel nunca inventa una conversión: muestra el monto_usd que vino."""
    assert "'USD '" in HTML
    assert "_finUsd" in HTML


def test_el_panel_tiene_reglas_para_modo_claro():
    assert "body.light .fin-card" in HTML
```

- [ ] **Step 4: Correr los tests y el validador de JS**

Run: `python -m pytest tests/test_finanzas_panel.py tests/test_dashboard_js.py -v && python scripts/check_js.py`
Expected: PASS, sin errores de JS

- [ ] **Step 5: Commit**

```bash
git add dashboard.py tests/test_finanzas_panel.py
git commit -m "feat(finanzas): KPIs, serie mensual y desgloses del panel"
```

---

### Task 9: Tabla de movimientos y el modal

**Files:**
- Modify: `dashboard.py` (el div del modal, el CSS del modal, y `loadMovimientos`, `abrirMovimiento`, `guardarMovimiento`, `borrarMovimientoUI`, `_finRecalcularUsd`)
- Test: `tests/test_finanzas_panel.py` (dos casos más)

**Interfaces:**
- Consumes: `GET/POST/PUT/DELETE /api/finanzas/movimientos`, `GET /api/finanzas/categorias` (Task 6).
- Produces: `abrirMovimiento(prefill)` — acepta un objeto opcional `{tipo, client_id, monto, moneda, budget_id, concepto}` que la Task 11 usa para el atajo desde presupuesto.

- [ ] **Step 1: Agregar el modal al HTML**

Junto a los otros modales de `DASHBOARD_HTML`:

Sigue la estructura real de los modales del CRM: el contenedor es
`.modal-overlay` (es el que recibe la clase `open`), adentro va `.modal`, el
título es un `<h3>`, los campos usan `.modal-label` / `.modal-input`, y los
botones van en `.modal-btns`. Verificar con
`grep -n 'class="modal-overlay"' dashboard.py` antes de escribir.

```html
<div class="modal-overlay" id="fin-modal" onclick="if(event.target===this)cerrarMovimiento()">
  <div class="modal" style="width:480px">
    <h3 id="fin-modal-title">Nuevo movimiento</h3>
    <input type="hidden" id="fin-mov-id">
    <input type="hidden" id="fin-mov-budget">

    <div class="fin-toggle" style="margin-bottom:14px">
      <button class="pill active" id="fin-tipo-egreso" onclick="finSetTipo('egreso')">Egreso</button>
      <button class="pill" id="fin-tipo-ingreso" onclick="finSetTipo('ingreso')">Ingreso</button>
    </div>

    <label class="modal-label">Fecha</label>
    <input type="date" id="fin-mov-fecha" class="modal-input">

    <label class="modal-label">Concepto</label>
    <input type="text" id="fin-mov-concepto" class="modal-input" placeholder="Fly.io, cobro Bloquera, ...">

    <label class="modal-label">Categoría</label>
    <select id="fin-mov-categoria"></select>

    <label class="modal-label">Monto</label>
    <div style="display:flex;gap:8px">
      <input type="number" step="0.01" min="0" id="fin-mov-monto" class="modal-input" oninput="_finRecalcularUsd()">
      <select id="fin-mov-moneda" onchange="_finRecalcularUsd()">
        <option value="USD">USD</option>
        <option value="UYU">UYU</option>
      </select>
    </div>

    <div id="fin-tc-row" style="display:none">
      <label class="modal-label">Tipo de cambio (pesos por dólar)</label>
      <input type="number" step="0.01" min="0" id="fin-mov-tc" class="modal-input" oninput="_finRecalcularUsd()">
      <div id="fin-tc-preview" class="fin-kpi-var"></div>
    </div>

    <label class="modal-label">Cliente (opcional)</label>
    <select id="fin-mov-cliente"><option value="">Sin atribuir</option></select>

    <label class="modal-label">Notas</label>
    <textarea id="fin-mov-notas" rows="2"></textarea>

    <div id="fin-modal-error" class="fin-rojo" style="font-size:.8rem;margin-top:10px"></div>
    <div class="modal-btns">
      <button class="btn-ghost" onclick="cerrarMovimiento()">Cancelar</button>
      <button class="btn-primary" onclick="guardarMovimiento()">Guardar</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Escribir el JS del modal y la tabla**

Reemplazar los stubs por:

```javascript
let _finCategorias = null;
let _finTipo = 'egreso';

async function _finCargarCategorias() {
  if (!_finCategorias) {
    _finCategorias = await (await fetch('/api/finanzas/categorias')).json();
  }
  return _finCategorias;
}

function finSetTipo(tipo) {
  _finTipo = tipo;
  document.getElementById('fin-tipo-egreso').classList.toggle('active', tipo === 'egreso');
  document.getElementById('fin-tipo-ingreso').classList.toggle('active', tipo === 'ingreso');
  const sel = document.getElementById('fin-mov-categoria');
  sel.innerHTML = (_finCategorias[tipo] || [])
    .map(c => `<option value="${c}">${c.replace(/_/g, ' ')}</option>`).join('');
}

function _finRecalcularUsd() {
  // El número congelado se ve ANTES de congelarlo, no después.
  const esPesos = document.getElementById('fin-mov-moneda').value === 'UYU';
  document.getElementById('fin-tc-row').style.display = esPesos ? '' : 'none';
  if (!esPesos) return;
  const monto = parseFloat(document.getElementById('fin-mov-monto').value);
  const tc = parseFloat(document.getElementById('fin-mov-tc').value);
  const box = document.getElementById('fin-tc-preview');
  box.textContent = (monto > 0 && tc > 0)
    ? `Se va a guardar como ${_finUsd(monto / tc)}`
    : 'Falta el tipo de cambio para poder guardarlo';
}

async function abrirMovimiento(prefill) {
  await _finCargarCategorias();
  const p = prefill || {};
  document.getElementById('fin-modal-title').textContent =
    p.id ? 'Editar movimiento' : 'Nuevo movimiento';
  document.getElementById('fin-mov-id').value = p.id || '';
  document.getElementById('fin-mov-budget').value = p.budget_id || '';
  document.getElementById('fin-mov-fecha').value =
    p.fecha || new Date().toISOString().slice(0, 10);
  document.getElementById('fin-mov-concepto').value = p.concepto || '';
  document.getElementById('fin-mov-monto').value = p.monto || '';
  document.getElementById('fin-mov-moneda').value = p.moneda || 'USD';
  document.getElementById('fin-mov-tc').value = p.tipo_cambio || '';
  document.getElementById('fin-mov-notas').value = p.notas || '';
  document.getElementById('fin-modal-error').textContent = '';

  finSetTipo(p.tipo || 'egreso');
  if (p.categoria) document.getElementById('fin-mov-categoria').value = p.categoria;
  await _finCargarClientes(p.client_id);
  _finRecalcularUsd();
  document.getElementById('fin-modal').classList.add('open');
}

function cerrarMovimiento() {
  document.getElementById('fin-modal').classList.remove('open');
}

async function _finCargarClientes(seleccionado) {
  const sel = document.getElementById('fin-mov-cliente');
  if (sel.dataset.cargado !== '1') {
    const r = await fetch('/api/leads?crm_group=clientes');
    const data = await r.json();
    const leads = Array.isArray(data) ? data : (data.items || []);
    sel.innerHTML = '<option value="">Sin atribuir</option>' +
      leads.map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
    sel.dataset.cargado = '1';
  }
  sel.value = seleccionado || '';
}

async function guardarMovimiento() {
  const id = document.getElementById('fin-mov-id').value;
  const moneda = document.getElementById('fin-mov-moneda').value;
  const cuerpo = {
    tipo: _finTipo,
    fecha: document.getElementById('fin-mov-fecha').value,
    concepto: document.getElementById('fin-mov-concepto').value,
    categoria: document.getElementById('fin-mov-categoria').value,
    monto: parseFloat(document.getElementById('fin-mov-monto').value),
    moneda,
    tipo_cambio: moneda === 'UYU'
      ? parseFloat(document.getElementById('fin-mov-tc').value) : null,
    client_id: document.getElementById('fin-mov-cliente').value || null,
    budget_id: document.getElementById('fin-mov-budget').value || null,
    notas: document.getElementById('fin-mov-notas').value,
  };
  const r = await fetch(id ? `/api/finanzas/movimientos/${id}` : '/api/finanzas/movimientos',
                        {method: id ? 'PUT' : 'POST',
                         headers: {'Content-Type': 'application/json'},
                         body: JSON.stringify(cuerpo)});
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    document.getElementById('fin-modal-error').textContent =
      err.error || 'No se pudo guardar';
    return;
  }
  cerrarMovimiento();
  loadFinanzas();
}

async function loadMovimientos(desde, hasta) {
  const cuerpo = document.getElementById('fin-tabla');
  const r = await fetch(`/api/finanzas/movimientos?desde=${desde}&hasta=${hasta}`);
  const movs = await r.json();
  if (!movs.length) {
    cuerpo.innerHTML = '<div class="empty-state">No hay movimientos en el período</div>';
    return;
  }
  cuerpo.innerHTML = movs.map(m => {
    const esIngreso = m.tipo === 'ingreso';
    const original = m.moneda === 'UYU'
      ? ` <span class="fin-kpi-var">($ ${m.monto.toLocaleString('es-UY')} @ ${m.tipo_cambio})</span>`
      : '';
    return `
    <div class="table-row no-cb">
      <div style="flex:0 0 92px" class="fin-kpi-var">${m.fecha}</div>
      <div style="flex:1">
        <div class="biz-name">${esc(m.concepto)}</div>
        <div class="fin-kpi-var">${esc(m.categoria.replace(/_/g, ' '))}${m.recurrente_id ? ' · fijo' : ''}</div>
      </div>
      <div style="flex:0 0 170px;text-align:right"
           class="${esIngreso ? 'fin-verde' : 'fin-rojo'}">
        ${esIngreso ? '+' : '−'}${_finUsd(m.monto_usd)}${original}
      </div>
      <div style="flex:0 0 76px;text-align:right">
        <button class="btn-ghost" onclick='abrirMovimiento(${JSON.stringify(m)})'
                title="Editar"><i data-lucide="pencil" class="nav-icon"></i></button>
        <button class="btn-ghost" onclick="borrarMovimientoUI(${m.id}, ${m.recurrente_id ? 1 : 0})"
                title="Borrar"><i data-lucide="trash-2" class="nav-icon"></i></button>
      </div>
    </div>`;
  }).join('');
  if (window.lucide) lucide.createIcons();
}

async function borrarMovimientoUI(id, esDeUnFijo) {
  const aviso = esDeUnFijo
    ? 'Este movimiento lo generó un gasto fijo. Se va a sacar de los totales de este mes, pero el fijo sigue activo para los meses que vienen. ¿Seguro?'
    : '¿Borrar el movimiento?';
  if (!confirm(aviso)) return;
  await fetch(`/api/finanzas/movimientos/${id}`, {method: 'DELETE'});
  loadFinanzas();
}
```

> **Ojo con `confirm()`:** el resto del CRM lo usa, así que se mantiene el patrón. Si en algún momento se migra a un modal propio, este es uno de los lugares a tocar.

- [ ] **Step 3: Agregar los tests**

Al final de `tests/test_finanzas_panel.py`:

```python
def test_el_modal_muestra_el_monto_en_dolares_antes_de_guardar():
    """Ver el número congelado antes de congelarlo es el punto del modal."""
    assert "Se va a guardar como" in HTML
    assert 'id="fin-tc-preview"' in HTML


def test_borrar_un_movimiento_de_un_fijo_avisa_que_el_fijo_sigue():
    assert "el fijo sigue activo" in HTML
```

- [ ] **Step 4: Correr los tests y el validador de JS**

Run: `python -m pytest tests/test_finanzas_panel.py tests/test_dashboard_js.py -v && python scripts/check_js.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard.py tests/test_finanzas_panel.py
git commit -m "feat(finanzas): tabla de movimientos y modal de carga"
```

---

### Task 10: La vista de fijos

**Files:**
- Modify: `dashboard.py` (el modal de fijos y las funciones `loadFijos`, `abrirFijo`, `guardarFijo`, `borrarFijoUI`)
- Test: `tests/test_finanzas_panel.py` (un caso más)

**Interfaces:**
- Consumes: `GET/POST/PUT/DELETE /api/finanzas/recurrentes` (Task 6); `_finUsd`, `_finCargarCategorias`, `_finCargarClientes` (Tasks 8-9).
- Produces: `loadFijos()` completa.

- [ ] **Step 1: Agregar el modal de fijos**

```html
<div class="modal-overlay" id="fin-fijo-modal" onclick="if(event.target===this)cerrarFijo()">
  <div class="modal" style="width:480px">
    <h3 id="fin-fijo-title">Nuevo fijo</h3>
    <input type="hidden" id="fin-fijo-id">

    <div class="fin-toggle" style="margin-bottom:14px">
      <button class="pill active" id="fin-fijo-tipo-egreso" onclick="finFijoSetTipo('egreso')">Egreso</button>
      <button class="pill" id="fin-fijo-tipo-ingreso" onclick="finFijoSetTipo('ingreso')">Ingreso</button>
    </div>

    <label class="modal-label">Concepto</label>
    <input type="text" id="fin-fijo-concepto" class="modal-input" placeholder="Fly.io, Vercel, Zoho, ...">

    <label class="modal-label">Categoría</label>
    <select id="fin-fijo-categoria"></select>

    <label class="modal-label">Monto</label>
    <div style="display:flex;gap:8px">
      <input type="number" step="0.01" min="0" id="fin-fijo-monto" class="modal-input">
      <select id="fin-fijo-moneda" onchange="_finFijoTc()">
        <option value="USD">USD</option>
        <option value="UYU">UYU</option>
      </select>
    </div>

    <div id="fin-fijo-tc-row" style="display:none">
      <label class="modal-label">Tipo de cambio (pesos por dólar)</label>
      <input type="number" step="0.01" min="0" id="fin-fijo-tc" class="modal-input">
    </div>

    <label class="modal-label">Día del mes (1 al 28)</label>
    <input type="number" min="1" max="28" id="fin-fijo-dia" class="modal-input" value="1">

    <label class="modal-label">Desde</label>
    <input type="month" id="fin-fijo-desde" class="modal-input">

    <label class="modal-label">Hasta (vacío = sigue vivo)</label>
    <input type="month" id="fin-fijo-hasta" class="modal-input">

    <div id="fin-fijo-error" class="fin-rojo" style="font-size:.8rem;margin-top:10px"></div>
    <div class="modal-btns">
      <button class="btn-ghost" onclick="cerrarFijo()">Cancelar</button>
      <button class="btn-primary" onclick="guardarFijo()">Guardar</button>
    </div>
  </div>
</div>
```

> `input[type=month]` no está en la lista de selectores que estiliza `.modal`
> (`grep -n "input\[type=date\]" dashboard.py` la muestra). Agregar
> `,.modal input[type=month]` a esa regla, o el campo se ve sin estilo.

- [ ] **Step 2: Escribir el JS**

Reemplazar el stub `async function loadFijos() {}` por:

```javascript
let _finFijoTipo = 'egreso';

function _finFijoTc() {
  const esPesos = document.getElementById('fin-fijo-moneda').value === 'UYU';
  document.getElementById('fin-fijo-tc-row').style.display = esPesos ? '' : 'none';
}

function finFijoSetTipo(tipo) {
  _finFijoTipo = tipo;
  document.getElementById('fin-fijo-tipo-egreso').classList.toggle('active', tipo === 'egreso');
  document.getElementById('fin-fijo-tipo-ingreso').classList.toggle('active', tipo === 'ingreso');
  document.getElementById('fin-fijo-categoria').innerHTML =
    (_finCategorias[tipo] || []).map(c =>
      `<option value="${c}">${c.replace(/_/g, ' ')}</option>`).join('');
}

async function abrirFijo(fijo) {
  await _finCargarCategorias();
  const f = fijo || {};
  document.getElementById('fin-fijo-title').textContent = f.id ? 'Editar fijo' : 'Nuevo fijo';
  document.getElementById('fin-fijo-id').value = f.id || '';
  document.getElementById('fin-fijo-concepto').value = f.concepto || '';
  document.getElementById('fin-fijo-monto').value = f.monto || '';
  document.getElementById('fin-fijo-moneda').value = f.moneda || 'USD';
  document.getElementById('fin-fijo-tc').value = f.tipo_cambio || '';
  document.getElementById('fin-fijo-dia').value = f.dia_del_mes || 1;
  document.getElementById('fin-fijo-desde').value =
    f.desde || new Date().toISOString().slice(0, 7);
  document.getElementById('fin-fijo-hasta').value = f.hasta || '';
  document.getElementById('fin-fijo-error').textContent = '';
  finFijoSetTipo(f.tipo || 'egreso');
  if (f.categoria) document.getElementById('fin-fijo-categoria').value = f.categoria;
  _finFijoTc();
  document.getElementById('fin-fijo-modal').classList.add('open');
}

function cerrarFijo() {
  document.getElementById('fin-fijo-modal').classList.remove('open');
}

async function guardarFijo() {
  const id = document.getElementById('fin-fijo-id').value;
  const moneda = document.getElementById('fin-fijo-moneda').value;
  const cuerpo = {
    tipo: _finFijoTipo,
    concepto: document.getElementById('fin-fijo-concepto').value,
    categoria: document.getElementById('fin-fijo-categoria').value,
    monto: parseFloat(document.getElementById('fin-fijo-monto').value),
    moneda,
    tipo_cambio: moneda === 'UYU'
      ? parseFloat(document.getElementById('fin-fijo-tc').value) : null,
    dia_del_mes: parseInt(document.getElementById('fin-fijo-dia').value, 10),
    desde: document.getElementById('fin-fijo-desde').value,
    hasta: document.getElementById('fin-fijo-hasta').value || null,
  };
  const r = await fetch(id ? `/api/finanzas/recurrentes/${id}` : '/api/finanzas/recurrentes',
                        {method: id ? 'PUT' : 'POST',
                         headers: {'Content-Type': 'application/json'},
                         body: JSON.stringify(cuerpo)});
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    document.getElementById('fin-fijo-error').textContent = err.error || 'No se pudo guardar';
    return;
  }
  cerrarFijo();
  loadFijos();
}

async function loadFijos() {
  const cuerpo = document.getElementById('fin-fijos');
  const fijos = await (await fetch('/api/finanzas/recurrentes')).json();

  const mensual = fijos
    .filter(f => f.activo && f.tipo === 'egreso')
    .reduce((suma, f) => suma + (f.moneda === 'USD' ? f.monto : f.monto / (f.tipo_cambio || 1)), 0);

  const encabezado = `
    <div class="fin-toolbar">
      <div class="fin-kpi-var">Egresos fijos activos: <strong>${_finUsd(mensual)}</strong> por mes</div>
      <button class="btn-primary" style="margin-left:auto" onclick="abrirFijo()">
        <i data-lucide="plus" class="nav-icon"></i> Fijo
      </button>
    </div>`;

  if (!fijos.length) {
    cuerpo.innerHTML = encabezado + '<div class="empty-state">No hay fijos cargados</div>';
    if (window.lucide) lucide.createIcons();
    return;
  }

  cuerpo.innerHTML = encabezado + fijos.map(f => `
    <div class="table-row no-cb" style="${f.activo ? '' : 'opacity:.5'}">
      <div style="flex:1">
        <div class="biz-name">${esc(f.concepto)}</div>
        <div class="fin-kpi-var">${esc(f.categoria.replace(/_/g, ' '))} · día ${f.dia_del_mes} · desde ${f.desde}${f.hasta ? ' hasta ' + f.hasta : ''}${f.activo ? '' : ' · apagado'}</div>
      </div>
      <div style="flex:0 0 150px;text-align:right"
           class="${f.tipo === 'ingreso' ? 'fin-verde' : 'fin-rojo'}">
        ${f.moneda} ${f.monto.toLocaleString('es-UY')}
      </div>
      <div style="flex:0 0 76px;text-align:right">
        <button class="btn-ghost" onclick='abrirFijo(${JSON.stringify(f)})'
                title="Editar"><i data-lucide="pencil" class="nav-icon"></i></button>
        <button class="btn-ghost" onclick="borrarFijoUI(${f.id})"
                title="Borrar"><i data-lucide="trash-2" class="nav-icon"></i></button>
      </div>
    </div>`).join('');
  if (window.lucide) lucide.createIcons();
}

async function borrarFijoUI(id) {
  if (!confirm('Se borra la definición del fijo. Los movimientos que ya generó quedan: son plata que se gastó. ¿Seguro?')) return;
  await fetch(`/api/finanzas/recurrentes/${id}`, {method: 'DELETE'});
  loadFijos();
}
```

- [ ] **Step 3: Agregar el test**

```python
def test_borrar_un_fijo_aclara_que_los_movimientos_quedan():
    assert "son plata que se gastó" in HTML
```

- [ ] **Step 4: Correr los tests y el validador de JS**

Run: `python -m pytest tests/test_finanzas_panel.py tests/test_dashboard_js.py -v && python scripts/check_js.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard.py tests/test_finanzas_panel.py
git commit -m "feat(finanzas): vista de gastos e ingresos fijos"
```

---

### Task 11: Atajo "Registrar cobro" desde el presupuesto

**Files:**
- Modify: `dashboard.py` (la ficha del presupuesto dentro del panel de cliente)
- Test: `tests/test_finanzas_panel.py` (un caso más)

**Interfaces:**
- Consumes: `abrirMovimiento(prefill)` (Task 9).
- Produces: `registrarCobro(clientId, clientName, budgetId, total)`.

**Nota de territorio:** esto es solo frontend. No se toca `routes/budgets.py`, que `COORDINACION.md` asigna a la sesión B.

- [ ] **Step 1: Encontrar dónde se dibuja el presupuesto**

Run: `grep -n "total_amount" dashboard.py`

Buscar el bloque del panel de cliente que muestra el total del presupuesto y sus botones de acción.

- [ ] **Step 2: Agregar el botón**

Junto a los otros botones de la ficha del presupuesto:

```html
<button class="btn-ghost" onclick="registrarCobro(${b.client_id}, '${esc(nombreCliente)}', ${b.id}, ${b.total_amount})">
  <i data-lucide="wallet" class="nav-icon"></i> Registrar cobro
</button>
```

- [ ] **Step 3: Escribir la función**

```javascript
async function registrarCobro(clientId, clientName, budgetId, total) {
  // Un presupuesto aprobado no es plata: es una expectativa. El atajo precarga
  // los campos, pero registrar el cobro sigue siendo un acto explícito.
  showPanel('finanzas');
  await abrirMovimiento({
    tipo: 'ingreso',
    concepto: `Cobro ${clientName}`,
    categoria: 'desarrollo_web',
    monto: total,
    moneda: 'USD',
    client_id: clientId,
    budget_id: budgetId,
  });
}
```

- [ ] **Step 4: Agregar el test**

```python
def test_el_atajo_desde_presupuesto_precarga_el_movimiento():
    assert "function registrarCobro(" in HTML
    assert "tipo: 'ingreso'" in HTML
    assert "budget_id: budgetId" in HTML
```

- [ ] **Step 5: Correr todo**

Run: `python -m pytest -q && python scripts/check_js.py`
Expected: PASS, sin errores de JS

- [ ] **Step 6: Commit**

```bash
git add dashboard.py tests/test_finanzas_panel.py
git commit -m "feat(finanzas): registrar cobro desde un presupuesto"
```

---

### Task 12: Coordinación y puesta en producción

**Files:**
- Modify: `COORDINACION.md`
- Verify: la suite entera, el CI local, y producción después del deploy

- [ ] **Step 1: Anotarse en COORDINACION.md**

Agregar la fila en la tabla "Quién está en qué" y una nota en la bitácora:

```markdown
| E (finanzas) | la sección financiera del CRM | `services/finanzas.py`, `routes/finanzas.py`, `database.py` (tablas de finanzas), `dashboard.py` (panel Finanzas) | 8/9 |
```

Y avisar del cruce: `dashboard.py` está declarado de **B** y `database.py` es zona compartida. La nota tiene que decir qué partes se tocan (el nav, un panel nuevo, y dos tablas nuevas al final de `init_db`) para que B sepa que no se le pisa nada de lo suyo.

- [ ] **Step 2: Correr la suite completa con cobertura, como el CI**

Run:
```bash
python -m pytest -q --cov=database --cov=routes --cov=services --cov=dashboard --cov=main --cov-report=term-missing --cov-fail-under=57
```
Expected: PASS. El umbral es un trinquete: con los tests de este plan la cobertura tiene que **subir**, no bajar. Si bajó, faltan tests.

- [ ] **Step 3: Validar el JS y que todo compile**

Run:
```bash
python -m compileall -q dashboard.py database.py server.py main.py routes services scripts && python scripts/check_js.py
```
Expected: sin salida (todo bien)

- [ ] **Step 4: Probar el panel a mano**

Run: `python main.py dashboard`

Verificar en el browser:
1. El ítem **Finanzas** aparece en el sidebar bajo GESTIÓN.
2. Cargar un egreso en USD → aparece en la tabla y en los KPIs.
3. Cargar un ingreso en UYU con tipo de cambio → el modal muestra el monto en USD **antes** de guardar, y el guardado coincide.
4. Crear un fijo con `desde` dos meses atrás → al recargar el panel aparecen los tres movimientos (los dos meses viejos y el actual).
5. Recargar el panel otra vez → **siguen siendo tres**, no seis. Este es el caso del deploy.
6. Borrar el movimiento de un fijo → recargar → no reaparece.
7. Cambiar a modo claro y revisar que los KPIs, las barras y los modales se lean.

- [ ] **Step 5: Verificar el árbol limpio antes de deployar**

Run: `git status --short`
Expected: vacío. El `Dockerfile` hace `COPY . .`: `flyctl deploy` sube el árbol de trabajo entero, incluido lo que otra sesión dejó a medio hacer.

- [ ] **Step 6: Mirar si hay otra sesión deployando**

Run: `flyctl releases --app scalerics-crm`
Expected: ningún release de los últimos minutos que no sea tuyo. Si lo hay, preguntar en `COORDINACION.md` antes de pisar.

- [ ] **Step 7: Deploy**

Run: `flyctl deploy --app scalerics-crm`

- [ ] **Step 8: Verificar contra producción leyendo el valor vivo**

No alcanza con mirar el log del deploy. Entrar a la máquina y leer:

```bash
flyctl ssh console --app scalerics-crm -C "python -c \"import database, sqlite3; conn=sqlite3.connect('/data/leads.db'); print(conn.execute(\\\"SELECT name FROM sqlite_master WHERE name LIKE 'finanzas%'\\\").fetchall()); print(conn.execute('SELECT name, panel_access FROM roles').fetchall())\""
```

Expected: aparecen `finanzas_movimientos`, `finanzas_recurrentes`, los dos índices, y `finanzas` dentro del `panel_access` de todos los roles.

- [ ] **Step 9: Cargar los fijos reales**

Desde el panel, en la vista **Fijos**, cargar los gastos que ya existen. Como mínimo:

- Fly.io — USD 4,18/mes, `infraestructura` (ver la memoria `project_fly_costos`)
- Vercel, Zoho, Resend, la API de Anthropic — `infraestructura` o `herramientas` según corresponda

Poner el `desde` en el mes en que efectivamente arrancó cada uno: la materialización va a generar todos los meses hacia atrás de una sola vez, y eso llena la serie histórica.

- [ ] **Step 10: Commit final**

```bash
git add COORDINACION.md
git commit -m "docs(coordinacion): sesion de finanzas anotada"
```

---

# Ampliación: el rendimiento de la pauta (Tasks 13 y 14)

> **Agregado el 8/9/2026, decisión de Juan, con la ejecución del plan ya
> empezada.** Existe `Downloads\Scalerics - Leads - 2026.xlsx` con dos hojas
> financieras que se llevaban a mano:
>
> - **Análisis** — marzo a agosto 2026, por mes: inversión en pauta, leads, CPL,
>   leads de calidad, demos, ventas. Totales: USD 3.017,15 invertidos, 227
>   leads, 64 de calidad, 44 demos, 2 ventas.
> - **Cuenta Corriente** — transferencias contra pauta gastada (2.200 contra
>   1.648,53). **Fuera de alcance**: Juan eligió no traerla.
>
> El CRM ya sabe cuántos leads, demos y ventas hay. Lo único que no sabía es
> cuánto se gastó. Con la inversión cargada como egresos de categoría
> `publicidad`, todo lo que la planilla calcula a mano sale solo.
>
> **Un error de la planilla que no se replica:** la hoja Análisis tiene 11
> encabezados y 10 columnas de datos. La columna rotulada «ROI» trae en realidad
> el *costo por venta* — 3.017,15 ÷ 2 = 1.508,575, que es el total que muestra.
> ROI de verdad nunca se pudo calcular ahí porque la planilla no tiene los
> ingresos. Acá sí, y va como columna aparte.

## Definiciones (fijadas por Juan, no son interpretables)

| Concepto | En términos del CRM |
|---|---|
| **Lead** | `businesses` con `source = 'meta'`. La pauta compra estos, no los scrapeados |
| **Mes del lead** | el mes en que entró (`scraped_at`), no el mes en que convirtió |
| **Lead de calidad** | llegó **al menos a `reunion_agendada`** en algún momento |
| **Demo** | llegó **al menos a `reunion_hecha`** en algún momento |
| **Venta** | llegó **al menos a `cliente_cerrado`** en algún momento |
| **Inversión** | egresos con `categoria = 'publicidad'` de ese período |

«En algún momento» es literal y se lee de `lead_events`, que guarda cada cambio
de estado con su fecha. **No** del `crm_status` actual: un lead que llegó a
reunión y después se cayó a `no_interesa` hoy figura como `no_interesa`, y
contarlo por el estado actual lo perdería.

`no_interesa` queda fuera del orden del embudo a propósito: es una salida, no
una etapa.

## Riesgo conocido, a verificar contra producción

**No está verificado que `lead_events` cubra marzo a agosto de 2026.** La base
local de desarrollo está vacía, así que no se pudo comprobar. Si la tabla no
llega tan atrás, o si en esos meses los estados se cambiaron sin dejar evento,
los meses históricos van a dar **por debajo** de la planilla.

Por eso la Task 14 no da los números por buenos: los compara contra el Excel mes
por mes y **reporta la diferencia**. Si coinciden, el CRM reemplaza la planilla.
Si no, sabemos que la historia no está completa y ahí se decide — pero se decide
viendo el número, no suponiéndolo.

---

### Task 13: El rendimiento de la pauta — servicio y ruta

**Files:**
- Modify: `services/finanzas.py` (al final)
- Modify: `routes/finanzas.py` (una ruta más)
- Test: `tests/test_finanzas_pauta.py`

**Interfaces:**
- Consumes: `database.listar_movimientos` y `database._connect` (Task 1); `meses_entre` (Task 2); el blueprint `finanzas_bp` con su `before_request` (Task 6).
- Produces:
  - `FUNNEL: list[str]`
  - `alcanzo(eventos: set, etapa: str) -> bool`
  - `rendimiento_pauta(db_path: str, desde: str, hasta: str) -> dict`

Forma exacta del dict, que el panel de la Task 14 consume tal cual:

```python
{
  "desde": "2026-03", "hasta": "2026-08",
  "meses": [
    {"periodo": "2026-03",
     "inversion_usd": 368.98,
     "leads": 59, "calificados": 4, "demos": 3, "ventas": 0,
     "ingresos_usd": 0.0,
     "cpl": 6.25, "costo_calificado": 92.25, "costo_demo": 122.99,
     "costo_venta": None,          # None, no 0: dividir por cero no es cero
     "roi": None},
  ],
  "total": {"periodo": "total", "...las mismas claves, sobre todo el rango": 0},
}
```

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_finanzas_pauta.py`:

```python
"""Rendimiento de la pauta: cuánto costó cada lead, cada demo y cada venta.

Lo que la planilla `Scalerics - Leads - 2026.xlsx` calculaba a mano cada mes.
El CRM ya tenía los leads, las demos y las ventas; lo único que le faltaba era
cuánta plata se gastó en traerlos.

Las etapas se leen de `lead_events`, no del `crm_status` actual: un lead que
llegó a reunión y después se cayó hoy figura como `no_interesa`, y contarlo por
el estado de hoy lo perdería.
"""

import sqlite3

import pytest

from database import crear_movimiento, init_db
from services.finanzas import alcanzo, rendimiento_pauta


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "p.db")
    init_db(ruta)
    return ruta


def _lead(db, periodo, source="meta", estados=()):
    """Crea un lead dado de alta en `periodo` que pasó por `estados`."""
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO businesses (name, source, scraped_at) VALUES (?,?,?)",
            (f"lead {periodo}", source, f"{periodo}-15 10:00:00"))
        lid = cur.lastrowid
        for estado in estados:
            conn.execute(
                "INSERT INTO lead_events (lead_id, new_status, created_at) "
                "VALUES (?,?,?)", (lid, estado, f"{periodo}-20 10:00:00"))
        conn.commit()
        return lid
    finally:
        conn.close()


def _pauta(db, periodo, monto):
    return crear_movimiento(db, tipo="egreso", fecha=f"{periodo}-05",
                            periodo=periodo, concepto="Meta Ads",
                            categoria="publicidad", monto=monto,
                            moneda="USD", monto_usd=monto)


# ── alcanzo ───────────────────────────────────────────────────────────────────

def test_alcanzo_es_verdadero_en_la_etapa_exacta():
    assert alcanzo({"reunion_agendada"}, "reunion_agendada") is True


def test_alcanzo_es_verdadero_si_paso_de_largo():
    assert alcanzo({"cliente_cerrado"}, "reunion_agendada") is True


def test_alcanzo_es_falso_si_no_llego():
    assert alcanzo({"interesado"}, "reunion_agendada") is False


def test_no_interesa_no_cuenta_como_etapa():
    """Es una salida del embudo, no un avance."""
    assert alcanzo({"no_interesa"}, "reunion_agendada") is False


def test_un_lead_que_llego_y_despues_se_cayo_sigue_contando():
    """El caso que rompía contar por crm_status actual."""
    assert alcanzo({"reunion_hecha", "no_interesa"}, "reunion_hecha") is True


def test_un_lead_sin_eventos_no_alcanzo_nada():
    assert alcanzo(set(), "reunion_agendada") is False


# ── rendimiento ───────────────────────────────────────────────────────────────

def test_cuenta_los_leads_del_mes_en_que_entraron(db):
    _lead(db, "2026-03")
    _lead(db, "2026-03")
    _lead(db, "2026-04")
    r = rendimiento_pauta(db, "2026-03", "2026-04")
    assert [m["leads"] for m in r["meses"]] == [2, 1]


def test_los_leads_scrapeados_no_cuentan(db):
    """La pauta compra leads de Meta, no el padrón scrapeado."""
    _lead(db, "2026-03", source="meta")
    _lead(db, "2026-03", source=None)
    _lead(db, "2026-03", source="calendly")
    assert rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]["leads"] == 1


def test_califica_desde_reunion_agendada(db):
    _lead(db, "2026-03", estados=("interesado",))
    _lead(db, "2026-03", estados=("interesado", "reunion_agendada"))
    _lead(db, "2026-03", estados=("reunion_hecha",))
    m = rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]
    assert m["calificados"] == 2
    assert m["demos"] == 1


def test_la_venta_cuenta_desde_cliente_cerrado(db):
    _lead(db, "2026-03", estados=("negociacion",))
    _lead(db, "2026-03", estados=("cliente_cerrado",))
    _lead(db, "2026-03", estados=("finalizado",))
    assert rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]["ventas"] == 2


def test_los_costos_dividen_la_inversion_del_mes(db):
    _pauta(db, "2026-03", 300.0)
    for _ in range(3):
        _lead(db, "2026-03")
    _lead(db, "2026-03", estados=("reunion_hecha",))
    m = rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]
    assert m["inversion_usd"] == 300.0
    assert m["leads"] == 4
    assert m["cpl"] == 75.0
    assert m["costo_demo"] == 300.0


def test_sin_ventas_el_costo_por_venta_es_none_no_cero(db):
    """La planilla mostraba #DIV/0! en esos meses. Cero sería mentira."""
    _pauta(db, "2026-03", 300.0)
    _lead(db, "2026-03")
    m = rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]
    assert m["costo_venta"] is None
    assert m["roi"] is None


def test_sin_inversion_los_costos_son_none(db):
    _lead(db, "2026-03")
    m = rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]
    assert m["inversion_usd"] == 0.0
    assert m["cpl"] is None


def test_el_roi_usa_los_ingresos_atribuidos_a_esos_leads(db):
    """Lo que la planilla nunca pudo calcular: no tenía los ingresos."""
    _pauta(db, "2026-03", 500.0)
    lid = _lead(db, "2026-03", estados=("cliente_cerrado",))
    crear_movimiento(db, tipo="ingreso", fecha="2026-04-10", periodo="2026-04",
                     concepto="Cobro", categoria="desarrollo_web", monto=1500,
                     moneda="USD", monto_usd=1500.0, client_id=lid)
    m = rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]
    assert m["ingresos_usd"] == 1500.0
    assert m["roi"] == 3.0


def test_un_ingreso_anulado_no_cuenta_en_el_roi(db):
    from database import actualizar_movimiento
    _pauta(db, "2026-03", 500.0)
    lid = _lead(db, "2026-03", estados=("cliente_cerrado",))
    mid = crear_movimiento(db, tipo="ingreso", fecha="2026-04-10",
                           periodo="2026-04", concepto="Cobro",
                           categoria="desarrollo_web", monto=1500,
                           moneda="USD", monto_usd=1500.0, client_id=lid)
    actualizar_movimiento(db, mid, anulado=1)
    m = rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]
    assert m["ingresos_usd"] == 0.0
    assert m["roi"] is None


def test_solo_cuenta_la_publicidad_no_los_demas_egresos(db):
    _pauta(db, "2026-03", 300.0)
    crear_movimiento(db, tipo="egreso", fecha="2026-03-01", periodo="2026-03",
                     concepto="Fly", categoria="infraestructura", monto=4.18,
                     moneda="USD", monto_usd=4.18)
    m = rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]
    assert m["inversion_usd"] == 300.0


def test_el_total_agrega_todo_el_rango(db):
    _pauta(db, "2026-03", 300.0)
    _pauta(db, "2026-04", 200.0)
    _lead(db, "2026-03")
    _lead(db, "2026-04")
    t = rendimiento_pauta(db, "2026-03", "2026-04")["total"]
    assert t["inversion_usd"] == 500.0
    assert t["leads"] == 2
    assert t["cpl"] == 250.0


def test_un_mes_sin_nada_aparece_igual(db):
    _pauta(db, "2026-03", 300.0)
    r = rendimiento_pauta(db, "2026-03", "2026-05")
    assert [m["periodo"] for m in r["meses"]] == ["2026-03", "2026-04", "2026-05"]
    assert r["meses"][2]["leads"] == 0
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `python -m pytest tests/test_finanzas_pauta.py -v`
Expected: FAIL con `ImportError: cannot import name 'alcanzo' from 'services.finanzas'`

- [ ] **Step 3: Escribir la implementación**

Al final de `services/finanzas.py`:

```python
# ─── Rendimiento de la pauta ─────────────────────────────────────────────────

# El embudo en orden, tal como lo define routes/leads.py. `no_interesa` NO está:
# es una salida, no una etapa, y un lead que se cayó ahí igual pasó por lo que
# haya pasado antes.
FUNNEL = ["sin_contactar", "interesado", "contactado", "reunion_agendada",
          "reunion_hecha", "presupuesto_enviado", "negociacion",
          "cliente_cerrado", "en_desarrollo", "finalizado"]


def alcanzo(eventos: set, etapa: str) -> bool:
    """Si el lead pasó por `etapa` o por cualquiera posterior, alguna vez.

    Se mira contra el historial de `lead_events`, no contra el `crm_status` de
    hoy: un lead que llegó a reunión y después se cayó a `no_interesa` figura
    hoy como `no_interesa`, y contarlo por el estado actual lo perdería.
    """
    objetivo = FUNNEL.index(etapa)
    return any(e in FUNNEL and FUNNEL.index(e) >= objetivo for e in eventos)


def _dividir(numerador: float, denominador: float):
    """El costo unitario, o None si no hay de qué dividir.

    Devuelve None y no 0.0 a propósito: un mes sin ventas no tiene un costo por
    venta de cero, no tiene costo por venta. La planilla mostraba #DIV/0! y esa
    era la lectura correcta.
    """
    if not denominador:
        return None
    return round(numerador / denominador, 2)


def _fila_pauta(periodo, inversion, leads, calificados, demos, ventas, ingresos):
    return {
        "periodo": periodo,
        "inversion_usd": round(inversion, 2),
        "leads": leads, "calificados": calificados,
        "demos": demos, "ventas": ventas,
        "ingresos_usd": round(ingresos, 2),
        "cpl": _dividir(inversion, leads),
        "costo_calificado": _dividir(inversion, calificados),
        "costo_demo": _dividir(inversion, demos),
        "costo_venta": _dividir(inversion, ventas),
        "roi": _dividir(ingresos, inversion),
    }


def rendimiento_pauta(db_path: str, desde: str, hasta: str) -> dict:
    """Qué compró la plata de pauta, mes a mes.

    Reemplaza la hoja «Análisis» de `Scalerics - Leads - 2026.xlsx`, que se
    llevaba a mano. Los leads se cuentan por el mes en que entraron, no por el
    mes en que convirtieron: la pauta de marzo compró los leads de marzo, aunque
    uno cierre en julio. Por lo mismo, el ingreso se atribuye al lead que lo
    generó y no al mes del cobro.
    """
    from database import _connect, listar_movimientos

    periodos = meses_entre(desde, hasta)
    if not periodos:
        return {"desde": desde, "hasta": hasta, "meses": [],
                "total": _fila_pauta("total", 0, 0, 0, 0, 0, 0)}

    inversion = {p: 0.0 for p in periodos}
    for m in listar_movimientos(db_path, desde=desde, hasta=hasta,
                                tipo="egreso", categoria="publicidad"):
        inversion[m["periodo"]] = inversion.get(m["periodo"], 0.0) + m["monto_usd"]

    conn = _connect(db_path)
    try:
        leads = conn.execute(
            "SELECT id, substr(scraped_at, 1, 7) AS periodo "
            "FROM businesses WHERE source = 'meta'").fetchall()
        eventos_filas = conn.execute(
            "SELECT lead_id, new_status FROM lead_events").fetchall()
        ingresos_filas = conn.execute(
            "SELECT client_id, monto_usd FROM finanzas_movimientos "
            "WHERE tipo = 'ingreso' AND anulado = 0 AND client_id IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    eventos: dict = {}
    for fila in eventos_filas:
        eventos.setdefault(fila["lead_id"], set()).add(fila["new_status"])

    ingresos_por_lead: dict = {}
    for fila in ingresos_filas:
        ingresos_por_lead[fila["client_id"]] = (
            ingresos_por_lead.get(fila["client_id"], 0.0) + fila["monto_usd"])

    conteo = {p: {"leads": 0, "calificados": 0, "demos": 0, "ventas": 0,
                  "ingresos": 0.0} for p in periodos}
    for lead in leads:
        periodo = lead["periodo"]
        if periodo not in conteo:
            continue
        suyos = eventos.get(lead["id"], set())
        c = conteo[periodo]
        c["leads"] += 1
        c["ingresos"] += ingresos_por_lead.get(lead["id"], 0.0)
        if alcanzo(suyos, "reunion_agendada"):
            c["calificados"] += 1
        if alcanzo(suyos, "reunion_hecha"):
            c["demos"] += 1
        if alcanzo(suyos, "cliente_cerrado"):
            c["ventas"] += 1

    meses = [_fila_pauta(p, inversion.get(p, 0.0), conteo[p]["leads"],
                         conteo[p]["calificados"], conteo[p]["demos"],
                         conteo[p]["ventas"], conteo[p]["ingresos"])
             for p in periodos]

    total = _fila_pauta("total",
                        sum(inversion.get(p, 0.0) for p in periodos),
                        sum(c["leads"] for c in conteo.values()),
                        sum(c["calificados"] for c in conteo.values()),
                        sum(c["demos"] for c in conteo.values()),
                        sum(c["ventas"] for c in conteo.values()),
                        sum(c["ingresos"] for c in conteo.values()))

    return {"desde": desde, "hasta": hasta, "meses": meses, "total": total}
```

- [ ] **Step 4: Agregar la ruta**

En `routes/finanzas.py`, junto a `api_resumen`, agregando `rendimiento_pauta` al
import de `services.finanzas`:

```python
@finanzas_bp.route("/api/finanzas/pauta")
def api_pauta():
    """Rendimiento de la pauta: qué compró cada dólar invertido."""
    db = _db()
    hoy = date.today()
    mes_actual = f"{hoy.year:04d}-{hoy.month:02d}"
    desde = request.args.get("desde") or mes_actual
    hasta = request.args.get("hasta") or mes_actual
    if desde > hasta:
        return jsonify({"ok": False, "error": "desde tiene que ser <= hasta"}), 400
    return jsonify(rendimiento_pauta(db, desde, hasta))
```

Sumar a `tests/test_finanzas_pauta.py` dos tests de la ruta. Para las fixtures,
copiar el patrón de `app` / `_rol` / `_usuario` / `_cli` de
`tests/test_finanzas_rutas.py`:

```python
def test_la_ruta_de_pauta_pide_el_panel(app):
    """Mismo candado que el resto del blueprint."""
    db = app.config["_DB"]
    uid = _usuario(db, "caller@scalerics.com", _rol(db, "Caller", ["cola"]))
    assert _cli(app, uid).get("/api/finanzas/pauta").status_code == 403


def test_la_ruta_de_pauta_rechaza_el_rango_al_reves(cli):
    r = cli.get("/api/finanzas/pauta?desde=2026-09&hasta=2026-08")
    assert r.status_code == 400
```

- [ ] **Step 5: Correr los tests**

Run: `python -m pytest tests/test_finanzas_pauta.py -v`
Expected: PASS, 19 tests

- [ ] **Step 6: Correr la suite entera**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add services/finanzas.py routes/finanzas.py tests/test_finanzas_pauta.py
git commit -m "feat(finanzas): rendimiento de la pauta por mes"
```

---

### Task 14: La vista de pauta y la carga histórica

**Files:**
- Modify: `dashboard.py` (un toggle más, la tabla de pauta y su CSS)
- Create: `scripts/cargar_pauta_historica.py`
- Test: `tests/test_finanzas_panel.py` (dos casos más)

**Interfaces:**
- Consumes: `GET /api/finanzas/pauta` (Task 13); `_finUsd`, `_finRango`, `finVista`, `esc` (Tasks 7-10).
- Produces: `loadPauta()`, `_finNum(v, prefijo)`, y `scripts/cargar_pauta_historica.py`.

- [ ] **Step 1: Agregar el tercer toggle y el contenedor**

En el `.fin-toolbar` del panel, junto a los dos botones que ya están:

```html
      <button class="pill" id="fin-tab-pauta" onclick="finVista('pauta')">Pauta</button>
```

Y el contenedor, junto a las otras dos vistas:

```html
  <div id="fin-vista-pauta" style="display:none">
    <div class="fin-card"><div class="fin-card-title">Qué compró la pauta</div>
      <div id="fin-pauta"></div></div>
  </div>
```

`finVista` hoy asume dos vistas (un booleano `esMovs`). Reescribirla para tres:

```javascript
const FIN_VISTAS = ['movimientos', 'fijos', 'pauta'];

function finVista(cual) {
  FIN_VISTAS.forEach(v => {
    document.getElementById(`fin-vista-${v}`).style.display = v === cual ? '' : 'none';
  });
  document.getElementById('fin-tab-movs').classList.toggle('active', cual === 'movimientos');
  document.getElementById('fin-tab-fijos').classList.toggle('active', cual === 'fijos');
  document.getElementById('fin-tab-pauta').classList.toggle('active', cual === 'pauta');
  if (cual === 'fijos') loadFijos();
  if (cual === 'pauta') loadPauta();
}
```

- [ ] **Step 2: Escribir el render**

```javascript
function _finNum(v, prefijo) {
  // Un guión, no un cero: un mes sin ventas no tiene un costo por venta de
  // cero, no tiene costo por venta.
  if (v === null || v === undefined) return '—';
  return (prefijo || '') + v.toLocaleString('es-UY', {minimumFractionDigits: 2,
                                                      maximumFractionDigits: 2});
}

async function loadPauta() {
  const cuerpo = document.getElementById('fin-pauta');
  const {desde, hasta} = _finRango();
  cuerpo.innerHTML = '<div style="color:#475569;padding:16px;font-size:.85rem">Cargando...</div>';
  try {
    const r = await fetch(`/api/finanzas/pauta?desde=${desde}&hasta=${hasta}`);
    if (!r.ok) throw new Error('no se pudo cargar el rendimiento');
    const data = await r.json();

    const fila = (m, esTotal) => `
      <tr style="${esTotal ? 'font-weight:700;border-top:2px solid #1e293b' : ''}">
        <td>${esTotal ? 'Total' : m.periodo}</td>
        <td class="fin-rojo">${_finNum(m.inversion_usd, 'USD ')}</td>
        <td>${m.leads}</td>
        <td>${_finNum(m.cpl, 'USD ')}</td>
        <td>${m.calificados}</td>
        <td>${_finNum(m.costo_calificado, 'USD ')}</td>
        <td>${m.demos}</td>
        <td>${_finNum(m.costo_demo, 'USD ')}</td>
        <td>${m.ventas}</td>
        <td>${_finNum(m.costo_venta, 'USD ')}</td>
        <td class="fin-verde">${_finNum(m.ingresos_usd, 'USD ')}</td>
        <td>${m.roi === null ? '—' : m.roi.toFixed(2) + '×'}</td>
      </tr>`;

    cuerpo.innerHTML = `
      <div style="overflow-x:auto">
      <table class="fin-tabla">
        <thead><tr>
          <th>Mes</th><th>Inversión</th><th>Leads</th><th>CPL</th>
          <th>Calificados</th><th>Costo</th><th>Demos</th><th>Costo</th>
          <th>Ventas</th><th>Costo</th><th>Ingresos</th><th>ROI</th>
        </tr></thead>
        <tbody>${data.meses.map(m => fila(m, false)).join('')}${fila(data.total, true)}</tbody>
      </table></div>`;
  } catch (e) {
    cuerpo.innerHTML = `<div style="color:#f87171;padding:16px">Error: ${esc(e.message)}</div>`;
  }
}
```

Y el CSS, junto al resto del bloque `.fin-*`:

```css
.fin-tabla{width:100%;border-collapse:collapse;font-size:.8rem}
.fin-tabla th{text-align:left;padding:8px 10px;color:#64748b;font-size:.68rem;
              text-transform:uppercase;letter-spacing:.6px;white-space:nowrap}
.fin-tabla td{padding:8px 10px;color:#e2e8f0;white-space:nowrap;
              border-top:1px solid #1e293b}
body.light .fin-tabla td{color:#1e293b;border-top-color:#e2e8f0}
```

- [ ] **Step 3: Escribir el script de carga histórica**

Crear `scripts/cargar_pauta_historica.py`:

```python
"""Carga los seis meses de pauta de `Scalerics - Leads - 2026.xlsx` y contrasta.

Hace dos cosas, en este orden:

1. Inserta la inversión de marzo a agosto de 2026 como egresos de categoría
   `publicidad`, para que la sección financiera no arranque vacía.
2. Compara los leads, calificados, demos y ventas que el CRM calcula contra los
   que la planilla trae a mano, mes por mes, y muestra la diferencia.

El paso 2 es el que importa. No está verificado que `lead_events` cubra esos
seis meses: si no llega tan atrás, los números del CRM van a dar por debajo de
los de la planilla. Este script no arregla eso — lo muestra, para que la
decisión se tome viendo el número y no suponiéndolo.

Es idempotente: si ya cargó un mes, no lo duplica.

Uso:  python scripts/cargar_pauta_historica.py [--db leads.db] [--aplicar]
Sin `--aplicar` no escribe nada: solo muestra qué haría y el contraste.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import crear_movimiento, listar_movimientos  # noqa: E402
from services.finanzas import rendimiento_pauta  # noqa: E402

# Copiado a mano de la hoja «Análisis». La columna rotulada «ROI» en la planilla
# es en realidad el costo por venta (3017.15 / 2 = 1508.575), así que no se
# transcribe: el ROI de verdad lo calcula el CRM con los ingresos.
PLANILLA = [
    # periodo,  inversion, leads, calificados, demos, ventas
    ("2026-03", 368.98, 59, 4, 3, 0),
    ("2026-04", 528.52, 25, 6, 5, 1),
    ("2026-05", 452.18, 28, 7, 8, 0),
    ("2026-06", 608.01, 49, 19, 12, 1),
    ("2026-07", 610.48, 40, 17, 11, 0),
    ("2026-08", 448.98, 26, 11, 5, 0),
]


def cargar(db_path: str, aplicar: bool) -> int:
    ya = {m["periodo"] for m in listar_movimientos(db_path, categoria="publicidad")}
    creados = 0
    for periodo, inversion, *_ in PLANILLA:
        if periodo in ya:
            print(f"  {periodo}  ya estaba, no se toca")
            continue
        print(f"  {periodo}  USD {inversion:>7.2f}  "
              f"{'CARGANDO' if aplicar else '(simulacro)'}")
        if aplicar:
            crear_movimiento(db_path, tipo="egreso", fecha=f"{periodo}-01",
                             periodo=periodo, concepto="Meta Ads",
                             categoria="publicidad", monto=inversion,
                             moneda="USD", monto_usd=inversion,
                             notas="Importado de Scalerics - Leads - 2026.xlsx",
                             created_by_name="carga histórica")
            creados += 1
    return creados


def contrastar(db_path: str) -> bool:
    r = rendimiento_pauta(db_path, PLANILLA[0][0], PLANILLA[-1][0])
    por_periodo = {m["periodo"]: m for m in r["meses"]}
    print(f"\n{'Mes':<9} {'concepto':<13} {'planilla':>9} {'CRM':>7} {'dif':>7}")
    print("-" * 50)
    coincide = True
    for periodo, _, leads, calificados, demos, ventas in PLANILLA:
        m = por_periodo.get(periodo, {})
        for etiqueta, esperado, clave in (("leads", leads, "leads"),
                                          ("calificados", calificados, "calificados"),
                                          ("demos", demos, "demos"),
                                          ("ventas", ventas, "ventas")):
            real = m.get(clave, 0)
            dif = real - esperado
            if dif:
                coincide = False
            marca = "" if not dif else ("  <<<" if abs(dif) > 2 else "  <")
            print(f"{periodo:<9} {etiqueta:<13} {esperado:>9} {real:>7} {dif:>+7}{marca}")
    return coincide


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=os.environ.get("DB_PATH", "leads.db"))
    p.add_argument("--aplicar", action="store_true",
                   help="escribir de verdad (sin esto es un simulacro)")
    args = p.parse_args()

    print(f"Base: {args.db}\n\nInversión en pauta:")
    creados = cargar(args.db, args.aplicar)
    print(f"\n{creados} movimiento(s) creado(s).")

    print("\nContraste contra la planilla:")
    if contrastar(args.db):
        print("\nTodo coincide. El CRM reemplaza la hoja «Análisis».")
    else:
        print("\nHay diferencias. Lo mas probable es que `lead_events` no cubra")
        print("todos esos meses, asi que la historia vieja esta incompleta y el")
        print("CRM cuenta de menos. No lo decidas sin mirar estos numeros.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Agregar los tests del panel**

En `tests/test_finanzas_panel.py`:

```python
def test_el_panel_tiene_la_vista_de_pauta():
    assert 'id="fin-vista-pauta"' in HTML
    assert "function loadPauta(" in HTML


def test_un_costo_sin_denominador_se_muestra_como_guion():
    """La planilla mostraba #DIV/0!. Un cero ahí sería mentira."""
    assert "function _finNum(" in HTML
    assert "return '—'" in HTML
```

- [ ] **Step 5: Correr los tests y el validador de JS**

Run: `python -m pytest tests/test_finanzas_panel.py -v && python scripts/check_js.py`
Expected: PASS

- [ ] **Step 6: Correr el script en simulacro**

Run: `python scripts/cargar_pauta_historica.py --db leads.db`

Expected: corre sin romperse y muestra el contraste. Contra la base de
desarrollo, que está vacía, el CRM va a dar 0 en todo y la diferencia va a ser
el total de la planilla — eso confirma que el script funciona, no que los
números estén mal. Si `leads.db` no existe en el worktree, crearla con
`python -c "from database import init_db; init_db('leads.db')"` primero.

- [ ] **Step 7: Correr la suite entera**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add dashboard.py scripts/cargar_pauta_historica.py tests/test_finanzas_panel.py
git commit -m "feat(finanzas): vista de pauta y carga historica de la planilla"
```


---

## Notas para quien ejecute

**El test que más importa de todo el plan** es `test_correrlo_dos_veces_no_duplica` (Task 3). Sin el índice único, cada deploy —que reinicia la máquina, que vuelve a materializar— duplicaría los gastos del mes, y el panel mostraría el doble de egresos sin que nada falle ruidosamente.

**No agregar hilos al arranque.** La materialización es perezosa a propósito. Si en algún momento parece buena idea moverla a un hilo de boot, leer primero la regla 3 de `COORDINACION.md`: los jobs de arranque de este repo ya provocaron una tanda de mails reales a clientes.

**`dashboard.py` es territorio compartido.** Antes de empezar, `git pull` y `git status`. Antes de deployar, el árbol limpio.

**Si un test de `dashboard.py` falla raro**, correr `python scripts/check_js.py`: un `\n` con una sola barra dentro de un string de Python se convierte en salto de línea real y parte el JavaScript sin que pytest se entere.
