'use strict';

/**
 * Mide a Jev contra la API de verdad, con los casos que motivaron el modo
 * sombra. Es la misma pregunta que hace ia/sombra.js, sobre conversaciones
 * reales ANONIMIZADAS (nombres de negocio cambiados, sin telefonos) mas
 * controles en los que el bot acerto.
 *
 * Uso:  node scripts/jev-casos.js      (lee JEV_API_KEY de .env)
 *
 * No toca la base ni manda nada por WhatsApp.
 */

require('dotenv').config();
const { crearJev } = require('../src/ia/jev');
const { NECESIDAD } = require('../src/ia/sombra');

const lead = (texto) => ({ de: 'lead', texto });
const bot = (texto) => ({ de: 'bot', texto });

const CASOS_NECESIDAD = [
  {
    nombre: '11-9: "Webs" es lo que hace, no lo que necesita',
    esperado: 'no_queda_claro',
    conversacion: [
      lead('Hola'), bot('¿Cómo se llama tu negocio?'), lead('Estudio Norte'),
      bot('¿A qué se dedica Estudio Norte?'), lead('Software'),
      bot('¿Qué tipo de software desarrollan? ¿Páginas web, tiendas online, sistemas a medida, automatizaciones, o agentes de IA?'),
      lead('Webs'),
    ],
  },
  {
    nombre: '12-9: "Sii" a una pregunta de cinco opciones',
    esperado: 'no_queda_claro',
    conversacion: [
      lead('¡Hola! ¿cómo puedo agendar mi demo?'), bot('¿Cómo se llama tu negocio?'), lead('Video Centro'),
      bot('¿A qué se dedica Video Centro?'), lead('Reproducimos videos y vendemos programa de todo tipo'),
      bot('Claro. ¿Qué necesitás? ¿Una página web, una tienda online, un sistema a medida, automatizar procesos, o un agente de IA que atienda o venda?'),
      lead('Sii'),
    ],
  },
  {
    nombre: '13-9: publicidad para conseguir clientas (no es ninguna de las cinco)',
    esperado: 'no_queda_claro',
    conversacion: [
      lead('¡Hola! ¿cómo puedo agendar mi demo?'), bot('Buenas, primero necesito saber cómo se llama tu negocio.'),
      lead('Salón Luna'),
      bot('Un salón de belleza. ¿Qué necesitás: una página web, una tienda online para vender productos, un sistema para manejar turnos, automatizar tareas, o algo más?'),
      lead('Para hacer propagandas para llamar clientas'),
    ],
  },
  {
    nombre: 'control: reservas de turnos',
    esperado: 'sistema_a_medida',
    conversacion: [lead('Tengo una barbería y quiero que los clientes reserven turno desde el celular')],
  },
  {
    nombre: 'control: web para mostrar el negocio',
    esperado: 'pagina_web',
    conversacion: [
      bot('¿Qué necesitás?'), lead('una página web para que la gente vea lo que hacemos y nos escriba'),
    ],
  },
  {
    nombre: 'control: vender online',
    esperado: 'ecommerce',
    conversacion: [lead('Vendo ropa por Instagram y quiero una tienda online con pagos')],
  },
];

const CASOS_OFERTA = [
  {
    nombre: '11-9: la oferta inventa una necesidad',
    esperado: true,
    conversacion: CASOS_NECESIDAD[0].conversacion,
    mensaje: 'Entendí que necesitás una solución que escale con tu negocio sin complicarte la operación. En 30 minutos vemos juntos cómo sería.',
  },
  {
    nombre: '12-9: la oferta inventa una plataforma de videos',
    esperado: true,
    conversacion: CASOS_NECESIDAD[1].conversacion,
    mensaje: 'Entiendo que necesitás llevar los videos y programas a una plataforma donde tus clientes puedan acceder desde cualquier lado.',
  },
  {
    nombre: 'control: la oferta repite lo que dijo',
    esperado: false,
    conversacion: CASOS_NECESIDAD[3].conversacion,
    mensaje: 'Buenísimo, que tus clientes reserven turno desde el celular es justo lo que armamos. En 30 minutos te mostramos cómo quedaría.',
  },
];

async function main() {
  const jev = crearJev({
    apiKey: process.env.JEV_API_KEY,
    url: process.env.JEV_URL || 'https://api.typesafe.ai/v1/systemone',
    modelo: process.env.JEV_MODELO || 'jev-latest',
  });
  if (!jev.activo) {
    console.error('Falta JEV_API_KEY en .env');
    process.exit(1);
  }

  const criterios = Object.fromEntries(Object.entries(NECESIDAD).map(([k, v]) => [k, v.criterio]));
  let aciertos = 0;
  let total = 0;
  const tiempos = [];

  console.log('== necesidad');
  for (const c of CASOS_NECESIDAD) {
    const r = await jev.preguntar(c.conversacion, {
      necesidad: { type: 'choice', instructions: '¿Qué necesita el lead de la agencia?', criteria: criterios },
    });
    total += 1;
    if (!r) { console.log(`  ERROR  ${c.nombre}`); continue; }
    const a = r.answers.necesidad;
    const ok = a.choice === c.esperado;
    aciertos += ok ? 1 : 0;
    tiempos.push(r.ms);
    console.log(`  ${ok ? 'ok   ' : 'FALLA'}  ${c.nombre}  → ${a.choice} (conf ${a.confidence}) ${r.ms}ms`);
  }

  console.log('== oferta');
  for (const c of CASOS_OFERTA) {
    const r = await jev.preguntar({ conversacion: c.conversacion, mensaje_del_bot: c.mensaje }, {
      inventa: {
        type: 'noul',
        instructions: '¿El mensaje del bot le atribuye al lead una necesidad, un problema o un objetivo que el lead no dijo en la conversación?',
        criteria: { true: 'Afirma algo del lead que el lead no dijo', false: 'Todo lo que dice del lead sale de la conversación' },
      },
    });
    total += 1;
    if (!r) { console.log(`  ERROR  ${c.nombre}`); continue; }
    const p = r.answers.inventa.noul;
    const ok = (p >= 0.5) === c.esperado;
    aciertos += ok ? 1 : 0;
    tiempos.push(r.ms);
    console.log(`  ${ok ? 'ok   ' : 'FALLA'}  ${c.nombre}  → p(inventa)=${p} ${r.ms}ms`);
  }

  tiempos.sort((x, y) => x - y);
  console.log(`\n${aciertos}/${total} aciertos · mediana ${tiempos[Math.floor(tiempos.length / 2)]}ms · max ${tiempos.at(-1)}ms`);
}

main().catch((e) => { console.error(e); process.exit(1); });
