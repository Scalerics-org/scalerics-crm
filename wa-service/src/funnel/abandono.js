'use strict';

const { S } = require('./states');

/**
 * Cuando el silencio del lead significa que se fue de la conversacion.
 *
 * La regla vive en un solo lado a proposito: la usan los dos extremos —quien
 * arma el reloj al terminar cada turno y quien lo ejecuta una hora despues— y
 * si se separaran, el reloj se armaria para casos que despues se descartan, o
 * peor, no se armaria para los que si.
 */

/**
 * Estados donde irse no es irse.
 *
 * SCHEDULED es el mas importante: el que agendo y no volvio a escribir es
 * exactamente el caso de exito, y mandarselo al agente comercial como "se fue"
 * es ruido en el peor lugar — el que recibe avisos que no sirven deja de
 * mirarlos, y despues no ve los que si.
 *
 * NURTURE es el mismo error con otra cara: el que dijo "en enero te escribo"
 * tampoco se fue, aviso cuando volvia. Derivarlo al comercial una hora despues
 * es pasarle a alguien que ya dijo que no es el momento.
 *
 * OPT_OUT y DISQUALIFIED se explican solos, y el que ya esta en HUMAN_QUEUED ya
 * lo tiene una persona.
 */
const CERRADOS = new Set([
  S.SCHEDULED, S.NURTURE, S.OPT_OUT, S.DISQUALIFIED, S.HUMAN_QUEUED,
]);

/**
 * @param {object} lead fila de leads, fresca de la base
 * @returns {boolean} si corresponde derivarlo por haberse ido
 */
function correspondeDerivar(lead) {
  if (!lead) return false;
  if (lead.opt_out || lead.human_requested) return false;
  // Agendo: no se fue, hizo justo lo que se le pidio.
  if (lead.meeting_booked_at) return false;
  return !CERRADOS.has(lead.fsm_state || S.NEW);
}

module.exports = { correspondeDerivar, CERRADOS };
