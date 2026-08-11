'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { cargar } = require('../src/config');

const MINIMO = { WA_API_KEY: 'una-clave-bien-larga-1234' };

test('exige WA_API_KEY y falla con un mensaje util', () => {
  assert.throws(() => cargar({}), /WA_API_KEY/);
  assert.throws(() => cargar({ WA_API_KEY: 'corta' }), /al menos 16/);
});

test('TYPING_ENABLED=false apaga el typing de verdad', () => {
  // z.coerce.boolean() hacia Boolean("false") === true: cualquier valor
  // encendia el typing y los envios quedaban lentos sin explicacion.
  assert.equal(cargar({ ...MINIMO, TYPING_ENABLED: 'false' }).TYPING_ENABLED, false);
  assert.equal(cargar({ ...MINIMO, TYPING_ENABLED: '0' }).TYPING_ENABLED, false);
  assert.equal(cargar({ ...MINIMO, TYPING_ENABLED: 'no' }).TYPING_ENABLED, false);
  assert.equal(cargar({ ...MINIMO, TYPING_ENABLED: 'true' }).TYPING_ENABLED, true);
  assert.equal(cargar({ ...MINIMO }).TYPING_ENABLED, true, 'por defecto va encendido');
});

test('parsea AM_PHONES a una lista limpia', () => {
  assert.deepEqual(cargar({ ...MINIMO, AM_PHONES: '' }).amPhones, []);
  assert.deepEqual(
    cargar({ ...MINIMO, AM_PHONES: '59899000111, 59899000222 ,' }).amPhones,
    ['59899000111', '59899000222']
  );
});

test('rechaza rangos de delay invertidos', () => {
  assert.throws(
    () => cargar({ ...MINIMO, DELAY_WELCOME_MIN_MS: '9000', DELAY_WELCOME_MAX_MS: '1000' }),
    /DELAY_WELCOME_MIN_MS/
  );
});

test('rechaza un proveedor desconocido', () => {
  assert.throws(() => cargar({ ...MINIMO, WA_PROVIDER: 'twilio' }), /WA_PROVIDER/);
});
