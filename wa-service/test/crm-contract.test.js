'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { CLAVE, ADMIN, conLead, stubTranscriptor } = require('./helpers');

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

/**
 * El boton de on/off del panel. Es el mismo que tiene el CRM de la bloquera y
 * manda sobre la pausa automatica: apagado es apagado.
 */
test('POST bot apaga y prende el bot para ese lead', async () => {
  const s = await conLead();

  const off = await comoElCrm(s, 'POST', '/api/leads/phone/59899123456/bot', { activo: false });
  assert.equal(off.statusCode, 200);
  assert.equal(s.repo.leadPorTelefono('59899123456').bot_enabled, 0);

  const on = await comoElCrm(s, 'POST', '/api/leads/phone/59899123456/bot', { activo: true });
  assert.equal(on.statusCode, 200);
  const lead = s.repo.leadPorTelefono('59899123456');
  assert.equal(lead.bot_enabled, 1);
  // Prenderlo a mano tambien levanta la pausa: si no, decis que si y el bot
  // sigue mudo unas horas mas sin ninguna explicacion.
  assert.equal(lead.bot_pausado_hasta, null);
});

test('POST bot sobre un telefono que no existe da 404', async () => {
  const s = await conLead();
  const r = await comoElCrm(s, 'POST', '/api/leads/phone/59899000000/bot', { activo: false });
  assert.equal(r.statusCode, 404);
});

test('la lista de leads dice si el bot esta prendido para cada uno', async () => {
  // El panel necesita saber en que posicion dibujar el interruptor, y si hay
  // una pausa corriendo, para poder decir por que esta callado.
  const s = await conLead();
  const r = await comoElCrm(s, 'GET', '/api/leads');
  const lead = r.json().leads[0];

  assert.equal(lead.bot_enabled, true, 'por defecto contesta');
  assert.equal(lead.bot_paused_until, null);
});

/**
 * Los avisos al equipo no son parte de la conversacion con el lead.
 *
 * Se guardan con el lead_id del lead del que HABLAN, pero se mandan a otro
 * numero. El panel los dibujaba en el medio del hilo, asi que mirandolo no
 * habia forma de saber que vio el lead y que no: la mitad de lo que parecia
 * que le escribiste nunca le llego.
 */
test('la conversacion del panel no trae los avisos al equipo', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta('59899123456', 'hola, tengo una panadería');
  await s.cola.vacia();

  const lead = s.repo.leadPorTelefono('59899123456');
  s.repo.registrarMensaje({
    lead_id: lead.id, direction: 'out', kind: 'am_notice',
    body: '🔔 Nuevo contacto por WhatsApp', provider: 'baileys',
    status: 'sent', destino: '59894053389',
  });

  const r = await comoElCrm(s, 'GET', '/api/leads/phone/59899123456');
  const cuerpos = r.json().messages.map((m) => m.content);

  assert.ok(!cuerpos.some((c) => c.includes('Nuevo contacto por WhatsApp')),
    'el aviso al equipo no va en el hilo del lead');
  assert.ok(cuerpos.includes('hola, tengo una panadería'), 'lo que el lead escribio si');
});

/**
 * El audio del que salio la transcripcion. El bot no tiene IP publica, asi que
 * el navegador no puede pedirselo: va navegador -> CRM -> bot -> archivo.
 */
test('la nota de voz se puede bajar desde el CRM', async () => {
  const s = await conLead({ openai: stubTranscriptor({ respuestas: { transcripcion: 'tengo una panadería' } }) });

  await s.proveedor.simularSinTexto({
    from: '59899123456', tipo: 'audio', segundos: 7,
    descargar: async () => Buffer.from('ogg-de-prueba'),
  });
  await s.agrupador.vaciar();
  await s.cola.vacia();

  const conv = await comoElCrm(s, 'GET', '/api/leads/phone/59899123456');
  const conAudio = conv.json().messages.find((m) => m.media && m.media.length);
  assert.ok(conAudio, 'el mensaje viene con su audio');
  assert.equal(conAudio.content, 'tengo una panadería');
  assert.equal(conAudio.media[0].tipo, 'audio');
  assert.equal(conAudio.media[0].segundos, 7);

  const r = await comoElCrm(s, 'GET', conAudio.media[0].url);
  assert.equal(r.statusCode, 200);
  assert.equal(r.headers['content-type'], 'audio/ogg');
  assert.equal(r.body, 'ogg-de-prueba');
});

test('pedir un audio que no existe da 404, y no se puede salir del directorio', async () => {
  const s = await conLead();
  assert.equal((await comoElCrm(s, 'GET', '/api/messages/9999/media/0')).statusCode, 404);
});
