'use strict';

const test = require('node:test');
const assert = require('node:assert');
const {
  jidDeTelefono, telefonoDeJid, textoDeMensaje, BACKOFF_MS,
} = require('../src/providers/baileys');
const { crearProveedor } = require('../src/providers');
const { montar } = require('./helpers');

// Lo que se puede probar sin un telefono vinculado: el mapeo de JIDs, la
// extraccion de texto y el cableado. La conexion real se valida escaneando
// el QR con el numero secundario.

test('el telefono se convierte al JID de WhatsApp', () => {
  assert.equal(jidDeTelefono('59899123456'), '59899123456@s.whatsapp.net');
  assert.equal(jidDeTelefono(59899123456), '59899123456@s.whatsapp.net');
});

test('un JID de grupo se respeta tal cual', () => {
  assert.equal(jidDeTelefono('120363000@g.us'), '120363000@g.us');
});

test('del JID se saca el telefono, con o sin sufijo de dispositivo', () => {
  assert.equal(telefonoDeJid('59899123456@s.whatsapp.net'), '59899123456');
  assert.equal(telefonoDeJid('59899123456:12@s.whatsapp.net'), '59899123456');
  assert.equal(telefonoDeJid(undefined), '');
});

test('extrae el texto de los formatos de mensaje que importan', () => {
  assert.equal(textoDeMensaje({ message: { conversation: 'hola' } }), 'hola');
  assert.equal(
    textoDeMensaje({ message: { extendedTextMessage: { text: 'con formato' } } }),
    'con formato'
  );
  assert.equal(
    textoDeMensaje({ message: { imageMessage: { caption: 'mira esto' } } }),
    'mira esto'
  );
  assert.equal(
    textoDeMensaje({ message: { listResponseMessage: { title: 'opcion 1' } } }),
    'opcion 1'
  );
});

test('un mensaje sin texto no rompe', () => {
  assert.equal(textoDeMensaje({}), '');
  assert.equal(textoDeMensaje({ message: {} }), '');
  assert.equal(textoDeMensaje({ message: { audioMessage: {} } }), '');
  assert.equal(textoDeMensaje(null), '');
});

test('el backoff crece: 30s, 5min, 30min', () => {
  assert.deepEqual(BACKOFF_MS, [30_000, 300_000, 1_800_000]);
  for (let i = 1; i < BACKOFF_MS.length; i++) {
    assert.ok(BACKOFF_MS[i] > BACKOFF_MS[i - 1], 'cada reintento espera mas');
  }
});

test('la fabrica construye el proveedor baileys sin conectarse', () => {
  const p = crearProveedor(
    { WA_PROVIDER: 'baileys', BAILEYS_AUTH_DIR: './auth', BAILEYS_PHONE: '' },
    { logger: null }
  );
  assert.equal(p.nombre, 'baileys');
  assert.deepEqual(p.capacidades, { typingIndicator: true, textoLibre: true, grupos: true });
  assert.equal(p.estado().conectado, false);
  assert.equal(p.qrCrudo(), null);
});

test('enviar sin conexion falla con un mensaje claro', async () => {
  const p = crearProveedor(
    { WA_PROVIDER: 'baileys', BAILEYS_AUTH_DIR: './auth' },
    { logger: null }
  );
  await assert.rejects(() => p.enviarTexto('59899123456', 'hola'), /no esta conectado/);
});

test('un proveedor desconocido falla al construirse', () => {
  assert.throws(
    () => crearProveedor({ WA_PROVIDER: 'twilio' }, {}),
    /WA_PROVIDER desconocido/
  );
});

// ── cableado del entrante ─────────────────────────────────────────────────────

test('un mensaje entrante entra al embudo', async () => {
  const s = await montar();
  await s.servicioLeads.alta({
    external_id: 'ent1', nombre: 'Ana', rubro: 'salud', telefono: '099555444', origen: 'form',
  });
  await s.cola.vacia();
  s.proveedor.limpiar();

  // Como si Baileys hubiera recibido el mensaje.
  s.proveedor.simularEntrante({ from: '59899555444', texto: 'hola', id: 'wamid.1' });
  await s.agrupador.vaciar();
  await s.cola.vacia();

  const lead = s.repo.leadPorTelefono('59899555444');
  assert.equal(lead.status, 'replied');
  assert.equal(lead.fsm_state, 'CONVERSANDO');

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899555444');
  assert.ok(alLead.length > 0, 'le contesta');
});

test('quien escribe al numero sin pasar por el formulario tambien entra', async () => {
  // El bot viejo hacia findOrCreate: era inbound-first, la gente escribia desde
  // un QR o un anuncio. Sin esto, el que le escribe al WhatsApp de la empresa
  // recibe silencio.
  const s = await montar();
  s.proveedor.simularEntrante({
    from: '59891111111', texto: 'hola, vi su web', id: 'wamid.2', nombre: 'Ana Torres',
  });
  await s.agrupador.vaciar();
  await s.cola.vacia();

  const lead = s.repo.leadPorTelefono('59891111111');
  assert.ok(lead, 'se da de alta el lead');
  assert.equal(lead.origen, 'wa');
  assert.equal(lead.nombre, 'Ana Torres', 'usa el nombre de perfil de WhatsApp');
  assert.equal(lead.fsm_state, 'CONVERSANDO');

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59891111111');
  assert.equal(alLead.length, 1, 'le contesta');
  assert.equal(alLead[0].texto, '[conversacion]');

  // Al AM le llega un "nuevo contacto", no un "respondió": no respondio nada,
  // escribio de la nada.
  const alAM = s.proveedor.getEnviados().find((e) => e.to === '59899000111');
  assert.match(alAM.texto, /Nuevo contacto por WhatsApp/);
  assert.match(alAM.texto, /wa\.me\/59891111111/);
  assert.ok(!alAM.texto.includes('respondió'));
});

test('sin nombre de perfil igual entra, y el saludo no queda raro', async () => {
  const s = await montar();
  s.proveedor.simularEntrante({ from: '59891111112', texto: 'hola', id: 'wamid.3' });
  await s.agrupador.vaciar();
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().find((e) => e.to === '59891111112');
  assert.equal(alLead.texto, '[conversacion]', 'igual le contesta');

  const alAM = s.proveedor.getEnviados().find((e) => e.to === '59899000111');
  assert.match(alAM.texto, /sin nombre/);
});

// ── endpoints de sesion ───────────────────────────────────────────────────────

test('GET /session/qr avisa cuando no hay QR pendiente', async () => {
  const s = await montar();
  const r = await s.app.inject({
    method: 'GET', url: '/session/qr', headers: { 'x-api-key': require('./helpers').CLAVE },
  });
  assert.equal(r.statusCode, 404);
  assert.match(r.json().error, /ya esta vinculado/);
});

test('POST /session/logout exige confirmacion explicita', async () => {
  const s = await montar();
  const sin = await s.app.inject({
    method: 'POST', url: '/session/logout',
    headers: { 'x-api-key': require('./helpers').CLAVE }, payload: {},
  });
  assert.equal(sin.statusCode, 400);
  assert.match(sin.json().error, /confirm/);
});

// ── direccionamiento @lid ─────────────────────────────────────────────────────

const { telefonoDelMensaje } = require('../src/providers/baileys');

test('con @lid usa senderPn, que es donde viaja el telefono real', () => {
  // WhatsApp migro a LIDs: remoteJid trae un id de dispositivo, no el numero.
  // Leer solo remoteJid daba "227771510997245" y ningun lead matcheaba.
  assert.equal(
    telefonoDelMensaje({ remoteJid: '227771510997245@lid', senderPn: '59894053389@s.whatsapp.net' }),
    '59894053389'
  );
});

test('en grupos usa participantPn', () => {
  assert.equal(
    telefonoDelMensaje({ remoteJid: '1203@g.us', participantPn: '59894053389@s.whatsapp.net' }),
    '59894053389'
  );
});

test('sin LID sigue leyendo remoteJid', () => {
  assert.equal(telefonoDelMensaje({ remoteJid: '59894053389@s.whatsapp.net' }), '59894053389');
  assert.equal(telefonoDelMensaje({ remoteJid: '59894053389:12@s.whatsapp.net' }), '59894053389');
});

test('un LID sin telefono asociado se descarta en vez de adivinar', () => {
  assert.equal(telefonoDelMensaje({ remoteJid: '227771510997245@lid' }), null);
  assert.equal(telefonoDelMensaje({}), null);
  assert.equal(telefonoDelMensaje(null), null);
});

test('resuelve el telefono con los nombres de campo de baileys 6 y de 7', () => {
  // 7.x renombro senderPn/participantPn a remoteJidAlt/participantAlt. Mirar
  // solo los viejos hacia que TODOS los entrantes se descartaran tras subir.
  assert.equal(
    telefonoDelMensaje({ remoteJid: '10660209537270@lid', remoteJidAlt: '59892781598@s.whatsapp.net' }),
    '59892781598', 'baileys 7'
  );
  assert.equal(
    telefonoDelMensaje({ remoteJid: '1203@g.us', participantAlt: '59892781598@s.whatsapp.net' }),
    '59892781598', 'baileys 7, grupo'
  );
  assert.equal(
    telefonoDelMensaje({ remoteJid: '10660209537270@lid', senderPn: '59892781598@s.whatsapp.net' }),
    '59892781598', 'baileys 6'
  );
});
