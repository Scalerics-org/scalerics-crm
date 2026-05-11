"""Generate and deploy sales demo presentations using Claude AI + Vercel."""

import base64
import json
import os
import re
import time
from pathlib import Path

import anthropic
import requests

LOGO_PATH = Path(os.environ.get(
    "SCALERICS_LOGO_PATH",
    str(Path(__file__).parent / "static" / "logo.png"),
))


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

def _content_prompt(business_name, rubro, city, client_color, lead_name, messages, phone=""):
    conv_lines = []
    for m in messages[:30]:
        direction = "Cliente" if m.get("direction") in ("in", "inbound") else "Bot"
        conv_lines.append(f"[{direction}]: {str(m.get('content',''))[:150]}")
    conv = "\n".join(conv_lines) or "(sin conversación)"

    color_hint = (f"Color primario del negocio: {client_color}." if client_color
                  else f"Elegí colores simbólicos para '{rubro}'.")
    slug = re.sub(r"[^a-z0-9]", "", business_name.lower())[:20] or "negocio"
    wa_number = re.sub(r"[^0-9]", "", phone) or "59899000000"

    return f"""Generá el contenido de 8 slides para una presentación de ventas de Scalerics para "{business_name}" ({rubro}, {city}).

LEAD: {lead_name} | {color_hint}
WHATSAPP (personalizá con esto): {conv}

⚠️ REGLAS CRÍTICAS:
1. JSON: usá SOLO comillas simples (') para atributos HTML dentro del JSON. Nunca comillas dobles dentro de los valores.
2. CSS: NUNCA uses llaves ({{ }}) dentro del HTML de los slides — todo debe ser inline styles (style='...'). Las llaves rompen el parser.
3. Sé CONCISO en cada slide: el HTML debe ser simple y directo, sin estilos repetitivos ni clases CSS internas.

Respondé SOLO con JSON válido, sin markdown ni explicaciones:

{{"s1":"<html slide 1>","s2":"<html slide 2>","s3":"<html slide 3>","s4_label":"Nombre estilo 1","s4":"<mockup 1>","s5_label":"Nombre estilo 2","s5":"<mockup 2>","s6_label":"Nombre estilo 3","s6":"<mockup 3>","s7":"<html slide 7>","s8":"<html slide 8>","questions":["q1","q2","q3","q4","q5","q6"]}}

COLORES — MUY IMPORTANTE:
- s1, s2, s3, s7, s8 → usá SOLO colores de Scalerics: fondo #0F1419, texto #e2e8f0, acento #06B6D4 (cyan) o #10b981 (verde), títulos con gradiente text de cyan a azul. NUNCA el color del cliente en estos slides.
- s4, s5, s6 (mockups) → usá el color del cliente ({color_hint}) para el diseño del mini-sitio.

INSTRUCCIONES POR SLIDE (sé breve, máximo 8 líneas de HTML por slide):

s1-PORTADA: H1 grande "Tu web, {business_name}" con gradiente cyan→azul. Subtítulo 1 línea personalizado. 3 bullets con emoji para {rubro}.

s2-PROBLEMA: Título impactante en #06B6D4. 3 cards horizontales con fondo rgba(255,255,255,.05) y borde rgba(255,255,255,.08): emoji + título + 1 frase concreta de problema de {rubro} sin web.

s3-SOLUCIÓN: Título "Lo que hacemos" en blanco. 4 filas con ✓ verde + entregable para {rubro}. 2 stats grandes (número + descripción) en cyan.

s4/s5/s6-MOCKUPS — browser frame exacto:
<div style='width:100%;max-width:780px;border-radius:12px;overflow:hidden;box-shadow:0 24px 70px rgba(0,0,0,.8)'><div style='background:#0d1117;padding:9px 14px;display:flex;align-items:center;gap:8px'><span style='width:10px;height:10px;border-radius:50%;background:#FF5F57;display:inline-block'></span><span style='width:10px;height:10px;border-radius:50%;background:#FEBC2E;display:inline-block'></span><span style='width:10px;height:10px;border-radius:50%;background:#28C840;display:inline-block'></span><span style='flex:1;background:#161b22;border-radius:5px;padding:3px 10px;font-size:11px;font-family:monospace;color:#64748b'>🔒 www.{slug}.com.uy</span></div><div style='height:390px;overflow:hidden'>CONTENIDO</div></div>

Dentro de CONTENIDO: navbar (nombre izq + CTA der) + hero (gradiente, H1, btn) + grid 3 items reales para {rubro} con precio en $UY.
Elegí 3 estilos apropiados para {rubro} (ej: para comida NO usar dark/premium; para joyería SÍ).

s7-INVERSIÓN: Título "¿Cuánto cuesta?" en blanco. Card central con rango USD + badge "Sin compromiso" en cyan. 4 deliverables en 2 columnas para {rubro}.

s8-PRÓXIMOS PASOS: "¿Arrancamos, {lead_name}?" grande, gradiente cyan→verde. Btn WA verde (href='https://wa.me/{wa_number}'). hola@scalerics.com. "Scalerics — Tu negocio, online."

"questions": 6 preguntas para hacerle al cliente si acepta (logo, productos, fotos, dominio, redes, etc.)."""


def _chat_prompt(business_name, rubro, city, client_color, lead_name, messages, phone=""):
    """Prompt for Claude.ai chat (asks for full deployable HTML, no API wrapper needed)."""
    conv_lines = []
    for m in messages[:20]:
        direction = "Cliente" if m.get("direction") in ("in", "inbound") else "Bot"
        conv_lines.append(f"[{direction}]: {str(m.get('content',''))[:120]}")
    conv = "\n".join(conv_lines) or "(sin conversación previa)"
    color_hint = f"Color principal del negocio: {client_color}." if client_color else f"Elegí colores que representen bien a {rubro}."
    slug = re.sub(r"[^a-z0-9]", "", business_name.lower())[:20] or "negocio"
    wa_number = re.sub(r"[^0-9]", "", phone) or "59899000000"

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

ESTILOS — elegí vos 3 estilos que tengan sentido real para "{rubro}". No uses siempre los mismos. Pensá qué espera ver un cliente de ese rubro, qué le genera confianza, qué está bien en ese mercado. Ejemplos de cómo razonar:
• Puesto de comida / rotisería → "Vibrante y popular" + "Rápido y moderno" + "Cálido y familiar" (no dark/premium, no tiene sentido)
• Joyería / relojería → "Elegante dorado" + "Minimalista blanco" + "Oscuro lujoso" (acá sí tiene sentido el premium)
• Clínica / médico → "Limpio y confiable" + "Moderno azul" + "Cálido y cercano" (nada demasiado oscuro o agresivo)
• Kinder / academia infantil → "Alegre y colorido" + "Ordenado y profesional" + "Fresco pastel" (nada oscuro)
• Estudio contable / abogados → "Serio y confiable" + "Moderno gris" + "Clásico premium"
• Gimnasio / CrossFit → "Energético rojo/negro" + "Moderno minimalista" + "Motivacional gradiente"
• Peluquería / estética → "Trendy y moderno" + "Minimalista chic" + depende si es barrio o premium

Para cada estilo poné una etiqueta descriptiva de 3-4 palabras como título del slide (ej: "Moderno y vibrante", "Clásico y confiable") — nada genérico como "Opción 1".

[7] INVERSIÓN
Card central: rango "USD 400–800" aprox + badge "Sin compromiso". 4 deliverables en 2 columnas con emoji, específicos para {rubro}.

[8] PRÓXIMOS PASOS
"¿Arrancamos, {lead_name}?" — texto grande centrado
Botón WhatsApp verde: href="https://wa.me/{wa_number}"
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

    prompt = _content_prompt(business_name, rubro, city, client_color, lead_name, messages, phone)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.beta.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        betas=["output-128k-2025-02-19"],
        messages=[{"role": "user", "content": prompt}],
    )

    if msg.stop_reason == "max_tokens":
        print(f"[demo_ai] WARNING: Claude hit max_tokens — response truncated!")

    raw = msg.content[0].text.strip()
    print(f"[demo_ai] Raw response length: {len(raw)} chars, stop_reason: {msg.stop_reason}")
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

    present_keys = [k for k in ["s1","s2","s3","s4","s5","s6","s7","s8"] if data.get(k)]
    print(f"[demo_ai] Slides received: {present_keys}")

    def _s(key, fallback=""):
        # Escape { } so Python .format() doesn't misinterpret CSS/JS braces in Claude's HTML
        return data.get(key, fallback).replace("{", "{{").replace("}", "}}")

    questions_html = "".join(f"<li>{q}</li>" for q in data.get("questions", []))

    html = _HTML_SHELL.format(
        business_name=business_name,
        s1=_s("s1"),
        s2=_s("s2"),
        s3=_s("s3"),
        s4_label=data.get("s4_label", "Propuesta 1"),
        s4=_s("s4"),
        s5_label=data.get("s5_label", "Propuesta 2"),
        s5=_s("s5"),
        s6_label=data.get("s6_label", "Propuesta 3"),
        s6=_s("s6"),
        s7=_s("s7"),
        s8=_s("s8"),
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
