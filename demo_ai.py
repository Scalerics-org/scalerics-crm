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

    return f"""HTML completo: presentación de ventas interactiva para "{business_name}" ({rubro}, {city}).

LEAD: {lead_name} | {color_hint}

WHATSAPP (personalizá con esto):
{conv}

TÉCNICO: 10 slides, position:absolute, opacity 0→1 (0.5s ease), progress bar #06B6D4, nav ←→ + prev/next + dots (activo=píldora), fondo #0F1419, Google Fonts DM Sans+Cormorant, Font Awesome 6 CDN, logo: <img src="{_LOGO_PLACEHOLDER}" style="height:36px;object-fit:contain">.

SLIDES:

S1-PORTADA: Logo Scalerics. H1: "Tu presencia online, {business_name}". Bajada personalizada. 4 bullets FA-icons en fila. 2 glows circulares cyan+color rubro.

S2-PROBLEMA (tag rojo): Título impactante. 3 cards glassmorphism (bg:rgba(255,255,255,.04);backdrop-filter:blur(14px);border:1px solid rgba(255,255,255,.07);border-radius:1.2rem) con problemas reales de {rubro} sin web.

S3-SOLUCIÓN (tag cyan): Título. 4 checkmarks (círculo cyan+✓) con entregables específicos de {rubro}.

S4-PROPUESTA 1 [nombre de estilo para {rubro}]: Línea en cursiva de la estética. Browser frame:
<div style="width:100%;max-width:820px;border-radius:14px;overflow:hidden;box-shadow:0 30px 90px rgba(0,0,0,.75),0 0 0 1px rgba(255,255,255,.06)"><div style="background:#0d1117;padding:10px 14px;display:flex;align-items:center;gap:10px;border-bottom:1px solid rgba(255,255,255,.05)"><span style="width:11px;height:11px;border-radius:50%;background:#FF5F57;display:inline-block"></span><span style="width:11px;height:11px;border-radius:50%;background:#FEBC2E;display:inline-block"></span><span style="width:11px;height:11px;border-radius:50%;background:#28C840;display:inline-block"></span><div style="flex:1;background:#161b22;border:1px solid rgba(255,255,255,.07);border-radius:6px;padding:5px 12px;font-size:11px;font-family:monospace;color:#64748b">🔒 www.{slug}.com.uy</div></div><div style="height:420px;overflow:hidden">[MOCKUP 1]</div></div>

S5-PROPUESTA 2 [estilo diferente]: Mismo frame. Paleta+tipografía+layout completamente distintos.

S6-PROPUESTA 3 [estilo más diferente]: Tercer frame. Máximo contraste con S4 y S5.

S7-STATS: 3 cards con números grandes (% verosímiles para {rubro}). Título "¿Por qué una web hoy?".

S8-QUÉ INCLUYE: Grid 2col con 6 ítems (ícono FA + texto), deliverables específicos para {rubro}.

S9-INVERSIÓN: Card central con rango USD. CTA agendar. Badge "Sin compromiso".

S10-PRÓXIMOS PASOS: "¿Avanzamos, {lead_name}?" Botón WA verde (href="https://wa.me/59899000000"). hola@scalerics.com. Tagline "Scalerics — Tu negocio, online."

MOCKUPS (S4-S6) — CRÍTICO:
- Se ven como diseños REALES, NO wireframes
- Cada uno: navbar (logo+links+CTA btn) + hero (gradiente fuerte, H1 grande, subtítulo, btn CTA) + grid 3 productos (emoji 70px, nombre real, precio $UY coherente, btn)
- Los 3 estilos MUY diferentes: bold/colorido · clean/blanco · premium/oscuro-serif
- Nombres de productos reales del rubro (bicicletería→"Trek Marlin 7 $45.990", restaurante→"Pasta al pesto $590", etc.)

PREGUNTAS: al final del body exactamente:
<div id="essential-questions" style="display:none"><ol>[8 preguntas específicas para {rubro}: logo existente, productos principales, fotos, etc.]</ol></div>

Generá SOLO <!DOCTYPE html>..., sin markdown ni explicaciones."""


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
