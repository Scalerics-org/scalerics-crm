import pytest
from database import init_db, add_attachment, get_attachment_file, update_attachment_file
from unittest.mock import MagicMock, patch
from services.budget_ai import ai_edit_html, generate_budget_html


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


def _mock_response(text: str):
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    return msg


def test_ai_edit_html_returns_modified_html():
    with patch("services.budget_ai.anthropic.Anthropic") as MockClient, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        MockClient.return_value.messages.create.return_value = _mock_response("<html>editado</html>")
        result = ai_edit_html("<html>original</html>", "cambia el precio a $500")
    assert result == "<html>editado</html>"


def test_generate_budget_html_returns_html():
    with patch("services.budget_ai.anthropic.Anthropic") as MockClient, \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        MockClient.return_value.messages.create.return_value = _mock_response("<html>generado</html>")
        result = generate_budget_html("Plan Arq", "Arquitectura", "Posadas")
    assert "<html>" in result
