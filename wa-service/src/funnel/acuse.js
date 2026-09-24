'use strict';

/**
 * Si el mensaje es solo un acuse ("Ok", "Dale, nos vemos", "Perfecto, gracias")
 * y no dice nada que el embudo tenga que atender.
 *
 * Despues de confirmar la reunion, Patricia mando "Ok perfecto", "Ok", y al
 * recordatorio "Ok bien", "Perfecto si" — y el bot le contesto cada uno,
 * repitiendo la fecha de la reunion cuatro veces seguidas. Se detecta en
 * codigo, sin IA: un acuse no tiene ambigüedad que justifique una llamada al
 * modelo, y es justo el tipo de caso donde el modelo puede "conversar" de mas.
 */

/** minusculas, sin acentos. Mismo criterio que derivacion.js. */
function normalizar(texto) {
  return String(texto || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '');
}

const PALABRAS_DE_ACUSE = new Set([
  'ok', 'okk', 'oka', 'okey', 'okay', 'oki', 'dale', 'listo', 'perfecto',
  'genial', 'buenisimo', 'barbaro', 'joya', 'excelente', 'gracias', 'muchas',
  'mil', 'bien', 'si', 'sii', 'bueno', 'de', 'una', 'nos', 'vemos', 'entonces',
  'igualmente', 'super',
]);

const MAX_PALABRAS = 5;

/** Emojis: presentacion emoji (👍) y pictograficos extendidos (🙏, 👌, etc). */
const HAY_EMOJI = /\p{Emoji_Presentation}|\p{Extended_Pictographic}/u;

function esSoloEmojis(texto) {
  const sinEspacios = texto.replace(/\s+/g, '');
  if (!sinEspacios) return false;
  // Si no queda ninguna letra ni numero, lo unico que puede haber matcheado
  // el emoji es eso: un emoji (o variantes/modificadores de tono, que no son
  // letras ni numeros y no le cambian el sentido).
  return HAY_EMOJI.test(sinEspacios) && !/[\p{L}\p{N}]/u.test(sinEspacios);
}

/**
 * @param {unknown} texto
 * @returns {boolean}
 */
function esAcuse(texto) {
  if (typeof texto !== 'string') return false;
  const t = texto.trim();
  if (!t) return false;
  // Una pregunta nunca es un acuse solo: "ok, pero ¿puedo cambiar el horario?"
  // tiene que llegar al embudo.
  if (t.includes('?') || t.includes('¿')) return false;

  if (esSoloEmojis(t)) return true;

  const limpio = normalizar(t).replace(/[^\p{L}\p{N}\s]/gu, ' ').trim();
  if (!limpio) return false;

  const palabras = limpio.split(/\s+/).filter(Boolean);
  if (palabras.length > MAX_PALABRAS) return false;

  return palabras.every((p) => PALABRAS_DE_ACUSE.has(p));
}

module.exports = { esAcuse };
