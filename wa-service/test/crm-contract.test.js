'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { CLAVE, ADMIN, conLead } = require('./helpers');

/** Igual que lo hace routes/wa.py del CRM. */
const comoElCrm = (s, method, url, payload) =>
  s.app.inject({ method, url, headers: { 'x-admin-token': ADMIN }, payload });

test('GET /api/leads devuelve {leads, count} con el shape del bot viejo', async () => {
  const s = await conLead();
  const r = await comoElCrm(s, 'GET', '/api/leads?limit=200');

  assert.equal(r.statusCode, 200);
  const body = r.json();
  assert.equal(body.count, 1);

  const lead = body.leads[0];
  // El frontend del CRM lee estos campos por nombre: si cambian, se rompe.
  for (const campo of [
    'id', 'phone', 'name', 'state', 'score', 'business_type', 'main_problem',
    'team_size', 'budget', 'urgency', 'business_name', 'colors',
    'instagram_web', 'needs', 'created_at', 'last_message_at',
  ]) {
    assert.ok(campo in lead, `falta el campo ${campo}`);
  }
  assert.equal(lead.phone, '59899123456');
  assert.equal(lead.state, 'NEW');
});

test('GET /api/leads/phone/:phone acepta cualquier formato de telefono', async () => {
  const s = await conLead();
  for (const formato of ['59899123456', '099123456', '%2B59899123456', '99123456']) {
    const r = await comoElCrm(s, 'GET', `/api/leads/phone/${formato}`);
    assert.equal(r.statusCode, 200, `fallo con ${formato}`);
    assert.equal(r.json().lead.phone, '59899123456');
  }
});

test('los mensajes vienen como {direction, content, sent_at}', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta('59899123456', 'hola');
  await s.cola.vacia();

  const r = await comoElCrm(s, 'GET', '/api/leads/phone/59899123456');
  const msgs = r.json().messages;
  assert.ok(msgs.length > 0);
  for (const m of msgs) {
    assert.ok('direction' in m && 'content' in m && 'sent_at' in m);
  }
  const entrante = msgs.find((m) => m.direction === 'in');
  assert.equal(entrante.content, 'hola');
});

test('GET /api/leads/search encuentra por nombre parcial', async () => {
  const s = await conLead();
  const r = await comoElCrm(s, 'GET', '/api/leads/search?name=Pereyra');
  assert.equal(r.statusCode, 200);
  assert.equal(r.json().lead.phone, '59899123456');

  const sinNada = await comoElCrm(s, 'GET', '/api/leads/search?name=Nadie');
  assert.equal(sinNada.statusCode, 404);
});

test('POST /api/send encola el mensaje del operador', async () => {
  const s = await conLead();
  const r = await comoElCrm(s, 'POST', '/api/send', { phone: '099123456', text: 'Hola, te escribo yo' });

  assert.equal(r.statusCode, 200);
  assert.equal(r.json().ok, true);
  await s.cola.vacia();

  const enviado = s.proveedor.getEnviados().find((e) => e.to === '59899123456');
  assert.equal(enviado.texto, 'Hola, te escribo yo');
});

test('POST /api/send valida telefono y texto', async () => {
  const s = await conLead();
  assert.equal((await comoElCrm(s, 'POST', '/api/send', { phone: '', text: 'x' })).statusCode, 400);
  assert.equal((await comoElCrm(s, 'POST', '/api/send', { phone: '099123456', text: '  ' })).statusCode, 400);
});

test('POST release devuelve el lead al bot', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta('59899123456', 'quiero hablar con una persona');
  await s.cola.vacia();
  assert.equal(s.repo.leadPorTelefono('59899123456').human_requested, 1);

  const r = await comoElCrm(s, 'POST', '/api/leads/phone/59899123456/release');
  assert.equal(r.statusCode, 200);

  const lead = s.repo.leadPorTelefono('59899123456');
  assert.equal(lead.human_requested, 0);

  // Lo que importa no es el nombre del estado sino que el bot lo atienda. Antes
  // quedaba en 'MENU', un estado que ya no existe: no estaba en ninguna de las
  // dos fases que atiende la IA, asi que el bot se comia el siguiente mensaje
  // sin contestar. Justo el de alguien que vuelve despues de hablar con una
  // persona.
  s.proveedor.limpiar();
  await s.servicioLeads.registrarRespuesta('59899123456', 'hola, sigo interesado');
  await s.cola.vacia();

  assert.ok(
    s.proveedor.getEnviados().some((e) => e.to === '59899123456'),
    'el bot le contesta al lead devuelto'
  );
});

test('sin x-admin-token no se entra a /api/', async () => {
  const s = await conLead();
  const r = await s.app.inject({ method: 'GET', url: '/api/leads' });
  assert.equal(r.statusCode, 401);
  assert.equal(r.json().error, 'Unauthorized');
});

test('el x-api-key del CRM nuevo no sirve para /api/, y viceversa', async () => {
  const s = await conLead();
  const conApiKey = await s.app.inject({
    method: 'GET', url: '/api/leads', headers: { 'x-api-key': CLAVE },
  });
  assert.equal(conApiKey.statusCode, 401, '/api/ solo acepta x-admin-token');

  const conAdmin = await s.app.inject({
    method: 'POST', url: '/leads',
    headers: { 'x-admin-token': ADMIN }, payload: { nombre: 'x', telefono: '099111222' },
  });
  assert.equal(conAdmin.statusCode, 401, '/leads solo acepta x-api-key');
});

test('tambien acepta el token por query, como el bot viejo', async () => {
  const s = await conLead();
  const r = await s.app.inject({ method: 'GET', url: `/api/leads?token=${ADMIN}` });
  assert.equal(r.statusCode, 200);
});

test('POST /leads/:telefono/reiniciar vuelve el lead al principio', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta('59899123456', 'hola, tengo una inmobiliaria');
  await s.cola.vacia();

  const r = await s.app.inject({
    method: 'POST', url: '/leads/099123456/reiniciar', headers: { 'x-api-key': CLAVE },
  });
  assert.equal(r.statusCode, 200);
  assert.equal(r.json().estado, 'NEW');

  const l = s.repo.leadPorTelefono('59899123456');
  assert.equal(l.fsm_state, 'NEW');
  assert.ok(l.conversacion_desde, 'queda marcado desde cuando cuenta la charla nueva');
  assert.ok(s.repo.mensajesDeLead(l.id).length > 0, 'el historial no se toca');
});

test('reiniciar un telefono que no existe da 404', async () => {
  const s = await conLead();
  const r = await s.app.inject({
    method: 'POST', url: '/leads/099888777/reiniciar', headers: { 'x-api-key': CLAVE },
  });
  assert.equal(r.statusCode, 404);
});
