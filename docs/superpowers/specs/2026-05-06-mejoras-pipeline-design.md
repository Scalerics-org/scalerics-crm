# Diseño: Mejoras al pipeline de lead gen

**Fecha:** 2026-05-06  
**Estado:** Aprobado para implementación

---

## Resumen

Tres mejoras al pipeline existente:
1. **Templates con personalidad** — 3 diseños HTML distintos según categoría del negocio, reemplazando `base.html`
2. **Dashboard Flask** — panel visual para gestionar leads, marcar contactados, ver demos
3. **Teléfono prominente** — visible en hero y sección de contacto de todos los templates

---

## 1. Templates

### Templates (3 nuevos archivos en `templates/`)

| Archivo | Estilo | Categorías |
|---|---|---|
| `template_editorial.html` | Dark + rojo fuego, tipografía enorme | bar, barbería, gym, peluquería, tattoo |
| `template_retro.html` | Ámbar cálido, serif, bordes punteados | panadería, almacén, rotisería, cafetería |
| `template_boutique.html` | Azul noche + dorado, elegante | restaurante, clínica, spa, estudio, hotel |
| `template_modern.html` | Actual mejorado | fallback para todo lo demás |

### Cambios en `demo_generator.py`

- Claude devuelve un campo extra `"template": "editorial"|"retro"|"boutique"|"modern"` en su JSON
- `render_html()` selecciona el archivo de template según ese campo
- El prompt se actualiza para incluir instrucciones de selección de template
- Todos los templates reciben: `name`, `category`, `tagline`, `about`, `services`, `cta_text`, `phone`, `address`, `city`, `rating`, `review_count`, `hours`, `color_scheme`

### Cada template incluye
- Google Fonts (Inter + Bricolage Grotesque o Playfair Display según estilo)
- Nav sticky con nombre + botón "Llamar ahora" (tel: link)
- Hero con número de teléfono grande y botón WhatsApp
- Rating strip si hay rating disponible
- Sección de servicios con íconos
- Sección de contacto con tarjetas (teléfono, dirección, horarios)
- Banner de demo Scalerics
- Footer con link a Scalerics

---

## 2. Dashboard Flask

### Nuevo archivo: `dashboard.py`

Servidor Flask que corre localmente. `python main.py dashboard` lo inicia y abre el browser.

**Rutas:**
- `GET /` — página principal con tabla de leads y stats
- `GET /api/leads` — JSON con todos los leads (filtros por status via query param)
- `POST /api/leads/<id>/contact` — marca lead como contactado, guarda nota opcional
- `GET /api/stats` — contadores por status

**UI (HTML/CSS/JS inline, sin frameworks externos):**
- Sidebar con navegación y botón "Correr pipeline" → abre modal mostrando el comando a copiar/ejecutar en terminal
- 4 tarjetas de stats: total / demos listas / con teléfono / contactados
- Filtros por status (botones toggle)
- Filtro por rubro/categoría (dropdown con las categorías presentes en la DB, se actualiza dinámico)
- Buscador por nombre
- Tabla con columnas: Negocio, Teléfono, Ciudad, Rating, Estado, Acciones
- Botón "Contactar" por fila → modal con campo de nota → actualiza status en DB
- Link "Ver demo" abre la URL de Vercel en nueva pestaña
- Diseño dark (mismo estilo del mockup aprobado)

### Cambios en `database.py`

- Agregar columna `notes TEXT` a la tabla `businesses`
- Agregar función `get_all_businesses(db_path)` que retorna todos sin filtrar por status
- `update_business` ya soporta campos arbitrarios — no hay cambio

### Cambios en `main.py`

- Nuevo subcomando `dashboard`: inicia Flask en puerto 5000, abre `http://localhost:5000` con `webbrowser.open()`

### Dependencias nuevas en `requirements.txt`

- `flask`

---

## 3. Teléfono prominente

Los 3 templates nuevos (y modern mejorado) muestran el teléfono:
- En el hero como botón primario `<a href="tel:...">`
- En el nav sticky
- En la sección de contacto como tarjeta destacada
- Con botón de WhatsApp `<a href="https://wa.me/...">`  (formateando el número para quitar espacios y el `+`)

---

## Archivos a crear/modificar

| Archivo | Acción |
|---|---|
| `templates/template_editorial.html` | Crear |
| `templates/template_retro.html` | Crear |
| `templates/template_boutique.html` | Crear |
| `templates/template_modern.html` | Crear (reemplaza base.html) |
| `demo_generator.py` | Modificar (nuevo campo template, selección de archivo) |
| `dashboard.py` | Crear |
| `database.py` | Modificar (columna notes, función get_all) |
| `main.py` | Modificar (subcomando dashboard) |
| `requirements.txt` | Modificar (agregar flask) |
