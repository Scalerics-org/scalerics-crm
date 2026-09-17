'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { crearJev } = require('../src/ia/jev');
const { conLead, stubModelo } = require('./helpers');
const { S } = require('../src/funnel/states');

const TEL = '59899123456';

// ── el cliente ───────────────────────────────────────────────────────────────

function fetchFalso(respuesta) {
  const pedidos = [];
  const fn = async (url, opciones) => {
    pedidos.push({ url, opciones, body: JSON.parse(opciones.body) });
    if (respuesta instanceof Error) throw respuesta;
    return respuesta;
  };
  return { fn, pedidos };
}

const CLIENTE = { apiKey: 'clave-jev-de-prueba', url: 'https://jev.test/v1/systemone', modelo: 'jev-latest' }; // gitleaks:allow — clave de mentira para el test

test('jev pide con la clave, el modelo, el estado y las preguntas', async () => {
  const f = fetchFalso({ ok: true, status: 200, json: async () => ({ model: 'jev-1.13.0', answers: { x: { type: 'noul', noul: 0.9 } } }) });
  const jev = crearJev({ ...CLIENTE, fetch: f.fn });

  const r = await jev.preguntar([{ de: 'lead', texto: 'hola' }], { x: { type: 'noul', instructions: '?' } });

  assert.deepEqual(r.answers, { x: { type: 'noul', noul: 0.9 } });
  assert.equal(r.model, 'jev-1.13.0');
  assert.equal(f.pedidos[0].url, 'https://jev.test/v1/systemone');
  assert.equal(f.pedidos[0].opciones.headers.Authorization, 'Bearer clave-jev-de-prueba'); // gitleaks:allow — clave de mentira para el test
  assert.deepEqual(f.pedidos[0].body, {
    model: 'jev-latest', state: [{ de: 'lead', texto: 'hola' }], questions: { x: { type: 'noul', instructions: '?' } },
  });
  assert.ok(f.pedidos[0].opciones.signal, 'con timeout');
});

/** Una falla de Jev no puede tocar la conversacion con el lead. */
test('jev nunca tira: rechazo, red caida o respuesta rara dan null', async () => {
  for (const respuesta of [
    { ok: false, status: 429, json: async () => ({}) },
    new Error('ECONNRESET'),
    { ok: true, status: 200, json: async () => ({ sin: 'answers' }) },
  ]) {
    const jev = crearJev({ ...CLIENTE, fetch: fetchFalso(respuesta).fn });
    assert.equal(await jev.preguntar('x', {}), null);
  }
});

test('sin clave jev queda inactivo y no pide nada', async () => {
  const f = fetchFalso({ ok: true });
  const jev = crearJev({ ...CLIENTE, apiKey: '', fetch: f.fn });
  assert.equal(jev.activo, false);
  assert.equal(await jev.preguntar('x', {}), null);
  assert.equal(f.pedidos.length, 0);
});

// ── la sombra dentro del embudo ──────────────────────────────────────────────

/** Jev de mentira: contesta lo que se le diga para cada pregunta que le llegue. */
function stubJev(respuestas, { falla = false } = {}) {
  const consultas = [];
  return {
    activo: true,
    consultas,
    async preguntar(state, questions) {
      consultas.push({ state, questions });
      if (falla) return null;
      const answers = Object.fromEntries(Object.keys(questions).map((k) => [k, respuestas[k]]));
      return { answers, model: 'jev-stub', ms: 7 };
    },
  };
}

/** Lead que dice lo que hace, no lo que necesita: el caso del 11-9. */
const DATOS_WEBS = { business_name: 'Estudio Norte', rubro: 'software', business_type: 'web' };
const DIJO_WEBS = 'somos Estudio Norte, hacemos software, webs';

test('al calificar, anota lo que Jev cree que necesita al lado de lo que guardo el bot', async () => {
  const jev = stubJev({
    necesidad: { type: 'choice', choice: 'no_queda_claro', confidence: 0.81, probabilities: { no_queda_claro: 0.84, pagina_web: 0.16 } },
    es_cliente: { type: 'noul', noul: 0.18 },
    inventa: { type: 'noul', noul: 0.9 },
  });
  const s = await conLead({ modelo: stubModelo({ datos: DATOS_WEBS }), _jev: jev });

  await s.servicioLeads.registrarRespuesta(TEL, DIJO_WEBS);
  await s.cola.vacia();
  await s.sombra.vaciar();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.fsm_state, S.MEETING_LINK_SENT, 'el bot hace lo mismo que sin Jev');
  assert.equal(l.business_type, 1);

  const necesidad = s.repo.sombrasDeLead(l.id).find((x) => x.decision === 'necesidad');
  assert.ok(necesidad, 'quedo anotada');
  assert.deepEqual(necesidad.bot, { business_type: 1 });
  assert.equal(necesidad.jev.necesidad.choice, 'no_queda_claro');
  assert.equal(necesidad.coincide, false);
  assert.equal(necesidad.confianza, 0.81);

  const estado = jev.consultas.find((c) => c.questions.necesidad).state;
  assert.ok(estado.some((m) => m.de === 'lead' && m.texto === DIJO_WEBS), 'Jev ve lo que dijo el lead');
  assert.ok(!estado.some((m) => /Nuevo contacto|Lead calificado/.test(m.texto)), 'y no los avisos al equipo');
});

test('la oferta se revisa con el texto que salio', async () => {
  const jev = stubJev({
    necesidad: { type: 'choice', choice: 'pagina_web', confidence: 0.9 },
    es_cliente: { type: 'noul', noul: 0.9 },
    inventa: { type: 'noul', noul: 0.8 },
  });
  const s = await conLead({ modelo: stubModelo({ datos: DATOS_WEBS }), _jev: jev });

  await s.servicioLeads.registrarRespuesta(TEL, DIJO_WEBS);
  await s.cola.vacia();
  await s.sombra.vaciar();

  const oferta = s.repo.sombrasDeLead(s.repo.leadPorTelefono(TEL).id).find((x) => x.decision === 'oferta');
  assert.ok(oferta);
  assert.deepEqual(oferta.bot, { situacion: 'link_reunion', texto: '[link_reunion]' });
  assert.equal(oferta.coincide, false, 'Jev cree que inventa algo');
  const consulta = jev.consultas.find((c) => c.questions.inventa);
  assert.equal(consulta.state.mensaje_del_bot, '[link_reunion]');
});

test('los mensajes que no son la oferta no se le mandan a Jev', async () => {
  const jev = stubJev({});
  const s = await conLead({ modelo: stubModelo(), _jev: jev });

  await s.servicioLeads.registrarRespuesta(TEL, 'hola, tengo una inmobiliaria');
  await s.cola.vacia();
  await s.sombra.vaciar();

  assert.equal(jev.consultas.length, 0);
  assert.equal(s.repo.sombrasDeLead(s.repo.leadPorTelefono(TEL).id).length, 0);
});

/** Si Jev no contesta, no se anota nada y el lead recibe exactamente lo mismo. */
test('con Jev caido el embudo sigue igual y no se anota nada', async () => {
  const jev = stubJev({}, { falla: true });
  const s = await conLead({ modelo: stubModelo({ datos: DATOS_WEBS }), _jev: jev });

  await s.servicioLeads.registrarRespuesta(TEL, DIJO_WEBS);
  await s.cola.vacia();
  await s.sombra.vaciar();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.fsm_state, S.MEETING_LINK_SENT);
  assert.equal(s.repo.sombrasDeLead(l.id).length, 0);
});

/** Lo que Jev contesta no puede cambiar ni un mensaje. */
test('con o sin sombra, al lead le llega lo mismo', async () => {
  const conversar = async (extra) => {
    const s = await conLead({ modelo: stubModelo({ datos: DATOS_WEBS }), ...extra });
    await s.servicioLeads.registrarRespuesta(TEL, DIJO_WEBS);
    await s.cola.vacia();
    await s.sombra.vaciar();
    return s.proveedor.getEnviados().map((e) => [e.to, e.texto]);
  };

  const sinSombra = await conversar({});
  const conSombra = await conversar({
    _jev: stubJev({
      necesidad: { type: 'choice', choice: 'no_queda_claro', confidence: 1 },
      es_cliente: { type: 'noul', noul: 0 },
      inventa: { type: 'noul', noul: 1 },
    }),
  });
  assert.deepEqual(conSombra, sinSombra);
});

test('apagado por config, aunque haya clave, no se consulta nada', async () => {
  const s = await conLead({ JEV_API_KEY: 'clave-jev-de-prueba', JEV_MODO: 'apagado' }); // gitleaks:allow — clave de mentira para el test
  assert.equal(s.sombra.activa, false);
});
