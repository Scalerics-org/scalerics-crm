-- Los mensajes que estan esperando para salir.
--
-- La cola vive en memoria, que esta bien para lo que sale en segundos. Lo que
-- espera al horario comercial es otra cosa: el 6-9 entraron dos leads un
-- domingo de madrugada y la bienvenida de los dos quedo esperando al lunes a
-- las 9. La maquina se reciclo en el medio y se perdieron las dos, sin dejar
-- rastro. Los leads recibieron "¿Cómo se llama tu negocio?" en frio, sin saber
-- quien les escribia.
--
-- Solo se guarda lo que quedo esperando. Lo que sale de una no toca la base.
CREATE TABLE IF NOT EXISTS salientes (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  telefono      TEXT NOT NULL,
  texto         TEXT NOT NULL,
  kind          TEXT NOT NULL,
  lead_id       INTEGER,
  no_antes_de   TEXT NOT NULL,
  encolado_en   TEXT,
  vence_en_min  INTEGER,
  creado_en     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_salientes_turno ON salientes(no_antes_de);
