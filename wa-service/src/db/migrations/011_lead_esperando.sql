-- Cuando se le aviso al equipo que un lead ya derivado sigue escribiendo.
--
-- No se reusa am_notified_at, que es el aviso del alta: son dos cosas
-- distintas y compartir la columna haria que una pise a la otra.
ALTER TABLE leads ADD COLUMN humano_avisado_at TEXT;
