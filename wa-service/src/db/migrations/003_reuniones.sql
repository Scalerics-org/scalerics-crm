-- Reunion agendada y sus recordatorios.
--
-- Son dos tipos distintos y no uno solo con parametro, porque el indice unico
-- de jobs es por (lead_id, type): con un unico tipo 'reminder' no podrian
-- convivir el del dia antes y el de los 30 minutos.
--
-- SQLite no deja modificar un CHECK: hay que recrear la tabla. Se copia el
-- contenido y se rehacen los indices.

CREATE TABLE jobs_nueva (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id     INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  type        TEXT NOT NULL
              CHECK (type IN ('welcome','followup','am_notice','reminder_24h','reminder_30m')),
  run_at      TEXT NOT NULL,
  attempts    INTEGER NOT NULL DEFAULT 0,
  status      TEXT NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','done','cancelled','failed')),
  last_error  TEXT
);

INSERT INTO jobs_nueva (id, lead_id, type, run_at, attempts, status, last_error)
  SELECT id, lead_id, type, run_at, attempts, status, last_error FROM jobs;

DROP TABLE jobs;
ALTER TABLE jobs_nueva RENAME TO jobs;

CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status, run_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_unico
  ON jobs(lead_id, type) WHERE status = 'pending';

-- Cuando se agendo, para no volver a insistir con el follow-up.
ALTER TABLE leads ADD COLUMN meeting_booked_at TEXT;
