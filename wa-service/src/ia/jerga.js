'use strict';

/**
 * Saca el arranque confianzudo que se le escapa al modelo.
 *
 * "¿Qué onda? ¿En qué te podemos ayudar?" salió en producción el 3-9, con la
 * regla ya escrita en el prompt —"cordial, no confianzudo", con estos mismos
 * ejemplos— y ya medida en los evals. Medir no es frenar.
 *
 * Es la misma lección que el tuteo y que los precios: si una regla importa de
 * verdad, se verifica la salida en vez de pedirla por favor. Sacar un arranque
 * de jerga es una operación mecánica, el mensaje sigue teniendo sentido sin él,
 * así que la hace el código.
 *
 * Solo el ARRANQUE. En el medio de una frase, sacarla partiría la oración: si
 * el bot escribe "contame qué onda con el sistema que tenés", eso no es una
 * muletilla, es parte de lo que está diciendo.
 *
 * La lista es corta a propósito, igual que la del tuteo: "dale" y "buenísimo"
 * se quedan. Son parte de cómo se habla en Uruguay y el bot los tiene que poder
 * usar — la regla es contra lo confianzudo, no contra el registro entero.
 */

const APERTURAS = [
  'qu[eé] onda',
  'dale loco',
  'todo bien capo',
  'todo bien loco',
  'qu[eé] hac[eé]s loco',
  'ep[aá] loco',
  'bo',
  'che bo',
];

/** Lo que puede venir pegado a la apertura antes del resto del mensaje. */
const CIERRE = '[?!.,\\u00a1\\u00bf\\u2026]*';
const ABRE = '[\\u00a1\\u00bf!]*';

const RE = new RegExp(
  `^${ABRE}(?:${APERTURAS.join('|')})${CIERRE}\\s*`,
  'i',
);

/**
 * @returns {{texto: string, sacados: string[]}}
 */
function quitar(texto) {
  const original = String(texto || '');
  const m = original.match(RE);
  if (!m) return { texto: original, sacados: [] };

  const resto = original.slice(m[0].length).trim();
  // Un mensaje vacío es peor que uno mal escrito: el lead se quedaría sin
  // respuesta. Si sacarlo no deja nada, se manda como estaba.
  if (!resto) return { texto: original, sacados: [] };

  // La frase que sigue arranca oración: si venía en minúscula, se levanta.
  const arreglado = resto[0].toUpperCase() + resto.slice(1);
  return { texto: arreglado, sacados: [m[0].trim()] };
}

module.exports = { quitar, APERTURAS };
