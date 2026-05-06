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
