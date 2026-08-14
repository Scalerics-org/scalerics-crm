'use strict';

const { construirSystem, faltantes } = require('./prompt');

const MAX_HISTORIAL = 20;
const MAX_CARACTERES = 900;

/** Campos que el modelo puede escribir, y como se validan antes de guardar. */
const CAMPOS = {
  business_name: { tipo: 'string' },
  rubro: { tipo: 'string' },
  business_type: { tipo: 'opcion' },
  budget: { tipo: 'opcion' },
  team_size: { tipo: 'opcion' },
  instagram_web: { tipo: 'string' },
  needs: { tipo: 'string' },
};

/**
 * Una sola herramienta que devuelve TODO: el mensaje y los datos.
 *
 * Podria pedirsele el texto por content y los datos por tool call, que es lo
 * natural, pero los modelos de OpenAI suelen mandar content vacio cuando llaman
 * una herramienta. Ahi habria que hacer una segunda llamada para conseguir la
 * respuesta —el doble de latencia y de costo en cada turno donde extrae algo—
 * o arriesgarse a que el lead no reciba nada. Metiendo el mensaje adentro de la
 * herramienta y forzandola con tool_choice, siempre viene todo en una llamada y
 * con forma conocida.
 */
const HERRAMIENTA = {
  type: 'function',
  function: {
    name: 'responder',
    description: 'Contesta al lead y guarda lo que hayas averiguado de él.',
    parameters: {
      type: 'object',
      properties: {
        mensaje: {
          type: 'string',
          description: 'Lo que se le manda por WhatsApp. Dos o tres líneas, una sola pregunta.',
        },
        business_name: { type: 'string', description: 'Nombre del negocio, tal como lo dijo' },
        rubro: { type: 'string', description: 'A qué se dedica, en sus palabras (ej: "carnicería de barrio")' },
        business_type: { type: 'integer', description: '1 página web, 2 e-commerce, 3 automatización, 4 app a medida' },
        budget: { type: 'integer', description: '1 menos de USD 500, 2 entre 500 y 3.000, 3 más de 3.000, 4 no lo tiene claro' },
        team_size: { type: 'integer', description: '1 solo él, 2 de 2 a 5, 3 de 6 a 20, 4 más de 20' },
        instagram_web: { type: 'string', description: 'Usuario de Instagram, URL de la web o lo que haya dicho' },
        needs: { type: 'string', description: 'Qué quiere lograr, en sus palabras' },
      },
      required: ['mensaje'],
    },
  },
};

/**
 * Un mensaje que menciona plata. El superprompt prohibe dar precios y el modelo
 * lo tiene en el prompt, pero bajo presion ("dale, tirame un numero") un LLM
 * cede — y una cifra dicha por WhatsApp despues la tiene que sostener alguien.
 * Asi que se verifica la salida en vez de confiar en la instruccion.
 *
 * Se piden las dos cosas, moneda y cifra, para no bloquear "el precio depende
 * del alcance", que es exactamente lo que si tiene que poder decir.
 */
const MONEDA = /\$|u\$s|usd|d[oó]lar|\bpesos?\b|euro/i;
const CIFRA = /\d|\b(mil|cien|doscientos|quinientos)\b/i;

function mencionaPlata(texto) {
  // Sin links ni arrobas: "calendly.com/scalerics" y "@local2000" no son precios.
  const limpio = String(texto)
    .replace(/https?:\/\/\S+/gi, ' ')
    .replace(/\b[\w.-]+\.(com|uy|net|org)\S*/gi, ' ')
    .replace(/@\S+/g, ' ');

  if (MONEDA.test(limpio) && CIFRA.test(limpio)) return true;
  // Una cifra grande y suelta ("arranca en 1500") tampoco pasa.
  return /\b\d[\d.,]{2,}\b/.test(limpio);
}

/** Deja solo lo que el modelo tiene permitido escribir, con el tipo correcto. */
function sanearDatos(crudo) {
  const limpio = {};
  for (const [campo, regla] of Object.entries(CAMPOS)) {
    const v = crudo?.[campo];
    if (v === null || v === undefined || v === '') continue;

    if (regla.tipo === 'opcion') {
      const n = parseInt(v, 10);
      if (n >= 1 && n <= 4) limpio[campo] = n;
      continue;
    }
    const t = String(v).trim();
    if (t) limpio[campo] = t.slice(0, 500);
  }
  return limpio;
}

/** El historial del repo al formato de la API. */
function aMensajes(historial, entrante) {
  const msgs = [];
  for (const m of historial.slice(-MAX_HISTORIAL)) {
    // El repo devuelve `body`; las rutas del CRM lo exponen como `content`.
    const contenido = String(m.body ?? m.content ?? '').trim();
    if (!contenido) continue;
    const rol = m.direction === 'in' ? 'user' : 'assistant';
    // Turnos seguidos del mismo rol se juntan: es una sola cosa que dijo.
    if (msgs.length && msgs[msgs.length - 1].role === rol) {
      msgs[msgs.length - 1].content += `\n${contenido}`;
      continue;
    }
    msgs.push({ role: rol, content: contenido });
  }

  const ultimo = String(entrante || '').trim();
  if (ultimo) {
    if (msgs.length && msgs[msgs.length - 1].role === 'user') {
      msgs[msgs.length - 1].content += `\n${ultimo}`;
    } else {
      msgs.push({ role: 'user', content: ultimo });
    }
  }
  while (msgs.length && msgs[0].role === 'assistant') msgs.shift();
  return msgs;
}

/**
 * Capa conversacional. Devuelve el texto a mandar y los datos que extrajo, o
 * null si no se puede usar — sin clave, si la API falla o si la respuesta no
 * pasa los controles. El que llama cae al embudo de siempre con ese null: el
 * FSM sigue existiendo justamente para eso.
 */
function crearAgente({ openai = null, modelo, textos, logger = null } = {}) {
  return {
    activo: Boolean(openai),

    async responder(lead, entrante, historial = []) {
      if (!openai) return null;

      const conversacion = aMensajes(historial, entrante);
      if (!conversacion.length) return null;

      let respuesta;
      try {
        respuesta = await openai.chat.completions.create({
          model: modelo,
          max_tokens: 500,
          messages: [{ role: 'system', content: construirSystem(lead) }, ...conversacion],
          tools: [HERRAMIENTA],
          // Forzada: sin esto el modelo a veces contesta por content y a veces
          // por la herramienta, y hay que manejar los dos caminos.
          tool_choice: { type: 'function', function: { name: 'responder' } },
        });
      } catch (e) {
        logger?.warn({ leadId: lead.id, err: String(e.message || e) }, 'la IA fallo, se usa el embudo fijo');
        return null;
      }

      const llamada = respuesta?.choices?.[0]?.message?.tool_calls?.[0];
      let argumentos;
      try {
        argumentos = JSON.parse(llamada?.function?.arguments || '{}');
      } catch (e) {
        logger?.warn({ leadId: lead.id }, 'la IA devolvio argumentos que no son JSON');
        return null;
      }

      const texto = String(argumentos.mensaje || '').trim();
      const datos = sanearDatos(argumentos);

      // Que haya guardado datos no sirve de nada si no contesto: el lead esta
      // esperando del otro lado.
      if (!texto) {
        logger?.warn({ leadId: lead.id }, 'la IA no devolvio mensaje');
        return null;
      }

      if (mencionaPlata(texto)) {
        logger?.warn({ leadId: lead.id, texto }, 'la IA menciono un precio, se reemplaza por el texto fijo');
        return { texto: textos.PRECIO, datos, precioBloqueado: true };
      }

      return { texto: texto.slice(0, MAX_CARACTERES), datos, precioBloqueado: false };
    },

    faltantes,
  };
}

module.exports = { crearAgente, mencionaPlata, sanearDatos, aMensajes, HERRAMIENTA };
