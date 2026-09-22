-- Por que se programo un job, cuando el tipo no alcanza para saberlo.
--
-- Un `followup` era siempre "el lead del formulario no contesto la bienvenida",
-- y por eso se cancelaba en cuanto el lead respondia. Pero el abandono de
-- madrugada tambien programa un `followup`, para retomar a la apertura al que
-- se durmio en medio de la charla — y ese lead ya habia respondido: es lo que
-- lo hizo lead. Se cancelaba siempre, apenas vencia. Le paso a Susana el 13-9 y
-- a Sebastian el 22-9: los dos quedaron colgados sin que nadie los retomara.
--
-- 'retomar' marca esos. NULL es el follow-up de siempre.
ALTER TABLE jobs ADD COLUMN motivo TEXT;

-- Cuando se le mando el ultimo retomar a un lead. Hace falta una señal propia
-- porque followup_sent_at tambien lo pone el follow-up clasico de 72 horas: un
-- lead del formulario que no contesto, recibio ESE follow-up, y meses despues
-- se durmio en medio de una charla real, no se retomaba nunca -followup_sent_at
-- ya estaba puesto de antes- y se derivaba a una persona en la apertura sin
-- haberlo intentado.
ALTER TABLE leads ADD COLUMN retomado_at TEXT;
