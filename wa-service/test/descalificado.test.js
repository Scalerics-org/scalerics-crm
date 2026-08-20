'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { conLead, stubOpenAI, ADMIN } = require('./helpers');
const { S } = require('../src/funnel/states');
const { NO_CLIENTE, HERRAMIENTA } = require('../src/ia/agente');

const TEL = '59899123456';

const stubQueVe = (queQuiere) => stubOpenAI({ datos: { que_quiere: queQuiere } });

const jobs = (s, leadId) => s.repo.db
  .prepare("SELECT type FROM jobs WHERE lead_id = ? AND status = 'pending'")
  .all(leadId).map((j) => j.type);

const comoElCrm = (s, metodo, url, payload) => s.app.inject({
  method: metodo, url, headers: { 'x-admin-token': ADMIN }, payload,
});

// ── la via principal: a mano desde el CRM ────────────────────────────────────

test('el CRM puede marcar que no es un cliente posible', async () => {
  const s = await conLead();
  const r = await comoElCrm(s, 'POST', `/api/leads/phone/${TEL}/descartar`, { motivo: 'manda CV' });
  assert.equal(r.statusCode, 200);

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.fsm_state, S.DISQUALIFIED);
  assert.equal(l.no_cliente_motivo, 'manda CV');
  assert.ok(l.no_cliente_desde);
});

test('al descartarlo se le deja de programar todo', async () => {
  const s = await conLead();
  const l = s.repo.leadPorTelefono(TEL);
  assert.ok(jobs(s, l.id).includes('followup'), 'el alta le dejo el seguimiento');

  await comoElCrm(s, 'POST', `/api/leads/phone/${TEL}/descartar`, {});

  assert.deepEqual(jobs(s, l.id), [], 'no le queda nada programado');
});

test('y no lo derivan al comercial por dejar de escribir', async () => {
  const s = await conLead();
  await comoElCrm(s, 'POST', `/api/leads/phone/${TEL}/descartar`, {});
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(new Date(Date.now() + 2 * 3600_000));
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().length, 0);
});

/**
 * Equivocarse para este lado cuesta un cliente, asi que salir tiene que ser
 * facil y no depender de que alguien mire el CRM.
 */
test('se revierte desde el CRM', async () => {
  const s = await conLead();
  await comoElCrm(s, 'POST', `/api/leads/phone/${TEL}/descartar`, { motivo: 'me confundi' });

  const r = await comoElCrm(s, 'POST', `/api/leads/phone/${TEL}/recuperar`);
  assert.equal(r.statusCode, 200);

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.fsm_state, S.CONVERSANDO);
  assert.equal(l.no_cliente_motivo, null);
});

test('y tambien solo: si vuelve a escribir, el bot lo atiende', async () => {
  const s = await conLead();
  await comoElCrm(s, 'POST', `/api/leads/phone/${TEL}/descartar`, {});
  s.proveedor.limpiar();

  await s.servicioLeads.registrarRespuesta(TEL, 'perdon, en realidad quiero una web');
  await s.cola.vacia();

  assert.ok(s.proveedor.getEnviados().some((e) => e.to === TEL), 'le contesta');
  assert.equal(s.repo.leadPorTelefono(TEL).fsm_state, S.CONVERSANDO, 'y sale del descarte');
});

test('un telefono que no existe da 404', async () => {
  const s = await conLead();
  const r = await comoElCrm(s, 'POST', '/api/leads/phone/59899000000/descartar', {});
  assert.equal(r.statusCode, 404);
});

// ── el bot: clasifica siempre, decide solo si se lo dejan ────────────────────

test('con la decision apagada, el bot anota pero NO descalifica', async () => {
  const s = await conLead({ openai: stubQueVe('trabajo') });
  await s.servicioLeads.registrarRespuesta(TEL, 'hola, les mando mi CV');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.no_cliente_motivo, 'trabajo', 'queda anotado para poder revisarlo');
  assert.notEqual(l.fsm_state, S.DISQUALIFIED, 'pero la decision es de una persona');
});

test('con la decision prendida, el bot si descalifica', async () => {
  const s = await conLead({
    openai: stubQueVe('trabajo'),
    DESCALIFICACION_AUTOMATICA: 'true',
  });
  await s.servicioLeads.registrarRespuesta(TEL, 'hola, les mando mi CV');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.fsm_state, S.DISQUALIFIED);
  assert.equal(l.no_cliente_motivo, 'trabajo');
});

test('un cliente normal no se toca, ni con la decision prendida', async () => {
  const s = await conLead({
    openai: stubQueVe('un_servicio'),
    DESCALIFICACION_AUTOMATICA: 'true',
  });
  await s.servicioLeads.registrarRespuesta(TEL, 'tengo una panadería y quiero una web');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.notEqual(l.fsm_state, S.DISQUALIFIED);
  assert.equal(l.no_cliente_motivo, null, 'ni se anota nada');
});

test('los motivos del codigo son los que se le ofrecen al modelo', () => {
  const enEnum = HERRAMIENTA.function.parameters.properties.que_quiere.enum;
  assert.deepEqual(enEnum, ['un_servicio', ...NO_CLIENTE]);
  assert.ok(!NO_CLIENTE.includes('un_servicio'), 'el caso normal nunca descalifica');
});
