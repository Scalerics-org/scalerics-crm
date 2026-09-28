'use strict';

// El grupo de captación en la calle (src/captacion.js): la IA saca los locales
// del mensaje, se cargan en el CRM y el bot contesta en el grupo lo que cargó.

const test = require('node:test');
const assert = require('node:assert');

const { crearCaptacion, resumen, fechaCorta } = require('../src/captacion');
const { crear: crearMock } = require('../src/providers/mock');
const { textoDeMensaje } = require('../src/providers/baileys');

const GRUPO = '120363000000000001@g.us';
const CFG = { GRUPO_CAPTACION_JID: GRUPO, CRM_API_URL: 'https://crm.test/', CRM_ADMIN_TOKEN: 'token-del-crm' };

function modeloQueDevuelve(argumentos) {
  const llamadas = [];
  return { activo: true, llamadas, async pedir(a) { llamadas.push(a); return { texto: null, argumentos }; } };
}

async function conFetch(responder, fn) {
  const original = global.fetch;
  const pedidos = [];
  global.fetch = async (url, opciones) => {
    const body = JSON.parse(opciones.body);
    pedidos.push({ url, opciones, body });
    const { status, datos } = responder(body);
    return { ok: status < 400, status, json: async () => datos };
  };
  try { await fn(pedidos); } finally { global.fetch = original; }
  return pedidos;
}

async function armar(argumentos, cfg = CFG) {
  const proveedor = crearMock();
  await proveedor.conectar();
  const vistos = new Set();
  const repo = { entranteEsNuevo: (id) => (vistos.has(id) ? false : (vistos.add(id), true)) };
  const modelo = modeloQueDevuelve(argumentos);
  const captacion = crearCaptacion({ cfg, modelo, proveedor, repo, ahora: () => new Date(2026, 8, 28, 12, 0) });
  return { proveedor, modelo, captacion };
}

const CREADO = {
  status: 201,
  datos: {
    ok: true, nuevo: true, resultado: 'Interesado',
    prospecto: { nombre: 'La Pizzería de Juan', barrio: 'Pocitos', estado: 'contactado', contacto: 'Martín',
      contacto_tel: '099 123 456', proxima_llamada: '2026-10-01 16:00' },
  },
};

test('carga la visita en el CRM y contesta en el grupo lo que cargó', async () => {
  const { proveedor, modelo, captacion } = await armar({
    es_avance: true,
    visitas: [{ nombre: 'La Pizzería de Juan', barrio: 'Pocitos', contacto: 'Martín', contacto_tel: '099 123 456',
      resultado: 'interesado', proxima: '2026-10-01T16:00', telefono: '' }],
  });
  const pedidos = await conFetch(() => CREADO, () => captacion.recibir({
    grupo: GRUPO, id: 'm1', nombre: 'Gonzalo',
    texto: 'Pasé por La Pizzería de Juan en Pocitos, el dueño Martín 099 123 456, le interesó, llamar el jueves a la tarde',
  }));
  assert.equal(pedidos.length, 1);
  assert.equal(pedidos[0].url, 'https://crm.test/api/fidelidad/visitas');
  assert.equal(pedidos[0].opciones.headers['x-admin-token'], 'token-del-crm');
  assert.equal(pedidos[0].body.autor, 'Gonzalo');
  assert.equal(pedidos[0].body.fuente, 'whatsapp');
  assert.equal('telefono' in pedidos[0].body, false, 'los campos vacíos no se mandan');
  // El modelo sabe qué día es, para entender "el jueves".
  assert.match(modelo.llamadas[0].system, /2026/);
  const [r] = proveedor.getEnviados();
  assert.equal(r.to, GRUPO);
  assert.match(r.texto, /✓ Cargado en el CRM/);
  assert.match(r.texto, /\*La Pizzería de Juan\* · Pocitos/);
  assert.match(r.texto, /Dueño: Martín · 099 123 456/);
  assert.match(r.texto, /Interesado · llamar jue 1\/10 16:00/);
});

test('lo que no es un avance no se carga ni se contesta', async () => {
  const { proveedor, captacion } = await armar({ es_avance: false, visitas: [] });
  const pedidos = await conFetch(() => CREADO, () => captacion.recibir({ grupo: GRUPO, id: 'm2', texto: 'Hoy almorzamos 🍕' }));
  assert.equal(pedidos.length, 0);
  assert.equal(proveedor.getEnviados().length, 0);
});

test('solo escucha su grupo, y no procesa dos veces el mismo mensaje', async () => {
  const { modelo, captacion } = await armar({ es_avance: true, visitas: [{ nombre: 'X', resultado: 'visitado' }] });
  await conFetch(() => CREADO, async () => {
    await captacion.recibir({ grupo: 'otro@g.us', id: 'a', texto: 'Pasé por X' });
    await captacion.recibir({ grupo: GRUPO, id: 'b', texto: 'Pasé por X' });
    await captacion.recibir({ grupo: GRUPO, id: 'b', texto: 'Pasé por X' });
  });
  assert.equal(modelo.llamadas.length, 1);
});

test('sin grupo configurado no hace nada', async () => {
  const { modelo, captacion } = await armar({ es_avance: true, visitas: [] }, { ...CFG, GRUPO_CAPTACION_JID: '' });
  assert.equal(captacion.activo, false);
  await captacion.recibir({ grupo: GRUPO, id: 'c', texto: 'Pasé por X' });
  assert.equal(modelo.llamadas.length, 0);
});

test('si el CRM rechaza uno, lo dice y carga los demás', async () => {
  const { proveedor, captacion } = await armar({
    es_avance: true,
    visitas: [{ nombre: 'Uno', resultado: 'visitado' }, { nombre: 'Dos', resultado: 'no_interesa' }],
  });
  await conFetch((b) => (b.nombre === 'Uno'
    ? { status: 400, datos: { ok: false, error: 'la fecha no puede quedar en el pasado' } }
    : { status: 201, datos: { ok: true, nuevo: false, resultado: 'No le interesa',
      prospecto: { nombre: 'Dos', barrio: 'Cordón', estado: 'descartado' } } }),
  () => captacion.recibir({ grupo: GRUPO, id: 'd', texto: 'Uno y Dos' }));
  const [r] = proveedor.getEnviados();
  assert.match(r.texto, /No pude cargar \*Uno\*: la fecha no puede quedar en el pasado/);
  assert.match(r.texto, /\*Dos\* · Cordón \(ya estaba: actualizado\)/);
  assert.match(r.texto, /No le interesa · vuelve en un año/);
});

test('fechas cortas y resumen de una reunión', () => {
  assert.equal(fechaCorta('2026-10-01 16:00'), 'jue 1/10 16:00');
  assert.equal(fechaCorta(null), '');
  assert.match(resumen({ nuevo: true, resultado: 'Reunión',
    prospecto: { nombre: 'Z', estado: 'reunion_agendada', fecha_reunion: '2026-09-30 11:00' } }), /Reunión · reunión mié 30\/9 11:00/);
});

test('el mock y baileys le pasan los grupos a quien escucha', async () => {
  const proveedor = crearMock();
  const recibidos = [];
  proveedor.alRecibirGrupo((m) => recibidos.push(m));
  proveedor.simularGrupo({ grupo: GRUPO, texto: 'hola', id: 'e' });
  assert.equal(recibidos.length, 1);
  assert.equal(textoDeMensaje({ message: { conversation: 'Pasé por X' } }), 'Pasé por X');
});
