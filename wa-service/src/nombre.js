'use strict';

const RE_EMOJI = /\p{Emoji_Presentation}|\p{Extended_Pictographic}|[\u200d\ufe0f\u20e3]/gu;

/**
 * El nombre de perfil de WhatsApp, sin emojis ni espacios de mas.
 *
 * Caso real, lead 18 (30-9): el perfil era "SILVANA RENEE DA COL 😍😍😍😍". Es
 * lo que el dueño del telefono eligio mostrar, no un nombre: los emojis no
 * pertenecen en un saludo ("Hola SILVANA RENEE DA COL 😍😍😍😍").
 */
function limpiarNombrePerfil(nombre) {
  return String(nombre || '').replace(RE_EMOJI, ' ').replace(/\s+/g, ' ').trim();
}

module.exports = { limpiarNombrePerfil };
