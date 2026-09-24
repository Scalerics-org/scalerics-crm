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
/**
 * Lo que un local de comidas escucha como "gancho": el 22-9 a Los Sopranos, que
 * habia pedido una pagina web, el pitch le hablo de "tu sistema de pedidos
 * online". Es otro producto, igual que la tienda.
 */
const PEDIDOS = 'sistema de pedidos|pedidos online|pedidos en l[ií]nea|pedidos por internet';

const RE_PAGINA = new RegExp(PAGINA, 'i');
const RE_TIENDA = new RegExp(TIENDA, 'i');
const RE_OTRO_PRODUCTO = new RegExp(`${TIENDA}|${PEDIDOS}`, 'i');

/** business_type 1: el "1" que guarda agente.js cuando el lead pidio una pagina web. */
const TIPO_PAGINA_WEB = 1;

/**
 * @param {string} needs lo que el lead dijo que necesitaba, como se guardo
 * @param {string} texto el mensaje que el modelo escribio
 * @param {number|null} [tipoProyecto] business_type guardado. Sirve cuando no hay
 *   texto libre: el lead que contesto "pagina web" queda con business_type = 1
 *   y needs vacio, y sin mirarlo no habia contra que comparar.
 * @returns {boolean} true si le cambia lo que pidio por otra cosa
 */
function cambiaLaNecesidad(needs, texto, tipoProyecto = null) {
  const n = String(needs || '');
  const t = String(texto || '');
  if (!t) return false;

  // Pidio pagina y le hablan de tienda o de pedidos. Al reves no cuenta: una
  // tienda online ES una pagina web, asi que nombrarla no le cambia nada.
  const pidioPagina = (RE_PAGINA.test(n) || (!n && Number(tipoProyecto) === TIPO_PAGINA_WEB))
    && !RE_TIENDA.test(n);
  if (!pidioPagina) return false;
  if (!RE_OTRO_PRODUCTO.test(t)) return false;

  // Nombrar las dos como opciones no es cambiarle nada: es la pregunta que el
  // bot hace al principio, antes de saber que quiere.
  if (RE_PAGINA.test(t)) return false;

  return true;
}

module.exports = { cambiaLaNecesidad };
