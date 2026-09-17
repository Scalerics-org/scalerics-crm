'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { cambiaLaNecesidad } = require('../src/ia/necesidad');

/**
 * El 3-9, dos veces en la misma tarde:
 *
 *   → ¿Qué necesitás? ¿Una página web, una tienda online, ...?
 *   ← Una página
 *   → Perfecto, entonces necesitás un e-commerce para que Popou venda
 *     empanadas online sin depender de mostrador.
 *
 * Y antes, con otro lead que tambien habia pedido pagina web:
 *
 *   → Perfecto, para tu tienda online necesitás que los clientes encuentren
 *     los productos fácil y que todo el proceso de compra sea seguro.
 *   ← Pero quiero página web no ecommerce
 *
 * El dato quedo bien guardado —needs: "Página web"— y el mensaje igual le
 * vendio otra cosa. Es de las peores formas de perder a alguien: contesto lo
 * que le preguntaron y el bot le mostro que no lo escucho.
 */
test('vender un e-commerce a quien pidió página web es cambiarle lo que pidió', () => {
  assert.ok(cambiaLaNecesidad('Página web', 'Perfecto, entonces necesitás un e-commerce para vender online.'));
  assert.ok(cambiaLaNecesidad('Página web', 'Para tu tienda online necesitás que los clientes encuentren los productos fácil.'));
});

test('hablar de lo que sí pidió está bien', () => {
  assert.equal(cambiaLaNecesidad('Página web', 'Para tu página web vemos cómo mostrar lo que vendés.'), false);
  assert.equal(cambiaLaNecesidad('Tienda online', 'Armamos el e-commerce con tu catálogo y el flujo de pago.'), false);
  assert.equal(cambiaLaNecesidad('', 'Vemos qué te sirve más, una página o una tienda online.'), false);
  assert.equal(cambiaLaNecesidad(null, 'Cualquier cosa'), false);
});

/**
 * Nombrar las dos como opciones no es cambiarle nada: es la pregunta que el
 * bot hace al principio, antes de saber qué quiere.
 */
test('ofrecer las opciones no cuenta', () => {
  assert.equal(
    cambiaLaNecesidad('Página web', '¿Qué necesitás: una página web, una tienda online, o un sistema a medida?'),
    false,
  );
});
