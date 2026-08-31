'use strict';

const { construirSystem, faltantes } = require('./prompt');
const { CAJONES } = require('../funnel/nurture');
const { corregir: corregirVoseo } = require('./voseo');

const MAX_HISTORIAL = 20;
const MAX_CARACTERES = 900;

/**
 * Del idioma del lead al del CRM. El modelo dice lo que escucho —"somos 3",
 * "una pagina web"— y el mapeo a los codigos 1-4 que guarda la base lo hace
 * esto, que no se equivoca.
 */
/**
 * El 6 es "todavia no sabe", y no es un caso raro: en el formulario de Calendly
 * fue la tercera respuesta mas elegida, 5 de 32 reservas reales. Uno de cada
 * seis que agenda no sabe que necesita — y agenda igual, porque el formulario
 * se lo permite. El bot no se lo permitia, y eran justo los que mas necesitan
 * el diagnostico, que es literalmente para lo que sirve la reunion.
 */
const TIPO_PROYECTO = {
  web: 1, ecommerce: 2, automatizacion: 3, sistema: 4, agente_ia: 5, no_sabe: 6,
};

/** Lo que, si el modelo lo ve, significa que no es un cliente posible. */
const NO_CLIENTE = ['trabajo', 'vender_algo', 'numero_equivocado', 'algo_que_no_hacemos'];
const PRESUPUESTO = { menos_500: 1, entre_500_y_3000: 2, mas_3000: 3, no_sabe: 4 };

/** Cuanta gente trabaja -> el tramo que usa el CRM. */
function tramoDeEquipo(personas) {
  const n = parseInt(personas, 10);
  if (!Number.isInteger(n) || n < 1) return null;
  if (n === 1) return 1;
  if (n <= 5) return 2;
  if (n <= 20) return 3;
  return 4;
}

/** Campos que el modelo puede escribir, y como se validan antes de guardar. */
const CAMPOS = {
  business_name: { tipo: 'string' },
  rubro: { tipo: 'string' },
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
        /**
         * Va PRIMERO y es obligatorio a proposito.
         *
         * El modelo llamaba la herramienta pero llenaba solo `mensaje`: el lead
         * decia "tengo una panaderia, se llama PanesAhora" y guardaba el nombre
         * ignorando el rubro, o daba el tipo de proyecto en el primer mensaje y
         * se lo preguntaban tres veces. Obligarlo a enumerar antes lo que
         * acaban de decirle lo fuerza a mirar el mensaje como fuente de datos
         * y no solo como algo que hay que contestar.
         *
         * No se le manda al lead: es para que el modelo piense antes de
         * escribir.
         */
        lo_que_acaba_de_decir: {
          type: 'string',
          description: 'Enumerá TODOS los datos que aporta el último mensaje del lead, aunque no se los hayas preguntado. Ejemplo: "dice que es una panadería (rubro) y que se llama PanesAhora (nombre)". Si no aporta ninguno, poné "nada".',
        },
        mensaje: {
          type: 'string',
          description: 'Lo que se le manda por WhatsApp. Dos o tres líneas, una sola pregunta.',
        },
        business_name: { type: 'string', description: 'Nombre del negocio, tal como lo dijo' },
        rubro: { type: 'string', description: 'A qué se dedica, en sus palabras (ej: "carnicería de barrio")' },
        /**
         * Estos tres se piden en el idioma del lead, no en el del CRM.
         *
         * Antes se le pedia el numero de opcion directamente y confundia la
         * cantidad con la escala: a "somos 3" le ponia team_size 3, que
         * significa "de 6 a 20 personas". Se le puede explicar en la
         * descripcion —se hizo, con ejemplos— y lo sigue errando, porque le
         * estamos pidiendo una conversion, no una observacion.
         *
         * Ahora informa lo que escucho y el mapeo lo hace el codigo, que no se
         * equivoca nunca.
         */
        business_type: {
          type: 'string',
          enum: ['web', 'ecommerce', 'sistema', 'automatizacion', 'agente_ia', 'no_sabe'],
          description: 'Qué necesita: web = página web, ecommerce = tienda online, sistema = sistema a medida, automatizacion = automatizar procesos internos, agente_ia = un agente de IA que atienda o venda. no_sabe = le ofreciste las opciones y no eligió ninguna: dijo "no sé", "quiero ver", "vos decime", "de todo un poco" o cambió de tema. Marcá no_sabe en vez de dejarlo vacío: no saber es una respuesta válida y para eso está la reunión.',
        },
        budget: {
          type: 'string',
          enum: ['menos_500', 'entre_500_y_3000', 'mas_3000', 'no_sabe'],
          description: 'Presupuesto en dólares. "unos 1000" es entre_500_y_3000. Si dijo que no tiene idea, no_sabe.',
        },
        team_size_personas: {
          type: 'integer',
          description: 'CUÁNTAS PERSONAS trabajan en el negocio, el número real que dijo. "somos 3" es 3, "estoy solo" es 1, "unos 30" es 30.',
        },
        instagram_web: { type: 'string', description: 'Usuario de Instagram, URL de la web o lo que haya dicho' },
        needs: { type: 'string', description: 'Qué quiere lograr, en sus palabras' },
        /**
         * Se le pide el cajon, no la fecha: la fecha la calcula el codigo.
         *
         * "no" tiene que estar en el enum y ser lo normal. Sin esa opcion, un
         * campo que solo se llena a veces tienta al modelo a llenarlo siempre,
         * y cualquier "lo pienso y te digo" terminaria mandando al lead a una
         * pausa de dos semanas.
         */
        aplaza: {
          type: 'string',
          enum: ['no', ...CAJONES],
          description: 'CUÁNDO dijo que lo va a ver, si dijo que ahora no. Poné el cajón cada vez que corra la fecha para adelante, aunque suene interesado: "el mes que viene lo veo", "más adelante", "cuando pase la temporada", "estoy viendo presupuestos", "ahora no puedo" son todos aplazos. unos_dias = esta semana o la que viene. unas_semanas = un par de semanas. un_mes = el mes que viene. varios_meses = a fin de año, después del verano. sin_fecha = dijo que ahora no pero no dijo cuándo. Solo poné "no" si NO corrió nada para adelante: estar ocupado hoy o tardar en contestar no es aplazar.',
        },
        aplaza_frase: {
          type: 'string',
          description: 'Las palabras con las que lo dijo, tal cual. Solo si aplaza no es "no".',
        },
        /**
         * Clasifica siempre, decida o no. Con DESCALIFICACION_AUTOMATICA
         * apagado esto solo se anota, y sirve para mirar contra las
         * conversaciones reales si el modelo acierta antes de darle la
         * decision.
         */
        que_quiere: {
          type: 'string',
          enum: ['un_servicio', 'trabajo', 'vender_algo', 'numero_equivocado', 'algo_que_no_hacemos'],
          description: 'Casi siempre es "un_servicio". trabajo = busca empleo o manda un CV. vender_algo = te esta ofreciendo algo a vos. numero_equivocado = no queria escribirle a esta empresa. algo_que_no_hacemos = pide algo que no es software ni web (arreglar una computadora, diseñar un logo, manejar redes). Trabajar EN un rubro no es buscar trabajo: "tengo una panaderia" es un_servicio.',
        },
      },
      required: ['lo_que_acaba_de_decir', 'mensaje'],
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
  for (const campo of Object.keys(CAMPOS)) {
    const v = crudo?.[campo];
    if (v === null || v === undefined || v === '') continue;
    const t = String(v).trim();
    if (t) limpio[campo] = t.slice(0, 500);
  }

  const tipo = TIPO_PROYECTO[crudo?.business_type];
  if (tipo) limpio.business_type = tipo;

  const presupuesto = PRESUPUESTO[crudo?.budget];
  if (presupuesto) limpio.budget = presupuesto;

  const equipo = tramoDeEquipo(crudo?.team_size_personas);
  if (equipo) limpio.team_size = equipo;

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
function crearAgente({ openai = null, modelo, textos, calendly = '', logger = null } = {}) {
  return {
    activo: Boolean(openai),

    async responder(lead, entrante, historial = [], fase = null) {
      if (!openai) return null;

      const conversacion = aMensajes(historial, entrante);
      if (!conversacion.length) return null;

      let respuesta;
      try {
        respuesta = await openai.chat.completions.create({
          model: modelo,
          max_tokens: 500,
          messages: [{ role: 'system', content: construirSystem(lead, fase, calendly) }, ...conversacion],
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

      const crudo = String(argumentos.mensaje || '').trim();
      // El prompt prohibe el tuteo con todas las letras y el modelo se va igual.
      // Es una conversion mecanica: la hace el codigo, que no se equivoca.
      const { texto, corregidos } = corregirVoseo(crudo);
      if (corregidos.length) {
        logger?.info({ leadId: lead.id, corregidos }, 'se le corrigio el tuteo al modelo');
      }
      const datos = sanearDatos(argumentos);
      // Va aparte de los datos: no es un dato del negocio sino una decision
      // sobre la conversacion, y la toma el embudo.
      const aplaza = CAJONES.includes(argumentos.aplaza) ? argumentos.aplaza : null;
      const aplazaFrase = String(argumentos.aplaza_frase || '').trim().slice(0, 200);
      const queQuiere = NO_CLIENTE.includes(argumentos.que_quiere) ? argumentos.que_quiere : null;

      // Que haya guardado datos no sirve de nada si no contesto: el lead esta
      // esperando del otro lado.
      if (!texto) {
        logger?.warn({ leadId: lead.id }, 'la IA no devolvio mensaje');
        return null;
      }

      if (mencionaPlata(texto)) {
        logger?.warn({ leadId: lead.id, texto }, 'la IA menciono un precio, se reemplaza por el texto fijo');
        return { texto: textos.PRECIO, datos, aplaza, aplazaFrase, queQuiere, precioBloqueado: true };
      }

      return {
        texto: texto.slice(0, MAX_CARACTERES),
        datos, aplaza, aplazaFrase, queQuiere, precioBloqueado: false,
      };
    },

    /**
     * Cual de una lista cerrada eligio el lead.
     *
     * El modelo interpreta —entiende "las 13", "la primera", "a la una y
     * media"— pero no puede devolver nada fuera de la lista: las opciones van
     * como enum en la herramienta. Sin eso podria confirmar un horario que
     * nunca se ofrecio, y el lead se presentaria a una reunion que no existe.
     *
     * @returns {Promise<string|null>} la opcion elegida, o null si no se entiende.
     */
    async elegirDeLista({ texto, opciones, etiquetas, instruccion }) {
      if (!openai || !opciones.length) return null;

      const lista = opciones.map((o, i) => `${o} = ${etiquetas[i]}`).join('\n');

      try {
        const r = await openai.chat.completions.create({
          model: modelo,
          max_tokens: 120,
          messages: [
            { role: 'system', content: `${instruccion}

Opciones:
${lista}` },
            { role: 'user', content: String(texto || '') },
          ],
          tools: [{
            type: 'function',
            function: {
              name: 'elegir',
              parameters: {
                type: 'object',
                properties: {
                  opcion: { type: 'string', enum: [...opciones, 'ninguno'] },
                },
                required: ['opcion'],
              },
            },
          }],
          tool_choice: { type: 'function', function: { name: 'elegir' } },
        });

        const args = JSON.parse(r.choices?.[0]?.message?.tool_calls?.[0]?.function?.arguments || '{}');
        return opciones.includes(args.opcion) ? args.opcion : null;
      } catch (e) {
        logger?.warn({ err: String(e.message || e) }, 'no se pudo interpretar la eleccion');
        return null;
      }
    },

    faltantes,
  };
}

module.exports = { crearAgente, sanearDatos, aMensajes, tramoDeEquipo, HERRAMIENTA, NO_CLIENTE };
