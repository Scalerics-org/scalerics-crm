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

/** "08:00" -> 480. */
function aMinutos(hhmm) {
  const [h, m] = String(hhmm).split(':').map(Number);
  return h * 60 + (m || 0);
}

/**
 * La franja de atencion de un dia de la semana, en minutos desde medianoche.
 *
 * La disponibilidad real no es la misma todos los dias. La de Scalerics en
 * Calendly, por ejemplo:
 *
 *   lun 08-20 · mar 08-20 · mie 10-20 · jue 07-20 · vie 08-20
 *
 * Con una sola franja para todos no habia forma de que el bot coincidiera: con
 * la mas angosta perdia las mañanas de cuatro dias, y con la mas ancha ofrecia
 * horas que el calendario no da.
 *
 * @param {string} horarios "mon:08:00-20:00,wed:10:00-20:00,..."
 * @param {object} unica    la franja de siempre, para cuando no hay lista
 * @returns {{desde: number, hasta: number}|null} null si ese dia no se atiende.
 */
function franjaDelDia(diaSemana, horarios, unica = {}) {
  const lista = String(horarios || '').trim();

  if (!lista) {
    const dias = new Set(String(unica.dias || '').split(',').map((d) => d.trim()));
    if (!dias.has(diaSemana)) return null;
    return { desde: aMinutos(unica.desde), hasta: aMinutos(unica.hasta) };
  }

  for (const trozo of lista.split(',')) {
    // No se puede partir por ":" porque el rango tambien los tiene.
    const m = trozo.trim().match(/^([a-z]{3})\s*:\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$/i);
    if (!m || m[1].toLowerCase() !== diaSemana) continue;
    return { desde: aMinutos(m[2]), hasta: aMinutos(m[3]) };
  }
  // Un dia que no figura en la lista no se atiende. Es lo que hace que
  // sabados y domingos queden afuera sin necesitar otra variable.
  return null;
}

/**
 * Si una hora que el lead propone —una que no estaba en la lista— es agendable.
 *
 * La lista que se le muestra son unas pocas sugerencias repartidas en dias, no
 * todo lo que hay libre: en una franja de 12 a 16 cada media hora entran ocho
 * por dia. Decirle que no a un horario que existe es perder la reunion por
 * nada, y fue exactamente lo que paso el 2-9: pidio las 15:00, que estaban
 * libres, y el bot le contesto con la misma lista tres veces seguidas.
 *
 * Esto decide lo que se puede decidir sin mirar el calendario. Que el hueco
 * este libre lo verifica la agenda despues: son dos preguntas distintas y los
 * motivos que se le explican al lead tambien.
 *
 * @returns {{ok: boolean, motivo?: string}}
 */
function revisarFranja(inicio, cfg, ahora = new Date()) {
  const tz = cfg.TZ || 'America/Montevideo';
  const { hora, minuto, diaSemana } = enZona(inicio, tz);

  const franja = franjaDelDia(diaSemana, cfg.AGENDA_HORARIOS, {
    desde: cfg.AGENDA_DESDE, hasta: cfg.AGENDA_HASTA, dias: cfg.AGENDA_DIAS,
  });
  if (!franja) return { ok: false, motivo: 'dia_no_habil' };

  const enMinutos = hora * 60 + minuto;
  const { desde, hasta } = franja;

  if (enMinutos < desde) return { ok: false, motivo: 'fuera_de_franja' };
  // La reunion tiene que TERMINAR dentro de la franja, no solo empezar.
  if (enMinutos + cfg.AGENDA_DURACION_MIN > hasta) return { ok: false, motivo: 'fuera_de_franja' };
  // La grilla de media hora NO se exige. Es una comodidad nuestra para armar la
  // lista de sugerencias, no un limite del negocio: una reunion a las 10:23 se
  // puede tener igual. El 3-9 el bot rechazo esa hora diciendo que no entraba
  // en la franja de 10:00 a 19:00 —y el lead le contesto "pero 10:23 entra en
  // la franja"—. Ademas de perder la reunion, el motivo era falso.

  const piso = ahora.getTime() + cfg.AGENDA_AVISO_MIN_HORAS * 3600_000;
  if (inicio.getTime() < piso) return { ok: false, motivo: 'muy_pronto' };

  const techo = ahora.getTime() + cfg.AGENDA_DIAS_ADELANTE * 86400_000;
  if (inicio.getTime() > techo) return { ok: false, motivo: 'muy_lejos' };

  return { ok: true };
}

/** 480 -> "08:00". */
function aHora(minutos) {
  const h = Math.floor(minutos / 60);
  const m = minutos % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/**
 * La franja de atencion del dia que pidio, en palabras, para decirsela al lead.
 *
 * Sale de la config real y no de AGENDA_DESDE/AGENDA_HASTA. El 3-9 el bot se
 * contradijo en dos mensajes seguidos —ofrecio "de 10:00 a 19:00" y al rechazar
 * un horario dijo "manejamos entre las 12 y las 16"— porque el mensaje de
 * rechazo leia esas dos variables, que habian quedado en los valores viejos
 * cuando la config paso a horarios por dia. El lead lo noto enseguida.
 *
 * @returns {string|null} null si ese dia no se atiende.
 */
function textoDeFranja(fecha, cfg) {
  const tz = cfg.TZ || 'America/Montevideo';
  const { diaSemana } = enZona(fecha, tz);
  const franja = franjaDelDia(diaSemana, cfg.AGENDA_HORARIOS, {
    desde: cfg.AGENDA_DESDE, hasta: cfg.AGENDA_HASTA, dias: cfg.AGENDA_DIAS,
  });
  if (!franja) return null;
  return `${aHora(franja.desde)} a ${aHora(franja.hasta)}`;
}


/**
 * Si el lead nombro algun dia, de cualquier forma.
 *
 * Al modelo que interpreta el momento se le pasa un solo dia de referencia
 * —hoy— asi que una hora suelta la resuelve contra hoy. El 3-9 el lead venia
 * preguntando por el viernes 11, escribio "a las 10:23", y eso cayo en el
 * jueves que ya estaba empezado: el bot lo rechazo con un motivo falso y volvio
 * a ofrecer los dias de la lista, tres veces seguidas.
 *
 * Quien nombra un dia y quien no se sabe leyendo el mensaje. Cuando no nombra
 * ninguno, el dia es el que se viene hablando.
 *
 * "el 11" es un dia; "las 10" y "10:23" son una hora. Por eso mira el articulo
 * y no el numero suelto.
 */
const RE_NOMBRA_DIA = new RegExp(
  '(lunes|martes|miercoles|jueves|viernes|sabado|domingo'
  + '|hoy|manana|pasado'
  + '|el [0-9]{1,2}([^0-9:]|$)'
  + '|[0-9]{1,2} de (enero|febrero|marzo|abril|mayo|junio|julio|agosto|setiembre|septiembre|octubre|noviembre|diciembre))',
  'i',
);

function nombroAlgunDia(texto) {
  // sinAcentos no toca la enie: "manana" se normaliza aparte.
  const t = sinAcentos(texto).split('ñ').join('n');
  return RE_NOMBRA_DIA.test(t);
}

module.exports = {
  horasQueDijo, diasQueNombro, eligioEsaHora, revisarFranja,
  franjaDelDia, textoDeFranja, nombroAlgunDia,
};
