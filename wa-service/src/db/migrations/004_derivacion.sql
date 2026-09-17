-- Cuantas veces el lead pregunto por precio.
--
-- El superprompt manda derivar cuando "pregunta precio exacto y no se conforma
-- con la respuesta general": la primera vez se le contesta el criterio, a la
-- segunda pasa a un humano. Hace falta contarlas.
ALTER TABLE leads ADD COLUMN consultas_precio INTEGER NOT NULL DEFAULT 0;

-- Por que se derivo, para que el AM sepa a que se enfrenta antes de abrir el chat.
ALTER TABLE leads ADD COLUMN motivo_derivacion TEXT;
