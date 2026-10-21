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
  assert.deepEqual(chatDe({ remoteJid: '888@lid' }, new Map()), { chat: 'lid:888', telefono: null });
  const m = aMensaje({ key: { id: 'z', remoteJid: '59894053389@s.whatsapp.net', fromMe: true },
    message: { conversation: 'te llamo mañana' }, messageTimestamp: 1700000000 }, new Map());
  assert.equal(m.de_juan, true);
  assert.equal(m.ts, 1700000000);
});

test('solo manda desde la cola de plantillas y nunca marca como leído', () => {
  const codigo = fs.readFileSync(path.join(__dirname, '..', 'lector.js'), 'utf8');
  assert.equal(/readMessages|sendPresenceUpdate|sendReceipt/.test(codigo), false);
  const envios = codigo.match(/sendMessage\(/g) || [];
  assert.equal(envios.length, 1);
  const cola = codigo.slice(codigo.indexOf('async function enviarCola'), codigo.indexOf('if (!vincular) setInterval(enviarCola'));
  assert.ok(cola.includes('sendMessage('));
});

test('un chat sin número de un excluido queda afuera por su nombre de perfil, y se junta al saber el número', () => {
  const d = db();
  const ex = ['Maximiliano Diel', 'Javo'];
  base.guardarContacto(d, { chat: '59891000001', telefono: '59891000001', agendado: 'Maximiliano Diel', perfil: 'Maxi Diel' });
  base.guardarContacto(d, { chat: '59891000002', telefono: '59891000002', agendado: 'Javo', perfil: 'Javier' });
  const lid = (id, chat, perfil) => ({ id, chat, telefono: null, de_juan: false, texto: 'hola', ts: 1000, perfil });
  assert.equal(base.guardarMensaje(d, ex, lid('1', 'lid:111', 'Maxi Diel')), false);
  assert.equal(base.guardarMensaje(d, ex, lid('2', 'lid:222', 'javier')), false);
  assert.equal(base.guardarMensaje(d, ex, lid('3', 'lid:333', 'Javier Popich')), true);
  assert.equal(base.guardarMensaje(d, ex, lid('4', 'lid:444', 'Trouville')), true);
  base.unirLid(d, '444', '59899444444');
  base.guardarContacto(d, { chat: '59899444444', telefono: '59899444444', agendado: 'Trouville pizzería' });
  const filas = d.prepare('SELECT id, chat FROM mensajes ORDER BY id').all().map((f) => [f.id, f.chat]);
  assert.deepEqual(filas, [['3', 'lid:333'], ['4', '59899444444']]);
  base.unirLid(d, '333', '59891000002');
  assert.equal(base.purgarExcluidos(d, ex), 1);
  assert.deepEqual(d.prepare('SELECT id FROM mensajes').all().map((f) => f.id), ['4']);
});
