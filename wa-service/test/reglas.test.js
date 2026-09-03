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

/**
 * Otro mensaje del 2-9, ya en una conversacion con siete mensajes arriba:
 *
 *   ← ola
 *   → ¿Todo bien? Tenemos la reunión agendada para el jueves 3...
 *
 * "¿Todo bien?" no es jerga, pero es un segundo saludo. El estilo lo prohibe
 * con todas las letras —"saludás una sola vez por conversación"— porque dos
 * saludos seguidos son la forma mas rapida de que se note que del otro lado hay
 * una maquina.
 */
test('marca el saludo cuando la conversacion ya venia', () => {
  const ctx = { etapa: 'cierre', primerTurno: false };
  for (const t of ['¿Todo bien? Ya está agendada.', '¡Hola! Ya está agendada.', '¿Cómo andás? Ya está agendada.']) {
    assert.ok(revisarMensaje(t, ctx).map((f) => f.id).includes('saludo_repetido'), t);
  }
});

test('en el primer turno saludar esta bien', () => {
  const ctx = { etapa: 'descubrimiento', primerTurno: true };
  assert.ok(!revisarMensaje('¡Buenas! ¿Cómo se llama tu negocio?', ctx)
    .map((f) => f.id).includes('saludo_repetido'));
});

test('un mensaje que no arranca saludando no se marca', () => {
  const ctx = { etapa: 'cierre', primerTurno: false };
  for (const t of ['Ya está agendada para el jueves.', 'Dale, te espero.']) {
    assert.ok(!revisarMensaje(t, ctx).map((f) => f.id).includes('saludo_repetido'), t);
  }
});

/**
 * El 3-9 el bot hablo de si mismo como una maquina, y encima rota:
 *
 *   → Ahora te muestra el sistema los horarios disponibles
 *   → El sistema debería haberte mandado los horarios. Revisá si llegó un
 *     mensaje de WhatsApp con el calendario
 *
 * Le prometio algo que no controla y lo mando a buscar un mensaje que nunca
 * existio. Del otro lado hay una persona hablando con lo que cree que es
 * alguien de la agencia: enterarse de que adentro hay "un sistema" que "debería
 * haber" hecho algo es peor que cualquier error.
 */
test('marca cuando el bot habla del sistema en tercera persona', () => {
  const ctx = { etapa: 'cierre', primerTurno: false };
  for (const t of [
    'Ahora te muestra el sistema los horarios disponibles.',
    'El sistema debería haberte mandado los horarios.',
    'Revisá si te llegó un mensaje con el calendario.',
    'Se te va a enviar automáticamente.',
  ]) {
    assert.ok(revisarMensaje(t, ctx).map((f) => f.id).includes('narra_el_sistema'), t);
  }
});

test('hablar del equipo o de la reunion no es narrar el sistema', () => {
  const ctx = { etapa: 'cierre', primerTurno: false };
  for (const t of [
    'Alguien del equipo te escribe en breve.',
    'Te paso el link de la videollamada.',
    'Quedó agendada para el viernes.',
  ]) {
    assert.ok(!revisarMensaje(t, ctx).map((f) => f.id).includes('narra_el_sistema'), t);
  }
});
