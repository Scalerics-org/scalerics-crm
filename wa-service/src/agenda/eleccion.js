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

/** Numeros en palabras, para "dos" o "la segunda" igual que "2" o "opcion 2". */
const PALABRAS_NUMERO = {
  uno: 1, una: 1, primero: 1, primera: 1,
  dos: 2, segundo: 2, segunda: 2,
  tres: 3, tercero: 3, tercera: 3,
  cuatro: 4,
  cinco: 5,
  seis: 6,
  siete: 7,
  ocho: 8,
  nueve: 9,
  diez: 10,
};

/** Emojis, para sacarlos de un mensaje que aparte de eso es solo un numero. */
const RE_EMOJI = /\p{Emoji_Presentation}|\p{Extended_Pictographic}/gu;

/**
 * Si el mensaje, sacando los adornos de las puntas (signos, parentesis,
 * emojis) y un "opcion"/"el"/"la" adelante, es UN SOLO numero de lista —en
 * digitos o en palabras— lo devuelve. Ninguno de esos adornos cambia lo que
 * el lead quiso decir: "2!", "2 👍", "el 2)" y "dos" son la misma eleccion
 * que "2".
 *
 * `esExplicito` distingue "opcion 2" y "2." —donde no hay AMBIGUEDAD posible
 * con una hora— de un numero suelto tipo "2" o "dos", que en elegirHoraPorCodigo
 * puede perder contra una hora en punto que coincida (ver ahi el porque).
 * "el 2" y "la 2" NO cuentan como explicitos: son la forma mas comun de decir
 * una fecha ("el 2 de setiembre"), y esa lectura ya se prueba antes en
 * elegirDiaPorCodigo — esto es solo el respaldo para cuando esa fecha no es
 * ninguna de las ofrecidas.
 *
 * `conArticulo` avisa que dijo "el 2" / "la 2": ahi el numero puede ser una
 * fecha, y elegirDiaPorCodigo la prueba antes de leerlo como opcion.
 *
 * @returns {{n: number, esExplicito: boolean, conArticulo: boolean}|null}
 */
function indiceDeLista(texto) {
  let t = sinAcentos(String(texto || '')).replace(RE_EMOJI, ' ').trim();

  const lead = t.match(/^[¡!¿?"'(]+\s*/);
  if (lead) t = t.slice(lead[0].length);
  const trail = t.match(/\s*([!?"').,;:]+)$/);
  if (trail) t = t.slice(0, t.length - trail[0].length);
  t = t.trim();

  const m = t.match(/^(opcion\s+|el\s+|la\s+)?(\d{1,2}|[a-z]+)$/);
  if (!m) return null;
  const [, prefijo, token] = m;

  let n;
  if (/^\d+$/.test(token)) {
    n = parseInt(token, 10);
  } else {
    n = PALABRAS_NUMERO[token];
    if (n === undefined) return null;
  }

  const dijoOpcion = /^opcion/.test(prefijo || '');
  const puntoFinal = Boolean(trail && trail[1].includes('.'));
  const conArticulo = /^(el|la)\s/.test(prefijo || '');
  return { n, esExplicito: dijoOpcion || puntoFinal, conArticulo };
}

const NOMBRES_DIA = 'lunes|martes|miercoles|jueves|viernes|sabado|domingo';

/**
 * Si pide ver la semana siguiente: "la semana que viene", "la proxima", "la que
 * viene", y tambien "el jueves que viene" / "el proximo jueves", que nombran un
 * dia de la semana siguiente aunque este mismo dia tambien exista esta semana.
 * Sin esto ultimo, "el jueves que viene" elegia el jueves de ESTA semana.
 */
const RE_SEMANA_QUE_VIENE = new RegExp(
  String.raw`\b(?:semana\s+(?:que\s+viene|proxima|siguiente|entrante)`
  + String.raw`|la\s+proxima|la\s+que\s+viene|la\s+siguiente|proxima\s+semana`
  + String.raw`|(?:${NOMBRES_DIA})\s+que\s+viene|proximo\s+(?:${NOMBRES_DIA}))\b`,
);

function pideSemanaQueViene(texto) {
  return RE_SEMANA_QUE_VIENE.test(sinAcentos(texto));
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
 * Los numeros que pueden ser "el dia" y no una hora.
 *
 * horasQueDijo agarra el "23" de "23:00" igual que el de "el 23": los dos son
 * "un numero de una o dos cifras", y para leer una HORA eso esta bien. Pero
 * nombroEseDia lo usaba para decidir si el lead nombro una FECHA, y ahi "23"
 * con los dos puntos pegados no es un dia, es una hora que quedo fuera de la
 * franja. Paso el 19-8, probando: el bot ofrecio horarios de un dia 23,
 * escribio "23:00" —una hora invalida, no una fecha— y el codigo lo tomo como
 * "nombro el dia 23", confirmo que eligio ESE horario y lo agendo a las 12:00
 * como si el lead hubiera pedido justo eso.
 *
 * Los minutos pegados ("23:00", "13.30") ya delataban la hora. Pero "13hs",
 * "13 h", "tipo 13" y "13 y media" son formas tan comunes de decir una hora
 * como esas, y ninguna tenia dos puntos: Andres escribio "Lunes 17hs" el
 * 11-9, y con la version anterior "17hs" contaba como si hubiera nombrado el
 * dia 17.
 *
 * Se "blanquean" primero las formas que son claramente una hora (para que el
 * numero que les queda pegado, si hay uno, no se lea aparte) y recien despues
 * se buscan los numeros sueltos que quedan, que ahi si pueden ser un dia.
 */
function diasNumeroQueNombro(texto) {
  let t = sinAcentos(String(texto || ''));
  const numeros = [];

  const blanquear = (regex, sacarDia) => {
    t = t.replace(regex, (...args) => {
      const m0 = args[0];
      if (sacarDia) {
        const n = parseInt(sacarDia(args), 10);
        if (n >= 1 && n <= 31) numeros.push(n);
      }
      return ' '.repeat(m0.length);
    });
  };

  // "13/9", "13-9": dia/mes. Solo el primero —el dia— cuenta; el segundo,
  // el mes, no tiene que leerse como otro dia suelto.
  blanquear(/\b(\d{1,2})\s*[/-]\s*\d{1,2}\b/g, (m) => m[1]);

  // "13hs", "13 h", "13hrs", "13 horas", con o sin "y media"/"y cuarto"/"y
  // 30" pegado atras.
  blanquear(/\b\d{1,2}\s*(?:h|hs|hrs?|horas?)\b(?:\s*y\s*(?:media|cuarto|\d{1,2}))?/g);
  // "13 y media", "13 y cuarto", "13 y 30": sin "h" pero igual es una hora.
  blanquear(/\b\d{1,2}\s*y\s*(?:media|cuarto|\d{1,2})\b/g);

  // Con una palabra de hora ADELANTE: "a las 14", "tipo 13", "a eso de las
  // 14", "a partir de las 9", "desde las 9", "despues de las 9", "antes de
  // las 9".
  blanquear(/\b(?:la|las|tipo|a eso de(?: las)?|a partir de(?: las)?|desde(?: las)?|despues de(?: las)?|antes de(?: las)?)\s+\d{1,2}\b/g);

  for (const m of t.matchAll(/(?<![\d:.,])(\d{1,2})(?:[:.](\d{2}))?(?![\d:.,])/g)) {
    if (m[2]) continue; // "23.00": los minutos pegados lo delatan como hora.
    const n = parseInt(m[1], 10);
    if (n >= 1 && n <= 31) numeros.push(n);
  }
  return numeros;
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
  return diasNumeroQueNombro(texto).includes(numeroDelDia);
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

/**
 * "hoy", "mañana", "pasado mañana": cuantos dias sumarle a `ahora` para
 * llegar al dia que nombran. null si no nombran ninguno de estos tres.
 *
 * Aparte de diasQueNombro (nombre del dia de semana) y diasNumeroQueNombro
 * (una fecha): estos tres no traen ni nombre de dia ni numero, asi que sin
 * esto elegirDiaPorCodigo no los reconocia — "mañana" contaba como "nombro un
 * dia" para nombroAlgunDia (que sí lo entiende) pero no para elegirDiaPorCodigo,
 * y el lead que contestaba "mañana" recibia "ese dia no esta" aunque mañana
 * fuera justo uno de los ofrecidos.
 */
function diaRelativoQueNombro(texto) {
  const t = sinAcentos(String(texto || '')).split('ñ').join('n');
  if (/\bpasado\s*manana\b/.test(t)) return 2;
  if (/\bmanana\b/.test(t)) return 1;
  if (/\bhoy\b/.test(t)) return 0;
  return null;
}

/**
 * Si el mensaje trae una negacion. "no", "ni", "tampoco", "imposible" y
 * "complicado" alcanzan para las frases que importan ("no puedo" y "no me
 * sirve" ya traen el "no" suelto).
 *
 * Sin esto, "el miercoles no puedo" se leia como "eligio el miercoles": el
 * codigo encontraba el nombre del dia y no miraba el resto de la frase.
 */
const RE_NEGACION = /\b(no|ni|tampoco|imposible|complicado)\b/;

function tieneNegacion(texto) {
  return RE_NEGACION.test(sinAcentos(String(texto || '')));
}

/** Corta un mensaje en clausulas, para poder mirar cada una por separado. */
function segmentos(texto) {
  return String(texto || '').split(/[,.;]|\bpero\b|\bsino\b/).map((s) => s.trim()).filter(Boolean);
}

/** Si ESE dia ofrecido esta nombrado en ESE segmento (por nombre, fecha, o relativo). */
function diaEnSegmento(segmento, dia, tz, ahora) {
  const { diaSemana, dia: diaISO } = enZona(dia, tz);
  if (diasQueNombro(segmento).includes(diaSemana)) return true;
  if (diasNumeroQueNombro(segmento).includes(Number(diaISO.slice(8)))) return true;

  const relativo = diaRelativoQueNombro(segmento);
  if (relativo !== null) {
    const { dia: diaRelISO } = enZona(new Date(ahora.getTime() + relativo * 86400_000), tz);
    if (diaRelISO === diaISO) return true;
  }
  return false;
}

/**
 * Cuando el mensaje trae una negacion, se corta en clausulas y cada una se
 * mira por separado: un dia nombrado en una clausula CON negacion queda
 * descartado; uno nombrado en una clausula SIN negacion queda afirmado.
 *
 * "el jueves mejor, el miercoles no puedo" -> jueves afirmado, miercoles
 * descartado -> se elige el jueves. "no, el jueves" -> el "no" esta solo, sin
 * ningun dia al lado -> no descarta nada, y "el jueves" en su propia clausula
 * queda afirmado -> se elige el jueves. "el miercoles no puedo" -> una sola
 * clausula, con negacion y con el miercoles -> descartado, nada afirmado ->
 * null: mejor no adivinar que dejarlo pasar como si fuera una eleccion.
 *
 * Si queda mas de un dia afirmado, o el mismo dia queda afirmado en una
 * clausula y descartado en otra, tambien null: ambiguo no es lo mismo que
 * claro.
 */
function elegirDiaConNegacion(texto, diasOfrecidos, tz, ahora) {
  const afirmados = new Set();
  const descartados = new Set();
  for (const seg of segmentos(texto)) {
    const segNegado = tieneNegacion(seg);
    for (const d of diasOfrecidos) {
      if (diaEnSegmento(seg, d, tz, ahora)) (segNegado ? descartados : afirmados).add(d.getTime());
    }
  }
  const candidatos = diasOfrecidos.filter((d) => afirmados.has(d.getTime()) && !descartados.has(d.getTime()));
  return candidatos.length === 1 ? candidatos[0] : null;
}

/**
 * Cual de los DIAS ofrecidos (agendar dia-primero-hora-despues) eligio,
 * resuelto en codigo: la lista y la eleccion las resuelve el codigo, no el
 * modelo, para que "1" o "2" nunca dependan de una interpretacion.
 *
 * Con una negacion en el medio, ver elegirDiaConNegacion: "priorizar no
 * equivocarse sobre resolver mas" — si no queda clarisimo, null.
 *
 * Sin negacion, cuatro formas, en este orden de prioridad:
 *  1. "hoy" / "mañana" / "pasado mañana", contra la fecha real de `ahora`.
 *  2. El nombre del dia de semana ("el viernes", "miercoles").
 *  3. Un numero de fecha que coincide con alguno de los ofrecidos ("el 24",
 *     "24/9"). diasNumeroQueNombro ya descarta los numeros que son en
 *     realidad una hora.
 *  4. Un numero de lista corto ("1", "2.", "opcion 1") — SOLO si el mensaje
 *     es basicamente ese numero y nada mas: un "12" dentro de una frase mas
 *     larga no es "elijo la opcion 12", es otra cosa (una hora, un error).
 *
 * @param {string} texto
 * @param {Date[]} diasOfrecidos en el mismo orden en que se listaron.
 * @param {string} tz
 * @param {Date} ahora para resolver "hoy"/"mañana"/"pasado mañana". Inyectable
 *   para los tests; en produccion es el reloj real.
 * @returns {Date|null}
 */
function elegirDiaPorCodigo(texto, diasOfrecidos, tz = 'America/Montevideo', ahora = new Date()) {
  const t = sinAcentos(String(texto || ''));

  if (tieneNegacion(t)) return elegirDiaConNegacion(t, diasOfrecidos, tz, ahora);

  const relativo = diaRelativoQueNombro(t);
  if (relativo !== null) {
    const { dia: diaBuscado } = enZona(new Date(ahora.getTime() + relativo * 86400_000), tz);
    // Es un dia real y puntual: si no esta entre los ofrecidos, no hay nada
    // mas que probar (no es "quiza nombro otra cosa") — el que llama decide
    // que hacer con un dia real que no es ninguno de los que hay.
    return diasOfrecidos.find((d) => enZona(d, tz).dia === diaBuscado) || null;
  }

  const nombrados = diasQueNombro(t);
  for (const d of diasOfrecidos) {
    if (nombrados.includes(enZona(d, tz).diaSemana)) return d;
  }

  const idx = indiceDeLista(t);
  const enLista = (n) => (n >= 1 && n <= diasOfrecidos.length ? diasOfrecidos[n - 1] : null);

  /**
   * Un numero SOLO ("2", "2!", "dos") contestando una lista numerada es el
   * numero de la opcion, no el dia del mes. Con la semana que viene en la
   * lista pasa seguido que los dos se pisan: "1. Lunes 28 ... 5. Viernes 2", y
   * el "2" es el martes 29 para quien lee la lista y el viernes 2 para quien
   * lee fechas. "el 2" y "la 2" son las que dicen fecha, y esas se prueban
   * abajo, antes de caer al indice. Un numero solo que no es opcion de la
   * lista ("25" con tres dias) sigue pudiendo ser una fecha.
   */
  if (idx && !idx.conArticulo) {
    const porNumero = enLista(idx.n);
    if (porNumero) return porNumero;
  }

  const numerados = diasNumeroQueNombro(t);
  for (const d of diasOfrecidos) {
    const { dia } = enZona(d, tz);
    if (numerados.includes(Number(dia.slice(8)))) return d;
  }

  if (idx) return enLista(idx.n);

  return null;
}

/**
 * Cual de las HORAS ofrecidas de un dia ya elegido eligio, resuelto en
 * codigo. Mismo criterio que elegirDiaPorCodigo: numero de lista, o una hora
 * que coincide exactamente con alguna de las ofrecidas (reloj de 24 o de 12).
 *
 * A diferencia del dia, ACA no hace falta mirar si el numero "podria ser
 * otra cosa": en el paso de horas, un numero de 1 a 2 cifras que no es un
 * indice de lista solo puede ser una hora.
 *
 * Un numero suelto, sin "opcion" ni punto, es ambiguo si la franja es ancha:
 * con turnos de 07:00 a 20:00, "12" puede ser "la opcion numero 12" (que
 * segun cuantos turnos entren antes puede ser cualquier hora) o "las 12:00"
 * —que es como lo va a leer casi todo el mundo—. Con la franja de siempre
 * (12:00 a 15:30) nunca chocan, asi que esto no se notaba; cualquier cambio
 * de AGENDA_* lo iba a activar sin aviso. Gana la hora en punto cuando el
 * numero coincide con una; "opcion 12" o "12." siguen siendo el indice, sin
 * ambiguedad posible.
 *
 * @param {string} texto
 * @param {Date[]} horasOfrecidas
 * @param {string} tz
 * @returns {Date|null}
 */
function elegirHoraPorCodigo(texto, horasOfrecidas, tz = 'America/Montevideo') {
  const t = sinAcentos(String(texto || '')).trim();

  const idx = indiceDeLista(t);
  if (idx) {
    const { n, esExplicito } = idx;

    if (!esExplicito) {
      const porHora = horasOfrecidas.find((d) => {
        const { hora, minuto } = enZona(d, tz);
        return minuto === 0 && n === hora;
      });
      if (porHora) return porHora;
    }

    const i = n - 1;
    if (i >= 0 && i < horasOfrecidas.length) return horasOfrecidas[i];
  }

  /**
   * Con mas texto que un numero solo, puede venir una negacion mezclada
   * ("a las 12 no puedo", "12 no, 13 si"): sin esto, horasQueDijo saca el
   * numero igual, sin mirar el "no" al lado, y agendaba justo lo que el lead
   * dijo que no podia.
   */
  if (tieneNegacion(t)) return elegirHoraConNegacion(t, horasOfrecidas, tz);

  /**
   * Si trae minutos explicitos ("13:30", "13.30"), tienen que coincidir.
   *
   * horasQueDijo descarta los minutos a proposito —"alcanza con la hora
   * para agarrar el error que importa"— pero eso vale para elegirEsaHora,
   * que compara contra UN solo horario ya elegido. Aca se compara contra
   * una LISTA con paso de media hora, y con eso "13:30" matcheaba el primer
   * horario de las 13 en punto, no el que de verdad pidio.
   */
  const conMinutos = t.match(/(?<![\d:.,])(\d{1,2})[:.](\d{2})(?![\d:.,])/);
  if (conMinutos) {
    const hh = parseInt(conMinutos[1], 10);
    const mm = parseInt(conMinutos[2], 10);
    for (const d of horasOfrecidas) {
      const { hora, minuto } = enZona(d, tz);
      if (minuto === mm && (hh === hora || (hh + 12) === hora || (hh - 12) === hora)) return d;
    }
    return null;
  }

  const dichas = horasQueDijo(t);
  if (!dichas.length) return null;
  for (const d of horasOfrecidas) {
    const { hora } = enZona(d, tz);
    if (dichas.some((h) => h === hora || (h + 12) === hora || (h - 12) === hora)) return d;
  }
  return null;
}

/**
 * Si ESA hora ofrecida esta nombrada en ESE segmento (con o sin minutos
 * explicitos). Sin minutos, solo cuenta el turno EN PUNTO: "12 no" no puede
 * marcar el turno de las 12:30 como descartado, o "13 si" en la misma frase
 * terminaria confundiendose con el de las 13:30.
 */
function horaEnSegmento(segmento, d, tz) {
  const { hora, minuto } = enZona(d, tz);
  const conMinutos = segmento.match(/(?<![\d:.,])(\d{1,2})[:.](\d{2})(?![\d:.,])/);
  if (conMinutos) {
    const hh = parseInt(conMinutos[1], 10);
    const mm = parseInt(conMinutos[2], 10);
    return minuto === mm && (hh === hora || (hh + 12) === hora || (hh - 12) === hora);
  }
  if (minuto !== 0) return false;
  const dichas = horasQueDijo(segmento);
  return dichas.some((h) => h === hora || (h + 12) === hora || (h - 12) === hora);
}

/**
 * Mismo criterio que elegirDiaConNegacion, para las horas: por clausula, una
 * hora nombrada junto con una negacion queda descartada; sin negacion en su
 * clausula, afirmada. Si queda exactamente una afirmada (y no descartada en
 * otra clausula), esa gana; si no, null.
 */
function elegirHoraConNegacion(texto, horasOfrecidas, tz) {
  const afirmadas = new Set();
  const descartadas = new Set();
  for (const seg of segmentos(texto)) {
    const segNegado = tieneNegacion(seg);
    for (const d of horasOfrecidas) {
      if (horaEnSegmento(seg, d, tz)) (segNegado ? descartadas : afirmadas).add(d.getTime());
    }
  }
  const candidatas = horasOfrecidas.filter((d) => afirmadas.has(d.getTime()) && !descartadas.has(d.getTime()));
  return candidatas.length === 1 ? candidatas[0] : null;
}

module.exports = {
  horasQueDijo, diasQueNombro, diasNumeroQueNombro, eligioEsaHora, revisarFranja,
  franjaDelDia, textoDeFranja, nombroAlgunDia, elegirDiaPorCodigo, elegirHoraPorCodigo,
  tieneNegacion,
  indiceDeLista,
  pideSemanaQueViene,
};
