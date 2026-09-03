'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { prometeAgendar } = require('../src/ia/promesas');
const { conLead, stubModelo } = require('./helpers');
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
    modelo: stubModelo({ respuestas: { conversacion: 'Perfecto, agendo la videollamada para el martes a las 10' } }),
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
    modelo: stubModelo({ respuestas: { conversacion: 'Sí, quedó agendada. Nos vemos.' } }),
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
 * El guardia corre SIEMPRE, tambien con los horarios reales prendidos.
 *
 * Este test afirmaba lo contrario: que con AGENDA_OFRECE_HORARIOS el embudo
 * agenda bien y el guardia sobra. El razonamiento tenia un agujero, y el 3-9 lo
 * encontro: vale solo si el lead LLEGA a la etapa de horarios. Ese dijo que su
 * negocio no tenia nombre todavia, business_name quedo vacio, el descubrimiento
 * no cerro nunca, y el modelo se puso a negociar fechas por su cuenta:
 *
 *   → Escribime cualquier día y hora que te venga bien entre hoy y el viernes
 *   ← Lunes a las 12 de la noche
 *   → Perfecto, lunes a las 12 de la noche anotado.
 *
 * Medianoche, fuera de la franja de atencion, sin nada en el calendario.
 * Prender los horarios reales habia apagado la unica proteccion contra eso.
 *
 * Lo que el embudo SI hace bien —mostrar horarios y agendar— sale por el
 * redactor, no por esta parte de la conversacion, asi que el guardia no lo toca.
 */
test('el guardia corre aunque los horarios reales esten prendidos', async () => {
  const s = await conLead({
    modelo: stubModelo({ respuestas: { conversacion: '¿Qué día te viene bien?' } }),
    AGENDA_OFRECE_HORARIOS: 'true',
  });

  await s.servicioLeads.registrarRespuesta(TEL, 'quiero ver');
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === TEL).map((e) => e.texto);
  assert.ok(!alLead.some((t) => /Qué día te viene bien/.test(t)),
    'no sale: el bot no coordina horarios por su cuenta');
});

/**
 * La salida lateral del guardia se salteaba el aviso al equipo.
 *
 * avisarDesenlace vive solo adentro de case S.SCORED. Yendo derecho a
 * MEETING_LINK_SENT, el lead recibia el link y del lado de adentro no se
 * enteraba nadie: ni la ficha al equipo ni el CRM.
 */
test('y el equipo igual se entera de que hay un lead con reunión ofrecida', async () => {
  const s = await conLead({
    modelo: stubModelo({ respuestas: { conversacion: '¿Qué día te viene bien?' } }),
  });
  s.proveedor.limpiar();

  await s.servicioLeads.registrarRespuesta(TEL, 'quiero ver');
  await s.cola.vacia();

  const alEquipo = s.proveedor.getEnviados().filter((e) => e.to === '59899000111');
  assert.ok(alEquipo.some((e) => /calificado/i.test(e.texto)), 'la ficha sale igual');
});

/**
 * Dos veces seguidas el 3-9, con los horarios reales prendidos:
 *
 *   ← Bueno dale
 *   → Perfecto, quedás agendado para el viernes 11 de setiembre a las 10:00.
 *     Te mando el link de la videollamada 15 minutos antes.
 *
 *   ← 10:15
 *   → Perfecto, quedás con el equipo el viernes 4 de setiembre a las 10:15.
 *     Te llega el link de la videollamada 15 minutos antes.
 *
 * En la base no habia ninguna reunion: meeting_time en null, el lead en
 * HORARIOS_OFRECIDOS y el equipo sin enterarse. Dos personas esperando.
 *
 * Ninguna de las formas que la lista ya tenia agarra "quedás": estan todas
 * escritas sobre "quedó/queda/quedamos". Una lista de frases siempre va a tener
 * agujeros, asi que ademas se mira la forma: arranca confirmando y fija una
 * hora, sin preguntar nada.
 */
test('agarra el "quedás agendado" que se le escapó dos veces', () => {
  assert.ok(prometeAgendar('Perfecto, quedás agendado para el viernes 11 de setiembre a las 10:00. Te mando el link de la videollamada 15 minutos antes.'));
  assert.ok(prometeAgendar('Perfecto, quedás con el equipo el viernes 4 de setiembre a las 10:15. Te llega el link de la videollamada 15 minutos antes.'));
});

test('y cualquier otro arranque que confirme una hora sin preguntar nada', () => {
  assert.ok(prometeAgendar('Listo, entonces el lunes 7 a las 14:30. Te paso el link.'));
  assert.ok(prometeAgendar('Dale, te espero el jueves a las 11.'));
});

/**
 * Ofrecer no es confirmar. Un mensaje que termina preguntando le deja al lead
 * la decision, que es exactamente lo que tiene que pasar.
 */
test('ofrecer horarios y preguntar cuál sigue estando bien', () => {
  assert.equal(prometeAgendar('Perfecto. Tengo libre el viernes 4 a las 10:00, a las 14:30 o a las 18:30. ¿Cuál te sirve?'), null);
  // Pedir la hora sigue estando prohibido y lo agarra PIDE_HORARIO: el que
  // toma horarios es el embudo, no esta parte de la conversacion.
  assert.equal(prometeAgendar('Dale. El viernes 11 tenemos libre de 10:00 a 19:00, decime si te sirve alguno.'), null);
});
