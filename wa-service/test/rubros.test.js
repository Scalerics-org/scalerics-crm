'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { clasificar } = require('../src/templates/rubros');

test('mapea el nombre exacto del rubro', () => {
  assert.equal(clasificar('inmobiliaria'), 'inmobiliaria');
  assert.equal(clasificar('gastronomia'), 'gastronomia');
});

test('tolera mayusculas y acentos', () => {
  assert.equal(clasificar('Gastronomía'), 'gastronomia');
  assert.equal(clasificar('ODONTOLOGÍA'), 'salud');
  assert.equal(clasificar('Educación'), 'educacion');
});

test('mapea sinonimos', () => {
  assert.equal(clasificar('bienes raices'), 'inmobiliaria');
  assert.equal(clasificar('consultorio'), 'salud');
  assert.equal(clasificar('concesionaria'), 'automotriz');
  assert.equal(clasificar('escribania'), 'servicios_profesionales');
});

test('tolera plurales', () => {
  assert.equal(clasificar('propiedades'), 'inmobiliaria');
  assert.equal(clasificar('restaurantes'), 'gastronomia');
  assert.equal(clasificar('taller'), 'automotriz');
  assert.equal(clasificar('talleres'), 'automotriz');
});

test('encuentra el rubro dentro de una frase', () => {
  assert.equal(clasificar('tengo una pizzeria en el centro'), 'gastronomia');
  assert.equal(clasificar('estudio contable'), 'servicios_profesionales');
});

test('"venta de autos" es automotriz, no comercio', () => {
  // "venta" era sinonimo de retail y le ganaba a "autos" por orden de declaracion.
  assert.equal(clasificar('venta de autos usados'), 'automotriz');
  assert.equal(clasificar('compraventa de autos'), 'automotriz');
});

test('gana el rubro con mas coincidencias', () => {
  assert.equal(clasificar('taller mecanico de motos'), 'automotriz');
});

test('cae en generico cuando no reconoce nada', () => {
  assert.equal(clasificar(''), 'generico');
  assert.equal(clasificar(null), 'generico');
  assert.equal(clasificar('torneria industrial'), 'generico');
  assert.equal(clasificar('   '), 'generico');
});
