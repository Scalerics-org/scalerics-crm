import pytest
from email_sender import build_email_html, build_subject

def test_build_subject_includes_business_name():
    subject = build_subject("Pizzería Don Pepe")
    assert "Pizzería Don Pepe" in subject
    assert len(subject) < 100

def test_build_email_html_includes_demo_url():
    business = {
        "name": "Ferretería El Clavo",
        "demo_url": "https://scalerics-demos-7.vercel.app",
        "phone": "+598 2900 0000",
    }
    factory = {
        "name": "Scalerics",
        "email": "hola.scalerics@gmail.com",
        "phone": "+598 99 000 000",
    }
    html = build_email_html(business, factory)
    assert "Ferretería El Clavo" in html
    assert "https://scalerics-demos-7.vercel.app" in html
    assert "Scalerics" in html

def test_build_email_html_includes_cta_link():
    business = {
        "name": "Test",
        "demo_url": "https://test.vercel.app",
        "phone": "",
    }
    factory = {"name": "Scalerics", "email": "test@test.com", "phone": ""}
    html = build_email_html(business, factory)
    assert 'href="https://test.vercel.app"' in html
