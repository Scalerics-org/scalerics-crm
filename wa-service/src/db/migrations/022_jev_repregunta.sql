-- Si ya se le repregunto que necesita porque no quedaba claro.
--
-- Con JEV_MODO=decide, un lead cuya necesidad Jev no entiende no se da por
-- calificado: el bot vuelve a preguntar. Una sola vez — sin esta marca, cada
-- respuesta confusa dispararia otra repregunta y el lead quedaria en un loop
-- de la misma pregunta. A la segunda va a una persona.
ALTER TABLE leads ADD COLUMN necesidad_repreguntada INTEGER NOT NULL DEFAULT 0;
