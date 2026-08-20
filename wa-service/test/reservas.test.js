'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { conLead } = require('./helpers');
const { crearVigilanteDeReservas } = require('../src/agenda/reservas');
const { S } = require('../src/funnel/states');

const TEL = '59899123456';

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
  inicio = '2026-09-01T13:00:00-03:00',
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
