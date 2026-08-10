import re
import sqlite3
import logging
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ─── Schema helpers ──────────────────────────────────────────────────────────

def _add_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError:
        pass  # column already exists


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")  # better concurrency
    conn.row_factory = sqlite3.Row
    return conn


# ─── Init ────────────────────────────────────────────────────────────────────

def init_db(db_path: str) -> None:
    conn = _connect(db_path)
    try:
        # ── Existing table ────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS businesses (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                category        TEXT,
                address         TEXT,
                city            TEXT,
                phone           TEXT UNIQUE,
                rating          REAL,
                review_count    INTEGER,
                hours           TEXT,
                maps_url        TEXT UNIQUE,
                facebook_url    TEXT,
                instagram_url   TEXT,
                color_scheme    TEXT,
                demo_html_path  TEXT,
                demo_url        TEXT,
                status          TEXT DEFAULT 'scraped',
                error_message   TEXT,
                scraped_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                email_sent_at   TIMESTAMP
            )
        """)
        # Additive column migrations for businesses
        _add_column(conn, "businesses", "notes", "TEXT")
        _add_column(conn, "businesses", "pitch_text", "TEXT")
        _add_column(conn, "businesses", "crm_status", "TEXT DEFAULT 'sin_contactar'")
        _add_column(conn, "businesses", "has_whatsapp", "INTEGER")
        _add_column(conn, "businesses", "callback_date", "TEXT")
        _add_column(conn, "businesses", "source", "TEXT")
        _add_column(conn, "businesses", "form_data", "TEXT")
        _add_column(conn, "businesses", "email", "TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS roles (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL UNIQUE,
                panel_access TEXT NOT NULL,
                created_at   TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Seed default roles if none exist
        if not conn.execute("SELECT 1 FROM roles LIMIT 1").fetchone():
            import json as _j
            _ALL = _j.dumps(["cola","seguimientos","meta","pipeline","clientes","tasks","wa","cal","metrics","activity","sdr"])
            _CALLER = _j.dumps(["cola","seguimientos","meta","wa"])
            _SALES = _j.dumps(["seguimientos","meta","pipeline","clientes","cal","metrics"])
            conn.executemany("INSERT INTO roles (name, panel_access) VALUES (?,?)", [
                ("Admin",  _ALL),
                ("Caller", _CALLER),
                ("Ventas", _SALES),
            ])
        _add_column(conn, "client_info", "meeting_time", "TEXT")
        _add_column(conn, "client_info", "meeting_url", "TEXT")

        # ── demos ─────────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS demos (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id       INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                status          TEXT NOT NULL DEFAULT 'pending',
                html_path       TEXT,
                url             TEXT,
                generated_at    TIMESTAMP,
                generated_by    TEXT,
                error_message   TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(client_id)
            )
        """)

        # ── jobs ──────────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                type            TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                payload         TEXT,
                result          TEXT,
                error_message   TEXT,
                retry_count     INTEGER DEFAULT 0,
                max_retries     INTEGER DEFAULT 3,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at      TIMESTAMP,
                completed_at    TIMESTAMP
            )
        """)

        # ── meetings ──────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meetings (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id           INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                calendar_event_id   TEXT UNIQUE,
                title               TEXT,
                start_at            TIMESTAMP,
                end_at              TIMESTAMP,
                meet_link           TEXT,
                status              TEXT DEFAULT 'scheduled',
                transcript          TEXT,
                summary             TEXT,
                requirements        TEXT,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── budgets ───────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS budgets (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id       INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                meeting_id      INTEGER REFERENCES meetings(id),
                items           TEXT,
                total_amount    REAL,
                status          TEXT DEFAULT 'draft',
                notes           TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent_at         TIMESTAMP
            )
        """)

        # ── tasks ─────────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id       INTEGER REFERENCES businesses(id),
                title           TEXT NOT NULL,
                description     TEXT,
                priority        TEXT DEFAULT 'medium',
                status          TEXT DEFAULT 'todo',
                assignee        TEXT,
                deadline        TIMESTAMP,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_progress_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                user_id      INTEGER,
                lead_id      INTEGER REFERENCES businesses(id) ON DELETE SET NULL,
                lead_name    TEXT,
                event_type   TEXT NOT NULL,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── client_info ───────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS client_info (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id       INTEGER NOT NULL UNIQUE REFERENCES businesses(id) ON DELETE CASCADE,
                lead_name       TEXT,
                business_name   TEXT,
                rubro           TEXT,
                budget_range    TEXT,
                colors          TEXT,
                needs           TEXT,
                instagram       TEXT,
                web             TEXT,
                meeting_time    TEXT,
                meeting_url     TEXT,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── pitch_templates ───────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pitch_templates (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                category_group  TEXT NOT NULL,
                content         TEXT NOT NULL,
                is_active       INTEGER DEFAULT 1,
                usage_count     INTEGER DEFAULT 0,
                last_used_at    TIMESTAMP,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── lead_attachments ──────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lead_attachments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id     INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                section     TEXT NOT NULL,
                name        TEXT NOT NULL,
                url         TEXT,
                file_data   BLOB,
                mime_type   TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── lead_events ───────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lead_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id     INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                new_status  TEXT NOT NULL,
                note        TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _add_column(conn, "businesses", "last_event_at", "TIMESTAMP")
        _add_column(conn, "businesses", "score", "INTEGER")
        _add_column(conn, "meetings", "recall_bot_id", "TEXT")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS wa_templates (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                body        TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS call_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id     INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                called_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                outcome     TEXT NOT NULL,
                notes       TEXT,
                created_by  TEXT DEFAULT 'sistema'
            )
        """)
        _add_column(conn, "lead_events", "created_by", "TEXT DEFAULT 'sistema'")

        # ── users ─────────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                email       TEXT NOT NULL UNIQUE,
                phone       TEXT NOT NULL,
                password    TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)

        # Estas dos van aca y no arriba: _add_column es un ALTER, y sobre una base
        # nueva la tabla users todavia no existe, asi que fallaba en silencio y las
        # columnas nunca se creaban.
        _add_column(conn, "users", "panel_access", "TEXT")
        _add_column(conn, "users", "role_id", "INTEGER REFERENCES roles(id) ON DELETE SET NULL")

        # ── password_reset_tokens ─────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token       TEXT NOT NULL UNIQUE,
                created_at  TEXT NOT NULL,
                used_at     TEXT
            )
        """)

        # ── activity_log ──────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                user_name   TEXT NOT NULL DEFAULT 'sistema',
                action      TEXT NOT NULL,
                entity_type TEXT,
                entity_id   INTEGER,
                entity_name TEXT,
                detail      TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── task assignment & goal tracking ────────────────────────────────────
        _add_column(conn, "tasks", "assignee_id",    "INTEGER REFERENCES users(id) ON DELETE SET NULL")
        _add_column(conn, "tasks", "assignee_name",  "TEXT")
        _add_column(conn, "tasks", "assignee_email", "TEXT")
        _add_column(conn, "tasks", "created_by_id",  "INTEGER")
        _add_column(conn, "tasks", "created_by_name","TEXT")
        _add_column(conn, "tasks", "goal",           "INTEGER")
        _add_column(conn, "tasks", "progress",       "INTEGER DEFAULT 0")
        _add_column(conn, "tasks", "goal_type",      "TEXT")

        # Backfill scores for leads that were scraped before scoring was added
        conn.execute("""
            UPDATE businesses SET score = (
                COALESCE((CASE WHEN instagram_url IS NOT NULL AND instagram_url != '' THEN 35 ELSE 0 END), 0) +
                COALESCE((CASE WHEN facebook_url  IS NOT NULL AND facebook_url  != '' THEN 15 ELSE 0 END), 0) +
                COALESCE((CASE WHEN CAST(rating AS REAL) >= 4.0 THEN 20
                               WHEN CAST(rating AS REAL) >= 3.5 THEN 10 ELSE 0 END), 0) +
                COALESCE((CASE WHEN CAST(review_count AS INTEGER) >= 20 THEN 15
                               WHEN CAST(review_count AS INTEGER) >= 5  THEN 8  ELSE 0 END), 0) +
                COALESCE((CASE WHEN hours   IS NOT NULL AND hours   != '' THEN 10 ELSE 0 END), 0) +
                COALESCE((CASE WHEN address IS NOT NULL AND address != '' THEN 5  ELSE 0 END), 0)
            )
            WHERE score IS NULL
        """)
        conn.commit()

        # Migrate contactado → interesado (idempotent)
        conn.execute("UPDATE businesses SET crm_status = 'interesado' WHERE crm_status = 'contactado'")
        conn.commit()

        # Remove duplicate phone rows before creating unique index (keeps oldest row)
        try:
            conn.execute("""
                DELETE FROM businesses
                WHERE phone IS NOT NULL
                  AND id NOT IN (
                      SELECT MIN(id) FROM businesses
                      WHERE phone IS NOT NULL
                      GROUP BY phone
                  )
            """)
            # Remove duplicate null-phone meta leads by (name, date)
            conn.execute("""
                DELETE FROM businesses
                WHERE source = 'meta' AND phone IS NULL
                  AND id NOT IN (
                      SELECT MIN(id) FROM businesses
                      WHERE source = 'meta' AND phone IS NULL
                      GROUP BY name, SUBSTR(COALESCE(scraped_at, ''), 1, 10)
                  )
            """)
            conn.commit()
        except Exception as e:
            logger.warning(f"Dedup migration: {e}")

        # Partial unique index on phone — prevents future duplicates, allows multiple NULLs
        try:
            conn.execute("DROP INDEX IF EXISTS idx_businesses_phone")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_businesses_phone ON businesses(phone) WHERE phone IS NOT NULL")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_businesses_crm_status ON businesses(crm_status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_businesses_score ON businesses(score)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_businesses_category ON businesses(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prt_user_id ON password_reset_tokens(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_log_time ON activity_log(created_at)")
            conn.commit()
        except Exception as e:
            logger.warning(f"Index creation: {e}")
    finally:
        conn.close()


# ─── Businesses (existing API, preserved) ────────────────────────────────────

ALLOWED_COLUMNS = {
    "name", "category", "address", "city", "phone", "email", "rating",
    "review_count", "hours", "maps_url", "facebook_url", "instagram_url",
    "color_scheme", "demo_html_path", "demo_url", "status", "error_message",
    "scraped_at", "notes", "pitch_text", "crm_status",
    "has_whatsapp", "last_event_at", "score", "callback_date", "source", "form_data",
}


def insert_business(db_path: str, data: dict) -> Optional[int]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO businesses
            (name, category, address, city, phone, rating, review_count,
             hours, maps_url, facebook_url, instagram_url,
             color_scheme, demo_html_path, demo_url, status, has_whatsapp, score, source, notes, form_data, scraped_at)
            VALUES (:name, :category, :address, :city, :phone, :rating,
                    :review_count, :hours, :maps_url, :facebook_url, :instagram_url,
                    :color_scheme, :demo_html_path, :demo_url, 'scraped', :has_whatsapp, :score, :source, :notes, :form_data,
                    COALESCE(:scraped_at, CURRENT_TIMESTAMP))
        """, {
            "name": data.get("name"),
            "category": data.get("category"),
            "address": data.get("address"),
            "city": data.get("city"),
            "phone": data.get("phone"),
            "rating": data.get("rating"),
            "review_count": data.get("review_count"),
            "hours": data.get("hours"),
            "maps_url": data.get("maps_url"),
            "facebook_url": data.get("facebook_url"),
            "instagram_url": data.get("instagram_url"),
            "color_scheme": data.get("color_scheme"),
            "demo_html_path": data.get("demo_html_path"),
            "demo_url": data.get("demo_url"),
            "has_whatsapp": data.get("has_whatsapp"),
            "score": data.get("score"),
            "source": data.get("source"),
            "notes": data.get("notes"),
            "form_data": data.get("form_data"),
            "scraped_at": data.get("scraped_at"),
        })
        conn.commit()
        return cursor.lastrowid if cursor.rowcount > 0 else None
    finally:
        conn.close()


def update_business(db_path: str, business_id: int, **fields) -> None:
    if not fields:
        return
    invalid = set(fields) - ALLOWED_COLUMNS
    if invalid:
        raise ValueError(f"Invalid column names: {invalid}")
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = business_id
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE businesses SET {set_clause} WHERE id = :id", fields)
        conn.commit()
    finally:
        conn.close()


def get_businesses_by_status(db_path: str, status: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT * FROM businesses WHERE status = ?", (status,)
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_all_businesses(db_path: str, crm_status: str | None = None, crm_statuses: list | None = None, source: str | None = None) -> list[dict]:
    conn = _connect(db_path)
    try:
        count_sql = (
            "(SELECT COUNT(*) FROM call_logs cl WHERE cl.lead_id = b.id AND cl.outcome = 'no_contestó') as no_contesto_count, "
            "(SELECT COUNT(*) FROM call_logs cl WHERE cl.lead_id = b.id AND cl.outcome = 'no_interesa') as no_interesa_count"
        )
        select = f"SELECT b.*, {count_sql} FROM businesses b"
        if source:
            where = "WHERE b.source = ?"
            params: list = [source]
        elif crm_statuses:
            placeholders = ",".join("?" * len(crm_statuses))
            where = f"WHERE b.crm_status IN ({placeholders})"
            params: list = list(crm_statuses)
        elif crm_status == "sin_contactar":
            where = "WHERE (b.crm_status IS NULL OR b.crm_status = ?) AND (b.source IS NULL OR b.source != 'meta')"
            params = ["sin_contactar"]
        elif crm_status == "llamar_despues":
            cursor = conn.execute(
                f"{select} WHERE b.crm_status = ? ORDER BY CASE WHEN b.callback_date IS NULL THEN 1 ELSE 0 END, b.callback_date ASC",
                ["llamar_despues"],
            )
            return [dict(row) for row in cursor.fetchall()]
        elif crm_status:
            where = f"WHERE b.crm_status = ?"
            params = [crm_status]
        else:
            where = ""
            params = []
        cursor = conn.execute(
            f"{select} {where} ORDER BY CASE WHEN b.score IS NULL THEN 1 ELSE 0 END, b.score DESC, b.scraped_at DESC",
            params,
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_business(db_path: str, business_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute("SELECT * FROM businesses WHERE id = ?", (business_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_business_by_phone(db_path: str, phone: str) -> Optional[dict]:
    digits = re.sub(r"[^\d]", "", phone)
    variants = [phone, digits, "+" + digits]
    if digits.startswith("598") and len(digits) == 11:
        variants.append("0" + digits[3:])
    conn = _connect(db_path)
    try:
        for v in variants:
            row = conn.execute("SELECT * FROM businesses WHERE phone = ?", (v,)).fetchone()
            if row:
                return dict(row)
        return None
    finally:
        conn.close()


def delete_business(db_path: str, business_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM businesses WHERE id = ?", (business_id,))
        conn.commit()
    finally:
        conn.close()


def merge_business(db_path: str, source_id: int, target_id: int) -> None:
    """Transfer all relations from source_id to target_id, then delete source."""
    conn = _connect(db_path)
    try:
        for table, col in [
            ("meetings",         "client_id"),
            ("demos",            "client_id"),
            ("budgets",          "client_id"),
            ("tasks",            "client_id"),
            ("lead_attachments", "lead_id"),
            ("lead_events",      "lead_id"),
            ("call_logs",        "lead_id"),
        ]:
            conn.execute(
                f"UPDATE {table} SET {col} = ? WHERE {col} = ?",
                (target_id, source_id),
            )

        # client_info has UNIQUE on client_id — only transfer if target has none
        has_ci = conn.execute(
            "SELECT 1 FROM client_info WHERE client_id = ?", (target_id,)
        ).fetchone()
        if not has_ci:
            conn.execute(
                "UPDATE client_info SET client_id = ? WHERE client_id = ?",
                (target_id, source_id),
            )

        conn.execute("DELETE FROM businesses WHERE id = ?", (source_id,))
        conn.commit()
    finally:
        conn.close()


# ─── Demos ────────────────────────────────────────────────────────────────────

_DEMO_COLUMNS = {"status", "html_path", "url", "generated_at", "generated_by", "error_message"}


def get_demo_for_client(db_path: str, client_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT * FROM demos WHERE client_id = ?", (client_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_demo(db_path: str, client_id: int) -> int:
    """Raises sqlite3.IntegrityError if demo already exists for this client."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO demos (client_id, status) VALUES (?, 'pending')",
            (client_id,)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_demo(db_path: str, demo_id: int, **fields) -> None:
    invalid = set(fields) - _DEMO_COLUMNS
    if invalid:
        raise ValueError(f"Invalid demo columns: {invalid}")
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = demo_id
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE demos SET {set_clause} WHERE id = :id", fields)
        conn.commit()
    finally:
        conn.close()


# ─── Jobs ─────────────────────────────────────────────────────────────────────

_JOB_COLUMNS = {"status", "result", "error_message", "retry_count", "started_at", "completed_at"}


def create_job(db_path: str, job_type: str, payload: str, max_retries: int = 3) -> int:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO jobs (type, payload, max_retries) VALUES (?, ?, ?)",
            (job_type, payload, max_retries)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_next_pending_job(db_path: str) -> Optional[dict]:
    """Atomically claims the next pending job by marking it as running."""
    conn = _connect(db_path)
    try:
        # Recover stale running jobs (>5 min) back to pending
        conn.execute("""
            UPDATE jobs SET status = 'pending'
            WHERE status = 'running'
              AND started_at < datetime('now', '-5 minutes')
              AND retry_count < max_retries
        """)
        # Claim next pending job
        conn.execute("""
            UPDATE jobs SET status = 'running', started_at = CURRENT_TIMESTAMP
            WHERE id = (
                SELECT id FROM jobs
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
            )
        """)
        conn.commit()
        cursor = conn.execute(
            "SELECT * FROM jobs WHERE status = 'running' ORDER BY started_at DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_job(db_path: str, job_id: int, **fields) -> None:
    allowed = _JOB_COLUMNS | {"status"}
    invalid = set(fields) - allowed
    if invalid:
        raise ValueError(f"Invalid job columns: {invalid}")
    if not fields:
        return
    if fields.get("status") == "completed":
        fields.setdefault("completed_at", "CURRENT_TIMESTAMP")
    set_clause = ", ".join(
        f"{k} = {v}" if v == "CURRENT_TIMESTAMP" else f"{k} = :{k}"
        for k, v in fields.items()
    )
    filtered = {k: v for k, v in fields.items() if v != "CURRENT_TIMESTAMP"}
    filtered["id"] = job_id
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = :id", filtered)
        conn.commit()
    finally:
        conn.close()


def get_job(db_path: str, job_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ─── Meetings ─────────────────────────────────────────────────────────────────

_MEETING_COLUMNS = {
    "calendar_event_id", "title", "start_at", "end_at", "meet_link",
    "status", "transcript", "summary", "requirements", "recall_bot_id",
}


def create_meeting(db_path: str, client_id: int, **fields) -> int:
    allowed_fields = {k: v for k, v in fields.items() if k in _MEETING_COLUMNS}
    cols = ["client_id"] + list(allowed_fields)
    vals = [client_id] + list(allowed_fields.values())
    placeholders = ", ".join("?" for _ in vals)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            f"INSERT INTO meetings ({', '.join(cols)}) VALUES ({placeholders})", vals
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_meeting(db_path: str, meeting_id: int, **fields) -> None:
    invalid = set(fields) - _MEETING_COLUMNS
    if invalid:
        raise ValueError(f"Invalid meeting columns: {invalid}")
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = meeting_id
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE meetings SET {set_clause} WHERE id = :id", fields)
        conn.commit()
    finally:
        conn.close()


def get_meetings_for_client(db_path: str, client_id: int) -> list[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT * FROM meetings WHERE client_id = ? ORDER BY start_at DESC",
            (client_id,)
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_meeting_by_calendar_id(db_path: str, calendar_event_id: str) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT * FROM meetings WHERE calendar_event_id = ?", (calendar_event_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_meeting(db_path: str, meeting_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_meeting(db_path: str, meeting_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        conn.commit()
    finally:
        conn.close()


# ─── Budgets ──────────────────────────────────────────────────────────────────

_BUDGET_COLUMNS = {"items", "total_amount", "status", "notes", "sent_at", "meeting_id"}


def create_budget(db_path: str, client_id: int, **fields) -> int:
    allowed = {k: v for k, v in fields.items() if k in _BUDGET_COLUMNS}
    cols = ["client_id"] + list(allowed)
    vals = [client_id] + list(allowed.values())
    placeholders = ", ".join("?" for _ in vals)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            f"INSERT INTO budgets ({', '.join(cols)}) VALUES ({placeholders})", vals
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_budget(db_path: str, budget_id: int, **fields) -> None:
    invalid = set(fields) - _BUDGET_COLUMNS
    if invalid:
        raise ValueError(f"Invalid budget columns: {invalid}")
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = budget_id
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE budgets SET {set_clause} WHERE id = :id", fields)
        conn.commit()
    finally:
        conn.close()


def get_budget_by_id(db_path: str, budget_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM budgets WHERE id = ?", (budget_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_budget_for_client(db_path: str, client_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT * FROM budgets WHERE client_id = ? ORDER BY created_at DESC LIMIT 1",
            (client_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ─── Tasks ────────────────────────────────────────────────────────────────────

_TASK_COLUMNS = {
    "client_id", "title", "description", "priority", "status", "assignee", "deadline",
    "assignee_id", "assignee_name", "assignee_email",
    "created_by_id", "created_by_name",
    "goal", "progress", "goal_type",
}


def create_task(db_path: str, **fields) -> int:
    allowed = {k: v for k, v in fields.items() if k in _TASK_COLUMNS}
    if "title" not in allowed:
        raise ValueError("title is required for tasks")
    cols = list(allowed)
    vals = list(allowed.values())
    placeholders = ", ".join("?" for _ in vals)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            f"INSERT INTO tasks ({', '.join(cols)}) VALUES ({placeholders})", vals
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_task(db_path: str, task_id: int, **fields) -> None:
    invalid = set(fields) - _TASK_COLUMNS
    if invalid:
        raise ValueError(f"Invalid task columns: {invalid}")
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = task_id
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = :id", fields)
        conn.commit()
    finally:
        conn.close()


def get_tasks(db_path: str, client_id: Optional[int] = None, status: Optional[str] = None) -> list[dict]:
    where_parts = []
    params: list = []
    if client_id is not None:
        where_parts.append("client_id = ?")
        params.append(client_id)
    if status is not None:
        where_parts.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            f"SELECT * FROM tasks {where} ORDER BY created_at DESC", params
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_lead_contributor_ids(db_path: str, lead_id: int) -> list[int]:
    """Return unique user_ids from activity_log for a given lead (excludes NULLs)."""
    if not lead_id:
        return []
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT user_id FROM activity_log "
            "WHERE entity_id = ? AND entity_type = 'lead' AND user_id IS NOT NULL",
            (lead_id,),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def increment_task_progress(
    db_path: str,
    user_ids: list[int],
    goal_type: str,
    lead_id: int | None = None,
    lead_name: str = "",
) -> list[int]:
    """Increment progress for all user_ids on tasks matching goal_type.
    Logs each increment to task_progress_events. Returns newly completed task IDs."""
    unique_ids = list({uid for uid in (user_ids or []) if uid})
    if not unique_ids:
        return []
    conn = _connect(db_path)
    all_completed: list[int] = []
    try:
        for uid in unique_ids:
            conn.execute(
                "UPDATE tasks SET progress = COALESCE(progress, 0) + 1 "
                "WHERE assignee_id = ? AND goal_type = ? AND status != 'done'",
                (uid, goal_type),
            )
            active = conn.execute(
                "SELECT id FROM tasks WHERE assignee_id = ? AND goal_type = ? AND status != 'done'",
                (uid, goal_type),
            ).fetchall()
            for row in active:
                conn.execute(
                    "INSERT INTO task_progress_events "
                    "(task_id, user_id, lead_id, lead_name, event_type) VALUES (?, ?, ?, ?, ?)",
                    (row[0], uid, lead_id, lead_name or "", goal_type),
                )
            completed = [
                r[0] for r in conn.execute(
                    "SELECT id FROM tasks WHERE assignee_id = ? AND goal_type = ? "
                    "AND goal IS NOT NULL AND COALESCE(progress, 0) >= goal AND status != 'done'",
                    (uid, goal_type),
                ).fetchall()
            ]
            if completed:
                conn.execute(
                    f"UPDATE tasks SET status = 'done' "
                    f"WHERE id IN ({','.join('?' * len(completed))})",
                    completed,
                )
            all_completed.extend(completed)
        conn.commit()
        return all_completed
    finally:
        conn.close()


def get_task_by_id(db_path: str, task_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_task(db_path: str, task_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()


def get_task_progress_history(db_path: str, task_id: int, limit: int = 50) -> list[dict]:
    """Return the last `limit` progress events for a task, newest first."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM task_progress_events WHERE task_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── Client info ──────────────────────────────────────────────────────────────

_CLIENT_INFO_COLUMNS = {
    "lead_name", "business_name", "rubro", "budget_range",
    "colors", "needs", "instagram", "web", "meeting_time", "meeting_url",
}


def get_client_info(db_path: str, client_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT * FROM client_info WHERE client_id = ?", (client_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_client_info(db_path: str, client_id: int, **fields) -> None:
    allowed = {k: v for k, v in fields.items() if k in _CLIENT_INFO_COLUMNS}
    if not allowed:
        return
    conn = _connect(db_path)
    try:
        existing = conn.execute(
            "SELECT id FROM client_info WHERE client_id = ?", (client_id,)
        ).fetchone()
        if existing:
            set_clause = ", ".join(f"{k} = :{k}" for k in allowed)
            allowed["client_id"] = client_id
            conn.execute(
                f"UPDATE client_info SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE client_id = :client_id",
                allowed,
            )
        else:
            cols = ["client_id"] + list(allowed)
            vals = {"client_id": client_id, **allowed}
            placeholders = ", ".join(f":{c}" for c in cols)
            conn.execute(
                f"INSERT INTO client_info ({', '.join(cols)}) VALUES ({placeholders})", vals
            )
        conn.commit()
    finally:
        conn.close()


# ─── Pitch templates ──────────────────────────────────────────────────────────

def get_pitch_templates(db_path: str, category_group: Optional[str] = None) -> list[dict]:
    conn = _connect(db_path)
    try:
        if category_group:
            cursor = conn.execute(
                "SELECT * FROM pitch_templates WHERE is_active = 1 AND category_group = ? ORDER BY usage_count ASC",
                (category_group,)
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM pitch_templates WHERE is_active = 1 ORDER BY usage_count ASC"
            )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def increment_template_usage(db_path: str, template_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE pitch_templates SET usage_count = usage_count + 1, last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
            (template_id,)
        )
        conn.commit()
    finally:
        conn.close()


# ─── Attachments ─────────────────────────────────────────────────────────────

def add_attachment(db_path: str, lead_id: int, section: str, name: str,
                   url: str = None, file_data: bytes = None, mime_type: str = None) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO lead_attachments (lead_id, section, name, url, file_data, mime_type) VALUES (?,?,?,?,?,?)",
            (lead_id, section, name, url, file_data, mime_type)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_attachments(db_path: str, lead_id: int, section: str) -> list:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, name, url, mime_type, created_at, (file_data IS NOT NULL) AS has_file "
            "FROM lead_attachments WHERE lead_id=? AND section=? ORDER BY created_at",
            (lead_id, section)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_attachment_file(db_path: str, attach_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT name, file_data, mime_type FROM lead_attachments WHERE id=?", (attach_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


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


def delete_attachment(db_path: str, attach_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM lead_attachments WHERE id=?", (attach_id,))
        conn.commit()
    finally:
        conn.close()


def seed_pitch_templates(db_path: str) -> None:
    """Inserts default pitch templates if the table is empty."""
    conn = _connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM pitch_templates").fetchone()[0]
        if count > 0:
            return

        templates = [
            ("gym", "Buenas, ¿cómo andás? Soy de Scalerics, una software factory uruguaya.\n\nVimos que {name} no tiene página web propia — con {rating} estrellas en Google, una web te ayudaría a convertir más búsquedas en socios nuevos. ¿Te interesaría que charlemos unos minutos?"),
            ("gym", "Hola! Te escribo de Scalerics. Vi que {name} en {city} tiene {review_count} reseñas en Google pero sin web propia.\n\nTenemos una propuesta concreta para que captures más clientes sin esfuerzo extra. ¿5 minutos para contártelo?"),
            ("peluqueria", "Buenas! Soy Juan de Scalerics. Vi que {name} tiene {rating}★ en Google pero sin web propia.\n\nUna página te ayudaría a que la gente reserve turno directo, sin llamar. ¿Te interesa que te mostremos cómo?"),
            ("peluqueria", "Hola! Te contacto de Scalerics. {name} aparece bien posicionado en Google con {review_count} reseñas.\n\nCon una web propia podrías recibir reservas 24/7 sin intermediarios. ¿Charlamos?"),
            ("restaurante", "Buenas! De parte de Scalerics. Vimos que {name} tiene muy buena reputación en Google ({rating}★) pero sin web propia.\n\nUna web aumenta las reservas directas y evita las comisiones de las apps de delivery. ¿Te interesa?"),
            ("restaurante", "Hola! Soy de Scalerics. {name} tiene {review_count} opiniones en Google — eso es tráfico que podrías convertir en reservas directas con una web.\n\n¿Tenés 5 minutos para que te mostremos cómo?"),
            ("bar", "Buenas! Te escribo de Scalerics. Vi que {name} aparece en Google con {rating}★ pero sin web propia.\n\nCon una web mostrás la carta, los eventos y el horario — y la gente llega más preparada. ¿Lo charlamos?"),
            ("panaderia", "Hola! De Scalerics. {name} tiene muy buena presencia en Google con {review_count} reseñas.\n\nCon una web podés mostrar tu carta del día y recibir pedidos anticipados. ¿Hablamos?"),
            ("panaderia", "Buenas! Soy de Scalerics. Vi que {name} en {city} tiene clientes fieles según Google.\n\nUna web sencilla te ayuda a llegar a nuevos clientes del barrio que buscan panaderías cerca. ¿Te cuento más?"),
            ("delivery", "Hola! Te escribo de Scalerics. {name} aparece en Google pero sin web propia.\n\nTener web propia significa recibir pedidos sin pagarle comisión a las apps. ¿Lo analizamos juntos?"),
            ("salud", "Buenas! De Scalerics. {name} tiene {rating}★ en Google — excelente reputación.\n\nCon una web tus pacientes pueden pedir turno online las 24hs. ¿Te mostramos cómo?"),
            ("hotel", "Hola! Soy de Scalerics. Vi que {name} tiene {review_count} reseñas en Google pero sin web propia.\n\nCon web propia recibís reservas directas sin comisión de Booking o Airbnb. ¿Charlamos?"),
            ("ferreteria", "Buenas! De Scalerics. {name} en {city} tiene buena presencia en Google pero sin web.\n\nUna web con catálogo de productos ayuda a que los clientes encuentren lo que buscan antes de venir. ¿Te interesa?"),
            ("veterinaria", "Hola! Te escribo de Scalerics. {name} tiene {rating}★ en Google — tus clientes te valoran.\n\nCon una web podés mostrar servicios, precios y agendar turnos online. ¿Lo vemos?"),
            ("default", "Buenas! Soy de Scalerics, agencia web uruguaya. Vi que {name} en {city} tiene {rating}★ en Google pero sin sitio web propio.\n\nUna web profesional te ayuda a conseguir más clientes. ¿Te interesa que te mostremos una demo gratuita?"),
            ("default", "Hola! Te contacto de Scalerics. {name} aparece en Google con {review_count} opiniones pero sin página web.\n\nPreparamos demos personalizadas sin costo para que veas cómo quedaría tu sitio. ¿Charlamos?"),
        ]

        conn.executemany(
            "INSERT INTO pitch_templates (category_group, content) VALUES (?, ?)",
            templates
        )
        conn.commit()
        logger.info(f"Seeded {len(templates)} pitch templates")
    finally:
        conn.close()


# ─── Lead events ──────────────────────────────────────────────────────────────

def add_lead_event(db_path: str, lead_id: int, new_status: str, note: str = "", created_by: str = "sistema") -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO lead_events (lead_id, new_status, note, created_by) VALUES (?, ?, ?, ?)",
            (lead_id, new_status, note or "", created_by),
        )
        conn.execute(
            "UPDATE businesses SET last_event_at = CURRENT_TIMESTAMP WHERE id = ?",
            (lead_id,),
        )
        conn.commit()
    finally:
        conn.close()


def get_lead_events(db_path: str, lead_id: int) -> list[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT id, new_status, note, created_at, created_by FROM lead_events WHERE lead_id = ? ORDER BY created_at DESC",
            (lead_id,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


# ─── Activity log ─────────────────────────────────────────────────────────────

def log_activity(db_path: str, user_name: str, action: str,
                 entity_type: str = "", entity_id: int = None,
                 entity_name: str = "", detail: str = "", user_id: int = None) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO activity_log (user_id, user_name, action, entity_type, entity_id, entity_name, detail) VALUES (?,?,?,?,?,?,?)",
            (user_id, user_name or "sistema", action, entity_type or "", entity_id, entity_name or "", detail or ""),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def get_activity_feed(db_path: str, limit: int = 60) -> list[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT id, user_name, action, entity_type, entity_id, entity_name, detail, created_at FROM activity_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


# ─── WA Templates ─────────────────────────────────────────────────────────────

def get_wa_templates(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute("SELECT * FROM wa_templates ORDER BY created_at ASC")
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def create_wa_template(db_path: str, name: str, body: str) -> int:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO wa_templates (name, body) VALUES (?, ?)", (name, body)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def delete_wa_template(db_path: str, template_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM wa_templates WHERE id = ?", (template_id,))
        conn.commit()
    finally:
        conn.close()


# ─── Call Logs ────────────────────────────────────────────────────────────────

def add_call_log(db_path: str, lead_id: int, outcome: str, notes: str = "", created_by: str = "sistema") -> int:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO call_logs (lead_id, outcome, notes, created_by) VALUES (?, ?, ?, ?)",
            (lead_id, outcome, notes or "", created_by),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_call_logs(db_path: str, lead_id: int) -> list[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT * FROM call_logs WHERE lead_id = ? ORDER BY called_at DESC",
            (lead_id,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


# ─── Users ────────────────────────────────────────────────────────────────────

def create_user(db_path: str, name: str, email: str, phone: str, password_hash: str) -> Optional[int]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO users (name, email, phone, password, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, email, phone, password_hash, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cursor.lastrowid if cursor.rowcount > 0 else None
    finally:
        conn.close()


def get_user_by_email(db_path: str, email: str) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(db_path: str, user_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_users(db_path: str) -> list[dict]:  # noqa: E302
    conn = _connect(db_path)
    try:
        cursor = conn.execute("SELECT u.id, u.name, u.email, u.phone, u.created_at, u.panel_access, u.role_id, r.name as role_name, r.panel_access as role_panels FROM users u LEFT JOIN roles r ON u.role_id=r.id ORDER BY u.created_at ASC")
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def delete_user(db_path: str, user_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def update_user_password(db_path: str, user_id: int, password_hash: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE users SET password = ? WHERE id = ?", (password_hash, user_id))
        conn.commit()
    finally:
        conn.close()

def update_user_profile(db_path: str, user_id: int, name: str, email: str, phone: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE users SET name=?, email=?, phone=? WHERE id=?",
            (name, email, phone, user_id),
        )
        conn.commit()
    finally:
        conn.close()


# ─── Password reset tokens ────────────────────────────────────────────────────

def create_reset_token(db_path: str, user_id: int, token: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO password_reset_tokens (user_id, token, created_at) VALUES (?, ?, ?)",
            (user_id, token, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_reset_token(db_path: str, token: str) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM password_reset_tokens WHERE token = ?", (token,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def use_reset_token(db_path: str, token: str) -> bool:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE token = ?",
            (datetime.now(timezone.utc).isoformat(), token),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
