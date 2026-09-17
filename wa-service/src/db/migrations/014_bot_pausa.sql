-- Los dos frenos del bot por lead.
--
-- Hasta ahora el unico era human_requested, que es definitivo: el lead pidio
-- una persona o se lo derivo, y no vuelve hasta que alguien lo suelte desde el
-- CRM. Faltaban los dos que ya tenia el bot de la bloquera:
--
--   bot_enabled       el interruptor del panel, lo mueve una persona
--   bot_pausado_hasta la pausa que se pone sola cuando entras vos al chat
--
-- La segunda vence sola a proposito. Un interruptor a secas obliga a acordarse
-- de prenderlo de nuevo, y cuando uno se olvida ese lead se queda sin bot sin
-- que nadie lo note. Ver src/funnel/pausa.js.
--
-- bot_enabled arranca en 1: apagado por defecto dejaria mudo a todo el mundo.
ALTER TABLE leads ADD COLUMN bot_enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE leads ADD COLUMN bot_pausado_hasta TEXT;
