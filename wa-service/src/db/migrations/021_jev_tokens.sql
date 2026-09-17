-- Lo que cuesta cada consulta a Jev.
--
-- La sombra guardaba cuanto tardo pero no cuantos tokens entraron, asi que no
-- habia forma de decir cuanto sale por lead. Se cobra solo la entrada (USD
-- 0,042 por millon; la salida es gratis), asi que con esta columna alcanza.
ALTER TABLE jev_sombra ADD COLUMN input_tokens INTEGER;
