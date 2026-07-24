# Spec: Extracción del frontend de `dashboard.py`

**Fecha:** 2026-07-24
**Estado:** Propuesto

---

## Contexto

`dashboard.py` tiene ~518 KB (~10.000 líneas). Es el CRM entero: el backend Flask **y todo el frontend** (HTML, CSS y JS) embebido como strings de Python en un solo archivo. Esto genera:

- El HTML/CSS/JS no tiene resaltado, linter, ni formateo — se edita "a ciegas" dentro de strings.
- No se puede testear la UI ni reutilizar componentes.
- Bugs difíciles de ver (ej: un `querySelector` roto sobrevivió porque estaba perdido en el blob).
- Convive con los blueprints de `routes/`, así que la lógica está en dos mundos (migración a medias).

El resto de la arquitectura está **bien**: capa de `services/`, blueprints en `routes/`, tests, CI, `database.py` separado. El problema está concentrado en este archivo.

---

## Objetivo

Sacar el frontend de `dashboard.py` a archivos propios (CSS, JS y un template HTML), **sin cambiar en nada el comportamiento ni el aspecto** del CRM. Al terminar, `dashboard.py` queda como app factory + registro de rutas + render del template, y el HTML/CSS/JS vive en archivos editables y testeables.

---

## Principio rector: cero cambio de comportamiento

Este refactor es **puramente mecánico**. En cada paso, el CRM se ve y funciona **idéntico** a antes. Nada de "de paso mejoro esto". Las mejoras de UX/bugs van en planes separados. Cada paso es verificable y reversible (commit por paso, en una rama).

---

## Arquitectura objetivo

```
static/
  css/
    crm.css            ← todo el <style> del CRM
  js/
    crm-core.js        ← helpers compartidos: showPanel, fetch, esc, theme, sidebar, bootstrap
    crm-cola.js        ← panel Cola (el más usado)
    crm-seguimientos.js
    crm-meta.js
    crm-pipeline.js
    crm-clientes.js
    crm-client-panel.js ← la ficha del cliente (openClientPanel, tabs, _cp*)
    crm-tasks.js
    crm-wa.js
    crm-calendar.js
    crm-metrics.js
    crm-activity.js
    crm-sdr.js
templates/
  crm/
    index.html         ← el shell HTML (sidebar, contenedores de panel, modales)
dashboard.py           ← app factory + auth + registro de blueprints + render_template
```

Nota: los JS se cargan como `<script src>` normales (NO ES modules) para no romper el uso de funciones globales entre archivos. El orden de carga importa: `crm-core.js` primero, después los de panel. Migrar a ES modules es un paso futuro, fuera de este plan.

---

## Principios SOLID (aplicados con criterio)

Esto es un app Flask + JS vanilla, no un sistema OOP pesado. Aplicamos SOLID por su **espíritu**, no metiendo clases e interfaces en todos lados (eso sería sobre-ingeniería). Los que más rinden acá son **SRP** y **DIP**.

**SRP — Responsabilidad única** (el corazón de este refactor). Cada archivo, una sola razón para cambiar:
- `dashboard.py`: solo ensambla la app (factory + registro de blueprints). Ni HTML, ni CSS, ni JS, ni lógica de negocio.
- Cada `crm-<panel>.js`: la lógica de un panel.
- `crm-core.js`: solo plumbing compartido (routing, fetch, theme, sidebar). NO un cajón de sastre.
- Cada blueprint de `routes/`: los endpoints de un recurso. Cada `services/*.py`: una preocupación.
- `database.py` (59 KB) hace demasiado: se puede partir en repositorios por entidad (leads, users, tasks). Es un win de SRP pero más grande → va en un plan aparte, no en éste.

**OCP — Abierto/Cerrado**: extender sin editar lo existente, con **registries**:
- Registro de paneles: en vez de un `showPanel` con switch hardcodeado + wiring manual del nav, registrar paneles en un mapa `{id, label, icon, init}`. Agregar un panel = agregar una entrada.
- Registro de plantillas de demo: las 4 (boutique/editorial/modern/retro) se eligen por mapa. Sumar una 5ta o un mapeo rubro→plantilla no toca el core del generador.

**DIP — Inversión de dependencias**: depender de abstracciones. Conecta directo con lo que ya decidimos:
- IA: `demo_ai.py`/`budget_ai.py` dependen de una interfaz `LLMClient` (`complete(system, user) -> str`), con impl Anthropic. Cambiar de modelo/proveedor = nueva impl, sin tocar a quien la usa.
- Deploy: `deployer.py` depende de una interfaz `DemoHost` (`deploy(files) -> url`), con impl Vercel y Cloudflare. (Es exactamente el debate Vercel↔Cloudflare resuelto por diseño.)
- Datos: services/routes dependen de funciones de repositorio, no de SQL crudo. `database.py` ya es esa costura; formalizarla.

**LSP — Sustitución de Liskov**: donde metemos las interfaces de DIP, las impls tienen que ser de verdad intercambiables — todo `DemoHost` devuelve la misma forma (una URL) y se comporta igual; todo `LLMClient` devuelve texto usable por igual. LSP es el contrato de esas interfaces.

**ISP — Segregación de interfaces**: interfaces chicas y enfocadas. Nada de un mega-módulo "utils" que todos importan; módulos específicos (quien solo manda email importa el service de email). En el front, `crm-core.js` no debe volverse el nuevo monolito.

> **Regla anti-sobre-ingeniería:** no crear abstracciones para algo con una sola implementación y sin segundo caso a la vista. Las interfaces de IA y deploy se justifican porque YA hay (o habrá) un segundo proveedor. El resto, SRP simple.

---

## El gotcha clave: valores inyectados por el servidor

Hoy el `<script>` embebido probablemente usa variables de Jinja/Python inyectadas en el momento del render (ej: nombre de usuario, rol, flags, IDs). Al mover el JS a un archivo estático, esas variables **ya no se pueden interpolar** ahí.

Solución (patrón bootstrap): en el template `index.html`, un `<script>` **inline mínimo** define un objeto de config antes de cargar los JS externos:

```html
<script>
  window.CRM = {
    user: {{ user_json|tojson }},
    isAdmin: {{ is_admin|tojson }},
    // ...todo lo que hoy se interpola en el JS
  };
</script>
<script src="/static/js/crm-core.js" defer></script>
<script src="/static/js/crm-cola.js" defer></script>
<!-- ... -->
```

Y en los JS externos, donde antes había `{{ algo }}`, ahora se lee `window.CRM.algo`. **Identificar todas estas interpolaciones es el paso #1 y más importante del refactor.**

---

## Estrategia por fases

1. **Red de seguridad** — rama nueva + baseline visual/funcional + tests verdes.
2. **Bootstrap de config** — mover las interpolaciones Jinja del `<script>` a `window.CRM`.
3. **Extraer CSS** — `<style>` → `static/css/crm.css`. (Riesgo mínimo, gran alivio.)
4. **Extraer JS a un solo archivo** — `<script>` → `static/js/crm.js`. Verificar paridad total.
5. **Extraer el shell HTML** — a `templates/crm/index.html` con `render_template`.
6. **Partir el JS por panel** — de `crm.js` a `crm-core.js` + un archivo por panel, uno a la vez, empezando por Cola.
7. **(Opcional) Terminar de mover rutas a blueprints** — `dashboard.py` queda mínimo.

Las fases 3–6 son las de mayor valor. La 7 es opcional y más grande; puede ir en un plan aparte.

---

## Criterios de aceptación

- [ ] `dashboard.py` baja de ~518 KB a un tamaño chico (solo backend + render).
- [ ] El CSS del CRM vive en `static/css/crm.css` y el HTML lo linkea.
- [ ] El JS vive en `static/js/crm-*.js`, cargados en orden, sin interpolación de Jinja.
- [ ] Todos los valores que antes se inyectaban están en `window.CRM`.
- [ ] El shell HTML vive en `templates/crm/index.html`.
- [ ] Cada panel (Cola, Seguimientos, Meta, Pipeline, Clientes, ficha de cliente, Tareas, WhatsApp, Calendario, Métricas, Actividad, SDR) funciona **idéntico** a antes.
- [ ] Modo claro/oscuro, sidebar, y la ficha del cliente funcionan igual.
- [ ] `pytest` pasa igual que antes.
- [ ] Sin errores nuevos en la consola del browser.
- [ ] Paridad visual: capturas antes/después de cada panel son equivalentes.

### SOLID
- [ ] SRP: `dashboard.py` solo ensambla la app; cada `crm-<panel>.js` es un panel; `crm-core.js` es solo plumbing compartido.
- [ ] OCP: existe un registro de paneles; agregar un panel no requiere editar un switch central.
- [ ] DIP (companion Task 7): `deployer.py` depende de una interfaz `DemoHost` con impl Vercel/Cloudflare; el LLM detrás de una interfaz `LLMClient`.
- [ ] Sin sobre-ingeniería: no hay interfaces/abstracciones para casos con una sola implementación sin segundo caso a la vista.
