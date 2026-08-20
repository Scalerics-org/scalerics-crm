-- Derivacion por abandono: el lead deja de contestar en el medio y lo levanta
-- una persona.
--
-- Es un tipo de job nuevo, y como en 003 hay que recrear la tabla: SQLite no
-- deja modificar un CHECK. Se copia el contenido y se rehacen los indices — si
-- no se rehacen, se pierden en el DROP y el indice unico es lo unico que evita
-- que un lead junte veinte jobs del mismo tipo.

CREATE TABLE jobs_nueva (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id     INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  type        TEXT NOT NULL
              CHECK (type IN ('welcome','followup','am_notice','reminder_24h','reminder_30m','abandono')),
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
