'use strict';

const { S } = require('./states');

/**
 * Solo quedan las transiciones de la fase de cierre, y solo se usan cuando la
 * IA no esta disponible. Mientras la IA responde, el estado lo maneja el motor
 * directamente: no hay una tabla que diga "de la pregunta 3 se pasa a la 4"
 * porque ya no hay preguntas numeradas.
 */
const TRANSICIONES = {
  [S.NEW]:               { '*': S.CONVERSANDO },
  [S.CONVERSANDO]:       { '*': S.CONVERSANDO },
  [S.MEETING_SENT]:      { '*': S.HORARIOS_OFRECIDOS },
  [S.HORARIOS_OFRECIDOS]: { '*': S.HORARIOS_OFRECIDOS },
  [S.MEETING_INFO]:      { '*': S.MEETING_LINK_SENT },
  [S.MEETING_LINK_SENT]: { '*': S.MEETING_LINK_SENT },
  [S.SCHEDULED]:         { '*': S.SCHEDULED },
  [S.NURTURE]:           { '*': S.CONVERSANDO },
  [S.DISQUALIFIED]:      { '*': S.CONVERSANDO },
  [S.HUMAN_QUEUED]:      { '*': S.HUMAN_QUEUED },
  [S.OPT_OUT]:           { '*': S.OPT_OUT },
};

/** Que campo del lead guarda cada respuesta. Lo llena la IA por tool call. */
const CAMPO_RESPUESTA = {};

module.exports = { TRANSICIONES, CAMPO_RESPUESTA };
