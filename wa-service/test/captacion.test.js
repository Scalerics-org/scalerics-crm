'use strict';

// El grupo de captación en la calle (src/captacion.js): la IA saca los locales
// del mensaje, se cargan en el CRM y el bot contesta en el grupo lo que cargó.

const test = require('node:test');
const assert = require('node:assert');

const { crearCaptacion, resumen, fechaCorta } = require('../src/captacion');
const { crear: crearMock } = require('../src/providers/mock');
const { textoDeMensaje, esParaElBot } = require('../src/providers/baileys');
const { AYUDA } = require('../src/captacion-pedidos');

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
    const body = opciones.body ? JSON.parse(opciones.body) : null;
    pedidos.push({ url, opciones, body });
    const { status, datos } = responder(body, url, opciones.method);
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

test('sin arrobar al bot no se llama a la IA ni se carga nada', async () => {
  const { proveedor, modelo, captacion } = await armar({ tipo: 'avance', visitas: [{ nombre: 'La Pizzería de Juan', resultado: 'interesado' }] });
  const pedidos = await conFetch(() => CREADO, () => captacion.recibir({ grupo: GRUPO, id: 's1', nombre: 'Gonzalo', texto: 'Pasé por La Pizzería de Juan, le interesó' }));
  assert.equal(modelo.llamadas.length, 0);
  assert.equal(pedidos.length, 0);
  assert.equal(proveedor.getEnviados().length, 0);
});

test('lo que se dijo sin arrobarlo sirve de contexto cuando lo arroban', async () => {
  const { modelo, captacion } = await armar({ tipo: 'avance', visitas: [{ nombre: 'La Pizzería de Juan', resultado: 'interesado' }] });
  await conFetch(() => CREADO, async () => {
    await captacion.recibir({ grupo: GRUPO, id: 'x1', nombre: 'Gonzalo', texto: 'Pasé por La Pizzería de Juan, le interesó' });
    await captacion.recibir({ grupo: GRUPO, id: 'x2', nombre: 'Juan', alBot: true, texto: '@bot cargá lo que dijo Gonzalo' });
  });
  assert.equal(modelo.llamadas.length, 1);
  assert.match(modelo.llamadas[0].mensajes[0].content, /\[Gonzalo\] Pasé por La Pizzería de Juan/);
});

test('carga la visita en el CRM y, si lo arrobaron, contesta lo que cargó', async () => {
  const { proveedor, modelo, captacion } = await armar({
    tipo: 'avance',
    visitas: [{ nombre: 'La Pizzería de Juan', barrio: 'Pocitos', contacto: 'Martín', contacto_tel: '099 123 456',
      resultado: 'interesado', proxima: '2026-10-01T16:00', telefono: '' }],
  });
  const pedidos = await conFetch(() => CREADO, () => captacion.recibir({
    grupo: GRUPO, id: 'm1', nombre: 'Gonzalo', alBot: true,
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
  const { proveedor, captacion } = await armar({ tipo: 'charla' });
  const pedidos = await conFetch(() => CREADO, () => captacion.recibir({ grupo: GRUPO, id: 'm2', texto: 'Hoy almorzamos 🍕' }));
  assert.equal(pedidos.length, 0);
  assert.equal(proveedor.getEnviados().length, 0);
});

test('solo escucha su grupo, y no procesa dos veces el mismo mensaje', async () => {
  const { modelo, captacion } = await armar({ tipo: 'avance', visitas: [{ nombre: 'X', resultado: 'visitado' }] });
  await conFetch(() => CREADO, async () => {
    await captacion.recibir({ grupo: 'otro@g.us', id: 'a', alBot: true, texto: 'Pasé por X' });
    await captacion.recibir({ grupo: GRUPO, id: 'b', alBot: true, texto: 'Pasé por X' });
    await captacion.recibir({ grupo: GRUPO, id: 'b', alBot: true, texto: 'Pasé por X' });
  });
  assert.equal(modelo.llamadas.length, 1);
});

test('sin grupo configurado no hace nada', async () => {
  const { modelo, captacion } = await armar({ tipo: 'charla' }, { ...CFG, GRUPO_CAPTACION_JID: '' });
  assert.equal(captacion.activo, false);
  await captacion.recibir({ grupo: GRUPO, id: 'c', alBot: true, texto: 'Pasé por X' });
  assert.equal(modelo.llamadas.length, 0);
});

test('si el CRM rechaza uno, lo dice y carga los demás', async () => {
  const { proveedor, captacion } = await armar({
    tipo: 'avance',
    visitas: [{ nombre: 'Uno', resultado: 'visitado' }, { nombre: 'Dos', resultado: 'no_interesa' }],
  });
  await conFetch((b) => (b.nombre === 'Uno'
    ? { status: 400, datos: { ok: false, error: 'la fecha no puede quedar en el pasado' } }
    : { status: 201, datos: { ok: true, nuevo: false, resultado: 'No le interesa',
      prospecto: { nombre: 'Dos', barrio: 'Cordón', estado: 'descartado' } } }),
  () => captacion.recibir({ grupo: GRUPO, id: 'd', alBot: true, texto: '@bot Uno y Dos' }));
  const [r] = proveedor.getEnviados();
  assert.match(r.texto, /No pude cargar \*Uno\*: la fecha no puede quedar en el pasado/);
  assert.match(r.texto, /\*Dos\* · Cordón \(ya estaba: actualizado\)/);
  assert.match(r.texto, /No le interesa · vuelve en un año/);
});

test('saluda en el grupo (así arma las sesiones de cifrado con todos)', async () => {
  const { proveedor, captacion } = await armar({ tipo: 'charla' });
  await captacion.saludar();
  const [r] = proveedor.getEnviados();
  assert.equal(r.to, GRUPO);
  assert.match(r.texto, /Soy el bot de Scalerics/);
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


// ── pedidos al bot ───────────────────────────────────────────────────────────

const LISTA = { status: 200, datos: { items: [
  { id: 1, nombre: 'Smashico Burger', grupo: 0, cuando: '2026-09-27 11:00' },
  { id: 2, nombre: 'Burger Club', grupo: 1, cuando: '2026-09-28 16:00' },
  { id: 3, nombre: 'Rigor Pizza', grupo: 2, cuando: null },
] } };

test('pedido de resumen: consulta el CRM y contesta, sin cargar visitas', async () => {
  const { proveedor, captacion } = await armar({ tipo: 'pedido', accion: 'resumen' });
  const pedidos = await conFetch(() => ({ status: 200, datos: {
    desde: '2026-09-28', hasta: '2026-09-28',
    visitas: { total: 3, por_persona: { Gonzalo: 2, Lucas: 1 }, por_resultado: { interesado: 2, no_interesa: 1 } },
    llamadas: { total: 5, por_persona: { Lucas: 5 } },
    reuniones: [{ nombre: 'Rodelú', fecha_reunion: '2026-10-02 11:00' }], mails: 0,
  } }), () => captacion.recibir({ grupo: GRUPO, id: 'p1', nombre: 'Juan', alBot: true, texto: '@bot cuántas visitas hicimos hoy' }));
  assert.equal(pedidos.length, 1);
  assert.match(pedidos[0].url, /\/api\/fidelidad\/resumen\?desde=2026-09-28&hasta=2026-09-28$/);
  const [r] = proveedor.getEnviados();
  assert.match(r.texto, /^Hoy: 3 visitas \(Gonzalo 2, Lucas 1\)\./);
  assert.match(r.texto, /2 interesados, 1 no les interesa/);
  assert.match(r.texto, /Llamadas: 5 \(Lucas 5\)/);
  assert.match(r.texto, /Reuniones agendadas: Rodelú \(vie 2\/10 11:00\)/);
});

test('un pedido sin arrobar al bot no se hace ni se contesta', async () => {
  const { proveedor, captacion } = await armar({ tipo: 'pedido', accion: 'llamar_hoy' });
  const pedidos = await conFetch(() => LISTA, () => captacion.recibir({ grupo: GRUPO, id: 'p2b', texto: 'bot, a quién llamo hoy?' }));
  assert.equal(pedidos.length, 0);
  assert.equal(proveedor.getEnviados().length, 0);
});

test('a quién llamar hoy', async () => {
  const { proveedor, captacion } = await armar({ tipo: 'pedido', accion: 'llamar_hoy' });
  await conFetch(() => LISTA, () => captacion.recibir({ grupo: GRUPO, id: 'p2', alBot: true, texto: '@bot a quién llamo hoy?' }));
  const [r] = proveedor.getEnviados();
  assert.match(r.texto, /Vencidas \(1\): Smashico Burger \(dom 27\/9\)/);
  assert.match(r.texto, /Para hoy \(1\): Burger Club \(16:00\)/);
  assert.doesNotMatch(r.texto, /Rigor/);
});

test('agendar: busca el local, pide la fecha si falta y agenda', async () => {
  let armado = await armar({ tipo: 'pedido', accion: 'agendar_reunion', local: 'Rodelú' });
  await conFetch(() => ({ status: 200, datos: { items: [{ id: 7, nombre: 'Rodelú' }] } }),
    () => armado.captacion.recibir({ grupo: GRUPO, id: 'p3', alBot: true, texto: 'agendá con Rodelú' }));
  assert.match(armado.proveedor.getEnviados()[0].texto, /¿Para cuándo la reunión con Rodelú\?/);

  armado = await armar({ tipo: 'pedido', accion: 'agendar_reunion', local: 'rodelu', fecha: '2026-10-02T11:00' });
  const pedidos = await conFetch((body, url) => (url.includes('/agendar')
    ? { status: 200, datos: { prospecto: { nombre: 'Rodelú', fecha_reunion: '2026-10-02 11:00' } } }
    : { status: 200, datos: { items: [{ id: 7, nombre: 'Rodelú' }, { id: 8, nombre: 'Rodelú Express' }] } }),
  () => armado.captacion.recibir({ grupo: GRUPO, id: 'p4', nombre: 'Gonzalo', alBot: true, texto: 'reunión con rodelu el viernes a las 11' }));
  assert.match(pedidos[1].url, /\/prospectos\/7\/agendar$/);          // el nombre exacto gana
  assert.deepEqual(pedidos[1].body, { tipo: 'reunion', fecha: '2026-10-02T11:00', autor: 'Gonzalo' });
  assert.equal(armado.proveedor.getEnviados()[0].texto, '✓ Rodelú · reunión vie 2/10 11:00');
});

test('si hay varios locales parecidos, pregunta cuál', async () => {
  const { proveedor, captacion } = await armar({ tipo: 'pedido', accion: 'info_local', local: 'burger' });
  await conFetch(() => LISTA, () => captacion.recibir({ grupo: GRUPO, id: 'p5', alBot: true, texto: 'qué sabemos de burger' }));
  assert.match(proveedor.getEnviados()[0].texto, /Encontré varios: Smashico Burger, Burger Club, Rigor Pizza\. ¿Cuál\?/);
});

test('lo que no entiende lo contesta igual, con lo que sabe hacer', async () => {
  const { proveedor, captacion } = await armar({ tipo: 'charla' });
  const pedidos = await conFetch(() => LISTA, () => captacion.recibir({ grupo: GRUPO, id: 'p6', alBot: true, texto: '@bot cantame algo' }));
  assert.equal(pedidos.length, 0);
  assert.equal(proveedor.getEnviados()[0].texto, `No entendí qué necesitás. ${AYUDA}`);
});

test('si el CRM falla, lo dice en el grupo', async () => {
  const { proveedor, captacion } = await armar({ tipo: 'pedido', accion: 'llamar_hoy' });
  await conFetch(() => ({ status: 500, datos: { error: 'se cayó' } }),
    () => captacion.recibir({ grupo: GRUPO, id: 'p7', alBot: true, texto: 'a quién llamo' }));
  assert.equal(proveedor.getEnviados()[0].texto, 'No pude hacerlo: se cayó');
});

test('le hablan al bot solo si lo arroban (responderle no cuenta)', () => {
  const propios = ['59892000713:12@s.whatsapp.net', '99887766554433:12@lid'];
  const con = (contextInfo) => ({ message: { extendedTextMessage: { text: 'x', contextInfo } } });
  assert.equal(esParaElBot(con({ mentionedJid: ['99887766554433@lid'] }), propios), true);
  assert.equal(esParaElBot(con({ participant: '59892000713@s.whatsapp.net' }), propios), false);
  assert.equal(esParaElBot(con({ mentionedJid: ['59811111111@s.whatsapp.net'] }), propios), false);
  assert.equal(esParaElBot({ message: { conversation: 'hola' } }, propios), false);
});


test('si falta un dato, pregunta en vez de adivinar', async () => {
  const { proveedor, captacion } = await armar({ tipo: 'pedido', accion: 'no_entiendo', pregunta: '¿De qué local hablás?' });
  await conFetch(() => LISTA, () => captacion.recibir({ grupo: GRUPO, id: 'q1', alBot: true, texto: 'bot agendá eso' }));
  assert.equal(proveedor.getEnviados()[0].texto, '¿De qué local hablás?');
});

test('la IA ve los mensajes anteriores del grupo y lo que contestó el bot', async () => {
  const { modelo, captacion } = await armar({ tipo: 'pedido', accion: 'llamar_hoy' });
  await conFetch(() => LISTA, async () => {
    await captacion.recibir({ grupo: GRUPO, id: 'c1', nombre: 'Lucas', alBot: true, texto: 'a quién llamo hoy?' });
    await captacion.recibir({ grupo: GRUPO, id: 'c2', nombre: 'Lucas', alBot: true, texto: 'y el primero de esos?' });
  });
  const segundo = modelo.llamadas[1].mensajes[0].content;
  assert.match(segundo, /\[Lucas\] a quién llamo hoy\?/);
  assert.match(segundo, /\[Bot\] Vencidas \(1\): Smashico Burger/);
  assert.match(segundo, /Último mensaje, de Lucas \(le habla al bot\):\ny el primero de esos\?$/);
});

test('charla entre ellos no se contesta; si le hablan al bot y es charla, contesta con lo que sabe hacer', async () => {
  const { proveedor, captacion } = await armar({ tipo: 'charla' });
  await conFetch(() => LISTA, async () => {
    await captacion.recibir({ grupo: GRUPO, id: 'h1', texto: 'nos vemos a las 3 en la esquina' });
    await captacion.recibir({ grupo: GRUPO, id: 'h2', alBot: true, texto: 'bot, qué onda' });
  });
  const env = proveedor.getEnviados();
  assert.equal(env.length, 1);
  assert.match(env[0].texto, /^No entendí qué necesitás/);
});

test('si la IA falla y le hablaban al bot, lo dice', async () => {
  const proveedor = crearMock();
  await proveedor.conectar();
  const captacion = crearCaptacion({ cfg: CFG, proveedor, modelo: { activo: true, pedir: async () => null } });
  await captacion.recibir({ grupo: GRUPO, id: 'f1', alBot: true, texto: 'bot?' });
  await captacion.recibir({ grupo: GRUPO, id: 'f2', texto: 'charla' });
  const env = proveedor.getEnviados();
  assert.equal(env.length, 1);
  assert.match(env[0].texto, /falló la IA/);
});
