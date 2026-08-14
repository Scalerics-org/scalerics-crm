'use strict';

const { S } = require('./states');

/**
 * Tabla de transiciones: { desde: { entrada: hacia } }. '*' es el comodin.
 * Portada de bot/src/fsm/transitions.js.
 */
const TRANSICIONES = {
  [S.NEW]:          { '*': S.MENU },
  [S.MENU]:         { 1: S.QUAL_0, 2: S.HUMAN_QUEUED, '*': S.MENU },
  [S.MENU_INFO]:    { 1: S.QUAL_1, 2: S.MENU, '*': S.MENU_INFO },
  [S.QUAL_0]:       { '*': S.QUAL_1 },
  [S.QUAL_1]:       { 1: S.QUAL_2, 2: S.QUAL_2, 3: S.QUAL_2, 4: S.QUAL_2, '*': S.QUAL_1 },
  [S.QUAL_2]:       { 1: S.QUAL_3, 2: S.QUAL_3, 3: S.QUAL_3, 4: S.QUAL_3, '*': S.QUAL_2 },
  [S.QUAL_3]:       { 1: S.QUAL_4, 2: S.QUAL_4, 3: S.QUAL_4, 4: S.QUAL_4, '*': S.QUAL_3 },
  [S.QUAL_4]:       { '*': S.QUAL_5 },
  [S.QUAL_5]:       { '*': S.QUAL_6 },
  [S.QUAL_6]:       { '*': S.SCORED },
  // El "2" (quiero saber mas) lleva a MEETING_INFO, y desde ahi otro "2"
  // (todavia no) cierra la insistencia. Cuando los dos estados eran uno solo,
  // cada "2" volvia a caer en el mismo lugar y repetia el mismo mensaje.
  [S.MEETING_SENT]: { 2: S.MEETING_INFO, '*': S.MEETING_SENT },
  [S.MEETING_INFO]: { 2: S.NURTURE, '*': S.MEETING_SENT },
  [S.SCHEDULED]:    { '*': S.SCHEDULED },
  [S.NURTURE]:      { '*': S.MENU },
  [S.DISQUALIFIED]: { '*': S.MENU },
  [S.HUMAN_QUEUED]: { '*': S.HUMAN_QUEUED },
  [S.OPT_OUT]:      { '*': S.OPT_OUT },
};

/** Opciones validas por estado, para el mensaje de "no entendi". */
const OPCIONES = {
  [S.QUAL_1]: '*1* Página web\n*2* E-commerce / tienda online\n*3* Automatización\n*4* App a medida',
  [S.QUAL_2]: '*1* Menos de $500 USD\n*2* $500 a $3.000 USD\n*3* Más de $3.000 USD\n*4* Todavía no lo sé',
  [S.QUAL_3]: '*1* Solo yo\n*2* 2-5 personas\n*3* 6-20 personas\n*4* Más de 20',
};

/**
 * Que campo del lead guarda la respuesta que llega ESTANDO en cada estado.
 *
 * Ojo con el desfasaje: la respuesta a la pregunta de QUAL_1 se recibe cuando el
 * lead todavia esta en QUAL_1 y lo lleva a QUAL_2. En el bot original esto vivia
 * disperso en los handlers (handleQualN guardaba la respuesta de QUAL_(N-1)) y
 * habia ademas un mapa QUAL_FIELD exportado que decia otra cosa y no lo usaba
 * nadie. Aca queda en un solo lugar y coincide con lo que realmente pasa.
 */
const CAMPO_RESPUESTA = {
  [S.QUAL_0]: { campo: 'business_name', numerico: false },
  [S.QUAL_1]: { campo: 'business_type', numerico: true },
  [S.QUAL_2]: { campo: 'budget', numerico: true },
  [S.QUAL_3]: { campo: 'team_size', numerico: true },
  [S.QUAL_4]: { campo: 'colors', numerico: false },
  [S.QUAL_5]: { campo: 'instagram_web', numerico: false },
  [S.QUAL_6]: { campo: 'needs', numerico: false },
};

module.exports = { TRANSICIONES, OPCIONES, CAMPO_RESPUESTA };
