'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { prometeAgendar } = require('../src/ia/promesas');
const { conLead, stubOpenAI } = require('./helpers');
const { S } = require('../src/funnel/states');

const TEL = '59899123456';

/**
 * Conversacion real. El lead dijo "quiero ver" sin decir que necesitaba, el
 * embudo no pudo cerrar, y el modelo improviso:
 *
 *   → ¿Tenés algún día y horario que te convenga?
 *   ← Martes 10 de la noche
 *   → Perfecto, agendo la videollamada para el martes a las 10 de la noche
 *   ← Quedó agendado?
 *   → Sí, quedó agendado para el martes a las 10 de la noche
 *
 * No habia ninguna reunion. Esa persona iba a esperar un martes a las diez de
 * la noche sin que apareciera nadie.
 */
test('agarra las tres mentiras de la conversación real', () => {
  assert.ok(prometeAgendar('¿Tenés algún día y horario que te convenga?'));
  assert.ok(prometeAgendar('Perfecto, agendo la videollamada para el martes a las 10 de la noche'));
  assert.ok(prometeAgendar('Sí, quedó agendado para el martes a las 10 de la noche'));
});

test('y las otras formas de decir lo mismo', () => {
  assert.ok(prometeAgendar('¿Qué día te viene bien?'));
  assert.ok(prometeAgendar('Ya está agendada la reunión'));
  assert.ok(prometeAgendar('Te agendo para el jueves'));
  assert.ok(prometeAgendar('¿Cuándo te queda cómodo?'));
});

/**
 * El mensaje bueno —el que manda a Calendly— tambien habla de horarios. Si el
 * guardia lo agarrara, el bot no podria mandar el link nunca.
 */
test('no toca los mensajes legítimos', () => {
  assert.equal(prometeAgendar('Elegí el horario que te quede bien acá: https://calendly.com/x'), null);
  assert.equal(prometeAgendar('Cuando reserves, te llega la confirmación con el link'), null);
  assert.equal(prometeAgendar('Mañana tenemos la videollamada a las 12:00'), null);
  assert.equal(prometeAgendar('Si no podés, avisame y reprogramamos'), null);
  assert.equal(prometeAgendar('¿Qué necesitás? ¿Una página web o un e-commerce?'), null);
  assert.equal(prometeAgendar(''), null);
});

// ── enchufado al embudo ──────────────────────────────────────────────────────

test('si el modelo se pone a agendar, sale el link en vez de la promesa', async () => {
  const s = await conLead({
    openai: stubOpenAI({ respuestas: { conversacion: 'Perfecto, agendo la videollamada para el martes a las 10' } }),
  });

  await s.servicioLeads.registrarRespuesta(TEL, 'quiero ver');
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === TEL).map((e) => e.texto);
  assert.ok(!alLead.some((t) => /agendo la videollamada/.test(t)), 'la promesa no sale');
  assert.ok(alLead.some((t) => /link_reunion/.test(t)), 'sale el link, que es lo único que agenda');
  assert.equal(s.repo.leadPorTelefono(TEL).fsm_state, S.MEETING_LINK_SENT);
});

test('pero si YA tiene reunión, confirmarla es la verdad', async () => {
  const s = await conLead({
    openai: stubOpenAI({ respuestas: { conversacion: 'Sí, quedó agendada. Nos vemos.' } }),
  });
  const l = s.repo.leadPorTelefono(TEL);
  s.repo.registrarReunion(l.id, {
    meetingTime: '2026-09-01T16:00:00.000Z', meetingUrl: 'https://meet.google.com/x',
    ahoraIso: new Date().toISOString(),
  });
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.servicioLeads.registrarRespuesta(TEL, '¿quedó agendado?');
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === TEL).map((e) => e.texto);
  assert.ok(alLead.some((t) => /quedó agendada/.test(t)), 'se le puede confirmar lo que sí existe');
});

/**
 * Con AGENDA_OFRECE_HORARIOS el bot SI reserva, asi que ahi pedir un horario y
 * confirmarlo es su trabajo, no una mentira.
 */
test('con el bot agendando de verdad, el guardia no se mete', async () => {
  const s = await conLead({
    openai: stubOpenAI({ respuestas: { conversacion: '¿Qué día te viene bien?' } }),
    AGENDA_OFRECE_HORARIOS: 'true',
  });

  await s.servicioLeads.registrarRespuesta(TEL, 'quiero ver');
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === TEL).map((e) => e.texto);
  assert.ok(alLead.some((t) => /Qué día te viene bien/.test(t)));
});
