# Puesta en producción del módulo de Marketing

Qué falta para que el panel deje de decir «sin datos» en los bloques de costo.
Nada de esto lo puede hacer el código solo: son cambios en la cuenta de Meta.

## Estado al 10/9/2026

Los pasos 1 a 3 **ya están hechos**. Lo que falta es del 4 en adelante.

| | |
|---|---|
| Producto Marketing API en la app | hecho |
| Usuario del sistema `crm-insights` (id `61594435974111`) | hecho, rol Employee |
| Cuenta publicitaria asignada | hecho, **Ver rendimiento** (solo lectura) |
| App asignada al usuario del sistema | hecho, **Desarrollar la aplicación** |
| Webhook de leads | verificado intacto antes y después |
| **Generar el token** | **pendiente** |
| **Cargar los secrets en Fly** | **pendiente** |
| **Verificar el `action_type`** | **pendiente** |

Pendiente además, sin urgencia: probar si el permiso de la app se puede bajar de
«Desarrollar la aplicación» a «Ver insights» ahora que el caso de uso existe.
Cuando se activó «Desarrollar», Meta prendió solas «Ver estadísticas» y «Probar
app», que tampoco se pidieron.

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

## 1. Antes que nada: la app necesita el producto Marketing API

**Este es el paso que hace fallar todo lo demás, y Meta lo esconde.** Se hizo
el 10/9/2026 y quedó documentado acá porque costó tres intentos encontrarlo.

La app `Scalerics CRM` (id `1306976674838718`, la del `META_APP_ID`) se creó para
el webhook de leads. Tenía solo *Inicio de sesión con Facebook*. **Sin el producto
de Marketing API, el permiso `ads_read` no existe para esa app**, así que el
asistente que genera el token no tiene nada que ofrecer.

Y lo que muestra cuando eso pasa manda al lugar equivocado:

> **No hay permisos disponibles.** Asigna un rol de aplicación al usuario del
> sistema o selecciona otra aplicación para continuar.

Ese mensaje hace pensar que falta un permiso del usuario del sistema. **No es
eso.** Se le puede dar hasta «Desarrollar la aplicación» y sigue igual, porque el
problema está dos pantallas más allá, en la configuración de la app.

Cómo se arregla, en
[developers.facebook.com](https://developers.facebook.com/apps/1306976674838718/dashboard/):

1. En el panel de la app, **Añadir casos de uso** (arriba a la derecha).
2. Marcar **«Crea y administra anuncios con la API de marketing»**, y nada más.
3. Guardar.

**Verificar el webhook antes y después.** Esta es la app que recibe los leads, y
ya hubo un incidente donde Meta dejó de entregar en silencio. Agregar un caso de
uso no debería tocar la suscripción, pero «no debería» no alcanza:

```bash
python -c "
import os, requests
from dotenv import load_dotenv
load_dotenv()
from meta_config import GRAPH
pt = os.environ['META_PAGE_TOKEN']; pid = os.environ['META_PAGE_ID']
d = requests.get(f'{GRAPH}/{pid}/subscribed_apps', params={'access_token': pt}).json()
for app in d.get('data', []):
    print(app.get('name'), sorted(app.get('subscribed_fields', [])))
"
```

Tiene que decir `Scalerics CRM ['leadgen']` **antes y después**. El 10/9 se
verificó y no cambió.

## 2. El usuario del sistema

Va un **usuario del sistema**, no un token de usuario normal. El motivo es
concreto: al generar el token de un usuario del sistema se elige la preferencia
de expiración y puede quedar sin vencimiento, mientras que un token de usuario
de larga duración **muere a los 60 días**. Con un cron semanal, eso significa que
dos meses después de configurarlo el panel empieza a mostrar «sin datos» y nadie
se entera hasta que alguien lo mira.

Ojo: el asistente propone **60 días** por defecto. Hay que cambiarlo a **Nunca**
a mano, y lo vuelve a proponer cada vez que se reinicia el asistente.

En [business.facebook.com](https://business.facebook.com/), portafolio
**Scalerics** (el que tiene la cuenta publicitaria; el otro, *Scalerics
Programacion*, no tiene ninguna):

1. **Configuración del negocio** → **Usuarios** → **Usuarios del sistema**.
2. **Añadir**, nombre que se entienda dentro de seis meses (`crm-insights`, no
   `test1`). Rol **Empleado**: solo tiene que leer.
3. La primera vez, Meta pide **aceptar su política de no discriminación
   publicitaria en nombre de todos los usuarios del sistema**. Es un compromiso
   legal del negocio: lo acepta una persona, no una herramienta.

## 3. Asignarle los DOS activos

Los dos, y este es el segundo error fácil: con la cuenta publicitaria sola no
alcanza.

**a) La cuenta publicitaria.** Con el usuario ya creado, **Asignar activos** →
**Cuentas publicitarias** → la cuenta de Scalerics → **Ver rendimiento**.

Dejar apagados «Administrar campañas», «Administrar modelos de Creative Hub» y
«Administrar cuentas publicitarias»: el token solo tiene que leer.

Sin este paso el token existe pero no ve nada, y **la API contesta con una lista
vacía en vez de un error** — que es peor, porque parece que no gastaste.

**b) La app.** El mismo diálogo → **Aplicaciones** → **Scalerics CRM** →
**Desarrollar la aplicación**.

> **Este permiso es más ancho de lo que uno querría:** deja cambiar la
> configuración de la app. Se probó primero con «Ver insights», que es lo mínimo,
> y **no alcanza**: el asistente sigue diciendo que no hay permisos. Meta no
> ofrece un punto intermedio. Además, al activar «Desarrollar» se prenden solos
> «Ver estadísticas» y «Probar app».
>
> Queda pendiente probar si, ahora que el caso de uso de Marketing API existe,
> se puede bajar a «Ver insights». No se probó todavía.

**Verificar recargando la página.** El diálogo dice «se ha asignado un activo»
pero la vista de atrás puede seguir mostrando «No se han asignado activos». Sin
recargar no se sabe cuál de las dos dice la verdad.

## 4. Generar el token

**Generar identificador** → app **Scalerics CRM** → caducidad **Nunca** →
permisos: **solo `ads_read`**.

`ads_management` aparece en la lista y **no se marca**: permite modificar
campañas, y este token solo tiene que leer.

Se muestra una sola vez. De la pantalla a la terminal, sin escalas y sin pegarlo
en ningún chat ni archivo.

## 5. El id de la cuenta publicitaria

Está en el Administrador de anuncios, arriba, con el formato `act_` seguido de
números. Va entero, con el prefijo: `META_AD_ACCOUNT_ID=act_123456789`.

## 6. Cargarlos en Fly

Lo más simple, y lo que evita que el token quede escrito en el historial del
shell:

```bash
python scripts/cargar_token_meta.py
```

Pide el token con `getpass` —no se ve mientras se pega, no va al historial—, lo
valida, lo carga con el id de la cuenta ya puesto, y te dice qué correr después.

A mano es lo mismo:

```bash
flyctl secrets set META_ADS_TOKEN="..." META_AD_ACCOUNT_ID="act_1165635198430883" -a scalerics-crm
```

Los secrets no van al `.env` del repo ni a ningún archivo versionado. Y un token
de usuario del sistema sin vencimiento tampoco va pegado en un chat ni en una
tarea de Notion: si ya pasó por algún lado así, conviene revocarlo desde
**Revocar identificadores** y generar otro.

> **Ojo:** `flyctl secrets set` reinicia la máquina, y cada reinicio dispara los
> jobs de fondo (regla 3 de `COORDINACION.md`). El sync de Insights tiene su
> propio tope rodante, así que no pasa nada, pero conviene no hacerlo en el
> minuto en que sale la tanda de recordatorios.

## 7. Verificar el `action_type` — este paso no es opcional

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

## 8. Traer la historia

El sync trae por defecto los últimos 7 días. Para llenar desde marzo, una vez:

```bash
curl -X POST -H "x-admin-token: $ADMIN_TOKEN" \
  "https://scalerics-crm.fly.dev/api/marketing/sync-insights?dias=200"
```

Después el cron semanal alcanza: resincroniza los últimos 7 días siempre, porque
Meta corrige cifras hacia atrás.

## 9. Qué mirar cuando esté

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


---

## Puesto en producción el 10/9/2026 — lo que dio de verdad

Token cargado, historia traída, todo verificado adentro de la máquina.

**El `action_type` de esta cuenta.** `act_1165635198430883` devuelve **`lead` y
`onsite_conversion.lead_grouped` a la vez**, y los dos están en la lista que el
módulo reconoce. `_leads_de` corta en la primera coincidencia, así que cuál gana
lo decide el orden del array de Meta. Comprobado sobre las 275 filas de marzo a
setiembre: **coinciden en todas** (252 contra 252), así que hoy da igual. Si
algún día aparece una cuenta donde difieran, hay que elegir explícitamente.

**Los números reales**, del 11/3 al 10/9:

| | |
|---|---:|
| Gasto | USD 3.515,80 |
| Impresiones | 525.457 |
| Clics | 7.909 |
| Leads según Meta | 252 |
| Leads en el CRM | 242 |
| Demos | 49 |

**Aparecieron dos campañas que no estaban en ningún lado**: `Leads - Set26 -
Winners` (52,77 y cero leads) y `Leads - Test Creativo` (25,17 y un lead).

**El ranking se da vuelta, con datos reales:**

| campaña | gasto | CPL | costo/demo | CTR |
|---|---:|---:|---:|---:|
| Form - 2026 | 981,31 | **11,54** (1º) | **140,19** (3º) | 1,92% |
| ARG - CH - 2026 | 870,83 | 17,08 | 96,76 | 1,04% |
| UY - 2026 | 1.585,72 | **18,44** (3º) | **83,46** (1º) | 1,21% |

La campaña que trae los leads más baratos consigue las reuniones más caras.

**Y la conciliación encontró algo el primer día.** Finanzas tiene cargados
**3.900** contra los **3.515,80** que Meta cobró de verdad — 384,20 de más — pero
el total esconde lo importante, que es mes a mes:

| mes | Meta | Finanzas | brecha |
|---|---:|---:|---:|
| 2026-06 | 608,01 | **0,00** | -608,01 |
| 2026-09 | 262,59 | **900,00** | +637,41 |

Junio no está cargado y setiembre está cargado de más. El resto son gastos fijos
de 600 contra un cobro que nunca fue 600. Eso es de Finanzas, no de este módulo:
acá solo se muestra la brecha.
