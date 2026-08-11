-- Embudo de calificacion, portado desde bot/.
--
-- El estado del FSM y el contador de reintentos vivian en Redis; aca van en la
-- fila del lead, que pasa a ser la unica fuente de verdad. Eso saca Redis de las
-- dependencias del servicio.

ALTER TABLE leads ADD COLUMN fsm_state       TEXT NOT NULL DEFAULT 'NEW';
ALTER TABLE leads ADD COLUMN fsm_retries     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE leads ADD COLUMN opt_out         INTEGER NOT NULL DEFAULT 0;
ALTER TABLE leads ADD COLUMN human_requested INTEGER NOT NULL DEFAULT 0;

-- Respuestas del embudo
ALTER TABLE leads ADD COLUMN business_name   TEXT;
ALTER TABLE leads ADD COLUMN business_type   INTEGER;  -- 1 web 2 ecommerce 3 automatizacion 4 app
ALTER TABLE leads ADD COLUMN budget          INTEGER;  -- 1 <500 2 500-3000 3 >3000 4 no se
ALTER TABLE leads ADD COLUMN team_size       INTEGER;  -- 1 solo 2 2-5 3 6-20 4 +20
ALTER TABLE leads ADD COLUMN colors          TEXT;
ALTER TABLE leads ADD COLUMN instagram_web   TEXT;
ALTER TABLE leads ADD COLUMN needs           TEXT;

-- Scoring
ALTER TABLE leads ADD COLUMN score           INTEGER;
ALTER TABLE leads ADD COLUMN priority        TEXT;
ALTER TABLE leads ADD COLUMN score_reason    TEXT;

-- Reunion
ALTER TABLE leads ADD COLUMN meeting_url     TEXT;
ALTER TABLE leads ADD COLUMN meeting_time    TEXT;

CREATE INDEX IF NOT EXISTS idx_leads_fsm ON leads(fsm_state);
