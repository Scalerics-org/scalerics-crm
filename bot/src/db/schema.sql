-- Scalerics WhatsApp Bot — Schema
-- Ejecutar una sola vez contra la base de datos de Railway

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS leads (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  phone             VARCHAR(20) UNIQUE NOT NULL,
  name              VARCHAR(100),

  -- Respuestas de calificación (1-5 según opciones del menú)
  business_type     SMALLINT,   -- 1=Agencia/Consultora 2=Ecommerce 3=Servicios prof. 4=SaaS 5=Otro
  main_problem      SMALLINT,   -- 1=No respondo a tiempo 2=Tareas repetitivas 3=Sin métricas 4=Escalar sin contratar 5=Otro
  team_size         SMALLINT,   -- 1=Solo yo 2=2-5 personas 3=6-20 personas 4=Más de 20
  budget            SMALLINT,   -- 1=Presupuesto disponible 2=Depende del ROI 3=Aún no
  urgency           SMALLINT,   -- 1=Este mes 2=2-3 meses 3=Evaluando

  -- Scoring
  score             SMALLINT,
  priority          VARCHAR(10),          -- high / medium / low
  score_reason      TEXT,

  -- Estado del lead en el funnel
  state             VARCHAR(30) NOT NULL DEFAULT 'NEW',

  -- Flags
  opt_out           BOOLEAN DEFAULT FALSE,
  human_requested   BOOLEAN DEFAULT FALSE,
  reminder_24h_sent BOOLEAN DEFAULT FALSE,
  reminder_1h_sent  BOOLEAN DEFAULT FALSE,

  -- Reunión
  meeting_url       TEXT,
  meeting_time      TIMESTAMPTZ,

  -- Origen
  source            VARCHAR(50),         -- qr_code / link / broadcast / organic
  utm_campaign      VARCHAR(100),

  -- Timestamps
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  updated_at        TIMESTAMPTZ DEFAULT NOW(),
  last_message_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS messages (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id    UUID REFERENCES leads(id) ON DELETE CASCADE,
  direction  VARCHAR(3) NOT NULL CHECK (direction IN ('in', 'out')),
  content    TEXT,
  wa_msg_id  VARCHAR(100),
  sent_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Idempotent column additions for existing deployments
ALTER TABLE leads ADD COLUMN IF NOT EXISTS business_name TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS colors TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS instagram_web TEXT;

CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);
CREATE INDEX IF NOT EXISTS idx_leads_state ON leads(state);
CREATE INDEX IF NOT EXISTS idx_leads_last_message ON leads(last_message_at);
CREATE INDEX IF NOT EXISTS idx_messages_lead_id ON messages(lead_id);

-- Trigger para actualizar updated_at automáticamente
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS leads_updated_at ON leads;
CREATE TRIGGER leads_updated_at
  BEFORE UPDATE ON leads
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();
