import pytest
from database import (init_db, add_attachment, get_attachment_file,
                      insert_business, update_attachment_file)
from unittest.mock import MagicMock, patch
from services.budget_ai import ai_edit_html, generate_budget_html


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "test.db")
    init_db(p)
    return p


def test_update_attachment_file_replaces_content(db_path):
    # El lead tiene que existir de verdad: lead_attachments.lead_id es una FK a
    # businesses. Antes este test pasaba con lead_id=1 sobre una base vacia porque
    # SQLite tenia las foreign keys apagadas y aceptaba el huerfano.
    lead_id = insert_business(db_path, {"name": "Cliente de prueba", "phone": "+59899000001"})
    attach_id = add_attachment(db_path, lead_id, "budget", "presupuesto.html",
                               file_data=b"<html>original</html>", mime_type="text/html")
    update_attachment_file(db_path, attach_id, b"<html>modificado</html>")
    row = get_attachment_file(db_path, attach_id)
    assert row["file_data"] == b"<html>modificado</html>"
    assert row["mime_type"] == "text/html"


def _mock_response(text: str):
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    return msg


def _edit(original: str, respuesta: str) -> str:
    with patch("services.budget_ai.anthropic.Anthropic") as MockClient, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        MockClient.return_value.messages.create.return_value = _mock_response(respuesta)
        return ai_edit_html(original, "cambia el precio a $500")


def test_ai_edit_html_aplica_los_reemplazos_del_json():
    """La IA devuelve pares {old,new}, no el HTML entero: asi no se trunca."""
    original = "<html><p>U$S 1.000</p><p>plazo de 3 semanas</p></html>"
    respuesta = '[{"old": "U$S 1.000", "new": "U$S 500"}, {"old": "3 semanas", "new": "4 semanas"}]'

    assert _edit(original, respuesta) == "<html><p>U$S 500</p><p>plazo de 4 semanas</p></html>"


def test_ai_edit_html_conserva_el_resto_del_documento():
    original = "<html><head><style>body{color:red}</style></head><p>U$S 1.000</p></html>"

    result = _edit(original, '[{"old": "U$S 1.000", "new": "U$S 2.000"}]')

    assert "<style>body{color:red}</style>" in result
    assert "U$S 2.000" in result


def test_ai_edit_html_acepta_el_json_envuelto_en_markdown():
    result = _edit("<p>viejo</p>", '```json\n[{"old": "viejo", "new": "nuevo"}]\n```')

    assert result == "<p>nuevo</p>"


def test_ai_edit_html_falla_si_la_respuesta_no_es_json():
    with pytest.raises(ValueError, match="no válida"):
        _edit("<p>original</p>", "<html>editado</html>")


def test_ai_edit_html_falla_si_ningun_fragmento_existe():
    """Sin esto, devolvia el documento sin tocar y el usuario no se enteraba."""
    with pytest.raises(ValueError, match="Ningun cambio"):
        _edit("<p>original</p>", '[{"old": "no esta en el documento", "new": "x"}]')


def test_generate_budget_html_returns_html():
    with patch("services.budget_ai.anthropic.Anthropic") as MockClient, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        MockClient.return_value.messages.create.return_value = _mock_response("<html>generado</html>")
        result = generate_budget_html("Plan Arq", "Arquitectura", "Posadas")
    assert "<html>" in result
