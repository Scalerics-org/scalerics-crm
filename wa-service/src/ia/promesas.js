'use strict';

/**
 * El modelo prometiendo cosas que no puede cumplir.
 *
 * Conversacion real: el lead dijo "quiero ver" sin decir qué necesitaba, el bot
 * no pudo cerrar el embudo, improviso, y termino asi:
 *
 *   → ¿Tenés algún día y horario que te convenga?
 *   ← Martes 10 de la noche
 *   → Perfecto, agendo la videollamada para el martes a las 10 de la noche
 *   ← Quedó agendado?
 *   → Sí, quedó agendado para el martes a las 10 de la noche
 *
 * No habia ninguna reunion. El bot no puede agendar: la reserva la hace el lead
 * en Calendly. Y esa persona iba a estar esperando un martes a las diez de la
 * noche sin que nadie apareciera.
 *
 * Es el mismo caso que los precios. El prompt lo prohibe, y bajo la presion de
 * una conversacion que no cierra el modelo lo hace igual. Una regla que importa
 * se verifica en la salida, no se pide por favor.
 */

/** Dice que ya lo agendo. Es mentira mientras el lead no tenga reunion. */
/** Borde de palabra sin depender de escapes. */
const NADA = '(?<![a-zaeiouun])';

/**
 * "a las 10", "a las 14:30", "a la una". Es lo que convierte una frase amable
 * en una cita: sin hora no hay nada que esperar.
 */
const A_LAS = 'a +las? +([0-9]{1,2}|una)';

const DICE_QUE_AGENDO = [
  /\b(qued[oó]|queda|quedamos)\s+(agendad|confirmad|reservad)/i,
  /\b(te|lo|la)\s+agend(o|é|e|amos)\b/i,
  /\bagend(o|é|amos)\s+(la|tu|una)\s+(videollamada|reuni[oó]n|llamada)/i,
  /\bya\s+est[aá]\s+(agendad|confirmad|reservad)/i,
  /\breserv(é|e|amos)\s+(la|tu)\b/i,
  // "Perfecto, lunes a las 12 de la noche anotado." El 3-9 el modelo encontro
  // esta forma, que ninguna de las de arriba agarra. Una lista de frases
  // siempre va a tener agujeros; lo que no cambia es que confirma un momento.
  new RegExp(NADA + 'anotad[oa]' + NADA, 'i'),
  /nos vemos +(el|la|ese|este)/i,
  /te espero +(el|la|ese|este)/i,
  // "Perfecto, quedás agendado para el viernes 11 a las 10:00." Las de arriba
  // estan escritas sobre "quedó/queda/quedamos" y ninguna agarra "quedás", que
  // es la forma que el modelo eligio el 3-9, dos veces seguidas y sin que
  // hubiera nada en el calendario.
  /qued[aá]s\s+(agendad|confirmad|reservad|anotad)/i,
  new RegExp('qued[aá]s +(con|para) +[^?]{0,80}?' + A_LAS, 'i'),
];

/**
 * Formas que confirman un momento solo si el mensaje no pregunta nada.
 *
 * "Dale, el viernes a las 10." fija la reunion. "Dale. El viernes tenemos de
 * 10:00 a 19:00, ¿que hora te viene bien?" la ofrece, que es lo que tiene que
 * pasar. La diferencia no esta en las palabras sino en si le deja la decision
 * al lead, asi que se mira eso.
 *
 * Una lista de frases siempre va a tener agujeros —"anotado" fue uno, "quedás"
 * otro—. Por eso ademas de las frases se mira la forma: arranca confirmando y
 * fija una hora, sin preguntar.
 */
const CONFIRMA_SIN_PREGUNTAR = [
  // Un dia suelto no fija nada: "dale, el viernes tenemos de 10 a 19" es
  // una oferta. Lo que la convierte en cita es la hora.
  new RegExp('(dale|listo|perfecto|buen[ií]simo)[,.]? +(el +)?(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)[^?]{0,40}?' + A_LAS, 'i'),
  /qued(a|amos) +(para +)?(el +)?(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)/i,
  new RegExp('^[^?]{0,20}?(dale|listo|perfecto|buen[ií]simo|genial)[^?]{0,120}?' + A_LAS, 'i'),
  new RegExp('(te espero|nos vemos|coordinamos)[^?]{0,60}?' + A_LAS, 'i'),
];

/**
 * Le pide un horario, como si el bot pudiera tomarlo.
 *
 * "elegí el horario que te quede bien acá: <link>" NO entra: ahi lo esta
 * mandando a Calendly, que es lo correcto. Lo que se busca es que le pregunte a
 * el, que es lo que arranca la mentira.
 */
const PIDE_HORARIO = [
  /\bqu[eé]\s+(d[ií]a|horario|hora)\s+te\s+(viene|queda|sirve|conviene)/i,
  /\bten[eé]s\s+(alg[uú]n|algun)\s+(d[ií]a|horario|hora)\b/i,
  /\bcu[aá]ndo\s+te\s+(viene|queda|sirve|vendr[ií]a)\b/i,
  /\bpas(ame|áme)\s+(un|el)\s+(d[ií]a|horario)/i,
];

/**
 * Promete un link que va a mandar despues.
 *
 * Costo un lead el 13-9. Susana, salon de belleza, ya habia dicho cuando podia:
 *
 *   ← Mañana al medio dia
 *   → Los horarios los maneja el sistema y se ven directamente cuando agendás.
 *     Entrá al link que te voy a pasar en un momento y elegí el que te venga bien.
 *
 * No existia ningun link y nunca salio ninguno. La conversacion murio ahi con
 * la clienta esperando.
 *
 * Es una mentira distinta de las de arriba —no dice que agendo, no confirma un
 * momento, no le pide un horario— pero hace el mismo daño: manda al lead a
 * esperar algo que no viene. El bot no tiene forma de mandar "otro mensaje
 * despues" con un link: o el link va en ESTE mensaje, o no hay link.
 */
const PROMETE_LINK = [
  /\b(te|le)\s+(paso|mando|env[ií]o|comparto|dejo)\s+(el|un|los?)\s+(link|enlace)/i,
  /\b(te|le)\s+(voy\s+a\s+|vas?\s+a\s+)?(pasar|mandar|enviar|llegar|compartir)\s+(el|un)\s+(link|enlace)/i,
  /\b(link|enlace)\s+que\s+te\s+(voy\s+a\s+)?(pas|mand|env)/i,
  /\b(te\s+)?(llega|llegar[aá])\s+(el|un)\s+(link|enlace)/i,
];

/** Un link de verdad adentro del mensaje. */
const TIENE_LINK = /https?:\/\/\S+/i;

/**
 * @returns {string|null} que fue lo que prometio, o null si esta bien.
 */
function prometeAgendar(texto) {
  const t = String(texto || '');
  // Prometer el link esta mal solo si no lo manda. "Te paso el link:
  // https://..." es exactamente lo que tiene que hacer.
  if (!TIENE_LINK.test(t) && PROMETE_LINK.some((re) => re.test(t))) {
    return 'prometio un link que no mando';
  }
  if (DICE_QUE_AGENDO.some((re) => re.test(t))) return 'dijo que ya lo agendo';
  // Preguntar es ofrecer. Solo se toma como confirmacion lo que no le deja la
  // decision al lead.
  const pregunta = t.includes('?');
  if (!pregunta && CONFIRMA_SIN_PREGUNTAR.some((re) => re.test(t))) return 'confirmo un momento';
  if (PIDE_HORARIO.some((re) => re.test(t))) return 'le pidio un horario';
  return null;
}

module.exports = { prometeAgendar, DICE_QUE_AGENDO, CONFIRMA_SIN_PREGUNTAR, PIDE_HORARIO, PROMETE_LINK };
