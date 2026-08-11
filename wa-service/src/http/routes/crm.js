'use strict';

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

function registrar(app, { cfg, repo, cola, logger }) {
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
    // Devolver el lead al bot: se libera el flag y vuelve al menu.
    repo.actualizarFunnel(lead.id, { human_requested: 0, fsm_state: 'MENU' });
    logger?.info({ leadId: lead.id }, 'lead devuelto al bot desde el CRM');
    return { ok: true };
  });
}

module.exports = { registrar, aFormatoBot, mensajesAFormatoBot };
