'use strict';

/**
 * Que el mensaje no le cambie al lead lo que pidio.
 *
 * El 3-9 pidio "una pagina" y el bot le contesto "entonces necesitas un
 * e-commerce". Un rato antes, con otro lead, lo mismo al reves de la
 * conversacion: "para tu tienda online necesitas..." cuando habia pedido
 * pagina web, y el lead lo corrigio —"pero quiero pagina web no ecommerce"— y
 * el bot le contesto con los horarios.
 *
 * El dato estaba bien guardado las dos veces. Lo que fallo es el mensaje, que
 * es donde el modelo mejora la idea del lead por su cuenta. Es de las peores
 * formas de perder a alguien: contesto lo que le preguntaron y le mostraron
 * que no lo escucharon.
 *
 * Misma leccion que el tuteo, los precios y la jerga: si una regla importa, se
 * verifica la salida. Esta no se puede arreglar tachando una palabra —hay que
 * escribir el mensaje de nuevo— asi que devuelve si hay que reescribirlo.
 */

const PAGINA = 'p[aá]gina web|sitio web|landing';
const TIENDA = 'e-?commerce|tienda online|tienda virtual|tienda web';

const RE_PAGINA = new RegExp(PAGINA, 'i');
const RE_TIENDA = new RegExp(TIENDA, 'i');

/**
 * @param {string} needs lo que el lead dijo que necesitaba, como se guardo
 * @param {string} texto el mensaje que el modelo escribio
 * @returns {boolean} true si le cambia lo que pidio por otra cosa
 */
function cambiaLaNecesidad(needs, texto) {
  const n = String(needs || '');
  const t = String(texto || '');
  if (!n || !t) return false;

  // Pidio pagina y le hablan de tienda. Al reves no cuenta: una tienda online
  // ES una pagina web, asi que nombrarla no le cambia nada.
  const pidioPagina = RE_PAGINA.test(n) && !RE_TIENDA.test(n);
  if (!pidioPagina) return false;
  if (!RE_TIENDA.test(t)) return false;

  // Nombrar las dos como opciones no es cambiarle nada: es la pregunta que el
  // bot hace al principio, antes de saber que quiere.
  if (RE_PAGINA.test(t)) return false;

  return true;
}

module.exports = { cambiaLaNecesidad };
