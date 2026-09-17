'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { crearJev } = require('../src/ia/jev');
const { conLead, stubModelo } = require('./helpers');
const { S } = require('../src/funnel/states');

const TEL = '59899123456';

/** Lead que dice lo que hace, no lo que necesita: el caso del 11-9. */
const DATOS_WEBS = { business_name: 'Estudio Norte', rubro: 'software', business_type: 'web' };
const DIJO_WEBS = 'somos Estudio Norte, hacemos software, webs';

const choice = (opcion, confidence) => ({ type: 'choice', choice: opcion, confidence, probabilities: { [opcion]: confidence } });
const noul = (p) => ({ type: 'noul', noul: p });

/**
 * Jev de mentira. `porTurno` deja contestar distinto en cada llamada de la
 * misma pregunta, que es como se prueba el reintento de la oferta.
 */
function stubJev({ necesidad = null, inventa = 0, porTurno = {}, falla = false, tira = false } = {}) {
  const consultas = [];
  const cuenta = {};
  return {
    activo: true,
    consultas,
    async preguntar(state, questions) {
      consultas.push({ state, questions });
      if (tira) throw new Error('jev exploto');
      if (falla) return null;
      const answers = {};
      for (const k of Object.keys(questions)) {
        cuenta[k] = (cuenta[k] || 0) + 1;
        const deTurno = porTurno[k]?.[cuenta[k] - 1];
        if (deTurno !== undefined) answers[k] = deTurno;
        else if (k === 'necesidad') answers[k] = necesidad || choice('pagina_web', 0.99);
        else if (k === 'es_cliente') answers[k] = noul(0.9);
        else if (k === 'inventa') answers[k] = noul(inventa);
      }
      return { answers, model: 'jev-stub', ms: 5, usage: { input_tokens: 500 } };
    },
  };
}

const DECIDE = { JEV_MODO: 'decide', JEV_UMBRAL: '0.8' };

async function conversar(s, texto = DIJO_WEBS) {
  await s.servicioLeads.registrarRespuesta(TEL, texto);
  await s.cola.vacia();
  await s.sombra.vaciar();
  return s.proveedor.getEnviados().filter((e) => e.to === TEL).map((e) => e.texto);
}

// ── necesidad ────────────────────────────────────────────────────────────────

test('por encima del umbral, la necesidad de Jev pisa la del bot', async () => {
  const jev = stubJev({ necesidad: choice('sistema_a_medida', 0.92) });
  const s = await conLead({ ...DECIDE, modelo: stubModelo({ datos: DATOS_WEBS }), _jev: jev });

  await conversar(s);

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.business_type, 4, 'sistema a medida, no la web que habia guardado el bot');
  assert.equal(l.fsm_state, S.MEETING_LINK_SENT, 'y el embudo sigue igual de largo');
  assert.equal(s.repo.sombrasDeLead(l.id).filter((x) => x.decision === 'necesidad').length, 1, 'queda anotado');
});

test('por debajo del umbral no pisa nada', async () => {
  const jev = stubJev({ necesidad: choice('sistema_a_medida', 0.79) });
  const s = await conLead({ ...DECIDE, modelo: stubModelo({ datos: DATOS_WEBS }), _jev: jev });

  await conversar(s);

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.business_type, 1, 'queda lo que guardo el bot');
  assert.equal(l.fsm_state, S.MEETING_LINK_SENT);
  assert.equal(s.repo.sombrasDeLead(l.id).find((x) => x.decision === 'necesidad').confianza, 0.79, 'igual se anota');
});

test('si Jev no contesta, el bot hace exactamente lo de antes', async () => {
  const jev = stubJev({ falla: true });
  const s = await conLead({ ...DECIDE, modelo: stubModelo({ datos: DATOS_WEBS }), _jev: jev });

  const msgs = await conversar(s);

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.business_type, 1);
  assert.equal(l.fsm_state, S.MEETING_LINK_SENT);
  assert.equal(msgs.at(-1), '[link_reunion]');
  assert.equal(s.repo.sombrasDeLead(l.id).length, 0, 'sin respuesta no hay nada que anotar');
});

test('si Jev explota, tampoco frena al lead', async () => {
  const jev = stubJev({ tira: true });
  const s = await conLead({ ...DECIDE, modelo: stubModelo({ datos: DATOS_WEBS }), _jev: jev });

  const msgs = await conversar(s);

  assert.equal(s.repo.leadPorTelefono(TEL).fsm_state, S.MEETING_LINK_SENT);
  assert.equal(msgs.at(-1), '[link_reunion]');
});

/**
 * Lo que tendria que haber pasado el 11 y el 12/9: no se lo da por calificado
 * y se le pregunta de nuevo, en vez de ofrecerle una reunion sin saber para que.
 */
test('si no queda claro que necesita, no califica y repregunta una sola vez', async () => {
  const jev = stubJev({ necesidad: choice('no_queda_claro', 0.85) });
  const s = await conLead({ ...DECIDE, modelo: stubModelo({ datos: DATOS_WEBS }), _jev: jev });

  const msgs = await conversar(s);

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(msgs.at(-1), '[necesidad_confusa]', 'le vuelve a preguntar');
  assert.ok(!msgs.includes('[link_reunion]'), 'y no le ofrece la reunion');
  assert.equal(l.fsm_state, S.CONVERSANDO);
  assert.equal(l.business_type, null, 'lo que habia guardado el bot no valia');
  assert.equal(l.necesidad_repreguntada, 1);
});

test('si a la segunda sigue sin quedar claro, va a una persona', async () => {
  const jev = stubJev({ necesidad: choice('no_queda_claro', 0.9) });
  const s = await conLead({ ...DECIDE, modelo: stubModelo({ datos: DATOS_WEBS }), _jev: jev });

  await conversar(s);
  const msgs = await conversar(s, 'nada, vos decime');

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.fsm_state, S.HUMAN_QUEUED);
  assert.equal(l.human_requested, 1);
  assert.equal(msgs.at(-1), '[derivacion]');
  assert.equal(l.motivo_derivacion, 'necesidad');

  const alEquipo = s.proveedor.getEnviados().filter((e) => e.to !== TEL).map((e) => e.texto).join('\n');
  assert.match(alEquipo, /no se entiende que necesita/, 'el equipo se entera del motivo');
});

// ── la oferta ────────────────────────────────────────────────────────────────

test('una oferta que le atribuye algo al lead se reescribe', async () => {
  const modelo = stubModelo({ datos: DATOS_WEBS });
  // Primera revision: inventa. Segunda (el reintento): ya no.
  const jev = stubJev({ porTurno: { inventa: [noul(0.95), noul(0.1)] } });
  const s = await conLead({ ...DECIDE, modelo, _jev: jev });

  const msgs = await conversar(s);

  assert.equal(msgs.at(-1), '[link_reunion]', 'sale la version reescrita');
  const pedidos = modelo.llamadas.map((l) => l.mensajes?.[0]?.content || '').filter((p) => p.includes('link_reunion'));
  assert.equal(pedidos.length, 2, 'se le pidio dos veces');
  assert.match(pedidos[1], /no le atribuyas al lead/, 'la segunda con la instruccion de no suponer');

  const anotado = s.repo.sombrasDeLead(s.repo.leadPorTelefono(TEL).id).filter((x) => x.decision === 'oferta');
  assert.deepEqual(anotado.map((x) => x.bot.intento), [1, 2]);
});

test('si el reintento tambien inventa, sale un texto fijo sin atribuciones', async () => {
  const jev = stubJev({ inventa: 0.97 });
  const s = await conLead({ ...DECIDE, modelo: stubModelo({ datos: DATOS_WEBS }), _jev: jev });

  const msgs = await conversar(s);

  const ultimo = msgs.at(-1);
  assert.notEqual(ultimo, '[link_reunion]', 'lo que escribio el modelo no sale');
  assert.match(ultimo, /videollamada de 30 minutos/);
  assert.match(ultimo, /calendly\.com/, 'y lleva el link para agendar');
  assert.ok(!/Entendí que necesitás/.test(ultimo));

  const anotado = s.repo.sombrasDeLead(s.repo.leadPorTelefono(TEL).id).filter((x) => x.decision === 'oferta');
  assert.equal(anotado.length, 2, 'los dos intentos quedan anotados');
  assert.equal(anotado.every((x) => x.coincide === false), true);
});

test('con Jev callado la oferta sale tal cual la escribio el modelo', async () => {
  const modelo = stubModelo({ datos: DATOS_WEBS });
  const s = await conLead({ ...DECIDE, modelo, _jev: stubJev({ falla: true }) });

  const msgs = await conversar(s);

  assert.equal(msgs.at(-1), '[link_reunion]');
  const pedidos = modelo.llamadas.map((l) => l.mensajes?.[0]?.content || '').filter((p) => p.includes('link_reunion'));
  assert.equal(pedidos.length, 1, 'ni siquiera se reintenta');
});

// ── el timeout de verdad, con el cliente ─────────────────────────────────────

test('si Jev se cuelga, el cliente corta y devuelve null', async () => {
  const jev = crearJev({
    apiKey: 'clave-jev-de-prueba', // gitleaks:allow — clave de mentira para el test
    url: 'https://jev.test/v1/systemone',
    modelo: 'jev-latest',
    timeoutMs: 30,
    fetch: (url, opciones) => new Promise((_, rechazar) => {
      opciones.signal.addEventListener('abort', () => rechazar(new Error('The operation was aborted')));
    }),
  });

  const t0 = Date.now();
  assert.equal(await jev.preguntar('x', { y: { type: 'noul', instructions: '?' } }), null);
  assert.ok(Date.now() - t0 < 2000, 'corta por el timeout, no espera');
});

test('los tokens de entrada quedan guardados, que es lo que deja calcular el costo', async () => {
  const s = await conLead({ ...DECIDE, modelo: stubModelo({ datos: DATOS_WEBS }), _jev: stubJev({}) });

  await conversar(s);

  const filas = s.repo.sombrasDeLead(s.repo.leadPorTelefono(TEL).id);
  assert.ok(filas.length);
  assert.equal(filas.every((f) => f.input_tokens === 500), true);
});
