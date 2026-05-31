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
