'use strict';

/**
 * Adaptador de modelo para el arnes.
 *
 * A diferencia de la version original, `.bot()` NO replica al agente: llama al
 * de verdad, el mismo que corre en produccion. Asi se evalua lo que realmente
 * pasa —incluido el guard de precios, el saneo de datos y el mapeo de tramos—
 * y no una imitacion que puede divergir sin que nadie lo note.
 *
 * `.lead()` si usa el modelo suelto: ahi el que actua es la persona del otro
 * lado, no nuestro sistema.
 */

const { crearAgente } = require('../src/ia/agente');
const { crearTextos } = require('../src/templates/funnel');

function crearModeloOpenAI({ modelo, modeloLead, calLink, apiKey } = {}) {
  const OpenAI = require('openai');
  const openai = new OpenAI({ apiKey: apiKey || process.env.OPENAI_API_KEY });

  const agente = crearAgente({
    openai,
    modelo,
    textos: crearTextos(),
    calendly: calLink,
    logger: null,
  });

  return {
    nombre: `openai:${modelo}`,

    /**
     * Un turno del bot. El arnes le pasa el historial completo; el agente lo
     * necesita en su formato, con `body` y `direction`.
     */
    async bot(_system, historial, _herramienta, lead) {
      const previos = historial.slice(0, -1).map((m) => ({
        direction: m.role === 'user' ? 'in' : 'out',
        body: m.content,
      }));
      const ultimo = historial[historial.length - 1];

      const r = await agente.responder(lead, ultimo ? ultimo.content : '', previos, lead.fsm_state || null);
      if (!r) return { texto: '', guardados: [], sinRespuesta: true };

      return {
        texto: r.texto,
        guardados: Object.keys(r.datos).length ? [r.datos] : [],
        precioBloqueado: r.precioBloqueado,
      };
    },

    /** El lead simulado. Este si es un modelo suelto con una persona encima. */
    async lead(persona, historial) {
      const mensajes = historial.map((m) => ({
        role: m.role === 'user' ? 'assistant' : 'user',
        content: m.content,
      }));
      if (!mensajes.length) {
        mensajes.push({ role: 'user', content: '(escribile el primer mensaje a la agencia)' });
      }
      const r = await openai.chat.completions.create({
        model: modeloLead || modelo,
        max_tokens: 200,
        messages: [{ role: 'system', content: persona }, ...mensajes],
      });
      return String(r.choices?.[0]?.message?.content || '').trim();
    },
  };
}

module.exports = { crearModeloOpenAI };
