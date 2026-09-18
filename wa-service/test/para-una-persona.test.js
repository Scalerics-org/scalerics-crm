'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { paraUnaPersona } = require('../src/funnel/derivacion');
const { crearTextos } = require('../src/templates/funnel');
const { montar, stubModelo } = require('./helpers');
const { S } = require('../src/funnel/states');

const NOMBRES = { nombres: ['Juan'] };

// ── la deteccion ─────────────────────────────────────────────────────────────

test('lo llama por el nombre al principio', () => {
  for (const texto of [
    'Hola Juan podemos mover nuestra reunión para el Lunes?',
    'Juan, te paso el contrato firmado',
    'buenas juan! cómo va',
    'Buen día Juan',
    'hola, juan',
  ]) {
    assert.deepEqual(paraUnaPersona(texto, NOMBRES), { motivo: 'para_una_persona', nombre: 'Juan' }, texto);
  }
});

test('un nombre parecido o en el medio de la frase no cuenta', () => {
  for (const texto of [
    'Hola Juana, quiero una web',
    'Hola, quiero una web para mi tienda',
    'me recomendó Juan que les escriba', // no le habla a Juan: lo nombra
    'Juancito me pasó el número',
  ]) {
    assert.equal(paraUnaPersona(texto, NOMBRES), null, texto);
  }
});

test('habla de mover una reunion que el bot no tiene', () => {
  for (const texto of [
    'podemos mover la reunión al lunes?',
    '¿Se puede pasar la llamada para el jueves?',
    'Tenemos que reprogramar la videollamada',
    'Agendaste con nosotros de hecho',
  ]) {
    assert.deepEqual(paraUnaPersona(texto, NOMBRES), { motivo: 'reunion_ajena', nombre: null }, texto);
  }
});

/**
 * Al que esta eligiendo horario, "¿podemos pasar la llamada al martes?" le
 * contesta el embudo: es quien tiene los horarios.
 */
test('si el bot ya le ofrecio o le agendo una reunion, moverla es cosa del embudo', () => {
  assert.equal(paraUnaPersona('¿podemos pasar la llamada al martes?', { ...NOMBRES, tieneReunion: true }), null);
});

test('hablar de reuniones o de cambios, por separado, no alcanza', () => {
  for (const texto of [
    'quiero cambiar mi página web',
    'necesito una llamada para ver precios',
    'me gustaría agendar una reunión',
  ]) {
    assert.equal(paraUnaPersona(texto, NOMBRES), null, texto);
  }
});

// ── de punta a punta: el caso del 18-9 ───────────────────────────────────────

const DAVID = '31687379000';

async function escribe(s, texto) {
  await s.servicioLeads.registrarRespuesta(DAVID, texto, 'David');
  await s.cola.vacia();
}

test('"Hola Juan…" no entra al embudo: una linea y el chat pasa a Juan', async () => {
  const modelo = stubModelo();
  const s = await montar({ modelo });

  await escribe(s, 'Hola Juan podemos mover nuestra reunión para el Lunes?');

  const alContacto = s.proveedor.getEnviados().filter((e) => e.to === DAVID).map((e) => e.texto);
  assert.deepEqual(alContacto, [crearTextos().paraUnaPersona('Juan')],
    'ni la presentacion de agente comercial, ni preguntas, ni "claro, sin problema"');

  const l = s.repo.leadPorTelefono(DAVID);
  assert.equal(l.fsm_state, S.HUMAN_QUEUED);
  assert.equal(l.human_requested, 1);
  assert.equal(l.motivo_derivacion, 'para_una_persona');

  const alEquipo = s.proveedor.getEnviados().filter((e) => e.to === '59899000111').map((e) => e.texto).join('\n');
  assert.match(alEquipo, /le escribio a una persona del equipo/);
  assert.match(alEquipo, /mover nuestra reunión/, 'con lo que dijo, para que Juan sepa de que se trata');

  assert.equal(modelo.llamadas.length, 0, 'el modelo ni se entera: no hay nada que conversar');
});

test('lo que manda despues le llega a Juan y el bot no le contesta mas', async () => {
  const s = await montar({ modelo: stubModelo() });

  await escribe(s, 'Hola Juan podemos mover nuestra reunión para el Lunes?');
  s.proveedor.limpiar();
  await escribe(s, 'Agendaste con nosotros de hecho. Soy David, la mano derecha de Alexis');

  assert.equal(s.proveedor.getEnviados().filter((e) => e.to === DAVID).length, 0, 'el bot se calla');
  const alEquipo = s.proveedor.getEnviados().filter((e) => e.to === '59899000111').map((e) => e.texto).join('\n');
  assert.match(alEquipo, /mano derecha de Alexis/, 'y Juan lo ve');
});

test('sin el nombre, una reunion que no conocemos tambien va a una persona', async () => {
  const s = await montar({ modelo: stubModelo() });

  await escribe(s, 'Buenas, ¿podemos pasar la llamada del viernes al lunes?');

  const alContacto = s.proveedor.getEnviados().filter((e) => e.to === DAVID).map((e) => e.texto);
  assert.deepEqual(alContacto, [crearTextos().paraUnaPersona(null)]);
  assert.equal(s.repo.leadPorTelefono(DAVID).motivo_derivacion, 'reunion_ajena');
});

test('un lead comun sigue recibiendo la presentacion y el embudo', async () => {
  const s = await montar({ modelo: stubModelo() });

  await escribe(s, 'Hola, quiero una web para mi barbería');

  const alContacto = s.proveedor.getEnviados().filter((e) => e.to === DAVID).map((e) => e.texto);
  assert.equal(alContacto[0], crearTextos().BIENVENIDA);
  assert.equal(s.repo.leadPorTelefono(DAVID).human_requested, 0);
});

test('los nombres salen de la config', async () => {
  const s = await montar({ modelo: stubModelo(), EQUIPO_NOMBRES: 'Juan, Alexis' });

  await escribe(s, 'Alexis, te mando lo que hablamos');

  assert.deepEqual(
    s.proveedor.getEnviados().filter((e) => e.to === DAVID).map((e) => e.texto),
    [crearTextos().paraUnaPersona('Alexis')]
  );
});
