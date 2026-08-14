'use strict';

const { construirRedaccion, situaciones } = require('./prompt');
const { mencionaPlata } = require('./precio');

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
function crearRedactor({ openai = null, modelo, calendly = '', logger = null } = {}) {
  return {
    activo: Boolean(openai),

    /** Las situaciones que sabe escribir. Util para no pedirle uña inexistente. */
    conoce: (situacion) => Boolean(situaciones(calendly)[situacion]),

    /**
     * @param {object} lead
     * @param {string} situacion clave de situaciones()
     * @param {string} extra contexto puntual (la fecha de la reunion, el link)
     * @returns {Promise<string|null>}
     */
    async escribir(lead, situacion, extra = '') {
      if (!openai) return null;

      const prompt = construirRedaccion(lead, situacion, calendly, extra);
      if (!prompt) {
        logger?.error({ situacion }, 'no existe esa situacion, no se escribe nada');
        return null;
      }

      let r;
      try {
        r = await openai.chat.completions.create({
          model: modelo,
          max_tokens: 400,
          messages: [{ role: 'user', content: prompt }],
        });
      } catch (e) {
        logger?.warn(
          { leadId: lead.id, situacion, err: String(e.message || e) },
          'no se pudo redactar el mensaje'
        );
        return null;
      }

      const texto = String(r?.choices?.[0]?.message?.content || '').trim()
        // A veces devuelve el mensaje entre comillas, como si lo citara.
        .replace(/^["“”']+|["“”']+$/g, '')
        .trim();

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
