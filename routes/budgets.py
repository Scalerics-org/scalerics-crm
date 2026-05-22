"""Budget generation and management routes."""

import datetime
import json
import logging
import os
from typing import Optional

from flask import Blueprint, current_app, jsonify, request

from database import (
    create_budget,
    get_budget_for_client,
    get_business,
    get_client_info,
    get_meetings_for_client,
    update_budget,
)

budgets_bp = Blueprint("budgets", __name__)
logger = logging.getLogger(__name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


def _he(s):
    import html
    return html.escape(str(s or ""))


def _clean_json(raw: str) -> str:
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rstrip("`").strip()
    return raw


def _build_budget_prompt(client: dict, service_type: str, budget_range: str, needs: str, requirements: str) -> str:
    return f"""Generá un presupuesto profesional para un proyecto de {service_type} para el negocio "{client.get('name', '')}".

Información del cliente:
- Negocio: {client.get('name', '')} ({client.get('category', '')})
- Ciudad: {client.get('city', 'Montevideo')}, Uruguay
- Tamaño del equipo / necesidades: {needs}
- Rango de presupuesto indicado: {budget_range}
- Requerimientos del proyecto:
{requirements}

Devolvé SOLO un JSON con este formato exacto (sin texto extra, sin markdown):
{{
  "hero_title": "Título descriptivo del proyecto (ej: Sitio web profesional para Restaurante X)",
  "hero_description": "Descripción de 2-3 oraciones del objetivo del proyecto y qué problema resuelve.",
  "intro": "1-2 oraciones sobre la situación actual del negocio y por qué necesitan este proyecto.",
  "sections": [
    {{
      "title": "Objetivos del proyecto",
      "items": ["objetivo 1", "objetivo 2", "objetivo 3"]
    }},
    {{
      "title": "Alcance del desarrollo",
      "subsections": [
        {{
          "title": "1. Nombre del módulo",
          "items": ["funcionalidad 1", "funcionalidad 2"]
        }},
        {{
          "title": "2. Siguiente módulo",
          "items": ["funcionalidad 1", "funcionalidad 2"]
        }}
      ]
    }},
    {{
      "title": "Tiempo estimado de desarrollo",
      "text": "Entre X y Y semanas."
    }}
  ],
  "dev_price": 1500,
  "monthly_price": 50,
  "payment_terms": "50% adelanto, 50% al entregar",
  "dev_checklist": ["Desarrollo completo del sistema", "Implementación y capacitación"],
  "monthly_checklist": ["Hosting incluido", "Soporte técnico", "Mantenimiento correctivo básico"],
  "notes": [
    "Incluye 2 rondas de revisiones.",
    "Hosting y dominio no incluidos en dev_price (cubiertos por mantenimiento mensual)."
  ]
}}

Precios en USD, realistas para el mercado uruguayo.
- dev_price: precio único de desarrollo (entre $800 y $4000 según complejidad)
- monthly_price: mantenimiento mensual (entre $30 y $100; usá 0 si no aplica hosting)
- Mínimo 2 secciones en "sections", mínimo 2 subsecciones en la sección de alcance.
- Las notas deben ser observaciones concretas para el cliente."""


def _generate_budget_internal(
    db_path: str,
    client_id: int,
    requirements: str = "",
    service_type: str = "",
) -> Optional[dict]:
    """Generate and persist a budget via Claude. Returns budget_data or None if budget exists/error."""
    import anthropic

    if get_budget_for_client(db_path, client_id):
        return None  # don't overwrite existing budget

    client = get_business(db_path, client_id)
    if not client:
        return None

    client_info = get_client_info(db_path, client_id) or {}
    meetings = get_meetings_for_client(db_path, client_id)
    meeting_reqs = "\n".join(m["requirements"] for m in meetings if m.get("requirements"))
    all_reqs = "\n".join(filter(None, [requirements, meeting_reqs])) or "sitio web profesional con diseño moderno"

    svc = service_type or client_info.get("rubro") or client.get("category", "desarrollo web")
    budget_range = client_info.get("budget_range") or "no especificado"
    needs = client_info.get("needs") or "no especificado"

    prompt = _build_budget_prompt(client, svc, budget_range, needs, all_reqs)

    try:
        ai = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        msg = ai.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        budget_data = json.loads(_clean_json(msg.content[0].text.strip()))
    except Exception as e:
        logger.warning(f"Budget AI generation failed for client {client_id}: {e}")
        return None

    sections_json = json.dumps(budget_data.get("sections", []), ensure_ascii=False)
    try:
        dev_price = float(budget_data.get("dev_price") or 0)
    except (TypeError, ValueError):
        dev_price = 0.0
    meta = {k: v for k, v in budget_data.items() if k != "sections"}
    notes_json = json.dumps(meta, ensure_ascii=False)

    create_budget(db_path, client_id, items=sections_json, total_amount=dev_price, notes=notes_json)
    return budget_data


@budgets_bp.route("/api/leads/<int:client_id>/budget", methods=["GET"])
def api_get_budget(client_id):
    budget = get_budget_for_client(_db(), client_id)
    if not budget:
        return jsonify(None), 200
    if budget.get("items") and isinstance(budget["items"], str):
        try:
            budget["items"] = json.loads(budget["items"])
        except Exception as e:
            logger.error("Error leyendo items del presupuesto %s: %s", budget.get("id"), e, exc_info=True)
            return jsonify({"ok": False, "error": "Error leyendo datos del presupuesto. Intentá de nuevo."}), 400
    if budget.get("notes") and isinstance(budget["notes"], str):
        try:
            budget["notes"] = json.loads(budget["notes"])
        except Exception as e:
            logger.error("Error leyendo notas del presupuesto %s: %s", budget.get("id"), e, exc_info=True)
            return jsonify({"ok": False, "error": "Error leyendo datos del presupuesto. Intentá de nuevo."}), 400
    return jsonify(budget)


@budgets_bp.route("/api/leads/<int:client_id>/budget/generate", methods=["POST"])
def api_generate_budget(client_id):
    import anthropic

    data = request.get_json() or {}
    client = get_business(_db(), client_id)
    if not client:
        return jsonify({"ok": False, "error": "Lead no encontrado"}), 404

    client_info = get_client_info(_db(), client_id) or {}
    meetings = get_meetings_for_client(_db(), client_id)

    extra_req = (data.get("requirements") or "").strip()
    meeting_reqs = "\n".join(m["requirements"] for m in meetings if m.get("requirements"))
    requirements = "\n".join(filter(None, [extra_req, meeting_reqs])) or "sitio web profesional con diseño moderno"

    service_type = client_info.get("rubro") or data.get("service_type") or client.get("category", "desarrollo web")
    budget_range = client_info.get("budget_range") or data.get("budget_range") or "no especificado"
    needs = client_info.get("needs") or "no especificado"

    prompt = _build_budget_prompt(client, service_type, budget_range, needs, requirements)

    try:
        ai = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        msg = ai.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        budget_data = json.loads(_clean_json(msg.content[0].text.strip()))
    except Exception as e:
        logger.error("Error generando presupuesto cliente %s: %s", client_id, e, exc_info=True)
        return jsonify({"ok": False, "error": "Error generando presupuesto. Intentá de nuevo."}), 500

    sections_json = json.dumps(budget_data.get("sections", []), ensure_ascii=False)
    try:
        dev_price = float(budget_data.get("dev_price") or 0)
    except (TypeError, ValueError):
        dev_price = 0.0
    meta = {k: v for k, v in budget_data.items() if k != "sections"}
    notes_json = json.dumps(meta, ensure_ascii=False)

    existing = get_budget_for_client(_db(), client_id)
    if existing:
        update_budget(_db(), existing["id"], items=sections_json, total_amount=dev_price, notes=notes_json, status="draft")
        budget_id = existing["id"]
    else:
        budget_id = create_budget(_db(), client_id, items=sections_json, total_amount=dev_price, notes=notes_json)

    return jsonify({"ok": True, "budget_id": budget_id, "data": budget_data})


@budgets_bp.route("/api/budgets/<int:budget_id>", methods=["PUT"])
def api_update_budget(budget_id):
    data = request.get_json() or {}
    fields: dict = {}
    if "items" in data:
        fields["items"] = json.dumps(data["items"], ensure_ascii=False) if not isinstance(data["items"], str) else data["items"]
    if "total_amount" in data:
        fields["total_amount"] = float(data["total_amount"])
    if "notes" in data:
        fields["notes"] = json.dumps(data["notes"], ensure_ascii=False) if isinstance(data["notes"], dict) else data["notes"]
    if "status" in data:
        fields["status"] = data["status"]
    if fields:
        update_budget(_db(), budget_id, **fields)
    return jsonify({"ok": True})


@budgets_bp.route("/api/budgets/<int:budget_id>/mark-sent", methods=["POST"])
def api_mark_budget_sent(budget_id):
    update_budget(_db(), budget_id, status="sent", sent_at=datetime.datetime.now().isoformat())
    return jsonify({"ok": True})


@budgets_bp.route("/api/leads/<int:client_id>/budget/preview")
def api_budget_preview(client_id):
    client = get_business(_db(), client_id)
    if not client:
        return "Lead no encontrado", 404
    budget = get_budget_for_client(_db(), client_id)
    if not budget:
        return "Sin presupuesto generado para este lead.", 404

    sections = []
    if budget.get("items"):
        try:
            sections = json.loads(budget["items"]) if isinstance(budget["items"], str) else budget["items"]
        except Exception:
            sections = []

    meta = {}
    if budget.get("notes"):
        try:
            meta = json.loads(budget["notes"]) if isinstance(budget["notes"], str) else budget["notes"]
        except Exception:
            pass

    hero_title = _he(meta.get("hero_title", f"Proyecto para {client.get('name', '')}"))
    hero_description = _he(meta.get("hero_description", ""))
    intro = _he(meta.get("intro", ""))
    dev_price = budget.get("total_amount") or meta.get("dev_price") or 0
    monthly_price = meta.get("monthly_price") or 0
    payment_terms = _he(meta.get("payment_terms", "50% adelanto, 50% al entregar"))
    dev_checklist = meta.get("dev_checklist", ["Desarrollo completo del sistema", "Implementación y capacitación"])
    monthly_checklist = meta.get("monthly_checklist", ["Hosting incluido", "Soporte técnico", "Mantenimiento correctivo básico"])
    notes_list = meta.get("notes", [])
    if isinstance(notes_list, str):
        notes_list = [notes_list]

    today = datetime.date.today().strftime("%d/%m/%Y")
    factory_website = os.environ.get("FACTORY_WEBSITE", "www.scalerics.com")
    factory_phone = os.environ.get("FACTORY_PHONE", "")
    status_label = "Enviado" if budget.get("status") == "sent" else "Borrador"

    # ── Sections HTML ──────────────────────────────────────────────────────────
    sections_html_parts = []
    for i, sec in enumerate(sections):
        parts = []
        if i > 0:
            parts.append('<div class="divider"></div>')
        parts.append(f'<div class="section"><div class="section-title"><div class="dot"></div> {_he(sec.get("title", ""))}</div>')
        if sec.get("items"):
            parts.append('<ul class="items">')
            for it in sec["items"]:
                parts.append(f"<li>{_he(it)}</li>")
            parts.append("</ul>")
        if sec.get("text"):
            parts.append(f"<p>{_he(sec['text'])}</p>")
        for sub in sec.get("subsections", []):
            parts.append(f'<div class="subsection"><div class="subsection-title">{_he(sub.get("title", ""))}</div>')
            if sub.get("text"):
                parts.append(f"<p>{_he(sub['text'])}</p>")
            if sub.get("items"):
                parts.append('<ul class="items">')
                for it in sub["items"]:
                    parts.append(f"<li>{_he(it)}</li>")
                parts.append("</ul>")
            parts.append("</div>")
        parts.append("</div>")
        sections_html_parts.extend(parts)
    sections_html = "\n".join(sections_html_parts)

    # ── Price formatting ───────────────────────────────────────────────────────
    def _fmt(n):
        try:
            return f"{int(float(n)):,}".replace(",", ".")
        except Exception:
            return str(n)

    dev_checklist_html = "\n".join(f"<li>{_he(i)}</li>" for i in dev_checklist)
    monthly_checklist_html = "\n".join(f"<li>{_he(i)}</li>" for i in monthly_checklist)

    monthly_card = ""
    if monthly_price:
        monthly_card = (
            '<div class="plus-sign">+</div>'
            '<div class="price-card">'
            '<div class="price-label">MANTENIMIENTO MENSUAL</div>'
            f'<div class="price-amount"><sup>USD</sup> {_fmt(monthly_price)}</div>'
            '<div class="price-desc">Servicio mensual recurrente</div>'
            f'<ul class="checklist">{monthly_checklist_html}</ul>'
            "</div>"
        )

    notes_block = ""
    if notes_list:
        items_html = "\n".join(f"<li>{_he(n)}</li>" for n in notes_list)
        notes_block = (
            '<div class="notes-section">'
            "<h3>OBSERVACIONES FINALES</h3>"
            f"<ul>{items_html}</ul>"
            "</div>"
        )

    hero_desc_block = f"<p>{hero_description}</p>" if hero_description else ""
    intro_block = f'<div class="intro">{intro}</div>' if intro else ""

    css = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--navy:#0f1f3d;--navy-mid:#1a2e54;--accent:#168e0b;--accent-light:#078009;--white:#ffffff;--off-white:#f7f8fc;--text:#1c2b40;--muted:#5a6a82;--border:#dde3ec;--green:#0d9e6e}
body{font-family:'DM Sans',sans-serif;background:var(--off-white);color:var(--text);font-size:14px;line-height:1.6;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.page{max-width:820px;margin:32px auto;background:var(--white);box-shadow:0 4px 40px rgba(15,31,61,.12);border-radius:6px;overflow:hidden}
.header{background:var(--navy);padding:28px 48px;display:flex;align-items:center;justify-content:space-between}
.logo-text{font-family:'Sora',sans-serif;font-size:22px;font-weight:700;color:var(--white);letter-spacing:-.3px}
.logo-text span{color:var(--accent)}
.header-title{font-family:'Sora',sans-serif;font-size:32px;font-weight:700;color:var(--white);letter-spacing:-.5px}
.meta-row{padding:20px 48px;display:flex;gap:48px;border-bottom:1px solid var(--border)}
.meta-item label{display:block;font-size:10px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:3px}
.meta-item .value{font-family:'Sora',sans-serif;font-size:14px;font-weight:600;color:var(--text)}
.hero-block{margin:32px 48px;background:var(--navy);border-radius:8px;padding:28px 32px}
.hero-block h2{font-family:'Sora',sans-serif;font-size:18px;font-weight:700;color:var(--accent);margin-bottom:10px}
.hero-block p{font-size:15px;font-style:italic;color:var(--white);font-weight:400;line-height:1.55}
.intro{padding:0 48px 24px;color:var(--text);font-size:13.5px;line-height:1.7}
.section{padding:0 48px 24px}
.section-title{display:flex;align-items:center;gap:10px;font-family:'Sora',sans-serif;font-size:15px;font-weight:700;color:var(--text);margin-bottom:16px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--accent);flex-shrink:0}
.subsection{margin-bottom:14px;padding-left:20px;border-left:2px solid var(--border)}
.subsection-title{font-size:13px;font-weight:600;color:var(--accent);margin-bottom:6px;display:flex;align-items:center;gap:6px}
.subsection-title::before{content:'–';color:var(--accent);font-weight:700}
ul.items{list-style:none;padding-left:8px}
ul.items li{position:relative;padding-left:14px;color:var(--text);font-size:13px;margin-bottom:4px;line-height:1.55}
ul.items li::before{content:'▸';position:absolute;left:0;color:var(--accent-light);font-size:10px;top:2px}
.divider{height:1px;background:var(--border);margin:4px 48px 24px}
.pricing-section{background:var(--off-white);padding:32px 48px;display:flex;gap:24px;align-items:stretch}
.price-card{flex:1;background:var(--white);border-radius:8px;padding:24px;border:1px solid var(--border);position:relative;overflow:hidden}
.price-card::before{content:'';position:absolute;top:0;left:0;right:0;height:4px;background:var(--navy)}
.price-label{font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:8px}
.price-amount{font-family:'Sora',sans-serif;font-size:34px;font-weight:700;color:var(--navy);line-height:1;margin-bottom:6px}
.price-amount sup{font-size:16px;font-weight:600;vertical-align:super}
.price-desc{font-size:12px;color:var(--muted);margin-bottom:16px;font-weight:500}
.plus-sign{font-family:'Sora',sans-serif;font-size:28px;font-weight:700;color:var(--border);display:flex;align-items:center;padding-top:20px}
.checklist{list-style:none;padding:0}
.checklist li{display:flex;align-items:flex-start;gap:8px;font-size:12.5px;color:var(--text);margin-bottom:6px;font-weight:500}
.checklist li::before{content:'✓';color:var(--green);font-weight:700;font-size:13px;flex-shrink:0;margin-top:1px}
.notes-section{padding:24px 48px 32px;border-top:1px solid var(--border)}
.notes-section h3{font-family:'Sora',sans-serif;font-size:16px;font-weight:700;color:var(--text);margin-bottom:16px}
.notes-section ul{list-style:none;padding:0}
.notes-section ul li{position:relative;padding-left:16px;font-size:12.5px;color:var(--muted);margin-bottom:6px;line-height:1.6}
.notes-section ul li::before{content:'·';position:absolute;left:4px;font-size:18px;line-height:1.1;color:var(--accent)}
.footer{background:var(--navy);padding:16px 48px;display:flex;align-items:center;justify-content:space-between}
.footer p{color:rgba(255,255,255,.6);font-size:12px}
.no-print{max-width:820px;margin:20px auto 0;display:flex;gap:10px;justify-content:flex-end}
.btn-action{background:var(--navy);color:var(--white);border:none;padding:10px 22px;border-radius:8px;font-size:.85rem;font-weight:600;cursor:pointer;font-family:'DM Sans',sans-serif}
.btn-action:hover{background:var(--navy-mid)}
@media print{body{background:white}.page{box-shadow:none;margin:0;border-radius:0}.no-print{display:none!important}}
"""

    html = (
        "<!DOCTYPE html>\n"
        '<html lang="es">\n'
        "<head>\n"
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>Presupuesto — {_he(client.get('name', ''))}</title>\n"
        '<link href="https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700&family=DM+Sans:ital,wght@0,300;0,400;0,500;1,400&display=swap" rel="stylesheet">\n'
        f"<style>{css}</style>\n"
        "</head>\n"
        "<body>\n"
        '<div class="no-print">\n'
        '  <button class="btn-action" onclick="window.print()">&#128424; Imprimir / Guardar PDF</button>\n'
        "</div>\n"
        '<div class="page">\n'
        '  <div class="header">\n'
        '    <div class="logo-text">Scale<span>rics</span></div>\n'
        '    <div class="header-title">Presupuesto</div>\n'
        "  </div>\n"
        '  <div class="meta-row">\n'
        '    <div class="meta-item"><label>EMPRESA / CLIENTE</label><div class="value">' + _he(client.get("name", "")) + "</div></div>\n"
        '    <div class="meta-item"><label>FECHA</label><div class="value">' + today + "</div></div>\n"
        '    <div class="meta-item"><label>ESTADO</label><div class="value">' + status_label + "</div></div>\n"
        "  </div>\n"
        '  <div class="hero-block">\n'
        f"    <h2>{hero_title}</h2>\n"
        f"    {hero_desc_block}\n"
        "  </div>\n"
        f"  {intro_block}\n"
        f"  {sections_html}\n"
        '  <div class="divider"></div>\n'
        '  <div class="pricing-section">\n'
        '    <div class="price-card">\n'
        '      <div class="price-label">DESARROLLO INICIAL</div>\n'
        f'      <div class="price-amount"><sup>USD</sup> {_fmt(dev_price)}</div>\n'
        f'      <div class="price-desc">{payment_terms}</div>\n'
        f'      <ul class="checklist">{dev_checklist_html}</ul>\n'
        "    </div>\n"
        f"    {monthly_card}\n"
        "  </div>\n"
        f"  {notes_block}\n"
        '  <div class="footer">\n'
        f"    <p>{_he(factory_website)}</p>\n"
        f"    <p>{_he(factory_phone)}</p>\n"
        "  </div>\n"
        "</div>\n"
        "</body>\n"
        "</html>"
    )
    return html
