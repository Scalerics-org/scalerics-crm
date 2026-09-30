'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { instanteLocal } = require('../src/agenda/gcal');
const { crearScheduler } = require('../src/scheduler/followup');
const { S } = require('../src/funnel/states');
const { conLead, stubModelo } = require('./helpers');
const { googleFalso } = require('./google-falso');

const TZ = 'America/Montevideo';
const TEL = '59899123456';
const en = (dia, h = 10, m = 0) => instanteLocal(dia, h, m, TZ);

const COMPLETO = {
  business_name: 'Panadería PanesAhora', rubro: 'panadería',
  business_type: 'web', budget: 'mas_3000', team_size_personas: 8,
  instagram_web: '@panesahora', needs: 'quiero vender online',
};
const DIJO_TODO = 'te cuento todo: es la Panadería PanesAhora, una panadería, '
  + 'estamos en @panesahora y quiero vender online';

/**
 * Lead 18 (30-9): a las 03:03 el bot le ofrecio "1. Jueves 1 / 2. Viernes 2 /
 * 3. La semana que viene". Se durmio. A las 12:13 el job 'retomar' salio en
 * prosa ("¿Alguno de esos días te viene bien? Jueves 1, viernes 2, o preferís
 * la semana que viene.") y repitiendo el pitch: sin numeros, sin poder elegir
 * con un "2".
 */
async function conOferta({ google = googleFalso(), respuestas = {} } = {}) {
  // Un Date mutable: setTime() mueve el reloj del bot entre la oferta y el retomar.
  const reloj = en('2026-09-23', 10);
  const modelo = stubModelo({ datos: COMPLETO, respuestas });
  const s = await conLead({ modelo, AGENDA_OFRECE_HORARIOS: 'true', _google: google.fetch }, undefined, reloj);
  s.proveedor.limpiar();
  await s.servicioLeads.registrarRespuesta(TEL, DIJO_TODO);
  await s.cola.vacia();
  const lead = () => s.repo.leadPorTelefono(TEL);
  const mover = (fecha) => reloj.setTime(fecha.getTime());
  return { s, modelo, lead, mover };
}

test('eligiendo dia: el retomar sale con la lista numerada recalculada, sin repetir el pitch', async () => {
  const { s, modelo, lead } = await conOferta({ respuestas: { retomar_oferta: 'Retomamos donde quedamos.' } });
  assert.equal(lead().fsm_state, S.HORARIOS_OFRECIDOS);

  const r = await s.embudo.retomarOferta(lead().id);

  assert.equal(r.texto,
    'Retomamos donde quedamos.\n\n¿Qué día te queda mejor?\n\n1. Miércoles 23\n2. Jueves 24\n3. Viernes 25\n4. La semana que viene');
  const prompt = modelo.llamadas.at(-1).mensajes[0].content;
  assert.match(prompt, /situación: retomar_oferta/);
  assert.match(prompt, /NO repitas el pitch/);
});

test('la lista se RECALCULA: si paso un dia, ya no esta', async () => {
  const { s, lead, mover } = await conOferta();
  mover(en('2026-09-24', 9)); // amanecio el jueves: el miercoles ya no existe

  const r = await s.embudo.retomarOferta(lead().id);

  assert.match(r.texto, /1\. Jueves 24\n2\. Viernes 25\n3\. La semana que viene$/);
  assert.doesNotMatch(r.texto, /Miércoles 23/);
  // Lo que se guardo es lo que el lead ve: un "1" elige el jueves.
  assert.match(JSON.parse(lead().horarios_ofrecidos)[0], /^2026-09-24/);
});

test('el dia que se lleno entre medio no aparece', async () => {
  const google = googleFalso({ ocupadosDia: { '2026-09-24': ['12:00-16:00'] } });
  const { s, lead } = await conOferta({ google });
  const r = await s.embudo.retomarOferta(lead().id);
  assert.doesNotMatch(r.texto, /Jueves 24/);
});

test('despues del retomar se puede elegir con el numero de la lista nueva', async () => {
  const { s, lead, mover } = await conOferta();
  mover(en('2026-09-24', 9));
  await s.embudo.retomarOferta(lead().id);

  s.proveedor.limpiar();
  await s.servicioLeads.registrarRespuesta(TEL, '1');
  await s.cola.vacia();
  assert.equal(lead().dia_en_foco, '2026-09-24', 'el 1 es el jueves, no el miercoles que ya paso');
});

test('eligiendo hora: vuelve a mostrar las horas de ese dia, numeradas', async () => {
  const { s, lead } = await conOferta({ respuestas: { retomar_oferta: 'Retomamos.' } });
  await s.servicioLeads.registrarRespuesta(TEL, '1');
  await s.cola.vacia();
  assert.equal(lead().dia_en_foco, '2026-09-23');

  const r = await s.embudo.retomarOferta(lead().id);

  assert.match(r.texto, /^Retomamos\.\n\nHorarios del Miércoles 23:\n1\. 13:00\n2\. 13:30\n/);
  assert.equal(lead().dia_en_foco, '2026-09-23', 'sigue en el paso de horas');
});

test('eligiendo hora de un dia que ya paso: vuelve a la lista de dias', async () => {
  const { s, lead, mover } = await conOferta();
  await s.servicioLeads.registrarRespuesta(TEL, '1');
  await s.cola.vacia();
  mover(en('2026-09-24', 9));

  const r = await s.embudo.retomarOferta(lead().id);

  assert.match(r.texto, /¿Qué día te queda mejor\?\n\n1\. Jueves 24/);
  assert.equal(lead().dia_en_foco, null, 'de nuevo en el paso de dias');
});

test('si el modelo escribe su propia lista, no sale duplicada', async () => {
  const propia = 'Retomamos. ¿Te sirve?\n1. Lunes 28\n2. Martes 29';
  const { s, lead } = await conOferta({ respuestas: { retomar_oferta: propia } });
  const r = await s.embudo.retomarOferta(lead().id);
  assert.doesNotMatch(r.texto, /Lunes 28/);
  assert.equal((r.texto.match(/^\d\. /gm) || []).length, 4);
});

test('si la IA no puede escribir, pide reintentar y NO toca lo que el lead ya tenia', async () => {
  const { s, modelo, lead, mover } = await conOferta();
  const antes = lead().horarios_ofrecidos;
  mover(en('2026-09-24', 9));
  modelo.pedir = async () => null; // el modelo falla desde ahora

  const r = await s.embudo.retomarOferta(lead().id);

  assert.deepEqual(r, { reintentar: true });
  assert.equal(lead().horarios_ofrecidos, antes, 'el reintento recalcula; mientras tanto nada cambia');
});

// ── lo que NO es retomarOferta ──────────────────────────────────────────────

test('en otra etapa no aplica: devuelve null y sigue el retomar de siempre', async () => {
  const { s, lead } = await conOferta();
  s.repo.actualizarFunnel(lead().id, { fsm_state: S.CONVERSANDO });
  assert.equal(await s.embudo.retomarOferta(lead().id), null);
});

test('sin agenda conectada devuelve null en vez de romper', async () => {
  const modelo = stubModelo({ datos: COMPLETO });
  const s = await conLead({ modelo, AGENDA_OFRECE_HORARIOS: 'true', GCAL_REFRESH_TOKEN: '' });
  await s.servicioLeads.registrarRespuesta(TEL, DIJO_TODO);
  await s.cola.vacia();
  s.repo.actualizarFunnel(s.repo.leadPorTelefono(TEL).id, { fsm_state: S.HORARIOS_OFRECIDOS });
  assert.equal(await s.embudo.retomarOferta(s.repo.leadPorTelefono(TEL).id), null);
});

test('con Google caido devuelve null (cae al retomar de siempre) y no inventa una lista', async () => {
  const g = googleFalso();
  let caido = false;
  const google = { fetch: async (u, o) => (caido && u.includes('/freeBusy')
    ? { ok: false, status: 500, text: async () => 'boom' } : g.fetch(u, o)) };
  const { s, lead } = await conOferta({ google });
  const antes = lead().horarios_ofrecidos;
  caido = true;

  assert.equal(await s.embudo.retomarOferta(lead().id), null);
  assert.equal(lead().horarios_ofrecidos, antes);
});

test('un lead que no existe devuelve null', async () => {
  const { s } = await conOferta();
  assert.equal(await s.embudo.retomarOferta(99999), null);
});

// ── el scheduler: usa la oferta, y cae al retomar de siempre si no hay ──────

function schedulerCon({ oferta, redactorTexto = 'retomando igual' }) {
  const encolados = []; const marcados = []; const cambios = [];
  const lead = { id: 1, telefono: TEL, conversacion_desde: null };
  const job = { id: 9, type: 'followup', motivo: 'retomar', lead_id: 1, run_at: new Date().toISOString() };
  const repo = {
    jobsVencidos: () => [job], leadPorId: () => lead, ultimosMensajes: () => [],
    actualizarLead: (id, c) => cambios.push(c),
    marcarJob: (id, estado) => marcados.push(estado), reprogramarJob: () => marcados.push('reprogramado'),
  };
  const pedidos = [];
  const redactor = { escribir: async (l, sit) => { pedidos.push(sit); return redactorTexto; } };
  const cola = { encolar: (m) => encolados.push(m) };
  const embudo = { retomarOferta: async () => oferta };
  return { scheduler: crearScheduler({ repo, cola, cfg: {}, redactor, embudo, ahora: () => new Date() }), encolados, marcados, cambios, pedidos };
}

test('scheduler: con oferta, manda ese texto (kind followup) y marca retomado_at', async () => {
  const t = schedulerCon({ oferta: { texto: 'Retomamos.\n\n1. Jueves 1' } });
  await t.scheduler.correrVencidos();
  assert.equal(t.encolados[0].texto, 'Retomamos.\n\n1. Jueves 1');
  assert.equal(t.encolados[0].kind, 'followup');
  assert.ok(t.cambios[0].retomado_at);
  assert.deepEqual(t.pedidos, [], 'no paso por el redactor de siempre');
  assert.equal(t.marcados[0], 'done');
});

test('scheduler: si la oferta pide reintentar, el job se reprograma y no sale nada', async () => {
  const t = schedulerCon({ oferta: { reintentar: true } });
  await t.scheduler.correrVencidos();
  assert.equal(t.encolados.length, 0);
  assert.notEqual(t.marcados[0], 'done');
});

test('scheduler: sin oferta (null) sigue el retomar de siempre', async () => {
  const t = schedulerCon({ oferta: null });
  await t.scheduler.correrVencidos();
  assert.deepEqual(t.pedidos, ['retomar']);
  assert.equal(t.encolados[0].texto, 'retomando igual');
});
