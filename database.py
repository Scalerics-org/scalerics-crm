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


def _grant_panel_to_existing_roles(conn: sqlite3.Connection, panel: str,
                                   solo_si_tiene: Optional[str] = None,
                                   si_tiene: tuple = ()) -> int:
    """Suma `panel` al panel_access de los roles que ya existen y no lo tengan.

    La siembra de roles por defecto solo corre con la tabla vacía, así que en
    una base que ya tiene roles (producción) un panel nuevo no le llega a
    nadie salvo a los admin, que reciben todos. Esto lo arregla en el arranque.

    Con `solo_si_tiene`, el panel le llega únicamente a los roles que ya
    tienen ese otro: sirve cuando un panel se parte en dos (Equipo ->
    Organigrama + Ausencias) y quien veía el viejo tiene que seguir viendo
    todo.

    Con `si_tiene`, solo se suma a los roles que ya tengan ALGUNO de esos
    paneles (Plantillas va a quien vende: `wa` o `notion_clients`).

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
        if solo_si_tiene is not None and solo_si_tiene not in paneles:
            continue
        if si_tiene and not any(p in paneles for p in si_tiene):
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


# Etapas del pipeline de PRE-CLIENTES, en el orden en que avanza una venta.
# Reemplazan a las genericas de antes (reunion_agendada / reunion_hecha /
# negociacion), que no describian como se vende aca: el eje real son las demos.
ETAPAS_PRECLIENTE = (
    "demo_agendada",        # pactada, todavia no se dio
    "demo_1",
    "demo_2",
    "demo_3",
    "presupuesto_enviado",
    "follow_up_1",
    "follow_up_2",
    "acepto",               # dijo que si, falta firma o pago
    "en_espera",            # frenado por el cliente, sin cerrar
    "rechazo",              # dijo que no despues de haber avanzado
)

# Las dos etapas que el sistema escribe solo, al agendar una reunion y al
# cerrarla. Tienen nombre propio porque se escriben desde routes/calendar.py y
# routes/leads.py, y ahi el string suelto ya se desincronizo una vez.
ETAPA_DEMO_AGENDADA = "demo_agendada"
ETAPA_DEMO_DADA = "demo_1"

# Estados de un CLIENTE ACTIVO. 'cerrado' es la puerta de entrada: el acuerdo se
# concreto y el lead deja el pipeline de pre-clientes.
ETAPAS_CLIENTE = ("cerrado", "en_desarrollo", "finalizado")

# Como se traduce cada estado viejo. Solo se migran los del pipeline: los de la
# Cola y Seguimientos (sin_contactar, interesado, contactado, llamar_despues,
# no_interesa) se dejan intactos, porque son de otra etapa del embudo.
#
# 'no_interesa' NO pasa a 'rechazo' a proposito: uno es "nunca engancho" y el otro
# es "avanzo y despues dijo que no". Mezclarlos borraria esa diferencia.
_MAPA_ESTADOS_VIEJOS = {
    "reunion_agendada": "demo_agendada",
    "reunion_hecha":    "demo_1",
    "negociacion":      "follow_up_1",
    "cliente_cerrado":  "cerrado",
    # alias que quedaron de una version anterior
    "agendo":           "demo_agendada",
    "firmo":            "cerrado",
}


def normalizar_crm_status(estado):
    """Traduce un estado viejo del pipeline al que lo reemplaza.

    Es para el BORDE: lo que llega de afuera (el navegador de alguien que tiene
    la pagina abierta desde antes del deploy, un webhook) puede traer un nombre
    viejo, y guardarlo tal cual deja al lead en un estado que ya no es etapa.
    Como el tablero filtra por `crm_status IN (etapas)`, ese lead existe en la
    base y no aparece en ninguna columna: desaparece de la vista sin error.

    **No se llama desde `update_business`, a proposito.** Traducir en la
    escritura parece la solucion obvia y rompe a cualquiera que haga
    leer-comparar-escribir: `services/planilla_semaforo.py` pide 'negociacion',
    lee de vuelta 'follow_up_1', no coinciden, y vuelve a escribir en cada
    corrida —un evento espurio por dia, para siempre. Se probo y se saco.

    Idempotente: las etapas nuevas no estan en el mapa.
    """
    return _MAPA_ESTADOS_VIEJOS.get(estado, estado)


def _migrar_estados_preclientes(conn: sqlite3.Connection) -> None:
    """Traduce los estados viejos del pipeline a las etapas de pre-clientes.

    Idempotente: los estados nuevos no estan en el mapa, asi que una segunda
    corrida no toca nada. Se registra cuantas filas movio cada regla, porque es
    una migracion de datos productivos y tiene que quedar rastro.
    """
    for viejo, nuevo in _MAPA_ESTADOS_VIEJOS.items():
        try:
            cur = conn.execute(
                "UPDATE businesses SET crm_status = ? WHERE crm_status = ?",
                (nuevo, viejo),
            )
            if cur.rowcount:
                logger.info("Migracion de etapas: %s -> %s (%s leads)",
                            viejo, nuevo, cur.rowcount)
        except sqlite3.Error as e:
            logger.error("No se pudo migrar %s -> %s: %s", viejo, nuevo, e)
    conn.commit()


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
        # Solo lectura por panel (pedido de Juan, 15/9): "el usuario que sea
        # dado de alta como contador no va a poder agregar movimientos o editar
        # cosas solo a visualizar salvo la parte de balances". Lista JSON de
        # paneles que el rol VE pero no modifica. El dato es genérico; hoy solo
        # Finanzas lo respeta en el servidor (routes/finanzas.py).
        #
        # La precarga del Contador corre UNA vez: cuando la columna es nueva.
        # Si corriera en cada arranque, destildar el "solo lectura" en el editor
        # de roles duraría hasta el próximo deploy.
        solo_lectura_es_nueva = not any(
            fila[1] == "paneles_solo_lectura"
            for fila in conn.execute("PRAGMA table_info(roles)").fetchall())
        _add_column(conn, "roles", "paneles_solo_lectura", "TEXT NOT NULL DEFAULT '[]'")
        if solo_lectura_es_nueva:
            conn.execute("UPDATE roles SET paneles_solo_lectura = ? "
                         "WHERE name = 'Contador' AND paneles_solo_lectura = '[]'",
                         ('["finanzas"]',))
        # Contador y Marketing (pedido de Juan, 15/9): hoy hay Admin, SDR y
        # Programador, y se suman estos dos. Se crean por nombre, una sola vez,
        # también en bases que ya tienen roles; INSERT OR IGNORE no pisa lo que
        # Juan cambie después en el editor de roles.
        # Contador arranca SIN Finanzas ni Simulador aunque sean su trabajo: por
        # el Ruling R20 los paneles con plata no se asignan desde el código,
        # Juan los tilda a mano en el editor de roles.
        # El Contador nace con Finanzas en solo lectura: el día que Juan le
        # tilde Finanzas, ya entra sin poder modificarla. Si el rol se crea
        # recién ahora (base nueva, o lo borraron), la precarga de arriba no
        # lo vio: por eso va también en el INSERT.
        import json as _jroles
        for _nombre, _paneles, _solo_lectura in (
                ("Contador", ["cal"], ["finanzas"]),
                ("Marketing", ["cal", "meta", "marketing"], [])):
            conn.execute("INSERT OR IGNORE INTO roles (name, panel_access, paneles_solo_lectura) "
                         "VALUES (?, ?, ?)",
                         (_nombre, _jroles.dumps(_paneles), _jroles.dumps(_solo_lectura)))
        _add_column(conn, "client_info", "meeting_time", "TEXT")
        _add_column(conn, "client_info", "meeting_url", "TEXT")

        # ── Marketing / Meta Ads ──────────────────────────────────────────────
        # La campaña del lead vivía embebida en `notes` como
        # "Meta Lead Ad · <campaña>". Un LIKE sobre notes no agrupa ni escala, y
        # el nombre de una campaña puede cambiar en Meta mientras el id no.
        _add_column(conn, "businesses", "meta_campaign_id", "TEXT")
        _add_column(conn, "businesses", "meta_campaign_name", "TEXT")
        _add_column(conn, "businesses", "meta_adset_id", "TEXT")
        _add_column(conn, "businesses", "meta_ad_id", "TEXT")
        _add_column(conn, "businesses", "meta_ad_name", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_biz_meta_campaign "
                     "ON businesses(meta_campaign_id)")

        # Gasto y performance que devuelve la Marketing API, al grano de
        # campaña × día. Solo métricas crudas y contables: CPM, CPC, CTR y CPL
        # se derivan al calcular. Una tasa guardada se desincroniza de sus
        # componentes y después nadie sabe cuál manda.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_insights (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                date          TEXT NOT NULL,
                campaign_id   TEXT NOT NULL,
                campaign_name TEXT,
                spend         REAL DEFAULT 0,
                currency      TEXT,
                impressions   INTEGER DEFAULT 0,
                clicks        INTEGER DEFAULT 0,
                reach         INTEGER DEFAULT 0,
                leads         INTEGER DEFAULT 0,
                synced_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, campaign_id)
            )
        """)

        # Un renglon por anuncio: el estado de HOY, no una serie. Lo que
        # cambia en el tiempo (gasto, leads) vive en `meta_ad_insights`.
        #
        # `imagen_url` es la que devuelve Meta: viene firmada y caduca, asi que
        # no sirve para guardar en el panel. Por eso se baja el archivo una vez
        # a `imagen_archivo` y de ahi en mas se sirve el nuestro.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_ads (
                ad_id            TEXT PRIMARY KEY,
                ad_name          TEXT,
                campaign_id      TEXT,
                campaign_name    TEXT,
                adset_name       TEXT,
                effective_status TEXT,
                creative_id      TEXT,
                object_type      TEXT,
                titulo           TEXT,
                cuerpo           TEXT,
                imagen_url       TEXT,
                imagen_archivo   TEXT,
                synced_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # El mismo grano que `meta_insights` pero por anuncio. Existe aparte y
        # no como columna de aquella porque un anuncio pertenece a una campana:
        # mezclarlos haria que sumar la tabla contara el gasto dos veces.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_ad_insights (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL,
                ad_id       TEXT NOT NULL,
                spend       REAL DEFAULT 0,
                currency    TEXT,
                impressions INTEGER DEFAULT 0,
                clicks      INTEGER DEFAULT 0,
                reach       INTEGER DEFAULT 0,
                leads       INTEGER DEFAULT 0,
                synced_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, ad_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ad_insights_fecha "
                     "ON meta_ad_insights(date)")

        # Cada corrida guarda el dossier entero, no solo el informe: es lo que
        # permite auditar después por qué se dijo lo que se dijo, y comparar
        # contra el período anterior sin recalcular el pasado.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS radiografias (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                period_start  TEXT NOT NULL,
                period_end    TEXT NOT NULL,
                dossier_json  TEXT NOT NULL,
                report_json   TEXT,
                model         TEXT,
                tokens_in     INTEGER,
                tokens_out    INTEGER,
                status        TEXT DEFAULT 'ok',
                error_message TEXT
            )
        """)

        _grant_panel_to_existing_roles(conn, "marketing")

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
        # Con que negocio del CRM se corresponde la ficha. Notion no trae ningun
        # id del CRM (solo el nombre), asi que se conecta a mano una vez. Con eso,
        # llegar a "Presupuesto Aceptado" pasa al negocio a Clientes: desde el
        # 14/9 es el unico camino, porque se saco el tablero de Pre-clientes.
        # El upsert del sync no lista esta columna, asi que no la pisa.
        _add_column(conn, "notion_clients", "business_id", "INTEGER")

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

        # ── finanzas ──────────────────────────────────────────────────────────
        # El libro de ingresos y egresos de Scalerics. Un solo libro con una
        # columna `tipo`: un cobro y un gasto tienen los mismos campos.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS finanzas_movimientos (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo            TEXT NOT NULL,
                fecha           TEXT NOT NULL,
                periodo         TEXT NOT NULL,
                concepto        TEXT NOT NULL,
                categoria       TEXT NOT NULL,
                monto           REAL NOT NULL,
                moneda          TEXT NOT NULL,
                tipo_cambio     REAL,
                monto_usd       REAL NOT NULL,
                client_id       INTEGER REFERENCES businesses(id),
                budget_id       INTEGER REFERENCES budgets(id),
                recurrente_id   INTEGER REFERENCES finanzas_recurrentes(id),
                anulado         INTEGER NOT NULL DEFAULT 0,
                notas           TEXT,
                created_by_id   INTEGER,
                created_by_name TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Esta es LA guarda del módulo. Materializar los fijos hace un INSERT
        # normal y atrapa el IntegrityError de violar este índice (descartando
        # solo ese caso puntual, no cualquier IntegrityError), así que correrlo
        # mil veces produce exactamente un movimiento por fijo y por mes. Sin
        # él, cada deploy duplicaría los gastos: reiniciar la máquina vuelve a
        # materializar.
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_finanzas_recurrente_periodo
                ON finanzas_movimientos (recurrente_id, periodo)
                WHERE recurrente_id IS NOT NULL
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_finanzas_periodo
                ON finanzas_movimientos (periodo)
        """)

        # IVA. `facturado` dice si el movimiento lleva factura: un gasto que
        # pago alguien del equipo de su bolsillo no descuenta IVA. `iva_usd`
        # guarda el impuesto en vez de calcularlo al leer, para que un cambio
        # de tasa no reescriba lo que ya se facturo.
        #
        # Los movimientos que ya existian arrancan en facturado=0: no se sabe
        # cuales llevaban factura, y suponer que si inventaria un IVA que nunca
        # se cobro. La pestania arranca vacia y se llena con lo que se cargue.
        _add_column(conn, "finanzas_movimientos", "facturado", "INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "finanzas_movimientos", "iva_usd", "REAL NOT NULL DEFAULT 0")

        # Lo que falta cobrar. El caso real es el 50% final de un desarrollo:
        # se cobra la mitad al empezar y el resto queda pendiente con una fecha
        # estimada.
        #
        # `cobrado_movimiento_id` NULL es "todavia se debe"; cuando se cobra
        # apunta al ingreso que lo salda. No hay un booleano `cobrado` aparte
        # porque serian dos fuentes de verdad para el mismo hecho, y la que
        # importa es cual movimiento entro la plata.
        #
        # `origen_movimiento_id` es el cobro parcial que lo genero. Puede ser
        # NULL: un pendiente cargado a mano no viene de ningun movimiento.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS finanzas_por_cobrar (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id             INTEGER REFERENCES businesses(id),
                concepto              TEXT NOT NULL,
                monto_usd             REAL NOT NULL,
                vence                 TEXT,
                origen_movimiento_id  INTEGER REFERENCES finanzas_movimientos(id),
                cobrado_movimiento_id INTEGER REFERENCES finanzas_movimientos(id),
                notas                 TEXT,
                created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_por_cobrar_pendientes
                ON finanzas_por_cobrar (vence)
                WHERE cobrado_movimiento_id IS NULL
        """)

        # Meses REABIERTOS, no cerrados. Un mes pasado esta cerrado por
        # default: si se guardaran los cerrados habria que acordarse de cerrar
        # cada mes, y el que nadie cierre quedaria editable para siempre.
        # Cerrar es la regla, abrir es la excepcion, y la excepcion es la que
        # se anota.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS finanzas_meses_abiertos (
                periodo     TEXT PRIMARY KEY,
                abierto_por TEXT,
                abierto_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS finanzas_recurrentes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo        TEXT NOT NULL,
                concepto    TEXT NOT NULL,
                categoria   TEXT NOT NULL,
                monto       REAL NOT NULL,
                moneda      TEXT NOT NULL,
                tipo_cambio REAL,
                dia_del_mes INTEGER NOT NULL DEFAULT 1,
                desde       TEXT NOT NULL,
                hasta       TEXT,
                activo      INTEGER NOT NULL DEFAULT 1,
                client_id   INTEGER REFERENCES businesses(id),
                notas       TEXT,
                facturado   INTEGER NOT NULL DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # El hosting, las herramientas y el contador vienen con factura todos
        # los meses, así que el fijo también lleva `facturado` y su IVA se
        # calcula al materializar. No hay `iva_usd` acá: el impuesto se congela
        # en el movimiento generado, no en la plantilla, para que un cambio de
        # tasa no reescriba los meses que ya se facturaron.
        #
        # Los fijos que ya existían arrancan en 0, por el mismo motivo que los
        # movimientos: suponer que llevaban factura inventaría un IVA que nunca
        # se descontó.
        _add_column(conn, "finanzas_recurrentes", "facturado",
                    "INTEGER NOT NULL DEFAULT 0")

        # A propósito, sin la migración que suma este panel al panel_access
        # de los roles que ya existen (Ruling R20): todos los demás paneles
        # nuevos se la aplican porque esconder un ítem del menú no es un
        # permiso real. Finanzas es la excepción: es el único panel que
        # muestra la plata de la empresa, así que arranca sin nadie asignado
        # en vez de con todos los roles adentro -Caller incluido. Un admin lo
        # ve igual, por el bypass de is_admin en tiene_panel(); el resto se
        # lo asigna Juan a mano desde el editor de roles, que es justamente
        # lo que eligió al marcar "panel normal, se asigna por rol".

        # ── simulador financiero ──────────────────────────────────────────────
        # Escenarios guardados del simulador. Tabla propia y aparte de las de
        # finanzas a propósito: el simulador LEE Finanzas para precargar, pero
        # trabaja sobre su copia y nunca escribe ahí. `datos` es el escenario
        # entero en JSON (equipo, listas, palancas, supuestos): su forma la
        # define el panel y lleva `version`, para poder migrarla sin tocar la
        # tabla cuando lleguen la comparación y la proyección a 12 meses.
        #
        # Igual que Finanzas (Ruling R20), sin `_grant_panel_to_existing_roles`:
        # el simulador muestra sueldos y lo que se debe cobrar, así que arranca
        # sin nadie asignado y Juan lo reparte desde el editor de roles.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS simulador_escenarios (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre          TEXT NOT NULL,
                datos           TEXT NOT NULL,
                created_by_id   INTEGER,
                created_by_name TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── equipo ────────────────────────────────────────────────────────────
        # Organigrama y horas a recuperar. A propósito NINGUNA de las tres
        # tablas tiene campos de dinero: si alguien falta no se le descuenta
        # nada, solo se registra cuántas horas debe y cuándo las devuelve
        # (pedido de Juan, 14/9). tests/test_equipo.py lo verifica sobre el
        # esquema.
        #
        # `reporta_a` es un solo id. Las personas con NULL son la fila de
        # arriba del organigrama; los hijos de cualquier raíz se dibujan bajo
        # un conector común que une a todas las raíces.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS equipo_personas (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre          TEXT NOT NULL,
                rol             TEXT NOT NULL DEFAULT '',
                reporta_a       INTEGER REFERENCES equipo_personas(id) ON DELETE SET NULL,
                lleva_horas     INTEGER NOT NULL DEFAULT 0,
                horas_por_dia   REAL NOT NULL DEFAULT 4,
                activo          INTEGER NOT NULL DEFAULT 1,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_equipo_personas_nombre "
                     "ON equipo_personas(nombre)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS equipo_ausencias (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id      INTEGER NOT NULL REFERENCES equipo_personas(id) ON DELETE CASCADE,
                fecha_desde     TEXT NOT NULL,
                fecha_hasta     TEXT NOT NULL,
                motivo          TEXT NOT NULL,
                horas_totales   REAL NOT NULL,
                created_by_id   INTEGER,
                created_by_name TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_equipo_ausencias_persona "
                     "ON equipo_ausencias(persona_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS equipo_recuperos (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ausencia_id     INTEGER NOT NULL REFERENCES equipo_ausencias(id) ON DELETE CASCADE,
                fecha           TEXT NOT NULL,
                horas           REAL NOT NULL,
                created_by_id   INTEGER,
                created_by_name TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_equipo_recuperos_ausencia "
                     "ON equipo_recuperos(ausencia_id)")
        conn.commit()
        _sembrar_equipo(conn)
        # Como Marketing y a diferencia de Finanzas/Simulador (Ruling R20):
        # acá no hay plata, así que el panel les llega a los roles existentes.
        _grant_panel_to_existing_roles(conn, "equipo")
        # Recursos Humanos partió Equipo en dos paneles (pedido de Juan): el
        # organigrama se quedó con el id `equipo` y Ausencias es `ausencias`.
        # Quien tenía Equipo veía las dos partes, así que recibe Ausencias.
        _grant_panel_to_existing_roles(conn, "ausencias", solo_si_tiene="equipo")

        # ── plantillas de mensajes ────────────────────────────────────────────
        # Los mensajes que Juan manda siempre, con variables entre llaves que se
        # completan con los datos del lead. NO es `wa_templates`: esa tabla son
        # atajos sueltos del chat de WhatsApp (nombre + texto, sin editar), y
        # meter ahí estas plantillas las haría aparecer con las llaves sin
        # completar en el "Abrir WA" del panel de cliente.
        #
        # `clave` identifica a las precargadas (NULL en las creadas a mano): la
        # precarga es INSERT OR IGNORE por clave, así que no duplica ni pisa lo
        # que se edite. Por eso borrar es marcar `borrada`: si se borrara la
        # fila, el próximo arranque volvería a crear la plantilla.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plantillas_mensajes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                clave           TEXT UNIQUE,
                orden           INTEGER NOT NULL DEFAULT 0,
                momento         TEXT NOT NULL DEFAULT '',
                titulo          TEXT NOT NULL,
                canal           TEXT NOT NULL DEFAULT '',
                cuerpo          TEXT NOT NULL,
                nota            TEXT NOT NULL DEFAULT '',
                explicacion     TEXT NOT NULL DEFAULT '',
                automatica      INTEGER NOT NULL DEFAULT 0,
                borrada         INTEGER NOT NULL DEFAULT 0,
                created_by_name TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        _sembrar_plantillas(conn)
        # Va a quien vende: los roles con WhatsApp o con Proceso de venta.
        _grant_panel_to_existing_roles(conn, "plantillas",
                                       si_tiene=("wa", "notion_clients"))

        # ── flujos ────────────────────────────────────────────────────────────
        # Cómo trabaja la empresa, paso a paso, con el ROL de cada etapa y
        # nunca nombres de personas. Se muestra al final de Ausencias. Los
        # pasos viven acá y no en el código: se agregan, editan y reordenan
        # desde la pantalla. `numero` se renumera 1..n en cada cambio.
        # `flujo_paso_cobros` deja que un paso tenga más de un momento de
        # cobro (50 % al inicio y 50 % contra entrega), aunque hoy haya uno.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS flujos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre      TEXT NOT NULL,
                descripcion TEXT NOT NULL DEFAULT '',
                orden       INTEGER NOT NULL DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_flujos_nombre ON flujos(nombre)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS flujo_pasos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                flujo_id    INTEGER NOT NULL REFERENCES flujos(id) ON DELETE CASCADE,
                numero      INTEGER NOT NULL,
                titulo      TEXT NOT NULL,
                rol         TEXT NOT NULL,
                detalle     TEXT NOT NULL DEFAULT '',
                pantalla    TEXT,
                destacado   INTEGER NOT NULL DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_flujo_pasos_flujo "
                     "ON flujo_pasos(flujo_id, numero)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS flujo_paso_cobros (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                paso_id     INTEGER NOT NULL REFERENCES flujo_pasos(id) ON DELETE CASCADE,
                orden       INTEGER NOT NULL DEFAULT 0,
                porcentaje  REAL NOT NULL,
                descripcion TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_flujo_paso_cobros_paso "
                     "ON flujo_paso_cobros(paso_id)")
        conn.commit()
        _sembrar_flujos(conn)

        # ── Daily Programador ─────────────────────────────────────────────────
        # Actividades del día y recordatorios que se repiten (pedido de Juan,
        # 15/9). Van por persona de `equipo_personas`, no por usuario: en el
        # menú hay una entrada por programador y cualquiera con el panel
        # `daily` entra a cualquiera. `programador` marca quién tiene su
        # daily; sumar a otro es prender la marca, sin tocar código. Se
        # precarga en Juan y Gonzalo solo cuando la columna es nueva, así un
        # cambio hecho a mano sobrevive a los reinicios.
        columnas = {fila[1] for fila in conn.execute("PRAGMA table_info(equipo_personas)")}
        if "programador" not in columnas:
            conn.execute("ALTER TABLE equipo_personas "
                         "ADD COLUMN programador INTEGER NOT NULL DEFAULT 0")
            conn.executemany("UPDATE equipo_personas SET programador = 1 WHERE nombre = ?",
                             [(n,) for n in _PROGRAMADORES_PRECARGA])
        # Daily Admin y el nombre para mostrar (Juan, 16/9). Lo nuevo de
        # personas (Matías en Daily Programador; Juan Pereyra y Javier en
        # Daily Admin; el apodo "Juanchi") se suma UNA sola vez, en el arranque
        # que crea `admin_daily`: lo que se cambie a mano antes o después no se
        # pisa, y nada de lo ya cargado cambia de dueño.
        _add_column(conn, "equipo_personas", "apodo", "TEXT")
        columnas = {fila[1] for fila in conn.execute("PRAGMA table_info(equipo_personas)")}
        if "admin_daily" not in columnas:
            conn.execute("ALTER TABLE equipo_personas "
                         "ADD COLUMN admin_daily INTEGER NOT NULL DEFAULT 0")
            _sumar_personas_daily(conn)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_actividades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id      INTEGER NOT NULL REFERENCES equipo_personas(id) ON DELETE CASCADE,
                fecha           TEXT NOT NULL,
                texto           TEXT NOT NULL,
                hecha           INTEGER NOT NULL DEFAULT 0,
                pasada_de       TEXT,
                created_by_id   INTEGER,
                created_by_name TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_actividades_dia "
                     "ON daily_actividades(persona_id, fecha)")
        # `dias`: los días de la semana como "0,2,4" (0 = lunes), solo con
        # frecuencia 'dias'. `desde`: el día en que se creó, para que no
        # aparezca en días anteriores.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_recordatorios (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id      INTEGER NOT NULL REFERENCES equipo_personas(id) ON DELETE CASCADE,
                texto           TEXT NOT NULL,
                frecuencia      TEXT NOT NULL DEFAULT 'diario',
                dias            TEXT NOT NULL DEFAULT '',
                activo          INTEGER NOT NULL DEFAULT 1,
                desde           TEXT NOT NULL,
                created_by_id   INTEGER,
                created_by_name TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_recordatorios_persona "
                     "ON daily_recordatorios(persona_id)")
        # Un recordatorio hecho en un día es una fila; desmarcarlo la borra.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_marcas (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                recordatorio_id INTEGER NOT NULL REFERENCES daily_recordatorios(id) ON DELETE CASCADE,
                fecha           TEXT NOT NULL,
                created_by_id   INTEGER,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (recordatorio_id, fecha)
            )
        """)
        # Hora y nota opcionales (pedido de Juan, 16/9: tarjetas como las de
        # Seguimiento de leads). Columnas nuevas en NULL: lo que ya estaba
        # cargado sigue igual, sin hora ni nota.
        for tabla in ("daily_actividades", "daily_recordatorios"):
            _add_column(conn, tabla, "hora", "TEXT")
            _add_column(conn, tabla, "nota", "TEXT")
            # De qué Daily es: 'programador' o 'admin'. Una persona puede estar
            # en los dos y las listas no se mezclan. Lo cargado antes es del
            # Daily Programador, que era el único.
            _add_column(conn, tabla, "seccion", "TEXT NOT NULL DEFAULT 'programador'")
        conn.commit()
        # Como Equipo: los roles que tienen Tareas reciben el Daily.
        _grant_panel_to_existing_roles(conn, "daily", solo_si_tiene="tasks")

        # ── seguimiento de leads ──────────────────────────────────────────────
        # La agenda de llamados de Juan (14/9). `lead_id` es `businesses.id`:
        # ahí está el teléfono y es la ficha que abre el panel de cliente. Las
        # fichas de Proceso de venta llegan a su lead por
        # `notion_clients.business_id`.
        #
        # Un lead tiene a lo sumo UN recordatorio pendiente: lo garantiza el
        # índice único parcial, no solo el código. `cierre` dice por qué se
        # cerró uno: 'llamado' (Hecho) o 'reemplazado' (se creó otro).
        seg_leads_nueva = not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='seg_recordatorios'"
        ).fetchone()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seg_recordatorios (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id     INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                fecha       TEXT NOT NULL,
                hora        TEXT,
                motivo      TEXT NOT NULL,
                nota        TEXT,
                estado      TEXT NOT NULL DEFAULT 'pendiente'
                            CHECK (estado IN ('pendiente', 'hecho')),
                creado_en   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                cerrado_en  TIMESTAMP,
                cierre      TEXT
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_seg_recordatorios_un_pendiente "
                     "ON seg_recordatorios(lead_id) WHERE estado = 'pendiente'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_seg_recordatorios_estado_fecha "
                     "ON seg_recordatorios(estado, fecha)")
        # El historial: una fila por cada Hecho. `fecha` es 'AAAA-MM-DD HH:MM'
        # en hora de Montevideo.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seg_llamados (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id         INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                fecha           TEXT NOT NULL,
                resultado       TEXT NOT NULL,
                recordatorio_id INTEGER REFERENCES seg_recordatorios(id) ON DELETE SET NULL,
                creado_por      TEXT,
                creado_en       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_seg_llamados_lead "
                     "ON seg_llamados(lead_id, fecha)")
        conn.commit()
        # Como Equipo (sin plata, así que no aplica el Ruling R20), pero solo a
        # los roles que ya ven Proceso de venta, y UNA sola vez: en el arranque
        # que crea la tabla. Si después Juan le saca el panel a un rol desde el
        # editor, un deploy no se lo vuelve a poner.
        if seg_leads_nueva:
            _grant_panel_to_existing_roles(conn, "seg_leads", solo_si_tiene="notion_clients")

        # ── Pre-clientes y clientes activos ───────────────────────────────────
        # Los tres responsables de un cliente activo. Apuntan a users para poder
        # filtrar "mis clientes"; si alguien se va, el vinculo queda en NULL en vez
        # de un nombre huerfano que nadie sabe a quien pertenecia.
        _add_column(conn, "businesses", "encargado_id",
                    "INTEGER REFERENCES users(id) ON DELETE SET NULL")
        _add_column(conn, "businesses", "mantenimiento_id",
                    "INTEGER REFERENCES users(id) ON DELETE SET NULL")
        _add_column(conn, "businesses", "cobros_id",
                    "INTEGER REFERENCES users(id) ON DELETE SET NULL")

        # Cuanto pago el cliente por su desarrollo. Lo carga Juan a mano: NO se
        # deriva de presupuestos ni de Finanzas, que pueden estar incompletos o
        # partidos en cobros parciales. Monto y moneda van separados, con las
        # mismas monedas que Finanzas (services.finanzas.MONEDAS), y sin pasar
        # a dolares: es el numero que se acordo, no una conversion. Los dos en
        # NULL es "todavia no se cargo", que no es lo mismo que 0.
        _add_column(conn, "businesses", "monto_pagado", "REAL")
        _add_column(conn, "businesses", "moneda_pagado", "TEXT")

        # Registro historico de demos dadas. NO es la tabla `demos`, que guarda la
        # pagina que genera la IA: esto es el evento comercial de haber mostrado
        # una demo, con quien la dio y como viene. Por eso admite varias por
        # cliente, mientras que `demos` tiene UNIQUE(client_id).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS demos_realizadas (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id       INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                numero          INTEGER,
                realizada_por   INTEGER REFERENCES users(id) ON DELETE SET NULL,
                fecha           TIMESTAMP,
                actualizacion   TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by      INTEGER REFERENCES users(id) ON DELETE SET NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_demos_realizadas_client "
                     "ON demos_realizadas(client_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_demos_realizadas_fecha "
                     "ON demos_realizadas(fecha)")
        # Demos que llegan solas desde la planilla de semaforo (ver
        # services/planilla_semaforo.sincronizar_demos). `origen` NULL es una
        # demo cargada a mano: el sync no las toca nunca. `estado_planilla` es
        # la clave estable del color (agendada, realizada, no_cerro, venta) y
        # `mes_planilla` ('AAAA-MM') la pestaña de donde salio.
        _add_column(conn, "demos_realizadas", "origen", "TEXT")
        _add_column(conn, "demos_realizadas", "estado_planilla", "TEXT")
        _add_column(conn, "demos_realizadas", "mes_planilla", "TEXT")
        # Una demo de planilla por cliente y mes: es lo que hace idempotente al
        # sync aunque dos corridas se pisen. Parcial para no limitar las
        # cargadas a mano, que pueden ser varias en el mismo mes.
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_demos_planilla_cliente_mes "
                     "ON demos_realizadas(client_id, mes_planilla) "
                     "WHERE origen = 'planilla'")
        # El presupuesto que se mando despues de una demo cuelga de la demo con
        # una columna propia y no codificado en `section` ("demo:123"): asi se
        # puede indexar y cruzar con un JOIN, y `section` sigue siendo 'budget',
        # con lo que el mismo archivo aparece tambien en la ficha del cliente.
        # El indice importa por el BLOB: buscar por demo_id sin leer la fila
        # evita recorrer las paginas de overflow de file_data.
        _add_column(conn, "lead_attachments", "demo_id", "INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lead_attachments_demo "
                     "ON lead_attachments(demo_id)")
        _migrar_estados_preclientes(conn)

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
            # Indice por EXPRESION: tiene que decir `lower(trim(email))` igual
            # que get_business_by_email, o SQLite no lo usa y el SELECT vuelve a
            # ser un scan. Parcial para no indexar los 4.929 negocios sin mail.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_businesses_email_lower "
                         "ON businesses(lower(trim(email))) WHERE email IS NOT NULL")
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
    # Responsables de un cliente activo: dia a dia, mantenimiento y cobro.
    "encargado_id", "mantenimiento_id", "cobros_id",
    # Cuanto pago por su desarrollo, cargado a mano desde Clientes.
    "monto_pagado", "moneda_pagado",
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


# El `email IS NOT NULL` no es decorativo: sin el, SQLite no puede probar que la
# consulta cumple la condicion del indice PARCIAL y vuelve a hacer un SCAN. Vive
# en una constante para que el test del plan mire esta consulta y no una copia
# que se desincronice.
_SQL_NEGOCIO_POR_MAIL = (
    "SELECT * FROM businesses "
    "WHERE email IS NOT NULL AND lower(trim(email)) = ? "
    "ORDER BY CASE WHEN score IS NULL THEN 1 ELSE 0 END, "
    "         score DESC, scraped_at DESC "
    "LIMIT 1"
)


def get_business_by_email(db_path: str, email) -> Optional[dict]:
    """El negocio que tenga ese mail, sin distinguir mayusculas ni espacios.

    Existe porque `routes/web.py` y `routes/calendly.py` hacian lo mismo a mano:
    traian la tabla entera con `get_all_businesses` y la recorrian en Python.
    Son 3.429 filas con mail sobre 8.358 negocios, ~38 MB de objetos por
    request, y en `web.py` eso cuelga de `/api/web/lead`, que es publico. El
    9-9-2026 el OOM killer se llevo un worker con la maquina en 12 MB libres.

    `businesses` tiene UNIQUE en `phone` pero no en `email`, y hay mails
    repetidos —seis en produccion, uno catorce veces—. Se ordena igual que
    `get_all_businesses` para devolver el mismo de siempre: primero los que
    tienen score, despues score DESC, despues scraped_at DESC. Cambiar ese
    orden cambiaria a que negocio se le atribuye una descarga de la guia.

    Un mail vacio o None devuelve None en vez de parear contra las filas que
    tienen `email` NULL.
    """
    objetivo = (email or "").strip().lower()
    if not objetivo:
        return None
    conn = _connect(db_path)
    try:
        row = conn.execute(_SQL_NEGOCIO_POR_MAIL, (objetivo,)).fetchone()
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
        # El ON DELETE CASCADE del esquema NO alcanza: SQLite trae las foreign
        # keys apagadas y hay que activarlas por conexion, cosa que _connect no
        # hace. Sin este borrado explicito quedarian demos huerfanas con las notas
        # comerciales de un cliente que ya no existe.
        # Primero los presupuestos de esas demos: son BLOBs de hasta 10 MB que
        # nadie podria volver a ver ni borrar.
        conn.execute("DELETE FROM lead_attachments WHERE demo_id IN "
                     "(SELECT id FROM demos_realizadas WHERE client_id = ?)", (business_id,))
        conn.execute("DELETE FROM demos_realizadas WHERE client_id = ?", (business_id,))
        conn.execute("DELETE FROM seg_llamados WHERE lead_id = ?", (business_id,))
        conn.execute("DELETE FROM seg_recordatorios WHERE lead_id = ?", (business_id,))
        conn.execute("DELETE FROM businesses WHERE id = ?", (business_id,))
        conn.commit()
    finally:
        conn.close()


def merge_business(db_path: str, source_id: int, target_id: int) -> None:
    """Transfer all relations from source_id to target_id, then delete source."""
    conn = _connect(db_path)
    try:
        # Seguimiento de leads: el indice unico no deja dos recordatorios
        # pendientes por lead. Si los dos tienen uno abierto, queda el del
        # destino y el del que se fusiona se cierra como reemplazado.
        if conn.execute("SELECT 1 FROM seg_recordatorios WHERE lead_id = ? "
                        "AND estado = 'pendiente'", (target_id,)).fetchone():
            conn.execute(
                "UPDATE seg_recordatorios SET estado = 'hecho', cierre = 'reemplazado', "
                "cerrado_en = CURRENT_TIMESTAMP WHERE lead_id = ? AND estado = 'pendiente'",
                (source_id,))
        for table, col in [
            ("seg_recordatorios", "lead_id"),
            ("seg_llamados",     "lead_id"),
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
    """Las fichas del espejo, con el nombre del negocio del CRM al que estan
    conectadas (`business_name`, None si no hay conexion o el negocio ya no
    existe)."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT nc.*, b.name AS business_name "
            "FROM notion_clients nc LEFT JOIN businesses b ON b.id = nc.business_id "
            "ORDER BY nc.name COLLATE NOCASE")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_notion_client_by_page(db_path: str, notion_page_id: str) -> dict | None:
    conn = _connect(db_path)
    try:
        fila = conn.execute("SELECT * FROM notion_clients WHERE notion_page_id = ?",
                            (notion_page_id,)).fetchone()
        return dict(fila) if fila else None
    finally:
        conn.close()


def vincular_notion_client(db_path: str, cliente_id: int,
                           business_id: int | None) -> None:
    """Conecta una ficha con su negocio del CRM, o la desconecta con None."""
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE notion_clients SET business_id = ? WHERE id = ?",
                     (business_id, cliente_id))
        conn.commit()
    finally:
        conn.close()


def get_notion_client_by_id(db_path: str, cliente_id: int) -> dict | None:
    conn = _connect(db_path)
    try:
        fila = conn.execute("SELECT * FROM notion_clients WHERE id = ?",
                            (cliente_id,)).fetchone()
        return dict(fila) if fila else None
    finally:
        conn.close()


def set_notion_client_status(db_path: str, notion_page_id: str, status: str) -> None:
    """Deja en el espejo el estado que Notion acaba de aceptar.

    Solo se llama despues de un PATCH que salio bien: si no, el tablero del CRM
    mostraria la ficha en la columna vieja hasta el proximo sync, y eso se ve
    como un "rebote" de lo que la persona acaba de arrastrar.
    """
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE notion_clients SET status = ?, notion_synced_at = ? "
            "WHERE notion_page_id = ?", (status, ahora, notion_page_id))
        conn.commit()
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


# ── Demos realizadas (registro comercial) ────────────────────────────────────
# Distinto de la tabla `demos`, que guarda la pagina que genera la IA. Aca queda
# el historial de cada demo que se dio: quien la tuvo y como viene.

_COLUMNAS_DEMO_REALIZADA = {"numero", "realizada_por", "fecha", "actualizacion"}


def crear_demo_realizada(db_path: str, client_id: int, **campos) -> int:
    """Registra una demo dada. Si no se pasa `numero`, se calcula como la
    siguiente del cliente, para que el equipo no tenga que llevar la cuenta."""
    datos = {k: v for k, v in campos.items() if k in _COLUMNAS_DEMO_REALIZADA}
    conn = _connect(db_path)
    try:
        if not datos.get("numero"):
            previas = conn.execute(
                "SELECT COALESCE(MAX(numero), 0) FROM demos_realizadas WHERE client_id = ?",
                (client_id,),
            ).fetchone()[0]
            datos["numero"] = previas + 1
        if not datos.get("fecha"):
            datos["fecha"] = datetime.now().isoformat(timespec="seconds")
        cols = ["client_id", "created_by"] + list(datos)
        vals = [client_id, campos.get("created_by")] + list(datos.values())
        cur = conn.execute(
            f"INSERT INTO demos_realizadas ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in vals)})",
            vals,
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def listar_demos_realizadas(db_path: str, client_id: int | None = None,
                            limite: int | None = None) -> list[dict]:
    """Las demos dadas, de la mas reciente a la mas vieja.

    Trae el nombre de quien la dio y del negocio en el mismo query: la vista los
    muestra siempre juntos, y resolverlos aparte seria un N+1.

    Del presupuesto adjunto trae solo el id y el nombre, NUNCA `file_data`: la
    maquina tiene 512 MB y un listado con los PDFs adentro son decenas de MB por
    pedido. El BLOB se lee unicamente al descargar uno (obtener_presupuesto_demo).
    Tampoco se pide `mime_type`: esta despues de `file_data` en la fila, y
    leerlo obliga a SQLite a recorrer las paginas del BLOB.
    """
    where, params = "", []
    if client_id is not None:
        where = "WHERE d.client_id = ?"
        params.append(client_id)
    sql = f"""
        SELECT d.*, u.name AS realizada_por_nombre, b.name AS cliente_nombre,
               (SELECT a.id FROM lead_attachments a WHERE a.demo_id = d.id
                 ORDER BY a.id DESC LIMIT 1) AS presupuesto_id,
               (SELECT a.name FROM lead_attachments a WHERE a.demo_id = d.id
                 ORDER BY a.id DESC LIMIT 1) AS presupuesto_nombre
        FROM demos_realizadas d
        LEFT JOIN users u ON d.realizada_por = u.id
        LEFT JOIN businesses b ON d.client_id = b.id
        {where}
        ORDER BY COALESCE(d.fecha, d.created_at) DESC, d.id DESC
    """
    if limite is not None:
        sql += " LIMIT ?"
        params.append(int(limite))
    conn = _connect(db_path)
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def actualizar_demo_realizada(db_path: str, demo_id: int, **campos) -> None:
    datos = {k: v for k, v in campos.items() if k in _COLUMNAS_DEMO_REALIZADA}
    if not datos:
        return
    conn = _connect(db_path)
    try:
        conn.execute(
            f"UPDATE demos_realizadas SET {', '.join(f'{k} = ?' for k in datos)} WHERE id = ?",
            [*datos.values(), demo_id],
        )
        conn.commit()
    finally:
        conn.close()


def borrar_demo_realizada(db_path: str, demo_id: int) -> None:
    conn = _connect(db_path)
    try:
        # Las foreign keys estan apagadas (ver delete_business): sin esto el PDF
        # quedaria ocupando el volumen sin ninguna pantalla que lo muestre.
        conn.execute("DELETE FROM lead_attachments WHERE demo_id = ?", (demo_id,))
        conn.execute("DELETE FROM demos_realizadas WHERE id = ?", (demo_id,))
        conn.commit()
    finally:
        conn.close()


def guardar_presupuesto_demo(db_path: str, demo_id: int, lead_id: int, nombre: str,
                             file_data: bytes, mime_type: str) -> int:
    """Adjunta el presupuesto de una demo. Hay uno por demo: subir otro
    reemplaza al anterior en la misma transaccion, para no acumular BLOBs."""
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM lead_attachments WHERE demo_id = ?", (demo_id,))
        cur = conn.execute(
            "INSERT INTO lead_attachments (lead_id, section, name, file_data, mime_type, demo_id) "
            "VALUES (?, 'budget', ?, ?, ?, ?)",
            (lead_id, nombre, file_data, mime_type, demo_id),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def obtener_presupuesto_demo(db_path: str, demo_id: int) -> Optional[dict]:
    """El presupuesto de una demo CON el archivo. Es la unica lectura del BLOB:
    usarla solo para descargar uno, nunca para armar un listado."""
    conn = _connect(db_path)
    try:
        fila = conn.execute(
            "SELECT id, name, file_data, mime_type FROM lead_attachments "
            "WHERE demo_id = ? ORDER BY id DESC LIMIT 1", (demo_id,)
        ).fetchone()
        return dict(fila) if fila else None
    finally:
        conn.close()


def borrar_presupuesto_demo(db_path: str, demo_id: int) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute("DELETE FROM lead_attachments WHERE demo_id = ?", (demo_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def obtener_demo_realizada(db_path: str, demo_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        fila = conn.execute("SELECT * FROM demos_realizadas WHERE id = ?", (demo_id,)).fetchone()
        return dict(fila) if fila else None
    finally:
        conn.close()


# ─── Finanzas ────────────────────────────────────────────────────────────────

_MOVIMIENTO_COLUMNS = {
    "tipo", "fecha", "periodo", "concepto", "categoria", "monto", "moneda",
    "tipo_cambio", "monto_usd", "client_id", "budget_id", "recurrente_id",
    "anulado", "notas", "created_by_id", "created_by_name",
    "facturado", "iva_usd",
}

_RECURRENTE_COLUMNS = {
    "tipo", "concepto", "categoria", "monto", "moneda", "tipo_cambio",
    "dia_del_mes", "desde", "hasta", "activo", "client_id", "notas",
    "facturado",
}


def _insert(db_path: str, tabla: str, columnas: set, fields: dict,
            obligatorias: tuple) -> int:
    # Primero las claves desconocidas: si un campo obligatorio viene mal
    # escrito, este mensaje señala el nombre exacto que está mal. Si
    # chequeáramos "faltan obligatorios" primero, un typo en "concepto"
    # se reportaría como "falta concepto" y escondería la causa real.
    invalidos = set(fields) - columnas
    if invalidos:
        raise ValueError(f"{tabla}: columnas inválidas {invalidos}")
    faltan = [c for c in obligatorias if c not in fields]
    if faltan:
        raise ValueError(f"{tabla}: faltan campos obligatorios {faltan}")
    cols = list(fields)
    vals = list(fields.values())
    marcas = ", ".join("?" for _ in vals)
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            f"INSERT INTO {tabla} ({', '.join(cols)}) VALUES ({marcas})", vals)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _update(db_path: str, tabla: str, columnas: set, fila_id: int,
            fields: dict) -> None:
    invalidos = set(fields) - columnas
    if invalidos:
        raise ValueError(f"{tabla}: columnas inválidas {invalidos}")
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields = {**fields, "id": fila_id}
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE {tabla} SET {set_clause} WHERE id = :id", fields)
        conn.commit()
    finally:
        conn.close()


def _delete(db_path: str, tabla: str, fila_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(f"DELETE FROM {tabla} WHERE id = ?", (fila_id,))
        conn.commit()
    finally:
        conn.close()


def _get_one(db_path: str, tabla: str, fila_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        fila = conn.execute(f"SELECT * FROM {tabla} WHERE id = ?",
                            (fila_id,)).fetchone()
        return dict(fila) if fila else None
    finally:
        conn.close()


def crear_movimiento(db_path: str, **fields) -> int:
    return _insert(db_path, "finanzas_movimientos", _MOVIMIENTO_COLUMNS, fields,
                   ("tipo", "fecha", "periodo", "concepto", "categoria",
                    "monto", "moneda", "monto_usd"))


def actualizar_movimiento(db_path: str, mov_id: int, **fields) -> None:
    _update(db_path, "finanzas_movimientos", _MOVIMIENTO_COLUMNS, mov_id, fields)


def borrar_movimiento(db_path: str, mov_id: int) -> None:
    _delete(db_path, "finanzas_movimientos", mov_id)


def get_movimiento(db_path: str, mov_id: int) -> Optional[dict]:
    return _get_one(db_path, "finanzas_movimientos", mov_id)


def listar_movimientos(db_path: str, desde: Optional[str] = None,
                       hasta: Optional[str] = None, tipo: Optional[str] = None,
                       categoria: Optional[str] = None,
                       client_id: Optional[int] = None,
                       incluir_anulados: bool = False) -> list[dict]:
    """Movimientos ordenados por fecha descendente.

    `desde` y `hasta` son períodos 'YYYY-MM', ambos inclusive. Por defecto no
    devuelve los anulados: un movimiento anulado es un fijo que se borró y que
    solo sigue en la tabla para que la materialización no lo regenere.
    """
    partes: list[str] = []
    params: list = []
    if not incluir_anulados:
        partes.append("anulado = 0")
    if desde is not None:
        partes.append("periodo >= ?")
        params.append(desde)
    if hasta is not None:
        partes.append("periodo <= ?")
        params.append(hasta)
    if tipo is not None:
        partes.append("tipo = ?")
        params.append(tipo)
    if categoria is not None:
        partes.append("categoria = ?")
        params.append(categoria)
    if client_id is not None:
        partes.append("client_id = ?")
        params.append(client_id)
    where = f"WHERE {' AND '.join(partes)}" if partes else ""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            f"SELECT * FROM finanzas_movimientos {where} "
            "ORDER BY fecha DESC, id DESC", params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def marcar_mes_abierto(db_path: str, periodo: str, quien: str = "sistema") -> None:
    """Reabre un mes cerrado. Idempotente: reabrir dos veces no duplica."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO finanzas_meses_abiertos (periodo, abierto_por) "
            "VALUES (?, ?) ON CONFLICT(periodo) DO UPDATE SET abierto_por = ?",
            (periodo, quien, quien))
        conn.commit()
    finally:
        conn.close()


def marcar_mes_cerrado(db_path: str, periodo: str) -> None:
    """Vuelve a cerrar un mes reabierto. Cerrar uno que nunca se abrio no hace
    nada, que es lo correcto: ya estaba cerrado."""
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM finanzas_meses_abiertos WHERE periodo = ?",
                     (periodo,))
        conn.commit()
    finally:
        conn.close()


def listar_meses_abiertos(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "SELECT * FROM finanzas_meses_abiertos ORDER BY periodo DESC")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


_POR_COBRAR_COLUMNS = {
    "client_id", "concepto", "monto_usd", "vence", "origen_movimiento_id",
    "cobrado_movimiento_id", "notas",
}


def crear_por_cobrar(db_path: str, **fields) -> int:
    return _insert(db_path, "finanzas_por_cobrar", _POR_COBRAR_COLUMNS, fields,
                   ("concepto", "monto_usd"))


def actualizar_por_cobrar(db_path: str, pc_id: int, **fields) -> None:
    _update(db_path, "finanzas_por_cobrar", _POR_COBRAR_COLUMNS, pc_id, fields)


def borrar_por_cobrar(db_path: str, pc_id: int) -> None:
    _delete(db_path, "finanzas_por_cobrar", pc_id)


def get_por_cobrar(db_path: str, pc_id: int) -> Optional[dict]:
    return _get_one(db_path, "finanzas_por_cobrar", pc_id)


def listar_por_cobrar(db_path: str, incluir_cobrados: bool = False) -> list[dict]:
    """Lo que falta cobrar, lo mas urgente primero.

    Los que no tienen fecha van al final, pero no se descartan: sin
    vencimiento no significa "no urgente", significa que nadie lo puso.
    """
    where = "" if incluir_cobrados else "WHERE p.cobrado_movimiento_id IS NULL"
    conn = _connect(db_path)
    try:
        cur = conn.execute(f"""
            SELECT p.*, b.name AS client_name
            FROM finanzas_por_cobrar p
            LEFT JOIN businesses b ON p.client_id = b.id
            {where}
            ORDER BY CASE WHEN p.vence IS NULL OR p.vence = '' THEN 1 ELSE 0 END,
                     p.vence ASC, p.id ASC
        """)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def crear_recurrente(db_path: str, **fields) -> int:
    return _insert(db_path, "finanzas_recurrentes", _RECURRENTE_COLUMNS, fields,
                   ("tipo", "concepto", "categoria", "monto", "moneda", "desde"))


def actualizar_recurrente(db_path: str, rec_id: int, **fields) -> None:
    _update(db_path, "finanzas_recurrentes", _RECURRENTE_COLUMNS, rec_id, fields)


def borrar_recurrente(db_path: str, rec_id: int) -> None:
    """Borra la definición del fijo.

    Los movimientos que ya generó quedan vivos: son plata que se gastó. Para
    dejar de generar hacia adelante sin borrar nada está `activo = 0`.
    """
    _delete(db_path, "finanzas_recurrentes", rec_id)


def get_recurrente(db_path: str, rec_id: int) -> Optional[dict]:
    return _get_one(db_path, "finanzas_recurrentes", rec_id)


def listar_recurrentes(db_path: str, solo_activos: bool = False) -> list[dict]:
    where = "WHERE activo = 1" if solo_activos else ""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            f"SELECT * FROM finanzas_recurrentes {where} ORDER BY concepto")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ─── Simulador financiero ────────────────────────────────────────────────────

def listar_clientes_activos(db_path: str) -> list[dict]:
    """Los clientes de verdad: los que están en una etapa de ETAPAS_CLIENTE.

    Es la lista con la que el simulador precarga los mantenimientos.
    """
    marcas = ", ".join("?" for _ in ETAPAS_CLIENTE)
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            f"SELECT id, name, crm_status FROM businesses "
            f"WHERE crm_status IN ({marcas}) ORDER BY name COLLATE NOCASE, id",
            list(ETAPAS_CLIENTE))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def crear_escenario(db_path: str, nombre: str, datos: str,
                    created_by_id=None, created_by_name=None) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO simulador_escenarios "
            "(nombre, datos, created_by_id, created_by_name) VALUES (?, ?, ?, ?)",
            (nombre, datos, created_by_id, created_by_name))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def actualizar_escenario(db_path: str, escenario_id: int, nombre: str,
                         datos: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE simulador_escenarios SET nombre = ?, datos = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (nombre, datos, escenario_id))
        conn.commit()
    finally:
        conn.close()


def borrar_escenario(db_path: str, escenario_id: int) -> None:
    _delete(db_path, "simulador_escenarios", escenario_id)


def get_escenario(db_path: str, escenario_id: int) -> Optional[dict]:
    return _get_one(db_path, "simulador_escenarios", escenario_id)


def listar_escenarios(db_path: str) -> list[dict]:
    """Sin `datos`: la lista es para elegir, el escenario entero se pide aparte."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "SELECT id, nombre, created_by_name, created_at, updated_at "
            "FROM simulador_escenarios ORDER BY updated_at DESC, id DESC")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ─── Equipo ──────────────────────────────────────────────────────────────────
# Sin dinero en ningún lado: personas, ausencias en horas y recuperos en horas.

# (nombre, rol, reporta_a por nombre, lleva_horas, horas_por_dia)
_EQUIPO_PRECARGA = (
    ("Juan Pereyra", "Comercial y administración", None, False, 4),
    ("Javier Tomasetti", "Legal", None, False, 4),
    ("Andrés Rosi", "Marketing digital", "Juan Pereyra", False, 4),
    ("Matías Domínguez", "CTO", "Juan Pereyra", False, 4),
    ("Guillermo Paredes", "Contador", "Juan Pereyra", False, 4),
    ("Juan Tomasetti", "Programador", "Matías Domínguez", True, 4),
    ("Gonzalo Siuciak", "Programador · project manager", "Matías Domínguez", True, 4),
)

# Quiénes arrancan en cada Daily (Juan, 16/9). Daily Programador: Juan
# Tomasetti, Gonzalo y Matías. Daily Admin: Juan Pereyra ("Juanchi") y
# Javier. La primera versión (15/9) tenía solo a Juan Tomasetti y Gonzalo:
# `_sumar_personas_daily` suma lo nuevo una sola vez.
_PROGRAMADORES_PRECARGA = ("Juan Tomasetti", "Gonzalo Siuciak", "Matías Domínguez")
_PROGRAMADORES_SUMADOS_16_9 = ("Matías Domínguez",)
_ADMIN_DAILY_PRECARGA = ("Juan Pereyra", "Javier Tomasetti")
_APODOS_PRECARGA = (("Juan Pereyra", "Juanchi"),)


# ─── Seguimiento de leads ────────────────────────────────────────────────────

_SEG_PENDIENTES = """
    SELECT r.id, r.lead_id, r.fecha, r.hora, r.motivo, r.nota, r.creado_en,
           b.name AS nombre, b.phone AS telefono, b.category AS rubro,
           ci.business_name AS empresa, ci.rubro AS ci_rubro,
           (SELECT l.resultado FROM seg_llamados l WHERE l.lead_id = r.lead_id
             ORDER BY l.fecha DESC, l.id DESC LIMIT 1) AS ultimo_resultado
      FROM seg_recordatorios r
      JOIN businesses b ON b.id = r.lead_id
      LEFT JOIN client_info ci ON ci.client_id = r.lead_id
     WHERE r.estado = 'pendiente'
"""


def seg_crear_recordatorio(db_path: str, lead_id: int, fecha: str, motivo: str,
                           hora: Optional[str] = None,
                           nota: Optional[str] = None) -> tuple[int, list[int]]:
    """Crea un recordatorio pendiente y cierra el que el lead tuviera abierto.

    Devuelve (id nuevo, ids cerrados). Todo en una transacción: nunca quedan
    dos pendientes, ni ninguno si el INSERT falla.
    """
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        abiertos = [f[0] for f in conn.execute(
            "SELECT id FROM seg_recordatorios WHERE lead_id = ? AND estado = 'pendiente'",
            (lead_id,))]
        if abiertos:
            conn.execute(
                "UPDATE seg_recordatorios SET estado = 'hecho', cierre = 'reemplazado', "
                "cerrado_en = CURRENT_TIMESTAMP WHERE lead_id = ? AND estado = 'pendiente'",
                (lead_id,))
        rid = conn.execute(
            "INSERT INTO seg_recordatorios (lead_id, fecha, hora, motivo, nota) "
            "VALUES (?, ?, ?, ?, ?)", (lead_id, fecha, hora, motivo, nota)).lastrowid
        conn.commit()
        return rid, abiertos
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def seg_get_recordatorio(db_path: str, recordatorio_id: int) -> Optional[dict]:
    return _get_one(db_path, "seg_recordatorios", recordatorio_id)


def seg_mover_fecha(db_path: str, recordatorio_id: int, fecha: str) -> bool:
    """Corre la fecha de un pendiente. False si ya no está pendiente."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE seg_recordatorios SET fecha = ? WHERE id = ? AND estado = 'pendiente'",
            (fecha, recordatorio_id))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def seg_cerrar_con_llamado(db_path: str, recordatorio_id: int, fecha_llamado: str,
                           resultado: str, creado_por: Optional[str] = None,
                           proximo: Optional[dict] = None) -> Optional[dict]:
    """Marca hecho un pendiente, guarda el llamado y, si hay, crea el próximo.

    `proximo` es {fecha, hora, motivo, nota} o None ("no hace falta volver a
    llamar"). Devuelve {lead_id, llamado_id, nuevo_id}, o None si el
    recordatorio ya no estaba pendiente.
    """
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        fila = conn.execute(
            "SELECT lead_id FROM seg_recordatorios WHERE id = ? AND estado = 'pendiente'",
            (recordatorio_id,)).fetchone()
        if not fila:
            conn.rollback()
            return None
        lead_id = fila[0]
        conn.execute(
            "UPDATE seg_recordatorios SET estado = 'hecho', cierre = 'llamado', "
            "cerrado_en = CURRENT_TIMESTAMP WHERE id = ?", (recordatorio_id,))
        llamado_id = conn.execute(
            "INSERT INTO seg_llamados (lead_id, fecha, resultado, recordatorio_id, creado_por) "
            "VALUES (?, ?, ?, ?, ?)",
            (lead_id, fecha_llamado, resultado, recordatorio_id, creado_por)).lastrowid
        nuevo_id = None
        if proximo:
            nuevo_id = conn.execute(
                "INSERT INTO seg_recordatorios (lead_id, fecha, hora, motivo, nota) "
                "VALUES (?, ?, ?, ?, ?)",
                (lead_id, proximo["fecha"], proximo.get("hora"), proximo["motivo"],
                 proximo.get("nota"))).lastrowid
        conn.commit()
        return {"lead_id": lead_id, "llamado_id": llamado_id, "nuevo_id": nuevo_id}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def seg_listar_pendientes(db_path: str, lead_id: Optional[int] = None) -> list[dict]:
    """Los pendientes con el nombre, el teléfono y el último resultado del lead."""
    sql = _SEG_PENDIENTES + (" AND r.lead_id = ?" if lead_id is not None else "")
    sql += " ORDER BY r.fecha, r.hora IS NULL, r.hora, r.id"
    conn = _connect(db_path)
    try:
        filas = conn.execute(sql, (lead_id,) if lead_id is not None else ()).fetchall()
        return [dict(f) for f in filas]
    finally:
        conn.close()


def seg_llamados_del_lead(db_path: str, lead_id: int) -> list[dict]:
    """El historial de llamados del lead, del más nuevo al más viejo."""
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT l.*, r.motivo AS motivo FROM seg_llamados l "
            "LEFT JOIN seg_recordatorios r ON r.id = l.recordatorio_id "
            "WHERE l.lead_id = ? ORDER BY l.fecha DESC, l.id DESC", (lead_id,)).fetchall()
        return [dict(f) for f in filas]
    finally:
        conn.close()


def _sembrar_equipo(conn: sqlite3.Connection) -> int:
    """Precarga idempotente de las siete personas del organigrama.

    Por nombre (hay un índice único): si ya están, no se duplican ni se pisa
    nada. `reporta_a` se escribe solo para las filas que se acaban de crear,
    así un cambio hecho a mano en la base sobrevive a los reinicios.
    Devuelve cuántas personas creó.
    """
    nuevas = []
    for nombre, rol, _jefe, lleva, horas in _EQUIPO_PRECARGA:
        cur = conn.execute(
            "INSERT OR IGNORE INTO equipo_personas "
            "(nombre, rol, lleva_horas, horas_por_dia, activo) VALUES (?,?,?,?,1)",
            (nombre, rol, 1 if lleva else 0, horas))
        if cur.rowcount:
            nuevas.append(nombre)
    jefes = {nombre: jefe for nombre, _r, jefe, _l, _h in _EQUIPO_PRECARGA}
    for nombre in nuevas:
        if jefes[nombre]:
            conn.execute(
                "UPDATE equipo_personas SET reporta_a = "
                "(SELECT id FROM equipo_personas WHERE nombre = ?) "
                "WHERE nombre = ? AND reporta_a IS NULL",
                (jefes[nombre], nombre))
    conn.commit()
    return len(nuevas)


def listar_personas_equipo(db_path: str, incluir_inactivas: bool = False) -> list[dict]:
    """En orden de alta: es el orden de los hermanos en el organigrama."""
    conn = _connect(db_path)
    try:
        where = "" if incluir_inactivas else "WHERE activo = 1"
        cur = conn.execute(f"SELECT * FROM equipo_personas {where} ORDER BY id")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_persona_equipo(db_path: str, persona_id: int) -> Optional[dict]:
    return _get_one(db_path, "equipo_personas", persona_id)


# ── Daily Programador ────────────────────────────────────────────────────────
# Las tablas se crean en init_db. Todo va por persona del equipo.

# La marca de `equipo_personas` que dice quién aparece en cada Daily.
_MARCAS_DAILY = {"programador": "programador", "admin": "admin_daily"}


def _sumar_personas_daily(conn: sqlite3.Connection) -> None:
    """Una sola vez (la llama init_db al crear `admin_daily`): Matías entra a
    Daily Programador, Juan Pereyra y Javier a Daily Admin, y Juan Pereyra se
    muestra como "Juanchi". Solo suma: no le saca la marca a nadie. Juan Pereyra
    y Javier ya están en la precarga de Equipo."""
    conn.executemany("UPDATE equipo_personas SET programador = 1 WHERE nombre = ?",
                     [(n,) for n in _PROGRAMADORES_SUMADOS_16_9])
    conn.executemany("UPDATE equipo_personas SET admin_daily = 1 WHERE nombre = ?",
                     [(n,) for n in _ADMIN_DAILY_PRECARGA])
    conn.executemany("UPDATE equipo_personas SET apodo = ? WHERE nombre = ? AND (apodo IS NULL OR apodo = '')",
                     [(apodo, nombre) for nombre, apodo in _APODOS_PRECARGA])
    conn.commit()


def listar_personas_daily(db_path: str, seccion: str = "programador") -> list[dict]:
    """Las personas activas de un Daily, en orden de alta."""
    campo = _MARCAS_DAILY[seccion]
    conn = _connect(db_path)
    try:
        cur = conn.execute(f"SELECT * FROM equipo_personas WHERE activo = 1 AND {campo} = 1 ORDER BY id")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def listar_programadores(db_path: str) -> list[dict]:
    return listar_personas_daily(db_path, "programador")


def crear_actividad_daily(db_path: str, persona_id: int, fecha: str, texto: str,
                          created_by_id: int | None = None,
                          created_by_name: str | None = None,
                          hora: str | None = None, nota: str | None = None,
                          seccion: str = "programador") -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO daily_actividades "
            "(persona_id, fecha, texto, hora, nota, seccion, created_by_id, created_by_name) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (persona_id, fecha, texto, hora, nota, seccion, created_by_id, created_by_name))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_actividad_daily(db_path: str, actividad_id: int) -> Optional[dict]:
    return _get_one(db_path, "daily_actividades", actividad_id)


_CAMPOS_ACTIVIDAD_DAILY = ("texto", "hecha", "fecha", "pasada_de", "hora", "nota")


def actualizar_actividad_daily(db_path: str, actividad_id: int, **campos) -> None:
    campos = {k: v for k, v in campos.items() if k in _CAMPOS_ACTIVIDAD_DAILY}
    if not campos:
        return
    sets = ", ".join(f"{k} = ?" for k in campos)
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE daily_actividades SET {sets} WHERE id = ?",
                     (*campos.values(), actividad_id))
        conn.commit()
    finally:
        conn.close()


def borrar_actividad_daily(db_path: str, actividad_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM daily_actividades WHERE id = ?", (actividad_id,))
        conn.commit()
    finally:
        conn.close()


def listar_actividades_daily(db_path: str, persona_id: int, fecha: str,
                             seccion: str = "programador") -> list[dict]:
    conn = _connect(db_path)
    try:
        # Las que tienen hora primero y en orden de hora; las otras, como se cargaron.
        cur = conn.execute("SELECT * FROM daily_actividades WHERE persona_id = ? AND fecha = ? AND seccion = ? "
                           "ORDER BY hora IS NULL, hora, id", (persona_id, fecha, seccion))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def crear_recordatorio_daily(db_path: str, persona_id: int, texto: str, frecuencia: str,
                             dias: str, desde: str, activo: int = 1,
                             created_by_id: int | None = None,
                             created_by_name: str | None = None,
                             hora: str | None = None, nota: str | None = None,
                             seccion: str = "programador") -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO daily_recordatorios "
            "(persona_id, texto, frecuencia, dias, activo, desde, hora, nota, seccion, created_by_id, created_by_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (persona_id, texto, frecuencia, dias, activo, desde, hora, nota, seccion, created_by_id, created_by_name))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_recordatorio_daily(db_path: str, recordatorio_id: int) -> Optional[dict]:
    return _get_one(db_path, "daily_recordatorios", recordatorio_id)


_CAMPOS_RECORDATORIO_DAILY = ("texto", "frecuencia", "dias", "activo", "hora", "nota")


def actualizar_recordatorio_daily(db_path: str, recordatorio_id: int, **campos) -> None:
    """No toca `daily_marcas`: editar un recordatorio no borra lo hecho otros días."""
    campos = {k: v for k, v in campos.items() if k in _CAMPOS_RECORDATORIO_DAILY}
    if not campos:
        return
    sets = ", ".join(f"{k} = ?" for k in campos)
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE daily_recordatorios SET {sets} WHERE id = ?",
                     (*campos.values(), recordatorio_id))
        conn.commit()
    finally:
        conn.close()


def borrar_recordatorio_daily(db_path: str, recordatorio_id: int) -> None:
    """Se lleva sus marcas. No depende de que SQLite tenga las foreign keys
    prendidas."""
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM daily_marcas WHERE recordatorio_id = ?", (recordatorio_id,))
        conn.execute("DELETE FROM daily_recordatorios WHERE id = ?", (recordatorio_id,))
        conn.commit()
    finally:
        conn.close()


def listar_recordatorios_daily(db_path: str, persona_id: int, seccion: str = "programador") -> list[dict]:
    conn = _connect(db_path)
    try:
        cur = conn.execute("SELECT * FROM daily_recordatorios WHERE persona_id = ? AND seccion = ? ORDER BY id",
                           (persona_id, seccion))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def listar_marcas_daily(db_path: str, persona_id: int, fecha: str, seccion: str = "programador") -> set[int]:
    """Los ids de los recordatorios de esa persona y ese Daily marcados como hechos ese día."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "SELECT m.recordatorio_id FROM daily_marcas m "
            "JOIN daily_recordatorios r ON r.id = m.recordatorio_id "
            "WHERE r.persona_id = ? AND r.seccion = ? AND m.fecha = ?", (persona_id, seccion, fecha))
        return {fila[0] for fila in cur.fetchall()}
    finally:
        conn.close()


def marcar_recordatorio_daily(db_path: str, recordatorio_id: int, fecha: str, hecha: bool,
                              created_by_id: int | None = None) -> None:
    """Marca o desmarca un recordatorio en un solo día; los otros días no se tocan."""
    conn = _connect(db_path)
    try:
        if hecha:
            conn.execute("INSERT OR IGNORE INTO daily_marcas (recordatorio_id, fecha, created_by_id) "
                         "VALUES (?,?,?)", (recordatorio_id, fecha, created_by_id))
        else:
            conn.execute("DELETE FROM daily_marcas WHERE recordatorio_id = ? AND fecha = ?",
                         (recordatorio_id, fecha))
        conn.commit()
    finally:
        conn.close()


def crear_ausencia_equipo(db_path: str, persona_id: int, fecha_desde: str,
                          fecha_hasta: str, motivo: str, horas_totales: float,
                          created_by_id: int | None = None,
                          created_by_name: str | None = None) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO equipo_ausencias (persona_id, fecha_desde, fecha_hasta, "
            "motivo, horas_totales, created_by_id, created_by_name) "
            "VALUES (?,?,?,?,?,?,?)",
            (persona_id, fecha_desde, fecha_hasta, motivo, horas_totales,
             created_by_id, created_by_name))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_ausencia_equipo(db_path: str, ausencia_id: int) -> Optional[dict]:
    return _get_one(db_path, "equipo_ausencias", ausencia_id)


def borrar_ausencia_equipo(db_path: str, ausencia_id: int) -> None:
    """Borra la ausencia y sus recuperos juntos. SQLite no tiene las foreign
    keys prendidas en este proyecto, así que el ON DELETE CASCADE del esquema
    no alcanza: sin esto quedarían recuperos huérfanos restando saldo."""
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM equipo_recuperos WHERE ausencia_id = ?", (ausencia_id,))
        conn.execute("DELETE FROM equipo_ausencias WHERE id = ?", (ausencia_id,))
        conn.commit()
    finally:
        conn.close()


def listar_ausencias_equipo(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "SELECT * FROM equipo_ausencias ORDER BY fecha_desde DESC, id DESC")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def crear_recupero_equipo(db_path: str, ausencia_id: int, fecha: str, horas: float,
                          created_by_id: int | None = None,
                          created_by_name: str | None = None) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO equipo_recuperos (ausencia_id, fecha, horas, "
            "created_by_id, created_by_name) VALUES (?,?,?,?,?)",
            (ausencia_id, fecha, horas, created_by_id, created_by_name))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_recupero_equipo(db_path: str, recupero_id: int) -> Optional[dict]:
    return _get_one(db_path, "equipo_recuperos", recupero_id)


def borrar_recupero_equipo(db_path: str, recupero_id: int) -> None:
    _delete(db_path, "equipo_recuperos", recupero_id)


def listar_recuperos_equipo(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        cur = conn.execute("SELECT * FROM equipo_recuperos ORDER BY fecha, id")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ─── Flujos ──────────────────────────────────────────────────────────────────
# Solo roles, nunca nombres de personas (tests/test_flujos.py lo verifica).

# (titulo, rol, detalle, pantalla, destacado, [(porcentaje, descripcion)])
_PASOS_DE_LEAD_A_COBRO = (
    ("Se genera el lead", "Marketing",
     "Meta Ads u Outbound. Cae en Proceso de venta.", "notion_clients", False, ()),
    ("Se atiende el lead", "Comercial",
     "Primer llamado. Se califica y se carga el seguimiento.", None, False, ()),
    ("Se lleva a videollamada", "Comercial",
     "Plantilla de confirmación. Recordatorio automático el mismo día.", None, False, ()),
    ("Se prepara la demo", "Project manager",
     "Se arma sobre el rubro y lo que pidió el lead.", None, False, ()),
    ("Se hace la demo", "Project manager",
     "Queda registrada en Demos, con lo que pidió y lo que objetó.", "demos", False, ()),
    ("Se presupuesta", "Comercial",
     "Dentro de 48 horas. Plantilla de resumen y presupuesto.", None, False, ()),
    ("Se cobra", "Administración",
     "Al confirmar. Se dan de alta el cliente y el proyecto.", "clientes", False,
     ((100, "al confirmar"),)),
    ("Se desarrolla", "Desarrollo",
     "El plazo corre desde que llega el material.", "projects", False, ()),
    ("Se entrega", "Desarrollo",
     "Publicación y capacitación. Se ofrece el mantenimiento.", None, False, ()),
    ("Se mantiene", "Soporte",
     "Cuota mensual. Es el ingreso que se acumula mes a mes.", None, True, ()),
)

# (nombre, descripcion, orden, pasos)
_FLUJOS_PRECARGA = (
    ("De lead a cobro", "Desde que entra un lead hasta que se cobra y queda en mantenimiento.",
     1, _PASOS_DE_LEAD_A_COBRO),
    ("Arranque de proyecto", "Desde que se confirma un proyecto hasta que arranca el desarrollo.", 2, ()),
    ("Cobranza", "Cómo se sigue lo que falta cobrar.", 3, ()),
    ("Alta de una persona", "Qué pasa cuando entra alguien nuevo al equipo.", 4, ()),
)


def _insertar_cobros(conn: sqlite3.Connection, paso_id: int, cobros) -> None:
    for orden, cobro in enumerate(cobros):
        if isinstance(cobro, dict):
            pct, desc = cobro["porcentaje"], cobro["descripcion"]
        else:
            pct, desc = cobro
        conn.execute("INSERT INTO flujo_paso_cobros (paso_id, orden, porcentaje, descripcion) "
                     "VALUES (?,?,?,?)", (paso_id, orden, pct, desc))


def _sembrar_flujos(conn: sqlite3.Connection) -> int:
    """Precarga idempotente de los cuatro flujos, con los diez pasos de "De
    lead a cobro".

    Por nombre (índice único). Los pasos se cargan SOLO para el flujo que se
    acaba de crear: si el flujo ya estaba, no se toca nada, así un paso
    editado, movido o borrado desde la pantalla sobrevive a los reinicios.
    Devuelve cuántos flujos creó.
    """
    creados = 0
    for nombre, descripcion, orden, pasos in _FLUJOS_PRECARGA:
        cur = conn.execute("INSERT OR IGNORE INTO flujos (nombre, descripcion, orden) VALUES (?,?,?)",
                           (nombre, descripcion, orden))
        if not cur.rowcount:
            continue
        creados += 1
        flujo_id = cur.lastrowid
        for numero, (titulo, rol, detalle, pantalla, destacado, cobros) in enumerate(pasos, start=1):
            pid = conn.execute(
                "INSERT INTO flujo_pasos (flujo_id, numero, titulo, rol, detalle, pantalla, destacado) "
                "VALUES (?,?,?,?,?,?,?)",
                (flujo_id, numero, titulo, rol, detalle, pantalla, 1 if destacado else 0)).lastrowid
            _insertar_cobros(conn, pid, cobros)
    conn.commit()
    return creados


def listar_flujos(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM flujos ORDER BY orden, id")]
    finally:
        conn.close()


def get_flujo(db_path: str, flujo_id: int) -> Optional[dict]:
    return _get_one(db_path, "flujos", flujo_id)


def listar_pasos_flujos(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM flujo_pasos ORDER BY flujo_id, numero, id")]
    finally:
        conn.close()


def get_paso_flujo(db_path: str, paso_id: int) -> Optional[dict]:
    return _get_one(db_path, "flujo_pasos", paso_id)


def listar_cobros_pasos_flujo(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM flujo_paso_cobros ORDER BY paso_id, orden, id")]
    finally:
        conn.close()


def _renumerar_flujo(conn: sqlite3.Connection, flujo_id: int, ids: Optional[list] = None) -> list:
    """Deja los números del flujo en 1..n. Sin `ids`, en el orden actual."""
    if ids is None:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM flujo_pasos WHERE flujo_id = ? ORDER BY numero, id", (flujo_id,))]
    for numero, pid in enumerate(ids, start=1):
        conn.execute("UPDATE flujo_pasos SET numero = ? WHERE id = ? AND numero != ?",
                     (numero, pid, numero))
    return ids


def crear_paso_flujo(db_path: str, flujo_id: int, titulo: str, rol: str, detalle: str = "",
                     pantalla: Optional[str] = None, destacado: bool = False,
                     cobros=None) -> int:
    """Lo agrega al final del flujo."""
    conn = _connect(db_path)
    try:
        _renumerar_flujo(conn, flujo_id)
        numero = conn.execute("SELECT COUNT(*) FROM flujo_pasos WHERE flujo_id = ?",
                              (flujo_id,)).fetchone()[0] + 1
        pid = conn.execute(
            "INSERT INTO flujo_pasos (flujo_id, numero, titulo, rol, detalle, pantalla, destacado) "
            "VALUES (?,?,?,?,?,?,?)",
            (flujo_id, numero, titulo, rol, detalle or "", pantalla, 1 if destacado else 0)).lastrowid
        _insertar_cobros(conn, pid, cobros or [])
        conn.commit()
        return pid
    finally:
        conn.close()


def editar_paso_flujo(db_path: str, paso_id: int, titulo: str, rol: str, detalle: str = "",
                      pantalla: Optional[str] = None, destacado: bool = False,
                      cobros=None) -> None:
    """`cobros` en None deja los momentos de cobro como estaban."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE flujo_pasos SET titulo = ?, rol = ?, detalle = ?, pantalla = ?, destacado = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (titulo, rol, detalle or "", pantalla, 1 if destacado else 0, paso_id))
        if cobros is not None:
            conn.execute("DELETE FROM flujo_paso_cobros WHERE paso_id = ?", (paso_id,))
            _insertar_cobros(conn, paso_id, cobros)
        conn.commit()
    finally:
        conn.close()


def borrar_paso_flujo(db_path: str, paso_id: int) -> None:
    """Se lleva sus momentos de cobro (sin foreign keys prendidas, el CASCADE
    no alcanza) y renumera lo que queda."""
    conn = _connect(db_path)
    try:
        fila = conn.execute("SELECT flujo_id FROM flujo_pasos WHERE id = ?", (paso_id,)).fetchone()
        if not fila:
            return
        conn.execute("DELETE FROM flujo_paso_cobros WHERE paso_id = ?", (paso_id,))
        conn.execute("DELETE FROM flujo_pasos WHERE id = ?", (paso_id,))
        _renumerar_flujo(conn, fila[0])
        conn.commit()
    finally:
        conn.close()


def mover_paso_flujo(db_path: str, paso_id: int, posicion: int) -> int:
    """Lleva el paso a `posicion` (1..n, se acota) y renumera el flujo entero.
    Devuelve el número con el que quedó."""
    conn = _connect(db_path)
    try:
        fila = conn.execute("SELECT flujo_id FROM flujo_pasos WHERE id = ?", (paso_id,)).fetchone()
        if not fila:
            return 0
        ids = _renumerar_flujo(conn, fila[0])
        ids.remove(paso_id)
        destino = max(1, min(int(posicion), len(ids) + 1))
        ids.insert(destino - 1, paso_id)
        _renumerar_flujo(conn, fila[0], ids)
        conn.commit()
        return destino
    finally:
        conn.close()


def entregas_de_proyectos(db_path: str) -> list[dict]:
    """Los proyectos del espejo de Notion que tienen fecha de entrega.

    La entrega es `timeline_end`: el final del rango "Timeline" de la database
    Projects (services/notion_service.py). Un Timeline de una sola fecha llega
    sin final y no se toma como entrega: no hay forma de saber si ese día es el
    arranque o la entrega.
    """
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "SELECT id, name, timeline_end FROM projects "
            "WHERE timeline_end IS NOT NULL AND timeline_end != '' ORDER BY timeline_end")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ─── Plantillas de mensajes ──────────────────────────────────────────────────
# Texto exacto del PDF "Plantillas de mensajes - Scalerics" (Juan, 14/9). Los
# párrafos van separados por una línea en blanco, como en el PDF; los cortes
# de renglón dentro de un párrafo del PDF son solo el ancho de la página.

_PLANTILLAS_PRECARGA = (
    # Pedido aparte de Juan (15/9): va primera porque es lo primero que pasa
    # con un lead. Una base que ya tenía las otras cinco la suma sola.
    {
        "clave": "no_atendio", "orden": 5,
        "momento": "LLAMÉ Y NO ATENDIÓ",
        "titulo": "Lead que no atendió", "canal": "WhatsApp",
        "cuerpo": ("¿Cómo estás {nombre}? Te escribe Juan de Scalerics. Respondiste un "
                   "formulario solicitando información acerca de {servicio}. Te llamé para "
                   "que me cuentes un poco y ver cómo te podemos ayudar en lo que estás "
                   "buscando. Cuando tengas unos minutos avisame y te llamo. Saludos."),
        "nota": "Se manda después de llamar sin respuesta.",
        "explicacion": "", "automatica": 0,
    },
    {
        "clave": "confirmacion_agenda", "orden": 10,
        "momento": "DESPUÉS DE LA PRIMERA LLAMADA",
        "titulo": "Confirmación de agenda", "canal": "WhatsApp",
        "cuerpo": ("¿Cómo estás {nombre}? Te habla Juan Pereyra de Scalerics.\n\n"
                   "Quedamos agendados para el {fecha} a las {hora}. Entrás a la "
                   "videollamada con el siguiente link: {link}\n\n"
                   "El mismo día, un rato antes, te mando recordatorio de la "
                   "videollamada. En lo posible confirmame con un okey.\n\n"
                   "Saludos."),
        "nota": "Se manda apenas queda agendada la demo.",
        "explicacion": "", "automatica": 0,
    },
    {
        "clave": "recordatorio_videollamada", "orden": 20,
        "momento": "EL DÍA DE LA DEMO",
        "titulo": "Recordatorio de videollamada", "canal": "WhatsApp",
        "cuerpo": ("¿Cómo estás {nombre}? Este es un recordatorio para la "
                   "videollamada de hoy a las {hora}.\n\n"
                   "Entrás con el link que te pasé arriba.\n\n"
                   "Saludos."),
        "nota": "Automático: se dispara unas horas antes de la demo, sin que lo mandes vos.",
        "explicacion": "", "automatica": 1,
    },
    {
        "clave": "resumen_presupuesto", "orden": 30,
        "momento": "DESPUÉS DE LA DEMO",
        "titulo": "Resumen y presupuesto", "canal": "WhatsApp o mail",
        "cuerpo": ("Hola {nombre}, gracias por el rato de hoy.\n\n"
                   "Te dejo el presupuesto de la {servicio} como quedamos: {monto}, "
                   "entrega en {plazo} desde que arrancamos.\n\n"
                   "Cualquier duda escribime. Si querés avanzar, con confirmarme "
                   "por acá alcanza."),
        "nota": "Adjunta el PDF del presupuesto.",
        "explicacion": "", "automatica": 0,
    },
    {
        "clave": "reactivacion", "orden": 40,
        "momento": "LEAD FRÍO",
        "titulo": "Reactivación", "canal": "WhatsApp",
        "cuerpo": ("¿Cómo estás {nombre}? Avisame si al final seguís interesado "
                   "en avanzar con el {servicio}.\n\n"
                   "Saludos."),
        "nota": "", "explicacion": "", "automatica": 0,
    },
    {
        "clave": "reactivacion_alternativa", "orden": 50,
        "momento": "LEAD FRÍO",
        "titulo": "Alternativa para el de reactivación", "canal": "WhatsApp",
        "cuerpo": ("¿Cómo estás {nombre}? Te escribo por el {servicio} que "
                   "habíamos charlado. ¿Lo dejamos para más adelante o lo retomamos?"),
        "nota": "",
        "explicacion": ("Preguntar si sigue interesado obliga al otro a decidir, y lo "
                        "más fácil es no contestar. Esta versión ofrece dos salidas y "
                        "las dos sirven: incluso el “más adelante” deja una fecha para "
                        "volver a llamar."),
        "automatica": 0,
    },
)

_COLUMNAS_PLANTILLA = ("orden", "momento", "titulo", "canal", "cuerpo", "nota",
                       "explicacion", "automatica")


def _sembrar_plantillas(conn: sqlite3.Connection) -> int:
    """Precarga idempotente de las seis plantillas: las cinco del PDF y la del
    lead que no atendió.

    Por `clave` (única): si ya están —editadas, o borradas, que quedan
    marcadas— no se duplican ni se pisan. Devuelve cuántas creó.
    """
    creadas = 0
    for p in _PLANTILLAS_PRECARGA:
        cur = conn.execute(
            "INSERT OR IGNORE INTO plantillas_mensajes "
            "(clave, orden, momento, titulo, canal, cuerpo, nota, explicacion, "
            " automatica, created_by_name) VALUES (?,?,?,?,?,?,?,?,?,'precarga')",
            (p["clave"],) + tuple(p[c] for c in _COLUMNAS_PLANTILLA))
        creadas += cur.rowcount
    conn.commit()
    return creadas


def listar_plantillas(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        cur = conn.execute("SELECT * FROM plantillas_mensajes WHERE borrada = 0 "
                           "ORDER BY orden, id")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_plantilla(db_path: str, plantilla_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        fila = conn.execute("SELECT * FROM plantillas_mensajes WHERE id = ? AND borrada = 0",
                            (plantilla_id,)).fetchone()
        return dict(fila) if fila else None
    finally:
        conn.close()


def crear_plantilla(db_path: str, created_by_name: Optional[str] = None, **campos) -> int:
    """Una plantilla nueva va al final, salvo que traiga `orden`."""
    datos = {c: campos[c] for c in _COLUMNAS_PLANTILLA if c in campos}
    conn = _connect(db_path)
    try:
        if "orden" not in datos:
            (ultimo,) = conn.execute(
                "SELECT COALESCE(MAX(orden), 0) FROM plantillas_mensajes").fetchone()
            datos["orden"] = int(ultimo) + 10
        cols = list(datos) + ["created_by_name"]
        cur = conn.execute(
            f"INSERT INTO plantillas_mensajes ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            tuple(datos.values()) + (created_by_name,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def actualizar_plantilla(db_path: str, plantilla_id: int, **campos) -> bool:
    datos = {c: campos[c] for c in _COLUMNAS_PLANTILLA if c in campos}
    if not datos:
        return False
    conn = _connect(db_path)
    try:
        sets = ", ".join(f"{c} = ?" for c in datos)
        cur = conn.execute(
            f"UPDATE plantillas_mensajes SET {sets}, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND borrada = 0", tuple(datos.values()) + (plantilla_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def borrar_plantilla(db_path: str, plantilla_id: int) -> bool:
    """Marca la plantilla como borrada (ver el comentario de la tabla)."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE plantillas_mensajes SET borrada = 1, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND borrada = 0", (plantilla_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
