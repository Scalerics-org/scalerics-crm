# Puesta en producción del módulo de Marketing

Qué falta para que el panel deje de decir «sin datos» en los bloques de costo.
Son dos cosas, y ninguna la puede hacer el código solo.

## Por qué no alcanza el token que ya tenemos

El CRM ya tiene `META_PAGE_TOKEN`, que sirve para recibir el webhook de leads y
leer los formularios. **Para los Insights de anuncios no sirve**: el gasto, las
impresiones y los clics viven en la cuenta publicitaria, no en la página, y
piden el permiso `ads_read` sobre esa cuenta.

Por eso van dos variables nuevas y separadas: `META_ADS_TOKEN` y
`META_AD_ACCOUNT_ID`. Mientras falten, `services/meta_insights.py` se saltea el
sync con un warning y **el resto del módulo funciona igual** — las tasas de
interés, de demo y de presupuesto salen completas. Lo único que no hay es plata.

---

## 1. El token

Va un **usuario del sistema**, no un token de usuario normal. El motivo es
concreto: al generar el token de un usuario del sistema se elige la preferencia
de expiración y puede quedar sin vencimiento, mientras que un token de usuario
de larga duración **muere a los 60 días**. Con un cron semanal, eso significa que
dos meses después de configurarlo el panel empieza a mostrar «sin datos» y nadie
se entera hasta que alguien lo mira.

Requisito previo: hay que ser dueño de una app asociada al portafolio comercial.
Ya la tenemos —es la del `META_APP_ID` que está en los secrets.

Pasos, en [business.facebook.com](https://business.facebook.com/):

1. **Configuración del negocio** → **Usuarios** → **Usuarios del sistema**.
2. **Agregar**, ponerle un nombre que se entienda dentro de seis meses
   (`crm-insights`, no `test1`). El rol de **empleado** alcanza: solo tiene que
   leer.
3. Con el usuario ya creado, **Agregar activos** → **Cuentas publicitarias** →
   elegir la cuenta de Scalerics y darle **ver rendimiento**. Sin este paso el
   token existe pero no ve nada, y la API contesta con una lista vacía en vez de
   un error — que es peor, porque parece que no gastaste.
4. **Generar nuevo token** → elegir la app → marcar **`ads_read`** → elegir la
   preferencia de expiración **sin vencimiento**.
5. **Copiarlo ahí mismo.** Se muestra una sola vez.

## 2. El id de la cuenta publicitaria

Está en el Administrador de anuncios, arriba, con el formato `act_` seguido de
números. Va entero, con el prefijo: `META_AD_ACCOUNT_ID=act_123456789`.

## 3. Cargarlos en Fly

```bash
flyctl secrets set META_ADS_TOKEN="..." META_AD_ACCOUNT_ID="act_..." -a scalerics-crm
```

Los secrets no van al `.env` del repo ni a ningún archivo versionado.

> **Ojo:** `flyctl secrets set` reinicia la máquina, y cada reinicio dispara los
> jobs de fondo (regla 3 de `COORDINACION.md`). El sync de Insights tiene su
> propio tope rodante, así que no pasa nada, pero conviene no hacerlo en el
> minuto en que sale la tanda de recordatorios.

## 4. Verificar el `action_type` — este paso no es opcional

Meta no devuelve los leads como un campo: hay que buscarlos entre las acciones,
y **el nombre de esa acción cambió entre versiones de la API**. En
`services/meta_insights.py` están los tres nombres conocidos, pero cuál usa la
cuenta de Scalerics no se puede saber sin credenciales.

Si no es ninguno de los tres, la columna `leads` de `meta_insights` queda en
cero, **todos los CPL dan «sin datos» y parece un bug del código cuando en
realidad es un nombre que falta en una lista**.

Con las credenciales ya puestas:

```bash
python scripts/verificar_meta_insights.py
```

El script pide una semana de Insights y muestra qué `action_type` viene. Si el
que aparece no está en `_ACCIONES_DE_LEAD`, agregarlo ahí y anotar en el
docstring cuál usa la cuenta.

## 5. Traer la historia

El sync trae por defecto los últimos 7 días. Para llenar desde marzo, una vez:

```bash
curl -X POST -H "x-admin-token: $ADMIN_TOKEN" \
  "https://scalerics-crm.fly.dev/api/marketing/sync-insights?dias=200"
```

Después el cron semanal alcanza: resincroniza los últimos 7 días siempre, porque
Meta corrige cifras hacia atrás.

## 6. Qué mirar cuando esté

Abrir el panel y comparar tres números contra el Administrador de anuncios:

- **Gasto del período.** Si no coincide, mirar la moneda: se guarda la de la
  cuenta y **no se convierte** a propósito.
- **Leads según Meta contra leads en el CRM.** Una brecha del 5-10% es normal
  (duplicados, algún webhook perdido). Una del 50% significa que se están
  perdiendo leads en la ingesta y es un problema de verdad.
- **El bloque de conciliación**, que compara el gasto de Meta contra lo que se
  carga a mano en Finanzas. Si difieren mucho, o falta cargar movimientos o hay
  gasto que la contabilidad no está viendo.

## Lo que este módulo nunca va a poder decir

**El costo por cierre por campaña, sobre la historia vieja.** De los 9 leads que
llegaron a `cerrado` o más, 8 no tienen campaña atribuida: la campaña vivía
dentro de las notas del lead y se perdió cuando alguien les escribió el monto
encima. Ese dato no se puede reconstruir de ningún lado.

De acá en adelante sí, porque la campaña ahora va en columna propia y la ingesta
la escribe con `COALESCE` para que una segunda pasada sin datos tampoco la borre.
Pero para marzo-septiembre de 2026, no.
