-- A quien se le mando cada mensaje.
--
-- No se guardaba, y por eso no habia forma de preguntar lo unico que delataba
-- el incidente de los 489 avisos: cuantos mensajes le estamos mandando a UNA
-- persona. El volumen total no lo mostraba —eran 24 por hora, un goteo— y
-- lead_id tampoco, porque en un aviso al equipo apunta al lead del que se
-- habla, no a quien lo recibe.
ALTER TABLE messages ADD COLUMN destino TEXT;

CREATE INDEX IF NOT EXISTS idx_messages_destino ON messages(destino, created_at);
