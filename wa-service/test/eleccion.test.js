'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { horasQueDijo, eligioEsaHora, diasNumeroQueNombro } = require('../src/agenda/eleccion');
const { instanteLocal } = require('../src/agenda/gcal');

const TZ = 'America/Montevideo';

/** Un jueves cualquiera, en hora de Montevideo (UTC-3). */
const alas = (h, m = 0) => new Date(Date.UTC(2026, 8, 3, h + 3, m));

test('saca las horas que el lead escribio', () => {
  assert.deepEqual(horasQueDijo('15:00'), [15]);
  assert.deepEqual(horasQueDijo('las 13'), [13]);
  assert.deepEqual(horasQueDijo('12:30'), [12]);
  assert.deepEqual(horasQueDijo('a las 3 de la tarde'), [3]);
  assert.deepEqual(horasQueDijo('la primera'), [], 'sin numeros no hay hora');
  assert.deepEqual(horasQueDijo('dale'), []);
});

/**
 * El bug del 2-9, tal cual paso:
 *
 *   → Tenemos estos horarios: 12:00, 12:30, 13:00, 13:30, 14:30. ¿Cuál te viene bien?
 *   ← 15:00
 *   → Perfecto. Jueves 3 a las 12:00. Te paso el link...
 *
 * Pidio las 15 —que no estaba en la lista— y el modelo eligio las 12 igual,
 * porque el enum solo lo deja devolver horarios ofrecidos: si el que quiere no
 * esta, devuelve el que menos le disgusta en vez de "ninguno". El codigo
 * confirmaba que el horario existiera y estuviera libre, pero no que fuera el
 * que el lead pidio.
 *
 * Con un cliente real eso es alguien conectandose a una hora y nosotros a otra.
 */
test('si dijo una hora que no es la elegida, no vale', () => {
  assert.equal(eligioEsaHora('15:00', alas(12), TZ), false);
  assert.equal(eligioEsaHora('a las 15', alas(12, 30), TZ), false);
  assert.equal(eligioEsaHora('9 de la mañana', alas(13), TZ), false);
});

test('si dijo la hora que se eligio, vale', () => {
  assert.equal(eligioEsaHora('15:00', alas(15), TZ), true);
  assert.equal(eligioEsaHora('las 13', alas(13), TZ), true);
  assert.equal(eligioEsaHora('12:30', alas(12, 30), TZ), true);
});

/**
 * La gente dice "a las 3" para las 15:00. Rechazar eso seria peor que el bug:
 * dejaria sin agendar a quien eligio bien.
 */
test('el reloj de 12 cuenta igual que el de 24', () => {
  assert.equal(eligioEsaHora('a las 3 de la tarde', alas(15), TZ), true);
  assert.equal(eligioEsaHora('1 y media', alas(13, 30), TZ), true);
  assert.equal(eligioEsaHora('12', alas(12), TZ), true);
});

/**
 * Sin numeros no hay nada que verificar y manda el modelo, que para eso esta:
 * entiende "la primera" y "la del mediodia".
 */
test('sin una hora escrita, se le cree al modelo', () => {
  assert.equal(eligioEsaHora('la primera', alas(12), TZ), true);
  assert.equal(eligioEsaHora('dale, esa', alas(14, 30), TZ), true);
  assert.equal(eligioEsaHora('', alas(12), TZ), true);
});

/**
 * Una fecha suelta no es una hora. "el 3" es el dia, y tomarlo como hora
 * rechazaria una eleccion buena.
 */
test('un numero que no puede ser hora se ignora', () => {
  assert.deepEqual(horasQueDijo('el jueves 3 dale'), [3]);
  assert.deepEqual(horasQueDijo('somos 45 personas'), []);
  assert.deepEqual(horasQueDijo('el 2026'), []);
});

/**
 * Desde que los horarios abarcan varios dias, el lead puede elegir nombrando el
 * dia en vez de la hora: "el viernes", "el 4". Mirando solo numeros, "el 4"
 * parece la hora 4 y rechazaria una eleccion perfectamente buena.
 */
test('nombrar el dia elegido tambien vale', () => {
  const viernes4 = new Date(Date.UTC(2026, 8, 4, 15)); // viernes 4, 12:00 en MVD
  assert.equal(eligioEsaHora('el viernes', viernes4, TZ), true);
  assert.equal(eligioEsaHora('el 4', viernes4, TZ), true);
  assert.equal(eligioEsaHora('viernes 4 dale', viernes4, TZ), true);
});

test('nombrar OTRO dia no vale', () => {
  const viernes4 = new Date(Date.UTC(2026, 8, 4, 15));
  assert.equal(eligioEsaHora('el jueves', viernes4, TZ), false);
  assert.equal(eligioEsaHora('el lunes mejor', viernes4, TZ), false);
});

/**
 * Dia y hora juntos: los dos tienen que dar. Es el caso normal cuando hay
 * horarios repetidos en dias distintos.
 */
test('con dia y hora, los dos tienen que coincidir', () => {
  const viernes4alas1230 = new Date(Date.UTC(2026, 8, 4, 15, 30));
  assert.equal(eligioEsaHora('el viernes 12:30', viernes4alas1230, TZ), true);
  assert.equal(eligioEsaHora('el jueves 12:30', viernes4alas1230, TZ), false);
});

/**
 * El bug del 19-8/22-9: un numero que es parte de una HORA ("23:00", "13.30",
 * "a las 14") no puede colarse como si nombrara un DIA. El dia 13 ofrecido a
 * las 12:00 y "13:00" (una hora invalida, fuera de franja) no son la misma
 * cosa, aunque el "13" aparezca en los dos.
 */
test('un numero pegado a una hora no cuenta como el dia, aunque coincida', () => {
  const dia13alas12 = new Date(Date.UTC(2026, 8, 13, 15)); // dia 13, 12:00 en MVD
  assert.equal(eligioEsaHora('13:00', dia13alas12, TZ), false, '"13:00" es una hora, no el dia 13');
  assert.equal(eligioEsaHora('13.00', dia13alas12, TZ), false, 'con punto tambien');
  assert.equal(eligioEsaHora('a las 13', dia13alas12, TZ), false, '"las 13" es una hora, no el dia');
});

test('pero "el 13" si nombra el dia, aunque la hora sea otra', () => {
  const dia13alas12 = new Date(Date.UTC(2026, 8, 13, 15));
  assert.equal(eligioEsaHora('el 13 a las 12', dia13alas12, TZ), true);
  assert.equal(eligioEsaHora('el 13, dale', dia13alas12, TZ), true);
});

test('una fecha con barra tambien nombra el dia', () => {
  const dia23alas12 = new Date(Date.UTC(2026, 8, 23, 15));
  assert.equal(eligioEsaHora('23/9 a las 12', dia23alas12, TZ), true);
});

test('"a las 3" sigue siendo las 15hs, aunque el dia ofrecido sea el 3', () => {
  const dia3alas15 = new Date(Date.UTC(2026, 8, 3, 18)); // dia 3, 15:00 en MVD
  assert.equal(eligioEsaHora('a las 3', dia3alas15, TZ), true);
});

/**
 * Segunda vuelta del mismo bug: "13hs", "13 y media", "tipo 13" son formas tan
 * comunes de decir una hora como "13:00", y ninguna tiene dos puntos. Andres
 * escribio "Lunes 17hs" el 11-9: con la version anterior, "17hs" contaba como
 * si hubiera nombrado el dia 17.
 */
test('formas de decir una hora sin dos puntos tampoco cuentan como el dia', () => {
  assert.deepEqual(diasNumeroQueNombro('13hs'), []);
  assert.deepEqual(diasNumeroQueNombro('13 hs'), []);
  assert.deepEqual(diasNumeroQueNombro('13h'), []);
  assert.deepEqual(diasNumeroQueNombro('13hrs'), []);
  assert.deepEqual(diasNumeroQueNombro('13 horas'), []);
  assert.deepEqual(diasNumeroQueNombro('13 y media'), []);
  assert.deepEqual(diasNumeroQueNombro('13 y cuarto'), []);
  assert.deepEqual(diasNumeroQueNombro('13 y 30'), [], 'ni el 13 ni el 30: los dos son la hora');
  assert.deepEqual(diasNumeroQueNombro('tipo 13'), []);
  assert.deepEqual(diasNumeroQueNombro('a eso de las 14'), []);
  assert.deepEqual(diasNumeroQueNombro('a partir de las 9'), []);
  assert.deepEqual(diasNumeroQueNombro('desde las 9'), []);
  assert.deepEqual(diasNumeroQueNombro('despues de las 14'), []);
  assert.deepEqual(diasNumeroQueNombro('antes de las 14'), []);
});

test('pero siguen contando como dia: "el 13", "miercoles 13", "13/9", "13 de setiembre"', () => {
  assert.deepEqual(diasNumeroQueNombro('el 13'), [13]);
  assert.deepEqual(diasNumeroQueNombro('miercoles 13'), [13]);
  assert.deepEqual(diasNumeroQueNombro('13/9'), [13], 'dia/mes: solo el dia, no el mes');
  assert.deepEqual(diasNumeroQueNombro('13 de setiembre'), [13]);
  assert.deepEqual(diasNumeroQueNombro('el 13 a las 12'), [13]);
});

test('"17hs" no hace confundir el dia 17 con la hora, de punta a punta', () => {
  // El caso real de Andres, 11-9: "Lunes 17hs".
  const lunes17alas10 = new Date(Date.UTC(2026, 8, 17, 13)); // dia 17, 10:00 en MVD
  assert.equal(eligioEsaHora('17hs', lunes17alas10, TZ), false, '17hs no es el dia 17, ni ninguna hora del horario ofrecido');
});

// ── una hora que el lead propone, fuera de la lista ──────────────────────────

const { revisarFranja } = require('../src/agenda/eleccion');

const CFG = {
  TZ, AGENDA_DESDE: '12:00', AGENDA_HASTA: '16:00',
  AGENDA_PASO_MIN: 30, AGENDA_DURACION_MIN: 30,
  AGENDA_DIAS: 'mon,tue,wed,thu,fri',
  AGENDA_DIAS_ADELANTE: 10, AGENDA_AVISO_MIN_HORAS: 3,
};

/** Miercoles 2 de setiembre de 2026, 09:00 en Montevideo. */
const AHORA = new Date(Date.UTC(2026, 8, 2, 12));

/**
 * La lista que se le muestra son unas pocas sugerencias repartidas en dias, no
 * todo lo que hay libre: en una franja de 12 a 16 cada media hora entran 8 por
 * dia. Si el lead pide otra hora que esta libre, hay que darsela — decirle que
 * no a un horario que existe es perder la reunion por nada.
 */
test('una hora de la franja, aunque no se haya listado, es aceptable', () => {
  assert.deepEqual(revisarFranja(alas(15), CFG, AHORA), { ok: true });
  assert.deepEqual(revisarFranja(alas(13, 30), CFG, AHORA), { ok: true });
});

test('fuera de la franja se rechaza, y se dice por que', () => {
  assert.equal(revisarFranja(alas(5), CFG, AHORA).ok, false);
  assert.equal(revisarFranja(alas(5), CFG, AHORA).motivo, 'fuera_de_franja');
  assert.equal(revisarFranja(alas(20), CFG, AHORA).motivo, 'fuera_de_franja');
  // 15:30 entra: arranca antes de las 16 y la reunion dura 30.
  assert.equal(revisarFranja(alas(15, 30), CFG, AHORA).ok, true);
  // 15:45 no: la reunion terminaria 16:15, despues de que cerramos.
  assert.equal(revisarFranja(alas(15, 45), CFG, AHORA).motivo, 'fuera_de_franja');
});

/**
 * El 3-9 el lead pidio las 10:23 dentro de una franja de 10:00 a 19:00 y el bot
 * le contesto "ese horario no entra en nuestra franja de 10:00 a 19:00". Se lo
 * marco enseguida: "pero 10:23 entra en la franja". Tenia razon.
 *
 * El motivo verdadero era la grilla de media hora, que es una comodidad nuestra
 * para armar la lista de sugerencias, no un limite del negocio. Una reunion a
 * las 10:23 se puede tener igual, y sostener que no —con un motivo que ademas
 * es falso— es perder la reunion discutiendo.
 */
test('una hora corrida, fuera de la grilla, se puede agendar igual', () => {
  assert.deepEqual(revisarFranja(alas(14, 23), CFG, AHORA), { ok: true });
  assert.deepEqual(revisarFranja(alas(12, 15), CFG, AHORA), { ok: true });
});

test('un dia no habil se rechaza', () => {
  // Sabado 5 de setiembre.
  const sabado = new Date(Date.UTC(2026, 8, 5, 15));
  assert.equal(revisarFranja(sabado, CFG, AHORA).motivo, 'dia_no_habil');
});

test('algo demasiado pronto o demasiado lejos tambien', () => {
  // A las 9:00 en punto, las 12:00 caen justo en el piso de 3 horas y valen.
  assert.equal(revisarFranja(new Date(Date.UTC(2026, 8, 2, 15)), CFG, AHORA).ok, true);
  // Media hora mas tarde ya no llegan.
  const nueveYMedia = new Date(Date.UTC(2026, 8, 2, 12, 30));
  assert.equal(revisarFranja(new Date(Date.UTC(2026, 8, 2, 15)), CFG, nueveYMedia).motivo, 'muy_pronto');
  // Mas de 10 dias adelante.
  const lejos = new Date(Date.UTC(2026, 9, 15, 15));
  assert.equal(revisarFranja(lejos, CFG, AHORA).motivo, 'muy_lejos');
});

// ── horarios distintos por dia ───────────────────────────────────────────────

const { franjaDelDia } = require('../src/agenda/eleccion');

/**
 * La disponibilidad real de Scalerics en Calendly cambia segun el dia:
 *
 *   lun 08-20 · mar 08-20 · mie 10-20 · jue 07-20 · vie 08-20
 *
 * El bot tenia una sola franja para todos, asi que no habia forma de que
 * coincidiera: con la mas angosta perdia las mañanas de cuatro dias, y con la
 * mas ancha ofrecia horas que Calendly no da.
 */
const HORARIOS = 'mon:08:00-20:00,tue:08:00-20:00,wed:10:00-20:00,thu:07:00-20:00,fri:08:00-20:00';

test('cada dia tiene su franja', () => {
  assert.deepEqual(franjaDelDia('mon', HORARIOS), { desde: 480, hasta: 1200 });
  assert.deepEqual(franjaDelDia('wed', HORARIOS), { desde: 600, hasta: 1200 });
  assert.deepEqual(franjaDelDia('thu', HORARIOS), { desde: 420, hasta: 1200 });
});

test('un dia que no esta en la lista no se atiende', () => {
  assert.equal(franjaDelDia('sat', HORARIOS), null);
  assert.equal(franjaDelDia('sun', HORARIOS), null);
});

test('sin la lista, cae a la franja unica de siempre', () => {
  // Para no romper una instalacion que solo tenga AGENDA_DESDE/HASTA.
  assert.deepEqual(franjaDelDia('mon', '', { desde: '12:00', hasta: '16:00', dias: 'mon,tue' }),
    { desde: 720, hasta: 960 });
  assert.equal(franjaDelDia('wed', '', { desde: '12:00', hasta: '16:00', dias: 'mon,tue' }), null);
});

test('revisarFranja respeta el horario del dia que toca', () => {
  const cfg = { ...CFG, AGENDA_HORARIOS: HORARIOS };
  // Miercoles 2 de setiembre: abre 10:00, asi que las 09:00 no.
  const mie9 = new Date(Date.UTC(2026, 8, 2, 12));
  const ahoraTemprano = new Date(Date.UTC(2026, 8, 2, 5));
  assert.equal(revisarFranja(mie9, cfg, ahoraTemprano).motivo, 'fuera_de_franja');
  // Jueves 3 a las 08:00 si, porque el jueves abre 07:00.
  const jue8 = new Date(Date.UTC(2026, 8, 3, 11));
  assert.equal(revisarFranja(jue8, cfg, ahoraTemprano).ok, true);
});

// ── como se le explica al lead que ese horario no se puede ───────────────────

const { textoDeFranja } = require('../src/agenda/eleccion');

/**
 * El 3-9 el bot se contradijo en dos mensajes seguidos:
 *
 *   → Tengo libre viernes 4 de 10:00 a 19:00
 *   ← Y el 17 a las 8?
 *   → Los horarios que manejamos son entre las 12 y las 16hs
 *   ← Pero si dijiste que era de 10 a 19
 *
 * El mensaje de "fuera de franja" leia AGENDA_DESDE/AGENDA_HASTA, que quedaron
 * en los valores viejos cuando la config paso a horarios por dia. Citaba una
 * variable muerta.
 */
test('la franja que se le dice al lead sale del dia que pidio', () => {
  const cfg = {
    TZ, AGENDA_DESDE: '12:00', AGENDA_HASTA: '16:00',
    AGENDA_HORARIOS: 'mon:10:00-19:00,tue:10:00-19:00,wed:10:00-19:00,thu:10:00-19:00,fri:10:00-19:00',
  };
  // Viernes 4, cualquier hora: la franja de ese dia es 10 a 19.
  const viernes = new Date(Date.UTC(2026, 8, 4, 11));
  assert.equal(textoDeFranja(viernes, cfg), '10:00 a 19:00');
  assert.ok(!textoDeFranja(viernes, cfg).includes('12:00'), 'no repite la config vieja');
});

test('sin horarios por dia, cae a la franja unica', () => {
  const cfg = { TZ, AGENDA_DESDE: '12:00', AGENDA_HASTA: '16:00', AGENDA_DIAS: 'mon,tue,wed,thu,fri' };
  assert.equal(textoDeFranja(new Date(Date.UTC(2026, 8, 4, 15)), cfg), '12:00 a 16:00');
});

// ── el dia del que se viene hablando ─────────────────────────────────────────

const { nombroAlgunDia } = require('../src/agenda/eleccion');

/**
 * El 3-9, con el lead preguntando por el viernes 11:
 *
 *   ← A las 9:59
 *   → Ese horario no entra en nuestra franja de atención. Tenemos libres el
 *     viernes 4 desde las 10 hasta las 19, o el lunes 7...
 *   ← A las 10:23
 *   → Ese horario no entra en nuestra franja de 10:00 a 19:00.
 *
 * La hora suelta se resolvia contra HOY, porque es el unico dia que se le pasa
 * al modelo. Asi "10:23" caia en un jueves que ya habia pasado y el rechazo
 * salia con un motivo inventado, ademas de volver a los dias de la lista en vez
 * de seguir en el que el lead estaba mirando.
 *
 * Quien nombra un dia se sabe leyendo el mensaje, asi que lo mira el codigo.
 */
test('una hora suelta no nombra ningún día', () => {
  assert.equal(nombroAlgunDia('A las 10:23'), false);
  assert.equal(nombroAlgunDia('10:15'), false);
  assert.equal(nombroAlgunDia('9:30 daleee'), false);
  assert.equal(nombroAlgunDia('a las 14'), false);
});

test('pero un día nombrado sí, por nombre o por número', () => {
  assert.equal(nombroAlgunDia('El 11 a las 10'), true);
  assert.equal(nombroAlgunDia('el viernes 18 a las 14'), true);
  assert.equal(nombroAlgunDia('mañana a las 10'), true);
  assert.equal(nombroAlgunDia('hoy si se puede?'), true);
  assert.equal(nombroAlgunDia('11 de setiembre'), true);
});

// ── agendar dia-primero-hora-despues: elegir de una lista numerada ──────────

const { elegirDiaPorCodigo, elegirHoraPorCodigo } = require('../src/agenda/eleccion');

const MIE23 = instanteLocal('2026-09-23', 12, 0, TZ);
const JUE24 = instanteLocal('2026-09-24', 12, 0, TZ);
const DIAS = [MIE23, JUE24];

test('elegirDiaPorCodigo: por numero de lista', () => {
  assert.equal(elegirDiaPorCodigo('1', DIAS, TZ), MIE23);
  assert.equal(elegirDiaPorCodigo('2', DIAS, TZ), JUE24);
  assert.equal(elegirDiaPorCodigo('2.', DIAS, TZ), JUE24, 'con punto');
  assert.equal(elegirDiaPorCodigo('opcion 2', DIAS, TZ), JUE24);
});

test('elegirDiaPorCodigo: por nombre del dia o por fecha', () => {
  assert.equal(elegirDiaPorCodigo('miercoles', DIAS, TZ), MIE23);
  assert.equal(elegirDiaPorCodigo('el jueves', DIAS, TZ), JUE24);
  assert.equal(elegirDiaPorCodigo('el 24', DIAS, TZ), JUE24);
  assert.equal(elegirDiaPorCodigo('24/9', DIAS, TZ), JUE24);
});

test('elegirDiaPorCodigo: un numero fuera de rango no elige nada', () => {
  assert.equal(elegirDiaPorCodigo('5', DIAS, TZ), null);
  assert.equal(elegirDiaPorCodigo('0', DIAS, TZ), null);
});

test('elegirDiaPorCodigo: un dia real que no es ninguno de los ofrecidos no elige nada', () => {
  assert.equal(elegirDiaPorCodigo('viernes', DIAS, TZ), null);
  assert.equal(elegirDiaPorCodigo('el 30', DIAS, TZ), null);
});

test('elegirDiaPorCodigo: entradas raras no rompen nada', () => {
  assert.equal(elegirDiaPorCodigo('', DIAS, TZ), null);
  assert.equal(elegirDiaPorCodigo('   ', DIAS, TZ), null);
  assert.equal(elegirDiaPorCodigo('¿cuánto sale?', DIAS, TZ), null);
  assert.equal(elegirDiaPorCodigo(null, DIAS, TZ), null);
  assert.equal(elegirDiaPorCodigo(undefined, DIAS, TZ), null);
  assert.equal(elegirDiaPorCodigo('hola', [], TZ), null, 'sin dias ofrecidos');
});

/**
 * Revision del PR #90: "mañana" nombra un dia real (nombroAlgunDia ya lo
 * detecta), pero elegirDiaPorCodigo no lo resolvia contra ninguna fecha, asi
 * que el lead que contestaba "mañana" recibia "ese dia no esta" aunque mañana
 * fuera justo uno de los dos dias ofrecidos.
 */
test('elegirDiaPorCodigo: "hoy", "mañana" y "pasado mañana" se resuelven contra la fecha real', () => {
  const ahoraFijo = instanteLocal('2026-09-22', 10, 0, TZ); // martes 22
  assert.equal(elegirDiaPorCodigo('hoy', DIAS, TZ, ahoraFijo), null, 'hoy (22) no esta ofrecido');
  assert.equal(elegirDiaPorCodigo('mañana', DIAS, TZ, ahoraFijo), MIE23, 'mañana (23) SI esta ofrecido');
  assert.equal(elegirDiaPorCodigo('Mañana', DIAS, TZ, ahoraFijo), MIE23, 'con mayuscula');
  assert.equal(elegirDiaPorCodigo('manana', DIAS, TZ, ahoraFijo), MIE23, 'sin tilde');
  assert.equal(elegirDiaPorCodigo('pasado mañana', DIAS, TZ, ahoraFijo), JUE24, 'pasado mañana (24) tambien esta');
});

test('elegirDiaPorCodigo: "mañana" que NO esta entre los ofrecidos no elige nada', () => {
  const ahoraFijo = instanteLocal('2026-09-01', 10, 0, TZ); // mañana seria el 2, lejos de la lista
  assert.equal(elegirDiaPorCodigo('mañana', DIAS, TZ, ahoraFijo), null);
});

test('elegirDiaPorCodigo: "hoy" cuando hoy SI esta entre los ofrecidos', () => {
  const ahoraFijo = instanteLocal('2026-09-23', 10, 0, TZ); // hoy es el 23
  assert.equal(elegirDiaPorCodigo('hoy', DIAS, TZ, ahoraFijo), MIE23);
  assert.equal(elegirDiaPorCodigo('dale, hoy mismo', DIAS, TZ, ahoraFijo), MIE23);
});

const HORAS = [12, 12.5, 13, 13.5, 14].map(
  (h) => instanteLocal('2026-09-23', Math.floor(h), h % 1 ? 30 : 0, TZ),
);

test('elegirHoraPorCodigo: por numero de lista, tiene prioridad sobre la hora', () => {
  // "3" es la opcion 3 (13:00), no las 3 de la mañana ni las 15.
  assert.equal(elegirHoraPorCodigo('3', HORAS, TZ), HORAS[2]);
  assert.equal(elegirHoraPorCodigo('1', HORAS, TZ), HORAS[0]);
});

test('elegirHoraPorCodigo: por la hora, cuando no es un indice valido', () => {
  assert.equal(elegirHoraPorCodigo('13:00', HORAS, TZ), HORAS[2]);
  assert.equal(elegirHoraPorCodigo('a las 13', HORAS, TZ), HORAS[2]);
  assert.equal(elegirHoraPorCodigo('12:30', HORAS, TZ), HORAS[1]);
});

test('elegirHoraPorCodigo: fuera de rango o sin nada reconocible no elige nada', () => {
  assert.equal(elegirHoraPorCodigo('8', HORAS, TZ), null, 'como indice, fuera de rango (solo hay 5)');
  assert.equal(elegirHoraPorCodigo('20', HORAS, TZ), null, 'ni como indice ni como hora ofrecida');
  assert.equal(elegirHoraPorCodigo('la primera', HORAS, TZ), null, 'eso lo entiende el modelo, no el codigo');
  assert.equal(elegirHoraPorCodigo('', HORAS, TZ), null);
  assert.equal(elegirHoraPorCodigo(null, HORAS, TZ), null);
});

/**
 * Revision del PR #90: con la franja de siempre (12:00 a 15:30, 7-8 turnos)
 * el indice y la hora nunca chocan, asi que el bug no se notaba. Con una
 * franja mas ancha (07:00 a 20:00, 26 turnos de media hora) "12" como INDICE
 * es el turno de las 12:30 (el numero 12), pero casi todo el mundo que
 * escribe "12" quiere decir las 12:00 — que es el turno numero 11. Un cambio
 * de AGENDA_* activaba esto sin aviso.
 */
const HORAS_26 = Array.from({ length: 26 }, (_, i) => {
  const minutos = 7 * 60 + i * 30; // 07:00 en adelante, cada 30 min
  return instanteLocal('2026-09-23', Math.floor(minutos / 60), minutos % 60, TZ);
});

test('elegirHoraPorCodigo: un numero suelto que coincide con una hora en punto gana sobre el indice', () => {
  // Item 11 = 12:00 (07:00 + 10*30min). "12" tiene que ser las 12:00, no el
  // item 12 (que serian las 12:30).
  assert.equal(elegirHoraPorCodigo('12', HORAS_26, TZ), HORAS_26[10]);
  assert.match(new Intl.DateTimeFormat('es-UY', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false })
    .format(elegirHoraPorCodigo('12', HORAS_26, TZ)), /^12:00$/);
});

test('elegirHoraPorCodigo: sin choque con ninguna hora, sigue siendo el indice', () => {
  // No hay ningun turno a las 3 (van de 07 a 20): "3" es el item 3 (08:00).
  assert.equal(elegirHoraPorCodigo('3', HORAS_26, TZ), HORAS_26[2]);
});

test('elegirHoraPorCodigo: "opcion N" o "N." fuerzan el indice, sin ambiguedad', () => {
  // Item 12 = 12:30. Con "opcion" o el punto, es inequivocamente el indice,
  // aunque el numero tambien coincida con una hora en punto de la lista.
  assert.equal(elegirHoraPorCodigo('12.', HORAS_26, TZ), HORAS_26[11]);
  assert.equal(elegirHoraPorCodigo('opcion 12', HORAS_26, TZ), HORAS_26[11]);
});
