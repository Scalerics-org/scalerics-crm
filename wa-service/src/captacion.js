'use strict';

/**
 * El grupo de captación en la calle (pedido de Juan, 28/9).
 *
 * El equipo anota en un grupo de WhatsApp los locales que visita, como lo
 * escribe siempre ("Pasé por La Pizzería de Juan en Pocitos, el dueño Martín
 * 099 123 456, le interesó, llamar el jueves"), y le pide cosas al bot. El bot
 * está en ese grupo y contesta ahí.
 *
 * Cada mensaje se lee UNA vez, con los últimos del grupo como contexto, y la
 * IA decide si es un avance (se carga en el Outbound de Fidelidad por POST
 * /api/fidelidad/visitas), un pedido (se hace: ver captacion-pedidos.js) o
 * charla.
 *
 * El bot hace algo SOLO si lo arroban (Juan, 28/9: "para evitar gastar
 * dinero y que no se mezcle"). Sin @ no se llama a la IA ni se carga nada: el
 * mensaje solo queda en la memoria corta, para que un "@bot cargá lo que dijo
 * Gonzalo" sepa de qué hablan.
 * Juan, al probarlo: "funciona pero no entiende todo". Por eso el contexto, un
 * modelo más capaz que el del embudo (CAPTACION_MODELO).
 *
 * Escucha SOLO el grupo de GRUPO_CAPTACION_JID. Los demás grupos siguen
 * ignorados como siempre, y el embudo de leads no se entera de nada de esto.
 */

const { crearPedidos, PROPIEDADES_PEDIDO } = require('./captacion-pedidos');

const VISITAS = {
  type: 'array',
  description: 'Solo si tipo=avance, o si piden cargar locales: un elemento por local.',
  items: {
    type: 'object',
    properties: {
      nombre: { type: 'string', description: 'Nombre del local, como lo escribieron.' },
      barrio: { type: 'string' },
      ciudad: { type: 'string', enum: ['Montevideo', 'Buenos Aires'] },
      tipo: { type: 'string', description: 'Pizzería, hamburguesería, barbería, peluquería, café…' },
      direccion: { type: 'string' },
      telefono: { type: 'string', description: 'Teléfono del local, si lo dan.' },
      contacto: { type: 'string', description: 'Nombre del dueño o encargado.' },
      contacto_tel: { type: 'string', description: 'Celular del dueño o encargado.' },
      resultado: {
        type: 'string',
        enum: ['visitado', 'interesado', 'reunion', 'no_interesa', 'cliente'],
        description: 'visitado: pasaron sin más o no estaba el dueño; interesado: le gustó, pidió info o que lo llamen; reunion: quedó una reunión o demo con fecha; no_interesa: dijo que no; cliente: cerró.',
      },
      proxima: {
        type: 'string',
        description: 'Cuándo volver a llamar, o la fecha de la reunión, como AAAA-MM-DDTHH:MM en hora de Montevideo. Vacío si no dicen.',
      },
      nota: { type: 'string', description: 'Lo que dijeron que sirve para la próxima charla, en una frase.' },
    },
    required: ['nombre', 'resultado'],
  },
};

const HERRAMIENTA = {
  nombre: 'interpretar_mensaje',
  descripcion: 'Qué es el último mensaje del grupo y qué hay que hacer con él.',
  parametros: {
    type: 'object',
    properties: {
      tipo: {
        type: 'string',
        enum: ['avance', 'pedido', 'charla'],
        description: 'avance: cuenta una o más visitas a locales. pedido: le piden algo al bot o al CRM. charla: todo lo demás.',
      },
      visitas: VISITAS,
      ...PROPIEDADES_PEDIDO,
    },
    required: ['tipo'],
  },
};

const DIAS = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'];
const MEMORIA = 8;

function hoyEnMontevideo(ahora) {
  return ahora.toLocaleString('es-UY', {
    timeZone: 'America/Montevideo', weekday: 'long', day: 'numeric', month: 'long',
    year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function sistema(ahora) {
  return [
    'Sos el asistente del equipo de Scalerics que sale a la calle a ofrecer Scalerics Fidelidad (un sistema de puntos) a restaurantes y peluquerías.',
    'Estás en el grupo de WhatsApp donde anotan las visitas. Te pasan los últimos mensajes como contexto; interpretá SOLO el último.',
    `Ahora es ${hoyEnMontevideo(ahora)} en Montevideo.`,
    'Qué es cada cosa:',
    '- avance: cuentan que pasaron por uno o más locales y cómo salió, aunque sea telegráfico ("Rodelú no", "fui a La Esquina, le copó, volver el jueves", una lista con varios). Un local por visita.',
    '- pedido: le piden algo al bot o al CRM: "cargá…", "anotá…", "agendá…", "¿cuántas visitas…?", "¿a quién llamo?", "¿qué sabemos de…?", "el dueño de X es…". También si corrigen lo que el bot acaba de cargar ("no, era en Cordón").',
    '- charla: saludos, logística del equipo entre ellos, chistes, fotos sin datos.',
    '- Si el mensaje le habla al bot y no sabés qué quieren, es pedido con accion=no_entiendo y una "pregunta" para aclarar.',
    '- Si para hacer el pedido falta un dato (qué local, qué día), completá "pregunta" en vez de adivinar.',
    '- Usá el contexto: "ese", "el de recién", "sí, dale" se refieren a lo último que se habló.',
    'Reglas para los datos:',
    '- Fechas relativas a fecha exacta: "mañana", "el jueves", "la semana que viene". "A la mañana" es 11:00, "a la tarde" 16:00; sin hora, 11:00.',
    '- La ciudad es Montevideo salvo que digan Buenos Aires, CABA o un barrio porteño (Caballito, Almagro, Villa Crespo…).',
    '- No inventes teléfonos, nombres ni barrios: si no están, dejalos vacíos.',
  ].join('\n');
}

function fechaCorta(txt) {
  const m = String(txt || '').match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (!m) return '';
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return `${DIAS[d.getDay()]} ${Number(m[3])}/${Number(m[2])} ${m[4]}:${m[5]}`;
}

/** La línea de respuesta en el grupo para un local cargado. */
function resumen(r) {
  const p = r.prospecto || {};
  const lineas = [`*${p.nombre}*${p.barrio ? ` · ${p.barrio}` : ''}${r.nuevo ? '' : ' (ya estaba: actualizado)'}`];
  const dueno = [p.contacto, p.contacto_tel].filter(Boolean).join(' · ');
  if (dueno) lineas.push(`Dueño: ${dueno}`);
  let estado = r.resultado;
  if (p.estado === 'reunion_agendada' && p.fecha_reunion) estado += ` · reunión ${fechaCorta(p.fecha_reunion)}`;
  else if (p.estado === 'descartado') estado += ' · vuelve en un año';
  else if (p.estado !== 'cerrado' && p.proxima_llamada) estado += ` · llamar ${fechaCorta(p.proxima_llamada)}`;
  lineas.push(estado);
  return lineas.join('\n');
}

function crearCaptacion({ cfg, modelo, proveedor, repo = null, logger = null, ahora = () => new Date() }) {
  const grupo = (cfg.GRUPO_CAPTACION_JID || '').trim();
  const activo = Boolean(grupo && cfg.CRM_API_URL && cfg.CRM_ADMIN_TOKEN && modelo?.activo);
  // Los últimos mensajes del grupo (y lo que contestó el bot), para que la IA
  // entienda "ese", "sí, dale" o una corrección. Solo en memoria.
  const historial = [];
  const recordar = (autor, texto) => {
    historial.push({ autor, texto: String(texto).slice(0, 600) });
    if (historial.length > MEMORIA) historial.shift();
  };

  async function crm(method, path, body) {
    const r = await fetch(`${cfg.CRM_API_URL.replace(/\/$/, '')}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json', 'x-admin-token': cfg.CRM_ADMIN_TOKEN },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(10000),
    });
    let datos = null;
    try { datos = await r.json(); } catch { /* sin cuerpo */ }
    if (!r.ok) throw new Error((datos && datos.error) || `el CRM contestó ${r.status}`);
    return datos;
  }

  function cargar(visita, autor) {
    const limpia = Object.fromEntries(Object.entries(visita).filter(([, x]) => x !== '' && x != null));
    return crm('POST', '/api/fidelidad/visitas', { ...limpia, autor, fuente: 'whatsapp' });
  }

  const pedidos = crearPedidos({ crm, cargarVisita: cargar, resumenVisita: resumen, ahora });


  async function cargarVarias(visitas, autor) {
    const partes = [];
    for (const v of visitas.slice(0, 6)) {
      try {
        partes.push(resumen(await cargar(v, autor)));
      } catch (e) {
        logger?.warn({ err: String(e.message || e) }, 'captación: no se pudo cargar una visita');
        partes.push(`No pude cargar *${v.nombre}*: ${e.message || e}`);
      }
    }
    const titulo = partes.some((x) => !x.startsWith('No pude')) ? '✓ Cargado en el CRM\n' : '';
    return titulo + partes.join('\n\n');
  }

  async function responder(texto) {
    recordar('Bot', texto);
    await proveedor.enviarTexto(grupo, texto);
  }

  async function recibir(m) {
    if (m.grupo !== grupo) {
      // Así se averigua el JID del grupo la primera vez: sale en el log.
      logger?.info({ grupo: m.grupo }, 'mensaje de un grupo que no es el de captación');
      return;
    }
    if (!activo || !m.texto || !m.texto.trim()) return;
    if (repo && !repo.entranteEsNuevo(m.id)) return;

    const autor = m.nombre || 'Alguien del equipo';
    const contexto = historial.length
      ? `Mensajes anteriores del grupo:\n${historial.map((h) => `[${h.autor}] ${h.texto}`).join('\n')}\n\n`
      : '';
    recordar(autor, m.texto);
    if (!m.alBot) return;

    const r = await modelo.pedir({
      system: sistema(ahora()),
      mensajes: [{
        role: 'user',
        content: `${contexto}Último mensaje, de ${autor} (le habla al bot):\n${m.texto}`,
      }],
      herramienta: HERRAMIENTA,
      maxTokens: 1200,
    });
    const a = r?.argumentos;
    if (!a) {
      logger?.warn({ id: m.id }, 'captación: la IA no contestó');
      await responder('Ahora no puedo pensar (falló la IA). Probá de nuevo en un rato.');
      return;
    }
    logger?.info({ id: m.id, tipo: a.tipo, accion: a.accion, visitas: a.visitas?.length || 0 }, 'captación: interpretado');

    let texto = null;
    try {
      if (a.tipo === 'avance' && Array.isArray(a.visitas) && a.visitas.length) {
        texto = await cargarVarias(a.visitas, m.nombre || '');
      } else {
        texto = await pedidos.ejecutar({ ...a, accion: a.tipo === 'pedido' ? a.accion : 'no_entiendo' }, m);
      }
    } catch (e) {
      logger?.warn({ err: String(e.message || e) }, 'captación: falló');
      texto = `No pude hacerlo: ${e.message || e}`;
    }
    if (texto) await responder(texto);
  }

  /**
   * Un mensaje del bot al grupo. Ademas de avisarle al equipo, arregla el
   * cifrado: al mandar a un grupo, WhatsApp arma una sesion nueva con cada
   * miembro. Sin eso, a un bot recien agregado le llegan mensajes de algunos
   * miembros que no puede descifrar ("No session found").
   */
  async function saludar() {
    if (!grupo) throw new Error('no hay GRUPO_CAPTACION_JID');
    return proveedor.enviarTexto(grupo, 'Hola 👋 Soy el bot de Scalerics. Cuando me necesiten, arróbenme: '
      + 'cargo visitas en el CRM, agendo, anoto y les cuento cómo vamos. Si no me arroban, no leo nada.');
  }

  return { activo, recibir, saludar };
}

module.exports = { crearCaptacion, HERRAMIENTA, resumen, fechaCorta };
