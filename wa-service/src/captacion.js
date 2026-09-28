'use strict';

/**
 * El grupo de captación en la calle (pedido de Juan, 28/9).
 *
 * El equipo anota en un grupo de WhatsApp los locales que visita, como lo
 * escribe siempre ("Pasé por La Pizzería de Juan en Pocitos, el dueño Martín
 * 099 123 456, le interesó, llamar el jueves"). El bot está en ese grupo: la IA
 * saca de cada mensaje los locales y cómo salió, los carga en el Outbound de
 * Fidelidad del CRM (POST /api/fidelidad/visitas) y contesta en el grupo lo
 * que cargó, para que se vea enseguida si entendió bien.
 *
 * Escucha SOLO el grupo de GRUPO_CAPTACION_JID. Los demás grupos siguen
 * ignorados como siempre, y el embudo de leads no se entera de nada de esto.
 * Lo que no es un avance ("hoy almorzamos") no se contesta.
 */

const HERRAMIENTA = {
  nombre: 'cargar_visitas',
  descripcion: 'Los locales visitados que aparecen en el mensaje, para cargarlos en el CRM.',
  parametros: {
    type: 'object',
    properties: {
      es_avance: {
        type: 'boolean',
        description: 'true si el mensaje cuenta una visita o un avance con uno o más locales; false si es charla, logística o cualquier otra cosa.',
      },
      visitas: {
        type: 'array',
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
              description: 'visitado: pasaron sin más; interesado: le gustó o pidió info o que lo llamen; reunion: quedó una reunión o demo con fecha; no_interesa: dijo que no; cliente: cerró.',
            },
            proxima: {
              type: 'string',
              description: 'Cuándo volver a llamar, o la fecha de la reunión, como AAAA-MM-DDTHH:MM en hora de Montevideo. Vacío si no dicen.',
            },
            nota: { type: 'string', description: 'Lo que dijeron que sirve para la próxima charla, en una frase.' },
          },
          required: ['nombre', 'resultado'],
        },
      },
    },
    required: ['es_avance', 'visitas'],
  },
};

const { crearPedidos } = require('./captacion-pedidos');

const DIAS = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'];

function hoyEnMontevideo(ahora) {
  return ahora.toLocaleString('es-UY', {
    timeZone: 'America/Montevideo', weekday: 'long', day: 'numeric', month: 'long',
    year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function sistema(ahora) {
  return [
    'Sos el asistente del equipo de Scalerics que sale a la calle a ofrecer Scalerics Fidelidad (un sistema de puntos) a restaurantes y peluquerías.',
    'Leés los mensajes del grupo donde anotan las visitas y sacás cada local visitado con cómo salió.',
    `Ahora es ${hoyEnMontevideo(ahora)} en Montevideo.`,
    'Reglas:',
    '- Si el mensaje no cuenta ninguna visita (charla, horarios, chistes, fotos sin datos), es_avance=false y visitas vacío.',
    '- Un mensaje puede traer varios locales: uno por visita.',
    '- Fechas relativas a fecha exacta: "mañana", "el jueves", "la semana que viene". "A la mañana" es 11:00, "a la tarde" 16:00; sin hora, 11:00.',
    '- La ciudad es Montevideo salvo que digan Buenos Aires, CABA o un barrio porteño (Palermo de Buenos Aires, Caballito, Almagro, Villa Crespo…).',
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

  const pedidos = crearPedidos({
    crm, modelo, sistema: () => sistema(ahora()), visitaSchema: HERRAMIENTA.parametros.properties.visitas,
    cargarVisita: cargar, resumenVisita: resumen, ahora,
  });

  /** Le hablan al bot: lo nombran, empiezan con "bot" o le responden a él. */
  function esPedido(m) {
    return Boolean(m.alBot) || /^\s*@?bot(?![a-z])/i.test(m.texto);
  }

  async function recibir(m) {
    if (m.grupo !== grupo) {
      // Así se averigua el JID del grupo la primera vez: sale en el log.
      logger?.info({ grupo: m.grupo }, 'mensaje de un grupo que no es el de captación');
      return;
    }
    if (!activo || !m.texto || !m.texto.trim()) return;
    if (repo && !repo.entranteEsNuevo(m.id)) return;

    if (esPedido(m)) {
      let texto;
      try {
        texto = await pedidos.atender(m);
      } catch (e) {
        logger?.warn({ err: String(e.message || e) }, 'captación: falló un pedido');
        texto = `No pude hacerlo: ${e.message || e}`;
      }
      logger?.info({ id: m.id }, 'captación: pedido');
      await proveedor.enviarTexto(grupo, texto);
      return;
    }

    const r = await modelo.pedir({
      system: sistema(ahora()),
      mensajes: [{ role: 'user', content: `${m.nombre || 'Alguien del equipo'} escribió:\n${m.texto}` }],
      herramienta: HERRAMIENTA,
      maxTokens: 900,
    });
    const a = r?.argumentos;
    if (!a || !a.es_avance || !Array.isArray(a.visitas) || !a.visitas.length) {
      logger?.info({ id: m.id, ia: Boolean(r) }, 'captación: el mensaje no es un avance');
      return;
    }
    logger?.info({ id: m.id, visitas: a.visitas.length }, 'captación: avance');

    const partes = [];
    for (const v of a.visitas.slice(0, 6)) {
      try {
        partes.push(resumen(await cargar(v, m.nombre || '')));
      } catch (e) {
        logger?.warn({ err: String(e.message || e) }, 'captación: no se pudo cargar una visita');
        partes.push(`No pude cargar *${v.nombre}*: ${e.message || e}`);
      }
    }
    const titulo = partes.some((x) => !x.startsWith('No pude')) ? '✓ Cargado en el CRM\n' : '';
    await proveedor.enviarTexto(grupo, titulo + partes.join('\n\n'));
  }

  /**
   * Un mensaje del bot al grupo. Ademas de avisarle al equipo, arregla el
   * cifrado: al mandar a un grupo, WhatsApp arma una sesion nueva con cada
   * miembro. Sin eso, a un bot recien agregado le llegan mensajes de algunos
   * miembros que no puede descifrar ("No session found").
   */
  async function saludar() {
    if (!grupo) throw new Error('no hay GRUPO_CAPTACION_JID');
    return proveedor.enviarTexto(grupo, 'Hola 👋 Soy el bot de Scalerics. Desde ahora leo este grupo: '
      + 'cuando anoten una visita (el local, cómo salió y cuándo volver) la cargo en el CRM y les confirmo acá.');
  }

  return { activo, recibir, saludar };
}

module.exports = { crearCaptacion, HERRAMIENTA, resumen, fechaCorta };
