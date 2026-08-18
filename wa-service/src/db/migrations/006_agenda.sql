-- Los horarios que se le mostraron al lead, en JSON.
--
-- Hacen falta para validar lo que conteste: si dice "las 13" hay que poder
-- resolverlo contra lo que efectivamente se le ofrecio, y no contra lo que el
-- modelo crea recordar. Ademas dejan rastro de que se le propuso, que es lo
-- primero que se mira cuando alguien dice "yo elegi otra hora".
ALTER TABLE leads ADD COLUMN horarios_ofrecidos TEXT;
ALTER TABLE leads ADD COLUMN meeting_event_id TEXT;
