# Blackphone — Demo Ecommerce (Scalerics)

**Fecha:** 2026-05-27  
**Cliente:** Blackphone — "Tu iPhone de confianza"  
**Archivo de salida:** `generated_demos/blackphone.html`  
**Upload destino:** `juantomasetti1/scalerics-assets/templates/demos/blackphone.html`

---

## Contexto

Demo de presentación estilo pitch deck para Blackphone, tienda de iPhones con locales en Villa Mercedes y San Luis (Argentina). Precios en USD. Basada en `templates_demo/ecommerce.html`. 7 slides, estructura estándar Scalerics.

---

## Slides

### S1 — Portada
- Logo Scalerics (URL raw GitHub)
- Badge: icono `fa-mobile-screen-button` + texto "Tienda de iPhones"
- H1: `Tu iPhone,` / `<em>siempre de confianza.</em>`
- Subtítulo: "Vendé iPhones con respaldo real. Catálogo online, precios en dólares y dos locales físicos en San Luis."
- Pills: `fa-shield-halved` Garantía incluida · `fa-location-dot` Villa Mercedes y San Luis · `fa-dollar-sign` Precios en USD

### S2 — Problema
- Título: `Comprar un iPhone` / `en Argentina es` / `<em>un riesgo.</em>`
- Tarjeta 1 — "Páginas sin garantía": comprás online y cuando llega el equipo no funciona, nadie responde.
- Tarjeta 2 — "Precios en pesos que no cierran": el precio cambia entre que lo ves y lo pagás. Sin referencia clara en dólares.
- Tarjeta 3 — "Sin soporte post-venta": si algo falla, no tenés a quién reclamarle. El vendedor desaparece.

### S3 — Solución
- Título: `Tu tienda de iPhones` / `<span class="g">lista para vender</span>`
- Grid 2×2:
  - Catálogo online con precios en USD actualizados
  - Garantía verificada en cada equipo
  - Locales físicos en Villa Mercedes y San Luis
  - Soporte real por WhatsApp y en persona
- Stats: `+20` modelos disponibles · `90` días de garantía · `2` locales físicos

### S4 — Mockup 1: Ultra Black Premium *(primer slide de mockup)*
- Label: `Demo · Tienda premium — Estilo oscuro`
- URL barra browser: `www.blackphone.com.ar`
- Announcement bar: "🚚 Envío a todo San Luis · Villa Mercedes y capital"
- Navbar: logo Blackphone real (base64 de `logo blackphone.png`), barra de búsqueda oscura, links Novedades / Modelos / Reacondicionados, carrito
- Category strip: iPhone 15 · iPhone 14 · iPhone 13 · Reacondicionados · Accesorios
- Hero lateral izquierdo: gradiente negro navy, texto "iPhone 15 Pro Max", "Tu iPhone de confianza", botón "Ver modelos →"
- Grid de productos (6 cards, foto real Unsplash):
  - iPhone 15 Pro Max — $1,199
  - iPhone 15 Pro — $999
  - iPhone 15 — $849
  - iPhone 14 Pro — $799
  - iPhone 14 — $649
  - iPhone 13 — $499
- Cada card: badge "NUEVO" o "OFERTA", foto, nombre, precio USD, botón "Ver →"
- Colores: `#000` fondo, `#111`/`#1a1a1a` cards, texto `#fff`/`#ccc`, botones blancos con texto negro

### S5 — Mockup 2: White Minimal Clean
- Label: `Demo · Tienda limpia — Estilo claro`
- Mismo layout y productos
- Colores: fondo `#f8f9fa`, navbar `#fff` con sombra, cards `#fff` con borde `#e5e7eb`, texto `#000`/`#374151`, botones `#000` con texto `#fff`

### S6 — Mockup 3: Dark Slate Elegante
- Label: `Demo · Tienda tech — Estilo grafito`
- Mismo layout y productos
- Colores: fondo `#0d0d0d`, navbar `#161616`, cards `#1a1a1a` con borde `#2a2a2a`, acentos grafito `#3a3a3a`/`#555`, announcement bar `#1a1a1a`

### S7 — Cierre
- Precio: placeholder en blanco (campo vacío, sin número)
- Botón WhatsApp → `https://wa.me/+549...`  (número a definir)
- Checklist: Diseño personalizado · Catálogo de productos · Panel de administración · Dominio + hosting · Soporte técnico

---

## Imágenes

| Elemento | Fuente |
|---|---|
| Logo Blackphone | `C:\Users\juant\OneDrive\Desktop\Scalerics\Presupuestos\logo blackphone.png` — embed como base64 |
| iPhone 15 Pro Max | Unsplash (foto de producto iPhone, orientación vertical, fondo neutro) |
| iPhone 15 Pro | Unsplash |
| iPhone 15 | Unsplash |
| iPhone 14 Pro | Unsplash |
| iPhone 14 | Unsplash |
| iPhone 13 | Unsplash |

---

## Reglas de estilo (CLAUDE.md)

- Slides S1–S3 y S7: paleta Scalerics siempre (`--blue: #0088CC`, `--bg: #09090f`, etc.)
- Slides S4–S6 (mockups): colores del cliente — black & white, sin azul Scalerics dentro del browser
- Fuentes: Inter (body) + Raleway (display/headings)
- Watermark "Scalerics" en esquina inferior izquierda
- Progreso 1/7 arriba a la derecha

---

## Archivo de salida

`lead-gen-uy/generated_demos/blackphone.html` — un único archivo HTML autocontenido, sin dependencias locales (imágenes en base64 o URLs externas, fuentes vía Google Fonts CDN).
