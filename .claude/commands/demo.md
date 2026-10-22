---
description: Crear una demo/pitch deck HTML para un cliente de Scalerics
argument-hint: <nombre-cliente> [tipo-negocio] [precio-acordado]
---

# Crear demo para cliente Scalerics

El usuario quiere crear una demo de pitch deck HTML para el cliente: **$ARGUMENTS**

## Paso 1: Recopilar contexto del cliente

Si el argumento no incluye toda la info, preguntá en UN solo mensaje (todo junto):
- Nombre del negocio y del dueño/a
- Rubro (trenzas, ropa, electrónica, restaurant, etc.)
- Zona / ciudad
- Características del servicio (a domicilio, local físico, delivery, etc.)
- Features principales del sitio (galería, menú, carrito, reservas, WhatsApp, etc.)
- Precio acordado (si hay)

Si ya está todo en `$ARGUMENTS`, saltá directo al Paso 2.

## Paso 2: Leer la plantilla base

**OBLIGATORIO antes de escribir una sola línea de HTML.**

Leer `templates_demo/pagina-web.html` completo para usar exactamente la misma estructura CSS, variables, y patrones de mockup.

## Paso 3: Crear el archivo

**Ruta:** `generated_demos/<nombre-kebab>.html`

**Estructura fija de 7 slides — no agregar ni sacar slides:**

| Slide | ID | Tipo | Qué va |
|-------|-----|------|--------|
| 1 | `s1` | Portada | Logo Scalerics, badge con nombre del cliente, h1 con nombre + `online.`, pills de features |
| 2 | `s2` | Problema | 3 tarjetas — los 3 problemas reales del negocio del cliente |
| 3 | `s3` | Solución | Grid 2×2 con los 4 beneficios del sitio + 3 stats (ej: 24/7, +70%, 1 clic) |
| 4 | `s4` | **Mockup 1** | Estilo oscuro y elegante — homepage completa |
| 5 | `s5` | **Mockup 2** | Estilo claro y limpio — homepage completa |
| 6 | `s6` | **Mockup 3** | Estilo vibrante — homepage completa |
| 7 | `s7` | Cierre | CTA card con precio + botón WhatsApp + checklist de 6 ítems |

## Reglas de CSS — NUNCA romper estas

**Paleta Scalerics — usar siempre, en TODOS los slides:**
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
```

**Colores del cliente (si los tiene):** solo dentro del HTML del browser en los mockups, nunca en el CSS principal ni en S1–S3 ni S7.

**Barra de progreso:** usa `var(--blue-dk)` → `var(--blue)` → `var(--blue-lt)`. Nunca otro color.

**Dot activo en nav:** `background: var(--blue)`.

**Botón "siguiente":** `background: linear-gradient(135deg, var(--blue-dk), var(--blue))`.

## Los 3 mockups — qué es cada uno

Los 3 muestran la **misma página** (homepage) del cliente en 3 estilos visuales diferentes. NO son 3 páginas distintas del mismo sitio.

### Mockup 1 — Oscuro y elegante
- `class="slide mock"`, `mlabel`: "Estilo 1 · Oscuro y elegante"
- `brow-body` background: `#09090f` o `#0d0d14`
- Announcement bar opcional con `rgba(0,136,204,.1)` de background
- Navbar oscura, hero con gradiente navy (`#0a0f1a → #0d1525`)
- Acentos: `#0088CC`, stats en azul y verde
- Testimonios below fold (scrollable)
- WA CTA verde al final: `background:linear-gradient(90deg,#005c1a,#003d12)`

### Mockup 2 — Claro y limpio
- `class="slide mock"`, `mlabel`: "Estilo 2 · Claro y limpio"
- `brow-body` background: `#f8fafc`
- Navbar blanca con `box-shadow:0 1px 6px rgba(0,0,0,.08)`
- Hero con gradiente `#eff6ff → #dbeafe`
- Feature cards con backgrounds `#f8fafc` y `#dcfce7`
- Testimonios 3 columnas below fold
- WA CTA azul: `background:#0088CC`

### Mockup 3 — Vibrante y cercano
- `class="slide mock"`, `mlabel`: "Estilo 3 · Vibrante y cercano"
- `brow-body` background: `#f0f9ff`
- Navbar `background:#0088CC` sólida, logo y links en blanco
- Hero con `background:linear-gradient(135deg,#0088CC,#005f8e)`
- Botón CTA principal: `background:#10B981`
- Service cards: uno con `border:2px solid #0088CC`, otros en `#f0f9ff` y `#f0fdf4`
- WA CTA final: `background:linear-gradient(135deg,#0088CC,#005f8e)` + botón `#10B981`

## Contenido de cada mockup

Cada mockup incluye (en este orden, scrollable con `height:460px`):
1. Announcement bar (Mockup 1, opcional) — dirección, zona, teléfono
2. Navbar — logo del negocio (emoji + nombre) + links + botón CTA
3. Hero — headline del negocio + subtítulo + 2 botones + visual/emoji decorativo
4. Sección de servicios — grid 3 columnas con los servicios o características principales
5. Testimonios / trust bar — below fold, 2–3 reseñas de clientes
6. Banner WA CTA — siempre al final, con botón de agendar/contactar

## Slide S7 — precio

```html
<div class="cta-price">
  <span class="cta-price-currency">$</span>
  <span class="cta-price-amount">X.XXX</span>
  <span class="cta-price-label">pago único</span>
</div>
```
- `cta-price-currency` y `cta-price-amount`: color `var(--blue)`
- Si no hay precio acordado, omitir el bloque `.cta-price` completamente

## JavaScript de navegación

Copiar exactamente el JS de `pagina-web.html`. Incluye:
- Teclado (flechas)
- Touch swipe
- Wheel (con `stopPropagation` en `.brow-body`)
- Dots con `goTo()`
- `scrollTop = 0` en cada cambio de slide

## Paso 4: Abrir en el browser

Después de crear el archivo, abrirlo localmente para verificar:
```
cmd.exe /c start "" "C:/Users/juant/lead-gen-uy/generated_demos/<nombre>.html"
```

## Paso 5: Subir a GitHub

```python
# Repo: Scalerics-org/scalerics-assets
# Ruta remota: templates/demos/<nombre>.html
# Usar upload_templates.py como referencia
```

Ejecutar el upload usando el patrón de `upload_templates.py` adaptado al archivo nuevo.

## Lo que NUNCA hacer

- Crear paleta de colores personalizada para el cliente (cobre, marrón, verde oliva, etc.) — va solo dentro de los mockups si aplica
- Mostrar 3 páginas distintas del mismo sitio en los mockups (galería, agendar, contacto) — los 3 deben ser la homepage en 3 estilos
- Usar otro fondo que no sea `#09090f` en los slides S1–S3 y S7
- Subir presupuestos (`budgets/`) a ningún repositorio público
