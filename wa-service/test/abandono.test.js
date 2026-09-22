'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { conLead, montar, stubModelo, LEAD } = require('./helpers');
const { S } = require('../src/funnel/states');
const { correspondeDerivar } = require('../src/funnel/abandono');

const TEL = '59899123456';
const AM = '59899000111';

const pendientes = (s, leadId, tipo) => s.repo.db
  .prepare("SELECT run_at FROM jobs WHERE lead_id = ? AND type = ? AND status = 'pending'")
  .all(leadId, tipo);

/** Corre el reloj hasta pasado el plazo de abandono. */
const pasadoElPlazo = (s) => new Date(Date.now() + (s.cfg.ABANDONO_MINUTOS + 5) * 60_000);

test('al contestar, se le arma el reloj del abandono', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta(TEL, 'hola, cuento con una parrilla');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(pendientes(s, l.id, 'abandono').length, 1);
});

/**
 * El reloj mide silencio desde lo ultimo que se hablo, no desde el principio.
 *
 * Si no se reiniciara, alguien que conversa tranquilo durante una hora seria
 * derivado en el medio de la conversacion — que es justo lo contrario de
 * haberse ido.
 */
test('cada mensaje reinicia el reloj', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta(TEL, 'hola');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  const primero = pendientes(s, l.id, 'abandono')[0].run_at;

  await new Promise((r) => setTimeout(r, 1100));
  await s.servicioLeads.registrarRespuesta(TEL, 'tengo una parrilla');
  await s.cola.vacia();

  const jobs = pendientes(s, l.id, 'abandono');
  assert.equal(jobs.length, 1, 'sigue habiendo uno solo');
  assert.ok(jobs[0].run_at > primero, 'y quedo corrido para mas adelante');
});

test('pasado el plazo, lo levanta el agente comercial', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta(TEL, 'hola, tengo una parrilla');
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(pasadoElPlazo(s));
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.fsm_state, S.HUMAN_QUEUED);
  assert.equal(l.human_requested, 1);
  assert.equal(l.motivo_derivacion, 'abandono');

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === TEL);
  assert.equal(alLead.length, 1, 'se le avisa que lo van a contactar');
  assert.match(alLead[0].texto, /derivado_por_abandono/);
});

test('el aviso al agente comercial lleva el contexto y los ultimos mensajes', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta(TEL, 'tengo una parrilla con delivery');
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(pasadoElPlazo(s));
  await s.cola.vacia();

  const aviso = s.proveedor.getEnviados().find((e) => e.to === AM);
  assert.ok(aviso, 'le llega al numero del agente comercial');
  assert.match(aviso.texto, /dejo de contestar en el medio/);
  assert.match(aviso.texto, /parrilla con delivery/, 'con lo que dijo, para no arrancar a ciegas');
  assert.match(aviso.texto, new RegExp(TEL));
});

/**
 * El que agendo y no volvio a escribir es el caso de exito.
 *
 * Mandarselo al agente comercial como "se fue" es ruido en el peor lugar: el
 * que recibe avisos que no sirven deja de mirarlos, y despues no ve los que si.
 */
test('al que agendo no se lo deriva por dejar de escribir', async () => {
  const s = await conLead();
  // Un "hola" suelto ya no alcanza para derivar: se mide lo que dijo.
  await s.servicioLeads.registrarRespuesta(TEL, 'hola');
  await s.servicioLeads.registrarRespuesta(TEL, 'y quiero vender online');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  s.servicioLeads.registrarReunion(l.id, {
    meeting_time: '2026-09-01T16:00:00.000Z',
    meeting_url: 'https://meet.google.com/x',
  });
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(pasadoElPlazo(s));
  await s.cola.vacia();

  assert.notEqual(s.repo.leadPorTelefono(TEL).fsm_state, S.HUMAN_QUEUED);
  assert.ok(!s.proveedor.getEnviados().some((e) => /derivado_por_abandono/.test(e.texto)));
});

test('al que se dio de baja tampoco', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta(TEL, 'hola');
  await s.cola.vacia();
  await s.servicioLeads.registrarRespuesta(TEL, 'baja');
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(pasadoElPlazo(s));
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().length, 0, 'silencio absoluto');
});

test('al que ya tiene una persona encima no se lo deriva de nuevo', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta(TEL, 'quiero hablar con una persona');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.fsm_state, S.HUMAN_QUEUED, 'ya esta derivado');
  assert.equal(pendientes(s, l.id, 'abandono').length, 0, 'ni se le arma el reloj');
});

/**
 * Si la IA no puede escribirle al lead, no se deriva a medias: el agente
 * comercial recibiria la conversacion mientras del otro lado la charla se corta
 * sin explicacion.
 */
test('si la IA no puede escribir, se reintenta en vez de dejarlo a medias', async () => {
  const s = await conLead();
  // Un "hola" suelto ya no alcanza para derivar: se mide lo que dijo.
  await s.servicioLeads.registrarRespuesta(TEL, 'hola');
  await s.servicioLeads.registrarRespuesta(TEL, 'y quiero vender online');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  s.proveedor.limpiar();
  // La IA se cae justo cuando toca derivar.
  s.embudo.derivarPorAbandono = async () => false;

  await s.scheduler.correrVencidos(pasadoElPlazo(s));
  await s.cola.vacia();

  assert.notEqual(s.repo.leadPorTelefono(TEL).fsm_state, S.HUMAN_QUEUED, 'no se deriva a medias');
  assert.equal(pendientes(s, l.id, 'abandono').length, 1, 'el job sigue vivo para reintentar');
});

// ── la regla, sola ───────────────────────────────────────────────────────────

test('la regla de cuando corresponde derivar', () => {
  assert.equal(correspondeDerivar({ fsm_state: S.CONVERSANDO }), true);
  assert.equal(correspondeDerivar({ fsm_state: S.MEETING_LINK_SENT }), true, 'tiene el link y no reservo');
  assert.equal(correspondeDerivar({ fsm_state: S.SCHEDULED }), false);
  assert.equal(correspondeDerivar({ fsm_state: S.HUMAN_QUEUED }), false);
  assert.equal(correspondeDerivar({ fsm_state: S.OPT_OUT }), false);
  assert.equal(correspondeDerivar({ fsm_state: S.DISQUALIFIED }), false);
  assert.equal(correspondeDerivar({ fsm_state: S.CONVERSANDO, opt_out: 1 }), false);
  assert.equal(correspondeDerivar({ fsm_state: S.CONVERSANDO, human_requested: 1 }), false);
  assert.equal(
    correspondeDerivar({ fsm_state: S.CONVERSANDO, meeting_booked_at: '2026-09-01' }),
    false,
    'agendo: no se fue, hizo lo que se le pidio'
  );
  assert.equal(correspondeDerivar(null), false);
});

test('con ABANDONO_MINUTOS en cero no se arma nada', async () => {
  const s = await conLead({ ABANDONO_MINUTOS: '0' });
  await s.servicioLeads.registrarRespuesta(TEL, 'hola');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(pendientes(s, l.id, 'abandono').length, 0);
});

// ── un mensaje suelto no es una conversacion ────────────────────────────────

/**
 * El 6-9 entro un numero argentino que escribio "T". El bot le pregunto el
 * nombre del negocio, no contesto, y a la hora lo derivo: ficha al equipo por
 * una letra. Una hora despues escribio "Y" y salio la segunda ficha.
 *
 * Derivar es pedirle tiempo a una persona. Si la mitad de las fichas son de
 * alguien que escribio una letra, el equipo deja de mirarlas, y despues no ve
 * las que si.
 */
test('a quien no dijo nada no se lo deriva', () => {
  const lead = { fsm_state: S.CONVERSANDO };
  assert.equal(correspondeDerivar(lead, 2), false, '"T" y "Y" son dos mensajes y no dicen nada');
  assert.equal(correspondeDerivar(lead, 29), true, '"Sí, me interesa, ¿cuánto sale?" es uno solo y vale oro');
});

/**
 * Sin el dato, se decide como siempre. Los que llaman lo pasan; que uno se
 * olvide no puede apagar la derivacion entera.
 */
test('si no se sabe cuánto escribió, se deriva igual', () => {
  assert.equal(correspondeDerivar({ fsm_state: S.CONVERSANDO }), true);
});

test('el que escribió una sola vez no llega al equipo', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta(TEL, 'T');
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(pasadoElPlazo(s));
  await s.cola.vacia();

  const alEquipo = s.proveedor.getEnviados().filter((e) => e.to === AM);
  assert.equal(alEquipo.length, 0, 'no se le gasta una ficha al equipo por una letra');
  assert.equal(s.repo.leadPorTelefono(TEL).fsm_state !== S.HUMAN_QUEUED, true);
});

// ── de madrugada no se deriva ────────────────────────────────────────────────

const { instanteLocal } = require('../src/agenda/gcal');
const TZ = 'America/Montevideo';

/**
 * El 6-9 un lead bueno —un tipo que ayuda a futbolistas a irse a estudiar a
 * Estados Unidos— escribio tres veces a las dos y media de la mañana de un
 * domingo, se durmio en la pregunta del menu, y a las 3:37 el bot le dijo que
 * le pasaba el caso al equipo. La ficha al agente comercial salio a la misma
 * hora.
 *
 * El mensaje de derivacion viaja como `manual`, y `manual` cuenta como
 * respuesta si el lead escribio en las ultimas dos horas. El abandono salta a
 * los sesenta minutos: 60 siempre es menor que 120, asi que ese mensaje SIEMPRE
 * esquivaba la ventana de horario. No fue mala suerte.
 *
 * Y una hora de silencio a las dos de la mañana no es abandono: se durmio.
 */
test('si se calla de madrugada, no se lo deriva: se retoma a la apertura', async () => {
  const s = await conLead(
    { BUSINESS_HOURS: '09:00-19:00', BUSINESS_DAYS: 'mon-fri' },
    undefined,
    instanteLocal('2026-09-06', 2, 30, TZ),
  );
  await s.servicioLeads.registrarRespuesta(TEL, 'buenass');
  await s.servicioLeads.registrarRespuesta(TEL, 'ayudo a futbolistas a irse a estudiar afuera');
  await s.cola.vacia();
  s.proveedor.limpiar();

  const l = s.repo.leadPorTelefono(TEL);
  await s.scheduler.correrVencidos(instanteLocal('2026-09-06', 3, 40, TZ));
  await s.cola.vacia();

  assert.notEqual(s.repo.leadPorTelefono(TEL).fsm_state, S.HUMAN_QUEUED,
    'no se lo entrega a una persona a las 3 de la mañana');
  assert.equal(s.proveedor.getEnviados().filter((e) => e.to === AM).length, 0,
    'y no se despierta a nadie del equipo');
  assert.equal(pendientes(s, l.id, 'followup').length, 1,
    'queda para retomarlo cuando abrimos');
});

/**
 * En hora se deriva como siempre. La regla es contra la madrugada, no contra
 * derivar.
 */
// ── retomar al que se calla fuera de horario, sin mentirle "hace tres dias" ──

/**
 * Sebastian escribio a las 5:56, el bot le ofrecio horarios y se durmio en
 * medio de la charla. El abandono programa un followup con motivo 'retomar' a
 * la apertura, que tiene que salir con la situacion 'retomar' (no la de "no
 * contestaste el formulario") y con lo ultimo que dijo el bot como contexto,
 * para no repetirse ni inventar que paso.
 */
test('el followup de retomar sale con la situacion retomar y cita lo ultimo que dijo el bot', async () => {
  const modelo = stubModelo({
    respuestas: { conversacion: 'Tengo libre a las 10 y a las 11, ¿cuál te sirve?', retomar: 'retomando' },
  });
  const s = await conLead(
    { BUSINESS_HOURS: '09:00-19:00', BUSINESS_DAYS: 'mon-fri', modelo },
    undefined,
    instanteLocal('2026-09-06', 2, 30, TZ), // domingo de madrugada
  );
  await s.servicioLeads.registrarRespuesta(TEL, 'hola, quiero agendar');
  await s.cola.vacia();
  s.proveedor.limpiar();

  // Se calla de madrugada: el abandono reprograma el followup como 'retomar'.
  await s.scheduler.correrVencidos(instanteLocal('2026-09-06', 3, 40, TZ));
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  const job = s.repo.db.prepare(
    "SELECT * FROM jobs WHERE lead_id = ? AND type = 'followup' AND status = 'pending'"
  ).get(l.id);
  assert.ok(job, 'quedo el followup pendiente');
  assert.equal(job.motivo, 'retomar');

  modelo.llamadas.length = 0;
  // A la hora en que quedo programado (la apertura del lunes en UTC).
  await s.scheduler.correrVencidos(new Date(new Date(job.run_at).getTime() + 60_000));
  await s.cola.vacia();

  const prompt = modelo.llamadas.at(-1).mensajes[0].content;
  assert.match(prompt, /situación: retomar/, 'usa la situacion de retomar, no la de "hace tres dias"');
  assert.match(prompt, /Tengo libre a las 10 y a las 11/, 'le pasa lo ultimo que dijo el bot');

  // El envio en si queda regido por el horario comercial (otra franja, con
  // sus propios tests); lo que importa aca es que el job se dio por hecho y
  // quedo la marca de que se le mando el retomar.
  const jobDespues = s.repo.db.prepare('SELECT status FROM jobs WHERE id = ?').get(job.id);
  assert.equal(jobDespues.status, 'done');
  assert.ok(s.repo.leadPorTelefono(TEL).followup_sent_at);
});

/**
 * Caso raro pero posible: no hay ningun saliente de tipo bot registrado (base
 * rota, o un lead cargado a mano). ultimosMensajes().filter(...).at(-1) puede
 * dar undefined, y eso no tiene que explotar el scheduler: se manda igual, sin
 * la cita.
 */
test('el retomar sin ningun mensaje previo del bot no explota, sale sin cita', async () => {
  const { crearScheduler } = require('../src/scheduler/followup');
  const encolados = [];
  const marcados = [];
  const lead = { id: 1, telefono: TEL, conversacion_desde: null };
  const job = { id: 9, type: 'followup', motivo: 'retomar', lead_id: 1, run_at: new Date().toISOString() };

  const repo = {
    jobsVencidos: () => [job],
    leadPorId: () => lead,
    ultimosMensajes: () => [], // nada registrado
    actualizarLead: () => {},
    marcarJob: (id, estado, error) => marcados.push({ id, estado, error }),
    reprogramarJob: () => {},
  };
  let pedido = null;
  const redactor = { escribir: async (l, situacion, extra) => { pedido = { situacion, extra }; return 'retomando igual'; } };
  const cola = { encolar: (m) => encolados.push(m) };

  const scheduler = crearScheduler({ repo, cola, cfg: {}, redactor, ahora: () => new Date() });
  await assert.doesNotReject(scheduler.correrVencidos());

  assert.equal(pedido.situacion, 'retomar');
  assert.equal(pedido.extra, '', 'sin mensaje del bot, no hay cita: extra vacio');
  assert.equal(encolados.length, 1);
  assert.equal(encolados[0].texto, 'retomando igual');
  assert.equal(marcados[0].estado, 'done');
});

/**
 * Si volvio a escribir antes de la apertura, ya retomo la charla solo: el
 * followup de "seguimos donde quedamos" no tiene que salirle igual, pisado
 * arriba de lo que ya esta charlando.
 */
test('si el lead escribe antes de la apertura, el followup de retomar se cancela', async () => {
  const s = await conLead(
    { BUSINESS_HOURS: '09:00-19:00', BUSINESS_DAYS: 'mon-fri' },
    undefined,
    instanteLocal('2026-09-06', 2, 30, TZ),
  );
  await s.servicioLeads.registrarRespuesta(TEL, 'hola, quiero agendar');
  await s.cola.vacia();

  await s.scheduler.correrVencidos(instanteLocal('2026-09-06', 3, 40, TZ));
  await s.cola.vacia();
  s.proveedor.limpiar();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(pendientes(s, l.id, 'followup').length, 1, 'quedo el de retomar');

  // Se despierta antes de la apertura y sigue solo.
  await s.servicioLeads.registrarRespuesta(TEL, 'che, perdon, me dormi. sigo interesado');
  await s.cola.vacia();

  assert.equal(pendientes(s, l.id, 'followup').length, 0, 'se cancela: ya no hace falta retomarlo');
});

/**
 * El seguimiento clasico de "no contestaste el formulario" sigue cancelandose
 * como siempre cuando el lead responde por primera vez: esto no le cambia nada
 * a ese caso, que no tiene motivo 'retomar'.
 */
test('el followup normal del formulario se sigue cancelando si el lead responde', async () => {
  const s = await montar({ BUSINESS_HOURS: '09:00-19:00', BUSINESS_DAYS: 'mon-fri' });
  await s.servicioLeads.alta(LEAD);
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(pendientes(s, l.id, 'followup').length, 1, 'el alta le dejo el de siempre, sin motivo');

  await s.servicioLeads.registrarRespuesta(TEL, 'hola');
  await s.cola.vacia();

  assert.equal(pendientes(s, l.id, 'followup').length, 0, 'se cancela igual que siempre');
});

/**
 * Si ya se le mando el retomar una vez y se vuelve a callar fuera de horario,
 * no se le manda un segundo "seguimos donde quedamos": el abandono se
 * reprograma para la proxima apertura, y de ahi en mas sigue el camino normal
 * (derivar si corresponde).
 */
test('si ya se retomo una vez y se vuelve a callar fuera de horario, no se retoma dos veces', async () => {
  const modelo = stubModelo({
    respuestas: { conversacion: 'dale, contame', retomar: 'retomando' },
  });
  const s = await conLead(
    { BUSINESS_HOURS: '09:00-19:00', BUSINESS_DAYS: 'mon-fri', modelo },
    undefined,
    instanteLocal('2026-09-06', 2, 30, TZ),
  );
  await s.servicioLeads.registrarRespuesta(TEL, 'hola, quiero agendar');
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(instanteLocal('2026-09-06', 3, 40, TZ));
  await s.cola.vacia();

  const jobRetomar = s.repo.db.prepare(
    "SELECT * FROM jobs WHERE lead_id = (SELECT id FROM leads WHERE telefono = ?) AND type = 'followup' AND status = 'pending'"
  ).get(TEL);
  assert.ok(jobRetomar, 'quedo el followup de retomar programado');

  // Se manda el retomar a la hora en que quedo programado.
  await s.scheduler.correrVencidos(new Date(new Date(jobRetomar.run_at).getTime() + 60_000));
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.ok(l.followup_sent_at, 'ya se le mando el retomar');
  assert.equal(pendientes(s, l.id, 'followup').length, 0, 'no queda otro followup pendiente');

  // Se vuelve a callar, de nuevo fuera de horario.
  s.proveedor.limpiar();
  await s.servicioLeads.registrarRespuesta(TEL, 'perdon, me dormi de nuevo');
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(instanteLocal('2026-09-08', 3, 40, TZ)); // martes de madrugada
  await s.cola.vacia();

  // No se crea un followup nuevo: el abandono se reprograma para la apertura.
  assert.equal(pendientes(s, l.id, 'followup').length, 0, 'no se retoma una segunda vez');
  const abandono = pendientes(s, l.id, 'abandono');
  assert.equal(abandono.length, 1, 'el abandono sigue vivo, reprogramado');
});

test('en horario se deriva igual', async () => {
  const s = await conLead(
    { BUSINESS_HOURS: '09:00-19:00', BUSINESS_DAYS: 'mon-fri' },
    undefined,
    instanteLocal('2026-09-07', 10, 0, TZ),
  );
  await s.servicioLeads.registrarRespuesta(TEL, 'hola');
  await s.servicioLeads.registrarRespuesta(TEL, 'tengo una parrilla');
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.scheduler.correrVencidos(instanteLocal('2026-09-07', 11, 10, TZ));
  await s.cola.vacia();

  assert.equal(s.repo.leadPorTelefono(TEL).fsm_state, S.HUMAN_QUEUED);
});
