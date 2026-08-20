'use strict';

const { S } = require('../../funnel/states');
const { normalizar } = require('../../telefono');

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
  };
}

function mensajesAFormatoBot(mensajes) {
  return mensajes.map((m) => ({
    direction: m.direction,
    content: m.body,
    sent_at: m.created_at,
  }));
}

function registrar(app, { cfg, repo, cola, embudo = null, logger }) {
  const LIMITE_MENSAJES = 120;

  function autorizado(req) {
    const token = req.headers['x-admin-token'] || req.query?.token;
    return Boolean(cfg.ADMIN_TOKEN) && token === cfg.ADMIN_TOKEN;
  }

  function conMensajes(lead) {
    const mensajes = repo.mensajesDeLead(lead.id).slice(-LIMITE_MENSAJES);
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
    cola.encolar({ to: tel, texto: String(text).trim(), kind: 'manual', leadId: lead ? lead.id : null });
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
