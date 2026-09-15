'use strict';

const { S } = require('../../funnel/states');
const { normalizar } = require('../../telefono');
const { pausarHasta } = require('../../funnel/pausa');

/**
 * Endpoints que consume el panel WA del CRM (routes/wa.py).
 *
 * Replican el contrato del bot viejo tal cual, para que apagarlo sea cambiar
 * BOT_API_URL y nada mas: ni routes/wa.py ni el frontend del CRM se tocan.
 *
 * Ojo con dos cosas heredadas:
 * - Autentican con x-admin-token (o ?token=), no con x-api-key.
 * - Cuelgan de /api/, porque el CRM arma la URL como {BOT_API_URL}/api/{path}.
 */

/**
 * Traduce una fila nuestra al shape que espera el CRM.
 * main_problem y urgency van en null a proposito: son columnas que el bot viejo
 * declaraba y no escribia nunca, pero el frontend las lee.
 */
function aFormatoBot(lead, ultimoMensajeAt = null) {
  if (!lead) return null;
  return {
    id: lead.id,
    phone: lead.telefono,
    name: lead.business_name || lead.nombre,
    state: lead.fsm_state,
    score: lead.score ?? null,
    business_type: lead.business_type ?? null,
    main_problem: null,
    team_size: lead.team_size ?? null,
    budget: lead.budget ?? null,
    urgency: null,
    business_name: lead.business_name ?? null,
    colors: lead.colors ?? null,
    instagram_web: lead.instagram_web ?? null,
    needs: lead.needs ?? null,
    created_at: lead.created_at,
    last_message_at: ultimoMensajeAt,
    // Para el interruptor del panel. Van los dos porque significan cosas
    // distintas: enabled es la decision de una persona, paused_until es la
    // pausa que se puso sola y vence sola. Con solo el primero, el panel diria
    // que el bot esta prendido mientras el lead no recibe respuesta.
    bot_enabled: lead.bot_enabled !== 0,
    bot_paused_until: lead.bot_pausado_hasta ?? null,
  };
}

/**
 * Los archivos que vinieron con el mensaje, cada uno con la URL para bajarlo.
 *
 * El bot no tiene IP publica, asi que el navegador no puede pedirle el audio:
 * el CRM hace de intermediario. Por eso se devuelve una ruta de la API y no una
 * ruta de archivo.
 */
function mediosAFormatoBot(m) {
  let lista;
  try {
    lista = JSON.parse(m.media || '[]');
  } catch {
    return [];
  }
  if (!Array.isArray(lista)) return [];
  return lista.map((x, i) => ({
    tipo: x.tipo || 'archivo',
    segundos: x.segundos || 0,
    // El nombre con el que lo mandaron. Solo lo traen los documentos, y es lo
    // unico que los distingue entre si: "presupuesto.pdf" y "IMG-4032.pdf" son
    // cosas muy distintas para el que atiende.
    nombre: x.nombre || null,
    // null cuando no hay archivo —era muy grande, fallo la descarga, o ya se
    // borro por antiguedad—. Es la unica senal que tiene el panel para saberlo:
    // `archivo` es el nombre en disco y no sale de aca a proposito. Devolver
    // una url igual dejaba al navegador pidiendo algo que no existe, y no habia
    // forma de dibujar "mandó un video que no pudimos guardar".
    url: x.archivo ? `/api/messages/${m.id}/media/${i}` : null,
  }));
}

function mensajesAFormatoBot(mensajes) {
  return mensajes.map((m) => ({
    direction: m.direction,
    content: m.body,
    sent_at: m.created_at,
    media: mediosAFormatoBot(m),
  }));
}

function registrar(app, { cfg, repo, cola, embudo = null, media = null, logger }) {
  const LIMITE_MENSAJES = 120;

  function autorizado(req) {
    const token = req.headers['x-admin-token'] || req.query?.token;
    return Boolean(cfg.ADMIN_TOKEN) && token === cfg.ADMIN_TOKEN;
  }

  /**
   * La conversacion con el lead, sin los avisos al equipo.
   *
   * Un am_notice se guarda con el lead_id del lead del que HABLA, pero se manda
   * a otro numero. Mientras iban en el hilo, el panel no permitia saber que vio
   * el lead y que no: la mitad de lo que parecia que le escribiste nunca le
   * llego, y encima el "ultimo mensaje" de la lista podia ser un aviso interno.
   */
  function conMensajes(lead) {
    const mensajes = repo.mensajesDeLead(lead.id)
      .filter((m) => m.kind !== 'am_notice')
      .slice(-LIMITE_MENSAJES);
    const ultimo = mensajes.length ? mensajes[mensajes.length - 1].created_at : null;
    return { lead: aFormatoBot(lead, ultimo), messages: mensajesAFormatoBot(mensajes) };
  }

  app.addHook('onRequest', async (req, reply) => {
    if (!req.url.startsWith('/api/')) return;
    if (!autorizado(req)) return reply.code(401).send({ error: 'Unauthorized' });
  });

  app.get('/api/leads', async (req) => {
    const limite = Math.min(parseInt(req.query.limit, 10) || 50, 200);
    let leads = repo.listarLeads(limite);
    if (req.query.state) leads = leads.filter((l) => l.fsm_state === req.query.state);
    return { leads: leads.map((l) => aFormatoBot(l)), count: leads.length };
  });

  /**
   * El archivo que vino con un mensaje: hoy, la nota de voz.
   *
   * Lo pide el CRM y lo reenvia al navegador, porque el bot vive solo en la red
   * privada. El indice existe porque un turno puede traer mas de un audio: el
   * agrupador junta los mensajes que llegan seguidos.
   */
  app.get('/api/messages/:id/media/:idx', async (req, reply) => {
    if (!media) return reply.code(404).send({ error: 'sin almacenamiento de archivos' });

    const fila = repo.mensajePorId(Number(req.params.id));
    if (!fila) return reply.code(404).send({ error: 'no existe ese mensaje' });

    let lista;
    try {
      lista = JSON.parse(fila.media || '[]');
    } catch {
      lista = [];
    }
    const item = lista[Number(req.params.idx)];
    if (!item) return reply.code(404).send({ error: 'ese mensaje no tiene ese archivo' });

    // El nombre sale de la base, no de la URL: el que pide elige el indice, no
    // el archivo. Igual media.leer() no deja salir del directorio.
    const cuerpo = media.leer(item.archivo);
    if (!cuerpo) return reply.code(404).send({ error: 'el archivo ya no esta' });

    return reply.type(media.contentType(item.archivo)).send(cuerpo);
  });

  app.get('/api/leads/phone/:phone', async (req, reply) => {
    // El CRM manda el telefono en cualquier formato; el nuestro esta canonizado,
    // asi que alcanza con normalizar la entrada.
    const tel = normalizar(req.params.phone, cfg.DEFAULT_COUNTRY_CODE);
    const lead = tel ? repo.leadPorTelefono(tel) : null;
    if (!lead) return reply.code(404).send({ error: 'Lead no encontrado' });
    return conMensajes(lead);
  });

  app.get('/api/leads/search', async (req, reply) => {
    const nombre = (req.query.name || '').trim();
    if (!nombre) return reply.code(404).send({ error: 'name requerido' });
    const encontrados = repo.buscarPorNombre(nombre);
    if (!encontrados.length) return reply.code(404).send({ error: 'Lead no encontrado' });
    return conMensajes(encontrados[0]);
  });

  app.post('/api/send', async (req, reply) => {
    const { phone, text } = req.body || {};
    const tel = normalizar(phone, cfg.DEFAULT_COUNTRY_CODE);
    if (!tel || !String(text || '').trim()) {
      return reply.code(400).send({ error: 'phone y text requeridos' });
    }
    const lead = repo.leadPorTelefono(tel);
    // humano: lo escribio una persona desde el CRM, sale aunque sea fuera de horario.
    cola.encolar({ to: tel, texto: String(text).trim(), kind: 'manual', leadId: lead ? lead.id : null, humano: true });

    /**
     * Escribirle desde el panel tambien pausa al bot en ese chat.
     *
     * La pausa automatica se disparaba solo cuando la persona escribia desde su
     * celular, porque se detecta por el eco de Baileys — y esto sale por la cola
     * del propio bot, asi que el eco se reconoce como propio y se descarta.
     * Resultado: escribias desde el CRM y el bot te seguia contestando por
     * arriba, en el mismo chat.
     *
     * Es la misma decision en los dos casos: si entro una persona, el bot se
     * corre. Y vence sola, igual que la otra.
     */
    if (lead) repo.actualizarFunnel(lead.id, { bot_pausado_hasta: pausarHasta() });

    return { ok: true };
  });

  app.post('/api/leads/phone/:phone/release', async (req, reply) => {
    const tel = normalizar(req.params.phone, cfg.DEFAULT_COUNTRY_CODE);
    const lead = tel ? repo.leadPorTelefono(tel) : null;
    if (!lead) return reply.code(404).send({ error: 'Lead no encontrado' });
    // Devolver el lead al bot: se libera el flag y vuelve a conversar.
    //
    // Estaba puesto 'MENU', que es un estado que ya no existe —se fue con el
    // embudo de preguntas numeradas—. Como no esta en ninguna de las dos fases
    // que atiende la IA, el lead devuelto no era de nadie: el bot se comia su
    // siguiente mensaje sin contestar y recien despues lo dejaba en
    // CONVERSANDO. Justo el mensaje de alguien que vuelve despues de hablar
    // con una persona.
    repo.actualizarFunnel(lead.id, { human_requested: 0, fsm_state: S.CONVERSANDO });
    logger?.info({ leadId: lead.id }, 'lead devuelto al bot desde el CRM');
    return { ok: true };
  });

  /**
   * El boton de on/off del panel, por lead.
   *
   * Es el respaldo, no el mecanismo principal: el bot ya se pausa solo cuando
   * escribis vos desde el telefono, y esa pausa vence sola. Este es para
   * apagarlo a proposito y por tiempo indefinido.
   *
   * Prenderlo tambien levanta la pausa automatica que hubiera corriendo. Si no,
   * decis que si y el bot sigue mudo unas horas mas sin ninguna explicacion.
   */
  app.post('/api/leads/phone/:phone/bot', async (req, reply) => {
    const tel = normalizar(req.params.phone, cfg.DEFAULT_COUNTRY_CODE);
    const lead = tel ? repo.leadPorTelefono(tel) : null;
    if (!lead) return reply.code(404).send({ error: 'Lead no encontrado' });

    const activo = req.body?.activo !== false;
    repo.actualizarFunnel(lead.id, {
      bot_enabled: activo ? 1 : 0,
      bot_pausado_hasta: activo ? null : lead.bot_pausado_hasta,
    });
    logger?.info({ leadId: lead.id, activo }, 'el bot se prendio o apago desde el CRM');
    return { ok: true, activo };
  });

  /**
   * Marcar que no es un cliente posible.
   *
   * Es la via principal para descalificar: el bot lo hace solo unicamente si se
   * prende DESCALIFICACION_AUTOMATICA, y esta apagada. Equivocarse para este
   * lado cuesta un cliente, asi que la decision es de una persona.
   */
  app.post('/api/leads/phone/:phone/descartar', async (req, reply) => {
    const tel = normalizar(req.params.phone, cfg.DEFAULT_COUNTRY_CODE);
    const lead = tel ? repo.leadPorTelefono(tel) : null;
    if (!lead) return reply.code(404).send({ error: 'Lead no encontrado' });

    const motivo = String(req.body?.motivo || '').trim().slice(0, 120) || 'a mano';
    embudo.descartar(lead.id, motivo);
    logger?.info({ leadId: lead.id, motivo }, 'lead descalificado desde el CRM');
    return { ok: true };
  });

  /** Deshacer lo anterior: vuelve al embudo como si nada. */
  app.post('/api/leads/phone/:phone/recuperar', async (req, reply) => {
    const tel = normalizar(req.params.phone, cfg.DEFAULT_COUNTRY_CODE);
    const lead = tel ? repo.leadPorTelefono(tel) : null;
    if (!lead) return reply.code(404).send({ error: 'Lead no encontrado' });

    repo.actualizarFunnel(lead.id, {
      fsm_state: S.CONVERSANDO,
      fsm_retries: 0,
      no_cliente_motivo: null,
      no_cliente_desde: null,
    });
    logger?.info({ leadId: lead.id }, 'lead recuperado desde el CRM');
    return { ok: true };
  });
}

module.exports = { registrar, aFormatoBot, mensajesAFormatoBot };
