-- Lo que Jev hubiera decidido, al lado de lo que decidio el bot.
--
-- Modo sombra: Jev (TypeSafe) contesta las mismas preguntas que hoy resuelve
-- el modelo que conversa, pero su respuesta no cambia nada de lo que sale.
-- Solo se anota, para medir en conversaciones reales si vale la pena dejarlo
-- decidir. Los logs de Fly no alcanzan: guardan unas pocas horas.
--
-- `bot` y `jev` son JSON: lo que hizo el bot y la respuesta entera de Jev
-- (eleccion, probabilidades, confianza). `coincide` es NULL cuando no hay nada
-- contra que comparar.
CREATE TABLE IF NOT EXISTS jev_sombra (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id     INTEGER REFERENCES leads(id) ON DELETE CASCADE,
  decision    TEXT NOT NULL,
  bot         TEXT,
  jev         TEXT,
  coincide    INTEGER,
  confianza   REAL,
  ms          INTEGER,
  creado_en   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jev_sombra_decision ON jev_sombra(decision, creado_en);
