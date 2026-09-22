'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { conLead } = require('./helpers');
const { crearVigilanteDeReservas, mismoInstante } = require('../src/agenda/reservas');
const { S } = require('../src/funnel/states');

const TEL = '59899123456';

/**
 * Relativo a hoy y calculado UNA vez.
 *
 * Con una fecha fija, al pasar los dias la reunion se acerca sola y un buen dia
 * queda a menos de 24 horas, que es el umbral del recordatorio del dia antes:
 * el test empezaba a fallar sin que nadie hubiera tocado nada. Y calculandolo
 * en cada llamada, dos reservas "iguales" salian con milisegundos distintos y
 * el vigilante las tomaba por reservas diferentes.
 */
const EN_CINCO_DIAS = new Date(Date.now() + 5 * 86_400_000).toISOString();

/**
 * Una reserva de Calendly tal como llega al Google Calendar.
 *
 * El formato no es inventado: se copio de un evento real. Las respuestas del
 * formulario aparecen como "Pregunta: respuesta" en medio de texto generico de
 * Calendly, y ahi viene el telefono que permite cruzarlo con el lead.
 */
function reservaDeCalendly({
  id = 'ev_1',
  telefono = '+598 99 123 456',
  inicio = EN_CINCO_DIAS,
  summary = 'Martin/Scalerics y Contacto Scalerics',
  status = 'confirmed',
} = {}) {
  return {
    id,
    status,
    summary,
    start: { dateTime: inicio },
    hangoutLink: 'https://meet.google.com/abc-defg-hij',
    description: [
      'Nombre del evento',
      'Consultoria gratuita de Scalerics',
      '',
      'Ubicación: Esto es una conferencia web de Google Meet.',
      'Empresa: Inmobiliaria Pereyra',
      '¿Qué necesitás?: Página web',
      'Teléfono / WhatsApp: ' + telefono,
      '',
      'Cancelar: https://calendly.com/cancellations/abc',
      'Desarrollado por Calendly.com',
    ].join('\n'),
  };
}

/** El vigilante contra un calendario de mentira. */
function conCalendario(s, eventos) {
  return crearVigilanteDeReservas({
    agenda: { activo: true, listarEventos: async () => eventos },
    repo: s.repo,
    servicioLeads: s.servicioLeads,
    cfg: s.cfg,
    logger: null,
  });
}

const pendientesDe = (s, leadId, tipo) => s.repo.db
  .prepare("SELECT COUNT(*) AS n FROM jobs WHERE lead_id = ? AND type LIKE ? AND status = 'pending'")
  .get(leadId, tipo).n;

test('una reserva de Calendly programa los dos recordatorios', async () => {
  const s = await conLead();
  const r = await conCalendario(s, [reservaDeCalendly()]).revisar();
  assert.equal(r.agendadas, 1);

  const l = s.repo.leadPorTelefono(TEL);
  assert.ok(l.meeting_time, 'quedo registrada la reunion');
  assert.equal(l.meeting_url, 'https://meet.google.com/abc-defg-hij');

  const tipos = s.repo.db.prepare("SELECT type FROM jobs WHERE lead_id = ? AND status = 'pending'")
    .all(l.id).map((j) => j.type);
  assert.ok(tipos.includes('reminder_24h'), 'el del dia antes');
  assert.ok(tipos.includes('reminder_30m'), 'el de media hora antes');
});

test('el seguimiento se cancela: ya agendo, no hay que insistirle', async () => {
  const s = await conLead();
  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(pendientesDe(s, l.id, 'followup'), 1, 'el alta le dejo uno');

  await conCalendario(s, [reservaDeCalendly()]).revisar();

  assert.equal(pendientesDe(s, l.id, 'followup'), 0);
});

/**
 * El vigilante mira el mismo calendario cada cinco minutos y ve la misma
 * reserva una y otra vez. Sin esto le avisaria al equipo en cada vuelta.
 */
test('la misma reserva vista dos veces no se registra dos veces', async () => {
  const s = await conLead();
  const v = conCalendario(s, [reservaDeCalendly()]);

  assert.equal((await v.revisar()).agendadas, 1);
  await s.cola.vacia();
  s.proveedor.limpiar();

  assert.equal((await v.revisar()).agendadas, 0, 'la segunda vuelta no hace nada');
  await s.cola.vacia();
  assert.equal(s.proveedor.getEnviados().length, 0, 'y no se vuelve a avisar al equipo');
});

test('si mueve la reunion, se reprograman los recordatorios', async () => {
  const s = await conLead();
  await conCalendario(s, [reservaDeCalendly()]).revisar();

  const movida = reservaDeCalendly({ inicio: '2026-09-05T16:00:00-03:00' });
  assert.equal((await conCalendario(s, [movida]).revisar()).agendadas, 1, 'la hora cambio');
  assert.match(s.repo.leadPorTelefono(TEL).meeting_time, /2026-09-05/);
});

/**
 * Calendly no borra la reunion cancelada: le pone "Cancelado:" adelante y la
 * deja en el calendario. Si no se mirara, el bot le recordaria una reunion que
 * la persona ya cancelo.
 */
test('una reunion cancelada cancela los recordatorios', async () => {
  const s = await conLead();
  await conCalendario(s, [reservaDeCalendly()]).revisar();

  const l = s.repo.leadPorTelefono(TEL);
  assert.ok(pendientesDe(s, l.id, 'reminder%') > 0);

  const cancelada = reservaDeCalendly({ summary: 'Cancelado: Martin/Scalerics y Contacto Scalerics' });
  assert.equal((await conCalendario(s, [cancelada]).revisar()).canceladas, 1);

  assert.equal(pendientesDe(s, l.id, 'reminder%'), 0, 'no se le recuerda una reunion que no existe');
  assert.equal(s.repo.leadPorTelefono(TEL).meeting_time, null);
});

test('una reunion cancelada de otra reserva no borra la que tiene en pie', async () => {
  const s = await conLead();
  await conCalendario(s, [reservaDeCalendly({ id: 'ev_nueva' })]).revisar();

  const otra = reservaDeCalendly({ id: 'ev_vieja', summary: 'Cancelado: lo que sea' });
  assert.equal((await conCalendario(s, [otra]).revisar()).canceladas, 0);
  assert.ok(s.repo.leadPorTelefono(TEL).meeting_time, 'la reunion buena sigue en pie');
});

test('un evento sin telefono se ignora', async () => {
  const s = await conLead();
  const sinTel = reservaDeCalendly();
  sinTel.description = 'Nombre del evento\nConsultoria gratuita\n\nDesarrollado por Calendly.com';

  const r = await conCalendario(s, [sinTel]).revisar();
  assert.equal(r.conTelefono, 0);
  assert.equal(r.agendadas, 0);
});

test('una reserva de alguien que nunca escribio por WhatsApp se ignora', async () => {
  const s = await conLead();
  const r = await conCalendario(s, [reservaDeCalendly({ telefono: '+598 91 111 111' })]).revisar();

  assert.equal(r.conTelefono, 1, 'el telefono se leyo bien');
  assert.equal(r.agendadas, 0, 'pero no hay lead con ese numero');
});

test('un numero de afuera de Uruguay tambien cruza', async () => {
  // Un cliente argentino dejo un +54 en el formulario. Si se le forzara el
  // codigo de pais uruguayo, fallaria el cruce justo con el de mas lejos.
  const s = await conLead(undefined, {
    external_id: 'ar', nombre: 'Franco', telefono: '+54 11 4069-8252', origen: 'form',
  });
  await s.cola.vacia();

  const r = await conCalendario(s, [reservaDeCalendly({ telefono: '+54 11 4069-8252' })]).revisar();
  assert.equal(r.agendadas, 1);
});

/**
 * Quien reserva queda en SCHEDULED, no en "tiene el link y todavia no reservo".
 *
 * Sin esto, el que agendaba y despues escribia recibia "ya tenes el link" y al
 * segundo mensaje lo derivaban a una persona por insistir: justo al que hizo lo
 * que se le habia pedido.
 */
test('el que agendo queda marcado como agendado', async () => {
  const s = await conLead();
  await conCalendario(s, [reservaDeCalendly()]).revisar();

  assert.equal(s.repo.leadPorTelefono(TEL).fsm_state, S.SCHEDULED);
});

test('y si despues escribe, el bot le contesta en vez de derivarlo', async () => {
  const s = await conLead();
  await conCalendario(s, [reservaDeCalendly()]).revisar();
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.servicioLeads.registrarRespuesta(TEL, 'una consulta antes de la reunion');
  await s.cola.vacia();

  assert.notEqual(s.repo.leadPorTelefono(TEL).fsm_state, S.HUMAN_QUEUED, 'no lo deriva');
  assert.ok(s.proveedor.getEnviados().some((e) => e.to === TEL), 'le contesta');
});

test('sin agenda conectada no hace nada, y no rompe', async () => {
  const s = await conLead();
  const v = crearVigilanteDeReservas({
    agenda: { activo: false, listarEventos: async () => { throw new Error('no deberia llamarse'); } },
    repo: s.repo, servicioLeads: s.servicioLeads, cfg: s.cfg, logger: null,
  });
  assert.equal((await v.revisar()).agendadas, 0);
});

test('si Google falla, se sigue vivo hasta la proxima vuelta', async () => {
  const s = await conLead();
  const v = crearVigilanteDeReservas({
    agenda: { activo: true, listarEventos: async () => { throw new Error('503'); } },
    repo: s.repo, servicioLeads: s.servicioLeads, cfg: s.cfg, logger: null,
  });
  assert.equal((await v.revisar()).agendadas, 0, 'no explota');
});

/**
 * El bug que le mando un aviso al equipo cada cinco minutos durante horas.
 *
 * Un lead con DOS reuniones agendadas: cada una veia el meeting_event_id de la
 * otra, se daba por no registrada y se registraba de nuevo. Ping-pong en cada
 * vuelta del vigilante, con su aviso cada vez.
 */
test('un lead con dos reuniones no hace que se pisen entre si', async () => {
  const s = await conLead();
  const dos = [
    reservaDeCalendly({ id: 'ev_lejos', inicio: '2026-09-05T12:00:00-03:00' }),
    reservaDeCalendly({ id: 'ev_cerca', inicio: '2026-09-01T18:00:00-03:00' }),
  ];
  const v = conCalendario(s, dos);

  assert.equal((await v.revisar()).agendadas, 1, 'se registra una sola: la mas proxima');
  assert.match(s.repo.leadPorTelefono(TEL).meeting_time, /2026-09-01/);

  await s.cola.vacia();
  s.proveedor.limpiar();

  // Tres vueltas mas del vigilante, como pasaria cada cinco minutos.
  for (let i = 0; i < 3; i++) assert.equal((await v.revisar()).agendadas, 0);

  await s.cola.vacia();
  assert.equal(s.proveedor.getEnviados().length, 0, 'y ni un aviso de mas al equipo');
});

test('una reserva ya registrada no se vuelve a registrar, ni tras reiniciar', async () => {
  const s = await conLead();
  await conCalendario(s, [reservaDeCalendly()]).revisar();
  await s.cola.vacia();
  s.proveedor.limpiar();

  // Un vigilante nuevo, como despues de un deploy: la memoria esta en la base.
  const otro = conCalendario(s, [reservaDeCalendly()]);
  assert.equal((await otro.revisar()).agendadas, 0);

  await s.cola.vacia();
  assert.equal(s.proveedor.getEnviados().length, 0);
});

test('pero si mueven la reunion, esa si es nueva', async () => {
  const s = await conLead();
  await conCalendario(s, [reservaDeCalendly()]).revisar();

  const movida = reservaDeCalendly({ inicio: '2026-09-09T15:00:00-03:00' });
  assert.equal((await conCalendario(s, [movida]).revisar()).agendadas, 1);
  assert.match(s.repo.leadPorTelefono(TEL).meeting_time, /2026-09-09/);
});

/**
 * Paso de verdad: un lead derivado por abandono reservo en Calendly, escribio
 * "Gracias!" y el bot no le contesto. Seguia marcado como que lo atendia una
 * persona, aunque el motivo —que habia dejado de contestar— se le habia caido
 * solo al reservar.
 */
test('al que se derivo por abandono y despues agenda, se le devuelve el bot', async () => {
  const s = await conLead();
  const l = s.repo.leadPorTelefono(TEL);
  s.repo.actualizarFunnel(l.id, { human_requested: 1, motivo_derivacion: 'abandono' });

  await conCalendario(s, [reservaDeCalendly()]).revisar();

  const f = s.repo.leadPorTelefono(TEL);
  assert.equal(f.human_requested, 0, 'vuelve a atenderlo el bot');
  assert.equal(f.motivo_derivacion, null);
});

test('pero al que se derivo por una queja, agendar no le resuelve nada', async () => {
  const s = await conLead();
  const l = s.repo.leadPorTelefono(TEL);
  s.repo.actualizarFunnel(l.id, { human_requested: 1, motivo_derivacion: 'queja' });

  await conCalendario(s, [reservaDeCalendly()]).revisar();

  const f = s.repo.leadPorTelefono(TEL);
  assert.equal(f.human_requested, 1, 'esa conversacion sigue siendo de la persona que la tomo');
});

test('y despues de agendar, si escribe, el bot le contesta', async () => {
  const s = await conLead();
  const l = s.repo.leadPorTelefono(TEL);
  s.repo.actualizarFunnel(l.id, { human_requested: 1, motivo_derivacion: 'abandono' });

  await conCalendario(s, [reservaDeCalendly()]).revisar();
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.servicioLeads.registrarRespuesta(TEL, 'Gracias!');
  await s.cola.vacia();

  assert.ok(s.proveedor.getEnviados().some((e) => e.to === TEL), 'no se queda mudo');
});

/**
 * El equipo reserva a nombre del cliente y a veces pone un telefono propio en
 * el formulario. El bot lo lee como "el que agendo" y le manda a esa persona el
 * recordatorio del cliente.
 *
 * Paso con una reunion de La Vaca Encantada: el formulario tenia el numero del
 * desarrollador y el recordatorio le llego a el, un dia antes, como si fuera el
 * cliente.
 */
test('una reserva con el telefono de alguien del equipo no se registra', async () => {
  const s = await conLead({ EQUIPO_TELEFONOS: TEL });

  const r = await conCalendario(s, [reservaDeCalendly()]).revisar();

  assert.equal(r.agendadas, 0, 'no se le programa un recordatorio a alguien del equipo');
  assert.equal(s.repo.leadPorTelefono(TEL).meeting_time, null);
});

test('pero la de un cliente sigue registrandose igual', async () => {
  const s = await conLead({ EQUIPO_TELEFONOS: '59899000111' });

  const r = await conCalendario(s, [reservaDeCalendly()]).revisar();
  assert.equal(r.agendadas, 1);
});

test('mismoInstante: el mismo instante en dos formatos distintos', () => {
  assert.equal(mismoInstante('2026-09-23T13:00:00.000Z', '2026-09-23T10:00:00-03:00'), true);
});

test('mismoInstante: formatos distintos, instante distinto', () => {
  assert.equal(mismoInstante('2026-09-23T13:00:00.000Z', '2026-09-23T13:00:00-03:00'), false);
});

test('mismoInstante: si falta alguno de los dos, no es el mismo instante', () => {
  assert.equal(mismoInstante(null, '2026-09-23T10:00:00-03:00'), false);
  assert.equal(mismoInstante('2026-09-23T13:00:00.000Z', null), false);
  assert.equal(mismoInstante(null, null), false);
  assert.equal(mismoInstante(undefined, undefined), false);
});

test('mismoInstante: una fecha invalida no explota, solo no es el mismo instante', () => {
  assert.equal(mismoInstante('esto no es una fecha', '2026-09-23T10:00:00-03:00'), false);
  assert.equal(mismoInstante('2026-09-23T13:00:00.000Z', 'tampoco esto'), false);
});

function conReunionDelBot(s, { eventId = 'ev_bot', meetingTime = '2026-09-23T13:00:00.000Z' } = {}) {
  const l = s.repo.leadPorTelefono(TEL);
  s.repo.registrarReunion(l.id, {
    meetingTime, meetingUrl: 'https://meet.google.com/abc-defg-hij', ahoraIso: '2026-09-01T12:00:00.000Z',
  });
  s.repo.actualizarFunnel(l.id, { meeting_event_id: eventId });
  return s.repo.leadPorTelefono(TEL);
}

/**
 * El bug de Patricia (22-9): el vigilante lee "WhatsApp: wa.me/…" en la
 * descripcion del evento que crea EL PROPIO BOT y lo registra de nuevo,
 * pisando meeting_booked_at y mandando "Reunión agendada" dos veces al equipo.
 */
test('la reunion que agendo el propio bot, misma hora en formato de Google, no avisa ni pisa meeting_booked_at', async () => {
  const s = await conLead();
  const antes = conReunionDelBot(s);

  const r = await conCalendario(
    s, [reservaDeCalendly({ id: 'ev_bot', inicio: '2026-09-23T10:00:00-03:00' })]
  ).revisar();

  assert.equal(r.agendadas, 0, 'ya estaba registrada, no es una reserva nueva');
  const despues = s.repo.leadPorTelefono(TEL);
  assert.equal(despues.meeting_booked_at, antes.meeting_booked_at, 'no se pisa');
  assert.equal(despues.meeting_time, antes.meeting_time);

  await s.cola.vacia();
  assert.equal(s.proveedor.getEnviados().length, 0, 'no se avisa dos veces al equipo');
});

test('la reunion del bot movida de hora si es una reserva nueva', async () => {
  const s = await conLead();
  conReunionDelBot(s);

  const r = await conCalendario(
    s, [reservaDeCalendly({ id: 'ev_bot', inicio: '2026-09-24T10:00:00-03:00' })]
  ).revisar();

  assert.equal(r.agendadas, 1, 'la hora cambio: hay que avisar de nuevo');
  assert.match(s.repo.leadPorTelefono(TEL).meeting_time, /2026-09-24/);
});

test('una reserva de Calendly de verdad, con otro event id, sigue funcionando', async () => {
  const s = await conLead();
  conReunionDelBot(s, { eventId: 'ev_bot' });
  await s.cola.vacia();
  s.proveedor.limpiar();

  // El lead reservo OTRA consultoria por Calendly, evento distinto.
  const r = await conCalendario(
    s, [reservaDeCalendly({ id: 'ev_calendly_de_verdad', inicio: '2026-09-30T10:00:00-03:00' })]
  ).revisar();

  assert.equal(r.agendadas, 1);
  assert.match(s.repo.leadPorTelefono(TEL).meeting_time, /2026-09-30/);
});

test('meeting_time invalido no rompe el vigilante', async () => {
  const s = await conLead();
  const l = s.repo.leadPorTelefono(TEL);
  s.repo.actualizarFunnel(l.id, { meeting_event_id: 'ev_bot', meeting_time: 'no-es-una-fecha' });

  await assert.doesNotReject(
    conCalendario(s, [reservaDeCalendly({ id: 'ev_bot', inicio: '2026-09-23T10:00:00-03:00' })]).revisar()
  );
});
