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


def _grant_panel_to_existing_roles(conn: sqlite3.Connection, panel: str) -> int:
    """Suma `panel` al panel_access de los roles que ya existen y no lo tengan.

    La siembra de roles por defecto solo corre con la tabla vacía, así que en
    una base que ya tiene roles (producción) un panel nuevo no le llega a
    nadie salvo a los admin, que reciben todos. Esto lo arregla en el arranque.

    Idempotente: si el panel ya está, no toca la fila. Un `panel_access` en
    NULL, vacío, con JSON inválido o con un JSON que no es una lista se saltea
    con un warning — no se pisa lo que no se entiende. Devuelve cuántas filas
    modificó.
    """
    import json as _j
    tocadas = 0
    try:
        filas = conn.execute("SELECT id, panel_access FROM roles").fetchall()
    except sqlite3.Error as e:
        logger.warning(f"panel_access migration: no se pudo leer roles ({e})")
        return 0

    for fila in filas:
        rid, crudo = fila[0], fila[1]
        try:
            paneles = _j.loads(crudo) if crudo else None
        except (ValueError, TypeError):
            paneles = None
        if not isinstance(paneles, list):
            logger.warning(
                f"panel_access migration: rol id={rid} tiene un panel_access "
                f"ilegible, se deja como está"
            )
            continue
        if panel in paneles:
            continue
        paneles.append(panel)
        try:
            conn.execute(
                "UPDATE roles SET panel_access = ? WHERE id = ?",
                (_j.dumps(paneles), rid),
            )
            tocadas += 1
        except sqlite3.Error as e:
            logger.warning(f"panel_access migration: rol id={rid} no se pudo actualizar ({e})")

    if tocadas:
        conn.commit()
        logger.info(f"panel_access migration: '{panel}' agregado a {tocadas} rol(es)")
    return tocadas


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
        # Qué servicio pidió el lead (web/Calendly). Aparte de `category`,
        # que es el rubro del negocio.
        _add_column(conn, "businesses", "interest", "TEXT")
        # El sitio web que Google Maps muestra para el negocio. Hasta la campana de
        # discovery el scraper lo extraia solo para descartar al negocio que lo
        # tenia, y no se guardaba en ningun lado.
        _add_column(conn, "businesses", "website", "TEXT")
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
        _grant_panel_to_existing_roles(conn, "meta")
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

        # ── projects ──────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                notion_page_id    TEXT UNIQUE,
                name              TEXT NOT NULL,
                stage             TEXT,
                timeline_start    TEXT,
                timeline_end      TEXT,
                lead              TEXT,
                notion_synced_at  TIMESTAMP
            )
        """)

        # ── notion_clients ────────────────────────────────────────────────────
        # Espejo de solo lectura de la database Clientes de Notion. Es aparte
        # de `businesses`: aca viven las fichas que el equipo maneja a mano en
        # el tablero, no los leads que junta el scraper.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notion_clients (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                notion_page_id         TEXT UNIQUE,
                name                   TEXT NOT NULL,
                status                 TEXT,
                descripcion            TEXT,
                due_date               TEXT,
                tiempo_estimado        REAL,
                notion_project_page_id TEXT,
                notion_synced_at       TIMESTAMP
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
        # Pareo con la database Tasks de Notion. `notion_status` guarda el
        # estado exacto que vimos alla la ultima vez (Backlog, Up next, On
        # Hold...), que es mas fino que los tres del CRM: es lo que nos permite
        # no pisarlo cuando el grupo no cambio.
        _add_column(conn, "tasks", "notion_page_id", "TEXT")
        _add_column(conn, "tasks", "notion_status", "TEXT")
        _add_column(conn, "tasks", "notion_synced_at", "TIMESTAMP")
        # A que proyecto de Notion pertenece la tarea. Se guarda el page id y no
        # el nombre: si el equipo renombra un proyecto, el pareo sobrevive.
        _add_column(conn, "tasks", "notion_project_page_id", "TEXT")
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

        # ── meta_token_alerts ─────────────────────────────────────────────────
        # Dedup del monitor de salud del token de Meta (routes/meta.py): sin
        # esto, cada ciclo de `_check_token_once` (cada 10 min) le manda un
        # mail a cada admin mientras el token siga vencido. Vive en la misma
        # base que `businesses` — montada en /data en Fly — para que el
        # silencio sobreviva a un restart o redeploy.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_token_alerts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_key     TEXT NOT NULL UNIQUE,
                detail        TEXT,
                last_sent_at  REAL NOT NULL
            )
        """)

        # ── meta_reminders ────────────────────────────────────────────────────
        # Una fila por CONTACTO, no por lead. El UNIQUE es (business_id, numero):
        # sigue siendo la base la que impide mandar dos veces el mismo contacto,
        # no una condicion en el codigo.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_reminders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id     INTEGER NOT NULL,
                numero          INTEGER NOT NULL DEFAULT 1,
                token           TEXT NOT NULL UNIQUE,
                sent_at         TEXT NOT NULL,
                unsubscribed_at TEXT,
                UNIQUE (business_id, numero)
            )
        """)

        # La campana de discovery lleva su propia tabla, con la misma forma. No
        # necesita migracion: nace asi, a diferencia de meta_reminders, que tuvo
        # que pasar de una fila por lead a una por contacto.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS corridas (
                nombre TEXT PRIMARY KEY,
                ultima TEXT NOT NULL
            )
        """)

        # Cuando corrio por ultima vez cada job diario. Los hilos arrancan
        # despues de CADA boot y Fly reinicia en cada deploy: sin esto, cinco
        # deploys en una tarde son cinco disparos.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mails_vedados (
                email      TEXT PRIMARY KEY,
                motivo     TEXT NOT NULL,
                detalle    TEXT,
                creado_at  TEXT NOT NULL
            )
        """)

        # Direcciones a las que no se les escribe mas: rebotes duros y quejas de
        # spam. Es UNA lista para las dos campanas: las dos salen de la misma
        # cuenta de Resend, y lo que rebota en una rebota en la otra.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS discovery_reminders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id     INTEGER NOT NULL,
                numero          INTEGER NOT NULL DEFAULT 1,
                token           TEXT NOT NULL UNIQUE,
                sent_at         TEXT NOT NULL,
                unsubscribed_at TEXT,
                UNIQUE (business_id, numero)
            )
        """)

        # Migracion de la tabla vieja, que tenia UNIQUE(business_id) y una sola
        # fila por lead. SQLite no deja quitar un UNIQUE: hay que reconstruir.
        # Las filas viejas son el contacto 1 y conservan su token, que esta
        # publicado dentro de mails que la gente ya recibio.
        columnas = [c[1] for c in conn.execute("PRAGMA table_info(meta_reminders)")]
        if "numero" not in columnas:
            # DROP...IF EXISTS, no CREATE...IF NOT EXISTS: el CREATE de aca abajo
            # se autocommitea solo (es DDL y todavia no hay ninguna transaccion
            # abierta), asi que un crash entre el DROP TABLE meta_reminders y el
            # commit final de mas abajo puede dejar esta tabla persistida y
            # huerfana. Si fuera IF NOT EXISTS, un reintento la dejaria como esta
            # y el INSERT...SELECT de abajo podria chocar contra su UNIQUE si esa
            # huerfana llegara a tener filas (a mano, o por un cambio futuro de
            # este bloque). Arrancar de cero es lo unico que deja la base sana en
            # todos los casos, no solo en el crash puntual que se pudo reproducir.
            conn.execute("DROP TABLE IF EXISTS meta_reminders_nueva")
            conn.execute("""
                CREATE TABLE meta_reminders_nueva (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_id     INTEGER NOT NULL,
                    numero          INTEGER NOT NULL DEFAULT 1,
                    token           TEXT NOT NULL UNIQUE,
                    sent_at         TEXT NOT NULL,
                    unsubscribed_at TEXT,
                    UNIQUE (business_id, numero)
                )
            """)
            conn.execute("""
                INSERT INTO meta_reminders_nueva
                       (id, business_id, numero, token, sent_at, unsubscribed_at)
                SELECT  id, business_id, 1,      token, sent_at, unsubscribed_at
                  FROM meta_reminders
            """)
            conn.execute("DROP TABLE meta_reminders")
            conn.execute("ALTER TABLE meta_reminders_nueva RENAME TO meta_reminders")

        # Segunda migracion: la secuencia dejo de ser una sola y paso a haber
        # una por estado del CRM, cada una con su propio contador. Sin la
        # columna `estado`, un lead que cambia de estado seguiria numerando
        # desde donde iba y recibiria el contacto 2 de una secuencia que nunca
        # empezo. Hay que reconstruir igual que arriba: el UNIQUE pasa de
        # (business_id, numero) a (business_id, estado, numero) y SQLite no deja
        # cambiarlo en el lugar.
        #
        # Las filas viejas se marcan 'sin_contactar' porque ESE era el estado de
        # esos leads cuando se les mando: hasta hoy el filtro no dejaba pasar
        # ningun otro. Conservan su token, que esta publicado dentro de mails
        # que la gente ya recibio y sigue siendo su unico link de baja.
        columnas = [c[1] for c in conn.execute("PRAGMA table_info(meta_reminders)")]
        if "estado" not in columnas:
            conn.execute("DROP TABLE IF EXISTS meta_reminders_estado")
            conn.execute("""
                CREATE TABLE meta_reminders_estado (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_id     INTEGER NOT NULL,
                    estado          TEXT NOT NULL DEFAULT 'sin_contactar',
                    numero          INTEGER NOT NULL DEFAULT 1,
                    token           TEXT NOT NULL UNIQUE,
                    sent_at         TEXT NOT NULL,
                    unsubscribed_at TEXT,
                    UNIQUE (business_id, estado, numero)
                )
            """)
            conn.execute("""
                INSERT INTO meta_reminders_estado
                       (id, business_id, estado,          numero, token, sent_at, unsubscribed_at)
                SELECT  id, business_id, 'sin_contactar', numero, token, sent_at, unsubscribed_at
                  FROM meta_reminders
            """)
            conn.execute("DROP TABLE meta_reminders")
            conn.execute("ALTER TABLE meta_reminders_estado RENAME TO meta_reminders")


        # ── LinkedIn ──────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS linkedin_posts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          INTEGER,
                lote            TEXT NOT NULL,
                tipo            TEXT NOT NULL,
                texto           TEXT NOT NULL,
                angulo          TEXT,
                fuente_tipo     TEXT NOT NULL,
                fuente_id       INTEGER,
                imagen_tipo     TEXT NOT NULL DEFAULT 'ninguna',
                imagen_spec     TEXT,
                fuente_desc     TEXT,
                aviso           TEXT,
                estado          TEXT NOT NULL DEFAULT 'generado',
                marcar_token    TEXT,
                creado_en       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                publicado_en    TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS linkedin_temas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo      TEXT NOT NULL UNIQUE,
                angulo      TEXT NOT NULL,
                usado_en    TIMESTAMP
            )
        """)
        # Los posts ya escritos. Reemplaza a la generacion por API en el
        # camino del cron: el texto y la frase de la tarjeta ya estan aca, asi
        # que la corrida de los martes y viernes no llama a ningun modelo.
        # UNIQUE(tema, angulo) hace que sembrar dos veces no duplique.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS linkedin_banco (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tema        TEXT NOT NULL,
                angulo      TEXT NOT NULL,
                texto       TEXT NOT NULL,
                frase       TEXT NOT NULL,
                usado_en    TIMESTAMP,
                UNIQUE (tema, angulo)
            )
        """)
        _add_column(conn, "businesses", "linkedin_ok", "INTEGER DEFAULT 0")

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
            # get_all_businesses cuenta llamadas con dos subconsultas
            # correlacionadas por fila. Sin este indice, cada request de la cola
            # escaneaba call_logs entero DOS VECES POR LEAD: con 6.199 filas y
            # 642 llamadas son ~8 millones de lecturas por pedido, en una CPU
            # compartida. Es lo que trababa el CRM al pasar los 6.000 leads.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_call_logs_lead ON call_logs(lead_id, outcome)")
            # Las secuencias y el sync de respuestas entran por business_id.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_reminders_business ON meta_reminders(business_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_lead_events_lead ON lead_events(lead_id)")
            # La cola filtra por source ademas de por estado.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_businesses_source ON businesses(source)")
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
    "has_whatsapp", "last_event_at", "score", "callback_date", "source",
    "interest", "form_data", "website",
}


def insert_business(db_path: str, data: dict) -> Optional[int]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO businesses
            (name, category, address, city, phone, email, website, rating, review_count,
             hours, maps_url, facebook_url, instagram_url,
             color_scheme, demo_html_path, demo_url, status, has_whatsapp, score, source, notes, form_data, scraped_at)
            VALUES (:name, :category, :address, :city, :phone, :email, :website, :rating,
                    :review_count, :hours, :maps_url, :facebook_url, :instagram_url,
                    :color_scheme, :demo_html_path, :demo_url, 'scraped', :has_whatsapp, :score, :source, :notes, :form_data,
                    COALESCE(:scraped_at, CURRENT_TIMESTAMP))
        """, {
            "name": data.get("name"),
            "category": data.get("category"),
            "address": data.get("address"),
            "city": data.get("city"),
            "phone": data.get("phone"),
            "email": data.get("email"),
            "website": data.get("website"),
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


# Las columnas que dibujan las listas del CRM. Traer `b.*` para la cola son
# 28 MB de objetos Python por request con 6.200 leads; estas diez son 10,7 MB,
# y paginadas de a 50, nada.
COLUMNAS_LISTA = ("id", "name", "phone", "category", "city", "notes",
                  "crm_status", "pitch_text", "callback_date", "scraped_at")


def listar_leads(db_path: str, crm_status: str | None = None, cohorte: str | None = None,
                 category: str | None = None, search: str | None = None,
                 page: int = 1, por_pagina: int = 50) -> dict:
    """Una pagina de la cola, con TODO resuelto en SQL.

    Nace de un incidente: el 28-8-2026 el CRM empezo a devolver 502 al pasar los
    6.000 leads. `get_all_businesses` traia las 6.200 filas de la cola con las 32
    columnas, y recien despues filtraba y paginaba en Python — o sea que el
    ahorro de paginar no existia del lado del servidor. Con gunicorn ocupando
    ~100 MB de los 256 de la maquina, dos pedidos a la vez mataban al worker.

    Devuelve {items, total, pages, page} para que la pantalla sepa cuantas
    paginas hay sin traerselas.
    """
    cols = ", ".join(f"b.{c}" for c in COLUMNAS_LISTA)
    cuenta = (
        "(SELECT COUNT(*) FROM call_logs cl WHERE cl.lead_id = b.id "
        "   AND cl.outcome = 'no_contestó') AS no_contesto_count, "
        "(SELECT COUNT(*) FROM call_logs cl WHERE cl.lead_id = b.id "
        "   AND cl.outcome = 'no_interesa') AS no_interesa_count"
    )
    cond, params = [], []
    if crm_status == "sin_contactar":
        # Los leads de Meta no entran a la cola fria: tienen su propio panel.
        cond.append("(b.crm_status IS NULL OR b.crm_status = ?)")
        cond.append("(b.source IS NULL OR b.source != 'meta')")
        params.append("sin_contactar")
    elif crm_status:
        cond.append("b.crm_status = ?")
        params.append(crm_status)
    if cohorte == COHORTE_SIN_WEB:
        cond.append("b.source IS NULL")
    elif cohorte:
        cond.append("b.source = ?")
        params.append(cohorte)
    if category:
        cond.append("b.category = ?")
        params.append(category)
    if search:
        cond.append("LOWER(b.name) LIKE ?")
        params.append(f"%{search.lower()}%")
    where = ("WHERE " + " AND ".join(cond)) if cond else ""

    conn = _connect(db_path)
    try:
        (total,) = conn.execute(
            f"SELECT COUNT(*) FROM businesses b {where}", params).fetchone()
        por_pagina = max(1, int(por_pagina))
        pages = max(1, (total + por_pagina - 1) // por_pagina)
        page = min(max(1, int(page or 1)), pages)
        orden = ("ORDER BY CASE WHEN b.callback_date IS NULL THEN 1 ELSE 0 END, "
                 "b.callback_date ASC"
                 if crm_status == "llamar_despues" else
                 "ORDER BY CASE WHEN b.score IS NULL THEN 1 ELSE 0 END, "
                 "b.score DESC, b.scraped_at DESC")
        filas = conn.execute(
            f"SELECT {cols}, {cuenta} FROM businesses b {where} {orden} LIMIT ? OFFSET ?",
            params + [por_pagina, (page - 1) * por_pagina]).fetchall()
    finally:
        conn.close()
    return {"items": [dict(f) for f in filas], "total": total,
            "pages": pages, "page": page}


# La cohorte que no tiene `source`: el padron scrapeado de comercios sin web.
# Es un valor de la UI, no de la base, y por eso vale la pena que tenga nombre:
# "b.source IS NULL" repartido por el codigo se lee como un descuido.
COHORTE_SIN_WEB = "sin_web"


def get_all_businesses(db_path: str, crm_status: str | None = None, crm_statuses: list | None = None,
                       source: str | None = None, cohorte: str | None = None) -> list[dict]:
    """Los leads de una vista del CRM.

    `cohorte` **se compone** con el resto en vez de reemplazarlo: es un filtro
    de la vista (de que origen la quiero ver), no otra vista. `source`, en
    cambio, sigue siendo la vista entera de un origen, que es lo que usa el
    panel de Meta Ads.
    """
    conn = _connect(db_path)
    try:
        count_sql = (
            "(SELECT COUNT(*) FROM call_logs cl WHERE cl.lead_id = b.id AND cl.outcome = 'no_contestó') as no_contesto_count, "
            "(SELECT COUNT(*) FROM call_logs cl WHERE cl.lead_id = b.id AND cl.outcome = 'no_interesa') as no_interesa_count"
        )
        select = f"SELECT b.*, {count_sql} FROM businesses b"
        orden = ("ORDER BY CASE WHEN b.score IS NULL THEN 1 ELSE 0 END, "
                 "b.score DESC, b.scraped_at DESC")
        if source:
            cond = ["b.source = ?"]
            params: list = [source]
        elif crm_statuses:
            placeholders = ",".join("?" * len(crm_statuses))
            cond = [f"b.crm_status IN ({placeholders})"]
            params = list(crm_statuses)
        elif crm_status == "sin_contactar":
            # Los leads de Meta no entran a la cola de llamadas fria: tienen su
            # propio panel y su propia secuencia.
            cond = ["(b.crm_status IS NULL OR b.crm_status = ?)",
                    "(b.source IS NULL OR b.source != 'meta')"]
            params = ["sin_contactar"]
        elif crm_status == "llamar_despues":
            # Los que tienen fecha de callback van primero, y los sin fecha al
            # fondo: es una agenda, no un ranking por score.
            cond = ["b.crm_status = ?"]
            params = ["llamar_despues"]
            orden = ("ORDER BY CASE WHEN b.callback_date IS NULL THEN 1 ELSE 0 END, "
                     "b.callback_date ASC")
        elif crm_status:
            cond = ["b.crm_status = ?"]
            params = [crm_status]
        else:
            cond = []
            params = []

        if cohorte == COHORTE_SIN_WEB:
            cond.append("b.source IS NULL")
        elif cohorte:
            cond.append("b.source = ?")
            params.append(cohorte)

        where = ("WHERE " + " AND ".join(cond)) if cond else ""
        cursor = conn.execute(f"{select} {where} {orden}", params)
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
    "notion_page_id", "notion_status", "notion_synced_at",
    "notion_project_page_id",
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


def get_tasks_notion(db_path: str) -> list[dict]:
    """Tareas pareadas con una tarjeta de Notion.

    El pull la usa para dos cosas: encontrar la tarea de cada pagina que vuelve
    del tablero, y saber cuales de las pareadas ya no volvieron (o sea, que la
    tarjeta desaparecio de Notion).
    """
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT * FROM tasks WHERE notion_page_id IS NOT NULL AND notion_page_id != ''"
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def upsert_project(db_path: str, notion_page_id: str, name: str,
                   stage: str | None = None, timeline_start: str | None = None,
                   timeline_end: str | None = None, lead: str | None = None) -> int:
    """Da de alta o actualiza un proyecto espejado de Notion.

    La identidad es `notion_page_id`, no el nombre: renombrar un proyecto alla
    tiene que actualizar la fila, no crear una nueva.
    """
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO projects
                   (notion_page_id, name, stage, timeline_start, timeline_end,
                    lead, notion_synced_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(notion_page_id) DO UPDATE SET
                   name = excluded.name,
                   stage = excluded.stage,
                   timeline_start = excluded.timeline_start,
                   timeline_end = excluded.timeline_end,
                   lead = excluded.lead,
                   notion_synced_at = excluded.notion_synced_at""",
            (notion_page_id, name, stage, timeline_start, timeline_end, lead, ahora),
        )
        conn.commit()
        fila = conn.execute("SELECT id FROM projects WHERE notion_page_id = ?",
                            (notion_page_id,)).fetchone()
        return fila["id"]
    finally:
        conn.close()


def get_projects(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def borrar_proyectos(db_path: str, page_ids: set) -> int:
    """Borra proyectos que ya no estan en el tablero y despareja sus tareas.

    Es un espejo: no hay historial propio que perder. Lo que si importa es no
    dejar tareas apuntando a un proyecto que ya no existe.
    """
    if not page_ids:
        return 0
    marcas = ",".join("?" for _ in page_ids)
    valores = list(page_ids)
    conn = _connect(db_path)
    try:
        conn.execute(
            f"UPDATE tasks SET notion_project_page_id = NULL "
            f"WHERE notion_project_page_id IN ({marcas})", valores)
        cursor = conn.execute(
            f"DELETE FROM projects WHERE notion_page_id IN ({marcas})", valores)
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def upsert_notion_client(db_path: str, notion_page_id: str, name: str,
                         status: str | None = None, descripcion: str | None = None,
                         due_date: str | None = None,
                         tiempo_estimado: float | None = None,
                         notion_project_page_id: str | None = None) -> int:
    """Da de alta o actualiza un cliente espejado de Notion.

    La identidad es `notion_page_id`, igual que en `projects`: renombrar la
    ficha alla actualiza la fila, no crea una nueva.
    """
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO notion_clients
                   (notion_page_id, name, status, descripcion, due_date,
                    tiempo_estimado, notion_project_page_id, notion_synced_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(notion_page_id) DO UPDATE SET
                   name = excluded.name,
                   status = excluded.status,
                   descripcion = excluded.descripcion,
                   due_date = excluded.due_date,
                   tiempo_estimado = excluded.tiempo_estimado,
                   notion_project_page_id = excluded.notion_project_page_id,
                   notion_synced_at = excluded.notion_synced_at""",
            (notion_page_id, name, status, descripcion, due_date,
             tiempo_estimado, notion_project_page_id, ahora),
        )
        conn.commit()
        fila = conn.execute(
            "SELECT id FROM notion_clients WHERE notion_page_id = ?",
            (notion_page_id,)).fetchone()
        return fila["id"]
    finally:
        conn.close()


def get_notion_clients(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT * FROM notion_clients ORDER BY name COLLATE NOCASE")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def borrar_notion_clients(db_path: str, page_ids: set) -> int:
    """Saca del espejo las fichas que ya no estan en el tablero.

    No hay nada colgando de un cliente espejado (las tareas apuntan a
    proyectos, no a esto), asi que el borrado no despareja nada.
    """
    if not page_ids:
        return 0
    marcas = ",".join("?" for _ in page_ids)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            f"DELETE FROM notion_clients WHERE notion_page_id IN ({marcas})",
            list(page_ids))
        conn.commit()
        return cursor.rowcount
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


# ─── LinkedIn ─────────────────────────────────────────────────────────────────

_LINKEDIN_POST_COLUMNS = {
    "job_id", "lote", "tipo", "texto", "angulo", "fuente_tipo", "fuente_id",
    "imagen_tipo", "imagen_spec", "fuente_desc", "aviso", "estado",
    "marcar_token", "publicado_en",
}


def create_linkedin_post(db_path: str, **fields) -> int:
    invalid = set(fields) - _LINKEDIN_POST_COLUMNS
    if invalid:
        raise ValueError(f"Invalid linkedin_posts columns: {invalid}")
    cols = ", ".join(fields)
    marks = ", ".join(f":{k}" for k in fields)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            f"INSERT INTO linkedin_posts ({cols}) VALUES ({marks})", fields
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_linkedin_post(db_path: str, post_id: int, **fields) -> None:
    invalid = set(fields) - _LINKEDIN_POST_COLUMNS
    if invalid:
        raise ValueError(f"Invalid linkedin_posts columns: {invalid}")
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = post_id
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE linkedin_posts SET {set_clause} WHERE id = :id", fields)
        conn.commit()
    finally:
        conn.close()


def get_linkedin_posts_by_job(db_path: str, job_id: int) -> list[dict]:
    """Accesor de diagnostico: que borradores dejo una corrida del worker.

    El pipeline busca por lote, no por job.
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM linkedin_posts WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_linkedin_posts_by_lote(db_path: str, lote: str) -> list[dict]:
    """Los borradores de una corrida.

    El lote lo genera el endpoint antes de encolar el job, asi que no hay
    ventana en la que el worker vea una fila sin identificar.
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM linkedin_posts WHERE lote = ? ORDER BY id", (lote,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_linkedin_post_by_token(db_path: str, token: str) -> Optional[dict]:
    if not token:
        return None
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM linkedin_posts WHERE marcar_token = ?", (token,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_fuentes_usadas(db_path: str) -> set:
    """Pares (fuente_tipo, fuente_id) que ya salieron por mail o se publicaron.

    Un borrador en estado 'generado' todavia no salio a ningun lado y uno
    'descartado' no lo va a hacer nunca, asi que su fuente sigue libre.
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT fuente_tipo, fuente_id FROM linkedin_posts "
            "WHERE estado IN ('enviado', 'publicado')"
        ).fetchall()
        return {(r["fuente_tipo"], r["fuente_id"]) for r in rows}
    finally:
        conn.close()


def get_temas_disponibles(db_path: str, limite_iso: str) -> list[dict]:
    """Temas nunca usados o usados antes de `limite_iso`, mas viejos primero."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM linkedin_temas "
            "WHERE usado_en IS NULL OR usado_en < ? "
            "ORDER BY usado_en IS NOT NULL, usado_en, id",
            (limite_iso,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def marcar_tema_usado(db_path: str, tema_id: int, cuando_iso: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE linkedin_temas SET usado_en = ? WHERE id = ?", (cuando_iso, tema_id)
        )
        conn.commit()
    finally:
        conn.close()


def get_banco_disponible(db_path: str, limite_iso: str) -> list[dict]:
    """Posts del banco nunca usados o usados antes de `limite_iso`.

    Primero los que nunca salieron, despues los reciclados de mas viejo a mas
    nuevo. Dentro de los que nunca salieron el orden es por angulo y recien
    despues por id, y eso no es cosmetico: los dos angulos de un tema son
    filas contiguas, asi que ordenando solo por id la corrida del viernes
    agarraba el otro angulo de los mismos temas del martes y la semana quedaba
    hablando dos veces de lo mismo. Ordenando por angulo se da una vuelta
    entera al banco en "concreto" (21 semanas) antes de empezar la vuelta en
    "implicancia", asi que un tema no se repite hasta cinco meses despues y
    vuelve desde el otro lado.
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM linkedin_banco "
            "WHERE usado_en IS NULL OR usado_en < ? "
            "ORDER BY usado_en IS NOT NULL, usado_en, angulo, id",
            (limite_iso,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def marcar_banco_usado(db_path: str, banco_id: int, cuando_iso: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE linkedin_banco SET usado_en = ? WHERE id = ?",
            (cuando_iso, banco_id),
        )
        conn.commit()
    finally:
        conn.close()


# Los 42 temas. Desde que existe el banco de posts ya escritos, esta tabla no
# la lee nadie en tiempo de ejecucion: quedo como el indice de los temas y,
# sobre todo, por la segunda columna, que dice que tiene que dejar dicho cada
# post. Es la referencia para escribir los del banco, no una entrada del cron.
_LINKEDIN_TEMAS_SEMILLA = [
    ("Por qué tu negocio no aparece en Google Maps",
     "la ficha existe pero está incompleta, y eso decide quién te encuentra"),
    ("Qué es un CRM y por qué tu Excel no lo es",
     "el Excel no te avisa, no recuerda y no lo ve tu equipo"),
    ("Cuánto tarda de verdad una tienda online",
     "el desarrollo es la parte corta; cargar el catálogo es la larga"),
    ("El costo de no tener web cuando te buscan por el nombre",
     "si no aparecés, el cliente asume que cerraste"),
    ("WhatsApp Business no es lo mismo que WhatsApp",
     "catálogo, respuestas rápidas y etiquetas cambian el volumen que aguantás"),
    ("Por qué el pago online sube el ticket promedio",
     "el que ya pagó no negocia el envío"),
    ("Qué mirar antes de contratar a alguien que te haga la web",
     "quién queda como titular del dominio y del hosting"),
    ("El dominio es tuyo, no de quien te hizo la web",
     "si está a nombre de otro, tu negocio está prestado"),
    ("Tres números que deberías saber de tu propio negocio",
     "cuánto vendés, cuánto te cuesta vender y cuánto vuelve"),
    ("Automatizar no es reemplazar gente",
     "es sacarle a la gente lo que hace mejor una máquina"),
    ("Cuándo conviene una web y cuándo alcanza con Instagram",
     "el límite lo pone el catálogo y el horario de atención"),
    ("Por qué los formularios de contacto no reciben nada",
     "van a una casilla que nadie abre"),
    ("El stock que no cuadra sale caro dos veces",
     "vendés lo que no tenés y no vendés lo que tenés"),
    ("Qué hace un sistema de gestión que no hace una planilla",
     "el historial, los permisos y que dos personas escriban a la vez"),
    ("Facturación electrónica en Uruguay sin dolor de cabeza",
     "quién la emite y cómo se integra con lo que ya usás"),
    ("Cómo se ve tu negocio desde un celular",
     "la mitad de las visitas entra desde el teléfono y ve otra cosa"),
    ("Cuando tu web tarda más de tres segundos ya perdiste visitas",
     "la velocidad no es estética, es plata"),
    ("Las reseñas de Google se responden todas",
     "las malas también, sobre todo las malas"),
    ("Qué pasa con tus datos si se rompe la computadora del mostrador",
     "sin backup no hay negocio, hay suerte"),
    ("Por qué pedir presupuesto por entregable y no por horas",
     "las horas no te dicen qué te llevás"),
    ("La diferencia entre una web y un catálogo online",
     "una informa, la otra vende"),
    ("Click and collect: vender online y entregar en el local",
     "sin costo de envío y con el cliente adentro del local"),
    ("Cuánto cuesta mantener una web al año",
     "dominio, hosting y las horas de quien la actualiza"),
    ("El error de tener el teléfono solo en la foto de portada",
     "nadie transcribe un número de una imagen"),
    ("Qué datos pedir en un formulario y cuáles sobran",
     "cada campo de más te cuesta respuestas"),
    ("Cómo saber si tu publicidad está funcionando",
     "si no podés decir cuántos clientes trajo, no lo sabés"),
    ("Integrar el sistema de tu proveedor con el tuyo",
     "cuando no hay API, el CSV sigue siendo una respuesta"),
    ("Por qué separar la casilla del negocio de la personal",
     "el día que te vas de vacaciones tu negocio sigue recibiendo"),
    ("Un correo con tu dominio cuesta menos de lo que pensás",
     "y cambia cómo te leen los proveedores"),
    ("Qué es el SEO local y por qué te importa más que el otro",
     "competís contra los cinco negocios de tu barrio, no contra el mundo"),
    ("Digitalizar de a poco también es digitalizar",
     "el orden importa más que la velocidad"),
    ("Las tres preguntas antes de comprar cualquier software",
     "quién lo usa, qué reemplaza y cómo salgo si no me sirve"),
    ("El sistema que nadie usa es un gasto, no una inversión",
     "la adopción se diseña, no se pide"),
    ("Qué mirar en un contrato de desarrollo de software",
     "entregables, plazos y qué pasa después de la entrega"),
    ("Por qué tu tienda online no vende aunque tenga visitas",
     "el problema está entre el carrito y el pago"),
    ("Los costos de envío decididos tarde matan la venta",
     "el cliente los quiere ver antes de cargar la tarjeta"),
    ("Qué información tiene que estar sí o sí en tu web",
     "qué vendés, dónde estás, cómo te contactan y a qué hora abrís"),
    ("Cómo se organiza un negocio con dos personas y cien pedidos",
     "primero el flujo, después la herramienta"),
    ("Cuándo conviene hacerlo a medida y cuándo comprar hecho",
     "lo raro de tu negocio se hace a medida, el resto se compra"),
    ("La transformación digital no empieza por la tecnología",
     "empieza por escribir cómo trabajás hoy"),
    ("Por qué el mismo producto tiene tres precios en tres lugares",
     "una fuente de verdad o ninguna"),
    ("Qué hacer con los contactos que juntaste y nunca usaste",
     "una base vieja vale más que una campaña nueva"),
]


def seed_linkedin_temas(db_path: str) -> None:
    """Inserta los temas educativos que falten. Idempotente por titulo."""
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO linkedin_temas (titulo, angulo) VALUES (?, ?)",
            _LINKEDIN_TEMAS_SEMILLA,
        )
        conn.commit()
    finally:
        conn.close()


def seed_linkedin_banco(db_path: str) -> None:
    """Inserta los posts ya escritos que falten. Idempotente por (tema, angulo).

    La semilla vive en su propio modulo: son 84 textos largos y no tienen nada
    que hacer en el medio de las queries.
    """
    from services.linkedin_banco_semilla import LINKEDIN_BANCO_SEMILLA

    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO linkedin_banco (tema, angulo, texto, frase) "
            "VALUES (?, ?, ?, ?)",
            LINKEDIN_BANCO_SEMILLA,
        )
        conn.commit()
    finally:
        conn.close()
