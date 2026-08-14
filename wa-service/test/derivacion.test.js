'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { conLead, stubOpenAI } = require('./helpers');
const { detectar } = require('../src/funnel/derivacion');
const { S } = require('../src/funnel/states');

const AM = '59899000111';
const LEAD_TEL = '59899123456';

async function lead(s, texto) {
  await s.servicioLeads.registrarRespuesta(LEAD_TEL, texto);
  await s.cola.vacia();
  return s.proveedor.getEnviados().filter((e) => e.to === LEAD_TEL).map((e) => e.texto);
}
const estado = (s) => s.repo.leadPorTelefono(LEAD_TEL).fsm_state;
const alAM = (s) => s.proveedor.getEnviados().filter((e) => e.to === AM);

// ── deteccion ────────────────────────────────────────────────────────────────

test('reconoce consultas de precio en las formas que se usan', () => {
  for (const t of ['¿cuánto sale?', 'cuanto cuesta una web', 'qué precio tiene',
                   'me pasás una cotización?', 'cuánto me sale más o menos']) {
    assert.equal(detectar(t)?.motivo, 'precio', t);
  }
});

test('reconoce quejas y enojo', () => {
  for (const t of ['esto es una estafa', 'quiero hacer un reclamo',
                   'estoy enojado con el servicio', 'el proyecto se atrasó dos semanas',
                   'un servicio pésimo']) {
    assert.equal(detectar(t)?.motivo, 'queja', t);
  }
});

test('reconoce consultas de facturacion', () => {
  for (const t of ['no me llegó la factura', '¿cómo se paga?',
                   'les paso el RUT', 'se puede en cuotas?']) {
    assert.equal(detectar(t)?.motivo, 'facturacion', t);
  }
});

test('una queja sobre el precio se trata como queja, no como consulta', () => {
  // Alguien indignado por lo que le cobraron no quiere el criterio de precios.
  assert.equal(detectar('me parece una estafa lo que cobran de precio').motivo, 'queja');
});

test('no dispara con mensajes normales del embudo', () => {
  for (const t of ['1', 'Inmobiliaria Pereyra', 'azul y blanco', '@juanito',
                   'necesito un sistema de stock', 'hola', '']) {
    assert.equal(detectar(t), null, t);
  }
});

// ── comportamiento ───────────────────────────────────────────────────────────

test('al primer "cuánto sale" contesta el criterio, sin dar numeros', async () => {
  const s = await conLead();
  await lead(s, 'hola');
  const msgs = await lead(s, '¿cuánto sale una página web?');

  assert.equal(msgs.at(-1), '[precio]');
  assert.ok(!/\d{3}/.test(msgs.at(-1)), 'no menciona ninguna cifra');
  assert.notEqual(estado(s), S.HUMAN_QUEUED, 'todavia no deriva');
  assert.equal(s.repo.leadPorTelefono(LEAD_TEL).consultas_precio, 1);
});

test('si vuelve a preguntar el precio, pasa a un humano', async () => {
  const s = await conLead();
  await lead(s, 'hola');
  await lead(s, '¿cuánto sale?');
  s.proveedor.limpiar();

  const msgs = await lead(s, 'dale pero decime un precio aproximado');

  assert.equal(estado(s), S.HUMAN_QUEUED);
  assert.equal(msgs.at(-1), '[derivacion]');
  assert.equal(s.repo.leadPorTelefono(LEAD_TEL).motivo_derivacion, 'precio');
});

test('una queja deriva en el acto, sin explicar nada', async () => {
  const s = await conLead();
  await lead(s, 'hola');
  s.proveedor.limpiar();

  const msgs = await lead(s, 'estoy enojado, el proyecto se atrasó dos semanas');

  assert.equal(estado(s), S.HUMAN_QUEUED);
  assert.equal(msgs.at(-1), '[derivacion]');
  // El superprompt lo dice explicito: el bot no explica ni promete fechas. Eso
  // ahora vive en el objetivo de la situacion 'derivacion' y se mide en evals/.
});

test('una consulta de facturacion deriva en el acto', async () => {
  const s = await conLead();
  await lead(s, 'hola');
  s.proveedor.limpiar();

  await lead(s, 'no me llegó la factura del mes');

  assert.equal(estado(s), S.HUMAN_QUEUED);
  assert.equal(s.repo.leadPorTelefono(LEAD_TEL).motivo_derivacion, 'facturacion');
});

test('al humano le llega el motivo y el historial reciente', async () => {
  const s = await conLead();
  await lead(s, 'hola');
  s.proveedor.limpiar();

  await lead(s, 'esto es una estafa');

  const aviso = alAM(s).find((e) => /Te pasan una conversación/.test(e.texto));
  assert.ok(aviso, 'le llega el aviso de derivacion');
  assert.match(aviso.texto, /Motivo: queja o reclamo/);
  assert.match(aviso.texto, /Martín Pereyra/);
  assert.match(aviso.texto, /Últimos mensajes/);
  assert.match(aviso.texto, /estafa/, 'incluye lo que dijo');
  assert.match(aviso.texto, /wa\.me\/59899123456/);
});

test('derivado, el bot se calla hasta que el CRM lo devuelva', async () => {
  const s = await conLead();
  await lead(s, 'hola');
  await lead(s, 'quiero hacer un reclamo');
  s.proveedor.limpiar();

  await lead(s, '¿hay alguien?');
  await lead(s, 'dale');
  assert.equal(s.proveedor.getEnviados().length, 0, 'con una persona a cargo el bot no se mete');
});

test('el motivo tambien queda cuando pide un humano o no entiende', async () => {
  const s = await conLead();
  await lead(s, 'hola');
  await lead(s, 'quiero hablar con una persona');
  assert.equal(s.repo.leadPorTelefono(LEAD_TEL).motivo_derivacion, 'pedido');

  // El motivo 'invalidos' se fue con el embudo numerado: sin opciones que
  // entender mal, no hay respuesta invalida. Ahora el equivalente es que la IA
  // no pueda contestar, y ese motivo es 'sin_ia'.
  const s2 = await conLead({ openai: stubOpenAI({ falla: '500' }) });
  await lead(s2, 'hola');
  assert.equal(s2.repo.leadPorTelefono(LEAD_TEL).motivo_derivacion, 'sin_ia');
});

test('la conversacion normal no se deriva por error', async () => {
  const s = await conLead();
  for (const t of ['hola', 'tengo una inmobiliaria', 'somos 6', 'necesito ordenar las consultas']) {
    await lead(s, t);
  }
  assert.equal(estado(s), S.CONVERSANDO, 'sigue conversando, no se deriva');
});

test('"lista de precios" es un requerimiento, no una consulta comercial', () => {
  // Sin esto, un lead de comercio describiendo lo que necesita terminaba
  // derivado a un humano por "precios".
  for (const t of ['necesito una tienda con lista de precios',
                   'quiero poder actualizar precios desde el celular',
                   'un catálogo de precios online']) {
    assert.equal(detectar(t), null, t);
  }
  // Pero preguntar el precio sigue disparando.
  assert.equal(detectar('decime un precio aproximado').motivo, 'precio');
});
