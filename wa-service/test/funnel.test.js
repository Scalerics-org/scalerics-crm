'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { conLead, stubOpenAI, ADMIN } = require('./helpers');
const { S } = require('../src/funnel/states');
const { porReglas } = require('../src/funnel/scoring');

const TEL = '59899123456';

/** Manda un mensaje del lead y devuelve lo que le contestaron. */
async function lead(s, texto) {
  await s.servicioLeads.registrarRespuesta(TEL, texto);
  await s.cola.vacia();
  return s.proveedor.getEnviados().filter((e) => e.to === TEL).map((e) => e.texto);
}

const estado = (s) => s.repo.leadPorTelefono(TEL).fsm_state;

/** Los siete datos completos: con esto el proximo turno cierra y califica. */
const COMPLETO = {
  business_name: 'Inmobiliaria Pereyra', rubro: 'inmobiliaria',
  business_type: 'ecommerce', budget: 'mas_3000', team_size_personas: 8,
  instagram_web: '@inmopereyra', needs: 'quiero dejar de perder consultas',
};

// ── la IA conduce ────────────────────────────────────────────────────────────

test('el primer mensaje del lead lo contesta la IA, no un menu', async () => {
  const s = await conLead();
  const msgs = await lead(s, 'hola, tengo una inmobiliaria');

  assert.equal(estado(s), S.CONVERSANDO);
  assert.equal(msgs.at(-1), '[conversacion]');
});

test('lo que la IA extrae queda guardado y clasificado', async () => {
  const s = await conLead({ openai: stubOpenAI({ datos: { rubro: 'parrilla con delivery' } }) });
  await lead(s, 'tenemos una parrilla');

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.rubro, 'parrilla con delivery');
  assert.equal(l.rubro_norm, 'gastronomia', 'el gancho del follow-up sale de aca');
});

test('con los siete datos cierra el codigo: califica y ofrece la reunion', async () => {
  const s = await conLead({ openai: stubOpenAI({ datos: COMPLETO }) });
  const msgs = await lead(s, 'te cuento todo de una');

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.fsm_state, S.MEETING_SENT);
  assert.equal(l.score, 9);
  assert.equal(msgs.at(-1), '[oferta_reunion]', 'la escribe la IA, pero la dispara el score');
});

test('un lead flojo no recibe la oferta', async () => {
  const flojo = {
    business_name: 'Kiosco', rubro: 'no se', business_type: 'web',
    budget: 'menos_500', team_size_personas: 1, instagram_web: 'no tengo', needs: 'algo',
  };
  const s = await conLead({ openai: stubOpenAI({ datos: flojo }) });
  const msgs = await lead(s, 'te cuento');

  assert.equal(estado(s), S.DISQUALIFIED);
  assert.equal(msgs.at(-1), '[descartado]');
});

// ── despues de la oferta ─────────────────────────────────────────────────────

/** Deja al lead con el link de Calendly en la mano. */
const CALENDLY = 'https://calendly.com/scalerics/diagnostico';

/** Un stub que ya tiene los datos y que, al decirle que si, manda el link. */
const stubQueMandaElLink = () => stubOpenAI({
  datos: COMPLETO,
  respuestas: { conversacion: `Genial, agendá acá: ${CALENDLY}` },
});

/** Deja al lead con el link de Calendly en la mano. */
async function conLink(s) {
  await lead(s, 'te cuento todo');
  assert.equal(estado(s), S.MEETING_SENT);
  await lead(s, 'dale');
  assert.equal(estado(s), S.MEETING_LINK_SENT, 'el codigo detecta que el link salio');
  s.proveedor.limpiar();
}

test('el link se manda una sola vez', async () => {
  // MEETING_SENT contestaba el link a cualquier cosa y se quedaba ahi: un
  // "hola" devolvia el link, y otro "hola" devolvia el link, sin final.
  const s = await conLead({ openai: stubQueMandaElLink() });
  await conLink(s);

  const msgs = await lead(s, 'hola');
  assert.equal(msgs.at(-1), '[ya_tiene_link]', 'no vuelve a la situacion del link');
});

test('si dice que ya agendo, se le cree', async () => {
  const s = await conLead({ openai: stubQueMandaElLink() });
  await conLink(s);

  const msgs = await lead(s, 'ya agendé para el jueves');
  assert.equal(msgs.at(-1), '[ya_agendo]');
});

test('"agendamos?" no se confunde con "ya agendé"', async () => {
  // El que pregunta todavia NO reservo. Tomarlo como que si seria dejar de
  // empujar justo al que estaba por convertir.
  const s = await conLead({ openai: stubQueMandaElLink() });
  await conLink(s);

  const msgs = await lead(s, 'agendamos entonces?');
  assert.notEqual(msgs.at(-1), '[ya_agendo]');
});

test('el que insiste con el link en la mano termina con una persona', async () => {
  const s = await conLead({ openai: stubQueMandaElLink() });
  await conLink(s);

  await lead(s, 'hola');
  const msgs = await lead(s, 'hola?');

  assert.equal(estado(s), S.HUMAN_QUEUED);
  assert.equal(msgs.at(-1), '[derivacion]');
  assert.equal(s.repo.leadPorTelefono(TEL).motivo_derivacion, 'post_oferta');
});

// ── lo que NO se le delega al modelo ─────────────────────────────────────────

test('la baja es texto fijo y no pasa por la IA', async () => {
  // Tiene que salir aunque la API este caida, y no se le da al modelo la
  // oportunidad de intentar retener a alguien que pidio que no le escriban.
  const openai = stubOpenAI();
  const s = await conLead({ openai });
  await lead(s, 'hola');
  const antes = openai.llamadas.length;

  const msgs = await lead(s, 'baja');
  assert.equal(estado(s), S.OPT_OUT);
  assert.match(msgs.at(-1), /no te escribo más/);
  assert.equal(openai.llamadas.length, antes, 'ni se le pregunta al modelo');

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.opt_out, 1);
  assert.equal(l.status, 'closed');

  s.proveedor.limpiar();
  await lead(s, 'hola?');
  assert.equal(s.proveedor.getEnviados().length, 0, 'despues hay silencio');
});

test('la baja sale aunque la IA este caida', async () => {
  const s = await conLead({ openai: stubOpenAI({ falla: '500' }) });
  const msgs = await lead(s, 'sacame de la lista');
  assert.match(msgs.at(-1), /no te escribo más/);
});

test('sin IA nadie queda sin respuesta: va a una persona', async () => {
  // Sacados los textos fijos no hay embudo que atienda. Contestar con algo
  // armado seria fingir; lo honesto es pasarlo con alguien.
  const s = await conLead({ openai: stubOpenAI({ falla: '529 overloaded' }) });
  const msgs = await lead(s, 'hola, necesito una web');

  assert.equal(estado(s), S.HUMAN_QUEUED);
  assert.match(msgs.at(-1), /te paso con alguien del equipo/);
  assert.equal(s.repo.leadPorTelefono(TEL).motivo_derivacion, 'sin_ia');
});

test('sin clave configurada tampoco se queda mudo', async () => {
  const s = await conLead({ sinIA: true });
  const msgs = await lead(s, 'hola');

  assert.equal(estado(s), S.HUMAN_QUEUED);
  assert.match(msgs.at(-1), /te paso con alguien del equipo/);
});

test('pedir un humano lo congela, y solo el CRM lo devuelve', async () => {
  const s = await conLead();
  await lead(s, 'quiero hablar con una persona');
  assert.equal(estado(s), S.HUMAN_QUEUED);
  s.proveedor.limpiar();

  await lead(s, 'hola?');
  assert.equal(s.proveedor.getEnviados().length, 0, 'el bot no se mete');

  await s.app.inject({
    method: 'POST', url: `/api/leads/phone/${TEL}/release`,
    headers: { 'x-admin-token': ADMIN },
  });
  assert.equal(s.repo.leadPorTelefono(TEL).human_requested, 0);
});

// ── scoring ──────────────────────────────────────────────────────────────────

test('un rubro que Scalerics sabe atender suma un punto', () => {
  const base = { budget: 2, team_size: 1, business_type: 1, needs: 'algo' };

  assert.equal(porReglas({ ...base }).score, 2, 'sin rubro');
  assert.equal(porReglas({ ...base, rubro_norm: 'generico' }).score, 2, 'no clasificado, no suma');

  for (const r of ['gastronomia', 'salud', 'retail', 'servicios_profesionales',
                   'inmobiliaria', 'educacion', 'automotriz']) {
    assert.equal(porReglas({ ...base, rubro_norm: r }).score, 3, r);
  }
});

test('ningun rubro pesa mas que otro', () => {
  // A proposito: poner uno arriba de otro seria inventar un ranking que nadie
  // midio. La tabla existe para cuando haya datos de conversion por vertical.
  const puntos = ['gastronomia', 'salud', 'retail', 'servicios_profesionales',
                  'inmobiliaria', 'educacion', 'automotriz']
    .map((r) => porReglas({ rubro_norm: r }).score);

  assert.equal(new Set(puntos).size, 1, 'todos valen lo mismo');
});

test('el rubro solo no alcanza para una reunion', () => {
  // Es un empujon, no un atajo: sin presupuesto ni proyecto sigue descartado.
  const r = porReglas({ rubro_norm: 'gastronomia' });
  assert.equal(r.score, 1);
  assert.equal(r.recommended_action, 'disqualify');
});

test('el rubro puede inclinar un lead del medio hacia la reunion', () => {
  // budget 2 (+2) + team>=2 (+2) = 4, justo debajo del umbral. Con el rubro
  // identificado llega a 5. Es el caso que el cambio busca mover.
  const medio = { budget: 2, team_size: 2, business_type: 1, needs: 'corto' };

  assert.equal(porReglas(medio).recommended_action, 'nurture');
  assert.equal(porReglas({ ...medio, rubro_norm: 'automotriz' }).recommended_action, 'meeting');
});

// ── reinicio de un lead ──────────────────────────────────────────────────────

test('reiniciar un lead lo devuelve al principio y la IA se olvida', async () => {
  // Reiniciar borrando solo el estado dejaba el reinicio a medias: el embudo
  // arrancaba de cero pero la IA seguia leyendo los mensajes viejos, asi que
  // retomaba una conversacion que para el lead ya no existia.
  const s = await conLead({ openai: stubOpenAI({ datos: { rubro: 'inmobiliaria' } }) });
  await lead(s, 'hola, tengo una inmobiliaria');
  await lead(s, 'somos 8');

  const antes = s.repo.leadPorTelefono(TEL);
  assert.equal(antes.fsm_state, S.CONVERSANDO);
  assert.equal(antes.rubro, 'inmobiliaria');
  const mensajesAntes = s.repo.mensajesDeLead(antes.id).length;
  assert.ok(mensajesAntes > 0);

  s.repo.reiniciarLead(antes.id, new Date().toISOString());

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.fsm_state, 'NEW');
  assert.equal(l.rubro, null, 'se le borra lo que habia averiguado');
  assert.equal(l.score, null);
  assert.equal(l.human_requested, 0);

  // El historial NO se borra: es lo que el equipo ve en el panel del CRM.
  assert.equal(s.repo.mensajesDeLead(l.id).length, mensajesAntes, 'los mensajes quedan');

  // Pero la IA ya no los ve.
  assert.equal(
    s.repo.ultimosMensajes(l.id, 20, l.conversacion_desde).length, 0,
    'para la IA la conversacion empieza de cero'
  );
  assert.ok(s.repo.ultimosMensajes(l.id, 20).length > 0, 'sin frontera se sigue viendo todo');
});

test('un lead sin reinicios ve todo su historial', async () => {
  const s = await conLead();
  await lead(s, 'hola');
  const l = s.repo.leadPorTelefono(TEL);

  assert.equal(l.conversacion_desde, null);
  assert.ok(s.repo.ultimosMensajes(l.id, 20, l.conversacion_desde).length > 0);
});

test('reiniciar cancela los jobs pendientes', async () => {
  // Un follow-up programado sobre una conversacion que ya no existe llegaria
  // hablando de algo que el lead no recuerda.
  const s = await conLead();
  const l = s.repo.leadPorTelefono(TEL);
  const pendientes = () => s.repo.db
    .prepare("SELECT COUNT(*) c FROM jobs WHERE lead_id=? AND status='pending'").get(l.id).c;

  assert.equal(pendientes(), 1, 'el follow-up quedo programado al dar de alta');
  s.repo.reiniciarLead(l.id, new Date().toISOString());
  assert.equal(pendientes(), 0);
});
