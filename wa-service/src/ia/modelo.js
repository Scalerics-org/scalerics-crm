'use strict';

/**
 * La unica puerta al modelo de lenguaje.
 *
 * Todo lo que el bot le pide a una IA para conversar pasa por aca: la
 * conversacion, los mensajes sueltos, elegir de una lista, el puntaje. Los
 * audios NO — esos siguen yendo a Whisper, que es de OpenAI, porque Anthropic
 * no transcribe.
 *
 * Existe para que cambiar de proveedor sea editar un archivo y no cuatro
 * llamadas repartidas. La primera vez costo mas de lo que deberia: cada lugar
 * armaba su pedido con la forma de OpenAI, y los tests verificaban esa forma.
 *
 * La interfaz es una sola funcion a proposito. Los cuatro usos son el mismo
 * pedido con y sin herramienta:
 *
 *   sin herramienta  -> devuelve texto      (los mensajes que escribe el redactor)
 *   con herramienta  -> devuelve argumentos (la conversacion, elegir, el puntaje)
 *
 * La herramienta va siempre forzada. Sin forzarla, el modelo a veces contesta
 * en texto y a veces llama la herramienta, y habria que manejar los dos caminos
 * en cada lugar.
 */

const MODELO_POR_DEFECTO = 'claude-haiku-4-5-20251001';

/**
 * Cuanto se espera una respuesta antes de darla por perdida.
 *
 * El SDK viene con 10 minutos y 2 reintentos: hasta media hora colgado en un
 * solo mensaje. Para un chat de WhatsApp eso es peor que fallar rapido — el
 * lead espera una eternidad, sus mensajes siguientes quedan encolados detras, y
 * si la respuesta llega a los diez minutos ya no sirve para nada.
 *
 * Con estos valores el peor caso es medio minuto, y despues de eso el embudo
 * hace lo que sabe hacer: derivar a una persona. Un lead atendido por alguien
 * del equipo a los treinta segundos es mucho mejor que uno esperando solo.
 */
const TIMEOUT_MS = 25_000;
const REINTENTOS = 1;

/**
 * Recorta un texto en la ultima oracion completa.
 *
 * Para cuando el modelo llego al tope de tokens: el ultimo renglon queda
 * cortado a la mitad y al lead le llega una frase sin terminar. Es mejor
 * mandar menos y que se entienda.
 *
 * Si no hay ningun corte razonable —una respuesta corta sin puntuacion— se
 * devuelve tal cual: cortar en la nada dejaria algo peor que el original.
 *
 * El minimo son veinte caracteres y no mas, porque los mensajes de este bot son
 * preguntas de un renglon: "¿Cómo se llama el negocio?" son veinticinco y es un
 * mensaje entero y perfecto. Un umbral pensado para respuestas largas —listas
 * de productos con precios— lo dejaria pasar sin recortar.
 */
const MIN_ORACION = 20;

function recortarEnOracion(texto) {
  const t = String(texto || '');
  const corte = Math.max(t.lastIndexOf('. '), t.lastIndexOf('.\n'), t.lastIndexOf('?'), t.lastIndexOf('!'));
  if (corte < MIN_ORACION) return t;
  return t.slice(0, corte + 1).trim();
}

/**
 * @param {object} deps.cliente  ya construido, o null para quedar inactivo.
 *   En los tests entra uno de mentira con la misma forma.
 */
function crearModelo({ cliente = null, modelo = MODELO_POR_DEFECTO, logger = null } = {}) {
  return {
    activo: Boolean(cliente),
    modelo,

    /**
     * @param {string} opciones.system      las instrucciones
     * @param {Array} opciones.mensajes     [{ role: 'user'|'assistant', content }]
     * @param {object} [opciones.herramienta] { nombre, descripcion, parametros }
     * @param {number} [opciones.maxTokens]
     * @returns {Promise<{texto: string|null, argumentos: object|null}|null>}
     *   null si no se pudo hablar con el modelo. Quien llama decide que hacer
     *   con eso — el embudo, por ejemplo, deriva a una persona.
     */
    async pedir({ system, mensajes, herramienta = null, maxTokens = 500 }) {
      if (!cliente) return null;

      const pedido = {
        model: modelo,
        // Anthropic lo exige, no tiene default.
        max_tokens: maxTokens,
        system,
        messages: mensajes,
      };

      if (herramienta) {
        pedido.tools = [{
          name: herramienta.nombre,
          description: herramienta.descripcion,
          input_schema: herramienta.parametros,
        }];
        // Sin esto puede llamar la herramienta mas de una vez en la misma
        // respuesta. Se lee la primera igual, pero pagar dos veces por un
        // resultado que se descarta no tiene sentido.
        pedido.tool_choice = { type: 'tool', name: herramienta.nombre, disable_parallel_tool_use: true };
      }

      let r;
      try {
        r = await cliente.messages.create(pedido);
      } catch (e) {
        logger?.warn({ err: String(e.message || e) }, 'fallo la llamada al modelo');
        return null;
      }

      // La respuesta viene como una lista de bloques: los de texto y los de uso
      // de herramienta llegan mezclados y en cualquier orden.
      const bloques = Array.isArray(r?.content) ? r.content : [];
      const crudo = bloques
        .filter((b) => b.type === 'text')
        .map((b) => b.text)
        .join('\n')
        .trim();
      const usoHerramienta = bloques.find((b) => b.type === 'tool_use');

      // Llego al tope de tokens: lo que vino esta cortado a la mitad. El texto
      // suelto se recorta aca, que es el unico lugar que sabe que paso. Lo que
      // viene por la herramienta no —esta capa no conoce los nombres de los
      // campos de nadie— asi que se avisa y lo resuelve el que llamo.
      const truncado = r?.stop_reason === 'max_tokens';
      if (truncado) logger?.warn({ modelo }, 'la respuesta llego al tope de tokens');
      const texto = truncado ? recortarEnOracion(crudo) : crudo;

      return {
        texto: texto || null,
        argumentos: usoHerramienta ? usoHerramienta.input : null,
        truncado,
      };
    },
  };
}

/** El cliente de verdad. Se arma aca para que nadie mas importe el SDK. */
function clienteAnthropic(apiKey) {
  if (!apiKey) return null;
  const Anthropic = require('@anthropic-ai/sdk');
  const Constructor = Anthropic.default || Anthropic;
  return new Constructor({ apiKey, timeout: TIMEOUT_MS, maxRetries: REINTENTOS });
}

/**
 * Avisa al equipo si el bot arranco sin una clave que esperaba tener.
 *
 * Faltar una clave no rompe nada, y ese es justamente el problema: el bot
 * levanta, contesta, y deriva a una persona cada lead que escribe. Desde
 * afuera se ve igual que un bot andando. Con dos proveedores la chance de que
 * pase se duplico, asi que el arranque lo dice en voz alta —el mismo tratamiento
 * que el link de Calendly roto, que tambien fallaba en silencio.
 *
 * @param {object} ia lo que devuelve construir(): { conversacion, transcripcion }
 */
function avisarSiFaltaClave({ cfg, cola, ia, logger = null }) {
  const faltantes = [];
  if (cfg.IA_CONVERSACION && !ia.conversacion) {
    faltantes.push('ANTHROPIC_API_KEY — el bot no conversa: cada lead que escriba se deriva a una persona');
  }
  if (cfg.IA_TRANSCRIPCION && !ia.transcripcion) {
    faltantes.push('OPENAI_API_KEY — no se transcriben las notas de voz: se le pide al lead que escriba');
  }
  if (!faltantes.length) return faltantes;

  logger?.error({ faltantes }, 'arranco sin claves de IA');
  for (const am of cfg.amPhones) {
    cola.encolar({
      to: am,
      texto: `⚠️ El bot arrancó sin claves de IA:\n\n${faltantes.map((f) => `• ${f}`).join('\n')}\n\nHay que cargarlas con "fly secrets set" y redeployar.`,
      kind: 'am_notice',
    });
  }
  return faltantes;
}

module.exports = {
  crearModelo,
  clienteAnthropic,
  avisarSiFaltaClave,
  recortarEnOracion,
  MODELO_POR_DEFECTO,
};
