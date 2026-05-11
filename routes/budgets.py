"""Budget generation and management routes."""

import json
import os

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


def _db() -> str:
    return current_app.config["DB_PATH"]


@budgets_bp.route("/api/leads/<int:client_id>/budget", methods=["GET"])
def api_get_budget(client_id):
    budget = get_budget_for_client(_db(), client_id)
    if not budget:
        return jsonify(None), 200
    if budget.get("items") and isinstance(budget["items"], str):
        try:
            budget["items"] = json.loads(budget["items"])
        except Exception:
            budget["items"] = []
    if budget.get("notes") and isinstance(budget["notes"], str):
        try:
            budget["notes"] = json.loads(budget["notes"])
        except Exception:
            pass
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

    # Gather requirements from all sources
    extra_req = (data.get("requirements") or "").strip()
    meeting_reqs = "\n".join(
        m["requirements"] for m in meetings if m.get("requirements")
    )
    requirements = "\n".join(filter(None, [extra_req, meeting_reqs])) or "sitio web profesional con diseño moderno"

    service_type = (
        client_info.get("rubro")
        or data.get("service_type")
        or client.get("category", "desarrollo web")
    )
    budget_range = client_info.get("budget_range") or data.get("budget_range") or "no especificado"
    team_size = client_info.get("needs") or "no especificado"

    prompt = f"""Generá un presupuesto profesional para un proyecto de {service_type} para el negocio "{client.get('name', '')}".

Información del cliente:
- Negocio: {client.get('name', '')} ({client.get('category', '')})
- Ciudad: {client.get('city', 'Montevideo')}, Uruguay
- Tamaño del equipo: {team_size}
- Rango de presupuesto indicado: {budget_range}
- Requerimientos del proyecto:
{requirements}

Devolvé SOLO un JSON con este formato exacto (sin texto extra, sin markdown):
{{
  "items": [
    {{"name": "Diseño y maquetado", "description": "Diseño responsive en Figma, paleta de marca", "hours": 20, "unit_price": 50, "total": 1000}},
    {{"name": "Desarrollo frontend", "description": "HTML/CSS/JS responsive, SEO básico", "hours": 30, "unit_price": 50, "total": 1500}}
  ],
  "subtotal": 2500,
  "discount": 0,
  "discount_note": "",
  "total": 2500,
  "currency": "USD",
  "payment_terms": "50% adelanto, 50% al entregar",
  "validity_days": 30,
  "notes": "Incluye 2 rondas de revisiones. Hosting y dominio no incluidos."
}}

Precios en USD, realistas para el mercado uruguayo (desarrolladores ~$40-70/h).
Mínimo 3 ítems, máximo 7. Adaptá los ítems al tipo de proyecto."""

    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        ai = anthropic.Anthropic(api_key=api_key)
        msg = ai.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rstrip("`").strip()
        budget_data = json.loads(raw)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Error generando presupuesto: {e}"}), 500

    items_json = json.dumps(budget_data.get("items", []), ensure_ascii=False)
    total = float(budget_data.get("total", 0))
    meta = {k: v for k, v in budget_data.items() if k != "items"}
    notes_json = json.dumps(meta, ensure_ascii=False)

    existing = get_budget_for_client(_db(), client_id)
    if existing:
        update_budget(_db(), existing["id"], items=items_json, total_amount=total, notes=notes_json, status="draft")
        budget_id = existing["id"]
    else:
        budget_id = create_budget(_db(), client_id, items=items_json, total_amount=total, notes=notes_json)

    return jsonify({"ok": True, "budget_id": budget_id, "data": budget_data})


@budgets_bp.route("/api/budgets/<int:budget_id>", methods=["PUT"])
def api_update_budget(budget_id):
    data = request.get_json() or {}
    fields: dict = {}
    if "items" in data:
        fields["items"] = json.dumps(data["items"], ensure_ascii=False) if isinstance(data["items"], list) else data["items"]
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
    import datetime
    update_budget(_db(), budget_id, status="sent", sent_at=datetime.datetime.now().isoformat())
    return jsonify({"ok": True})


@budgets_bp.route("/api/leads/<int:client_id>/budget/preview")
def api_budget_preview(client_id):
    import datetime

    client = get_business(_db(), client_id)
    if not client:
        return "Lead no encontrado", 404
    budget = get_budget_for_client(_db(), client_id)
    if not budget:
        return "Sin presupuesto generado para este lead.", 404

    items = []
    if budget.get("items"):
        try:
            items = json.loads(budget["items"]) if isinstance(budget["items"], str) else budget["items"]
        except Exception:
            items = []

    meta = {}
    if budget.get("notes"):
        try:
            meta = json.loads(budget["notes"]) if isinstance(budget["notes"], str) else budget["notes"]
        except Exception:
            pass

    currency = meta.get("currency", "USD")
    total = budget.get("total_amount", 0) or 0
    subtotal = meta.get("subtotal", total)
    discount = meta.get("discount", 0)
    payment_terms = meta.get("payment_terms", "50% adelanto, 50% al entregar")
    validity_days = meta.get("validity_days", 30)
    notes = meta.get("notes", "")
    today = datetime.date.today().strftime("%d/%m/%Y")

    items_html = ""
    for item in items:
        items_html += f"""
        <tr>
          <td><strong>{_he(item.get('name',''))}</strong><br><span class="item-desc">{_he(item.get('description',''))}</span></td>
          <td class="num">{item.get('hours',0)}</td>
          <td class="num">{currency} {item.get('unit_price',0):,.0f}</td>
          <td class="num total-cell">{currency} {item.get('total',0):,.0f}</td>
        </tr>"""

    discount_row = ""
    if discount:
        discount_row = f'<tr class="subtotal-row"><td colspan="3">Descuento</td><td class="num">- {currency} {discount:,.0f}</td></tr>'

    factory_name = os.environ.get("FACTORY_NAME", "Scalerics")
    factory_email = os.environ.get("FACTORY_EMAIL", "")
    factory_phone = os.environ.get("FACTORY_PHONE", "")

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Presupuesto — {_he(client.get('name',''))}</title>
<style>
*,*::before,*::after{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f8fafc;color:#1e293b;min-height:100vh;padding:32px 16px}}
.page{{max-width:780px;margin:0 auto;background:#fff;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,.08);overflow:hidden}}
.header{{background:linear-gradient(135deg,#0088cc,#0055a5);padding:32px 40px;color:#fff;display:flex;justify-content:space-between;align-items:flex-start}}
.header-logo{{font-size:1.6rem;font-weight:800;letter-spacing:-0.5px}}
.header-sub{{font-size:.8rem;opacity:.75;margin-top:2px}}
.header-right{{text-align:right;font-size:.82rem;opacity:.85;line-height:1.6}}
.body{{padding:36px 40px}}
.meta-row{{display:flex;justify-content:space-between;margin-bottom:32px;gap:20px}}
.meta-block{{flex:1}}
.meta-label{{font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:#94a3b8;margin-bottom:4px}}
.meta-val{{font-size:.92rem;font-weight:600;color:#0f172a}}
.meta-sub{{font-size:.78rem;color:#64748b;margin-top:1px}}
table{{width:100%;border-collapse:collapse;margin-bottom:24px}}
th{{background:#f1f5f9;color:#475569;font-size:.68rem;text-transform:uppercase;letter-spacing:.6px;padding:10px 14px;text-align:left;border-bottom:2px solid #e2e8f0}}
td{{padding:12px 14px;border-bottom:1px solid #f1f5f9;font-size:.85rem;vertical-align:top}}
.num{{text-align:right;white-space:nowrap}}
.total-cell{{font-weight:600;color:#0f172a}}
.item-desc{{color:#64748b;font-size:.78rem;margin-top:2px}}
.subtotal-row td{{color:#64748b;font-size:.82rem;border-bottom:none;padding:6px 14px}}
.total-row td{{font-size:1rem;font-weight:800;color:#0088cc;border-top:2px solid #e2e8f0;border-bottom:none;padding:14px}}
.info-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:24px}}
.info-card{{background:#f8fafc;border-radius:8px;padding:14px}}
.info-card-label{{font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:#94a3b8;margin-bottom:4px}}
.info-card-val{{font-size:.82rem;color:#334155}}
.notes-box{{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:14px;margin-bottom:24px;font-size:.82rem;color:#92400e;line-height:1.6}}
.footer{{background:#f8fafc;border-top:1px solid #e2e8f0;padding:20px 40px;display:flex;justify-content:space-between;align-items:center;font-size:.75rem;color:#94a3b8}}
.print-btn{{background:#0088cc;color:#fff;border:none;padding:10px 22px;border-radius:8px;font-size:.85rem;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px}}
.print-btn:hover{{background:#0070aa}}
.no-print{{margin-bottom:20px;display:flex;gap:10px;justify-content:flex-end}}
@media print{{
  body{{background:#fff;padding:0}}
  .page{{box-shadow:none;border-radius:0}}
  .no-print{{display:none!important}}
}}
</style>
</head>
<body>
<div class="no-print">
  <button class="print-btn" onclick="window.print()">🖨 Imprimir / Guardar PDF</button>
  <button class="print-btn" style="background:#1e293b" onclick="_copyWA()">📋 Copiar para WhatsApp</button>
</div>
<div class="page">
  <div class="header">
    <div>
      <div class="header-logo">{_he(factory_name)}</div>
      <div class="header-sub">Agencia web · www.scalerics.com</div>
    </div>
    <div class="header-right">
      {_he(factory_email)}<br>
      {_he(factory_phone)}<br>
      {today}
    </div>
  </div>
  <div class="body">
    <div class="meta-row">
      <div class="meta-block">
        <div class="meta-label">Presupuesto para</div>
        <div class="meta-val">{_he(client.get('name',''))}</div>
        <div class="meta-sub">{_he(client.get('city',''))}</div>
      </div>
      <div class="meta-block" style="text-align:right">
        <div class="meta-label">Válido hasta</div>
        <div class="meta-val">{validity_days} días desde hoy</div>
        <div class="meta-sub">Estado: {'Enviado' if budget.get('status') == 'sent' else 'Borrador'}</div>
      </div>
    </div>
    <table>
      <thead><tr><th>Descripción</th><th class="num">Horas</th><th class="num">Precio/h</th><th class="num">Total</th></tr></thead>
      <tbody>
        {items_html}
        {'<tr class="subtotal-row"><td colspan="3">Subtotal</td><td class="num">' + currency + ' ' + f'{subtotal:,.0f}' + '</td></tr>' if discount else ''}
        {discount_row}
        <tr class="total-row"><td colspan="3">TOTAL</td><td class="num">{currency} {total:,.0f}</td></tr>
      </tbody>
    </table>
    <div class="info-grid">
      <div class="info-card">
        <div class="info-card-label">Forma de pago</div>
        <div class="info-card-val">{_he(payment_terms)}</div>
      </div>
      <div class="info-card">
        <div class="info-card-label">Moneda</div>
        <div class="info-card-val">{currency} (Dólares americanos)</div>
      </div>
    </div>
    {f'<div class="notes-box">📌 {_he(notes)}</div>' if notes else ''}
  </div>
  <div class="footer">
    <span>© {datetime.date.today().year} {_he(factory_name)} · Todos los derechos reservados</span>
    <span>Documento generado el {today}</span>
  </div>
</div>
<script>
function _copyWA() {{
  const items = {json.dumps(items)};
  const lines = ['*Presupuesto — {_he(client.get("name",""))}*', ''];
  items.forEach(i => lines.push(`• *${{i.name}}*: {currency} ${{(i.total||0).toLocaleString()}}  _(${{i.description||''}})_`));
  lines.push('');
  lines.push(`*Total: {currency} {total:,.0f}*`);
  lines.push(`_{_he(payment_terms)}_`);
  lines.push(`_Válido {validity_days} días_`);
  {'lines.push("", "_" + ' + repr(_he(notes)) + ' + "_");' if notes else ''}
  navigator.clipboard.writeText(lines.join('\\n')).then(() => alert('Copiado al portapapeles'));
}}
</script>
</body>
</html>"""
    return html


def _he(s):
    import html
    return html.escape(str(s or ""))
