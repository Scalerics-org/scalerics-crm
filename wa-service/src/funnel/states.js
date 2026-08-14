'use strict';

/**
 * Estados del embudo. Ya no marcan que pregunta toca —eso lo decide la IA
 * mirando que datos faltan— sino en que momento de la relacion esta el lead.
 *
 * Los QUAL_0..QUAL_6 y el MENU numerado se fueron con el embudo de preguntas
 * fijas: cada uno existia para saber que texto mandar, y ya no hay textos.
 */
const S = {
  NEW: 'NEW',
  // La IA esta averiguando quien es y que necesita.
  CONVERSANDO: 'CONVERSANDO',
  // Momento de calificar. Es de paso: nunca queda guardado.
  SCORED: 'SCORED',
  MEETING_SENT: 'MEETING_SENT',
  MEETING_INFO: 'MEETING_INFO',
  // El link de Calendly ya salio. Existe para no volver a mandarlo.
  MEETING_LINK_SENT: 'MEETING_LINK_SENT',
  SCHEDULED: 'SCHEDULED',
  NURTURE: 'NURTURE',
  DISQUALIFIED: 'DISQUALIFIED',
  HUMAN_QUEUED: 'HUMAN_QUEUED',
  OPT_OUT: 'OPT_OUT',
};

/**
 * Lo que gana sobre cualquier otra cosa, en cualquier estado: irse y pedir una
 * persona. No se le delega al modelo, y no alcanza con una palabra suelta.
 *
 * Antes era un mapa de substrings y solo entendia "baja" literal: "sacame de la
 * lista" y "no me escribas mas" —que es como lo dice la gente de verdad—
 * seguian de largo hacia el bot.
 *
 * "cancelar" salio de la lista: el que escribe "quiero cancelar la reunion" no
 * se esta dando de baja, y darlo de baja era perderlo entero.
 * "ayuda" nunca estuvo: la gente la usa para describir su problema.
 */
const BAJA = [
  /\bbaja\b/,
  /\bstop\b/,
  /\bunsubscribe\b/,
  /no me escrib/,
  /dej[aá] de escribirme/,
  /dejen de escribir/,
  /sacame de la lista/,
  /borrame/,
  /no quiero recibir/,
  /no me mand[eé]s? m[aá]s/,
  /no me contacten/,
];

const HUMANO = [
  /\bhumano\b/,
  /\basesor\b/,
  /\bpersona\b/,
  /hablar con alguien/,
  /alguien del equipo/,
  /un encargado/,
];

/**
 * La entrada llega normalizada: minusculas y sin acentos.
 * @returns {string|null} el estado al que hay que ir, o null.
 */
function palabraGlobal(entrada) {
  if (BAJA.some((re) => re.test(entrada))) return S.OPT_OUT;
  if (HUMANO.some((re) => re.test(entrada))) return S.HUMAN_QUEUED;
  return null;
}

module.exports = { S, palabraGlobal, BAJA, HUMANO };
