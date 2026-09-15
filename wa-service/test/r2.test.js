'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { crearR2 } = require('../src/r2');

const CFG = {
  R2_ACCOUNT_ID: 'abc123',
  R2_BUCKET: 'scalerics-backups',
  R2_ACCESS_KEY_ID: 'AKIAEJEMPLO',
  R2_SECRET_ACCESS_KEY: 'secretodeejemplo',
  R2_PREFIX: 'wa-service',
};

/** Guarda el pedido en vez de salir a internet. */
function fetchFalso(respuesta = { ok: true, status: 200 }) {
  const pedidos = [];
  const fn = async (url, opciones) => {
    pedidos.push({ url, ...opciones });
    return { ...respuesta, text: async () => respuesta.cuerpo || '' };
  };
  fn.pedidos = pedidos;
  return fn;
}

test('sin credenciales queda apagado y no intenta nada', async () => {
  const f = fetchFalso();
  const r2 = crearR2({ cfg: { ...CFG, R2_ACCESS_KEY_ID: '' }, fetch: f });

  assert.equal(r2.activo, false);
  assert.equal((await r2.subir('wa.db', Buffer.from('x'))).ok, false);
  assert.equal(f.pedidos.length, 0, 'ni sale a la red');
});

test('sube al bucket y a la ruta que corresponde', async () => {
  const f = fetchFalso();
  const r2 = crearR2({ cfg: CFG, fetch: f });

  const r = await r2.subir('wa-2026-08-27.db', Buffer.from('datos'));
  assert.equal(r.ok, true);
  assert.equal(f.pedidos.length, 1);

  const p = f.pedidos[0];
  assert.equal(p.method, 'PUT');
  assert.equal(p.url, 'https://abc123.r2.cloudflarestorage.com/scalerics-backups/wa-service/wa-2026-08-27.db');
});

/**
 * Sin la firma, R2 devuelve 403 y el respaldo no llega. Se verifica la forma
 * —que estan las tres partes y los encabezados firmados— no el valor, que
 * depende de la hora.
 */
test('va firmado con SigV4', async () => {
  const f = fetchFalso();
  await crearR2({ cfg: CFG, fetch: f }).subir('wa.db', Buffer.from('datos'));

  const h = f.pedidos[0].headers;
  assert.match(h.Authorization, /^AWS4-HMAC-SHA256 Credential=AKIAEJEMPLO\/\d{8}\/auto\/s3\/aws4_request/);
  assert.match(h.Authorization, /SignedHeaders=host;x-amz-content-sha256;x-amz-date/);
  assert.match(h.Authorization, /Signature=[0-9a-f]{64}$/);
  assert.match(h['x-amz-date'], /^\d{8}T\d{6}Z$/);
  assert.match(h['x-amz-content-sha256'], /^[0-9a-f]{64}$/);
});

test('el hash es el del contenido, no uno fijo', async () => {
  const f = fetchFalso();
  const r2 = crearR2({ cfg: CFG, fetch: f });

  await r2.subir('a.db', Buffer.from('uno'));
  await r2.subir('b.db', Buffer.from('otro distinto'));

  const [a, b] = f.pedidos.map((p) => p.headers['x-amz-content-sha256']);
  assert.notEqual(a, b, 'si fuera fijo, R2 rechazaria el segundo');
});

/**
 * El cuerpo del error de S3 dice cual credencial esta mal. Sin eso, depurar
 * esto es adivinar entre cuatro variables.
 */
test('si R2 rechaza, se queda el motivo', async () => {
  const f = fetchFalso({ ok: false, status: 403, cuerpo: '<Error><Code>SignatureDoesNotMatch</Code></Error>' });
  let loggeado = '';
  const r2 = crearR2({ cfg: CFG, fetch: f, logger: { error: (o) => { loggeado = JSON.stringify(o); }, info: () => {} } });

  const r = await r2.subir('wa.db', Buffer.from('x'));
  assert.equal(r.ok, false);
  assert.equal(r.estado, 403);
  assert.match(r.error, /SignatureDoesNotMatch/);
  assert.match(loggeado, /SignatureDoesNotMatch/);
});

test('si se cae la red, devuelve el error sin tirar', async () => {
  const f = async () => { throw new Error('ENOTFOUND'); };
  const r = await crearR2({ cfg: CFG, fetch: f }).subir('wa.db', Buffer.from('x'));

  assert.equal(r.ok, false);
  assert.match(r.error, /ENOTFOUND/);
});

test('sin prefijo sube a la raíz del bucket', async () => {
  const f = fetchFalso();
  await crearR2({ cfg: { ...CFG, R2_PREFIX: '' }, fetch: f }).subir('wa.db', Buffer.from('x'));

  assert.equal(f.pedidos[0].url, 'https://abc123.r2.cloudflarestorage.com/scalerics-backups/wa.db');
});
