import pytest
from database import (
    init_db,
    create_user,
    get_user_by_email,
    get_user_by_id,
    get_all_users,
    delete_user,
    update_user_password,
    create_reset_token,
    get_reset_token,
    use_reset_token,
)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


def test_tables_created(db):
    import sqlite3
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "users" in tables
    assert "password_reset_tokens" in tables
    conn.close()


def test_create_and_get_user(db):
    uid = create_user(db, name="Juan", email="juan@test.com", phone="099123456", password_hash="hash123")
    assert isinstance(uid, int)
    user = get_user_by_email(db, "juan@test.com")
    assert user is not None
    assert user["name"] == "Juan"
    assert user["phone"] == "099123456"


def test_email_uniqueness(db):
    create_user(db, name="Juan", email="juan@test.com", phone="099111111", password_hash="hash1")
    result = create_user(db, name="Otro", email="juan@test.com", phone="099222222", password_hash="hash2")
    assert result is None


def test_get_user_by_id(db):
    uid = create_user(db, name="Ana", email="ana@test.com", phone="099333333", password_hash="hash")
    user = get_user_by_id(db, uid)
    assert user["email"] == "ana@test.com"


def test_get_user_by_email_not_found(db):
    assert get_user_by_email(db, "noexiste@test.com") is None


def test_get_all_users(db):
    create_user(db, name="A", email="a@test.com", phone="1", password_hash="h")
    create_user(db, name="B", email="b@test.com", phone="2", password_hash="h")
    users = get_all_users(db)
    assert len(users) == 2


def test_delete_user(db):
    uid = create_user(db, name="Del", email="del@test.com", phone="9", password_hash="h")
    delete_user(db, uid)
    assert get_user_by_id(db, uid) is None


def test_create_and_get_reset_token(db):
    uid = create_user(db, name="X", email="x@test.com", phone="0", password_hash="h")
    token_str = "abc123token"
    create_reset_token(db, user_id=uid, token=token_str)
    record = get_reset_token(db, token_str)
    assert record is not None
    assert record["user_id"] == uid
    assert record["used_at"] is None


def test_use_reset_token(db):
    uid = create_user(db, name="Y", email="y@test.com", phone="0", password_hash="h")
    create_reset_token(db, user_id=uid, token="tok456")
    use_reset_token(db, "tok456")
    record = get_reset_token(db, "tok456")
    assert record["used_at"] is not None


def test_get_nonexistent_token(db):
    assert get_reset_token(db, "noexiste") is None


def test_update_user_password(db):
    uid = create_user(db, name="Pw", email="pw@test.com", phone="0", password_hash="old_hash")
    update_user_password(db, uid, "new_hash")
    user = get_user_by_id(db, uid)
    assert user["password"] == "new_hash"
