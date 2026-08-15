'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { CLAVE, LEAD, montar } = require('./helpers');


function postLead(s, body = LEAD) {
  return s.app.inject({
    method: 'POST', url: '/leads',
    headers: { 'x-api-key': CLAVE }, payload: body,
  });
}

test('POST /leads responde 202 y crea el lead', async () => {
  const s = await montar();
  const r = await postLead(s);

  assert.equal(r.statusCode, 202);
  const body = r.json();
  assert.equal(body.ok, true);
  assert.equal(body.status, 'queued');

  const lead = s.repo.leadPorId(body.lead_id);
  assert.equal(lead.nombre, 'Martín Pereyra');
  assert.equal(lead.telefono, '59899123456', 'el telefono se normaliza a E.164 sin +');
  assert.equal(lead.rubro_norm, 'inmobiliaria');
});

test('manda la ficha al AM y la bienvenida al lead', async () => {
  const s = await montar();
  await postLead(s);
  await s.cola.vacia();

  const enviados = s.proveedor.getEnviados();
  assert.equal(enviados.length, 2);

  const ficha = enviados.find((e) => e.to === '59899000111');
  assert.ok(ficha, 'la ficha va al AM configurado');
  assert.match(ficha.texto, /Nuevo lead/);
  assert.match(ficha.texto, /Martín Pereyra/);
  assert.match(ficha.texto, /Inmobiliaria/);
  assert.match(ficha.texto, /\+59899123456/);
  assert.match(ficha.texto, /seguimiento de consultas de alquiler/);
  assert.match(ficha.texto, /wa\.me\/59899123456/);

  const bienvenida = enviados.find((e) => e.to === '59899123456');
  assert.ok(bienvenida, 'la bienvenida va al lead');
  assert.equal(bienvenida.texto, '[bienvenida]');

  // El gancho del rubro ya no se verifica en el texto —lo escribe la IA— sino
  // en que se le haya pasado. Como suena el mensaje se mide en evals/.
  const { construirRedaccion } = require('../src/ia/prompt');
  const prompt = construirRedaccion(s.repo.leadPorTelefono('59899123456'), 'bienvenida', 'x');
  assert.match(prompt, /inmobiliarias/i, 'el prompt lleva el gancho del rubro');
  assert.match(prompt, /seguimiento de consultas de alquiler/, 'y lo que puso en el formulario');
});

test('la ficha al AM sale antes que la bienvenida', async () => {
  const s = await montar();
  await postLead(s);
  await s.cola.vacia();

  const enviados = s.proveedor.getEnviados();
  assert.equal(enviados[0].to, '59899000111', 'am_notice tiene prioridad sobre welcome');
});

test('a las 72h sin respuesta sale el follow-up', async () => {
  const s = await montar();
  await postLead(s);
  await s.cola.vacia();
  s.proveedor.limpiar();

  // A las 25 horas todavia no: el plazo pasa a 72h.
  const en25Horas = new Date(Date.now() + 25 * 3600 * 1000);
  assert.equal(await s.scheduler.correrVencidos(en25Horas), 0);

  const en73Horas = new Date(Date.now() + 73 * 3600 * 1000);
  assert.equal(await s.scheduler.correrVencidos(en73Horas), 1);
  await s.cola.vacia();

  const enviados = s.proveedor.getEnviados();
  const followup = enviados.find((e) => e.to === '59899123456');
  assert.ok(followup, 'le llega el follow-up al lead');
  assert.equal(followup.texto, '[followup]');

  const avisoAM = enviados.find((e) => e.to === '59899000111');
  assert.match(avisoAM.texto, /no respondió en 72h/);

  const lead = s.repo.leadPorTelefono('59899123456');
  assert.equal(lead.status, 'followed_up');
});

test('si el lead responde se cancela el follow-up y se avisa al AM', async () => {
  const s = await montar();
  await postLead(s);
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.servicioLeads.registrarRespuesta('59899123456', 'Sí, me interesa. ¿Cuánto sale?');
  await s.cola.vacia();

  const lead = s.repo.leadPorTelefono('59899123456');
  assert.equal(lead.status, 'replied');
  assert.ok(lead.replied_at);

  const aviso = s.proveedor.getEnviados().find((e) => e.to === '59899000111');
  assert.match(aviso.texto, /respondió/);
  assert.match(aviso.texto, /Cuánto sale/);

  // Y a las 25h ya no sale nada.
  s.proveedor.limpiar();
  const en73Horas = new Date(Date.now() + 73 * 3600 * 1000);
  await s.scheduler.correrVencidos(en73Horas);
  await s.cola.vacia();
  assert.equal(s.proveedor.getEnviados().length, 0, 'no se le insiste a quien ya contesto');
});

test('el mismo external_id no duplica el lead', async () => {
  const s = await montar();
  const primera = await postLead(s);
  const segunda = await postLead(s);

  assert.equal(primera.statusCode, 202);
  assert.equal(segunda.statusCode, 200);
  assert.equal(segunda.json().status, 'ya_existia');
  assert.equal(segunda.json().lead_id, primera.json().lead_id);

  await s.cola.vacia();
  assert.equal(s.proveedor.getEnviados().length, 2, 'no se reenvia nada');
});

test('un telefono ilegible igual notifica al AM y marca el lead como fallido', async () => {
  const s = await montar();
  const r = await postLead(s, { ...LEAD, external_id: 'lead_x', telefono: 'no tengo' });

  assert.equal(r.statusCode, 202);
  assert.equal(r.json().status, 'telefono_invalido');
  await s.cola.vacia();

  const enviados = s.proveedor.getEnviados();
  assert.equal(enviados.length, 1, 'solo la ficha al AM');
  assert.equal(enviados[0].to, '59899000111');
  assert.match(enviados[0].texto, /no reconocido/);

  const lead = s.repo.leadPorId(r.json().lead_id);
  assert.equal(lead.status, 'failed');
});

test('un rubro desconocido usa la plantilla generica', async () => {
  const s = await montar();
  await postLead(s, { ...LEAD, external_id: 'lead_y', rubro: 'Tornería industrial' });
  await s.cola.vacia();

  const lead = s.repo.leadPorTelefono('59899123456');
  assert.equal(lead.rubro_norm, 'generico');

  const bienvenida = s.proveedor.getEnviados().find((e) => e.to === '59899123456');
  assert.equal(bienvenida.texto, '[bienvenida]', 'igual se le escribe');

  // Sin rubro conocido no hay gancho, y el prompt no inventa uno.
  const { construirRedaccion } = require('../src/ia/prompt');
  const prompt = construirRedaccion(lead, 'bienvenida', 'x');
  assert.ok(!/Gancho útil/.test(prompt));
});

test('sin x-api-key no se entra', async () => {
  const s = await montar();
  const r = await s.app.inject({ method: 'POST', url: '/leads', payload: LEAD });
  assert.equal(r.statusCode, 401);
});

test('/health no pide clave y reporta el proveedor', async () => {
  const s = await montar();
  const r = await s.app.inject({ method: 'GET', url: '/health' });
  assert.equal(r.statusCode, 200);
  assert.equal(r.json().provider, 'mock');
  assert.equal(r.json().connected, true);
});

test('rechaza un alta sin nombre ni telefono', async () => {
  const s = await montar();
  const r = await s.app.inject({
    method: 'POST', url: '/leads',
    headers: { 'x-api-key': CLAVE }, payload: { rubro: 'salud' },
  });
  assert.equal(r.statusCode, 400);
  assert.match(r.json().error, /nombre/);
});

test('el link de Calendly va en el follow-up, no en la bienvenida', async () => {
  // Ya no se puede mirar el texto —lo escribe la IA— asi que se mira la
  // instruccion. Un link en el primer mensaje a alguien que nunca te escribio
  // es de las seniales de spam mas fuertes, y esa regla tiene que estar dicha.
  const { situaciones } = require('../src/ia/prompt');
  const s = situaciones('https://calendly.com/scalerics/diagnostico');

  assert.ok(!s.bienvenida.includes('calendly.com'), 'a la bienvenida no se le da el link');
  assert.match(s.bienvenida, /No mandes ningún link/);
  assert.match(s.followup, /calendly\.com/, 'al follow-up si');
});

test('el objetivo de la bienvenida no trae una frase copiable', async () => {
  // Esto paso de verdad: el objetivo daba un ejemplo entre comillas de como
  // retomar lo que el lead conto, y el modelo se lo mandaba textual a leads que
  // no habian contado nada. La instruccion que buscaba evitar el invento era la
  // que lo causaba. Los ejemplos de contenido en un prompt se copian; los de
  // forma —"tenés" y no "tienes"— no.
  const { situaciones } = require('../src/ia/prompt');
  const b = situaciones('https://calendly.com/x').bienvenida;

  assert.ok(!/"vi que quer|"nos lleg|"me alegra/i.test(b), 'sin frases de ejemplo entrecomilladas');
  assert.match(b, /SOLO podés mencionar lo que figura arriba/, 'y si con la prohibicion de inventar');
});
