'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { crearNotificadorCRM } = require('../src/crm-notify');
const { conLead } = require('./helpers');

const CFG = { CRM_API_URL: 'https://crm.test/', CRM_ADMIN_TOKEN: 'token-del-crm' };

/** Reemplaza fetch mientras corre `fn` y devuelve lo que se pidio. */
async function conFetch(respuesta, fn) {
  const original = global.fetch;
  const pedidos = [];
  global.fetch = async (url, opciones) => {
    pedidos.push({ url, opciones, body: opciones?.body ? JSON.parse(opciones.body) : null });
    if (respuesta instanceof Error) throw respuesta;
    return respuesta;
  };
  try {
    await fn(pedidos);
  } finally {
    global.fetch = original;
  }
  return pedidos;
}

test('reporta el entrante al CRM, con el token y siempre como entrante', async () => {
  const n = crearNotificadorCRM({ cfg: CFG, repo: null, logger: null });

  const pedidos = await conFetch({ ok: true, status: 200 }, async () => {
    assert.equal(await n.mensajeEntrante({ telefono: '59899123456', nombre: 'Ana', texto: 'hola' }), true);
  });

  assert.equal(pedidos.length, 1);
  assert.equal(pedidos[0].url, 'https://crm.test/api/bot/mensaje-entrante', 'sin doble barra');
  assert.equal(pedidos[0].opciones.headers['x-admin-token'], 'token-del-crm');
  assert.deepEqual(pedidos[0].body, { phone: '59899123456', name: 'Ana', text: 'hola', direction: 'in' });
});

test('el texto viaja recortado', async () => {
  const n = crearNotificadorCRM({ cfg: CFG, repo: null, logger: null });
  const pedidos = await conFetch({ ok: true }, () => n.mensajeEntrante({ telefono: '598', texto: 'x'.repeat(900) }));
  assert.equal(pedidos[0].body.text.length, 500);
});

/**
 * Que el CRM este caido no puede cortar la conversacion con el lead, que es lo
 * unico que no se recupera despues.
 */
test('nunca tira: ni con el CRM caido ni con un rechazo', async () => {
  const n = crearNotificadorCRM({ cfg: CFG, repo: null, logger: null });

  await conFetch(new Error('ECONNREFUSED'), async () => {
    assert.equal(await n.mensajeEntrante({ telefono: '598', texto: 'hola' }), false);
  });
  await conFetch({ ok: false, status: 500 }, async () => {
    assert.equal(await n.mensajeEntrante({ telefono: '598', texto: 'hola' }), false);
  });
});

test('sin CRM configurado, o sin telefono, no pide nada', async () => {
  const sinCrm = crearNotificadorCRM({ cfg: { CRM_API_URL: '', CRM_ADMIN_TOKEN: '' }, repo: null, logger: null });
  const conCrm = crearNotificadorCRM({ cfg: CFG, repo: null, logger: null });

  const pedidos = await conFetch({ ok: true }, async () => {
    assert.equal(await sinCrm.mensajeEntrante({ telefono: '598', texto: 'hola' }), false);
    assert.equal(await conCrm.mensajeEntrante({ telefono: '', texto: 'hola' }), false);
  });
  assert.equal(pedidos.length, 0);
});

// ── cableado: quien lo llama ─────────────────────────────────────────────────

test('cada mensaje del lead, con texto o con una foto, se reporta', async () => {
  const pedidos = await conFetch({ ok: true, status: 200, json: async () => ({}) }, async () => {
    const s = await conLead(CFG);

    await s.servicioLeads.registrarRespuesta('59899123456', 'quiero una web');
    await s.proveedor.simularSinTexto({
      from: '59899123456', tipo: 'imagen', id: 'wamid.crm1', descargar: async () => Buffer.from('jpg'),
    });
    await s.cola.vacia();
  });

  const entrantes = pedidos.filter((p) => p.url.endsWith('/api/bot/mensaje-entrante')).map((p) => p.body.text);
  assert.ok(entrantes.includes('quiero una web'));
  assert.ok(entrantes.includes('(mandó una foto)'));
});
