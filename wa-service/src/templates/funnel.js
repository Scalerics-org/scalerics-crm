'use strict';

/**
 * Lo unico que le llega a un lead sin haberlo escrito el modelo.
 *
 * Todo el resto —bienvenida, preguntas, oferta, follow-up, recordatorios— lo
 * redacta la IA en el momento; los objetivos de cada mensaje estan en
 * ia/prompt.js. Aca quedan tres casos donde depender del modelo seria peor:
 *
 * 1. La baja. Alguien pidio que no le escriban mas. Eso tiene que salir si o
 *    si, aunque la API este caida, y ademas no se le puede dar al modelo la
 *    oportunidad de intentar retenerlo.
 * 2. El reemplazo cuando el guard de precios atrapa una cifra. No se le puede
 *    pedir al modelo que escriba el mensaje que corrige su propia falla.
 * 3. El aviso de que sigue una persona, cuando la IA no esta disponible. Es la
 *    red de ultimo momento: sin esto, una caida de la API deja al lead mudo.
 */
function crearTextos({ horarioAtencion = 'Lun a sáb, 9 a 19hs' } = {}) {
  return {
    /**
     * El saludo. Es fijo por decision del negocio: dicho siempre igual, avisa
     * de entrada que vienen preguntas, y saber eso de antemano baja el abandono
     * a mitad del cuestionario.
     *
     * Efecto lateral que conviene: es el unico mensaje que sale antes de que el
     * lead diga nada, asi que ser fijo lo hace inmune a que la API este caida.
     *
     * Sin link a proposito. Un link en el primer mensaje a alguien que nunca te
     * escribio es de las seniales de spam mas fuertes que hay.
     */
    BIENVENIDA: '¡Buenas! Soy el agente comercial de Scalerics.\n\nLa idea es hacerte unas preguntas introductorias de tu proyecto, para saber cómo podemos ayudarte.',

    /** Del superprompt, textual. El bot NUNCA da un numero ni un rango. */
    PRECIO: 'El precio depende del alcance del proyecto, por eso el primer paso es una charla rápida para entenderlo — así te armamos un presupuesto real, no una cifra al aire.\n\n¿Te sirve que coordinemos 15 minutos esta semana?',

    OPT_OUT: 'Entendido, no te escribo más 👍\n\nSi en algún momento querés retomar, escribime *MENÚ*.\n\n¡Hasta pronto!',

    /**
     * La IA no contesto y hay alguien esperando del otro lado. No se disimula
     * con un texto armado: se lo pasa a una persona, que es lo unico honesto
     * que se puede hacer sin el modelo.
     */
    SIN_IA: `Dame un momento que te paso con alguien del equipo 👤\n\n_Horario de atención: ${horarioAtencion} (GMT-3)_`,

    /**
     * La oferta sin una sola suposicion, para cuando el modelo insiste en
     * atribuirle cosas al lead.
     *
     * Con JEV_MODO=decide, si Jev dice que el mensaje afirma algo que el lead
     * nunca dijo, se le pide al modelo que lo reescriba. Si el segundo intento
     * tambien lo hace, sale esto: no dice nada del lead, y por eso no puede
     * inventar nada. Es peor mensaje y mejor que mentirle — el 11 y el 12/9 la
     * oferta arranco con "Entendí que necesitás…" y algo que nadie habia dicho.
     */
    ofertaNeutra(situacion, { extra = '', calendly = '' } = {}) {
      const base = 'Te propongo una videollamada de 30 minutos: vemos en detalle qué necesitás '
        + 'y el equipo te arma un prototipo de lo que estés buscando.';
      if (situacion === 'oferta_con_horarios' && extra) {
        return `${base}\n\nTengo libre ${extra}\n\n¿Qué día y hora te viene bien?`;
      }
      if (calendly) return `${base}\n\nElegí el horario que te quede bien acá: ${calendly}`;
      return `${base}\n\n¿Te sirve? Decime qué día y hora te viene bien.`;
    },
  };
}

module.exports = { crearTextos };
