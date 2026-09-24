'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { normalizar, primerNombre } = require('../src/telefono');

test('agrega el codigo de pais a un celular uruguayo', () => {
  assert.equal(normalizar('099123456'), '59899123456');
  assert.equal(normalizar('99123456'), '59899123456');
});

test('ignora espacios, guiones y parentesis', () => {
  assert.equal(normalizar('+598 99 123 456'), '59899123456');
  assert.equal(normalizar('(099) 123-456'), '59899123456');
});

test('respeta un numero que ya viene en internacional', () => {
  assert.equal(normalizar('+5491133334444'), '5491133334444');
  assert.equal(normalizar('005491133334444'), '5491133334444');
  assert.equal(normalizar('59899123456'), '59899123456');
});

test('acepta otro codigo de pais por defecto', () => {
  assert.equal(normalizar('1133334444', '54'), '541133334444');
});

test('devuelve null cuando no se puede interpretar', () => {
  assert.equal(normalizar(''), null);
  assert.equal(normalizar('sin telefono'), null);
  assert.equal(normalizar('123'), null);
  assert.equal(normalizar(null), null);
  assert.equal(normalizar(undefined), null);
});

test('primerNombre toma el primer token capitalizado', () => {
  assert.equal(primerNombre('martin pereyra'), 'Martin');
  assert.equal(primerNombre('  ANA maria  '), 'Ana');
  assert.equal(primerNombre(''), '');
  assert.equal(primerNombre(null), '');
});
