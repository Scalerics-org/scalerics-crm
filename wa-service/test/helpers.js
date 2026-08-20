'use strict';

const { cargar } = require('../src/config');
const { construir } = require('../src/app');

/**
 * Cliente de OpenAI falso y deterministico.
 *
 * Los tests verifican CABLEADO —que situacion se dispara, en que estado queda
 * el lead, que datos se guardan— y no redaccion. Por eso devuelve marcadores
 * como "[bienvenida]" en vez de texto realista: si un test dependiera de las
 * palabras exactas, cambiar una coma del prompt lo romperia.
 *
 * La calidad de la redaccion se mide aparte, en evals/, contra la API de verdad.
 */
function stubOpenAI({ respuestas = {}, datos = {}, falla = null } = {}) {
  const llamadas = [];
  return {
    llamadas,
    chat: {
      completions: {
        create: async (args) => {
          llamadas.push(args);
          if (falla) throw new Error(falla);

          // Con tools es la conversacion; sin tools, un mensaje suelto.
          if (args.tools) {
            const mensaje = respuestas.conversacion || '[conversacion]';
            return {
              choices: [{
                message: {
                  content: null,
                  tool_calls: [{
                    type: 'function',
                    function: { name: 'responder', arguments: JSON.stringify({ mensaje, ...datos }) },
                  }],
                },
              }],
            };
          }

          const prompt = args.messages[0].content;
          const m = prompt.match(/situación: (\w+)/);
          const situacion = m ? m[1] : 'desconocida';
          return {
            choices: [{ message: { content: respuestas[situacion] || `[${situacion}]` } }],
          };
        },
      },
    },
    audio: {
      transcriptions: {
        create: async () => {
          if (falla) throw new Error(falla);
          return { text: respuestas.transcripcion || 'audio transcripto' };
        },
      },
    },
  };
}

const CLAVE = 'clave-de-test-larguita-1234';
const ADMIN = 'admin-token-de-prueba';

/**
 * Config de test construida con el cargar() real, no a mano: asi los tests
 * ejercitan el parseo de verdad y una clave nueva no rompe toda la suite.
 *
 * El horario comercial va abierto 24/7 a proposito. Con el default (09:00-19:00
 * lun-sab) la suite pasaria o fallaria segun la hora a la que se corra.
 */
function cfgTest(extra = {}) {
  return cargar({
    WA_API_KEY: CLAVE,
    ADMIN_TOKEN: ADMIN,
    WA_PROVIDER: 'mock',
    DB_PATH: ':memory:',
    LOG_LEVEL: 'silent',
    NODE_ENV: 'test',
    AM_PHONES: '59899000111',
    CALENDLY_LINK: 'https://calendly.com/scalerics/consultoriagratuita',
    // Sin esperas: los delays ya tienen sus propios tests.
    DELAY_AM_MIN_MS: '0', DELAY_AM_MAX_MS: '0',
    DELAY_WELCOME_MIN_MS: '0', DELAY_WELCOME_MAX_MS: '0',
    DELAY_BETWEEN_MIN_MS: '0', DELAY_BETWEEN_MAX_MS: '0',
    TYPING_ENABLED: 'false',
    FOLLOWUP_JITTER_MINUTES: '0',
    BUSINESS_HOURS: '00:00-23:59',
    BUSINESS_DAYS: 'sun-sat',
    // Credenciales de agenda de mentira: el fetch se inyecta aparte.
    GCAL_CLIENT_ID: 'x', GCAL_CLIENT_SECRET: 'y', GCAL_REFRESH_TOKEN: 'z',
    GCAL_CALENDAR_ID: 'agenda@scalerics',
    // Sin espera: agrupar entrantes tiene sus propios tests.
    AGRUPAR_ENTRANTES_MS: '0',
    ...extra,
  });
}

/**
 * Servicio montado y conectado.
 * @param {Date} [reloj] congela el tiempo, para probar horario comercial.
 */
async function montar(extra, reloj) {
  // `openai` no es una clave de config: es el cliente falso que usan los tests
  // de la capa de IA. Se separa antes de armar la config.
  // Por defecto va el stub: sin IA el bot deriva todo a una persona, que es
  // el camino degradado y no el que hay que probar.
  const { openai = stubOpenAI(), sinIA = false, _google = null, ...cfgExtra } = extra || {};
  const s = construir(cfgTest(cfgExtra), {
    logger: null,
    openai: sinIA ? null : openai,
    google: _google,
    ahora: reloj ? () => reloj : undefined,
  });
  await s.proveedor.conectar();
  return s;
}

const LEAD = {
  external_id: 'l1',
  nombre: 'Martín Pereyra',
  rubro: 'Inmobiliaria',
  telefono: '099123456',
  necesidad: 'Quiero automatizar el seguimiento de consultas de alquiler',
  origen: 'form',
};

/** Servicio con un lead ya dado de alta y la cola limpia. */
async function conLead(extra, datos = LEAD) {
  const s = await montar(extra);
  await s.servicioLeads.alta(datos);
  await s.cola.vacia();
  s.proveedor.limpiar();
  return s;
}

module.exports = { CLAVE, ADMIN, LEAD, cfgTest, montar, conLead, stubOpenAI };
