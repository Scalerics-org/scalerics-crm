'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { corregir } = require('../src/ia/voseo');
const { conLead, stubModelo } = require('./helpers');

const TEL = '59899123456';

/**
 * El prompt prohibe el tuteo con todas las letras y hasta lista los verbos uno
 * por uno. Aun asi salio "¿Qué necesitas?" en la primera conversacion de
 * prueba. Es la misma leccion que el tamaño del equipo y las fechas: pedirle al
 * modelo una conversion mecanica falla aunque se la expliques con ejemplos.
 */
test('corrige el tuteo que se le escapa al modelo', () => {
  assert.equal(corregir('¿Qué necesitas?').texto, '¿Qué necesitás?');
  assert.equal(corregir('¿A qué te dedicas?').texto, '¿A qué te dedicás?');
  assert.equal(corregir('Si tienes dudas, dime').texto, 'Si tenés dudas, decime');
  assert.equal(corregir('¿Cómo te llamas?').texto, '¿Cómo te llamás?');
  assert.equal(corregir('¿Puedes el martes?').texto, '¿Podés el martes?');
});

/**
 * El \\b de JavaScript mira solo [A-Za-z0-9_], asi que una palabra acentuada no
 * tiene borde donde va el acento. Con \\b, "tú" nunca coincidia y quedaba sin
 * corregir justo el pronombre que mas delata.
 */
test('tambien las palabras con acento', () => {
  assert.equal(corregir('¿Tú qué preferís?').texto, '¿Vos qué preferís?');
  assert.equal(corregir('Contame tú').texto, 'Contame vos');
});

test('respeta la mayúscula de la palabra original', () => {
  assert.equal(corregir('Tienes razón').texto, 'Tenés razón');
  assert.equal(corregir('tienes razón').texto, 'tenés razón');
});

/**
 * La lista es corta a proposito: solo formas donde el tuteo no puede ser otra
 * cosa. Corregir de mas rompe frases validas, y eso es peor que un "tienes".
 */
test('no toca palabras que solo se parecen', () => {
  assert.equal(corregir('Las esperas son largas').texto, 'Las esperas son largas');
  assert.equal(corregir('Tenemos varias cuentas').texto, 'Tenemos varias cuentas');
  assert.equal(corregir('Mandanos tus preguntas').texto, 'Mandanos tus preguntas');
  assert.equal(corregir('Mantenimiento y sostenes').texto, 'Mantenimiento y sostenes');
});

test('lo que ya está en voseo queda igual', () => {
  const bien = 'Contame de tu negocio, ¿qué necesitás? Si querés, podés escribirme.';
  const r = corregir(bien);
  assert.equal(r.texto, bien);
  assert.deepEqual(r.corregidos, []);
});

test('dice qué corrigió, para poder ver si el modelo se va seguido', () => {
  const r = corregir('Si tienes tiempo, dime qué necesitas');
  assert.deepEqual(r.corregidos.sort(), ['dime', 'necesitas', 'tienes']);
});

// ── enchufado a la conversación ──────────────────────────────────────────────

test('lo que le llega al lead sale en voseo aunque el modelo tutee', async () => {
  const s = await conLead({
    modelo: stubModelo({ respuestas: { conversacion: '¿Qué necesitas? Si tienes dudas, dime.' } }),
  });
  await s.servicioLeads.registrarRespuesta(TEL, 'hola');
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === TEL).map((e) => e.texto);
  const conTuteo = alLead.filter((t) => /necesitas|tienes|dime/.test(t));
  assert.equal(conTuteo.length, 0, `salió con tuteo: ${conTuteo.join(' | ')}`);
});
