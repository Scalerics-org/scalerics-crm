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

/** Los tres datos que se piden. Con esto ya se le ofrece la reunion. */
const COMPLETO = {
  business_name: 'Inmobiliaria Pereyra',
  rubro: 'inmobiliaria',
  business_type: 'ecommerce',
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

test('con los tres datos se le ofrece la reunion, sin filtro de score', async () => {
  // Antes un puntaje decidia si merecia reunion, y con solo tres datos nadie
  // llegaba al umbral. La calificacion pasa a la reunion misma, que es
  // literalmente un diagnostico.
  const s = await conLead({ openai: stubOpenAI({ datos: COMPLETO }) });
  const msgs = await lead(s, 'te cuento todo de una');

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.fsm_state, S.MEETING_LINK_SENT);
  assert.equal(msgs.at(-1), '[link_reunion]', 'le explica el proceso y le pasa el link, en uno');
});

test('un lead chico tambien recibe la oferta', async () => {
  // Con el filtro de score, un kiosco sin presupuesto declarado quedaba
  // descartado por WhatsApp. Ahora entra a la reunion igual: si no encaja, se
  // ve ahi en dos preguntas.
  const chico = { business_name: 'Kiosco', rubro: 'kiosco de barrio', business_type: 'web' };
  const s = await conLead({ openai: stubOpenAI({ datos: chico }) });
  const msgs = await lead(s, 'te cuento');

  assert.equal(estado(s), S.MEETING_LINK_SENT);
  assert.equal(msgs.at(-1), '[link_reunion]');
});

// ── despues de la oferta ─────────────────────────────────────────────────────

/** Deja al lead con el link de Calendly en la mano. */
const CALENDLY = 'https://calendly.com/scalerics/consultoriagratuita';

const stubQueMandaElLink = () => stubOpenAI({ datos: COMPLETO });

/**
 * Deja al lead con el link de Calendly en la mano.
 *
 * Antes hacian falta dos turnos: el bot ofrecia la reunion, el lead decia que
 * si, y recien ahi salia el link. Ahora sale en cuanto el bot sabe que necesita
 * —el mensaje explica el proceso y termina con el link—, asi que es un turno.
 */
async function conLink(s) {
  await lead(s, 'te cuento todo');
  assert.equal(estado(s), S.MEETING_LINK_SENT, 'el link sale sin preguntar antes');
  s.proveedor.limpiar();
}

/**
 * Con el link en la mano, la IA sigue conversando.
 *
 * Antes este estado quedaba afuera de las dos fases donde el modelo mira lo que
 * dice el lead: el turno caia en la tabla de transiciones y salia un texto
 * armado. Y como el link ahora se manda apenas se sabe que necesita, ahi es
 * donde transcurre casi toda la conversacion.
 *
 * Lo que hacian esos textos armados —no reenviar el link, creerle si dice que
 * agendo, ofrecer una persona si no se puede resolver— esta escrito en las
 * instrucciones de la etapa, asi que no se pierde nada.
 */
test('con el link en la mano, el bot le contesta lo que trae', async () => {
  const s = await conLead({ openai: stubQueMandaElLink() });
  await conLink(s);

  const msgs = await lead(s, 'una duda antes de reservar');
  assert.equal(msgs.at(-1), '[conversacion]', 'lo atiende el modelo, no un texto fijo');
  assert.equal(estado(s), S.MEETING_LINK_SENT, 'y sigue siendo el que tiene el link');
});

/**
 * El bug que trajo todo esto, visto en produccion:
 *
 *   ← Me interesa pero para mas adelante, para el mes que viene
 *   → ¿Pudiste agendar? Si te queda mas comodo, puedo hacer que alguien te escriba
 *   ← Para el mes que viene
 *   → Te pido disculpas por las molestias, un compañero te va a escribir
 *
 * Dijo dos veces cuando volvia y terminó derivado por insistente. Es ademas el
 * momento donde mas gente lo dice: ya vio el link y calcula si es ahora o no.
 */
test('el que tiene el link y dice "el mes que viene" queda en pausa', async () => {
  // El modelo recien avisa el aplazo cuando el lead lo dice, no antes: si
  // aplazara desde el primer turno nunca llegaria a tener el link.
  const openai = stubQueMandaElLink();
  const original = openai.chat.completions.create;
  let aplaza = false;
  openai.chat.completions.create = async (args) => {
    const r = await original(args);
    const llamada = r.choices?.[0]?.message?.tool_calls?.[0];
    if (aplaza && llamada) {
      const a = JSON.parse(llamada.function.arguments);
      a.aplaza = 'un_mes';
      a.aplaza_frase = 'para el mes que viene';
      llamada.function.arguments = JSON.stringify(a);
    }
    return r;
  };

  const s = await conLead({ openai });
  await conLink(s);

  aplaza = true;
  await lead(s, 'me interesa pero para el mes que viene');

  const l = s.repo.leadPorTelefono(TEL);
  assert.equal(l.fsm_state, S.NURTURE, 'queda en pausa');
  assert.notEqual(l.fsm_state, S.HUMAN_QUEUED, 'y no derivado por insistente');
  assert.match(l.nurture_motivo, /mes que viene/);

  const programado = s.repo.db
    .prepare("SELECT COUNT(*) AS n FROM jobs WHERE lead_id = ? AND type = 'nurture' AND status = 'pending'")
    .get(l.id).n;
  assert.equal(programado, 1, 'con la vuelta programada');
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
  assert.equal(l.welcomed_at, null, 'vuelve a recibir la presentacion');

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

/**
 * El corte esconde lo de antes, pero tiene que DEJAR PASAR lo de despues.
 *
 * El test de arriba solo miraba que lo viejo se escondiera, y eso pasaba
 * igual —de hecho pasaba de mas—: `conversacion_desde` se guardaba con
 * `toISOString()` y `created_at` con el formato de SQLite, que compara texto.
 * En la posicion 10 el espacio (32) va antes que la T (84), asi que ningun
 * mensaje del mismo dia pasaba el filtro y la IA recibia cero historial.
 *
 * En produccion se vio asi: el lead dijo el nombre de su negocio y el bot se lo
 * volvio a preguntar con las mismas palabras, porque para el modelo los dos
 * turnos eran el primero.
 *
 * Las fechas de este test son del MISMO DIA a proposito. Con dias distintos la
 * comparacion de texto acierta de casualidad y el bug queda invisible.
 */
test('despues de reiniciar, la IA si ve lo que se hablo desde el reinicio', async () => {
  const s = await conLead();
  const l0 = s.repo.leadPorTelefono(TEL);

  // Se parte de cero para que el test no dependa de lo que haya dicho el alta.
  s.repo.db.prepare('DELETE FROM messages WHERE lead_id = ?').run(l0.id);

  const enFecha = (id, cuando) =>
    s.repo.db.prepare('UPDATE messages SET created_at = ? WHERE id = ?').run(cuando, id);
  const guardar = (body) => s.repo.registrarMensaje({
    lead_id: l0.id, direction: 'in', kind: 'reply', body, provider: 'test',
  });

  enFecha(guardar('esto es de antes'), '2026-03-05 15:00:00');
  s.repo.reiniciarLead(l0.id, '2026-03-05T16:00:00.000Z');
  enFecha(guardar('Easy rider'), '2026-03-05 17:00:00');

  const l = s.repo.leadPorId(l0.id);
  const visto = s.repo.ultimosMensajes(l.id, 20, l.conversacion_desde);

  assert.deepEqual(
    visto.map((m) => m.body), ['Easy rider'],
    've lo de despues del reinicio, y nada de antes'
  );
});

/**
 * El mismo choque de formatos, en el guardarrail anti-baneo.
 *
 * `sent_at` se guarda con el formato de SQLite y la ventana llegaba en ISO, asi
 * que el contador de la ultima hora daba cero SIEMPRE y el limite por hora no
 * frenaba nunca. Fallaba en silencio: nada rompe, solo deja de proteger.
 *
 * limits.test.js no lo agarra porque prueba la logica contra un repo de
 * mentira. Este va contra el SQL de verdad.
 */
test('el limite por hora cuenta los envios de la ultima hora', async () => {
  const s = await conLead();
  const haceUnaHora = new Date(Date.now() - 3600_000).toISOString();

  const antes = s.repo.enviosDesde(haceUnaHora);
  s.repo.registrarEnvio('59899000111', true);

  assert.equal(s.repo.enviosDesde(haceUnaHora), antes + 1, 'sin esto el limite no frena nunca');
  assert.equal(s.repo.nuevosDesde(haceUnaHora), s.repo.nuevosDesde(haceUnaHora), 'misma ventana');
});

test('las fechas de JavaScript se guardan en el formato que compara SQLite', () => {
  const { aFechaSqlite } = require('../src/db/repo');

  assert.equal(aFechaSqlite('2026-08-19T16:52:03.966Z'), '2026-08-19 16:52:03');
  assert.equal(aFechaSqlite('2026-08-19 16:52:03'), '2026-08-19 16:52:03', 'lo ya convertido no se toca');
  assert.equal(aFechaSqlite(null), null);
  assert.equal(aFechaSqlite(''), null);
  // Lo que no es una fecha pasa tal cual: mejor que la consulta no encuentre
  // nada y se note, a inventar una fecha y devolver datos de otro momento.
  assert.equal(aFechaSqlite('cualquier cosa'), 'cualquier cosa');

  // La razon de todo esto, en una linea.
  assert.ok(
    '2026-08-19 17:24:03' < '2026-08-19T16:52:03.966Z',
    'el espacio va antes que la T: por eso hay que normalizar'
  );
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

/**
 * Repetir una pregunta no es insistir cuando nadie contesto la primera vez.
 *
 * Paso en produccion: el lead pregunto el precio, el tope por hora freno la
 * respuesta, volvio a preguntar a los ocho minutos y el bot lo derivo por
 * "insiste con el precio". Desde su lado habia preguntado una sola vez y nunca
 * le contestaron.
 */
test('si no le contestamos el precio, repetir la pregunta no lo deriva', async () => {
  const s = await conLead({ MAX_MSGS_PER_HOUR: 1 });

  // El cupo se gasta con la ficha al equipo... no: esa es interna. Se gasta a
  // mano, para dejar la respuesta del precio frenada.
  s.repo.registrarEnvio('59899000999', 0);

  await lead(s, '¿cuánto sale una página web?');
  // El atajo del precio no cambia el estado: contesta y deja al lead donde estaba.
  assert.notEqual(estado(s), S.HUMAN_QUEUED);
  assert.ok(
    !s.proveedor.getEnviados().some((e) => e.to === TEL),
    'y la respuesta quedo frenada por el cupo, sin llegarle'
  );

  // Vuelve a preguntar porque no le llego nada.
  await lead(s, 'hola? cuánto sale?');

  const l = s.repo.leadPorTelefono(TEL);
  assert.notEqual(l.fsm_state, S.HUMAN_QUEUED, 'no lo derivan por preguntar dos veces sin respuesta');
  assert.equal(l.consultas_precio, 1, 'sigue contando una sola consulta');
});
