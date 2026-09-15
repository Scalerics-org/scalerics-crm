'use strict';

/**
 * Cuando el bot se calla en un chat, y cuando vuelve.
 *
 * Son dos frenos distintos y hacen falta los dos:
 *
 * 1. `bot_enabled` — el interruptor del panel. Lo prende y lo apaga una
 *    persona, y manda sobre todo lo demas.
 * 2. `bot_pausado_hasta` — la pausa que se pone sola cuando el dueño entra a
 *    la conversacion desde su telefono.
 *
 * Lo importante de la segunda es que VENCE. El pedido textual de Jose, que es
 * de donde sale este diseño: "que el sistema pare solo cuando yo entro en la
 * conversacion, y que si el cliente vuelve a escribir a los dias le conteste".
 * Un interruptor a secas obliga a acordarse de prenderlo de nuevo, y cuando
 * uno se olvida ese lead se queda sin bot sin que nadie lo note.
 *
 * Esto es distinto de `human_requested`, que ya existia: aquel es definitivo
 * —el lead pidio una persona, o se lo derivo— y solo se suelta desde el CRM.
 * Este es momentaneo y se suelta solo.
 */

/** Cuanto se calla el bot despues de que escribis vos. */
const HORAS_PAUSA = 12;

/**
 * La pregunta que se hace el bot antes de contestar.
 *
 * @param {object} lead
 * @param {Date} ahora
 */
function botActivo(lead, ahora = new Date()) {
  if (!lead) return false;
  // Sin la columna todavia migrada, o en un lead viejo, el bot contesta: es
  // como venia funcionando y apagarlo por defecto dejaria a todos mudos.
  if (lead.bot_enabled === 0) return false;
  if (!lead.bot_pausado_hasta) return true;
  return new Date(lead.bot_pausado_hasta) <= ahora;
}

/** Hasta cuando queda pausado si escribis ahora. */
function pausarHasta(ahora = new Date(), horas = HORAS_PAUSA) {
  return new Date(ahora.getTime() + horas * 3600_000).toISOString();
}

module.exports = { botActivo, pausarHasta, HORAS_PAUSA };
