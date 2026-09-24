'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { quitar } = require('../src/ia/jerga');

/**
 * "¿Qué onda? ¿En qué te podemos ayudar?" salio en produccion el 3-9, con la
 * regla de jerga ya escrita en el prompt y medida en los evals.
 *
 * Medir no es frenar. Es la misma leccion que el tuteo y los precios: si una
 * regla importa, se verifica la salida en vez de pedirla por favor. Sacar un
 * arranque de jerga es una operacion mecanica —el mensaje sigue teniendo
 * sentido sin el— asi que la hace el codigo.
 */
test('saca el arranque de jerga y deja el resto', () => {
  assert.equal(quitar('¿Qué onda? ¿En qué te podemos ayudar?').texto,
    '¿En qué te podemos ayudar?');
  assert.equal(quitar('Dale loco, contame de tu negocio.').texto,
    'Contame de tu negocio.');
  assert.equal(quitar('Todo bien capo. ¿Cómo se llama?').texto, '¿Cómo se llama?');
});

test('dice qué sacó, para poder verlo en el log', () => {
  const r = quitar('¿Qué onda? ¿En qué te ayudo?');
  assert.deepEqual(r.sacados, ['¿Qué onda?']);
});

/**
 * Lo cordial se queda. "Dale" y "buenísimo" son parte de como se habla en
 * Uruguay y el bot los tiene que poder usar: la regla es contra lo confianzudo,
 * no contra el registro entero.
 */
test('no toca lo que es cordial y no confianzudo', () => {
  for (const t of [
    'Dale, contame a qué se dedican.',
    'Buenísimo. ¿Qué necesitás?',
    '¡Buenas! ¿Cómo se llama tu negocio?',
  ]) {
    assert.equal(quitar(t).texto, t, t);
  }
});

/**
 * Si al sacarlo no queda nada, se deja como estaba: un mensaje vacio es peor
 * que uno mal escrito, y el lead se quedaria sin respuesta.
 */
test('si sacarlo dejaria el mensaje vacio, no se toca', () => {
  assert.equal(quitar('¿Qué onda?').texto, '¿Qué onda?');
  assert.equal(quitar('Dale loco').texto, 'Dale loco');
});

test('la jerga en el medio de una frase no se toca', () => {
  // Sacarla ahi partiria la oracion. Solo se limpia el arranque, que es donde
  // aparece y donde se puede sacar sin romper nada.
  const t = 'Contame qué onda con el sistema que tenés ahora.';
  assert.equal(quitar(t).texto, t);
});
