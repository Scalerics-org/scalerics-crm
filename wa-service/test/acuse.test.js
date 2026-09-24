'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { esAcuse } = require('../src/funnel/acuse');
const { conLead } = require('./helpers');
const { S } = require('../src/funnel/states');

const TEL = '59899123456';

test('los acuses de Patricia', () => {
  assert.equal(esAcuse('Ok perfecto'), true);
  assert.equal(esAcuse('Ok'), true);
  assert.equal(esAcuse('Ok bien'), true);
  assert.equal(esAcuse('Perfecto si'), true);
});

test('otras formas comunes de acuse', () => {
  assert.equal(esAcuse('dale, nos vemos'), true);
  assert.equal(esAcuse('genial, gracias!'), true);
  assert.equal(esAcuse('muchas gracias'), true);
  assert.equal(esAcuse('joya'), true);
  assert.equal(esAcuse('bárbaro'), true, 'con acento tambien');
  assert.equal(esAcuse('  OK  '), true, 'mayusculas y espacios de mas');
});

test('con signo de pregunta no es un acuse, aunque tenga palabras de acuse', () => {
  assert.equal(esAcuse('ok, pero ¿puedo cambiar el horario?'), false);
  assert.equal(esAcuse('¿de verdad?'), false);
  // Corto y todo palabras de acuse, pero es una pregunta: "¿estamos?" no es
  // lo mismo que "estamos".
  assert.equal(esAcuse('¿dale?'), false);
  assert.equal(esAcuse('dale?'), false);
});

test('"no" no es un acuse', () => {
  assert.equal(esAcuse('no'), false);
  assert.equal(esAcuse('no puedo mañana'), false);
});

test('mas de cinco palabras deja de ser un acuse', () => {
  assert.equal(esAcuse('dale perfecto genial bueno gracias listo'), false, 'seis palabras');
  assert.equal(esAcuse('dale perfecto genial bueno gracias'), true, 'cinco, todavia entra');
});

test('una sola palabra que no esta en la lista no es acuse', () => {
  assert.equal(esAcuse('interesante'), false);
  assert.equal(esAcuse('mañana'), false);
});

test('solo emojis es un acuse', () => {
  assert.equal(esAcuse('👍'), true);
  assert.equal(esAcuse('🙏'), true);
  assert.equal(esAcuse('👌'), true);
  assert.equal(esAcuse('👍🙏'), true);
  assert.equal(esAcuse('  👍  '), true);
});

test('emoji junto con texto se evalua por las palabras', () => {
  assert.equal(esAcuse('dale 👍'), true, 'dale esta en la lista');
  assert.equal(esAcuse('interesante 👍'), false, '"interesante" no esta en la lista');
});

test('texto vacio o solo espacios no es un acuse', () => {
  assert.equal(esAcuse(''), false);
  assert.equal(esAcuse('   '), false);
});

test('entradas raras no rompen nada', () => {
  assert.equal(esAcuse(null), false);
  assert.equal(esAcuse(undefined), false);
  assert.equal(esAcuse(12345), false);
  assert.equal(esAcuse({}), false);
  assert.equal(esAcuse([]), false);
  assert.equal(esAcuse(true), false);
  assert.doesNotThrow(() => esAcuse('ok '.repeat(50_000)));
  assert.equal(esAcuse('ok '.repeat(50_000)), false, 'un solo "ok" repetido igual pasa el tope de palabras');
});

test('signos de puntuacion sueltos, sin letras, no son un acuse', () => {
  assert.equal(esAcuse('...'), false);
  assert.equal(esAcuse('!!!'), false);
  assert.equal(esAcuse(','), false);
});

// ── cableado: el embudo no le contesta un acuse al que ya esta agendado ─────

function conAgendado(s) {
  const l = s.repo.leadPorTelefono(TEL);
  s.repo.registrarReunion(l.id, {
    meetingTime: '2026-09-23T13:00:00.000Z',
    meetingUrl: 'https://meet.google.com/abc',
    ahoraIso: new Date().toISOString(),
  });
  s.repo.actualizarFunnel(l.id, { fsm_state: S.SCHEDULED });
  return s.repo.leadPorTelefono(TEL);
}

test('al que ya esta agendado, un acuse no le genera respuesta', async () => {
  const s = await conLead();
  conAgendado(s);
  s.proveedor.limpiar();

  for (const acuse of ['Ok perfecto', 'Ok', 'Ok bien', 'Perfecto si']) {
    await s.servicioLeads.registrarRespuesta(TEL, acuse);
  }
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().filter((e) => e.to === TEL).length, 0, 'no le contesta ninguno');
  // Pero el mensaje si quedo registrado: no es lo mismo no contestar que no escuchar.
  const mensajes = s.repo.mensajesDeLead(s.repo.leadPorTelefono(TEL).id);
  assert.equal(mensajes.filter((m) => m.direction === 'in').length, 4);
});

test('pero si pregunta algo, aunque este agendado, se le contesta', async () => {
  const s = await conLead();
  conAgendado(s);
  s.proveedor.limpiar();

  await s.servicioLeads.registrarRespuesta(TEL, 'ok, pero ¿puedo cambiar el horario?');
  await s.cola.vacia();

  assert.ok(s.proveedor.getEnviados().some((e) => e.to === TEL), 'esto no es un acuse, llega al embudo');
});

test('y si dice que no puede, tambien se le contesta', async () => {
  const s = await conLead();
  conAgendado(s);
  s.proveedor.limpiar();

  await s.servicioLeads.registrarRespuesta(TEL, 'no puedo mañana');
  await s.cola.vacia();

  assert.ok(s.proveedor.getEnviados().some((e) => e.to === TEL));
});

test('un "gracias!" sin reunion agendada (en medio del embudo) si se contesta', async () => {
  const s = await conLead();
  // Sin agendar nada: sigue en el embudo normal.
  await s.servicioLeads.registrarRespuesta(TEL, 'gracias!');
  await s.cola.vacia();

  assert.ok(s.proveedor.getEnviados().some((e) => e.to === TEL));
});

test('agendado pero con un estado distinto de SCHEDULED (dato corrupto): se contesta igual', async () => {
  // Defensivo: si algun dia meeting_booked_at queda seteado sin que el estado
  // sea SCHEDULED, no hay que silenciar al lead por las dudas.
  const s = await conLead();
  const l = s.repo.leadPorTelefono(TEL);
  s.repo.registrarReunion(l.id, {
    meetingTime: '2026-09-23T13:00:00.000Z', meetingUrl: '', ahoraIso: new Date().toISOString(),
  });
  s.proveedor.limpiar();

  await s.servicioLeads.registrarRespuesta(TEL, 'Ok');
  await s.cola.vacia();

  assert.ok(s.proveedor.getEnviados().some((e) => e.to === TEL));
});
