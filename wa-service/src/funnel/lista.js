'use strict';

/**
 * Pegar la lista numerada que arma el codigo abajo de lo que escribio el modelo,
 * sin que salga dos veces.
 *
 * Caso real, charla de Los Sopranos del 22-9 a las 21:40: al lead le mostraron
 * "¿Qué día te queda mejor? 1. Miércoles 23 2. Jueves 24", contestó "dije
 * pagina web" (corregia la necesidad, no elegia dia) y el modelo le contesto
 * bien, pero sin lista: el lead quedo sin opciones. La lista tiene que salir
 * igual, pegada por codigo, porque el modelo no sabe que dias hay libres.
 *
 * Si el modelo igual escribio una lista de dias u horas, se le saca: la suya la
 * invento, y la del codigo es la unica que coincide con lo que se puede elegir.
 * Solo se sacan las lineas que PARECEN esa lista (dia de la semana, hora, "la
 * semana que viene"): una lista numerada de otra cosa —los cinco servicios,
 * por ejemplo— es contenido del mensaje y se deja.
 */

const LINEA_DE_LISTA = /^\s*\d{1,2}\s*[.)]\s*(?:(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b|\d{1,2}:\d{2}\b|la\s+semana\s+que\s+viene\b)/i;
const ENCABEZADO_DE_DIAS = /^\s*¿?\s*qu[eé]\s+d[ií]a\s+te\s+queda\s+mejor\s*\??\s*$/i;

/**
 * @param {string} texto lo que escribio el modelo
 * @param {string} sufijo la lista del codigo (puede traer su propio encabezado)
 * @returns {string}
 */
function pegarLista(texto, sufijo) {
  if (!sufijo) return texto;

  const lineas = String(texto || '').split('\n');
  const conLista = lineas.filter((l) => LINEA_DE_LISTA.test(l)).length >= 2;
  const restantes = lineas.filter((l) => !(conLista && LINEA_DE_LISTA.test(l)));

  // El encabezado de la lista de dias tambien lo pone el codigo: si el modelo
  // ya escribio esa misma pregunta, con o sin su propia lista, sale dos veces.
  const traeEncabezado = ENCABEZADO_DE_DIAS.test(sufijo.split('\n')[0]);
  const sinEncabezado = traeEncabezado ? restantes.filter((l) => !ENCABEZADO_DE_DIAS.test(l)) : restantes;

  const limpio = sinEncabezado.join('\n').replace(/\n{3,}/g, '\n\n').trim();
  return limpio ? `${limpio}\n\n${sufijo}` : sufijo;
}

module.exports = { pegarLista };
