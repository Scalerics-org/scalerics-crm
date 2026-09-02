'use strict';

const { enZona } = require('./gcal');

/**
 * El control que le falta a "el modelo interpreta, el codigo confirma".
 *
 * Cual de los horarios ofrecidos eligio el lead lo resuelve el modelo, porque
 * entiende "las 13", "la primera" y "a la una y media". Eso esta bien y no se
 * puede hacer con una regla. Pero el modelo elige de un enum cerrado con los
 * horarios que se ofrecieron, asi que cuando el lead pide uno que NO esta en la
 * lista no puede decir "ninguno": devuelve el que menos le disgusta.
 *
 * Paso el 2-9, probando:
 *
 *   → Tenemos estos horarios: 12:00, 12:30, 13:00, 13:30, 14:30. ¿Cuál te viene bien?
 *   ← 15:00
 *   → Perfecto. Jueves 3 a las 12:00. Te paso el link...
 *
 * El codigo confirmaba que el horario existiera y siguiera libre —las dos cosas
 * eran ciertas— pero no que fuera el que el lead pidio. Con un cliente real eso
 * es alguien conectandose a una hora y nosotros a otra.
 *
 * Asi que cuando el lead escribe una hora, esa hora tiene que ser la del
 * horario elegido. Cuando no escribe ninguna —"la primera", "dale esa"— no hay
 * nada que verificar y manda el modelo, que para eso esta.
 */

/**
 * Las horas que el lead escribio.
 *
 * Solo numeros que pueden ser una hora del dia. Un "somos 45" o un "2026" no lo
 * son, y tomarlos como hora rechazaria una eleccion buena.
 *
 * Los minutos no se miran: alcanza con la hora para agarrar el error que
 * importa —pedir las 15 y que agende las 12— y mirarlos traeria el problema de
 * distinguir "12:30" de "12 30".
 */
function horasQueDijo(texto) {
  const t = String(texto || '');
  const horas = [];
  // La hora de "15:00" o "15.00" es lo de antes de los dos puntos; suelta, es
  // el numero entero. El grupo de minutos se captura para poder descartarlo.
  for (const m of t.matchAll(/(?<![\d:.,])(\d{1,2})(?:[:.](\d{2}))?(?![\d:.,])/g)) {
    const h = parseInt(m[1], 10);
    if (h >= 0 && h <= 23) horas.push(h);
  }
  return horas;
}

const DIAS_SEMANA = {
  lunes: 'mon', martes: 'tue', miercoles: 'wed', miércoles: 'wed',
  jueves: 'thu', viernes: 'fri', sabado: 'sat', sábado: 'sat', domingo: 'sun',
};

function sinAcentos(texto) {
  return String(texto || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
}

/** Los dias de semana que nombro, en el codigo corto que usa enZona. */
function diasQueNombro(texto) {
  const t = sinAcentos(texto);
  const salida = new Set();
  for (const [nombre, codigo] of Object.entries(DIAS_SEMANA)) {
    const limpio = sinAcentos(nombre);
    if (new RegExp(`(?<![\\p{L}])${limpio}(?![\\p{L}])`, 'u').test(t)) salida.add(codigo);
  }
  return [...salida];
}

/**
 * Si el lead nombro el dia del horario elegido, por nombre o por numero.
 *
 * Desde que los horarios abarcan varios dias, elegir diciendo "el viernes" es
 * normal. Mirando solo numeros, "el 4" parece la hora 4 y rechazaria una
 * eleccion buena.
 */
function nombroEseDia(texto, elegido, tz) {
  const { dia, diaSemana } = enZona(elegido, tz);
  if (diasQueNombro(texto).includes(diaSemana)) return true;

  const numeroDelDia = Number(dia.slice(8));
  return horasQueDijo(texto).includes(numeroDelDia);
}

/**
 * Si se le puede creer al modelo que el lead eligio ese horario.
 *
 * Tres casos:
 *  - No escribio ni hora ni dia ("la primera", "dale esa"): manda el modelo,
 *    que para eso esta.
 *  - Nombro un dia: tiene que ser el del horario elegido.
 *  - Escribio una hora: tiene que ser la del horario elegido, salvo que el dia
 *    que nombro ya lo confirme —"el viernes 4" trae un 4 que es fecha, no hora.
 *
 * @param {string} texto lo que escribio el lead
 * @param {Date} elegido el horario que el modelo dice que eligio
 * @param {string} tz
 * @returns {boolean}
 */
function eligioEsaHora(texto, elegido, tz = 'America/Montevideo') {
  const diasNombrados = diasQueNombro(texto);
  if (diasNombrados.length) {
    const { diaSemana } = enZona(elegido, tz);
    // Nombro OTRO dia: eso no es el horario que eligio, sin importar la hora.
    if (!diasNombrados.includes(diaSemana)) return false;
  }

  const dichas = horasQueDijo(texto);
  if (!dichas.length) return true;

  const { hora } = enZona(elegido, tz);
  // "a las 3" es las 15: la gente habla en reloj de 12 y rechazar eso seria
  // peor que el bug, porque dejaria sin agendar a quien eligio bien.
  if (dichas.some((h) => h === hora || (h + 12) === hora || (h - 12) === hora)) return true;

  // Ninguna hora coincide, pero puede que esos numeros fueran la fecha.
  return nombroEseDia(texto, elegido, tz);
}

module.exports = { horasQueDijo, diasQueNombro, eligioEsaHora };
