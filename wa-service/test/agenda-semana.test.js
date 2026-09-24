'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { crearAgenda, instanteLocal } = require('../src/agenda/gcal');
const { pegarLista } = require('../src/funnel/lista');
const { elegirDiaPorCodigo, pideSemanaQueViene } = require('../src/agenda/eleccion');
const { S } = require('../src/funnel/states');
const { conLead, stubModelo } = require('./helpers');
const { googleFalso } = require('./google-falso');

const TZ = 'America/Montevideo';

const CFG = {
  TZ,
  GCAL_CLIENT_ID: 'x', GCAL_CLIENT_SECRET: 'y', GCAL_REFRESH_TOKEN: 'z',
  GCAL_CALENDAR_ID: 'agenda@scalerics',
  AGENDA_DESDE: '12:00', AGENDA_HASTA: '16:00',
  AGENDA_PASO_MIN: 30, AGENDA_DURACION_MIN: 30,
  AGENDA_DIAS: 'mon,tue,wed,thu,fri',
  AGENDA_MAX_OPCIONES: 5, AGENDA_DIAS_ADELANTE: 60, AGENDA_AVISO_MIN_HORAS: 3,
  AGENDA_MAX_POR_DIA: 10, AGENDA_MAX_DIAS: 7,
};

const en = (dia, h = 10, m = 0) => instanteLocal(dia, h, m, TZ);
const diasDe = (r) => r.map((x) => x.dia);

// ── diasConHueco: los dias que quedan de la semana, y la que viene ───────────

const SEMANA_QUE_VIENE = ['2026-09-28', '2026-09-29', '2026-09-30', '2026-10-01', '2026-10-02'];

test('un miercoles: lo que queda de la semana en curso es miercoles, jueves y viernes', async () => {
  const a = crearAgenda({ cfg: CFG, fetch: googleFalso().fetch });
  assert.deepEqual(diasDe(await a.diasConHueco(en('2026-09-23'), { semana: 'actual' })),
    ['2026-09-23', '2026-09-24', '2026-09-25']);
});

test('sin indicar semana, es la semana en curso', async () => {
  const a = crearAgenda({ cfg: CFG, fetch: googleFalso().fetch });
  assert.deepEqual(diasDe(await a.diasConHueco(en('2026-09-23'))),
    ['2026-09-23', '2026-09-24', '2026-09-25']);
});

test('la semana que viene es de lunes a viernes, sin sabado ni domingo', async () => {
  const a = crearAgenda({ cfg: CFG, fetch: googleFalso().fetch });
  assert.deepEqual(diasDe(await a.diasConHueco(en('2026-09-23'), { semana: 'proxima' })), SEMANA_QUE_VIENE);
});

test('un dia sin ningun hueco no aparece', async () => {
  const g = googleFalso({ ocupadosDia: { '2026-09-24': ['12:00-16:00'] } });
  const a = crearAgenda({ cfg: CFG, fetch: g.fetch });
  assert.deepEqual(diasDe(await a.diasConHueco(en('2026-09-23'))), ['2026-09-23', '2026-09-25']);
});

test('un dia con un solo hueco libre SI aparece', async () => {
  const g = googleFalso({ ocupadosDia: { '2026-09-24': ['12:00-15:30'] } });
  const a = crearAgenda({ cfg: CFG, fetch: g.fetch });
  assert.deepEqual(diasDe(await a.diasConHueco(en('2026-09-23'))), ['2026-09-23', '2026-09-24', '2026-09-25']);
});

test('el viernes de tarde no queda nada esta semana', async () => {
  const a = crearAgenda({ cfg: CFG, fetch: googleFalso().fetch });
  const ahora = en('2026-09-25', 17);
  assert.deepEqual(await a.diasConHueco(ahora, { semana: 'actual' }), []);
  assert.deepEqual(diasDe(await a.diasConHueco(ahora, { semana: 'proxima' })), SEMANA_QUE_VIENE);
});

test('sabado y domingo: la semana en curso ya no tiene nada y la que viene es la de despues', async () => {
  const a = crearAgenda({ cfg: CFG, fetch: googleFalso().fetch });
  for (const dia of ['2026-09-26', '2026-09-27']) {
    assert.deepEqual(await a.diasConHueco(en(dia), { semana: 'actual' }), [], `${dia} actual`);
    assert.deepEqual(diasDe(await a.diasConHueco(en(dia), { semana: 'proxima' })), SEMANA_QUE_VIENE, `${dia} proxima`);
  }
});

test('un lunes la semana en curso es toda, y la que viene arranca el lunes siguiente', async () => {
  const a = crearAgenda({ cfg: CFG, fetch: googleFalso().fetch });
  const ahora = en('2026-09-21', 9);
  assert.deepEqual(diasDe(await a.diasConHueco(ahora)),
    ['2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24', '2026-09-25']);
  assert.deepEqual(diasDe(await a.diasConHueco(ahora, { semana: 'proxima' })), SEMANA_QUE_VIENE);
});

test('fin de semana solo si la config de dias habiles lo incluye', async () => {
  const cfg = { ...CFG, AGENDA_DIAS: 'mon,tue,wed,thu,fri,sat' };
  const a = crearAgenda({ cfg, fetch: googleFalso().fetch });
  assert.deepEqual(diasDe(await a.diasConHueco(en('2026-09-23'))),
    ['2026-09-23', '2026-09-24', '2026-09-25', '2026-09-26']);
});

test('la semana se cuenta en la zona del bot, no en la del servidor', async () => {
  // Viernes 25 a las 22:30 en Montevideo ya es sabado 26 en UTC. Si se contara
  // en UTC, "lo que queda de la semana" incluiria un sabado que no es hoy.
  const a = crearAgenda({ cfg: { ...CFG, AGENDA_DIAS: 'mon,tue,wed,thu,fri,sat' }, fetch: googleFalso().fetch });
  const ahora = en('2026-09-25', 22, 30);
  assert.deepEqual(diasDe(await a.diasConHueco(ahora)), ['2026-09-26']);
});

test('domingo de noche: en UTC ya es lunes, pero para el bot la semana en curso sigue vacia', async () => {
  // Domingo 27 a las 22:30 en Montevideo son las 01:30 del lunes 28 en UTC. Contando
  // en UTC, la semana en curso seria la que empieza ese lunes y saldria como "esta
  // semana" lo que en realidad es la semana que viene.
  const a = crearAgenda({ cfg: CFG, fetch: googleFalso().fetch });
  const ahora = en('2026-09-27', 22, 30);
  assert.deepEqual(await a.diasConHueco(ahora, { semana: 'actual' }), []);
  assert.deepEqual(diasDe(await a.diasConHueco(ahora, { semana: 'proxima' })), SEMANA_QUE_VIENE);
});

test('AGENDA_MAX_DIAS es un tope de seguridad: con 7 no corta la semana a la mitad', async () => {
  const a = crearAgenda({ cfg: CFG, fetch: googleFalso().fetch });
  assert.equal((await a.diasConHueco(en('2026-09-21', 9))).length, 5, 'los cinco dias de la semana');

  const corto = crearAgenda({ cfg: { ...CFG, AGENDA_MAX_DIAS: 2 }, fetch: googleFalso().fetch });
  assert.equal((await corto.diasConHueco(en('2026-09-21', 9))).length, 2, 'el tope corta cuando se baja');
});

test('si Google no contesta, no hay dias (y no rompe)', async () => {
  const a = crearAgenda({ cfg: CFG, fetch: googleFalso({ fallaFreeBusy: true }).fetch });
  assert.deepEqual(await a.diasConHueco(en('2026-09-23')), []);
});

// ── el flujo: la lista con "La semana que viene" ────────────────────────────

const COMPLETO = {
  business_name: 'Panadería PanesAhora', rubro: 'panadería',
  business_type: 'web', budget: 'mas_3000', team_size_personas: 8,
  instagram_web: '@panesahora', needs: 'quiero vender online',
};
const DIJO_TODO = 'te cuento todo: es la Panadería PanesAhora, una panadería, '
  + 'estamos en @panesahora y quiero vender online';
const TEL = '59899123456';

const MIERCOLES_23 = en('2026-09-23', 10);
const VIERNES_25_TARDE = en('2026-09-25', 17);

const LISTA_CON_SEMANA = '¿Qué día te queda mejor?\n\n1. Miércoles 23\n2. Jueves 24\n3. Viernes 25\n4. La semana que viene';
const LISTA_SEMANA_QUE_VIENE = '¿Qué día te queda mejor?\n\n1. Lunes 28\n2. Martes 29\n3. Miércoles 30\n4. Jueves 1\n5. Viernes 2';

/** Un lead que ya recibio la lista de dias, con el reloj congelado. */
async function conListaDeDias({ reloj = MIERCOLES_23, google = googleFalso(), respuestas = {} } = {}) {
  const modelo = stubModelo({ datos: COMPLETO, respuestas });
  const original = modelo.pedir.bind(modelo);
  modelo.pedir = async (args) => {
    // El modelo de respaldo no entiende nada de fechas y no elige nada: lo que
    // se prueba aca es lo que resuelve el codigo.
    if (args.herramienta?.nombre === 'elegir') return { texto: null, argumentos: { opcion: 'ninguno' } };
    if (args.herramienta?.nombre === 'momento') return { texto: null, argumentos: { pide: false } };
    return original(args);
  };
  const s = await conLead({ modelo, AGENDA_OFRECE_HORARIOS: 'true', _google: google.fetch }, undefined, reloj);

  const responder = async (t) => {
    s.proveedor.limpiar();
    await s.servicioLeads.registrarRespuesta(TEL, t);
    await s.cola.vacia();
    return s.proveedor.getEnviados().filter((e) => e.to === TEL).map((e) => e.texto).at(-1);
  };
  const lead = () => s.repo.leadPorTelefono(TEL);

  const primero = await responder(DIJO_TODO);
  return { s, responder, lead, primero };
}

test('un miercoles la lista trae lo que queda de la semana y, ultima, "La semana que viene"', async () => {
  const { primero, lead } = await conListaDeDias();
  assert.match(primero, /^\[oferta_con_horarios\]/);
  assert.ok(primero.endsWith(LISTA_CON_SEMANA), primero);
  assert.equal(lead().fsm_state, S.HORARIOS_OFRECIDOS);
  assert.equal(lead().dia_en_foco, null, 'paso 1');
});

test('un dia sin huecos no sale en la lista, y "La semana que viene" sigue ultima', async () => {
  const google = googleFalso({ ocupadosDia: { '2026-09-24': ['12:00-16:00'] } });
  const { primero } = await conListaDeDias({ google });
  assert.ok(primero.endsWith('1. Miércoles 23\n2. Viernes 25\n3. La semana que viene'), primero);
});

test('elegir un dia de esta semana por su numero sigue andando', async () => {
  const { responder, lead } = await conListaDeDias();
  const r = await responder('3');
  assert.match(r, /^\[disponibilidad_del_dia\]/);
  assert.equal(lead().dia_en_foco, '2026-09-25');
});

test('elegir el ultimo numero (la semana que viene) muestra esa semana, sin la opcion extra', async () => {
  const { responder, lead } = await conListaDeDias();
  const r = await responder('4');
  assert.match(r, /^\[semana_que_viene\]/);
  assert.ok(r.endsWith(LISTA_SEMANA_QUE_VIENE), r);
  assert.equal(lead().dia_en_foco, null, 'sigue eligiendo dia');
  const guardado = JSON.parse(lead().horarios_ofrecidos);
  assert.equal(guardado.length, 5);
  assert.ok(!guardado.includes('semana_que_viene'), 'ya no hay opcion extra');
});

test('el numero de "la semana que viene" acepta los mismos adornos que cualquier numero de lista', async () => {
  for (const t of ['4!', '4 👍', 'la 4', 'cuatro', 'opcion 4', 'el 4)']) {
    const { responder } = await conListaDeDias();
    const r = await responder(t);
    assert.match(r, /^\[semana_que_viene\]/, t);
  }
});

test('tambien por nombre: "la semana que viene", "la proxima", "la que viene"', async () => {
  for (const t of ['la semana que viene', 'la próxima', 'la que viene', 'La Semana Que Viene!', 'prefiero la proxima semana']) {
    const { responder, lead } = await conListaDeDias();
    const r = await responder(t);
    assert.match(r, /^\[semana_que_viene\]/, t);
    assert.equal(lead().dia_en_foco, null, t);
  }
});

test('desde la semana que viene, elige dia y despues hora, y agenda ahi', async () => {
  const { responder, lead } = await conListaDeDias();
  await responder('4');
  const horas = await responder('2');
  assert.match(horas, /^\[disponibilidad_del_dia\]/);
  assert.equal(lead().dia_en_foco, '2026-09-29');

  await responder('1');
  assert.equal(lead().fsm_state, S.SCHEDULED);
  assert.match(lead().meeting_time, /^2026-09-29T15:00/, 'martes 29 a las 12:00 de Montevideo');
});

test('"el jueves que viene" va derecho a las horas de ese jueves, no al de esta semana', async () => {
  const { responder, lead } = await conListaDeDias();
  const r = await responder('el jueves que viene');
  assert.match(r, /^\[disponibilidad_del_dia\]/);
  assert.equal(lead().dia_en_foco, '2026-10-01');
});

test('"el proximo viernes" tambien es el de la semana que viene', async () => {
  const { responder, lead } = await conListaDeDias();
  await responder('el proximo viernes');
  assert.equal(lead().dia_en_foco, '2026-10-02');
});

test('negada, "la semana que viene" no se elige: lo atiende la conversacion, con la lista abajo', async () => {
  for (const t of ['la semana que viene no puedo', 'esta semana no puedo, la semana que viene tampoco']) {
    const { responder, lead } = await conListaDeDias({ respuestas: { conversacion: 'Ah, tranquilo. Avisame cuando puedas.' } });
    const r = await responder(t);
    assert.equal(r, `Ah, tranquilo. Avisame cuando puedas.\n\n${LISTA_CON_SEMANA}`, t);
    assert.equal(lead().dia_en_foco, null, t);
  }
});

test('"el jueves no puedo" sigue sin elegir el jueves, aunque ahora haya tres dias', async () => {
  const { responder, lead } = await conListaDeDias({ respuestas: { conversacion: 'Dale, contame.' } });
  const r = await responder('el jueves no puedo');
  assert.equal(r, `Dale, contame.\n\n${LISTA_CON_SEMANA}`);
  assert.equal(lead().dia_en_foco, null);
});

test('mañana y pasado mañana se resuelven contra los tres dias de la semana', async () => {
  const a = await conListaDeDias();
  await a.responder('mañana');
  assert.equal(a.lead().dia_en_foco, '2026-09-24');

  const b = await conListaDeDias();
  await b.responder('pasado mañana');
  assert.equal(b.lead().dia_en_foco, '2026-09-25');
});

test('un numero fuera de la lista (el 5 con cuatro opciones) se pide de nuevo, con la lista', async () => {
  const { responder } = await conListaDeDias();
  const r = await responder('5');
  assert.match(r, /^\[dia_no_entendido\]/);
  assert.ok(r.endsWith(LISTA_CON_SEMANA), r);
});

// ── viernes de tarde, sabado, domingo, semana que viene vacia ───────────────

test('viernes de tarde: se muestra directo la semana que viene, sin la opcion extra ni nombrar la de ahora', async () => {
  const { primero, lead } = await conListaDeDias({ reloj: VIERNES_25_TARDE });
  assert.ok(primero.endsWith(LISTA_SEMANA_QUE_VIENE), primero);
  assert.doesNotMatch(primero, /La semana que viene/);
  assert.doesNotMatch(primero, /esta semana/i);
  assert.equal(JSON.parse(lead().horarios_ofrecidos).length, 5);
});

test('sabado y domingo: tambien directo la semana que viene', async () => {
  for (const dia of ['2026-09-26', '2026-09-27']) {
    const { primero } = await conListaDeDias({ reloj: en(dia, 11) });
    assert.ok(primero.endsWith(LISTA_SEMANA_QUE_VIENE), `${dia}: ${primero}`);
  }
});

test('desde la lista directa, el numero 5 es el viernes y no hay "semana que viene" que elegir', async () => {
  const { responder, lead } = await conListaDeDias({ reloj: VIERNES_25_TARDE });
  await responder('5');
  assert.equal(lead().dia_en_foco, '2026-10-02');

  const otro = await conListaDeDias({ reloj: VIERNES_25_TARDE });
  const r = await otro.responder('la semana que viene');
  assert.doesNotMatch(r, /^\[semana_que_viene\]/, 'no hay opcion extra: no se re-muestra la misma lista como si fuera otra');
});

test('si la semana que viene esta llena, no se ofrece la opcion', async () => {
  const llena = Object.fromEntries(SEMANA_QUE_VIENE.map((d) => [d, ['12:00-16:00']]));
  const { primero } = await conListaDeDias({ google: googleFalso({ ocupadosDia: llena }) });
  assert.ok(primero.endsWith('1. Miércoles 23\n2. Jueves 24\n3. Viernes 25'), primero);
  assert.doesNotMatch(primero, /La semana que viene/);
});

// ── en el paso de horas ────────────────────────────────────────────────────

test('en el paso de horas, "el jueves que viene" cambia a ese jueves', async () => {
  const { responder, lead } = await conListaDeDias();
  await responder('1');
  assert.equal(lead().dia_en_foco, '2026-09-23');
  await responder('el jueves que viene');
  assert.equal(lead().dia_en_foco, '2026-10-01');
});

test('en el paso de horas, un dia de la semana que viene (sin decir "que viene") tambien se puede pedir', async () => {
  // El lunes no esta entre los tres dias que se le mostraron, pero existe y
  // tiene lugar: no hay razon para decirle que no.
  const { responder, lead } = await conListaDeDias();
  await responder('1');
  await responder('lunes');
  assert.equal(lead().dia_en_foco, '2026-09-28');
});

// ── la lista no se pierde cuando la respuesta cae a conversacion ────────────

/**
 * Charla de Los Sopranos, 22-9 a las 21:40:
 *   → ¿Qué día te queda mejor? 1. Miércoles 23 2. Jueves 24
 *   ← dije pagina web
 *   → Tenés razón, disculpá... ¿Qué días te viene bien para la videollamada?
 * El lead corrigio la necesidad, no eligio dia; el modelo contesto bien, pero
 * sin lista: se quedo sin opciones.
 */
const INCIDENTE = 'Tenés razón, disculpá. ¿Qué días te viene bien para la videollamada?';

test('en el paso de dias, si la respuesta cae a conversacion, la lista sale igual abajo', async () => {
  const { responder, lead } = await conListaDeDias({ respuestas: { conversacion: INCIDENTE } });
  const antes = lead().horarios_ofrecidos;

  const r = await responder('dije pagina web');

  assert.equal(r, `${INCIDENTE}\n\n${LISTA_CON_SEMANA}`);
  assert.equal(lead().fsm_state, S.HORARIOS_OFRECIDOS, 'sigue eligiendo');
  assert.equal(lead().horarios_ofrecidos, antes, 'la conversacion no toca lo que se ofrecio');
});

test('despues de la conversacion se puede elegir de esa misma lista', async () => {
  const { responder, lead } = await conListaDeDias({ respuestas: { conversacion: INCIDENTE } });
  await responder('dije pagina web');
  const r = await responder('4');
  assert.match(r, /^\[semana_que_viene\]/);
  assert.equal(lead().dia_en_foco, null);
});

test('en el paso de horas, la conversacion tambien deja la lista de horas de ese dia', async () => {
  const { responder, lead } = await conListaDeDias({ respuestas: { conversacion: 'Tenés razón, disculpá.' } });
  await responder('1');
  const antes = lead().horarios_ofrecidos;

  const r = await responder('dije pagina web');

  assert.match(r, /^Tenés razón, disculpá\.\n\nHorarios del Miércoles 23:\n1\. 13:00\n2\. 13:30\n/);
  assert.equal(lead().horarios_ofrecidos, antes);
  assert.equal(lead().dia_en_foco, '2026-09-23');
});

test('si el modelo escribe su propia lista de dias, no sale duplicada: queda la del codigo', async () => {
  const conListaPropia = 'Perdón. ¿Qué día te queda mejor?\n1. Lunes 28\n2. Martes 29\n3. Miércoles 30';
  const { responder } = await conListaDeDias({ respuestas: { conversacion: conListaPropia } });

  const r = await responder('dije pagina web');

  assert.equal((r.match(/¿Qué día te queda mejor\?/g) || []).length, 1, 'el encabezado una sola vez');
  assert.equal((r.match(/^\d\. /gm) || []).length, 4, 'las cuatro opciones del codigo, no las inventadas');
  assert.doesNotMatch(r, /Lunes 28/, 'los dias que invento el modelo no salen');
  assert.ok(r.endsWith(LISTA_CON_SEMANA), r);
});

// ── pegarLista, aislado ─────────────────────────────────────────────────────

test('pegarLista: agrega la lista abajo, separada por una linea en blanco', () => {
  assert.equal(pegarLista('Dale.', '1. 12:00\n2. 12:30'), 'Dale.\n\n1. 12:00\n2. 12:30');
});

test('pegarLista: sin lista que pegar, deja el texto como esta', () => {
  assert.equal(pegarLista('Dale.', ''), 'Dale.');
  assert.equal(pegarLista('Dale.', undefined), 'Dale.');
});

test('pegarLista: si el modelo escribio una lista de horas, se saca y queda la del codigo', () => {
  const modelo = 'Estos son los horarios:\n1. 12:00\n2. 12:30\n3. 13:00';
  assert.equal(pegarLista(modelo, 'Horarios del Jueves 24:\n1. 13:00\n2. 13:30'),
    'Estos son los horarios:\n\nHorarios del Jueves 24:\n1. 13:00\n2. 13:30');
});

test('pegarLista: una lista numerada de OTRA cosa no se toca', () => {
  const servicios = 'Hacemos:\n1. Páginas web\n2. E-commerce\n3. Automatización';
  assert.equal(pegarLista(servicios, '1. 12:00'), `${servicios}\n\n1. 12:00`);
});

test('pegarLista: una sola linea con forma de dia no alcanza para tomarla por una lista', () => {
  const t = 'Te propongo el\n1. Lunes 28 y vemos';
  assert.equal(pegarLista(t, '1. 12:00'), `${t}\n\n1. 12:00`);
});

test('pegarLista: el encabezado repetido sin lista tambien se saca', () => {
  const r = pegarLista('Perdón.\n¿Qué día te queda mejor?', '¿Qué día te queda mejor?\n\n1. Lunes 28');
  assert.equal(r, 'Perdón.\n\n¿Qué día te queda mejor?\n\n1. Lunes 28');
});

test('pegarLista: si el modelo no escribio nada, sale solo la lista', () => {
  assert.equal(pegarLista('', '1. 12:00'), '1. 12:00');
  assert.equal(pegarLista(null, '1. 12:00'), '1. 12:00');
});

// ── las funciones de eleccion siguen andando con tres dias ──────────────────

const VIE25 = en('2026-09-25', 12);
const TRES = [en('2026-09-23', 12), en('2026-09-24', 12), VIE25];

test('elegirDiaPorCodigo con tres dias: numero, nombre, fecha, adornos y palabras', () => {
  assert.equal(elegirDiaPorCodigo('3', TRES, TZ), VIE25);
  assert.equal(elegirDiaPorCodigo('3!', TRES, TZ), VIE25);
  assert.equal(elegirDiaPorCodigo('tres', TRES, TZ), VIE25);
  assert.equal(elegirDiaPorCodigo('el viernes', TRES, TZ), VIE25);
  assert.equal(elegirDiaPorCodigo('el 25', TRES, TZ), VIE25);
  assert.equal(elegirDiaPorCodigo('la 3', TRES, TZ), VIE25);
});

test('elegirDiaPorCodigo con tres dias: la negacion sigue sin elegir', () => {
  assert.equal(elegirDiaPorCodigo('el jueves no puedo', TRES, TZ), null);
  assert.equal(elegirDiaPorCodigo('el jueves no, el viernes si', TRES, TZ), VIE25);
});

test('elegirDiaPorCodigo no confunde "la semana que viene" con un dia ni con un numero', () => {
  assert.equal(elegirDiaPorCodigo('la semana que viene', TRES, TZ), null);
  assert.equal(elegirDiaPorCodigo('4', TRES, TZ), null, 'el 4 de una lista de tres es la semana que viene: lo resuelve el motor');
});

test('pideSemanaQueViene: lo que pide la semana siguiente y lo que no', () => {
  for (const t of ['la semana que viene', 'La próxima', 'la que viene', 'proxima semana', 'el jueves que viene', 'el proximo martes']) {
    assert.equal(pideSemanaQueViene(t), true, t);
  }
  for (const t of ['el jueves', '4', 'dale', 'esta semana', 'el que viene', 'ninguno', '']) {
    assert.equal(pideSemanaQueViene(t), false, t);
  }
});

// ── un numero solo es la opcion, no el dia del mes ──────────────────────────

const SEMANA_SIGUIENTE = SEMANA_QUE_VIENE.map((d) => en(d, 12));

/**
 * Con la semana que viene en la lista los dias del mes chicos aparecen a la
 * vez que los numeros de opcion: "1. Lunes 28 ... 4. Jueves 1, 5. Viernes 2".
 * El lead que ve esa lista y escribe "1" quiere el lunes 28, no el jueves 1.
 */
test('un numero solo contestando la lista es la opcion, aunque coincida con un dia del mes', () => {
  assert.equal(elegirDiaPorCodigo('1', SEMANA_SIGUIENTE, TZ), SEMANA_SIGUIENTE[0], 'lunes 28, no jueves 1');
  assert.equal(elegirDiaPorCodigo('2', SEMANA_SIGUIENTE, TZ), SEMANA_SIGUIENTE[1], 'martes 29, no viernes 2');
  assert.equal(elegirDiaPorCodigo('2!', SEMANA_SIGUIENTE, TZ), SEMANA_SIGUIENTE[1]);
  assert.equal(elegirDiaPorCodigo('dos', SEMANA_SIGUIENTE, TZ), SEMANA_SIGUIENTE[1]);
  assert.equal(elegirDiaPorCodigo('opcion 1', SEMANA_SIGUIENTE, TZ), SEMANA_SIGUIENTE[0]);
  assert.equal(elegirDiaPorCodigo('1.', SEMANA_SIGUIENTE, TZ), SEMANA_SIGUIENTE[0]);
});

test('con "el" o "la" delante, el numero sigue siendo una fecha primero', () => {
  assert.equal(elegirDiaPorCodigo('el 1', SEMANA_SIGUIENTE, TZ), SEMANA_SIGUIENTE[3], 'jueves 1');
  assert.equal(elegirDiaPorCodigo('el 2', SEMANA_SIGUIENTE, TZ), SEMANA_SIGUIENTE[4], 'viernes 2');
  assert.equal(elegirDiaPorCodigo('el 29', SEMANA_SIGUIENTE, TZ), SEMANA_SIGUIENTE[1]);
});

test('un numero solo que no es opcion de la lista puede ser una fecha', () => {
  assert.equal(elegirDiaPorCodigo('25', TRES, TZ), VIE25);
  assert.equal(elegirDiaPorCodigo('29', SEMANA_SIGUIENTE, TZ), SEMANA_SIGUIENTE[1]);
});

test('el tope de dias por lista tiene un default que no corta la semana', () => {
  const { cfgTest } = require('./helpers');
  assert.equal(cfgTest().AGENDA_MAX_DIAS, 7);
});
