-- El lead que dice "mas adelante": queda en pausa y se le vuelve a escribir
-- cuando dijo, en vez de insistirle a las 72 horas o derivarlo al comercial
-- como si se hubiera ido.
--
-- Tipo de job nuevo, asi que hay que recrear la tabla —SQLite no deja modificar
-- un CHECK— y REHACER LOS INDICES: se pierden en el DROP, y el unico es lo que
-- evita que un lead junte veinte jobs iguales.

CREATE TABLE jobs_nueva (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id     INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  type        TEXT NOT NULL
              CHECK (type IN ('welcome','followup','am_notice','reminder_24h','reminder_30m','abandono','nurture')),
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

-- Cuando se puso en pausa y con que palabras, para que el equipo lo vea en el
-- CRM sin tener que leer la conversacion entera.
ALTER TABLE leads ADD COLUMN nurture_desde TEXT;
ALTER TABLE leads ADD COLUMN nurture_motivo TEXT;
