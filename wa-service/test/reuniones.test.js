'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { CLAVE, LEAD, montar } = require('./helpers');

const AM = '59899000111';
const LEAD_TEL = '59899123456';

/** Servicio con el lead ya dado de alta y la cola limpia. */
async function conLeadLimpio(extra) {
  const s = await montar(extra);
  await s.servicioLeads.alta(LEAD);
  await s.cola.vacia();
  s.proveedor.limpiar();
  return s;
}

const agendar = (s, cuando, url) =>
  s.app.inject({
    method: 'POST', url: '/meetings', headers: { 'x-api-key': CLAVE },
    payload: { telefono: LEAD_TEL, meeting_time: cuando.toISOString(), meeting_url: url },
  });

const enHoras = (h) => new Date(Date.now() + h * 3600_000);

test('agendar cancela el follow-up: solo se insiste al que no agendo', async () => {
  const s = await conLeadLimpio();
  assert.equal(
    s.repo.db.prepare("SELECT COUNT(*) n FROM jobs WHERE type='followup' AND status='pending'").get().n,
    1
  );

  const r = await agendar(s, enHoras(48), 'https://meet.google.com/abc');
  assert.equal(r.statusCode, 200);
  assert.equal(r.json().followup_cancelado, true);

  assert.equal(
    s.repo.db.prepare("SELECT COUNT(*) n FROM jobs WHERE type='followup' AND status='pending'").get().n,
    0, 'el follow-up queda cancelado'
  );

  // Y a las 73h no le llega ningun "¿seguís interesado?".
  await s.cola.vacia();
  s.proveedor.limpiar();
  await s.scheduler.correrVencidos(new Date(Date.now() + 73 * 3600_000));
  await s.cola.vacia();
  const alLead = s.proveedor.getEnviados().filter((e) => e.to === LEAD_TEL);
  assert.ok(!alLead.some((m) => /seguís interesado/i.test(m.texto)));
});

test('con la reunion a 48h se programan los dos recordatorios', async () => {
  const s = await conLeadLimpio();
  const r = await agendar(s, enHoras(48));

  assert.deepEqual(r.json().recordatorios, ['reminder_24h', 'reminder_30m']);
});

test('si falta menos de un dia, solo se programa el de 30 minutos', async () => {
  // "Te recuerdo que es mañana" no tiene sentido si la reunion es en 3 horas.
  const s = await conLeadLimpio();
  const r = await agendar(s, enHoras(3));

  assert.deepEqual(r.json().recordatorios, ['reminder_30m']);
});

test('una reunion en 10 minutos no programa nada', async () => {
  const s = await conLeadLimpio();
  const r = await agendar(s, new Date(Date.now() + 10 * 60_000));

  assert.deepEqual(r.json().recordatorios, []);
});

test('el recordatorio del dia antes sale a las 24h de la reunion', async () => {
  const s = await conLeadLimpio();
  const reunion = enHoras(48);
  await agendar(s, reunion, 'https://meet.google.com/abc');
  await s.cola.vacia();
  s.proveedor.limpiar();

  // 25 horas antes de la reunion: todavia no.
  assert.equal(await s.scheduler.correrVencidos(new Date(reunion.getTime() - 25 * 3600_000)), 0);

  // 23 horas antes: sale.
  assert.equal(await s.scheduler.correrVencidos(new Date(reunion.getTime() - 23 * 3600_000)), 1);
  await s.cola.vacia();

  const msg = s.proveedor.getEnviados().find((e) => e.to === LEAD_TEL);
  // La fecha y el link ya no se arman en codigo: se le pasan a la IA como
  // contexto. Como suena el mensaje se mide en evals/.
  assert.equal(msg.texto, '[recordatorio_dia_antes]');
});

test('el recordatorio de 30 minutos sale justo antes', async () => {
  const s = await conLeadLimpio();
  const reunion = enHoras(3);
  await agendar(s, reunion, 'https://meet.google.com/xyz');
  await s.cola.vacia();
  s.proveedor.limpiar();

  assert.equal(await s.scheduler.correrVencidos(new Date(reunion.getTime() - 45 * 60_000)), 0);
  assert.equal(await s.scheduler.correrVencidos(new Date(reunion.getTime() - 20 * 60_000)), 1);
  await s.cola.vacia();

  const msg = s.proveedor.getEnviados().find((e) => e.to === LEAD_TEL);
  assert.equal(msg.texto, '[recordatorio_30min]');
});

test('a la IA se le pasan la fecha y el link para el recordatorio', async () => {
  // El texto lo escribe ella, pero los datos duros salen del lead: si no se le
  // pasan, el recordatorio no puede decir a que hora es ni por donde entrar.
  const { construirRedaccion } = require('../src/ia/prompt');

  const prompt = construirRedaccion(
    { nombre: 'Martín' }, 'recordatorio_30min', 'x',
    'La reunión es mañana a las 15:00. El link para entrar es https://meet.google.com/xyz'
  );
  assert.match(prompt, /meet\.google\.com\/xyz/);
  assert.match(prompt, /15:00/);
});

test('al AM le avisa que se agendo, con fecha y link', async () => {
  const s = await conLeadLimpio();
  await agendar(s, enHoras(48), 'https://meet.google.com/abc');
  await s.cola.vacia();

  const alAM = s.proveedor.getEnviados().find((e) => e.to === AM);
  assert.match(alAM.texto, /Reunión agendada/);
  assert.match(alAM.texto, /Martín Pereyra/);
  assert.match(alAM.texto, /meet\.google\.com\/abc/);
  assert.match(alAM.texto, /wa\.me\/59899123456/);
});

test('si el lead se da de baja no le llegan los recordatorios', async () => {
  const s = await conLeadLimpio();
  const reunion = enHoras(48);
  await agendar(s, reunion);
  await s.cola.vacia();

  await s.servicioLeads.registrarRespuesta(LEAD_TEL, 'baja');
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(new Date(reunion.getTime() - 20 * 60_000));
  await s.cola.vacia();
  assert.equal(s.proveedor.getEnviados().length, 0);
});

test('rechaza una fecha invalida o un lead que no existe', async () => {
  const s = await conLeadLimpio();

  const malaFecha = await s.app.inject({
    method: 'POST', url: '/meetings', headers: { 'x-api-key': CLAVE },
    payload: { telefono: LEAD_TEL, meeting_time: 'el jueves' },
  });
  assert.equal(malaFecha.statusCode, 400);
  assert.match(malaFecha.json().error, /meeting_time/);

  const sinLead = await s.app.inject({
    method: 'POST', url: '/meetings', headers: { 'x-api-key': CLAVE },
    payload: { telefono: '099888777', meeting_time: enHoras(24).toISOString() },
  });
  assert.equal(sinLead.statusCode, 404);
});

test('/meetings pide x-api-key', async () => {
  const s = await conLeadLimpio();
  const r = await s.app.inject({
    method: 'POST', url: '/meetings',
    payload: { telefono: LEAD_TEL, meeting_time: enHoras(24).toISOString() },
  });
  assert.equal(r.statusCode, 401);
});
