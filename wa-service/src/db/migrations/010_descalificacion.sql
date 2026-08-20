-- El que no es un cliente posible: el que manda el CV, el proveedor que ofrece
-- algo, el numero equivocado, el que pide un servicio que no hacemos.
--
-- No toca jobs: no hay tipo de job nuevo. Descalificar es dejar de programar
-- cosas, no programar una mas.

ALTER TABLE leads ADD COLUMN no_cliente_motivo TEXT;
ALTER TABLE leads ADD COLUMN no_cliente_desde TEXT;
