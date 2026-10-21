'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');

process.env.WA_LECTOR_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-lector-'));
const base = require('../base');
const { aMensaje, textoDeMensaje, chatDe } = require('../lector');

function db() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-lector-db-'));
  return base.abrir(dir);
}

const EXCLUIDOS = ['Javo', 'Juan Tomasetti', '099 123 456'];

test('excluye por nombre entero y por número, sin tildes ni mayúsculas', () => {
  const ex = (tel, ...nombres) => base.estaExcluido(EXCLUIDOS, { telefono: tel, nombres });
  assert.equal(ex('598991', 'Javo Pérez'), true);
  assert.equal(ex('598991', 'JAVO'), true);
  assert.equal(ex('598991', 'Javier'), false);
  assert.equal(ex('598991', 'Juan Tomasetti'), true);
  assert.equal(ex('598991', 'Juan Pereyra'), false);
  assert.equal(ex('59899123456', 'Cualquiera'), true);
  assert.equal(ex('59899999999', 'Trouville'), false);
  assert.equal(ex(null), false);
});

test('no guarda a un excluido y borra lo previo cuando se sabe cómo está agendado', () => {
  const d = db();
  const m = (id, chat, texto, extra = {}) => ({ id, chat, telefono: chat, de_juan: false, texto, ts: 1000, ...extra });
  assert.equal(base.guardarMensaje(d, EXCLUIDOS, m('1', '59891111111', 'hola', { perfil: 'Javo' })), false);
  assert.equal(base.guardarMensaje(d, EXCLUIDOS, m('2', '59892222222', 'hola', { perfil: 'Javi' })), true);
  base.guardarContacto(d, { chat: '59892222222', telefono: '59892222222', agendado: 'Javo socio' });
  assert.equal(base.purgarExcluidos(d, EXCLUIDOS), 1);
  assert.equal(base.guardarMensaje(d, EXCLUIDOS, m('3', '59893333333', '¿me pasás la propuesta?', { perfil: 'TNT Burger' })), true);
  const filas = d.prepare('SELECT id FROM mensajes').all().map((f) => f.id);
  assert.deepEqual(filas, ['3']);
});

test('nuevos trae solo lo posterior a la marca, con contexto, y marcar avanza', () => {
  const d = db();
  const ahora = Math.floor(Date.now() / 1000);
  base.guardarContacto(d, { chat: '598944', telefono: '598944', agendado: 'Trouville' });
  base.guardarMensaje(d, [], { id: 'a', chat: '598944', de_juan: true, texto: 'el jueves te paso la propuesta', ts: ahora - 100 });
  base.guardarMensaje(d, [], { id: 'b', chat: '598944', de_juan: false, texto: 'dale', ts: ahora - 50 });
  const n = base.chatsNuevos(d, []);
  assert.equal(n.chats.length, 1);
  assert.equal(n.chats[0].contacto, 'Trouville');
  assert.deepEqual(n.chats[0].mensajes.map((x) => x.de), ['Juan', 'contacto']);
  base.marcar(d, 'procesado_hasta', n.hasta);
  assert.equal(base.chatsNuevos(d, []).chats.length, 0);
  assert.equal(base.chat(d, [], 'trouv')[0].mensajes.length, 2);
  assert.equal(base.chat(d, ['Trouville'], 'trouv').length, 0);
});

test('saca el texto o marca el tipo, y solo acepta chats uno a uno', () => {
  assert.equal(textoDeMensaje({ message: { conversation: 'hola' } }), 'hola');
  assert.equal(textoDeMensaje({ message: { ephemeralMessage: { message: { extendedTextMessage: { text: 'x' } } } } }), 'x');
  assert.equal(textoDeMensaje({ message: { audioMessage: {} } }), '[audio]');
  assert.equal(chatDe({ remoteJid: '123@g.us' }, new Map()), null);
  assert.equal(chatDe({ remoteJid: 'status@broadcast' }, new Map()), null);
  assert.deepEqual(chatDe({ remoteJid: '777@lid', remoteJidAlt: '59894053389@s.whatsapp.net' }, new Map()),
    { chat: '59894053389', telefono: '59894053389' });
  assert.deepEqual(chatDe({ remoteJid: '777@lid' }, new Map([['777', '59899']])), { chat: '59899', telefono: '59899' });
  const m = aMensaje({ key: { id: 'z', remoteJid: '59894053389@s.whatsapp.net', fromMe: true },
    message: { conversation: 'te llamo mañana' }, messageTimestamp: 1700000000 }, new Map());
  assert.equal(m.de_juan, true);
  assert.equal(m.ts, 1700000000);
});

test('el lector no tiene cómo mandar mensajes', () => {
  const codigo = fs.readFileSync(path.join(__dirname, '..', 'lector.js'), 'utf8');
  assert.equal(/sendMessage|readMessages|sendPresenceUpdate|sendReceipt/.test(codigo), false);
});
