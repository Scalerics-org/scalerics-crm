-- Esquema del wa-service. Una sola base SQLite, sin Postgres ni Redis.

CREATE TABLE IF NOT EXISTS leads (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  external_id       TEXT UNIQUE,              -- id del lead en el CRM, para idempotencia
  nombre            TEXT NOT NULL,
  rubro             TEXT,                     -- crudo, como vino del form
  rubro_norm        TEXT,                     -- normalizado a una clave de plantilla
  telefono          TEXT NOT NULL,            -- E.164 sin '+'
  necesidad         TEXT,
  origen            TEXT,                     -- 'form' | 'chat' | 'wa'
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  welcomed_at       TEXT,
  replied_at        TEXT,
  followup_sent_at  TEXT,
  am_notified_at    TEXT,
  status            TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new','welcomed','replied','followed_up','closed','failed'))
);

CREATE INDEX IF NOT EXISTS idx_leads_telefono ON leads(telefono);
CREATE INDEX IF NOT EXISTS idx_leads_status   ON leads(status);

CREATE TABLE IF NOT EXISTS messages (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id         INTEGER REFERENCES leads(id) ON DELETE CASCADE,
  direction       TEXT NOT NULL CHECK (direction IN ('out','in')),
  kind            TEXT NOT NULL CHECK (kind IN ('welcome','followup','am_notice','manual','reply')),
  body            TEXT,
  provider        TEXT NOT NULL,
  provider_msg_id TEXT,
  status          TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','sent','delivered','read','failed')),
  error           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_lead ON messages(lead_id);

-- Reemplaza a BullMQ: las tareas diferidas viven acá y las levanta el scheduler.
CREATE TABLE IF NOT EXISTS jobs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id     INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  type        TEXT NOT NULL CHECK (type IN ('welcome','followup','am_notice')),
  run_at      TEXT NOT NULL,
  attempts    INTEGER NOT NULL DEFAULT 0,
  status      TEXT NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','done','cancelled','failed')),
  last_error  TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status, run_at);

-- Un solo job pendiente por lead y tipo: evita duplicados si el CRM reintenta.
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_unico
  ON jobs(lead_id, type) WHERE status = 'pending';

-- Alimenta los limites de tasa y la rampa de warm-up.
CREATE TABLE IF NOT EXISTS send_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  sent_at       TEXT NOT NULL DEFAULT (datetime('now')),
  telefono      TEXT NOT NULL,
  first_contact INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_send_log_time ON send_log(sent_at);
