"""Generate and deploy sales demo presentations using Claude AI + Vercel."""

import base64
import json
import os
import re
import time
from pathlib import Path

import anthropic
import requests

LOGO_PATH = Path(r"C:\Users\juant\OneDrive\Desktop\Scalerics\Assets\logo_full.png")


def _logo_data_uri() -> str:
    try:
        with open(LOGO_PATH, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"data:image/png;base64,{b64}"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Claude only generates slide CONTENT — ~4K tokens, never truncates
# ---------------------------------------------------------------------------

def _content_prompt(business_name, rubro, city, client_color, lead_name, messages):
    conv_lines = []
    for m in messages[:30]:
        direction = "Cliente" if m.get("direction") in ("in", "inbound") else "Bot"
        conv_lines.append(f"[{direction}]: {str(m.get('content',''))[:150]}")
    conv = "\n".join(conv_lines) or "(sin conversación)"

    color_hint = (f"Color primario del negocio: {client_color}." if client_color
                  else f"Elegí colores simbólicos para '{rubro}'.")
    slug = re.sub(r"[^a-z0-9]", "", business_name.lower())[:20] or "negocio"

    return f"""Generá el contenido de 8 slides para una presentación de ventas de Scalerics para "{business_name}" ({rubro}, {city}).

LEAD: {lead_name} | {color_hint}
WHATSAPP (personalizá con esto): {conv}

⚠️ REGLA CRÍTICA DE JSON: En TODO el HTML que escribas dentro del JSON, usá ÚNICAMENTE comillas simples (') para atributos HTML — style='...', class='...', href='...'. NUNCA uses comillas dobles (") dentro de los valores del JSON porque rompe el parser. Solo se permiten comillas dobles para delimitar claves y valores del propio JSON.

Respondé SOLO con un JSON válido con esta estructura exacta — sin markdown, sin explicaciones:

{{
  "accent": "#HEXCOLOR",
  "s1": "<contenido HTML del slide 1>",
  "s2": "<contenido HTML del slide 2>",
  "s3": "<contenido HTML del slide 3>",
  "s4_label": "Nombre estilo bold/energético",
  "s4": "<mockup HTML 1>",
  "s5_label": "Nombre estilo clean/moderno",
  "s5": "<mockup HTML 2>",
  "s6_label": "Nombre estilo premium/elegante",
  "s6": "<mockup HTML 3>",
  "s7": "<contenido HTML del slide 7>",
  "s8": "<contenido HTML del slide 8>",
  "questions": ["pregunta 1", "pregunta 2", "pregunta 3", "pregunta 4", "pregunta 5", "pregunta 6"]
}}

INSTRUCCIONES POR SLIDE:

s1-PORTADA: H1 grande "Tu web, {business_name}", subtítulo personalizado 1 línea, 3 bullets con emoji relevante para {rubro}. Sin nav.

s2-PROBLEMA: Título impactante (sin web estás perdiendo clientes). 3 cards con problema CONCRETO de {rubro} (texto corto, directo). Cada card: emoji grande + título + 1 frase.

s3-SOLUCIÓN: Título "Lo que hacemos". 4 filas: ✓ + entregable específico para {rubro}. Abajo: 2 stats en números grandes relevantes para {rubro}.

s4/s5/s6-MOCKUPS: Cada uno es un mini-sitio completo dentro de un browser frame con estructura FIJA:
- Navbar: nombre negocio bold izq + btn CTA der (colores del estilo)
- Hero: fondo gradiente, H1 impactante, subtítulo 1 línea, btn CTA
- Grid 3 productos: emoji 60px + nombre real + precio $UY + btn "Ver más"
ESTILOS MUY DIFERENTES: s4=bold+colorido, s5=clean+blanco/claro, s6=dark+elegante
Productos con nombres y precios REALES para {rubro}.
Usá el browser frame EXACTO (no lo modifiques):
<div style="width:100%;max-width:780px;border-radius:12px;overflow:hidden;box-shadow:0 24px 70px rgba(0,0,0,.8),0 0 0 1px rgba(255,255,255,.07)"><div style="background:#0d1117;padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid rgba(255,255,255,.06)"><span style="width:10px;height:10px;border-radius:50%;background:#FF5F57;display:inline-block"></span><span style="width:10px;height:10px;border-radius:50%;background:#FEBC2E;display:inline-block"></span><span style="width:10px;height:10px;border-radius:50%;background:#28C840;display:inline-block"></span><span style="flex:1;background:#161b22;border:1px solid rgba(255,255,255,.08);border-radius:5px;padding:3px 10px;font-size:11px;font-family:monospace;color:#64748b">🔒 www.{slug}.com.uy</span></div><div style="height:390px;overflow:hidden">...CONTENIDO...</div></div>

s7-INVERSIÓN: Título "¿Cuánto cuesta?". Card central con rango USD sin ser exacto + badge "Sin compromiso". 4 deliverables en 2 columnas (emoji + texto, específicos para {rubro}).

s8-PRÓXIMOS PASOS: "¿Arrancamos, {lead_name}?" grande y centrado. Btn WA verde (href="https://wa.me/59899000000"). Texto: hola@scalerics.com. Tagline: "Scalerics — Tu negocio, online."

"accent": color hex que mejor representa el rubro (vibrante, no negro ni blanco).
"questions": 6 preguntas concretas que necesitás hacerle al cliente si acepta (logo?, productos principales?, fotos?, dominio?, redes?, etc.)."""


def _chat_prompt(business_name, rubro, city, client_color, lead_name, messages):
    """Prompt for Claude.ai chat (asks for full deployable HTML, no API wrapper needed)."""
    conv_lines = []
    for m in messages[:20]:
        direction = "Cliente" if m.get("direction") in ("in", "inbound") else "Bot"
        conv_lines.append(f"[{direction}]: {str(m.get('content',''))[:120]}")
    conv = "\n".join(conv_lines) or "(sin conversación previa)"
    color_hint = f"Color principal del negocio: {client_color}." if client_color else f"Elegí colores que representen bien a {rubro}."
    slug = re.sub(r"[^a-z0-9]", "", business_name.lower())[:20] or "negocio"

    return f"""Sos un desarrollador web senior de Scalerics, agencia uruguaya. Creá una presentación de ventas HTML completa para el cliente "{business_name}" ({rubro}, {city}).

Lead: {lead_name} | {color_hint}
Conversación WhatsApp (usala para personalizar):
{conv}

━━━ TÉCNICO ━━━
• HTML completo auto-contenido (<!DOCTYPE html>…</html>), listo para abrir en browser
• Google Fonts: DM Sans 300/400/600/700/800 + Cormorant Garamond 400/600
• Font Awesome 6.5 via CDN
• 8 slides: position:absolute, inset:0, opacity+translateX transition .5s
• Slide activa: opacity:1, translateX(0) | Inactiva: opacity:0, translateX(60px) | Saliente: translateX(-60px)
• Barra progreso fija top:0, height:3px, gradiente #06B6D4→#3b82f6, z-index:100
• Navegación fija bottom:28px centrada: btn prev (←) + dots + btn next (→) + teclas ArrowLeft/ArrowRight
• Contador fijo top:16px right:20px — "N / 8"
• Fondo global: #0F1419 | Texto: #e2e8f0

━━━ SLIDES ━━━

[1] PORTADA
H1 grande: "Tu web, {business_name}" | Subtítulo personalizado 1 línea | 3 bullets con emoji específicos para {rubro}

[2] PROBLEMA — título impactante
3 cards horizontales: cada una = emoji grande + título corto + 1 frase. Problemas MUY concretos de {rubro} sin presencia web (pérdida real de clientes, competencia, etc.)

[3] SOLUCIÓN — "Lo que hacemos"
4 filas: ✓ + entregable específico para {rubro}. Abajo: 2 stats en número grande relevantes (ej: "73% de los uruguayos busca negocios online antes de ir")

[4][5][6] — MOCKUPS (3 estilos distintos, mismo browser frame)

Browser frame EXACTO para los 3:
<div style="width:100%;max-width:780px;border-radius:12px;overflow:hidden;box-shadow:0 24px 70px rgba(0,0,0,.8)">
  <div style="background:#0d1117;padding:9px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid rgba(255,255,255,.06)">
    <span style="width:10px;height:10px;border-radius:50%;background:#FF5F57;display:inline-block"></span>
    <span style="width:10px;height:10px;border-radius:50%;background:#FEBC2E;display:inline-block"></span>
    <span style="width:10px;height:10px;border-radius:50%;background:#28C840;display:inline-block"></span>
    <span style="flex:1;background:#161b22;border:1px solid rgba(255,255,255,.08);border-radius:5px;padding:3px 10px;font-size:11px;font-family:monospace;color:#64748b">🔒 www.{slug}.com.uy</span>
  </div>
  <div style="height:390px;overflow:hidden">
    <!-- CONTENIDO DEL MINI-SITIO ACÁ -->
  </div>
</div>

ESTRUCTURA INTERNA de cada mockup — adaptala según {rubro}:
• Navbar: nombre negocio izq + menú items relevantes para {rubro} + btn CTA der
• Hero: fondo con color/gradiente, H1 impactante, subtítulo, btn CTA principal
• Sección principal adaptada al rubro:
  - Restaurante/bar/panadería → menú con platos reales + precios $UY + "Pedir ahora"
  - Ropa/calzado/accesorios → grid productos con foto-placeholder + nombre + precio + talle
  - Clínica/odontología/salud → servicios con íconos médicos + turnos online + equipo
  - Estetica/spa/peluquería → servicios con precios + galería before/after + reserva
  - Taller/mecánica/servicio técnico → servicios + "Solicitar presupuesto" + horarios
  - Educación/academia → cursos + niveles + "Inscribirse" + próximas fechas
  - Inmobiliaria → propiedades destacadas + filtros + "Ver más"
  - Veterinaria → servicios + turnos + productos para mascotas
  - Cualquier otro → lo que tenga más sentido para ese rubro específico

ESTILOS DIFERENTES (los 3 deben verse claramente distintos):
[4] BOLD/COLORIDO — colores intensos saturados, tipografía grande y pesada, gradientes llamativos
[5] CLEAN/PROFESIONAL — fondo blanco/gris muy claro, tipografía ligera, mucho espacio, minimalista
[6] DARK/PREMIUM — fondo #0a0a0a o #0d0d1a, detalles dorados o neón, elegante y exclusivo

[7] INVERSIÓN
Card central: rango "USD 400–800" aprox + badge "Sin compromiso". 4 deliverables en 2 columnas con emoji, específicos para {rubro}.

[8] PRÓXIMOS PASOS
"¿Arrancamos, {lead_name}?" — texto grande centrado
Botón WhatsApp verde: href="https://wa.me/59899000000"
Email: hola@scalerics.com | Tagline: "Scalerics — Tu negocio, online."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Respondé ÚNICAMENTE con el HTML completo. Sin explicaciones. Sin markdown. Sin bloques de código. Empezá directamente con <!DOCTYPE html> y terminá con </html>."""

# ---------------------------------------------------------------------------
# Fixed HTML shell — navigation, CSS, progress bar all pre-written
# ---------------------------------------------------------------------------

_HTML_SHELL = """\
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{business_name} — Propuesta Scalerics</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700;800&family=Cormorant+Garamond:ital,wght@0,400;0,600;1,400&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'DM Sans',sans-serif;background:#0F1419;color:#e2e8f0;overflow:hidden;height:100vh;width:100vw}}
#progress{{position:fixed;top:0;left:0;height:3px;background:linear-gradient(90deg,#06B6D4,#3b82f6);transition:width .4s ease;z-index:100}}
#slides{{position:relative;width:100%;height:100vh}}
.slide{{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:40px 60px;opacity:0;pointer-events:none;transition:opacity .5s ease,transform .5s ease;transform:translateX(60px)}}
.slide.active{{opacity:1;pointer-events:auto;transform:translateX(0)}}
.slide.prev{{transform:translateX(-60px)}}
#nav{{position:fixed;bottom:28px;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:16px;z-index:100}}
.nav-btn{{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);color:#e2e8f0;width:40px;height:40px;border-radius:50%;cursor:pointer;font-size:1rem;display:flex;align-items:center;justify-content:center;transition:background .2s}}
.nav-btn:hover{{background:rgba(255,255,255,.18)}}
#dots{{display:flex;gap:8px;align-items:center}}
.dot{{width:7px;height:7px;border-radius:99px;background:rgba(255,255,255,.25);cursor:pointer;transition:all .3s ease}}
.dot.active{{width:22px;background:#06B6D4}}
#slide-counter{{position:fixed;top:16px;right:20px;font-size:.72rem;color:#475569;z-index:100}}
.badge-red{{background:#7f1d1d;color:#fca5a5;border:1px solid #991b1b;padding:4px 12px;border-radius:99px;font-size:.7rem;font-weight:700;letter-spacing:.06em;display:inline-block;margin-bottom:18px}}
.badge-cyan{{background:rgba(6,182,212,.15);color:#06B6D4;border:1px solid rgba(6,182,212,.3);padding:4px 12px;border-radius:99px;font-size:.7rem;font-weight:700;letter-spacing:.06em;display:inline-block;margin-bottom:18px}}
.glass-card{{background:rgba(255,255,255,.04);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.07);border-radius:1.1rem;padding:24px}}
</style>
</head>
<body>
<div id="progress"></div>
<div id="slide-counter"></div>
<div id="slides">
<div class="slide active" id="slide-0">{s1}</div>
<div class="slide" id="slide-1">{s2}</div>
<div class="slide" id="slide-2">{s3}</div>
<div class="slide" id="slide-3">
  <p style="font-style:italic;color:#94a3b8;margin-bottom:18px;font-size:.95rem">{s4_label}</p>
  {s4}
</div>
<div class="slide" id="slide-4">
  <p style="font-style:italic;color:#94a3b8;margin-bottom:18px;font-size:.95rem">{s5_label}</p>
  {s5}
</div>
<div class="slide" id="slide-5">
  <p style="font-style:italic;color:#94a3b8;margin-bottom:18px;font-size:.95rem">{s6_label}</p>
  {s6}
</div>
<div class="slide" id="slide-6">{s7}</div>
<div class="slide" id="slide-7">{s8}</div>
</div>
<nav id="nav">
  <button class="nav-btn" id="btn-prev" onclick="move(-1)"><i class="fa fa-chevron-left"></i></button>
  <div id="dots"></div>
  <button class="nav-btn" id="btn-next" onclick="move(1)"><i class="fa fa-chevron-right"></i></button>
</nav>
<div id="essential-questions" style="display:none"><ol>{questions_html}</ol></div>
<script>
const TOTAL=8;let cur=0;
const slides=document.querySelectorAll('.slide');
const dotsEl=document.getElementById('dots');
const prog=document.getElementById('progress');
const counter=document.getElementById('slide-counter');
for(let i=0;i<TOTAL;i++){{const d=document.createElement('div');d.className='dot'+(i===0?' active':'');d.onclick=()=>go(i);dotsEl.appendChild(d);}}
function go(n){{
  slides[cur].classList.remove('active');slides[cur].classList.add('prev');
  setTimeout(()=>slides[cur].classList.remove('prev'),500);
  cur=Math.max(0,Math.min(TOTAL-1,n));
  slides[cur].classList.add('active');
  document.querySelectorAll('.dot').forEach((d,i)=>d.classList.toggle('active',i===cur));
  prog.style.width=((cur+1)/TOTAL*100)+'%';
  counter.textContent=(cur+1)+' / '+TOTAL;
}}
function move(d){{go(cur+d);}}
document.addEventListener('keydown',e=>{{if(e.key==='ArrowRight'||e.key==='ArrowDown')move(1);if(e.key==='ArrowLeft'||e.key==='ArrowUp')move(-1);}});
go(0);
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_and_deploy(
    phone: str,
    business_name: str,
    rubro: str,
    city: str,
    client_color: str,
    lead_name: str,
    messages: list,
) -> dict:
    """Generate demo HTML with Claude (content only), wrap in shell, deploy to Vercel."""

    prompt = _content_prompt(business_name, rubro, city, client_color, lead_name, messages)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\s*\n?", "", raw)
    raw = re.sub(r"\n?```\s*$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        try:
            from json_repair import repair_json
            data = json.loads(repair_json(raw))
        except Exception:
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                try:
                    from json_repair import repair_json
                    data = json.loads(repair_json(m.group()))
                except Exception:
                    raise Exception(f"Claude no devolvió JSON válido: {e}\n\nRaw: {raw[:500]}")
            else:
                raise Exception(f"Claude no devolvió JSON válido: {e}\n\nRaw: {raw[:500]}")

    questions_html = "".join(f"<li>{q}</li>" for q in data.get("questions", []))

    html = _HTML_SHELL.format(
        business_name=business_name,
        s1=data.get("s1", ""),
        s2=data.get("s2", ""),
        s3=data.get("s3", ""),
        s4_label=data.get("s4_label", "Propuesta 1"),
        s4=data.get("s4", ""),
        s5_label=data.get("s5_label", "Propuesta 2"),
        s5=data.get("s5", ""),
        s6_label=data.get("s6_label", "Propuesta 3"),
        s6=data.get("s6", ""),
        s7=data.get("s7", ""),
        s8=data.get("s8", ""),
        questions_html=questions_html,
    )

    # Inject Scalerics logo into slide 1
    logo_uri = _logo_data_uri()
    if logo_uri:
        logo_tag = f'<img src="{logo_uri}" style="height:34px;object-fit:contain;margin-bottom:24px;display:block">'
        html = html.replace('<div class="slide active" id="slide-0">',
                            f'<div class="slide active" id="slide-0">{logo_tag}', 1)

    url = _deploy_to_vercel(html, business_name)
    return {"url": url, "questions": data.get("questions", [])}


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
        err = data.get("error", {})
        if err.get("invalidToken") or err.get("code") == "forbidden":
            raise Exception(
                "Token de Vercel inválido o expirado. "
                "Generá uno nuevo en vercel.com/account/tokens y actualizalo "
                "en las variables de entorno de Railway (VERCEL_TOKEN)."
            )
        raise Exception(f"Vercel deploy failed: {data}")

    return f"https://{data['url']}"
