'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { quitarPreambulo } = require('../src/ia/preambulo');
const { crearRedactor } = require('../src/ia/redactor');

/**
 * Lead 18, 30-9 03:02: el modelo contesto como si hablara con un operador y el
 * bot le mando al lead "Entendido. El mensaje que le mandás a Silvana es:".
 */
const CUERPO = 'Para optimizar esas ropas y accesorios en línea, en 30 minutos hacemos un prototipo.\n\n¿Qué día te queda mejor?\n1. Jueves 1';

test('saca la linea meta del caso real y deja el mensaje', () => {
  const r = quitarPreambulo(`Entendido. El mensaje que le mandás a Silvana es:\n\n${CUERPO}`);
  assert.equal(r.texto, CUERPO);
  assert.equal(r.sacado, 'Entendido. El mensaje que le mandás a Silvana es:');
});

test('cubre las formas conocidas, con y sin palabra inicial', () => {
  for (const linea of [
    'Acá va el mensaje:', 'Aquí está el mensaje:', 'Perfecto, el mensaje sería:',
    'Te dejo el mensaje:', 'Mensaje:', 'Listo, acá va el texto:', 'Claro. Esta es la respuesta:',
    'Dale, el mensaje para Juan:', 'OK: el mensaje es:', 'ENTENDIDO. EL MENSAJE ES:',
  ]) {
    const r = quitarPreambulo(`${linea}\n${CUERPO}`);
    assert.equal(r.texto, CUERPO, linea);
    assert.equal(r.sacado, linea, linea);
  }
});

test('un mensaje normal con ":" en la primera linea NO se toca', () => {
  for (const t of [
    'Horarios del jueves:\n1. 12:00\n2. 12:30',
    'Te cuento algo:\nhacemos páginas web.',
    '¿Qué día te queda mejor?\n1. Jueves 1',
    'Hola Juan, te escribo por tu consulta: armamos una web.',
    'Dale, contame.\nMensaje recibido:',
  ]) {
    const r = quitarPreambulo(t);
    assert.equal(r.texto, t);
    assert.equal(r.sacado, null);
  }
});

test('solo la PRIMERA linea: una meta en el medio no se toca', () => {
  const t = `Hola.\nEl mensaje es:\n${CUERPO}`;
  assert.equal(quitarPreambulo(t).texto, t);
});

test('una primera linea larga, aunque hable del mensaje y termine en ":", no se toca', () => {
  const larga = `Te cuento que el mensaje que nos dejaste por el formulario lo leímos con atención y queremos entender mejor qué necesitás para tu negocio:`;
  assert.ok(larga.length > 120);
  assert.equal(quitarPreambulo(`${larga}\nalgo`).sacado, null);
});

test('si despues de sacarla no queda nada, vuelve vacio', () => {
  assert.equal(quitarPreambulo('Acá va el mensaje:').texto, '');
  assert.equal(quitarPreambulo('Acá va el mensaje:\n\n  ').texto, '');
});

test('entradas raras no rompen', () => {
  for (const t of ['', null, undefined, '   ']) assert.equal(quitarPreambulo(t).sacado, null);
});

test('el redactor saca el preambulo y lo loguea', async () => {
  const logs = [];
  const modelo = { activo: true, async pedir() { return { texto: `Entendido. El mensaje que le mandás a Silvana es:\n\n${CUERPO}` }; } };
  const redactor = crearRedactor({ modelo, logger: { warn: (...a) => logs.push(a), info() {}, error() {} } });
  const texto = await redactor.escribir({ id: 18, nombre: 'Adán' }, 'oferta_con_horarios');
  assert.ok(!/Entendido|El mensaje que/.test(texto), texto);
  assert.match(texto, /^Para optimizar/);
  assert.ok(logs.some((l) => /preambulo/i.test(l[1])), 'se loguea');
});

test('el redactor devuelve null si el modelo solo escribio el preambulo', async () => {
  const modelo = { activo: true, async pedir() { return { texto: 'Acá va el mensaje:' }; } };
  const texto = await crearRedactor({ modelo }).escribir({ id: 1 }, 'oferta_con_horarios');
  assert.equal(texto, null);
});

// ── el otro camino de salida: la conversacion (agente.js) ───────────────────

const { conLead, stubModelo } = require('./helpers');

test('la conversacion tambien saca el preambulo antes de mandarlo al lead', async () => {
  const meta = `Entendido. El mensaje que le mandás a Silvana es:\n\nContame, ¿a qué se dedica tu negocio?`;
  const s = await conLead({ modelo: stubModelo({ respuestas: { conversacion: meta } }) });
  s.proveedor.limpiar();
  await s.servicioLeads.registrarRespuesta('59899123456', 'hola');
  await s.cola.vacia();
  const msgs = s.proveedor.getEnviados().map((e) => e.texto).join('\n');
  assert.ok(!/Entendido|El mensaje que/.test(msgs), msgs);
  assert.match(msgs, /Contame, ¿a qué se dedica tu negocio\?/);
});

test('la conversacion no toca un mensaje normal con ":" en la primera linea', async () => {
  const normal = 'Horarios del jueves:\n1. 12:00';
  const s = await conLead({ modelo: stubModelo({ respuestas: { conversacion: normal } }) });
  s.proveedor.limpiar();
  await s.servicioLeads.registrarRespuesta('59899123456', 'hola');
  await s.cola.vacia();
  assert.ok(s.proveedor.getEnviados().some((e) => e.texto.includes('Horarios del jueves:')));
});
