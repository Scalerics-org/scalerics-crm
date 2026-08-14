'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { crearAgrupador } = require('../src/inbound/agrupador');
const { montar } = require('./helpers');

const esperar = (ms) => new Promise((r) => setTimeout(r, ms));

test('tres fragmentos seguidos son un solo turno', async () => {
  // Lo que pasaba en produccion: "Necesito un" / "ecommerce" / "a medida" eran
  // tres mensajes, y el bot contestaba tres veces.
  const turnos = [];
  const a = crearAgrupador({ procesar: async (tel, texto) => turnos.push([tel, texto]), esperaMs: 30 });

  a.recibir({ from: '598991', texto: 'Necesito un' });
  a.recibir({ from: '598991', texto: 'ecommerce' });
  a.recibir({ from: '598991', texto: 'a medida' });
  await a.vaciar();

  assert.equal(turnos.length, 1, 'un turno, no tres');
  assert.equal(turnos[0][1], 'Necesito un\necommerce\na medida');
});

test('la espera se reinicia con cada mensaje nuevo', async () => {
  const turnos = [];
  const a = crearAgrupador({ procesar: async (tel, t) => turnos.push(t), esperaMs: 60 });

  a.recibir({ from: '598991', texto: 'uno' });
  await esperar(40);
  a.recibir({ from: '598991', texto: 'dos' });   // reinicia la cuenta
  await esperar(40);
  assert.equal(turnos.length, 0, 'todavia esta escribiendo');

  await esperar(40);
  assert.equal(turnos.length, 1);
  assert.equal(turnos[0], 'uno\ndos');
});

test('dos personas distintas no se mezclan', async () => {
  const turnos = [];
  const a = crearAgrupador({ procesar: async (tel, t) => turnos.push([tel, t]), esperaMs: 20 });

  a.recibir({ from: '598991', texto: 'hola' });
  a.recibir({ from: '598992', texto: 'buenas' });
  await a.vaciar();

  assert.equal(turnos.length, 2);
  assert.deepEqual(turnos.map((t) => t[0]).sort(), ['598991', '598992']);
});

test('el segundo turno espera a que termine el primero', async () => {
  // Sin la fila, dos tandas contestadas en paralelo devuelven las respuestas
  // desordenadas: el modelo tarda distinto cada vez.
  const orden = [];
  let primera = true;
  const a = crearAgrupador({
    esperaMs: 5,
    procesar: async (tel, texto) => {
      orden.push(`empieza ${texto}`);
      // La primera tarda mas: sin fila, la segunda terminaria antes.
      await esperar(primera ? 40 : 1);
      primera = false;
      orden.push(`termina ${texto}`);
    },
  });

  a.recibir({ from: '598991', texto: 'A' });
  await esperar(15);
  a.recibir({ from: '598991', texto: 'B' });
  await a.vaciar();

  assert.deepEqual(orden, ['empieza A', 'termina A', 'empieza B', 'termina B']);
});

test('un mensaje vacio no dispara nada', async () => {
  const turnos = [];
  const a = crearAgrupador({ procesar: async (t, x) => turnos.push(x), esperaMs: 5 });
  a.recibir({ from: '598991', texto: '   ' });
  await a.vaciar();
  assert.equal(turnos.length, 0);
});

test('si un turno falla, el siguiente igual se atiende', async () => {
  const turnos = [];
  const a = crearAgrupador({
    esperaMs: 5,
    procesar: async (tel, t) => {
      if (t === 'rompe') throw new Error('boom');
      turnos.push(t);
    },
  });

  a.recibir({ from: '598991', texto: 'rompe' });
  await a.vaciar();
  a.recibir({ from: '598991', texto: 'sigue' });
  await a.vaciar();

  assert.deepEqual(turnos, ['sigue']);
});

test('el bot contesta una sola vez a una tanda de fragmentos', async () => {
  const s = await montar({ AGRUPAR_ENTRANTES_MS: '25' });
  s.proveedor.simularEntrante({ from: '59899123456', texto: 'hola', id: 'w.1' });
  s.proveedor.simularEntrante({ from: '59899123456', texto: 'necesito un ecommerce', id: 'w.2' });
  s.proveedor.simularEntrante({ from: '59899123456', texto: 'para mi tienda', id: 'w.3' });

  await s.agrupador.vaciar();
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456');
  assert.equal(alLead.length, 1, 'una respuesta, no tres');

  // Y el turno llego entero, no en pedazos.
  const entrantes = s.repo.mensajesDeLead(s.repo.leadPorTelefono('59899123456').id)
    .filter((m) => m.direction === 'in');
  assert.equal(entrantes.length, 1);
  assert.match(entrantes[0].body, /hola\nnecesito un ecommerce\npara mi tienda/);
});
