import sqlite3
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS businesses (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                category        TEXT,
                address         TEXT,
                city            TEXT,
                phone           TEXT,
                email           TEXT,
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
        try:
            conn.execute("ALTER TABLE businesses ADD COLUMN notes TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE businesses ADD COLUMN pitch_text TEXT")
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()

def insert_business(db_path: str, data: dict) -> Optional[int]:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO businesses
            (name, category, address, city, phone, rating, review_count,
             hours, maps_url, facebook_url, instagram_url, email,
             color_scheme, demo_html_path, demo_url, status)
            VALUES (:name, :category, :address, :city, :phone, :rating,
                    :review_count, :hours, :maps_url, :facebook_url, :instagram_url,
                    :email, :color_scheme, :demo_html_path, :demo_url, 'scraped')
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
            "email": data.get("email"),
            "color_scheme": data.get("color_scheme"),
            "demo_html_path": data.get("demo_html_path"),
            "demo_url": data.get("demo_url"),
        })
        conn.commit()
        return cursor.lastrowid if cursor.rowcount > 0 else None
    finally:
        conn.close()

ALLOWED_COLUMNS = {
    "name", "category", "address", "city", "phone", "email", "rating",
    "review_count", "hours", "maps_url", "facebook_url", "instagram_url",
    "color_scheme", "demo_html_path", "demo_url", "status", "error_message",
    "scraped_at", "email_sent_at", "notes", "pitch_text",
}

def update_business(db_path: str, business_id: int, **fields) -> None:
    if not fields:
        return
    invalid = set(fields) - ALLOWED_COLUMNS
    if invalid:
        raise ValueError(f"Invalid column names: {invalid}")
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = business_id
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"UPDATE businesses SET {set_clause} WHERE id = :id", fields)
        conn.commit()
    finally:
        conn.close()

def get_businesses_by_status(db_path: str, status: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "SELECT * FROM businesses WHERE status = ?", (status,)
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def get_all_businesses(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute("SELECT * FROM businesses ORDER BY scraped_at DESC")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
