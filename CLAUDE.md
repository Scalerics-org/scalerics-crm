# Guía de trabajo — lead-gen-uy

## Demos / pitch deck para clientes

Las demos son presentaciones HTML slide-by-slide que se usan para mostrarle a un cliente potencial cómo quedaría su página web. Viven en `generated_demos/` si son para un cliente específico, o en `templates_demo/` si son plantillas genéricas reutilizables.

### Regla principal: colores Scalerics siempre

**Nunca crear paletas de colores personalizadas para las demos.** Aunque el negocio del cliente use otros colores (cobre, verde oliva, etc.), los slides que NO son mockups siempre usan la paleta de Scalerics:

```css
--bg: #09090f;
--surface: rgba(255,255,255,.04);
--border: rgba(255,255,255,.07);
--text: #e2e8f0;
--muted: #64748b;
--blue: #0088CC;
--blue-lt: #33aadd;
--blue-dk: #005f8e;
--blue-glow: rgba(0,136,204,.14);
--blue-border: rgba(0,136,204,.28);
--green: #10B981;
--green-lt: #34d399;
--green-glow: rgba(16,185,129,.12);
--ff: 'Inter', sans-serif;
--ff-d: 'Raleway', sans-serif;
```

El color del cliente solo aparece **dentro de los mockups del browser**, si es necesario para que el sitio mockeado se vea personalizado.

### Estructura de slides

Toda demo sigue esta estructura de 7 slides (igual a `templates_demo/pagina-web.html`):

| Slide | Tipo | Contenido |
|-------|------|-----------|
| S1 | Portada | Logo Scalerics, badge con nombre del cliente, h1 con nombre, pills de features |
| S2 | Problema | 3 tarjetas con los problemas actuales del cliente |
| S3 | Solución | Grid 2×2 de soluciones + 3 stats |
| S4 | **Mockup 1** | Estilo oscuro y elegante — homepage completa del cliente |
| S5 | **Mockup 2** | Estilo claro y limpio — homepage completa del cliente |
| S6 | **Mockup 3** | Estilo vibrante — homepage completa del cliente |
| S7 | Cierre | CTA card con precio acordado + botón WhatsApp + checklist |

### Los 3 mockups

Cada mockup muestra la **homepage completa** del cliente en un estilo visual diferente, igual a como lo hace `pagina-web.html`. Los 3 siempre muestran la misma página (inicio), no 3 páginas distintas del mismo sitio.

- **Estilo 1 — Oscuro y elegante**: fondo `#09090f`/`#0d0d14`, navbar oscura, hero con gradiente navy, acentos azul `#0088CC`
- **Estilo 2 — Claro y limpio**: fondo `#f8fafc`, navbar blanca con sombra, hero con gradiente `#eff6ff → #dbeafe`, acentos azul
- **Estilo 3 — Vibrante**: navbar `background:#0088CC` sólida, hero con gradiente azul, botones verdes `#10B981`

Cada mockup incluye (scrollable, height 460px):
- Barra de anuncio (opcional, Estilo 1)
- Navbar con logo del negocio + links + botón CTA
- Hero con título, subtítulo y 2 botones
- Sección de servicios/características (grid 3 columnas)
- Testimonios o trust bar (below fold)
- Banner WA CTA al fondo

### Slide S7 — precio

El precio acordado va en el S7. Formato:
```html
<div class="cta-price">
  <span class="cta-price-currency">$</span>
  <span class="cta-price-amount">X.XXX</span>
  <span class="cta-price-label">pago único</span>
</div>
```
El color del precio usa `var(--blue)`, no ningún color personalizado.

### Plantilla base

Siempre usar `templates_demo/pagina-web.html` como referencia estructural. Copiar el CSS base completo y adaptar solo el contenido (textos, íconos, URL en la barra del browser).

### Upload a GitHub

Las demos de clientes van a: `juantomasetti1/scalerics-assets/templates/demos/<nombre>.html`

Script de referencia: `upload_templates.py`

**Los presupuestos (`budgets/`) nunca se suben a ningún repo público.**

---

## Seguridad

- Presupuestos con precios: solo en `budgets/`, nunca en repositorios públicos
- Tokens OAuth: en `.env`, nunca en código fuente

---

## Convenciones de código (Clean Code + SOLID)

Seguir **`docs/clean-code.md`** en todo código que se toque o agregue (guía completa, adaptada de C# a Python/JS). Reglas clave:

- **Sin strings mágicos** para estados/roles → enums/constantes (`CrmStatus`, `TASK_STATUS`, etc.).
- **Máx ~2–3 argumentos** por función; el resto agrupado en objeto/dataclass (en JS, objeto de opciones).
- **Guard clauses** (fail-fast), evitar negaciones poco claras, y condiciones complejas en variables/funciones con nombre.
- **Mapear con comprehensions/`map`**, no loops manuales; encapsular el mapeo Request→entidad y entidad→response fuera del route.
- **No `commit` dentro de loops** — batch afuera.
- **Nombres:** Python `snake_case`; JS `camelCase`; clases `PascalCase`; privados con `_`; constantes `UPPER_CASE`.
- **Fechas:** parseo explícito con formato + timezone.
- Aplicar al código tocado, **sin reescribir todo de golpe**.

**Arquitectura (SOLID):** apuntar a SRP (cada archivo una responsabilidad), OCP (registries en vez de switches hardcodeados), DIP (depender de interfaces para IA y deploy, no de proveedores concretos). Ver el plan/spec de extracción del frontend en `docs/superpowers/`. No inventar abstracciones para casos con una sola implementación sin segundo caso a la vista.
