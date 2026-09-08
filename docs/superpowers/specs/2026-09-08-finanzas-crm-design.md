# Sección financiera del CRM

Fecha: 2026-09-08
Estado: diseño aprobado, pendiente de plan de implementación

## Problema

El CRM sabe a quién le vendemos y por cuánto —hay presupuestos con montos y
clientes cerrados— pero no sabe cuánta plata entró ni cuánta salió. Los gastos
fijos de la operación (Fly, Vercel, Zoho, Resend, la API de Anthropic) viven en
resúmenes de tarjeta y en la cabeza de Juan. No hay un lugar donde mirar el mes
y saber si cerró en verde.

Esa información existe pero está desparramada, y el costo de juntarla a mano
cada mes es alto como para hacerlo, así que no se hace.

## Alcance

Un panel nuevo, **Finanzas**, con el libro de ingresos y egresos de Scalerics.
Cada movimiento puede atribuirse opcionalmente a un cliente, de modo que los
mismos datos sirven para ver el resultado global del mes y la rentabilidad de
una cuenta.

Incluye:

- Carga manual de ingresos y egresos, con atajo desde un presupuesto aprobado.
- Gastos e ingresos **fijos** que se repiten todos los meses sin intervención.
- Vista mensual: KPIs del período, serie de los últimos 12 meses, desglose por
  categoría y por cliente.
- Dos monedas (UYU y USD) con totales en USD.

Fuera de alcance: facturación, cuentas por cobrar / por pagar, conciliación
bancaria, integración con Mercado Pago o con un banco, impuestos calculados,
y multi-empresa. Nada de eso se pidió y todo se puede sumar después sobre estas
mismas tablas.

## Decisiones

Cinco decisiones tomadas en el brainstorming, con su razón:

**El libro es de Scalerics, con atribución opcional a cliente.** No hay una
contabilidad por cliente separada: hay un solo libro y una columna `client_id`.
Un gasto de infraestructura no es de nadie; un cobro sí. Filtrar por cliente da
la vista por cuenta sin duplicar el modelo.

**Los ingresos se cargan a mano.** Un presupuesto aprobado no genera plata:
genera una expectativa. Registrar el cobro es un acto explícito. El atajo desde
el presupuesto precarga los campos, pero alguien tiene que apretar el botón.

**Dos monedas, totales en USD.** Fly, Vercel y Anthropic cobran en dólares;
varios clientes pagan en pesos. Cada movimiento guarda su moneda original y su
tipo de cambio, y se congela un `monto_usd` al guardar.

**Los fijos se materializan como movimientos reales**, no se calculan al vuelo.
Un fijo genera una fila por mes, editable como cualquier otra. Así un mes en el
que Fly cobró distinto se corrige tocando esa fila, sin inventar excepciones en
la definición del fijo.

**El panel se asigna por rol**, como los demás, pero con el candado del lado del
servidor (ver Permisos).

## Arquitectura

```
database.py                 + tablas finanzas_movimientos y finanzas_recurrentes
                            + accesos CRUD
services/finanzas.py        conversión, materialización de fijos, agregados
services/auth.py            + require_panel()
routes/finanzas.py          endpoints /api/finanzas/*
dashboard.py                el panel (HTML + JS de render) y el registro del nav
tests/test_finanzas.py      la suite
```

El JavaScript del panel dibuja lo que le mandan; no convierte monedas ni suma
meses. Toda la aritmética que puede dar un número mal está en
`services/finanzas.py`, que se testea sin levantar Flask ni un browser. Un total
de egresos equivocado en un panel financiero es peor que un bug de UI: se cree.

## Datos

### `finanzas_movimientos`

```sql
CREATE TABLE IF NOT EXISTS finanzas_movimientos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo            TEXT NOT NULL,              -- 'ingreso' | 'egreso'
    fecha           TEXT NOT NULL,              -- 'YYYY-MM-DD'
    periodo         TEXT NOT NULL,              -- 'YYYY-MM'
    concepto        TEXT NOT NULL,
    categoria       TEXT NOT NULL,
    monto           REAL NOT NULL,              -- en la moneda original
    moneda          TEXT NOT NULL,              -- 'USD' | 'UYU'
    tipo_cambio     REAL,                       -- UYU por USD; NULL si moneda='USD'
    monto_usd       REAL NOT NULL,              -- congelado al guardar
    client_id       INTEGER REFERENCES businesses(id),
    budget_id       INTEGER REFERENCES budgets(id),
    recurrente_id   INTEGER REFERENCES finanzas_recurrentes(id),
    anulado         INTEGER NOT NULL DEFAULT 0,
    notas           TEXT,
    created_by_id   INTEGER,
    created_by_name TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_finanzas_recurrente_periodo
    ON finanzas_movimientos (recurrente_id, periodo)
    WHERE recurrente_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_finanzas_periodo
    ON finanzas_movimientos (periodo);
```

`periodo` se guarda, no se deriva con `strftime` en cada consulta: es lo que
agrupa todos los agregados y lo que sostiene el índice único de los fijos.

`monto_usd` se congela al guardar. Si se recalculara con el dólar del día, el
resultado de julio cambiaría cada vez que se mueve el tipo de cambio y ningún
mes quedaría cerrado nunca. La contrapartida: un tipo de cambio mal cargado se
arregla editando el movimiento, no solo.

La atribución va a `businesses(id)`, que es lo que ya usan `budgets`, `tasks` y
el panel Clientes. `notion_clients` es un espejo de solo lectura de Notion; no
se le cuelga plata a algo que se reescribe en cada sync.

### `finanzas_recurrentes`

```sql
CREATE TABLE IF NOT EXISTS finanzas_recurrentes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo        TEXT NOT NULL,              -- 'ingreso' | 'egreso'
    concepto    TEXT NOT NULL,
    categoria   TEXT NOT NULL,
    monto       REAL NOT NULL,
    moneda      TEXT NOT NULL,
    tipo_cambio REAL,
    dia_del_mes INTEGER NOT NULL DEFAULT 1,  -- 1..28
    desde       TEXT NOT NULL,               -- 'YYYY-MM'
    hasta       TEXT,                        -- 'YYYY-MM' o NULL si sigue vivo
    activo      INTEGER NOT NULL DEFAULT 1,
    client_id   INTEGER REFERENCES businesses(id),
    notas       TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

`dia_del_mes` se topea en 28 en la validación. No hay 30 de febrero y no hace
falta lógica de "último día del mes" para un caso que no existe hoy.

### Categorías

Lista fija, constante en `services/finanzas.py`, no texto libre. Con texto libre
alcanza con escribir "Infra" una vez en lugar de "Infraestructura" para que el
desglose por categoría se parta en dos sin que nadie lo note.

```python
CATEGORIAS = {
    "egreso":  ["infraestructura", "herramientas", "publicidad",
                "retiros", "impuestos", "servicios", "otros"],
    "ingreso": ["desarrollo_web", "software_medida", "mantenimiento",
                "marketing", "otros"],
}
```

Se expone en `GET /api/finanzas/categorias` para que el front no la duplique.

## `services/finanzas.py`

```python
CATEGORIAS: dict[str, list[str]]

def periodo_de(fecha: str) -> str
    # '2026-09-20' -> '2026-09'

def a_usd(monto: float, moneda: str, tipo_cambio: float | None) -> float
    # UYU sin tipo_cambio > 0 levanta ValueError.

def materializar_recurrentes(db_path: str, hoy: date | None = None) -> int
    # Genera los movimientos faltantes de cada fijo activo, mes a mes,
    # desde `desde` hasta el mes en curso inclusive. Devuelve cuántos creó.

def resumen(db_path: str, desde: str, hasta: str) -> dict
    # desde/hasta son períodos 'YYYY-MM', ambos inclusive.
    # {"kpis": {...}, "serie": [...], "por_categoria": [...], "por_cliente": [...]}
    # kpis trae ingresos_usd, egresos_usd, neto_usd, y los mismos tres del
    # período inmediatamente anterior de igual longitud, para la variación.
```

### Materialización de los fijos

El fijo del mes en curso se genera el día 1, con la fecha de su `dia_del_mes`,
aunque ese día todavía no haya llegado. Así el mes muestra su costo fijo
completo. La contrapartida, aceptada: a mitad de mes el neto ya descuenta gastos
que no se pagaron.

Nunca se generan períodos futuros, ni anteriores a `desde`, ni posteriores a
`hasta`, ni de fijos con `activo = 0`.

`INSERT OR IGNORE` contra el índice único `(recurrente_id, periodo)`: correr la
materialización mil veces produce exactamente un movimiento por fijo y por mes.
Esa es la guarda que exige la regla 3 de `COORDINACION.md`. Sin ella, cada
deploy —que reinicia la máquina— duplicaría los gastos del mes.

**Se llama perezosamente**, al principio de `GET /api/finanzas/resumen`. No hay
hilo de arranque. Los jobs de boot de este repo ya provocaron una tanda de
mails reales; un hilo más es una cosa más que puede fallar sin que nadie mire.
El costo de la alternativa perezosa es un puñado de `INSERT OR IGNORE` cuando se
abre el panel.

Si un fijo está en UYU, la materialización usa el `tipo_cambio` guardado en el
fijo. El movimiento generado es editable, así que un mes con otro dólar se
corrige en la fila.

### Editar y borrar un movimiento generado por un fijo

Editarlo es seguro: la materialización usa `INSERT OR IGNORE`, así que ve el
período ya ocupado y no pisa nada. El monto corregido sobrevive.

Borrarlo necesita cuidado, porque un `DELETE` liberaría el par
`(recurrente_id, periodo)` y el fijo volvería a generarlo en la próxima
materialización —el movimiento reaparecería solo—. Por eso:

- Borrar un movimiento **generado por un fijo** pone `anulado = 1`. La fila
  queda ocupando su período y no se regenera.
- Borrar un movimiento **cargado a mano** es un `DELETE` real.

Todos los agregados y listados filtran `anulado = 0`.

## Rutas — `routes/finanzas.py`

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/api/finanzas/resumen?desde=&hasta=` | materializa y devuelve KPIs, serie, desgloses |
| GET | `/api/finanzas/movimientos?periodo=&tipo=&categoria=&client_id=` | lista filtrada |
| POST | `/api/finanzas/movimientos` | crea |
| PUT | `/api/finanzas/movimientos/<id>` | edita |
| DELETE | `/api/finanzas/movimientos/<id>` | borra |
| GET | `/api/finanzas/recurrentes` | lista los fijos |
| POST | `/api/finanzas/recurrentes` | crea |
| PUT | `/api/finanzas/recurrentes/<id>` | edita |
| DELETE | `/api/finanzas/recurrentes/<id>` | borra |
| GET | `/api/finanzas/categorias` | la constante |

Las rutas son finas: validan, llaman al servicio y serializan. Crear, editar y
borrar dejan rastro en `activity_log`, igual que `tasks`.

Borrar un fijo no borra los movimientos que ya generó: son plata que se gastó.
Para dejar de generar hacia adelante está `activo = 0` o `hasta`.

## Permisos

Se suma a `services/auth.py`, al lado de `require_admin`:

```python
def require_panel(db_path: str, panel: str):
    """403 si el usuario de la sesión no tiene ese panel. None si puede seguir."""
```

Un admin pasa siempre. Se engancha con `@finanzas_bp.before_request`: una línea,
cubre todo el blueprint, y no hay forma de agregar una ruta el mes que viene y
olvidarse del candado.

Esto importa porque hoy `panel_access` **solo esconde el ítem del menú**: nada
frena un `fetch('/api/...')` de un usuario logueado que no tiene el panel. Para
los leads es tolerable; para la plata no.

Se mantiene el bypass de `x-admin-token` que ya tienen las demás APIs. El token
es de la casa y romper el patrón acá no compra nada.

En `database.py`, junto a la línea equivalente de `meta`:

```python
_grant_panel_to_existing_roles(conn, "finanzas")
```

Sin eso, en producción —donde la tabla `roles` ya tiene filas— el panel no le
llega a nadie salvo a los admin. Es exactamente el problema que esa función
existe para resolver.

## El panel

### Registro — los siete lugares

Agregar un panel toca siete lugares: seis en `dashboard.py` y uno en
`database.py`. Dos fallan en silencio si se olvidan:

1. El ítem del nav (`~1147`), bajo **GESTIÓN**, arriba de Métricas, ícono `wallet`.
2. `ALL_PANELS` (`~4538`).
3. `ALL_PANELS` y `PANEL_LABELS` del editor de roles (`~6784`). **Si falta, el
   panel no se le puede asignar a nadie.**
4. `NAV_PRIORITY` (`~4447`) y `NAV_ICONS` (`~4455`).
5. El dispatch de `showPanel` (`~1750`): `if (name === 'finanzas') loadFinanzas();`
6. El `<div class="panel" id="finanzas-panel">`.
7. `_grant_panel_to_existing_roles(conn, "finanzas")` en `database.py`. **Si
   falta, en producción no lo ve nadie salvo los admin.**

En mobile cae en "Más secciones": `_buildMobileNav` solo muestra los primeros
cinco de `NAV_PRIORITY`.

### La vista

De arriba a abajo:

- **Barra**: selector de período (mes actual / últimos 3 / últimos 12 / año),
  botón **+ Movimiento**, toggle **Movimientos ↔ Fijos**.
- **Tres KPIs**: Ingresos, Egresos, Resultado. El resultado en verde o rojo, con
  la variación contra el período anterior.
- **Serie mensual**: barras de ingresos contra egresos, últimos 12 meses. CSS
  puro, mismo tratamiento que `_funnelBars`. No se suma ninguna librería de
  charts ni ningún CDN.
- **Dos desgloses** lado a lado: egresos por categoría e ingresos por cliente,
  barras horizontales.
- **Tabla de movimientos** del período, filtrable por tipo, categoría y cliente,
  con cada fila editable.
- **Vista Fijos**: tabla de recurrentes con su estado, y cuánto suman por mes
  en USD.

Colores consistentes con lo que ya usa el CRM: verde `#10b981` para ingresos,
rojo `#f87171` para egresos. Se cubre `body.light`, que el CRM tiene modo claro.
Íconos lucide, sin emojis.

### El modal de movimiento

Tipo (toggle ingreso/egreso), fecha, concepto, categoría (el select se filtra
según el tipo), monto y moneda, cliente opcional, notas.

Al elegir **UYU** aparece el campo de tipo de cambio y, debajo, en vivo, el
monto en USD que se va a guardar. El número congelado se ve antes de
congelarlo, no después.

### El atajo desde presupuesto

Un botón **Registrar cobro** en la ficha del presupuesto abre ese mismo modal
con tipo, cliente, monto y `budget_id` precargados. Es solo front: no se toca
`routes/budgets.py`, que `COORDINACION.md` asigna a la sesión B.

## Tests — `tests/test_finanzas.py`

- `a_usd` convierte bien y **rechaza** UYU sin tipo de cambio.
- Materializar dos veces no duplica. Este es el caso del deploy y es el test que
  más importa de la suite.
- La materialización respeta `desde`, `hasta` y `activo`, y no genera futuro.
- El resumen suma bien mezclando UYU y USD en el mismo mes.
- Un usuario sin el panel `finanzas` recibe 403 de una ruta del blueprint.
- Borrar un fijo deja vivos los movimientos que ya había generado.
- Editar un movimiento generado por un fijo y volver a materializar: el monto
  editado sobrevive.
- Borrar un movimiento generado por un fijo y volver a materializar: **no
  reaparece**, y no cuenta en los totales.

## Coordinación

`COORDINACION.md` asigna `dashboard.py` a la sesión **B**. Hay que anotarse ahí
antes de empezar y avisar del cruce. `database.py` está declarado zona
compartida: también hay que avisar.

Nada de esto agrega hilos al arranque ni manda correo, así que no toca las
reglas 3 y 4.
