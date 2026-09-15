-- Entrantes ya vistos, para no contestar dos veces el mismo mensaje.
--
-- Cuando Baileys reconecta, WhatsApp le reenvia lo que no quedo confirmado. Sin
-- esto el bot vuelve a procesar el mismo "hola" y manda una segunda respuesta,
-- que ademas llega desordenada respecto de la primera. Paso en produccion: una
-- caida de conexion a mitad de un turno y el lead recibio dos saludos.
--
-- Va en su propia tabla y no sobre messages porque el agrupador junta varios
-- entrantes en un solo turno: los ids individuales no sobreviven ahi.
CREATE TABLE IF NOT EXISTS inbound_seen (
  provider_msg_id TEXT PRIMARY KEY,
  seen_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_inbound_seen_at ON inbound_seen(seen_at);
