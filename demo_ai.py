"""Generate and deploy sales demo presentations using Claude AI + Vercel."""

import base64
import os
import re
import time
from pathlib import Path

import anthropic
import requests

LOGO_PATH = Path(r"C:\Users\juant\OneDrive\Desktop\Scalerics\Assets\logo_full.png")
_LOGO_PLACEHOLDER = "SCALERICS_LOGO_PLACEHOLDER"


def _logo_data_uri() -> str:
    with open(LOGO_PATH, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:image/png;base64,{b64}"


def _build_prompt(business_name: str, rubro: str, city: str, client_color: str,
                  lead_name: str, messages: list) -> str:
    conv_lines = []
    for m in messages[:40]:
        direction = "Cliente" if m.get("direction") in ("in", "inbound") else "Bot Scalerics"
        content = str(m.get("content", ""))[:200]
        conv_lines.append(f"[{direction}]: {content}")
    conv = "\n".join(conv_lines) if conv_lines else "(Sin conversación disponible)"

    color_hint = (f"Color primario conocido del negocio: {client_color}. Usalo como color principal en los mockups."
                  if client_color else
                  f"Elegí colores simbólicos apropiados para el rubro '{rubro}'.")

    slug = re.sub(r"[^a-z0-9]", "", business_name.lower())[:20] or "negocio"

    return f"""Generá un archivo HTML completo de una presentación interactiva de ventas para el negocio "{business_name}" ({rubro}, {city}).

DATOS DEL LEAD:
- Nombre de contacto: {lead_name}
- Negocio: {business_name}
- Rubro: {rubro}
- Ciudad: {city}
- {color_hint}

CONVERSACIÓN DE WHATSAPP (usala para personalizar el contenido):
{conv}

══════════════════════════════════════════════════════════
REQUISITOS TÉCNICOS

- 10 slides con position:absolute, opacity:0→1, transform para transiciones suaves (0.55s cubic-bezier)
- Progress bar superior con gradiente #06B6D4
- Navegación: flechas de teclado ←→ y botones prev/next abajo
- Puntos de navegación (dots) con el dot activo expandido en píldora
- Fondo general: #0F1419. Acento Scalerics: #06B6D4
- Google Fonts: DM Sans (principal) y Cormorant Garamond (para elegancia)
- Font Awesome 6 CDN para iconos
- Para el logo de Scalerics usá exactamente: <img src="{_LOGO_PLACEHOLDER}" style="height:36px;object-fit:contain;filter:brightness(1.1)">
- HTML completamente self-contained (solo deps externas: Google Fonts CDN y Font Awesome CDN)

══════════════════════════════════════════════════════════
LOS 10 SLIDES

SLIDE 1 — Portada:
Logo Scalerics arriba. Título grande: "Tu presencia online, {business_name}". Bajada personalizada para este negocio en {city}. Cuatro bullets con iconos Font Awesome relevantes para {rubro} (en una fila). Fondo: dark con dos glows circulares de color #06B6D4 y color del rubro.

SLIDE 2 — El problema (tag rojo "La situación hoy"):
Título impactante sobre el problema real de un {rubro} sin presencia web. Tres cards glassmorphism (bg:rgba(255,255,255,0.04);backdrop-filter:blur(14px);border:1px solid rgba(255,255,255,0.07);border-radius:1.25rem) con problemas MUY ESPECÍFICOS del rubro {rubro}.

SLIDE 3 — La solución (tag cyan "Lo que hacemos"):
Título + descripción. Cuatro items con checkmark (círculo cyan con ✓) y texto de entregables específicos para {rubro}.

SLIDE 4 — Propuesta 1 — [inventá un nombre de estilo apropiado para {rubro}]:
Una línea en cursiva describiendo la estética. Luego el browser frame completo:

<div style="width:100%;max-width:820px;border-radius:14px;overflow:hidden;box-shadow:0 30px 90px rgba(0,0,0,.75),0 0 0 1px rgba(255,255,255,.06)">
  <div style="background:#0d1117;padding:10px 14px;display:flex;align-items:center;gap:10px;border-bottom:1px solid rgba(255,255,255,.05)">
    <span style="width:11px;height:11px;border-radius:50%;background:#FF5F57;display:inline-block"></span>
    <span style="width:11px;height:11px;border-radius:50%;background:#FEBC2E;display:inline-block"></span>
    <span style="width:11px;height:11px;border-radius:50%;background:#28C840;display:inline-block"></span>
    <div style="flex:1;background:#161b22;border:1px solid rgba(255,255,255,.07);border-radius:6px;padding:5px 12px;font-size:11px;font-family:monospace;color:#64748b;display:flex;align-items:center;gap:6px">🔒 www.{slug}.com.uy</div>
  </div>
  <div style="overflow-y:auto;height:440px;scroll-behavior:smooth">
    [HOMEPAGE ULTRA-REALISTA AQUÍ — ver instrucciones abajo]
  </div>
</div>

SLIDE 5 — Propuesta 2 — [nombre de estilo diferente]:
Mismo browser frame pero estética completamente diferente: otra paleta, otra tipografía, otro layout de hero.

SLIDE 6 — Propuesta 3 — [nombre de estilo más diferente]:
Tercer browser frame. Puede ser minimalista, editorial, retro o premium según lo que más contraste con las anteriores.

SLIDE 7 — ¿Por qué una web profesional?:
Stats y datos específicos para {rubro} (porcentajes inventados pero verosímiles). Tres cards con números grandes.

SLIDE 8 — Qué incluye:
Lista detallada de deliverables específicos para {rubro}. Grid de dos columnas con íconos.

SLIDE 9 — La inversión:
Título "¿Cuánto cuesta?". Card central con rango de precios (en USD, sin ser exacto). CTA para agendar.

SLIDE 10 — Próximos pasos:
"¿Avanzamos, {lead_name}?" usando el nombre. Botón WhatsApp verde (href="https://wa.me/59899000000"). Email hola@scalerics.com. Tagline "Scalerics — Tu negocio, online."

══════════════════════════════════════════════════════════
INSTRUCCIONES CRÍTICAS PARA LOS MOCKUPS (slides 4-6)

Los tres browser mockups deben verse como DISEÑOS REALES y casi finales, NO wireframes.

Cada homepage debe incluir OBLIGATORIAMENTE:
1. Navbar sticky con logo/nombre del negocio, links de navegación y botón CTA
2. Hero section con fondo de color fuerte (gradiente o color sólido), headline grande, subtítulo, botón CTA principal
3. Grid de 4-6 productos o servicios con: emoji grande (80px) como placeholder de imagen, nombre real del producto, precio en pesos uruguayos (inventado pero coherente), botón de acción
4. Una sección secundaria (about, testimonios, o beneficios)
5. Footer con datos de contacto

Los TRES estilos deben ser MUY diferentes entre sí:
- Propuesta 1: puede ser bold/industrial, colorido y enérgico
- Propuesta 2: puede ser clean/moderno, blanco con acentos de color
- Propuesta 3: puede ser premium/elegante, oscuro o con serif

Productos/servicios deben tener NOMBRES REALES apropiados para {rubro}:
- Si es bicicletería: "Mountain Bike Trek 29\"" → $45.990, "Casco Giro Bike" → $3.490, etc.
- Si es restaurante: "Pasta al pesto" → $590, "Medallón de cerdo" → $790, etc.
- Adaptá siempre al rubro específico

══════════════════════════════════════════════════════════
PREGUNTAS ESENCIALES

Al final del body incluí exactamente esto:
<div id="essential-questions" style="display:none">
<ol>
[Exactamente 8 preguntas MUY ESPECÍFICAS para {rubro} que Juan necesita hacerle al cliente si acepta el presupuesto. Cosas como: ¿Tenés logo existente?, ¿Cuáles son tus 5 productos/servicios principales?, ¿Tenés fotos de productos?, ¿Manejás catálogo online o stock?, etc. Todo específico para {rubro}]
</ol>
</div>

══════════════════════════════════════════════════════════

Generá SOLO el HTML completo empezando con <!DOCTYPE html>. Sin explicaciones, sin markdown, sin bloques de código."""


def generate_and_deploy(
    phone: str,
    business_name: str,
    rubro: str,
    city: str,
    client_color: str,
    lead_name: str,
    messages: list,
) -> dict:
    """Generate demo HTML with Claude Opus, inject logo, deploy to Vercel.

    Returns {"url": str, "questions": list[str]}.
    """
    prompt = _build_prompt(business_name, rubro, city, client_color, lead_name, messages)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
        betas=["output-128k-2025-02-19"],
    )

    html = msg.content[0].text.strip()
    # Strip markdown code fences if Claude wrapped the output
    html = re.sub(r"^```[a-z]*\s*\n?", "", html)
    html = re.sub(r"\n?```\s*$", "", html)

    # Detect truncation: if HTML doesn't close properly, log a warning
    if not html.rstrip().endswith("</html>"):
        print(f"[demo_ai] WARNING: HTML may be truncated. stop_reason={msg.stop_reason}, length={len(html)}")

    # Inject actual Scalerics logo
    html = html.replace(_LOGO_PLACEHOLDER, _logo_data_uri())

    questions = _extract_questions(html)
    url = _deploy_to_vercel(html, business_name)

    return {"url": url, "questions": questions}


def _extract_questions(html: str) -> list:
    match = re.search(
        r'id=["\']essential-questions["\'][^>]*>(.*?)</div>',
        html, re.DOTALL | re.IGNORECASE
    )
    if not match:
        return []
    items = re.findall(r"<li>(.*?)</li>", match.group(1), re.DOTALL)
    return [re.sub(r"<[^>]+>", "", q).strip() for q in items if q.strip()]


def _deploy_to_vercel(html_content: str, business_name: str) -> str:
    token = os.environ.get("VERCEL_TOKEN", "")
    if not token:
        raise Exception("VERCEL_TOKEN no configurado en .env")

    slug = re.sub(r"[^a-z0-9]+", "-", business_name.lower()).strip("-")[:25]
    project_name = f"demo-{slug}-{int(time.time())}"

    resp = requests.post(
        "https://api.vercel.com/v13/deployments",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "name": project_name,
            "files": [{"file": "index.html", "data": html_content}],
            "projectSettings": {"framework": None, "buildCommand": None, "outputDirectory": None},
            "target": "production",
        },
        timeout=90,
    )

    data = resp.json()
    if "url" not in data:
        raise Exception(f"Vercel deploy failed: {data}")

    return f"https://{data['url']}"
