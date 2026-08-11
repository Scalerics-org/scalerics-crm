'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { construir } = require('../src/app');

const CLAVE = 'clave-de-test-larguita-1234';
const ADMIN = 'admin-token-de-prueba';

function cfgTest(extra = {}) {
  return Object.freeze({
    PORT: 0, NODE_ENV: 'test', LOG_LEVEL: 'silent', WA_API_KEY: CLAVE,
    ADMIN_TOKEN: ADMIN, CRM_API_URL: '', CRM_ADMIN_TOKEN: '',
    WA_PROVIDER: 'mock', DB_PATH: ':memory:', BAILEYS_AUTH_DIR: './auth',
    AM_PHONES: '59899000111', amPhones: ['59899000111'],
    DEFAULT_COUNTRY_CODE: '598', TZ: 'America/Montevideo',
    ANTHROPIC_API_KEY: '', CALENDLY_LINK: 'https://calendly.com/x', FUNNEL_ENABLED: true,
    FOLLOWUP_DELAY_HOURS: 24, FOLLOWUP_JITTER_MINUTES: 0,
    DELAY_AM_MIN_MS: 0, DELAY_AM_MAX_MS: 0,
    DELAY_WELCOME_MIN_MS: 0, DELAY_WELCOME_MAX_MS: 0,
    DELAY_BETWEEN_MIN_MS: 0, DELAY_BETWEEN_MAX_MS: 0, TYPING_ENABLED: false,
    ...extra,
  });
}

async function montar(extra) {
  const s = construir(cfgTest(extra), { logger: null });
  await s.proveedor.conectar();
  s.servicioLeads.alta({
    external_id: 'l1', nombre: 'Martín Pereyra', rubro: 'Inmobiliaria',
    telefono: '099123456', necesidad: 'automatizar consultas', origen: 'form',
  });
  await s.cola.vacia();
  s.proveedor.limpiar();
  return s;
}

/** Igual que lo hace routes/wa.py del CRM. */
const comoElCrm = (s, method, url, payload) =>
  s.app.inject({ method, url, headers: { 'x-admin-token': ADMIN }, payload });

test('GET /api/leads devuelve {leads, count} con el shape del bot viejo', async () => {
  const s = await montar();
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
  const s = await montar();
  for (const formato of ['59899123456', '099123456', '%2B59899123456', '99123456']) {
    const r = await comoElCrm(s, 'GET', `/api/leads/phone/${formato}`);
    assert.equal(r.statusCode, 200, `fallo con ${formato}`);
    assert.equal(r.json().lead.phone, '59899123456');
  }
});

test('los mensajes vienen como {direction, content, sent_at}', async () => {
  const s = await montar();
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
  const s = await montar();
  const r = await comoElCrm(s, 'GET', '/api/leads/search?name=Pereyra');
  assert.equal(r.statusCode, 200);
  assert.equal(r.json().lead.phone, '59899123456');

  const sinNada = await comoElCrm(s, 'GET', '/api/leads/search?name=Nadie');
  assert.equal(sinNada.statusCode, 404);
});

test('POST /api/send encola el mensaje del operador', async () => {
  const s = await montar();
  const r = await comoElCrm(s, 'POST', '/api/send', { phone: '099123456', text: 'Hola, te escribo yo' });

  assert.equal(r.statusCode, 200);
  assert.equal(r.json().ok, true);
  await s.cola.vacia();

  const enviado = s.proveedor.getEnviados().find((e) => e.to === '59899123456');
  assert.equal(enviado.texto, 'Hola, te escribo yo');
});

test('POST /api/send valida telefono y texto', async () => {
  const s = await montar();
  assert.equal((await comoElCrm(s, 'POST', '/api/send', { phone: '', text: 'x' })).statusCode, 400);
  assert.equal((await comoElCrm(s, 'POST', '/api/send', { phone: '099123456', text: '  ' })).statusCode, 400);
});

test('POST release devuelve el lead al bot', async () => {
  const s = await montar();
  await s.servicioLeads.registrarRespuesta('59899123456', 'quiero hablar con una persona');
  await s.cola.vacia();
  assert.equal(s.repo.leadPorTelefono('59899123456').human_requested, 1);

  const r = await comoElCrm(s, 'POST', '/api/leads/phone/59899123456/release');
  assert.equal(r.statusCode, 200);

  const lead = s.repo.leadPorTelefono('59899123456');
  assert.equal(lead.human_requested, 0);
  assert.equal(lead.fsm_state, 'MENU');
});

test('sin x-admin-token no se entra a /api/', async () => {
  const s = await montar();
  const r = await s.app.inject({ method: 'GET', url: '/api/leads' });
  assert.equal(r.statusCode, 401);
  assert.equal(r.json().error, 'Unauthorized');
});

test('el x-api-key del CRM nuevo no sirve para /api/, y viceversa', async () => {
  const s = await montar();
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
  const s = await montar();
  const r = await s.app.inject({ method: 'GET', url: `/api/leads?token=${ADMIN}` });
  assert.equal(r.statusCode, 200);
});
