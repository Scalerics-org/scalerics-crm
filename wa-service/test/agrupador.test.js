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

  // Dos mensajes: la presentacion y UNA respuesta. Lo que se prueba es que la
  // tanda de tres fragmentos genere una sola respuesta, no tres.
  const { crearTextos } = require('../src/templates/funnel');
  const respuestas = s.proveedor.getEnviados()
    .filter((e) => e.to === '59899123456' && e.texto !== crearTextos().BIENVENIDA);
  assert.equal(respuestas.length, 1, 'una respuesta a la tanda, no tres');

  // Y el turno llego entero, no en pedazos.
  const entrantes = s.repo.mensajesDeLead(s.repo.leadPorTelefono('59899123456').id)
    .filter((m) => m.direction === 'in');
  assert.equal(entrantes.length, 1);
  assert.match(entrantes[0].body, /hola\nnecesito un ecommerce\npara mi tienda/);
});

// ── reentregas de WhatsApp ───────────────────────────────────────────────────

test('el mismo mensaje reenviado no se contesta dos veces', async () => {
  // Paso en produccion: se cayo la conexion a mitad de un turno, WhatsApp
  // reenvio el "hola" al reconectar, y el lead recibio dos respuestas — con la
  // segunda llegando despues de la primera, sin sentido en la conversacion.
  const s = await montar({ AGRUPAR_ENTRANTES_MS: '15' });
  const { crearTextos } = require('../src/templates/funnel');

  s.proveedor.simularEntrante({ from: '59899123456', texto: 'hola', id: 'wamid.ABC' });
  await s.agrupador.vaciar();
  await s.cola.vacia();

  // WhatsApp lo reenvia con el mismo id al reconectar.
  s.proveedor.simularEntrante({ from: '59899123456', texto: 'hola', id: 'wamid.ABC' });
  await s.agrupador.vaciar();
  await s.cola.vacia();

  const respuestas = s.proveedor.getEnviados()
    .filter((e) => e.to === '59899123456' && e.texto !== crearTextos().BIENVENIDA);
  assert.equal(respuestas.length, 1, 'una sola respuesta');
});

test('dos mensajes distintos si se atienden los dos', async () => {
  const s = await montar({ AGRUPAR_ENTRANTES_MS: '15' });
  const { crearTextos } = require('../src/templates/funnel');

  s.proveedor.simularEntrante({ from: '59899123456', texto: 'hola', id: 'wamid.A' });
  await s.agrupador.vaciar();
  await s.cola.vacia();
  s.proveedor.simularEntrante({ from: '59899123456', texto: 'tengo una panaderia', id: 'wamid.B' });
  await s.agrupador.vaciar();
  await s.cola.vacia();

  const respuestas = s.proveedor.getEnviados()
    .filter((e) => e.to === '59899123456' && e.texto !== crearTextos().BIENVENIDA);
  assert.equal(respuestas.length, 2);
});

test('un entrante sin id no se descarta', async () => {
  // El mock y algunos tipos de mensaje no traen id. Perderlos seria peor que
  // arriesgarse a un duplicado.
  const s = await montar({ AGRUPAR_ENTRANTES_MS: '15' });
  assert.equal(s.repo.entranteEsNuevo(null), true);
  assert.equal(s.repo.entranteEsNuevo(''), true);
});

test('los ids viejos se limpian', async () => {
  const s = await montar();
  s.repo.entranteEsNuevo('wamid.viejo');
  s.repo.db.prepare("UPDATE inbound_seen SET seen_at = datetime('now','-10 days')").run();
  assert.equal(s.repo.limpiarEntrantesVistos(3), 1);
  assert.equal(s.repo.entranteEsNuevo('wamid.viejo'), true, 'despues de limpiar vuelve a ser nuevo');
});

test('el bot no contesta con una pregunta que el lead ya respondio', async () => {
  // Reproduce lo que paso en produccion. Con las demoras reales de la cola, la
  // respuesta a un mensaje sale despues de que el lead contesto el siguiente:
  // el bot preguntaba "¿cómo se llama tu negocio?" cuando ya se lo habian dicho.
  const s = await montar({
    AGRUPAR_ENTRANTES_MS: '5',
    // Demoras largas: la primera respuesta queda esperando en la cola.
    DELAY_BETWEEN_MIN_MS: '400', DELAY_BETWEEN_MAX_MS: '400',
  });
  await s.servicioLeads.alta({
    external_id: 'x1', nombre: 'Juanchi', telefono: '099123456', origen: 'form',
  });

  // Dos turnos seguidos, el segundo antes de que salga la respuesta al primero.
  await s.servicioLeads.registrarRespuesta('59899123456', 'hola');
  await s.servicioLeads.registrarRespuesta('59899123456', 'se llama Easy Rider');
  await s.cola.vacia();

  const { crearTextos } = require('../src/templates/funnel');
  const respuestas = s.proveedor.getEnviados()
    .filter((e) => e.to === '59899123456' && e.texto !== crearTextos().BIENVENIDA);

  assert.equal(respuestas.length, 1, 'sale la respuesta al ultimo mensaje, no las dos');
});
