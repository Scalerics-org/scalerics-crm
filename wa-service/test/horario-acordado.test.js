'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { crearLimites } = require('../src/outbound/limits');
const { cargar } = require('../src/config');
const { crearTextos } = require('../src/templates/funnel');
const { ADMIN, montar, conLead } = require('./helpers');

const TEL = '59899123456';
const CERRADO = { BUSINESS_HOURS: '09:00-19:00', BUSINESS_DAYS: 'mon-sat' };
// Domingo 03:00 en Montevideo: fuera de horario por dia y por hora.
const DOMINGO_MADRUGADA = new Date('2026-08-09T06:00:00Z');

function limites(contadores = {}) {
  const cfg = cargar({ WA_API_KEY: 'una-clave-bien-larga-1234', TZ: 'America/Montevideo', MAX_MSGS_PER_HOUR: '3' }); // gitleaks:allow — clave de mentira para el test
  const repo = {
    enviosDesde: () => contadores.envios || 0,
    nuevosDesde: () => 0,
  };
  return crearLimites({ repo, cfg, logger: null });
}

// ── la tercera puerta del horario ────────────────────────────────────────────

test('lo acordado sale fuera de horario', () => {
  assert.equal(limites().permitido({ ahora: DOMINGO_MADRUGADA }).ok, false, 'lo comun espera');
  assert.equal(limites().permitido({ esAcordado: true, ahora: DOMINGO_MADRUGADA }).ok, true);
});

/**
 * Saltear el horario no es saltear el cupo: el tope anti-baneo sigue corriendo.
 */
test('pero lo acordado sigue contando para el tope por hora', () => {
  const r = limites({ envios: 3 }).permitido({ esAcordado: true, ahora: DOMINGO_MADRUGADA });
  assert.equal(r.ok, false);
  assert.equal(r.motivo, 'limite por hora');
});

// ── la presentacion al que escribe de noche ──────────────────────────────────

/**
 * Susana escribio un domingo 18:53, el bot le contesto al instante y
 * charlaron; al otro dia 09:19 le llego "¡Buenas! Soy el agente comercial…",
 * despues de toda la conversacion.
 */
test('al que escribe fuera de horario la presentacion le llega antes que la respuesta', async () => {
  const s = await montar(CERRADO, DOMINGO_MADRUGADA);
  const { BIENVENIDA } = crearTextos();

  await s.servicioLeads.registrarRespuesta('59899555002', '¡Hola! ¿cómo puedo agendar mi demo?', 'Susana');
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899555002');
  assert.ok(alLead.length >= 2, 'presentacion y respuesta, en el momento');
  assert.equal(alLead[0].texto, BIENVENIDA, 'la presentacion va primero');
});

// ── lo que escribe una persona desde el panel ────────────────────────────────

/**
 * El 14-9 Juan escribio a las 22:36 desde el CRM y la cola se lo guardo hasta
 * las 9 de la mañana. La decision de mandarlo a esa hora fue suya.
 */
test('lo que se manda desde el panel sale aunque sea fuera de horario', async () => {
  const s = await conLead(CERRADO, undefined, DOMINGO_MADRUGADA);

  const r = await s.app.inject({
    method: 'POST', url: '/api/send',
    headers: { 'x-admin-token': ADMIN },
    payload: { phone: TEL, text: 'te escribo yo' },
  });
  assert.equal(r.statusCode, 200);
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === TEL);
  assert.deepEqual(alLead.map((e) => e.texto), ['te escribo yo']);
});

// ── los recordatorios ────────────────────────────────────────────────────────

/** Le arma al lead una reunion y deja vencido el recordatorio pedido. */
function reunionCon(s, tipo, horasHastaReunion, reloj) {
  const l = s.repo.leadPorTelefono(TEL);
  s.repo.registrarReunion(l.id, {
    meetingTime: new Date(reloj.getTime() + horasHastaReunion * 3600_000).toISOString(),
    meetingUrl: 'https://meet.google.com/x',
    ahoraIso: reloj.toISOString(),
  });
  const vencido = new Date(reloj.getTime() - 60_000).toISOString();
  s.repo.encolarJob(l.id, tipo, vencido);
  s.repo.db.prepare('UPDATE jobs SET run_at = ? WHERE lead_id = ? AND type = ?').run(vencido, l.id, tipo);
  return l;
}

/**
 * CD Montevideo tenia reunion el lunes 10:00. El recordatorio del dia antes
 * caia el domingo, que no es habil, y la cola lo guardo hasta el lunes 09:04:
 * le llego 56 minutos antes diciendo "mañana lunes 14".
 */
test('el recordatorio sale en su momento aunque caiga en domingo', async () => {
  const s = await conLead(CERRADO, undefined, DOMINGO_MADRUGADA);
  reunionCon(s, 'reminder_24h', 20, DOMINGO_MADRUGADA);
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(DOMINGO_MADRUGADA);
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().filter((e) => e.to === TEL).length, 1);
});

/**
 * Lo acordado es solo lo que el lead reservo. Un follow-up aparece sin que
 * nadie lo pida, y ese si espera a la apertura.
 */
test('un follow-up en cambio sigue esperando la apertura', async () => {
  const s = await conLead(CERRADO, undefined, DOMINGO_MADRUGADA);
  await s.cola.vacia();
  s.proveedor.limpiar();

  s.cola.encolar({ to: TEL, texto: '¿seguís interesado?', kind: 'followup', leadId: s.repo.leadPorTelefono(TEL).id });
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().filter((e) => e.to === TEL).length, 0);
});

/**
 * Un recordatorio dice cuanto falta. Si por lo que sea no pudo salir antes de
 * la reunion, no se guarda para despues: se tira.
 */
test('un recordatorio que no pudo salir antes de la reunion se descarta', async () => {
  const s = await montar({ MAX_MSGS_PER_HOUR: '1' });
  const reloj = new Date();
  // El cupo de la hora ya esta gastado: el recordatorio no puede salir ahora.
  s.repo.registrarEnvio('59899000999', false);

  s.cola.encolar({
    to: TEL, texto: 'la videollamada es en 30 minutos', kind: 'manual',
    acordado: true, venceEnMin: 5, encoladoEn: reloj,
  });
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().filter((e) => e.to === TEL).length, 0, 'no sale');
  assert.equal(s.cola.pendientes(), 0, 'y no queda esperando: el reintento seria despues de que venza');
});
