'use strict';

/**
 * Estados del embudo de calificacion. Portado de bot/src/fsm/states.js.
 *
 * Cambios respecto del original: se saca QUAL_7, que estaba declarado y no lo
 * referenciaba nadie.
 */
const S = {
  NEW: 'NEW',
  MENU: 'MENU',
  MENU_INFO: 'MENU_INFO',
  QUAL_0: 'QUAL_0',
  QUAL_1: 'QUAL_1',
  QUAL_2: 'QUAL_2',
  QUAL_3: 'QUAL_3',
  QUAL_4: 'QUAL_4',
  QUAL_5: 'QUAL_5',
  QUAL_6: 'QUAL_6',
  // La IA esta conduciendo la conversacion. Si se apaga (sin clave, API caida),
  // el lead vuelve al menu del embudo fijo: se le repiten preguntas, pero sigue
  // atendido, que es lo que importa.
  CONVERSANDO: 'CONVERSANDO',
  SCORED: 'SCORED',
  MEETING_SENT: 'MEETING_SENT',
  // Se le contesto el "quiero saber mas". Existe para no volver a mandarle lo
  // mismo si insiste con esa opcion: sin este estado, el "2" se atendia siempre
  // igual y la conversacion quedaba en loop.
  MEETING_INFO: 'MEETING_INFO',
  SCHEDULED: 'SCHEDULED',
  NURTURE: 'NURTURE',
  DISQUALIFIED: 'DISQUALIFIED',
  HUMAN_QUEUED: 'HUMAN_QUEUED',
  OPT_OUT: 'OPT_OUT',
};

// Estados donde se espera un numero: si viene otra cosa se reintenta.
// QUAL_4, QUAL_5 y QUAL_6 son texto libre, cualquier cosa avanza.
const ESTADOS_CON_OPCIONES = new Set([S.QUAL_1, S.QUAL_2, S.QUAL_3]);

/**
 * Palabras que ganan sobre la tabla de transiciones, en cualquier estado.
 * "ayuda" quedo afuera a proposito: la gente la usa para describir su problema,
 * no para pedir un humano.
 */
const PALABRAS_GLOBALES = {
  humano: S.HUMAN_QUEUED,
  asesor: S.HUMAN_QUEUED,
  persona: S.HUMAN_QUEUED,
  stop: S.OPT_OUT,
  baja: S.OPT_OUT,
  cancelar: S.OPT_OUT,
  listo: S.MENU,
  menu: S.MENU,
};

module.exports = { S, ESTADOS_CON_OPCIONES, PALABRAS_GLOBALES };
