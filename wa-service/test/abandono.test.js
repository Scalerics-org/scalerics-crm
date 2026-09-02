'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { conLead, stubModelo } = require('./helpers');
const { S } = require('../src/funnel/states');
const { correspondeDerivar } = require('../src/funnel/abandono');

const TEL = '59899123456';
const AM = '59899000111';

const pendientes = (s, leadId, tipo) => s.repo.db
  .prepare("SELECT run_at FROM jobs WHERE lead_id = ? AND type = ? AND status = 'pending'")
  .all(leadId, tipo);

/** Corre el reloj hasta pasado el plazo de abandono. */
const pasadoElPlazo = (s) => new Date(Date.now() + (s.cfg.ABANDONO_MINUTOS + 5) * 60_000);

test('al contestar, se le arma el reloj del abandono', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta(TEL, 'hola, cuento con una parrilla');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(pendientes(s, l.id, 'abandono').length, 1);
});

/**
 * El reloj mide silencio desde lo ultimo que se hablo, no desde el principio.
 *
 * Si no se reiniciara, alguien que conversa tranquilo durante una hora seria
 * derivado en el medio de la conversacion — que es justo lo contrario de
 * haberse ido.
 */
test('cada mensaje reinicia el reloj', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta(TEL, 'hola');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  const primero = pendientes(s, l.id, 'abandono')[0].run_at;

  await new Promise((r) => setTimeout(r, 1100));
  await s.servicioLeads.registrarRespuesta(TEL, 'tengo una parrilla');
  await s.cola.vacia();

  const jobs = pendientes(s, l.id, 'abandono');
  assert.equal(jobs.length, 1, 'sigue habiendo uno solo');
  assert.ok(jobs[0].run_at > primero, 'y quedo corrido para mas adelante');
});

test('pasado el plazo, lo levanta el agente comercial', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta(TEL, 'hola, tengo una parrilla');
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(pasadoElPlazo(s));
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.fsm_state, S.HUMAN_QUEUED);
  assert.equal(l.human_requested, 1);
  assert.equal(l.motivo_derivacion, 'abandono');

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === TEL);
  assert.equal(alLead.length, 1, 'se le avisa que lo van a contactar');
  assert.match(alLead[0].texto, /derivado_por_abandono/);
});

test('el aviso al agente comercial lleva el contexto y los ultimos mensajes', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta(TEL, 'tengo una parrilla con delivery');
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(pasadoElPlazo(s));
  await s.cola.vacia();

  const aviso = s.proveedor.getEnviados().find((e) => e.to === AM);
  assert.ok(aviso, 'le llega al numero del agente comercial');
  assert.match(aviso.texto, /dejo de contestar en el medio/);
  assert.match(aviso.texto, /parrilla con delivery/, 'con lo que dijo, para no arrancar a ciegas');
  assert.match(aviso.texto, new RegExp(TEL));
});

/**
 * El que agendo y no volvio a escribir es el caso de exito.
 *
 * Mandarselo al agente comercial como "se fue" es ruido en el peor lugar: el
 * que recibe avisos que no sirven deja de mirarlos, y despues no ve los que si.
 */
test('al que agendo no se lo deriva por dejar de escribir', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta(TEL, 'hola');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  s.servicioLeads.registrarReunion(l.id, {
    meeting_time: '2026-09-01T16:00:00.000Z',
    meeting_url: 'https://meet.google.com/x',
  });
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(pasadoElPlazo(s));
  await s.cola.vacia();

  assert.notEqual(s.repo.leadPorTelefono(TEL).fsm_state, S.HUMAN_QUEUED);
  assert.ok(!s.proveedor.getEnviados().some((e) => /derivado_por_abandono/.test(e.texto)));
});

test('al que se dio de baja tampoco', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta(TEL, 'hola');
  await s.cola.vacia();
  await s.servicioLeads.registrarRespuesta(TEL, 'baja');
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(pasadoElPlazo(s));
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().length, 0, 'silencio absoluto');
});

test('al que ya tiene una persona encima no se lo deriva de nuevo', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta(TEL, 'quiero hablar con una persona');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.fsm_state, S.HUMAN_QUEUED, 'ya esta derivado');
  assert.equal(pendientes(s, l.id, 'abandono').length, 0, 'ni se le arma el reloj');
});

/**
 * Si la IA no puede escribirle al lead, no se deriva a medias: el agente
 * comercial recibiria la conversacion mientras del otro lado la charla se corta
 * sin explicacion.
 */
test('si la IA no puede escribir, se reintenta en vez de dejarlo a medias', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta(TEL, 'hola');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  s.proveedor.limpiar();
  // La IA se cae justo cuando toca derivar.
  s.embudo.derivarPorAbandono = async () => false;

  await s.scheduler.correrVencidos(pasadoElPlazo(s));
  await s.cola.vacia();

  assert.notEqual(s.repo.leadPorTelefono(TEL).fsm_state, S.HUMAN_QUEUED, 'no se deriva a medias');
  assert.equal(pendientes(s, l.id, 'abandono').length, 1, 'el job sigue vivo para reintentar');
});

// ── la regla, sola ───────────────────────────────────────────────────────────

test('la regla de cuando corresponde derivar', () => {
  assert.equal(correspondeDerivar({ fsm_state: S.CONVERSANDO }), true);
  assert.equal(correspondeDerivar({ fsm_state: S.MEETING_LINK_SENT }), true, 'tiene el link y no reservo');
  assert.equal(correspondeDerivar({ fsm_state: S.SCHEDULED }), false);
  assert.equal(correspondeDerivar({ fsm_state: S.HUMAN_QUEUED }), false);
  assert.equal(correspondeDerivar({ fsm_state: S.OPT_OUT }), false);
  assert.equal(correspondeDerivar({ fsm_state: S.DISQUALIFIED }), false);
  assert.equal(correspondeDerivar({ fsm_state: S.CONVERSANDO, opt_out: 1 }), false);
  assert.equal(correspondeDerivar({ fsm_state: S.CONVERSANDO, human_requested: 1 }), false);
  assert.equal(
    correspondeDerivar({ fsm_state: S.CONVERSANDO, meeting_booked_at: '2026-09-01' }),
    false,
    'agendo: no se fue, hizo lo que se le pidio'
  );
  assert.equal(correspondeDerivar(null), false);
});

test('con ABANDONO_MINUTOS en cero no se arma nada', async () => {
  const s = await conLead({ ABANDONO_MINUTOS: '0' });
  await s.servicioLeads.registrarRespuesta(TEL, 'hola');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(pendientes(s, l.id, 'abandono').length, 0);
});
