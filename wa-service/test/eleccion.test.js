'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { horasQueDijo, eligioEsaHora } = require('../src/agenda/eleccion');

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
  // 15:45 no: no esta en la grilla de media hora.
  assert.equal(revisarFranja(alas(15, 45), CFG, AHORA).motivo, 'fuera_de_franja');
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
