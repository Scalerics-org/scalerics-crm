'use strict';

const { corregir: corregirVoseo } = require('./voseo');
const { quitar: quitarJerga } = require('./jerga');

const { construirRedaccion, situaciones } = require('./prompt');
const { mencionaPlata } = require('./precio');
const { cambiaLaNecesidad } = require('./necesidad');

const MAX_CARACTERES = 900;

/**
 * Escribe los mensajes que el codigo decide mandar: la bienvenida, el
 * follow-up, los recordatorios, la oferta de reunion.
 *
 * La division con agente.js es quien empieza. El agente contesta un mensaje del
 * lead y ademas extrae datos; el redactor escribe cuando el que arranca es el
 * servicio y no hay nada que extraer. Los dos comparten el estilo, las
 * prohibiciones y el control de precios.
 *
 * Devuelve null si no se puede escribir. El que llama decide que hacer con eso
 * —reprogramar el job, derivar a una persona— pero nunca manda un texto fijo
 * como si nada: si el modelo no esta, el lead se merece una persona.
 */
function crearRedactor({ modelo = null, calendly = '', logger = null } = {}) {
  return {
    activo: Boolean(modelo?.activo),

    /** Las situaciones que sabe escribir. Util para no pedirle uña inexistente. */
    conoce: (situacion) => Boolean(situaciones(calendly)[situacion]),

    /**
     * @param {object} lead
     * @param {string} situacion clave de situaciones()
     * @param {string} extra contexto puntual (la fecha de la reunion, el link)
     * @returns {Promise<string|null>}
     */
    async escribir(lead, situacion, extra = '') {
      if (!modelo?.activo) return null;

      const prompt = construirRedaccion(lead, situacion, calendly, extra);
      if (!prompt) {
        logger?.error({ situacion }, 'no existe esa situacion, no se escribe nada');
        return null;
      }

      const pedir = async (extraInstruccion = '') => {
        const r = await modelo.pedir({
          system: 'Escribís mensajes de WhatsApp para una agencia uruguaya. Devolvés solo el mensaje, sin comillas ni explicaciones.',
          mensajes: [{ role: 'user', content: prompt + extraInstruccion }],
          maxTokens: 400,
        });
        if (!r) return null;
        return String(r.texto || '').trim()
          // A veces devuelve el mensaje entre comillas, como si lo citara.
          .replace(/^["“”']+|["“”']+$/g, '')
          .trim();
      };

      let crudo = await pedir();

      if (crudo === null) {
        logger?.warn({ leadId: lead.id, situacion }, 'no se pudo redactar el mensaje');
        return null;
      }

      /**
       * No le cambies al lead lo que pidio.
       *
       * El 3-9 pidio "una pagina" y el mensaje le vendio un e-commerce. El dato
       * estaba bien guardado; lo que fallo es el mensaje, que es donde el
       * modelo mejora la idea del lead por su cuenta.
       *
       * Esto no se arregla tachando una palabra —hay que escribirlo de nuevo—
       * asi que se le pide otra vez, ahora diciendoselo. Si insiste, sale igual:
       * un mensaje con la solucion equivocada es malo, pero no contestar es
       * peor, y del otro lado hay alguien esperando.
       */
      if (cambiaLaNecesidad(lead.needs, crudo)) {
        logger?.warn({ leadId: lead.id, situacion, needs: lead.needs, crudo },
          'el mensaje le cambiaba lo que pidio: se reescribe');
        const salto = String.fromCharCode(10);
        const otra = await pedir(
          salto + salto + `OJO: el lead pidió ${lead.needs}. Hablale de eso y no de otra cosa.`,
        );
        if (otra) crudo = otra;
      }

      // El tuteo que se le escapa al modelo lo corrige el codigo, igual que en
      // la conversacion. Un recordatorio con un "tienes" delata lo mismo.
      const sinJerga = quitarJerga(crudo);
      if (sinJerga.sacados.length) {
        logger?.info({ leadId: lead.id, situacion, sacados: sinJerga.sacados }, 'se le saco la jerga al modelo');
      }
      const { texto, corregidos } = corregirVoseo(sinJerga.texto);
      if (corregidos.length) {
        logger?.info({ leadId: lead.id, situacion, corregidos }, 'se le corrigio el tuteo al modelo');
      }

      if (!texto) {
        logger?.warn({ leadId: lead.id, situacion }, 'la redaccion vino vacia');
        return null;
      }

      // El mismo control que la conversacion. Aca no hay reemplazo posible: un
      // follow-up que menciona plata no se manda, y se reintenta despues.
      if (mencionaPlata(texto)) {
        logger?.warn({ leadId: lead.id, situacion, texto }, 'la redaccion menciono un precio, se descarta');
        return null;
      }

      return texto.slice(0, MAX_CARACTERES);
    },
  };
}

module.exports = { crearRedactor };
