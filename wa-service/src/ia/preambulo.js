'use strict';

/**
 * Saca la primera linea "meta" que el modelo a veces escribe antes del mensaje.
 *
 * Caso real, lead 18 (30-9, 03:02): el bot mando por WhatsApp, tal cual,
 *
 *   "Entendido. El mensaje que le mandás a Silvana es:
 *
 *    Para optimizar esas ropas y accesorios en línea, ..."
 *
 * El modelo contesto como si le hablara a un operador que le pide un texto, y
 * esa presentacion salio al lead. Es el unico caso de toda la base, pero un
 * prompt no alcanza para garantizar que no vuelva: se verifica la salida.
 *
 * Regla conservadora a proposito: solo se saca la PRIMERA linea, y solo si es
 * corta, termina en ":" y habla del mensaje/texto/respuesta. "Horarios del
 * jueves:" o "Te cuento algo:" son mensajes normales y no se tocan.
 */

const MAX_LARGO_PREAMBULO = 120;

const APERTURA = '(?:(?:entendido|perfecto|listo|claro|dale|ok|okey|genial|bien)[\\s,.!¡:;-]+)?';
const RE_LINEA_META = new RegExp(
  `^\\s*${APERTURA}.*\\b(?:mensaje|texto|respuesta)\\b.*:\\s*$`,
  'iu',
);

/**
 * @param {string} texto
 * @returns {{texto: string, sacado: string|null}} `sacado` es la linea que se
 *   quito, para loguearla; null si no se toco nada.
 */
function quitarPreambulo(texto) {
  const original = String(texto || '');
  const lineas = original.split('\n');
  const primera = lineas[0].trim();

  if (!primera || primera.length > MAX_LARGO_PREAMBULO) return { texto: original, sacado: null };
  if (!RE_LINEA_META.test(primera)) return { texto: original, sacado: null };

  return { texto: lineas.slice(1).join('\n').trim(), sacado: primera };
}

module.exports = { quitarPreambulo };
