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
 *
 * Los dos pasan por src/ia/modelo.js, la misma puerta que usa el bot. Es lo que
 * hace que la nota del eval sea la nota del bot: si aca se hablara con el SDK
 * por separado, el arnes podria seguir en verde con el bot ya roto.
 */

const { crearAgente } = require('../src/ia/agente');
const { crearTextos } = require('../src/templates/funnel');
const { crearModelo, clienteAnthropic, MODELO_POR_DEFECTO } = require('../src/ia/modelo');

function crearModeloAnthropic({ modelo, modeloLead, calLink, apiKey } = {}) {
  const cliente = clienteAnthropic(apiKey || process.env.ANTHROPIC_API_KEY);
  if (!cliente) throw new Error('Falta ANTHROPIC_API_KEY');

  const nombreModelo = modelo || MODELO_POR_DEFECTO;
  const ia = crearModelo({ cliente, modelo: nombreModelo });
  // La persona simulada puede correr con otro modelo: sirve para que el lead
  // no sea el mismo modelo evaluandose a si mismo.
  const iaLead = modeloLead ? crearModelo({ cliente, modelo: modeloLead }) : ia;

  const agente = crearAgente({
    modelo: ia,
    textos: crearTextos(),
    calendly: calLink,
    logger: null,
  });

  return {
    nombre: `anthropic:${nombreModelo}`,

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
      const r = await iaLead.pedir({ system: persona, mensajes, maxTokens: 200 });
      return String(r?.texto || '').trim();
    },
  };
}

module.exports = { crearModeloAnthropic };
