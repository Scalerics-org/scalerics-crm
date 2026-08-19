import pytest
from unittest.mock import MagicMock, patch
from demo_generator import build_prompt, parse_claude_response, render_html, ICONS_BY_SCHEME

def test_build_prompt_includes_business_name():
    business = {"name": "Ferretería El Clavo", "category": "Ferretería", "city": "Salto", "rating": 4.2, "review_count": 45, "hours": "Lun-Sáb 8-18"}
    prompt = build_prompt(business)
    assert "Ferretería El Clavo" in prompt
    assert "Salto" in prompt

def test_parse_claude_response_valid():
    raw = '{"tagline": "Todo para tu obra", "about": "Texto sobre nosotros.", "services": ["Herramientas", "Materiales", "Asesoramiento"], "cta_text": "Visitanos", "color_scheme": "cool"}'
    result = parse_claude_response(raw)
    assert result["tagline"] == "Todo para tu obra"
    assert len(result["services"]) == 3
    assert result["color_scheme"] == "cool"

def test_parse_claude_response_extracts_json_from_extra_text():
    raw = 'Aquí está el JSON:\n```json\n{"tagline": "Hola", "about": "Texto.", "services": ["A", "B", "C"], "cta_text": "Llamanos", "color_scheme": "warm"}\n```'
    result = parse_claude_response(raw)
    assert result["tagline"] == "Hola"

def test_parse_claude_response_raises_on_bad_json():
    with pytest.raises(ValueError):
        parse_claude_response("esto no es JSON válido")

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


def test_run_no_le_genera_demo_a_la_cohorte_de_discovery(tmp_path, monkeypatch):
    """Los comercios de discovery YA tienen sitio web propio: el spec dice que
    a estos no se les vende una pagina. Generarles demo es una llamada a la API
    por negocio y despues un deploy que publica en Vercel una copia del sitio
    real del cliente, que ya paso una vez en este proyecto.
    """
    import demo_generator
    from database import get_business, init_db, insert_business, update_business

    db = str(tmp_path / "demos.db")
    init_db(db)
    ids = {}
    for nombre, source in [("Sin Web", None), ("Con Web", "discovery"),
                           ("Lead Meta", "meta")]:
        biz_id = insert_business(db, {
            "name": nombre, "phone": f"+598 2900 {len(ids):04d}",
            "maps_url": f"https://maps.google.com/?cid={len(ids)}",
            "category": "Comercio", "city": "Montevideo", "source": source,
        })
        update_business(db, biz_id, status="email_found")
        ids[nombre] = biz_id

    generados = []

    def _contenido_falso(business, api_key):
        generados.append(business["name"])
        return {"tagline": "T", "about": "A", "services": ["a", "b", "c"],
                "cta_text": "C", "color_scheme": "warm", "template": "modern"}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(demo_generator, "generate_content", _contenido_falso)
    monkeypatch.setattr(demo_generator, "render_html", lambda c, b: "<html></html>")
    monkeypatch.setattr(demo_generator.time, "sleep", lambda s: None)

    demo_generator.run(db, "api-key-de-prueba")

    assert sorted(generados) == ["Lead Meta", "Sin Web"]
    assert get_business(db, ids["Con Web"])["status"] == "email_found", \
        "la fila de discovery no se toca"
