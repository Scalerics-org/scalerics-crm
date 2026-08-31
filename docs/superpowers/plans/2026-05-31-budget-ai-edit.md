# Budget Tab AI Edit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar la pestaña Presupuesto con una UI state-based que muestra "Generar con IA" cuando no hay presupuesto, y "Editar con IA" cuando ya existe un HTML adjunto.

**Architecture:** Nuevo servicio `services/budget_ai.py` con dos funciones (editar / generar) que llaman a Claude Haiku. Tres endpoints nuevos en `routes/leads.py`. `_cpRenderBudget()` en `dashboard.py` se reescribe completamente.

**Tech Stack:** Python `anthropic` SDK (ya instalado), Flask, SQLite, JavaScript vanilla en `dashboard.py`.

---

## File Map

| Archivo | Acción |
|---|---|
| `database.py` | Agregar `update_attachment_file()` |
| `services/budget_ai.py` | Crear — funciones `ai_edit_html()` y `generate_budget_html()` |
| `routes/leads.py` | Agregar 4 endpoints: `ai-edit`, `ai-apply`, `budget/generate`, `print` |
| `dashboard.py` | Reescribir `_cpRenderBudget()` + modal Editar IA + modal Generar IA + botón PDF |
| `tests/test_budget_ai.py` | Crear — tests para `services/budget_ai.py` |

---

## Task 1: DB helper — `update_attachment_file()`

**Files:**
- Modify: `database.py` (después de `get_attachment_file`, línea ~1071)
- Test: `tests/test_budget_ai.py`

- [ ] **Agregar la función a `database.py`** justo después de `get_attachment_file`:

```python
def update_attachment_file(db_path: str, attach_id: int, file_data: bytes, mime_type: str = "text/html") -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE lead_attachments SET file_data=?, mime_type=? WHERE id=?",
            (file_data, mime_type, attach_id)
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Crear `tests/test_budget_ai.py`** con el test de la función DB:

```python
import pytest
from database import init_db, add_attachment, get_attachment_file, update_attachment_file


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "test.db")
    init_db(p)
    return p


def test_update_attachment_file_replaces_content(db_path):
    attach_id = add_attachment(db_path, 1, "budget", "presupuesto.html",
                               file_data=b"<html>original</html>", mime_type="text/html")
    update_attachment_file(db_path, attach_id, b"<html>modificado</html>")
    row = get_attachment_file(db_path, attach_id)
    assert row["file_data"] == b"<html>modificado</html>"
    assert row["mime_type"] == "text/html"
```

- [ ] **Correr el test:**

```bash
cd C:\Users\juant\lead-gen-uy
pytest tests/test_budget_ai.py::test_update_attachment_file_replaces_content -v
```

Esperado: `PASSED`

- [ ] **Commit:**

```bash
git add database.py tests/test_budget_ai.py
git commit -m "feat: add update_attachment_file DB helper"
```

---

## Task 2: Servicio `services/budget_ai.py`

**Files:**
- Create: `services/budget_ai.py`
- Test: `tests/test_budget_ai.py`

- [ ] **Crear `services/budget_ai.py`:**

```python
"""AI service for budget HTML editing and generation using Claude Haiku."""

import os
import anthropic

MODEL = "claude-haiku-4-5-20251001"

_EDIT_SYSTEM = """Sos un asistente que edita documentos HTML de presupuestos profesionales.
Tu tarea es aplicar los cambios solicitados preservando exactamente la estructura,
los estilos CSS y el formato visual del documento original.
Devolvé ÚNICAMENTE el HTML completo modificado, sin explicaciones ni bloques markdown."""

_GEN_SYSTEM = """Sos un asistente que genera presupuestos HTML profesionales para
Scalerics, una agencia de desarrollo web en Uruguay.
Usá esta paleta de colores: navy #0f1f3d, accent #0088CC, green #0d9e6e, texto #1c2b40.
Incluí: encabezado con logo de Scalerics, datos del cliente, descripción del proyecto,
lista de lo que incluye el desarrollo, precio de desarrollo, precio mensual de mantenimiento,
notas y pie de página.
Devolvé ÚNICAMENTE el HTML completo listo para abrir en el navegador, sin explicaciones ni markdown."""


def ai_edit_html(original_html: str, instructions: str) -> str:
    """Apply AI instructions to an existing HTML budget. Returns modified HTML."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model=MODEL,
        max_tokens=8096,
        system=_EDIT_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"HTML original:\n\n{original_html}\n\nInstrucciones: {instructions}",
            }
        ],
    )
    return message.content[0].text


def generate_budget_html(business_name: str, category: str, city: str, instructions: str = "") -> str:
    """Generate a full HTML budget from scratch for the given business."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = f"Generá un presupuesto HTML para:\n- Negocio: {business_name}\n- Rubro: {category}\n- Ciudad: {city}"
    if instructions:
        prompt += f"\n- Instrucciones adicionales: {instructions}"
    message = client.messages.create(
        model=MODEL,
        max_tokens=8096,
        system=_GEN_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text
```

- [ ] **Agregar tests con mock de Anthropic a `tests/test_budget_ai.py`:**

```python
from unittest.mock import MagicMock, patch
from services.budget_ai import ai_edit_html, generate_budget_html


def _mock_response(text: str):
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    return msg


def test_ai_edit_html_returns_modified_html():
    with patch("services.budget_ai.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = _mock_response("<html>editado</html>")
        result = ai_edit_html("<html>original</html>", "cambia el precio a $500")
    assert result == "<html>editado</html>"


def test_generate_budget_html_returns_html():
    with patch("services.budget_ai.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = _mock_response("<html>generado</html>")
        result = generate_budget_html("Plan Arq", "Arquitectura", "Posadas")
    assert "<html>" in result
```

- [ ] **Correr los tests:**

```bash
pytest tests/test_budget_ai.py -v
```

Esperado: 3 PASSED

- [ ] **Commit:**

```bash
git add services/budget_ai.py tests/test_budget_ai.py
git commit -m "feat: budget AI service — ai_edit_html and generate_budget_html"
```

---

## Task 3: Endpoints en `routes/leads.py`

**Files:**
- Modify: `routes/leads.py`

Agregar los tres endpoints al final del archivo, antes del último cierre. Requieren el import de los helpers nuevos.

- [ ] **Agregar imports al tope de `routes/leads.py`** (después de los imports existentes):

```python
from database import get_attachment_file, update_attachment_file
from services.budget_ai import ai_edit_html, generate_budget_html
```

- [ ] **Agregar los cuatro endpoints al final de `routes/leads.py`** (antes del EOF):

```python
@leads_bp.route("/api/attachments/<int:attach_id>/ai-edit", methods=["POST"])
def api_attachment_ai_edit(attach_id):
    data = request.get_json() or {}
    instructions = (data.get("instructions") or "").strip()
    if not instructions:
        return jsonify({"ok": False, "error": "instructions requeridas"}), 400
    row = get_attachment_file(_db(), attach_id)
    if not row or not row["file_data"]:
        return jsonify({"ok": False, "error": "Adjunto no encontrado"}), 404
    if (row.get("mime_type") or "") != "text/html":
        return jsonify({"ok": False, "error": "El adjunto no es HTML"}), 400
    try:
        original_html = row["file_data"].decode("utf-8")
        modified_html = ai_edit_html(original_html, instructions)
        return jsonify({"ok": True, "html": modified_html})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@leads_bp.route("/api/attachments/<int:attach_id>/ai-apply", methods=["POST"])
def api_attachment_ai_apply(attach_id):
    data = request.get_json() or {}
    html = (data.get("html") or "").strip()
    if not html:
        return jsonify({"ok": False, "error": "html requerido"}), 400
    update_attachment_file(_db(), attach_id, html.encode("utf-8"))
    return jsonify({"ok": True})


@leads_bp.route("/api/attachments/<int:attach_id>/print")
def api_attachment_print(attach_id):
    """Serve the HTML with auto-print injected so the browser opens the print dialog."""
    row = get_attachment_file(_db(), attach_id)
    if not row or not row["file_data"]:
        return jsonify({"error": "not found"}), 404
    html = row["file_data"].decode("utf-8")
    # Inject print trigger just before </body>
    print_script = "<script>window.addEventListener('load',()=>window.print())</script>"
    if "</body>" in html:
        html = html.replace("</body>", f"{print_script}</body>", 1)
    else:
        html += print_script
    return Response(html, mimetype="text/html")


@leads_bp.route("/api/leads/<int:biz_id>/budget/generate", methods=["POST"])
def api_budget_generate(biz_id):
    biz = get_business(_db(), biz_id)
    if not biz:
        return jsonify({"ok": False, "error": "Lead no encontrado"}), 404
    data = request.get_json() or {}
    instructions = (data.get("instructions") or "").strip()
    try:
        html = generate_budget_html(
            business_name=biz.get("name", ""),
            category=biz.get("category", ""),
            city=biz.get("city", ""),
            instructions=instructions,
        )
        file_data = html.encode("utf-8")
        safe_name = re.sub(r"[^a-z0-9]", "-", (biz.get("name") or "cliente").lower()).strip("-")
        attach_id = add_attachment(_db(), biz_id, "budget", f"presupuesto-{safe_name}.html",
                                   file_data=file_data, mime_type="text/html")
        return jsonify({"ok": True, "attachment_id": attach_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
```

- [ ] **Verificar que el servidor arranca sin errores:**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "from routes.leads import leads_bp; print('OK')"
```

Esperado: `OK`

- [ ] **Commit:**

```bash
git add routes/leads.py
git commit -m "feat: ai-edit, ai-apply and budget/generate endpoints"
```

---

## Task 4: Reescribir `_cpRenderBudget()` en `dashboard.py`

**Files:**
- Modify: `dashboard.py` líneas 3518–3570 (función `_cpRenderBudget`)

- [ ] **Reemplazar toda la función `_cpRenderBudget()`** (líneas 3518–3569) con:

```javascript
function _cpRenderBudget() {
  const items = (_cpData.attBudget || []).filter(a => a.mime_type === 'text/html');
  const hasBudget = items.length > 0;

  if (!hasBudget) {
    return `<div class="cp-section">
      <div class="cp-section-title">Presupuesto</div>
      <div style="color:#475569;font-size:.85rem;margin-bottom:14px">No hay presupuesto para este cliente.</div>
      <button class="cp-btn cp-btn-primary" onclick="_cpOpenGenBudgetModal()">
        ⚡ Generar con IA
      </button>
    </div>
    ${_cpRenderAttachBox('budget')}`;
  }

  const listHtml = items.map(a => `
    <div class="attach-item" style="display:flex;align-items:center;justify-content:space-between;padding:8px 10px;background:#0a0f1a;border-radius:6px;margin-bottom:6px">
      <a href="/api/attachments/${a.id}/file" target="_blank" style="color:#33aadd;font-size:.85rem;text-decoration:none">📄 ${esc(a.name)}</a>
      <div style="display:flex;gap:6px">
        <a class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:3px 10px;text-decoration:none" href="/api/attachments/${a.id}/print" target="_blank">🖨 PDF</a>
        <button class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:3px 10px" onclick="_cpOpenAiEditModal(${a.id},'${esc(a.name)}')">✏️ Editar con IA</button>
        <button class="attach-del" title="Eliminar" onclick="_cpDeleteAttach(${a.id},'budget')">✕</button>
      </div>
    </div>`).join('');

  return `<div class="cp-section">
    <div class="cp-section-title">Presupuesto</div>
    ${listHtml}
  </div>
  ${_cpRenderAttachBox('budget')}`;
}
```

- [ ] **Verificar que `_cpBindBudget` sigue existiendo** (se llama en `_cpSwitchTab`). Buscar en el archivo:

```bash
grep -n "_cpBindBudget" C:\Users\juant\lead-gen-uy\dashboard.py
```

Si solo existe `function _cpBindBudget() {}` en la línea 3604, no hay nada que cambiar.

- [ ] **Commit:**

```bash
git add dashboard.py
git commit -m "feat: rewrite _cpRenderBudget as state-based UI"
```

---

## Task 5: Modal "Editar con IA"

**Files:**
- Modify: `dashboard.py` — agregar función `_cpOpenAiEditModal` y el modal HTML

- [ ] **Buscar dónde está `_cpBindBudget` en dashboard.py:**

```bash
grep -n "_cpBindBudget\|function _cpOpenDemoModal" C:\Users\juant\lead-gen-uy\dashboard.py
```

Anotar la línea de `function _cpOpenDemoModal()` (actualmente ~3606). Agregar las funciones nuevas justo antes de esa línea.

- [ ] **Agregar las funciones JS antes de `function _cpOpenDemoModal()`:**

```javascript
function _cpOpenAiEditModal(attachId, attachName) {
  const existing = document.getElementById('ai-edit-modal');
  if (existing) existing.remove();
  const modal = document.createElement('div');
  modal.id = 'ai-edit-modal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;display:flex;align-items:center;justify-content:center';
  modal.innerHTML = `
    <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:24px;width:min(680px,95vw);max-height:90vh;overflow:auto">
      <div style="font-family:Sora,sans-serif;font-size:1rem;font-weight:700;color:#e2e8f0;margin-bottom:16px">✏️ Editar con IA — ${esc(attachName)}</div>
      <textarea id="ai-edit-instr" placeholder="Ej: cambia el precio a $500 USD, agrega mantenimiento mensual de $30..."
        style="width:100%;height:80px;background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;color:#e2e8f0;font-size:.85rem;padding:10px;resize:vertical;box-sizing:border-box"></textarea>
      <div style="display:flex;gap:8px;margin-top:10px">
        <button class="cp-btn cp-btn-primary" id="ai-edit-preview-btn" onclick="_cpAiEditPreview(${attachId})">
          <span id="ai-edit-spin" class="cp-spinner" style="display:none"></span>
          Generar preview
        </button>
        <button class="cp-btn cp-btn-ghost" onclick="document.getElementById('ai-edit-modal').remove()">Cancelar</button>
      </div>
      <div id="ai-edit-preview-wrap" style="display:none;margin-top:16px">
        <div style="font-size:.75rem;color:#475569;margin-bottom:6px">Preview:</div>
        <iframe id="ai-edit-iframe" style="width:100%;height:400px;border:1px solid #1e293b;border-radius:6px;background:#fff"></iframe>
        <div style="display:flex;gap:8px;margin-top:10px">
          <button class="cp-btn cp-btn-success" id="ai-edit-save-btn" onclick="_cpAiEditSave(${attachId})">✅ Guardar</button>
          <button class="cp-btn cp-btn-ghost" onclick="document.getElementById('ai-edit-modal').remove()">Cancelar</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(modal);
}

let _aiEditPendingHtml = '';

async function _cpAiEditPreview(attachId) {
  const instr = (document.getElementById('ai-edit-instr').value || '').trim();
  if (!instr) { alert('Escribí las instrucciones primero'); return; }
  const btn = document.getElementById('ai-edit-preview-btn');
  const spin = document.getElementById('ai-edit-spin');
  const textarea = document.getElementById('ai-edit-instr');
  btn.disabled = true; spin.style.display = 'inline-block'; textarea.disabled = true;
  try {
    const r = await fetch(`/api/attachments/${attachId}/ai-edit`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({instructions: instr})
    });
    const d = await r.json();
    if (!d.ok) { alert(d.error || 'Error generando preview'); return; }
    _aiEditPendingHtml = d.html;
    const iframe = document.getElementById('ai-edit-iframe');
    iframe.srcdoc = d.html;
    document.getElementById('ai-edit-preview-wrap').style.display = '';
  } catch(e) { alert('Error: ' + e); }
  finally { btn.disabled = false; spin.style.display = 'none'; textarea.disabled = false; }
}

async function _cpAiEditSave(attachId) {
  if (!_aiEditPendingHtml) return;
  const btn = document.getElementById('ai-edit-save-btn');
  btn.disabled = true;
  try {
    const r = await fetch(`/api/attachments/${attachId}/ai-apply`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({html: _aiEditPendingHtml})
    });
    const d = await r.json();
    if (d.ok) {
      document.getElementById('ai-edit-modal').remove();
      await _cpReloadAttach('budget');
      _cpSwitchTab('budget');
    } else { alert(d.error || 'Error guardando'); }
  } catch(e) { alert('Error: ' + e); }
  finally { btn.disabled = false; }
}
```

- [ ] **Commit:**

```bash
git add dashboard.py
git commit -m "feat: AI edit modal for budget HTML attachments"
```

---

## Task 6: Modal "Generar con IA"

**Files:**
- Modify: `dashboard.py` — agregar `_cpOpenGenBudgetModal` y `_cpGenBudget`

- [ ] **Agregar estas funciones justo después de `_cpAiEditSave` del task anterior:**

```javascript
function _cpOpenGenBudgetModal() {
  const existing = document.getElementById('gen-budget-modal');
  if (existing) existing.remove();
  const modal = document.createElement('div');
  modal.id = 'gen-budget-modal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;display:flex;align-items:center;justify-content:center';
  modal.innerHTML = `
    <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:24px;width:min(480px,95vw)">
      <div style="font-family:Sora,sans-serif;font-size:1rem;font-weight:700;color:#e2e8f0;margin-bottom:16px">⚡ Generar presupuesto con IA</div>
      <textarea id="gen-budget-instr" placeholder="Instrucciones adicionales (opcional). Ej: sitio web para arquitecta, precio $370 USD, mantenimiento $25/mes..."
        style="width:100%;height:80px;background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;color:#e2e8f0;font-size:.85rem;padding:10px;resize:vertical;box-sizing:border-box"></textarea>
      <div style="display:flex;gap:8px;margin-top:12px">
        <button class="cp-btn cp-btn-primary" id="gen-budget-btn" onclick="_cpGenBudget()">
          <span id="gen-budget-spin" class="cp-spinner" style="display:none"></span>
          Generar
        </button>
        <button class="cp-btn cp-btn-ghost" onclick="document.getElementById('gen-budget-modal').remove()">Cancelar</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
}

async function _cpGenBudget() {
  const instr = (document.getElementById('gen-budget-instr').value || '').trim();
  const btn = document.getElementById('gen-budget-btn');
  const spin = document.getElementById('gen-budget-spin');
  btn.disabled = true; spin.style.display = 'inline-block';
  try {
    const r = await fetch(`/api/leads/${_cpClientId}/budget/generate`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({instructions: instr})
    });
    const d = await r.json();
    if (d.ok) {
      document.getElementById('gen-budget-modal').remove();
      await _cpReloadAttach('budget');
      _cpSwitchTab('budget');
    } else { alert(d.error || 'Error generando presupuesto'); }
  } catch(e) { alert('Error: ' + e); }
  finally { btn.disabled = false; spin.style.display = 'none'; }
}
```

- [ ] **Commit:**

```bash
git add dashboard.py
git commit -m "feat: generate budget with AI modal"
```

---

## Task 7: Push y deploy

- [ ] **Verificar que todos los tests pasan:**

```bash
cd C:\Users\juant\lead-gen-uy
pytest tests/test_budget_ai.py -v
```

Esperado: 3 PASSED

- [ ] **Push a Railway (incluye el fix de teléfonos commiteado previamente):**

```bash
git push origin main
```

- [ ] **Esperar el deploy y verificar el endpoint nuevo:**

```bash
curl -s -X POST https://web-production-cb6e.up.railway.app/api/leads/598/budget/generate \
  -H "Content-Type: application/json" \
  -H "x-admin-token: $ADMIN_TOKEN" \
  -d '{"instructions":"test"}'
```

Esperado: `{"ok":true,"attachment_id":<N>}`

- [ ] **Abrir el CRM, ir al lead Plan Arq → pestaña Presupuesto y verificar:**
  - Si no hay adjunto HTML: aparece "No hay presupuesto" + botón "Generar con IA"
  - Si hay adjunto HTML: aparece el archivo con botón "Editar con IA"
  - Probar el flujo Editar con IA: instrucción → preview en iframe → guardar
