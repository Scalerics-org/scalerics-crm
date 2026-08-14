# Mejoras Pipeline: Templates + Dashboard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar 4 templates HTML con personalidad (auto-seleccionados por categoría), dashboard Flask para gestionar leads, y teléfono/WhatsApp prominente en todos los templates.

**Architecture:** Templates son archivos Jinja2 seleccionados en render time según un campo `template` que Claude devuelve en su JSON. El dashboard es un archivo Flask único (`dashboard.py`) que sirve HTML/CSS/JS inline y lee/escribe el SQLite existente.

**Tech Stack:** Python 3.14, Flask, Jinja2, SQLite, Google Fonts (CDN), vanilla JS fetch API

---

## Mapa de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `database.py` | Modificar | Agregar columna `notes`, función `get_all_businesses()` |
| `demo_generator.py` | Modificar | TEMPLATE_MAP, campo `template` en prompt, `wa_number` en render |
| `templates/template_modern.html` | Crear | Template fallback, reemplaza base.html |
| `templates/template_editorial.html` | Crear | Estilo dark/bold para bares/gyms/barberías |
| `templates/template_retro.html` | Crear | Estilo retro cálido para panaderías/almacenes |
| `templates/template_boutique.html` | Crear | Estilo premium para restaurantes/clínicas/spas |
| `dashboard.py` | Crear | Flask app con UI completa para gestión de leads |
| `main.py` | Modificar | Agregar subcomando `dashboard` |
| `requirements.txt` | Modificar | Agregar `flask` |

---

## Task 1: DB — columna notes + get_all_businesses()

**Files:**
- Modify: `database.py`
- Modify: `tests/test_database.py`

- [ ] **Step 1: Agregar tests para notes y get_all_businesses**

En `tests/test_database.py`, agregar al final:

```python
from database import get_all_businesses

def test_get_all_businesses_returns_all(db_path):
    insert_business(db_path, {"name": "A", "maps_url": "http://a.com"})
    insert_business(db_path, {"name": "B", "maps_url": "http://b.com"})
    result = get_all_businesses(db_path)
    assert len(result) == 2

def test_notes_column_exists_after_init(db_path):
    rows = get_all_businesses(db_path)
    insert_business(db_path, {"name": "C", "maps_url": "http://c.com"})
    rows = get_all_businesses(db_path)
    assert "notes" in rows[0]

def test_update_notes(db_path):
    bid = insert_business(db_path, {"name": "D", "maps_url": "http://d.com"})
    update_business(db_path, bid, notes="llamar mañana")
    rows = get_all_businesses(db_path)
    assert rows[0]["notes"] == "llamar mañana"
```

- [ ] **Step 2: Correr tests — verificar que fallan**

```
pytest tests/test_database.py -v
```

Esperado: FAIL en los 3 nuevos tests.

- [ ] **Step 3: Modificar `database.py`**

Agregar la migración en `init_db`, la columna a `ALLOWED_COLUMNS`, y la función nueva:

```python
# En init_db(), DESPUÉS de conn.execute("""CREATE TABLE IF NOT EXISTS...""") y ANTES de conn.commit():
        try:
            conn.execute("ALTER TABLE businesses ADD COLUMN notes TEXT")
        except sqlite3.OperationalError:
            pass  # columna ya existe

# En ALLOWED_COLUMNS, agregar "notes":
ALLOWED_COLUMNS = {
    "name", "category", "address", "city", "phone", "email", "rating",
    "review_count", "hours", "maps_url", "facebook_url", "instagram_url",
    "color_scheme", "demo_html_path", "demo_url", "status", "error_message",
    "scraped_at", "email_sent_at", "notes",
}

# Al final del archivo, agregar:
def get_all_businesses(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute("SELECT * FROM businesses ORDER BY scraped_at DESC")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
```

- [ ] **Step 4: Correr tests — verificar que pasan**

```
pytest tests/test_database.py -v
```

Esperado: todos PASS.

- [ ] **Step 5: Commit**

```
git add database.py tests/test_database.py
git commit -m "feat: add notes column and get_all_businesses to database"
```

---

## Task 2: demo_generator — TEMPLATE_MAP + prompt + render_html

**Files:**
- Modify: `demo_generator.py`
- Modify: `tests/test_demo_generator.py`

- [ ] **Step 1: Agregar tests**

En `tests/test_demo_generator.py`, reemplazar el test existente `test_render_html_contains_business_name` y agregar:

```python
def test_build_prompt_includes_template_field():
    business = {"name": "Test Bar", "category": "Bar", "city": "MVD",
                 "rating": 4.5, "review_count": 10, "hours": ""}
    prompt = build_prompt(business)
    assert '"template"' in prompt
    assert "editorial" in prompt

def test_render_html_contains_business_name():
    content = {
        "tagline": "Tagline", "about": "Sobre nosotros.",
        "services": ["Servicio A", "Servicio B", "Servicio C"],
        "cta_text": "Contactar", "color_scheme": "warm", "template": "modern",
    }
    business = {"name": "Mi Negocio", "category": "Comercio", "phone": "099 000 000",
                 "address": "Calle 1", "city": "Mvd", "rating": 4.0, "review_count": 10, "hours": ""}
    html = render_html(content, business)
    assert "Mi Negocio" in html
    assert "Tagline" in html

def test_render_html_formats_wa_number():
    content = {"tagline": "t", "about": "a", "services": [], "cta_text": "CTA",
               "color_scheme": "warm", "template": "modern"}
    business = {"name": "X", "phone": "+598 99 123 456", "category": "",
                 "address": "", "city": "", "rating": None, "review_count": None, "hours": ""}
    html = render_html(content, business)
    assert "59899123456" in html

def test_render_html_uses_editorial_template():
    content = {"tagline": "t", "about": "a", "services": [], "cta_text": "CTA",
               "color_scheme": "dark", "template": "editorial"}
    business = {"name": "El Bar", "phone": "+598 99 000 000", "category": "Bar",
                 "address": "", "city": "", "rating": None, "review_count": None, "hours": ""}
    html = render_html(content, business)
    assert "El Bar" in html
```

- [ ] **Step 2: Correr tests — verificar que el nuevo de template_field falla**

```
pytest tests/test_demo_generator.py -v
```

Esperado: `test_build_prompt_includes_template_field` FAIL, el de `wa_number` FAIL.

- [ ] **Step 3: Modificar `demo_generator.py`**

Reemplazar el contenido completo del archivo:

```python
import json
import logging
import re
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader

from database import get_businesses_by_status, update_business

load_dotenv()
logger = logging.getLogger(__name__)

ICONS_BY_SCHEME = {
    "warm":   ["🍕", "🥗", "☕", "🍰", "🍷"],
    "cool":   ["🔨", "🏗️", "🪚", "⚙️", "🔩"],
    "green":  ["💊", "🩺", "🌿", "❤️", "🐾"],
    "purple": ["✂️", "💅", "👗", "💄", "🌸"],
    "dark":   ["💡", "📱", "🖥️", "⚡", "🔧"],
}

DEFAULT_ICONS = ["✅", "⭐", "🚀"]

TEMPLATE_MAP = {
    "editorial": "template_editorial.html",
    "retro":     "template_retro.html",
    "boutique":  "template_boutique.html",
    "modern":    "template_modern.html",
}

SYSTEM_PROMPT = """Sos un copywriter experto en diseño web para pymes de Uruguay.
Respondé ÚNICAMENTE con un JSON válido, sin texto adicional, sin markdown, sin bloques de código."""

def build_prompt(business: dict) -> str:
    return f"""Creá el contenido para la página web de este negocio uruguayo:

Nombre: {business.get('name', '')}
Categoría: {business.get('category', '')}
Ciudad: {business.get('city', '')}
Rating: {business.get('rating', '')} ({business.get('review_count', '')} reseñas)
Horarios: {business.get('hours', '')}

Respondé con este JSON exacto:
{{
  "tagline": "slogan atractivo en máximo 10 palabras",
  "about": "descripción del negocio en 2-3 oraciones, tono cálido y profesional",
  "services": ["servicio 1", "servicio 2", "servicio 3"],
  "cta_text": "texto del botón de contacto (máx 4 palabras)",
  "color_scheme": "uno de: warm, cool, dark, green, purple según el rubro",
  "template": "uno de: editorial, retro, boutique, modern — editorial para bares/gyms/barberías/tattoo/peluquerías, retro para panaderías/almacenes/rotiserías/cafeterías, boutique para restaurantes/clínicas/spas/estudios/hoteles, modern para todo lo demás"
}}"""

def parse_claude_response(raw: str) -> dict:
    match = re.search(r'\{{.*\}}', raw, re.DOTALL)
    candidate = match.group() if match else raw
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude response is not valid JSON: {{e}}\nRaw: {{raw[:200]}}") from e

def render_html(content: dict, business: dict) -> str:
    scheme = content.get("color_scheme", "dark")
    template_key = content.get("template", "modern")
    template_file = TEMPLATE_MAP.get(template_key, "template_modern.html")
    icons = ICONS_BY_SCHEME.get(scheme, DEFAULT_ICONS)
    services_with_icons = [
        {{"icon": icons[i % len(icons)], "name": svc}}
        for i, svc in enumerate(content.get("services", []))
    ]
    phone = business.get("phone", "") or ""
    wa_number = re.sub(r"[^0-9]", "", phone)

    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"))
    template = env.get_template(template_file)
    return template.render(
        business_name=business.get("name", ""),
        category=business.get("category", ""),
        tagline=content.get("tagline", ""),
        about=content.get("about", ""),
        services=services_with_icons,
        cta_text=content.get("cta_text", "Contactanos"),
        phone=phone,
        wa_number=wa_number,
        address=business.get("address", ""),
        city=business.get("city", ""),
        rating=business.get("rating"),
        review_count=business.get("review_count"),
        hours=business.get("hours", "") or "",
        color_scheme=scheme,
    )

def generate_content(business: dict, api_key: str) -> dict:
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{{"role": "user", "content": build_prompt(business)}}],
    )
    raw = message.content[0].text
    return parse_claude_response(raw)

def run(db_path: str, api_key: str) -> None:
    businesses = (
        get_businesses_by_status(db_path, "email_found") +
        get_businesses_by_status(db_path, "no_email")
    )
    logger.info(f"Generando demos para {{len(businesses)}} negocios")
    output_dir = Path("generated_demos")
    output_dir.mkdir(exist_ok=True)

    for biz in businesses:
        try:
            logger.info(f"Generando demo para: {{biz['name']}}")
            content = generate_content(biz, api_key)
            html = render_html(content, biz)

            slug = re.sub(r'[^a-z0-9]+', '-', biz["name"].lower()).strip('-')
            filename = f"{{int(biz['id'])}}-{{slug}}.html"
            filepath = output_dir / filename
            filepath.write_text(html, encoding="utf-8")

            update_business(
                db_path, biz["id"],
                color_scheme=content.get("color_scheme"),
                demo_html_path=str(filepath),
                status="demo_generated",
            )
            time.sleep(1)
        except Exception as e:
            logger.error(f"Error generando demo para {{biz['name']}}: {{e}}")
            update_business(db_path, biz["id"], status="error", error_message=str(e))
```

**Nota:** al copiar este código, los `{{` y `}}` dentro de las f-strings deben ser `{` y `}` simples. El plan los dobla para escapar el formato Markdown.

- [ ] **Step 4: Correr tests**

```
pytest tests/test_demo_generator.py -v
```

Esperado: todos PASS.

- [ ] **Step 5: Commit**

```
git add demo_generator.py tests/test_demo_generator.py
git commit -m "feat: multi-template selection in demo_generator"
```

---

## Task 3: template_modern.html (fallback mejorado)

**Files:**
- Create: `templates/template_modern.html`

- [ ] **Step 1: Crear el archivo**

```html
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ business_name }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;900&family=Bricolage+Grotesque:wght@700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{font-family:'Inter',system-ui,sans-serif;background:#fff;color:#111}
body.warm{--p:#ea580c;--pd:#9a3412;--pl:#fff7ed;--pb:#fed7aa}
body.cool{--p:#2563eb;--pd:#1e40af;--pl:#eff6ff;--pb:#bfdbfe}
body.dark{--p:#7c3aed;--pd:#4c1d95;--pl:#f5f3ff;--pb:#ddd6fe}
body.green{--p:#16a34a;--pd:#14532d;--pl:#f0fdf4;--pb:#bbf7d0}
body.purple{--p:#db2777;--pd:#831843;--pl:#fdf2f8;--pb:#fbcfe8}
.demo-banner{background:#1e293b;color:#94a3b8;text-align:center;padding:9px 16px;font-size:.78rem}
.demo-banner strong{color:#f8fafc}
.demo-banner a{color:var(--p);text-decoration:none;font-weight:600}
nav{position:sticky;top:0;z-index:100;background:rgba(255,255,255,.93);backdrop-filter:blur(12px);border-bottom:1px solid #f1f5f9;padding:0 20px;display:flex;align-items:center;justify-content:space-between;height:54px}
.nav-logo{font-family:'Bricolage Grotesque',sans-serif;font-weight:800;font-size:1.05rem;color:var(--pd)}
.nav-cta{background:var(--p);color:#fff;padding:7px 16px;border-radius:8px;font-size:.82rem;font-weight:700;text-decoration:none;display:flex;align-items:center;gap:5px}
.hero{background:var(--pd);background-image:radial-gradient(ellipse at 70% 50%,color-mix(in srgb,var(--p) 30%,transparent),transparent 70%);color:#fff;padding:64px 24px 56px;text-align:center}
.hero-badge{display:inline-flex;align-items:center;gap:5px;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);padding:4px 14px;border-radius:999px;font-size:.75rem;font-weight:600;letter-spacing:.5px;text-transform:uppercase;margin-bottom:18px}
.hero h1{font-family:'Bricolage Grotesque',sans-serif;font-size:clamp(2rem,6vw,3.5rem);font-weight:800;line-height:1.05;margin-bottom:12px;letter-spacing:-1px}
.hero .tagline{font-size:1.05rem;opacity:.82;margin-bottom:28px;max-width:460px;margin-left:auto;margin-right:auto;line-height:1.55}
.hero-btns{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}
.btn-call{background:var(--p);color:#fff;padding:13px 26px;border-radius:10px;font-weight:700;font-size:.95rem;text-decoration:none;display:inline-flex;align-items:center;gap:7px;box-shadow:0 4px 18px rgba(0,0,0,.25)}
.btn-wa{background:#25d366;color:#fff;padding:13px 26px;border-radius:10px;font-weight:700;font-size:.95rem;text-decoration:none;display:inline-flex;align-items:center;gap:7px}
.hero-meta{display:flex;gap:18px;justify-content:center;flex-wrap:wrap;margin-top:28px;font-size:.82rem;opacity:.7}
.rating-strip{background:var(--pl);border-bottom:1px solid var(--pb);padding:13px 24px;display:flex;align-items:center;justify-content:center;gap:20px;flex-wrap:wrap;font-size:.88rem}
.rating-num{font-weight:800;font-size:1.15rem;color:var(--pd)}
.stars{color:#f59e0b;font-size:.95rem}
.section{padding:56px 24px;max-width:960px;margin:0 auto}
.section-label{font-size:.7rem;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--p);margin-bottom:8px}
.section h2{font-family:'Bricolage Grotesque',sans-serif;font-size:clamp(1.4rem,3.5vw,2rem);font-weight:800;color:#111;margin-bottom:18px;letter-spacing:-.5px}
.about-text{font-size:1.02rem;line-height:1.8;color:#374151;max-width:620px}
.services-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-top:20px}
.service-card{background:var(--pl);border:1px solid var(--pb);border-radius:12px;padding:24px 18px;text-align:center;transition:transform .15s,box-shadow .15s}
.service-card:hover{transform:translateY(-3px);box-shadow:0 8px 20px rgba(0,0,0,.08)}
.svc-icon{font-size:2rem;display:block;margin-bottom:10px}
.service-card h3{font-size:.9rem;font-weight:700;color:var(--pd)}
.contact-section{background:var(--pd);background-image:radial-gradient(ellipse at 30% 50%,color-mix(in srgb,var(--p) 25%,transparent),transparent 60%);color:#fff;padding:64px 24px;text-align:center}
.contact-section .section-label{color:color-mix(in srgb,var(--pb) 80%,#fff)}
.contact-section h2{color:#fff;margin-bottom:10px}
.contact-section>p{opacity:.8;margin-bottom:28px;font-size:1rem}
.contact-cards{display:flex;gap:14px;justify-content:center;flex-wrap:wrap;margin-top:28px}
.contact-card{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.15);border-radius:12px;padding:18px 22px;min-width:150px;text-align:center;backdrop-filter:blur(8px)}
.ci-icon{font-size:1.4rem;margin-bottom:6px}
.ci-label{font-size:.68rem;opacity:.6;letter-spacing:1px;text-transform:uppercase;margin-bottom:3px}
.ci-value{font-weight:700;font-size:.9rem}
footer{background:#0f172a;color:#475569;padding:18px 24px;text-align:center;font-size:.78rem}
footer a{color:var(--p);text-decoration:none}
</style>
</head>
<body class="{{ color_scheme }}">
<div class="demo-banner">⚡ Demostración gratuita por <strong>Scalerics</strong> — <a href="https://wa.me/59899000000">¿Querés tu sitio así?</a></div>
<nav>
  <div class="nav-logo">{{ business_name }}</div>
  {% if phone %}<a href="tel:{{ phone }}" class="nav-cta">📞 Llamar</a>{% endif %}
</nav>
<div class="hero">
  <div class="hero-badge">{{ category }}{% if city %} · {{ city }}{% endif %}</div>
  <h1>{{ business_name }}</h1>
  <p class="tagline">{{ tagline }}</p>
  <div class="hero-btns">
    {% if phone %}<a href="tel:{{ phone }}" class="btn-call">📞 {{ phone }}</a>{% endif %}
    {% if wa_number %}<a href="https://wa.me/{{ wa_number }}" class="btn-wa">💬 WhatsApp</a>{% endif %}
  </div>
  <div class="hero-meta">
    {% if hours %}<span>⏰ {{ hours }}</span>{% endif %}
    {% if address %}<span>📍 {{ address }}</span>{% endif %}
  </div>
</div>
{% if rating %}
<div class="rating-strip">
  <span class="stars">★★★★★</span>
  <span class="rating-num">{{ rating }}/5</span>
  <span style="color:#6b7280">en Google Maps</span>
  {% if review_count %}<span style="color:#9ca3af">·</span><span>{{ review_count }} reseñas</span>{% endif %}
</div>
{% endif %}
<div class="section">
  <div class="section-label">Quiénes somos</div>
  <h2>Sobre nosotros</h2>
  <p class="about-text">{{ about }}</p>
</div>
<div style="background:#f8fafc">
<div class="section">
  <div class="section-label">Lo que ofrecemos</div>
  <h2>Nuestros servicios</h2>
  <div class="services-grid">
    {% for svc in services %}
    <div class="service-card"><span class="svc-icon">{{ svc.icon }}</span><h3>{{ svc.name }}</h3></div>
    {% endfor %}
  </div>
</div>
</div>
<div class="contact-section">
  <div class="section-label">Contacto</div>
  <h2>¿Cómo llegarnos?</h2>
  <p>{{ cta_text }} — estamos para ayudarte</p>
  {% if phone %}<a href="tel:{{ phone }}" class="btn-call" style="margin-bottom:8px">📞 {{ cta_text }}</a>{% endif %}
  <div class="contact-cards">
    {% if phone %}<div class="contact-card"><div class="ci-icon">📞</div><div class="ci-label">Teléfono</div><div class="ci-value">{{ phone }}</div></div>{% endif %}
    {% if address %}<div class="contact-card"><div class="ci-icon">📍</div><div class="ci-label">Dirección</div><div class="ci-value">{{ address }}</div></div>{% endif %}
    {% if hours %}<div class="contact-card"><div class="ci-icon">⏰</div><div class="ci-label">Horarios</div><div class="ci-value">{{ hours }}</div></div>{% endif %}
  </div>
</div>
<footer>Sitio web creado por <a href="https://wa.me/59899000000">Scalerics</a> · ¿Querés el tuyo? <a href="https://wa.me/59899000000">Contactanos</a></footer>
</body>
</html>
```

- [ ] **Step 2: Verificar que render_html funciona con este template**

```
python -c "
from demo_generator import render_html
c = {'tagline':'Test','about':'Sobre nosotros','services':['A','B'],'cta_text':'Llamar','color_scheme':'warm','template':'modern'}
b = {'name':'Test','category':'Bar','phone':'+598 99 111 222','address':'Calle 1','city':'MVD','rating':4.5,'review_count':10,'hours':'9-18'}
html = render_html(c, b)
print('OK' if 'Test' in html and '59899111222' in html else 'FAIL')
"
```

Esperado: `OK`

- [ ] **Step 3: Commit**

```
git add templates/template_modern.html
git commit -m "feat: add improved modern template with WhatsApp support"
```

---

## Task 4: template_editorial.html (dark/bold — bares, gyms, barberías)

**Files:**
- Create: `templates/template_editorial.html`

- [ ] **Step 1: Crear el archivo**

```html
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ business_name }}</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;700&family=Inter:wght@400;600&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{font-family:'Inter',sans-serif;background:#0d0d0d;color:#e5e7eb}
.demo-banner{background:#1a1a1a;border-bottom:1px solid #222;color:#6b7280;text-align:center;padding:8px 16px;font-size:.75rem}
.demo-banner strong{color:#e5e7eb}
.demo-banner a{color:#ff3b00;text-decoration:none;font-weight:600}
nav{position:sticky;top:0;z-index:100;background:rgba(13,13,13,.95);backdrop-filter:blur(10px);border-bottom:1px solid #1f1f1f;padding:0 20px;display:flex;align-items:center;justify-content:space-between;height:54px}
.nav-logo{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:1rem;color:#fff;letter-spacing:-.3px}
.nav-cta{background:#ff3b00;color:#fff;padding:7px 16px;border-radius:6px;font-size:.8rem;font-weight:700;text-decoration:none;letter-spacing:.3px}
.hero{padding:56px 24px 48px;border-bottom:1px solid #1f1f1f;max-width:900px;margin:0 auto}
.hero-category{color:#ff3b00;font-size:.68rem;font-weight:700;letter-spacing:2px;text-transform:uppercase;margin-bottom:12px}
.hero-title{font-family:'Space Grotesk',sans-serif;font-size:clamp(3rem,10vw,6rem);font-weight:700;line-height:.92;letter-spacing:-3px;color:#fff;margin-bottom:20px;text-transform:uppercase}
.hero-title em{color:#ff3b00;font-style:normal}
.hero-tagline{font-size:1rem;color:#6b7280;line-height:1.6;max-width:420px;margin-bottom:28px}
.hero-btns{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.btn-call{background:#ff3b00;color:#fff;padding:13px 24px;border-radius:6px;font-weight:700;font-size:.92rem;text-decoration:none;letter-spacing:.3px}
.btn-wa{background:#111;border:1px solid #333;color:#e5e7eb;padding:13px 24px;border-radius:6px;font-weight:600;font-size:.92rem;text-decoration:none}
.hero-stats{display:flex;gap:32px;margin-top:36px;padding-top:28px;border-top:1px solid #1f1f1f;flex-wrap:wrap}
.stat-val{font-family:'Space Grotesk',sans-serif;font-size:1.6rem;font-weight:700;color:#fff}
.stat-label{font-size:.65rem;color:#4b5563;text-transform:uppercase;letter-spacing:1px;margin-top:2px}
.section{padding:52px 24px;max-width:900px;margin:0 auto;border-bottom:1px solid #1a1a1a}
.section-label{font-size:.65rem;color:#ff3b00;font-weight:700;letter-spacing:2px;text-transform:uppercase;margin-bottom:10px}
.section h2{font-family:'Space Grotesk',sans-serif;font-size:clamp(1.4rem,4vw,2rem);font-weight:700;color:#fff;margin-bottom:16px;letter-spacing:-1px}
.about-text{font-size:.98rem;line-height:1.8;color:#9ca3af;max-width:600px}
.services-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1px;background:#1f1f1f;margin-top:20px;border:1px solid #1f1f1f}
.service-card{background:#0d0d0d;padding:28px 20px;text-align:center}
.service-card:hover{background:#111}
.svc-icon{font-size:1.8rem;display:block;margin-bottom:10px}
.service-card h3{font-size:.85rem;font-weight:700;color:#e5e7eb;font-family:'Space Grotesk',sans-serif;text-transform:uppercase;letter-spacing:.5px}
.contact-section{background:#0d0d0d;border-top:1px solid #1f1f1f;padding:60px 24px;text-align:center}
.contact-section .section-label{color:#ff3b00}
.contact-section h2{font-family:'Space Grotesk',sans-serif;color:#fff;font-size:clamp(1.6rem,4vw,2.2rem);font-weight:700;letter-spacing:-1px;margin-bottom:10px}
.contact-section>p{color:#6b7280;margin-bottom:28px}
.contact-cards{display:flex;gap:1px;justify-content:center;flex-wrap:wrap;margin-top:28px;background:#1f1f1f;border:1px solid #1f1f1f;max-width:600px;margin-left:auto;margin-right:auto}
.contact-card{background:#0d0d0d;padding:20px 24px;flex:1;min-width:140px;text-align:center}
.ci-icon{font-size:1.3rem;margin-bottom:6px}
.ci-label{font-size:.62rem;color:#4b5563;letter-spacing:1px;text-transform:uppercase;margin-bottom:4px}
.ci-value{font-weight:700;font-size:.88rem;color:#e5e7eb}
footer{background:#080808;color:#374151;padding:16px 24px;text-align:center;font-size:.75rem;border-top:1px solid #111}
footer a{color:#ff3b00;text-decoration:none}
</style>
</head>
<body>
<div class="demo-banner">⚡ Demo gratuita por <strong>Scalerics</strong> · <a href="https://wa.me/59899000000">¿Querés tu sitio?</a></div>
<nav>
  <div class="nav-logo">{{ business_name }}</div>
  {% if phone %}<a href="tel:{{ phone }}" class="nav-cta">📞 Llamar</a>{% endif %}
</nav>
<div class="hero">
  <div class="hero-category">{{ category }}{% if city %} · {{ city }}{% endif %}</div>
  {% set words = business_name.split() %}
  {% if words|length >= 2 %}
  <div class="hero-title">{{ words[:-1]|join(' ') }}<br><em>{{ words[-1] }}</em></div>
  {% else %}
  <div class="hero-title"><em>{{ business_name }}</em></div>
  {% endif %}
  <p class="hero-tagline">{{ tagline }}</p>
  <div class="hero-btns">
    {% if phone %}<a href="tel:{{ phone }}" class="btn-call">📞 {{ phone }}</a>{% endif %}
    {% if wa_number %}<a href="https://wa.me/{{ wa_number }}" class="btn-wa">💬 WhatsApp</a>{% endif %}
  </div>
  <div class="hero-stats">
    {% if rating %}<div><div class="stat-val">{{ rating }}★</div><div class="stat-label">Google Maps</div></div>{% endif %}
    {% if review_count %}<div><div class="stat-val">{{ review_count }}</div><div class="stat-label">Reseñas</div></div>{% endif %}
    {% if city %}<div><div class="stat-val">{{ city }}</div><div class="stat-label">Ubicación</div></div>{% endif %}
  </div>
</div>
<div class="section">
  <div class="section-label">Quiénes somos</div>
  <h2>Sobre nosotros</h2>
  <p class="about-text">{{ about }}</p>
</div>
<div class="section" style="border-bottom:none">
  <div class="section-label">Lo que hacemos</div>
  <h2>Servicios</h2>
  <div class="services-grid">
    {% for svc in services %}<div class="service-card"><span class="svc-icon">{{ svc.icon }}</span><h3>{{ svc.name }}</h3></div>{% endfor %}
  </div>
</div>
<div class="contact-section">
  <div class="section-label">Contacto</div>
  <h2>Hablemos</h2>
  <p>{{ cta_text }} — te respondemos rápido</p>
  {% if phone %}<a href="tel:{{ phone }}" class="btn-call">📞 {{ cta_text }}</a>{% endif %}
  <div class="contact-cards">
    {% if phone %}<div class="contact-card"><div class="ci-icon">📞</div><div class="ci-label">Teléfono</div><div class="ci-value">{{ phone }}</div></div>{% endif %}
    {% if address %}<div class="contact-card"><div class="ci-icon">📍</div><div class="ci-label">Dirección</div><div class="ci-value">{{ address }}</div></div>{% endif %}
    {% if hours %}<div class="contact-card"><div class="ci-icon">⏰</div><div class="ci-label">Horario</div><div class="ci-value">{{ hours }}</div></div>{% endif %}
  </div>
</div>
<footer>Creado por <a href="https://wa.me/59899000000">Scalerics</a></footer>
</body>
</html>
```

- [ ] **Step 2: Verificar render**

```
python -c "
from demo_generator import render_html
c = {'tagline':'El bar de siempre','about':'Bar de barrio','services':['Birra','Picadas','Fútbol'],'cta_text':'Llamanos','color_scheme':'dark','template':'editorial'}
b = {'name':'Ejido Bar','category':'Bar','phone':'+598 99 456 789','address':'Centro','city':'MVD','rating':4.5,'review_count':89,'hours':'12-2am'}
print('OK' if 'Ejido' in render_html(c,b) else 'FAIL')
"
```

- [ ] **Step 3: Commit**

```
git add templates/template_editorial.html
git commit -m "feat: add editorial bold template for bars/gyms/barbershops"
```

---

## Task 5: template_retro.html (cálido/retro — panaderías, almacenes)

**Files:**
- Create: `templates/template_retro.html`

- [ ] **Step 1: Crear el archivo**

```html
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ business_name }}</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;1,700&family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{font-family:'Inter',sans-serif;background:#fefce8;color:#1c1917}
.demo-banner{background:#92400e;color:#fde68a;text-align:center;padding:8px 16px;font-size:.75rem}
.demo-banner strong{color:#fff}
.demo-banner a{color:#fde68a;text-decoration:none;font-weight:600}
nav{position:sticky;top:0;z-index:100;background:#78350f;padding:0 20px;display:flex;align-items:center;justify-content:space-between;height:52px;border-bottom:3px solid #d97706}
.nav-logo{font-family:'Playfair Display',serif;font-size:1.1rem;color:#fef3c7;font-style:italic}
.nav-cta{background:#d97706;color:#fff;padding:7px 14px;border-radius:4px;font-size:.8rem;font-weight:700;text-decoration:none}
.hero{background:#78350f;padding:48px 24px 40px;text-align:center;border-bottom:3px dashed #d97706;position:relative}
.hero-icon{font-size:3.2rem;margin-bottom:12px;display:block}
.hero h1{font-family:'Playfair Display',serif;font-size:clamp(2rem,6vw,3.2rem);font-weight:700;color:#fef3c7;line-height:1.1;margin-bottom:6px}
.hero-since{font-size:.72rem;color:#d97706;letter-spacing:2px;text-transform:uppercase;margin-bottom:14px}
.hero .tagline{font-size:.95rem;color:#fde68a;font-style:italic;margin-bottom:24px;max-width:400px;margin-left:auto;margin-right:auto}
.hero-btns{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}
.btn-call{background:#d97706;color:#fff;padding:12px 22px;border-radius:6px;font-weight:700;font-size:.92rem;text-decoration:none;display:inline-flex;align-items:center;gap:6px}
.btn-wa{background:#25d366;color:#fff;padding:12px 22px;border-radius:6px;font-weight:700;font-size:.92rem;text-decoration:none;display:inline-flex;align-items:center;gap:6px}
.hero-meta{display:flex;gap:16px;justify-content:center;flex-wrap:wrap;margin-top:20px;font-size:.8rem;color:#d97706}
.rating-strip{background:#fffbeb;border-top:2px dashed #d97706;border-bottom:2px dashed #d97706;padding:12px 24px;display:flex;align-items:center;justify-content:center;gap:16px;flex-wrap:wrap;font-size:.88rem;color:#92400e}
.rating-num{font-family:'Playfair Display',serif;font-weight:700;font-size:1.2rem}
.stars{color:#d97706}
.section{padding:48px 24px;max-width:900px;margin:0 auto}
.section-label{font-size:.68rem;color:#92400e;font-weight:700;letter-spacing:2px;text-transform:uppercase;margin-bottom:8px}
.section h2{font-family:'Playfair Display',serif;font-size:clamp(1.5rem,4vw,2rem);font-weight:700;color:#78350f;margin-bottom:16px}
.about-text{font-size:1rem;line-height:1.8;color:#57534e;max-width:620px}
.services-grid{display:flex;flex-wrap:wrap;gap:0;margin-top:20px;border:2px dashed #d97706}
.service-card{flex:1;min-width:150px;padding:24px 16px;text-align:center;border-right:2px dashed #d97706;background:#fffbeb}
.service-card:last-child{border-right:none}
.service-card:hover{background:#fef9c3}
.svc-icon{font-size:1.8rem;display:block;margin-bottom:8px}
.service-card h3{font-size:.82rem;font-weight:700;color:#92400e;text-transform:uppercase;letter-spacing:.5px}
.contact-section{background:#78350f;border-top:3px dashed #d97706;padding:56px 24px;text-align:center}
.contact-section .section-label{color:#d97706}
.contact-section h2{font-family:'Playfair Display',serif;color:#fef3c7;font-size:clamp(1.5rem,4vw,2rem);margin-bottom:10px}
.contact-section>p{color:#fde68a;font-style:italic;margin-bottom:24px;font-size:.95rem}
.contact-cards{display:flex;gap:0;justify-content:center;flex-wrap:wrap;margin-top:24px;border:2px dashed rgba(217,119,6,.5);max-width:560px;margin-left:auto;margin-right:auto}
.contact-card{padding:18px 22px;flex:1;min-width:130px;text-align:center;border-right:2px dashed rgba(217,119,6,.5)}
.contact-card:last-child{border-right:none}
.ci-icon{font-size:1.3rem;margin-bottom:5px}
.ci-label{font-size:.62rem;color:#d97706;letter-spacing:1px;text-transform:uppercase;margin-bottom:3px}
.ci-value{font-weight:700;font-size:.88rem;color:#fef3c7}
footer{background:#451a03;color:#78350f;padding:16px 24px;text-align:center;font-size:.75rem}
footer a{color:#d97706;text-decoration:none}
</style>
</head>
<body>
<div class="demo-banner">⚡ Demo gratuita por <strong>Scalerics</strong> · <a href="https://wa.me/59899000000">¿Querés tu sitio?</a></div>
<nav>
  <div class="nav-logo">{{ business_name }}</div>
  {% if phone %}<a href="tel:{{ phone }}" class="nav-cta">📞 Llamar</a>{% endif %}
</nav>
<div class="hero">
  {% for svc in services[:1] %}<span class="hero-icon">{{ svc.icon }}</span>{% else %}<span class="hero-icon">🏪</span>{% endfor %}
  <h1>{{ business_name }}</h1>
  {% if city %}<div class="hero-since">{{ category }} · {{ city }}</div>{% endif %}
  <p class="tagline">"{{ tagline }}"</p>
  <div class="hero-btns">
    {% if phone %}<a href="tel:{{ phone }}" class="btn-call">📞 {{ phone }}</a>{% endif %}
    {% if wa_number %}<a href="https://wa.me/{{ wa_number }}" class="btn-wa">💬 WhatsApp</a>{% endif %}
  </div>
  <div class="hero-meta">
    {% if hours %}<span>⏰ {{ hours }}</span>{% endif %}
    {% if address %}<span>📍 {{ address }}</span>{% endif %}
  </div>
</div>
{% if rating %}
<div class="rating-strip">
  <span class="stars">★★★★★</span>
  <span class="rating-num">{{ rating }}/5</span>
  <span>en Google Maps</span>
  {% if review_count %}<span>· {{ review_count }} reseñas</span>{% endif %}
</div>
{% endif %}
<div class="section">
  <div class="section-label">Quiénes somos</div>
  <h2>Sobre nosotros</h2>
  <p class="about-text">{{ about }}</p>
</div>
<div style="background:#fffbeb;border-top:2px dashed #d97706;border-bottom:2px dashed #d97706">
<div class="section">
  <div class="section-label">Lo que ofrecemos</div>
  <h2>Nuestros productos</h2>
  <div class="services-grid">
    {% for svc in services %}<div class="service-card"><span class="svc-icon">{{ svc.icon }}</span><h3>{{ svc.name }}</h3></div>{% endfor %}
  </div>
</div>
</div>
<div class="contact-section">
  <div class="section-label">Contacto</div>
  <h2>Visitanos o llamanos</h2>
  <p>{{ cta_text }} — siempre con atención personalizada</p>
  {% if phone %}<a href="tel:{{ phone }}" class="btn-call">📞 {{ cta_text }}</a>{% endif %}
  <div class="contact-cards">
    {% if phone %}<div class="contact-card"><div class="ci-icon">📞</div><div class="ci-label">Teléfono</div><div class="ci-value">{{ phone }}</div></div>{% endif %}
    {% if address %}<div class="contact-card"><div class="ci-icon">📍</div><div class="ci-label">Dirección</div><div class="ci-value">{{ address }}</div></div>{% endif %}
    {% if hours %}<div class="contact-card"><div class="ci-icon">⏰</div><div class="ci-label">Horarios</div><div class="ci-value">{{ hours }}</div></div>{% endif %}
  </div>
</div>
<footer>Creado por <a href="https://wa.me/59899000000">Scalerics</a></footer>
</body>
</html>
```

- [ ] **Step 2: Verificar render**

```
python -c "
from demo_generator import render_html
c = {'tagline':'El pan de siempre','about':'Panadería artesanal','services':['Pan','Facturas','Tortas'],'cta_text':'Visitanos','color_scheme':'warm','template':'retro'}
b = {'name':'El Manjar','category':'Panadería','phone':'+598 99 111 000','address':'Calle 5','city':'MVD','rating':4.3,'review_count':55,'hours':'6-18'}
print('OK' if 'El Manjar' in render_html(c,b) else 'FAIL')
"
```

- [ ] **Step 3: Commit**

```
git add templates/template_retro.html
git commit -m "feat: add retro warm template for bakeries/stores"
```

---

## Task 6: template_boutique.html (premium — restaurantes, clínicas, spas)

**Files:**
- Create: `templates/template_boutique.html`

- [ ] **Step 1: Crear el archivo**

```html
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ business_name }}</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;1,700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{font-family:'Inter',sans-serif;background:#0f1623;color:#e2e8f0}
.demo-banner{background:#0a0f1a;border-bottom:1px solid rgba(196,164,102,.15);color:#475569;text-align:center;padding:8px 16px;font-size:.75rem}
.demo-banner strong{color:#c4a466}
.demo-banner a{color:#c4a466;text-decoration:none;font-weight:600}
nav{position:sticky;top:0;z-index:100;background:rgba(15,22,35,.95);backdrop-filter:blur(12px);border-bottom:1px solid rgba(196,164,102,.15);padding:0 24px;display:flex;align-items:center;justify-content:space-between;height:56px}
.nav-logo{font-family:'Playfair Display',serif;font-size:1.05rem;color:#c4a466;letter-spacing:.5px}
.nav-cta{border:1px solid rgba(196,164,102,.5);color:#c4a466;padding:7px 16px;border-radius:4px;font-size:.78rem;font-weight:500;text-decoration:none;letter-spacing:.3px;text-transform:uppercase}
.hero{padding:72px 24px 60px;max-width:900px;margin:0 auto;position:relative}
.hero::after{content:'';position:absolute;top:20px;right:-40px;width:300px;height:300px;background:radial-gradient(circle,rgba(196,164,102,.07),transparent 70%);pointer-events:none}
.hero-sub{color:#c4a466;font-size:.68rem;letter-spacing:3px;text-transform:uppercase;margin-bottom:14px}
.hero h1{font-family:'Playfair Display',serif;font-size:clamp(2.2rem,6vw,3.8rem);font-weight:700;color:#fff;line-height:1.08;margin-bottom:6px;letter-spacing:-.5px}
.hero h1 em{color:#c4a466;font-style:italic}
.hero-divider{width:48px;height:1px;background:linear-gradient(90deg,#c4a466,transparent);margin:20px 0}
.hero-tagline{font-size:1rem;color:#64748b;line-height:1.75;max-width:420px;margin-bottom:28px}
.hero-btns{display:flex;gap:12px;flex-wrap:wrap}
.btn-call{background:linear-gradient(135deg,#c4a466,#a07840);color:#0f1623;padding:13px 26px;border-radius:6px;font-weight:700;font-size:.9rem;text-decoration:none;display:inline-flex;align-items:center;gap:7px;letter-spacing:.3px}
.btn-wa{background:transparent;border:1px solid rgba(196,164,102,.4);color:#c4a466;padding:13px 26px;border-radius:6px;font-weight:500;font-size:.9rem;text-decoration:none;display:inline-flex;align-items:center;gap:7px}
.hero-meta{display:flex;gap:20px;margin-top:28px;flex-wrap:wrap}
.hero-meta span{font-size:.8rem;color:#334155;display:flex;align-items:center;gap:5px}
.rating-strip{border-top:1px solid rgba(196,164,102,.12);border-bottom:1px solid rgba(196,164,102,.12);padding:16px 24px;display:flex;align-items:center;justify-content:center;gap:20px;flex-wrap:wrap;font-size:.88rem;background:rgba(196,164,102,.03)}
.rating-num{font-family:'Playfair Display',serif;font-weight:700;font-size:1.2rem;color:#c4a466}
.stars{color:#c4a466}
.section{padding:60px 24px;max-width:900px;margin:0 auto;border-bottom:1px solid rgba(196,164,102,.08)}
.section-label{font-size:.65rem;color:#c4a466;font-weight:600;letter-spacing:2.5px;text-transform:uppercase;margin-bottom:10px}
.section h2{font-family:'Playfair Display',serif;font-size:clamp(1.5rem,4vw,2.2rem);font-weight:700;color:#fff;margin-bottom:18px}
.about-text{font-size:.98rem;line-height:1.85;color:#64748b;max-width:600px}
.services-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1px;background:rgba(196,164,102,.1);margin-top:24px;border:1px solid rgba(196,164,102,.1)}
.service-card{background:#0f1623;padding:28px 20px;text-align:center;transition:background .15s}
.service-card:hover{background:#131d2e}
.svc-icon{font-size:1.8rem;display:block;margin-bottom:10px}
.service-card h3{font-size:.85rem;font-weight:500;color:#94a3b8;letter-spacing:.5px}
.contact-section{background:#090e18;border-top:1px solid rgba(196,164,102,.12);padding:72px 24px;text-align:center;position:relative;overflow:hidden}
.contact-section::before{content:'';position:absolute;top:-80px;left:50%;transform:translateX(-50%);width:400px;height:400px;background:radial-gradient(circle,rgba(196,164,102,.05),transparent 70%);pointer-events:none}
.contact-section .section-label{color:#c4a466}
.contact-section h2{font-family:'Playfair Display',serif;color:#fff;font-size:clamp(1.6rem,4vw,2.4rem);margin-bottom:8px}
.contact-section>p{color:#475569;margin-bottom:28px;font-style:italic;font-size:.95rem}
.contact-cards{display:flex;gap:1px;justify-content:center;flex-wrap:wrap;margin-top:28px;background:rgba(196,164,102,.08);border:1px solid rgba(196,164,102,.1);max-width:580px;margin-left:auto;margin-right:auto}
.contact-card{background:#090e18;padding:22px 24px;flex:1;min-width:140px;text-align:center}
.ci-icon{font-size:1.2rem;margin-bottom:6px}
.ci-label{font-size:.6rem;color:#334155;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:4px}
.ci-value{font-weight:600;font-size:.88rem;color:#c4a466}
footer{background:#060b13;color:#1e293b;padding:18px 24px;text-align:center;font-size:.75rem;border-top:1px solid rgba(196,164,102,.07)}
footer a{color:#c4a466;text-decoration:none}
</style>
</head>
<body>
<div class="demo-banner">⚡ Demo gratuita por <strong>Scalerics</strong> · <a href="https://wa.me/59899000000">¿Querés tu sitio?</a></div>
<nav>
  <div class="nav-logo">{{ business_name }}</div>
  {% if phone %}<a href="tel:{{ phone }}" class="nav-cta">Llamar</a>{% endif %}
</nav>
<div class="hero">
  <div class="hero-sub">{{ category }}{% if city %} · {{ city }}{% endif %}</div>
  {% set words = business_name.split() %}
  {% if words|length >= 2 %}
  <h1>{{ words[:-1]|join(' ') }}<br><em>{{ words[-1] }}</em></h1>
  {% else %}
  <h1><em>{{ business_name }}</em></h1>
  {% endif %}
  <div class="hero-divider"></div>
  <p class="hero-tagline">{{ tagline }}</p>
  <div class="hero-btns">
    {% if phone %}<a href="tel:{{ phone }}" class="btn-call">📞 {{ phone }}</a>{% endif %}
    {% if wa_number %}<a href="https://wa.me/{{ wa_number }}" class="btn-wa">💬 WhatsApp</a>{% endif %}
  </div>
  <div class="hero-meta">
    {% if hours %}<span>⏰ {{ hours }}</span>{% endif %}
    {% if address %}<span>📍 {{ address }}</span>{% endif %}
  </div>
</div>
{% if rating %}
<div class="rating-strip">
  <span class="stars">★★★★★</span>
  <span class="rating-num">{{ rating }}/5</span>
  <span style="color:#334155">en Google Maps</span>
  {% if review_count %}<span style="color:#1e293b">·</span><span style="color:#475569">{{ review_count }} reseñas</span>{% endif %}
</div>
{% endif %}
<div class="section">
  <div class="section-label">Quiénes somos</div>
  <h2>Sobre nosotros</h2>
  <p class="about-text">{{ about }}</p>
</div>
<div class="section" style="border-bottom:none">
  <div class="section-label">Lo que ofrecemos</div>
  <h2>Nuestros servicios</h2>
  <div class="services-grid">
    {% for svc in services %}<div class="service-card"><span class="svc-icon">{{ svc.icon }}</span><h3>{{ svc.name }}</h3></div>{% endfor %}
  </div>
</div>
<div class="contact-section">
  <div class="section-label">Contacto</div>
  <h2>Reservas y consultas</h2>
  <p>{{ cta_text }}</p>
  {% if phone %}<a href="tel:{{ phone }}" class="btn-call">📞 {{ cta_text }}</a>{% endif %}
  <div class="contact-cards">
    {% if phone %}<div class="contact-card"><div class="ci-icon">📞</div><div class="ci-label">Teléfono</div><div class="ci-value">{{ phone }}</div></div>{% endif %}
    {% if address %}<div class="contact-card"><div class="ci-icon">📍</div><div class="ci-label">Dirección</div><div class="ci-value">{{ address }}</div></div>{% endif %}
    {% if hours %}<div class="contact-card"><div class="ci-icon">⏰</div><div class="ci-label">Horarios</div><div class="ci-value">{{ hours }}</div></div>{% endif %}
  </div>
</div>
<footer>Creado por <a href="https://wa.me/59899000000">Scalerics</a></footer>
</body>
</html>
```

- [ ] **Step 2: Verificar render**

```
python -c "
from demo_generator import render_html
c = {'tagline':'Gastronomía de autor','about':'Restaurante de cocina creativa','services':['Menú ejecutivo','Carta de vinos','Eventos privados'],'cta_text':'Reservar mesa','color_scheme':'dark','template':'boutique'}
b = {'name':'Pantagruel','category':'Restaurante','phone':'+598 99 789 012','address':'Ciudad Vieja','city':'MVD','rating':4.8,'review_count':112,'hours':'Mar-Dom 19-23'}
print('OK' if 'Pantagruel' in render_html(c,b) else 'FAIL')
"
```

- [ ] **Step 3: Commit**

```
git add templates/template_boutique.html
git commit -m "feat: add boutique premium template for restaurants/clinics/spas"
```

---

## Task 7: dashboard.py — Flask app completo

**Files:**
- Create: `dashboard.py`

- [ ] **Step 1: Instalar Flask**

```
pip install flask
```

- [ ] **Step 2: Crear `dashboard.py`**

```python
import threading
import webbrowser
from flask import Flask, jsonify, request, render_template_string

from database import get_all_businesses, update_business

app = Flask(__name__)
_db_path: str = ""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Scalerics — Panel de leads</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh;display:flex}
/* Sidebar */
.sidebar{width:220px;min-height:100vh;background:#161b27;border-right:1px solid #1e293b;display:flex;flex-direction:column;padding:20px 0;flex-shrink:0;position:fixed;top:0;bottom:0;left:0}
.sidebar-logo{padding:0 20px 22px;border-bottom:1px solid #1e293b;margin-bottom:14px}
.brand{font-size:1.1rem;font-weight:800;color:#fff}
.brand-sub{font-size:.7rem;color:#475569;margin-top:2px}
.nav-item{display:flex;align-items:center;gap:10px;padding:10px 20px;font-size:.85rem;font-weight:500;color:#64748b;cursor:pointer;border-left:3px solid transparent;transition:all .15s}
.nav-item:hover{color:#e2e8f0;background:#1e293b}
.nav-item.active{color:#fff;background:#1e293b;border-left-color:#6366f1}
.sidebar-bottom{margin-top:auto;padding:16px 20px;border-top:1px solid #1e293b}
.run-btn{width:100%;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;font-size:.82rem;font-weight:700;padding:10px;border-radius:8px;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px}
/* Main */
.main{margin-left:220px;padding:28px 32px;flex:1}
.page-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:24px}
.page-header h1{font-size:1.4rem;font-weight:800;color:#fff}
.page-date{font-size:.78rem;color:#475569;margin-top:3px}
/* Stats */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:24px}
.stat-card{background:#161b27;border:1px solid #1e293b;border-radius:12px;padding:18px 20px}
.stat-label{font-size:.68rem;color:#475569;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}
.stat-val{font-size:1.8rem;font-weight:800;color:#fff}
.stat-val.green{color:#4ade80}
.stat-val.yellow{color:#fbbf24}
.stat-val.blue{color:#60a5fa}
/* Filters */
.filters{display:flex;gap:8px;margin-bottom:16px;align-items:center;flex-wrap:wrap}
.filter-btn{padding:6px 14px;border-radius:8px;font-size:.78rem;font-weight:600;border:1px solid #1e293b;background:#161b27;color:#64748b;cursor:pointer;transition:all .15s}
.filter-btn:hover{color:#e2e8f0}
.filter-btn.active{background:#6366f1;border-color:#6366f1;color:#fff}
.filter-select{background:#161b27;border:1px solid #1e293b;border-radius:8px;padding:6px 12px;font-size:.78rem;color:#94a3b8;font-family:'Inter',sans-serif;cursor:pointer;outline:none}
.filter-select option{background:#161b27}
.search-box{margin-left:auto;background:#161b27;border:1px solid #1e293b;border-radius:8px;padding:7px 14px;font-size:.82rem;color:#e2e8f0;width:200px;outline:none;font-family:'Inter',sans-serif}
.search-box::placeholder{color:#334155}
/* Table */
.table-wrap{background:#161b27;border:1px solid #1e293b;border-radius:14px;overflow:hidden}
.table-header{display:grid;grid-template-columns:2fr 1.2fr 1fr .8fr 1.3fr 1.1fr;padding:12px 20px;background:#0f1117;border-bottom:1px solid #1e293b}
.table-header span{font-size:.65rem;font-weight:700;color:#334155;text-transform:uppercase;letter-spacing:1px}
.table-row{display:grid;grid-template-columns:2fr 1.2fr 1fr .8fr 1.3fr 1.1fr;padding:13px 20px;border-bottom:1px solid #1a2234;align-items:center;transition:background .1s}
.table-row:hover{background:#1a2234}
.table-row:last-child{border-bottom:none}
.biz-name{font-weight:600;font-size:.88rem;color:#e2e8f0}
.biz-cat{font-size:.7rem;color:#475569;margin-top:2px}
.phone-val{font-size:.8rem;color:#94a3b8;font-family:monospace}
.no-phone{font-size:.78rem;color:#1e293b}
.city-val{font-size:.82rem;color:#94a3b8}
.rating-val{font-size:.82rem;color:#fbbf24;font-weight:700}
.status-badge{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:999px;font-size:.68rem;font-weight:700}
.status-badge.scraped{background:#1e293b;color:#64748b}
.status-badge.no_email{background:#292524;color:#a16207}
.status-badge.demo_generated,.status-badge.demo_deployed{background:#1a2e1e;color:#4ade80}
.status-badge.contacted{background:#1e1b4b;color:#a5b4fc}
.status-badge.error{background:#2a1515;color:#f87171}
.dot{width:5px;height:5px;border-radius:50%;display:inline-block}
.dot.green{background:#4ade80}
.dot.orange{background:#fbbf24}
.dot.gray{background:#334155}
.dot.purple{background:#818cf8}
.dot.red{background:#f87171}
.actions{display:flex;gap:6px;align-items:center}
.demo-link{color:#6366f1;font-size:.78rem;text-decoration:none;font-weight:600;white-space:nowrap}
.demo-link:hover{color:#818cf8}
.no-demo{color:#1e293b;font-size:.75rem}
.contact-btn{background:#1e293b;border:none;color:#64748b;padding:5px 10px;border-radius:6px;font-size:.72rem;cursor:pointer;font-weight:600;font-family:'Inter',sans-serif;white-space:nowrap}
.contact-btn:hover{background:#6366f1;color:#fff}
.contacted-tag{font-size:.72rem;color:#a5b4fc;font-weight:600}
/* Modal */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:1000;align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal{background:#161b27;border:1px solid #1e293b;border-radius:16px;padding:28px;width:420px;max-width:90vw}
.modal h3{font-size:1rem;font-weight:700;color:#fff;margin-bottom:6px}
.modal p{font-size:.82rem;color:#64748b;margin-bottom:18px}
.modal textarea{width:100%;background:#0f1117;border:1px solid #1e293b;border-radius:8px;padding:10px 14px;font-size:.85rem;color:#e2e8f0;font-family:'Inter',sans-serif;resize:vertical;min-height:80px;outline:none;margin-bottom:16px}
.modal textarea::placeholder{color:#334155}
.modal-btns{display:flex;gap:10px;justify-content:flex-end}
.btn-cancel{background:#1e293b;border:none;color:#64748b;padding:9px 18px;border-radius:8px;cursor:pointer;font-size:.82rem;font-weight:600;font-family:'Inter',sans-serif}
.btn-confirm{background:#6366f1;border:none;color:#fff;padding:9px 18px;border-radius:8px;cursor:pointer;font-size:.82rem;font-weight:700;font-family:'Inter',sans-serif}
/* Pipeline modal */
.cmd-box{background:#0f1117;border:1px solid #1e293b;border-radius:8px;padding:12px 16px;font-family:monospace;font-size:.82rem;color:#4ade80;margin-bottom:16px;word-break:break-all}
.empty-state{padding:40px;text-align:center;color:#334155;font-size:.9rem}
</style>
</head>
<body>
<div class="sidebar">
  <div class="sidebar-logo">
    <div class="brand">⚡ Scalerics</div>
    <div class="brand-sub">Panel de leads</div>
  </div>
  <div class="nav-item active">📋 Leads</div>
  <div class="sidebar-bottom">
    <button class="run-btn" onclick="openPipelineModal()">▶ Correr pipeline</button>
  </div>
</div>

<div class="main">
  <div class="page-header">
    <div>
      <h1 id="page-title">Leads</h1>
      <div class="page-date" id="page-date"></div>
    </div>
  </div>

  <div class="stats">
    <div class="stat-card"><div class="stat-label">Total leads</div><div class="stat-val" id="stat-total">—</div></div>
    <div class="stat-card"><div class="stat-label">Demos listas</div><div class="stat-val green" id="stat-demos">—</div></div>
    <div class="stat-card"><div class="stat-label">Con teléfono</div><div class="stat-val yellow" id="stat-phones">—</div></div>
    <div class="stat-card"><div class="stat-label">Contactados</div><div class="stat-val blue" id="stat-contacted">—</div></div>
  </div>

  <div class="filters">
    <button class="filter-btn active" data-status="">Todos</button>
    <button class="filter-btn" data-status="demo_generated">Con demo</button>
    <button class="filter-btn" data-status="demo_deployed">Deployados</button>
    <button class="filter-btn" data-status="contacted">Contactados</button>
    <select class="filter-select" id="category-filter">
      <option value="">Todos los rubros</option>
    </select>
    <input class="search-box" id="search-input" placeholder="🔍 Buscar negocio...">
  </div>

  <div class="table-wrap">
    <div class="table-header">
      <span>Negocio</span><span>Teléfono</span><span>Ciudad</span><span>Rating</span><span>Estado</span><span>Acciones</span>
    </div>
    <div id="table-body"></div>
  </div>
</div>

<!-- Modal contactar -->
<div class="modal-overlay" id="contact-modal">
  <div class="modal">
    <h3 id="modal-title">Marcar como contactado</h3>
    <p id="modal-sub">Podés agregar una nota opcional sobre el contacto</p>
    <textarea id="modal-note" placeholder="Ej: Llamé el martes, quedó en pensar, volver a llamar en una semana..."></textarea>
    <div class="modal-btns">
      <button class="btn-cancel" onclick="closeModal()">Cancelar</button>
      <button class="btn-confirm" onclick="confirmContact()">✓ Confirmar contacto</button>
    </div>
  </div>
</div>

<!-- Modal pipeline -->
<div class="modal-overlay" id="pipeline-modal">
  <div class="modal">
    <h3>Correr el pipeline</h3>
    <p>Abrí una terminal en la carpeta del proyecto y ejecutá:</p>
    <div class="cmd-box">python main.py run-all --query "negocio ciudad" --max 50</div>
    <p style="margin-bottom:12px">O por pasos:</p>
    <div class="cmd-box" style="margin-bottom:8px">python main.py scrape --query "restaurante Montevideo" --max 50</div>
    <div class="cmd-box" style="margin-bottom:8px">python main.py find-emails</div>
    <div class="cmd-box" style="margin-bottom:8px">python main.py generate-demos</div>
    <div class="cmd-box" style="margin-bottom:8px">python main.py deploy</div>
    <div class="cmd-box">python main.py send-emails</div>
    <div class="modal-btns" style="margin-top:16px">
      <button class="btn-confirm" onclick="document.getElementById('pipeline-modal').classList.remove('open')">Cerrar</button>
    </div>
  </div>
</div>

<script>
let currentStatus = '';
let currentCategory = '';
let currentSearch = '';
let contactingId = null;

function statusDot(s) {
  if (s === 'contacted') return '<span class="dot purple"></span>';
  if (s === 'demo_generated' || s === 'demo_deployed') return '<span class="dot green"></span>';
  if (s === 'error') return '<span class="dot red"></span>';
  if (s === 'no_email') return '<span class="dot orange"></span>';
  return '<span class="dot gray"></span>';
}
function statusLabel(s) {
  const map = {scraped:'Scraped',no_email:'Sin email',email_found:'Email encontrado',
    demo_generated:'Demo lista',demo_deployed:'Deployado',contacted:'Contactado',error:'Error'};
  return map[s] || s;
}

async function loadStats() {
  const r = await fetch('/api/stats');
  const d = await r.json();
  document.getElementById('stat-total').textContent = d.total;
  document.getElementById('stat-demos').textContent = d.demo_ready;
  document.getElementById('stat-phones').textContent = d.with_phone;
  document.getElementById('stat-contacted').textContent = d.contacted;
  const sel = document.getElementById('category-filter');
  const prev = sel.value;
  while (sel.options.length > 1) sel.remove(1);
  (d.categories || []).forEach(c => {
    const o = new Option(c, c);
    sel.add(o);
  });
  if (prev) sel.value = prev;
  document.getElementById('page-date').textContent = 'Actualizado: ' + new Date().toLocaleString('es-UY');
}

async function loadLeads() {
  const params = new URLSearchParams();
  if (currentStatus) params.set('status', currentStatus);
  if (currentCategory) params.set('category', currentCategory);
  if (currentSearch) params.set('search', currentSearch);
  const r = await fetch('/api/leads?' + params);
  const leads = await r.json();
  const body = document.getElementById('table-body');
  if (!leads.length) { body.innerHTML = '<div class="empty-state">No hay leads con estos filtros</div>'; return; }
  body.innerHTML = leads.map(b => `
    <div class="table-row">
      <div><div class="biz-name">${esc(b.name||'')}</div><div class="biz-cat">${esc(b.category||'')}</div></div>
      <div>${b.phone ? `<span class="phone-val">${esc(b.phone)}</span>` : '<span class="no-phone">— sin teléfono</span>'}</div>
      <div class="city-val">${esc(b.city||'')}</div>
      <div>${b.rating ? `<span class="rating-val">★ ${b.rating}</span>` : '<span style="color:#1e293b">—</span>'}</div>
      <div><span class="status-badge ${b.status||''}">${statusDot(b.status)} ${statusLabel(b.status||'')}</span></div>
      <div class="actions">
        ${b.demo_url ? `<a class="demo-link" href="${b.demo_url}" target="_blank">Ver demo ↗</a>` : '<span class="no-demo">Sin demo</span>'}
        ${b.status !== 'contacted' ? `<button class="contact-btn" onclick="openContact(${b.id}, '${esc(b.name||'')}')">✓ Contactar</button>` : '<span class="contacted-tag">✓ Listo</span>'}
      </div>
    </div>`).join('');
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function openContact(id, name) {
  contactingId = id;
  document.getElementById('modal-title').textContent = `Contactar: ${name}`;
  document.getElementById('modal-note').value = '';
  document.getElementById('contact-modal').classList.add('open');
}
function closeModal() { document.getElementById('contact-modal').classList.remove('open'); contactingId = null; }
async function confirmContact() {
  if (!contactingId) return;
  const note = document.getElementById('modal-note').value;
  await fetch(`/api/leads/${contactingId}/contact`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({note})});
  closeModal();
  loadStats();
  loadLeads();
}
function openPipelineModal() { document.getElementById('pipeline-modal').classList.add('open'); }

document.querySelectorAll('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentStatus = btn.dataset.status;
    loadLeads();
  });
});
document.getElementById('category-filter').addEventListener('change', e => { currentCategory = e.target.value; loadLeads(); });
let searchTimeout;
document.getElementById('search-input').addEventListener('input', e => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => { currentSearch = e.target.value; loadLeads(); }, 300);
});
document.getElementById('contact-modal').addEventListener('click', e => { if (e.target === e.currentTarget) closeModal(); });
document.getElementById('pipeline-modal').addEventListener('click', e => { if (e.target === e.currentTarget) e.currentTarget.classList.remove('open'); });

loadStats();
loadLeads();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/leads")
def api_leads():
    businesses = get_all_businesses(_db_path)
    status = request.args.get("status")
    category = request.args.get("category")
    search = (request.args.get("search") or "").lower()
    if status:
        businesses = [b for b in businesses if b.get("status") == status]
    if category:
        businesses = [b for b in businesses if (b.get("category") or "").lower() == category.lower()]
    if search:
        businesses = [b for b in businesses if search in (b.get("name") or "").lower()]
    return jsonify(businesses)


@app.route("/api/leads/<int:biz_id>/contact", methods=["POST"])
def api_contact(biz_id):
    data = request.get_json() or {}
    note = data.get("note", "")
    update_business(_db_path, biz_id, status="contacted", notes=note)
    return jsonify({"ok": True})


@app.route("/api/stats")
def api_stats():
    businesses = get_all_businesses(_db_path)
    categories = sorted({b.get("category") or "" for b in businesses if b.get("category")})
    return jsonify({
        "total": len(businesses),
        "demo_ready": sum(1 for b in businesses if b.get("status") in ("demo_generated", "demo_deployed")),
        "with_phone": sum(1 for b in businesses if b.get("phone")),
        "contacted": sum(1 for b in businesses if b.get("status") == "contacted"),
        "categories": categories,
    })


def run(db_path: str) -> None:
    global _db_path
    _db_path = db_path
    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(port=5000, debug=False, use_reloader=False)
```

- [ ] **Step 3: Correr tests de database (verificar que get_all_businesses funciona correctamente)**

```
pytest tests/test_database.py -v
```

Esperado: todos PASS.

- [ ] **Step 4: Commit**

```
git add dashboard.py
git commit -m "feat: add Flask dashboard with leads table, filters, and contact modal"
```

---

## Task 8: main.py + requirements.txt — cablear dashboard

**Files:**
- Modify: `main.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Agregar `flask` a requirements.txt**

Agregar al final de `requirements.txt`:

```
flask
```

- [ ] **Step 2: Modificar `main.py`**

Agregar el subparser `dashboard`:

```python
# En create_parser(), agregar dentro de los subparsers (después del último add_parser):
subparsers.add_parser("dashboard", help="Abrir panel de leads en el browser")
```

Agregar la función `cmd_dashboard`:

```python
def cmd_dashboard(args):
    from dashboard import run
    run(DB_PATH)
```

Agregar la entrada en el dict `commands`:

```python
commands = {
    "scrape": cmd_scrape,
    "find-emails": cmd_find_emails,
    "generate-demos": cmd_generate_demos,
    "deploy": cmd_deploy,
    "send-emails": cmd_send_emails,
    "run-all": cmd_run_all,
    "dashboard": cmd_dashboard,
}
```

- [ ] **Step 3: Verificar que el comando aparece en el help**

```
python main.py --help
```

Esperado: `dashboard` aparece en la lista de subcomandos.

- [ ] **Step 4: Smoke test — abrir el dashboard**

```
python main.py dashboard
```

Esperado: browser abre en `http://localhost:5000`, se ve el panel oscuro con los leads de la DB.

- [ ] **Step 5: Commit**

```
git add main.py requirements.txt
git commit -m "feat: add dashboard subcommand to main pipeline"
```

---

## Task 9: Integración — regenerar demos con templates nuevos

- [ ] **Step 1: Resetear status de demos existentes para regenerarlas**

```
python -c "
import sqlite3
conn = sqlite3.connect('leads.db')
conn.execute(\"UPDATE businesses SET status='no_email' WHERE status='demo_generated'\")
conn.commit()
conn.close()
print('Reset OK')
"
```

- [ ] **Step 2: Regenerar demos**

```
python main.py generate-demos
```

Esperado: 6 demos generadas con los nuevos templates (editorial, retro, boutique, o modern según categoría).

- [ ] **Step 3: Abrir un demo de cada tipo y verificar visualmente**

Abrir los archivos HTML generados en `generated_demos/` en el browser y confirmar que:
- El teléfono aparece en el hero y en la sección de contacto
- El botón de WhatsApp existe y tiene el número correcto (solo dígitos)
- El template se corresponde con la categoría del negocio
- El banner de Scalerics está visible

- [ ] **Step 4: Verificar el dashboard con los nuevos datos**

```
python main.py dashboard
```

Confirmar en el browser:
- Los leads aparecen en la tabla
- El filtro por rubro funciona
- El buscador filtra por nombre
- El botón "Contactar" abre el modal y guarda la nota

- [ ] **Step 5: Correr todos los tests**

```
pytest tests/ -v
```

Esperado: todos PASS.

- [ ] **Step 6: Commit final**

```
git add .
git commit -m "feat: integrate multi-template demos with dashboard - pipeline fully operational"
```
