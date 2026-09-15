-- El dia del que se viene hablando cuando se eligen horarios.
--
-- Sin esto, una hora suelta ("a las 10:23") se resolvia contra hoy, porque hoy
-- es el unico dia de referencia que recibe el modelo. El 3-9 el lead venia
-- preguntando por el viernes 11, escribio esa hora, y el bot la ubico en el
-- jueves que ya estaba empezado: la rechazo con un motivo falso y volvio a
-- ofrecer los dias de la lista.
ALTER TABLE leads ADD COLUMN dia_en_foco TEXT;
