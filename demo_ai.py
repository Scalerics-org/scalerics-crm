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

    browser_frame = (
        f'<div style="width:100%;max-width:800px;border-radius:12px;overflow:hidden;'
        f'box-shadow:0 28px 80px rgba(0,0,0,.8),0 0 0 1px rgba(255,255,255,.06)">'
        f'<div style="background:#0d1117;padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid rgba(255,255,255,.05)">'
        f'<span style="width:11px;height:11px;border-radius:50%;background:#FF5F57;display:inline-block"></span>'
        f'<span style="width:11px;height:11px;border-radius:50%;background:#FEBC2E;display:inline-block"></span>'
        f'<span style="width:11px;height:11px;border-radius:50%;background:#28C840;display:inline-block"></span>'
        f'<div style="flex:1;background:#161b22;border:1px solid rgba(255,255,255,.07);border-radius:5px;padding:4px 10px;font-size:11px;font-family:monospace;color:#64748b">🔒 www.{slug}.com.uy</div>'
        f'</div><div style="height:400px;overflow:hidden">'
    )
    browser_close = '</div></div>'

    return f"""HTML completo: presentación de ventas para "{business_name}" ({rubro}, {city}).

LEAD: {lead_name} | {color_hint}
WHATSAPP: {conv}

TÉCNICO: 8 slides, position:absolute, opacity 0→1 (0.5s ease), progress bar #06B6D4 arriba, nav teclado ←→ + btns prev/next + dots (activo=píldora cyan), fondo #0F1419, DM Sans (Google Fonts), Font Awesome 6 CDN, logo exactamente: <img src="{_LOGO_PLACEHOLDER}" style="height:34px;object-fit:contain">.

S1-PORTADA: Logo Scalerics arriba izq. H1 grande: "Tu web, {business_name}". Bajada 1 línea personalizada. 3 bullets con íconos FA relevantes. Glow cyan circular en fondo.

S2-PROBLEMA (badge rojo "Hoy"): Título impactante. 3 cards glassmorphism (bg rgba(255,255,255,.04), backdrop-filter blur(12px), border 1px solid rgba(255,255,255,.07), border-radius 1.1rem) con problemas CONCRETOS de un {rubro} sin web.

S3-SOLUCIÓN (badge cyan "Lo que hacemos"): Título. 4 ítems: círculo cyan con ✓ + texto de entregable real para {rubro}. Abajo: 2 stats inline (números grandes, ej: "83% de compradores busca online antes de ir").

S4-PROPUESTA 1 — [nombre estilo bold/energético para {rubro}]:
Línea cursiva de la estética. Luego:
{browser_frame}[MOCKUP 1 AQUÍ]{browser_close}

S5-PROPUESTA 2 — [nombre estilo clean/moderno]:
Línea cursiva. Luego mismo frame, diseño MUY diferente al anterior.
{browser_frame}[MOCKUP 2 AQUÍ]{browser_close}

S6-PROPUESTA 3 — [nombre estilo premium/elegante]:
Línea cursiva. Tercer frame, máximo contraste con S4 y S5.
{browser_frame}[MOCKUP 3 AQUÍ]{browser_close}

S7-INVERSIÓN: Título "¿Cuánto cuesta?". Card central con rango USD + "Sin compromiso". Debajo: 2 columnas, 4 deliverables con ícono FA cada uno (específicos para {rubro}). CTA btn "Quiero mi web".

S8-PRÓXIMOS PASOS: "¿Arrancamos, {lead_name}?" centrado grande. Btn WA verde grande (href="https://wa.me/59899000000" target="_blank"). Línea: hola@scalerics.com. Tagline pequeño: "Scalerics — Tu negocio, online."

MOCKUPS (S4-S6) — CRÍTICO, SIN EXCEPCIONES:
Cada mockup: (1) navbar: fondo sólido, nombre negocio bold a la izq, 1 btn CTA a la der — SIN lista de links para ahorrar espacio; (2) hero: fondo gradiente fuerte, H1 grande impactante, subtítulo 1 línea, 1 btn CTA; (3) grid 3 productos: cada producto = emoji 64px + nombre real + precio $UY real + btn "Ver más". NADA MÁS (no footer, no about).
Los 3 estilos COMPLETAMENTE diferentes en paleta, tipografía y composición: bold+colorido · clean+blanco · dark+serif.
Productos con nombres y precios reales del rubro (mueblería→"Sillón Chester $24.900", restaurante→"Pasta al pesto $590", etc.).

PREGUNTAS: al final del body:
<div id="essential-questions" style="display:none"><ol>[6 preguntas concretas para {rubro}: logo?, productos principales?, fotos?, dominio?, redes?, etc.]</ol></div>

Generá SOLO el HTML completo desde <!DOCTYPE html>. Sin markdown, sin explicaciones."""


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
        max_tokens=20000,
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
