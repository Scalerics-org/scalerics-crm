'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { revisarMensaje } = require('../evals/reglas');

const fallas = (texto, ctx = {}) => revisarMensaje(texto, { etapa: 'descubrimiento', ...ctx })
  .map((f) => f.id);

/**
 * El 2-9, primer mensaje de una conversacion ya empezada:
 *
 *   ← Ola
 *   → ¿Qué onda?
 *
 * Es como escribe un amigo, no una agencia. El estilo pide "calido y directo" y
 * "como un uruguayo, no como un bot", y el modelo lo leyo como permiso para la
 * jerga. Escribe en nombre de una empresa a alguien que no lo conoce.
 */
test('marca la jerga', () => {
  assert.ok(fallas('¿Qué onda?').includes('jerga'));
  assert.ok(fallas('Dale loco, contame').includes('jerga'));
  assert.ok(fallas('Buenísimo bo, ¿cómo se llama?').includes('jerga'));
});

test('lo cordial no es jerga', () => {
  for (const t of [
    '¡Buenas! ¿Cómo se llama tu negocio?',
    'Dale, contame a qué se dedican.',
    'Buenísimo. ¿Qué necesitás?',
  ]) {
    assert.ok(!fallas(t).includes('jerga'), t);
  }
});

/**
 * El otro problema del mismo mensaje: contesto y no pregunto nada. En
 * descubrimiento cada mensaje tiene que traer la pregunta de lo que falta, o el
 * turno se gasta en un saludo y la conversacion no avanza.
 */
test('en descubrimiento, un mensaje sin pregunta es un turno perdido', () => {
  assert.ok(fallas('¿Qué onda?').includes('turno_perdido') === false,
    'una pregunta, aunque sea mala, no es turno perdido');
  assert.ok(fallas('Buenísimo, gracias.').includes('turno_perdido'));
  assert.ok(fallas('Perfecto, anotado.').includes('turno_perdido'));
});

test('despues de la oferta no hace falta preguntar en cada mensaje', () => {
  // Ahi el objetivo ya no es averiguar: puede confirmar, despedirse o dejarlo
  // tranquilo sin que eso sea una falla.
  assert.ok(!fallas('Listo, quedó agendado. Nos vemos.', { etapa: 'post_link' })
    .includes('turno_perdido'));
});
