'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { conLead, stubOpenAI } = require('./helpers');
const { S } = require('../src/funnel/states');
const { cuandoVolver, CAJONES } = require('../src/funnel/nurture');

const TEL = '59899123456';
const AM = '59899000111';

const COMPLETO = {
  business_name: 'Panadería PanesAhora', rubro: 'panadería', business_type: 'web',
};

/** Un lead que en su proximo mensaje dice que no es el momento. */
const stubQueAplaza = (aplaza = 'un_mes', frase = 'el mes que viene lo vemos') =>
  stubOpenAI({ datos: { ...COMPLETO, aplaza, aplaza_frase: frase } });

const jobs = (s, leadId, tipo) => s.repo.db
  .prepare("SELECT run_at FROM jobs WHERE lead_id = ? AND type = ? AND status = 'pending'")
  .all(leadId, tipo);

const enDias = (n) => new Date(Date.now() + n * 86_400_000);

test('el que dice "mas adelante" queda en pausa, no se le empuja mas', async () => {
  const s = await conLead({ openai: stubQueAplaza() });
  await s.servicioLeads.registrarRespuesta(TEL, 'me interesa pero el mes que viene lo vemos');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.fsm_state, S.NURTURE);
  assert.ok(l.nurture_desde, 'queda registrado cuando se pauso');
  assert.match(l.nurture_motivo, /el mes que viene/, 'y con que palabras lo dijo');
});

test('se le programa la vuelta para cuando dijo', async () => {
  const s = await conLead({ openai: stubQueAplaza('un_mes') });
  await s.servicioLeads.registrarRespuesta(TEL, 'el mes que viene');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  const programado = jobs(s, l.id, 'nurture');
  assert.equal(programado.length, 1);

  const cuando = new Date(programado[0].run_at);
  assert.ok(cuando > enDias(25) && cuando < enDias(35), `cerca del mes, dio ${cuando.toISOString()}`);
});

/**
 * Las dos cosas que hoy le pasan al que dice "mas adelante" y que esto viene a
 * arreglar: se le insiste a los tres dias, y a la hora se lo derivan al
 * comercial como si se hubiera ido.
 */
test('no se le insiste con el seguimiento de 72 horas', async () => {
  const s = await conLead({ openai: stubQueAplaza() });
  await s.servicioLeads.registrarRespuesta(TEL, 'el mes que viene');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(jobs(s, l.id, 'followup').length, 0);
});

test('y no lo derivan al comercial por dejar de escribir', async () => {
  const s = await conLead({ openai: stubQueAplaza() });
  await s.servicioLeads.registrarRespuesta(TEL, 'el mes que viene');
  await s.cola.vacia();
  s.proveedor.limpiar();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(jobs(s, l.id, 'abandono').length, 0, 'ni se le arma el reloj');

  await s.scheduler.correrVencidos(new Date(Date.now() + 2 * 3600_000));
  await s.cola.vacia();
  assert.ok(
    !s.proveedor.getEnviados().some((e) => e.to === AM),
    'no se le pasa al comercial alguien que ya dijo cuando volvia'
  );
});

test('estando en pausa no se le vuelve a mandar el link', async () => {
  const s = await conLead({ openai: stubQueAplaza() });
  await s.servicioLeads.registrarRespuesta(TEL, 'el mes que viene');
  await s.cola.vacia();
  s.proveedor.limpiar();

  // Escribe cualquier cosa. Tiene todos los datos cargados, asi que sin la
  // guarda esto lo llevaria derecho a recibir el link otra vez.
  await s.servicioLeads.registrarRespuesta(TEL, 'gracias');
  await s.cola.vacia();

  const textos = s.proveedor.getEnviados().filter((e) => e.to === TEL).map((e) => e.texto);
  assert.ok(!textos.some((t) => /link_reunion/.test(t)), 'no le empuja el link al que pidio tiempo');
});

test('si vuelve por su cuenta, se cancela el mensaje programado', async () => {
  // Aplaza en el primer turno y despues no: si aplazara siempre, el segundo
  // mensaje volveria a pausarlo y el test no probaria nada.
  const openai = stubQueAplaza();
  const original = openai.chat.completions.create;
  let turnos = 0;
  openai.chat.completions.create = async (args) => {
    const r = await original(args);
    turnos += 1;
    const llamada = r.choices?.[0]?.message?.tool_calls?.[0];
    if (turnos > 1 && llamada) {
      const a = JSON.parse(llamada.function.arguments);
      delete a.aplaza;
      delete a.aplaza_frase;
      llamada.function.arguments = JSON.stringify(a);
    }
    return r;
  };

  const s = await conLead({ openai });
  await s.servicioLeads.registrarRespuesta(TEL, 'el mes que viene');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(jobs(s, l.id, 'nurture').length, 1);

  await s.servicioLeads.registrarRespuesta(TEL, 'ya lo hablamos, arranquemos');
  await s.cola.vacia();

  assert.equal(jobs(s, l.id, 'nurture').length, 0, 'el mensaje programado ya no va');
  assert.equal(s.repo.leadPorTelefono(TEL).fsm_state, S.CONVERSANDO, 'y sale de la pausa');
});

/**
 * Ponerlo en pausa dejaria vivos el evento del calendario y sus dos
 * recordatorios: al que acaba de decir que no puede le llegaria "mañana tenes
 * la videollamada". Mover una reunion de verdad es de una persona.
 */
test('al que ya agendo y dice que no puede, lo levanta una persona', async () => {
  const s = await conLead({ openai: stubQueAplaza() });
  await s.servicioLeads.registrarRespuesta(TEL, 'hola');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  s.repo.registrarReunion(l.id, {
    meetingTime: '2026-09-01T16:00:00.000Z', meetingUrl: 'https://meet.google.com/x',
    ahoraIso: new Date().toISOString(),
  });
  s.proveedor.limpiar();

  await s.servicioLeads.registrarRespuesta(TEL, 'no voy a poder, lo vemos el mes que viene');
  await s.cola.vacia();

  const fresco = s.repo.leadPorTelefono(TEL);
  assert.equal(fresco.fsm_state, S.HUMAN_QUEUED);
  assert.equal(fresco.motivo_derivacion, 'reprograma');
  assert.notEqual(fresco.fsm_state, S.NURTURE, 'nunca a pausa: quedarian vivos los recordatorios');
});

test('cuando vence la pausa se le escribe y vuelve al embudo', async () => {
  const s = await conLead({ openai: stubQueAplaza() });
  await s.servicioLeads.registrarRespuesta(TEL, 'el mes que viene');
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(enDias(40));
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === TEL);
  assert.equal(alLead.length, 1);
  assert.match(alLead[0].texto, /nurture_vuelta/);
  assert.equal(s.repo.leadPorTelefono(TEL).fsm_state, S.CONVERSANDO, 'vuelve al embudo');
});

test('si ya habia vuelto, el mensaje programado no sale', async () => {
  const s = await conLead({ openai: stubQueAplaza() });
  await s.servicioLeads.registrarRespuesta(TEL, 'el mes que viene');
  await s.cola.vacia();

  // Alguien lo saca de la pausa a mano desde el CRM.
  const l = s.repo.leadPorTelefono(TEL);
  s.repo.actualizarFunnel(l.id, { fsm_state: S.CONVERSANDO });
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(enDias(40));
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().length, 0, 'nada de "quedamos en que te escribia"');
});

test('estar ocupado hoy no es aplazar: el modelo dice "no" y sigue normal', async () => {
  const s = await conLead({ openai: stubOpenAI({ datos: { ...COMPLETO, aplaza: 'no' } }) });
  await s.servicioLeads.registrarRespuesta(TEL, 'ando corriendo pero contame');
  await s.cola.vacia();

  assert.notEqual(s.repo.leadPorTelefono(TEL).fsm_state, S.NURTURE);
});

// ── el mapeo de cajones a fechas, solo ───────────────────────────────────────

test('cada cajon cae donde tiene que caer', () => {
  const cfg = { NURTURE_DEFAULT_DIAS: 14, NURTURE_MIN_DIAS: 2, NURTURE_MAX_DIAS: 180 };
  const ahora = new Date('2026-01-01T12:00:00Z');
  const dias = (c) => Math.round((cuandoVolver(c, ahora, cfg) - ahora) / 86_400_000);

  assert.equal(dias('unos_dias'), 5);
  assert.equal(dias('unas_semanas'), 14);
  assert.equal(dias('un_mes'), 30);
  assert.equal(dias('varios_meses'), 75);
  assert.equal(dias('sin_fecha'), 14, 'sin referencia, el default');
  assert.equal(dias('cualquier_cosa'), 14, 'algo que el modelo no deberia mandar tampoco rompe');
});

test('el piso y el techo se aplican siempre', () => {
  const ahora = new Date('2026-01-01T12:00:00Z');
  const dias = (c, cfg) => Math.round((cuandoVolver(c, ahora, cfg) - ahora) / 86_400_000);

  assert.equal(
    dias('unos_dias', { NURTURE_DEFAULT_DIAS: 14, NURTURE_MIN_DIAS: 10, NURTURE_MAX_DIAS: 180 }),
    10,
    'nunca antes del piso'
  );
  assert.equal(
    dias('varios_meses', { NURTURE_DEFAULT_DIAS: 14, NURTURE_MIN_DIAS: 2, NURTURE_MAX_DIAS: 30 }),
    30,
    'nunca despues del techo'
  );
});

test('los cajones del codigo son los que se le ofrecen al modelo', () => {
  const { HERRAMIENTA } = require('../src/ia/agente');
  const enEnum = HERRAMIENTA.function.parameters.properties.aplaza.enum;

  assert.deepEqual(enEnum, ['no', ...CAJONES], 'si se agrega uno, el enum lo tiene que tener');
});
