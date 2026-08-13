'use strict';

const { abrir } = require('./db');
const { crearRepo } = require('./db/repo');
const { crearProveedor } = require('./providers');
const { crearCola } = require('./outbound/queue');
const { crearLimites } = require('./outbound/limits');
const { crearServicioLeads } = require('./leads');
const { crearScheduler } = require('./scheduler/followup');
const { crearServidor } = require('./http/server');
const { crear: crearLogger } = require('./logger');
const { crearEmbudo } = require('./funnel/engine');
const { crearScorer } = require('./funnel/scoring');
const { crearTextos } = require('./templates/funnel');
const { crearTextosLead } = require('./templates');
const { crearNotificadorCRM } = require('./crm-notify');

/**
 * Arma el servicio entero y devuelve las piezas.
 * Los tests lo llaman con una config a medida y :memory: como base.
 */
function construir(cfg, { logger, ahora = () => new Date() } = {}) {
  const log = logger || crearLogger({
    level: cfg.LOG_LEVEL,
    produccion: cfg.NODE_ENV === 'production',
  });

  const db = abrir(cfg.DB_PATH);
  const repo = crearRepo(db);
  // buscarMensaje deja que baileys reenvie un mensaje cuando el dispositivo del
  // destinatario no lo pudo descifrar y pide el reintento.
  const proveedor = crearProveedor(cfg, {
    logger: log,
    buscarMensaje: (providerMsgId) => repo.cuerpoPorProviderId(providerMsgId),
  });
  const limites = crearLimites({ repo, cfg, logger: log });
  const cola = crearCola({ proveedor, repo, cfg, logger: log, limites, ahora });

  // Si se abre el circuit breaker, que el AM se entere y responda a mano.
  cola.onPausa((hasta) => {
    for (const am of cfg.amPhones) {
      cola.encolar({
        to: am,
        texto: `⚠️ El canal de WhatsApp se pausó por fallos repetidos.\nSe retoma ${hasta.toLocaleString('es-UY', { timeZone: cfg.TZ })}.\nMientras tanto hay que contestar a mano.`,
        kind: 'am_notice',
      });
    }
  });

  // Sin ANTHROPIC_API_KEY el scoring cae a reglas, no se rompe.
  let anthropic = null;
  if (cfg.ANTHROPIC_API_KEY) {
    const Anthropic = require('@anthropic-ai/sdk');
    anthropic = new Anthropic({ apiKey: cfg.ANTHROPIC_API_KEY });
  }
  const scorer = crearScorer({ anthropic, logger: log });
  const textos = crearTextos({ calendlyLink: cfg.CALENDLY_LINK });
  const textosLead = crearTextosLead({ calendlyLink: cfg.CALENDLY_LINK });
  const crmNotify = crearNotificadorCRM({ cfg, repo, logger: log });
  const embudo = crearEmbudo({ repo, cola, textos, scorer, logger: log, cfg, crmNotify });

  const scheduler = crearScheduler({ repo, cola, cfg, textosLead, logger: log, ahora });
  const servicioLeads = crearServicioLeads({
    repo, cola, cfg, logger: log, textosLead, embudo, scheduler, ahora,
  });

  // Todo lo que entra por WhatsApp pasa por aca: marca la respuesta, cancela el
  // follow-up, avisa al AM y sigue el embudo.
  // Acuses de entrega. "sent" solo dice que el proveedor lo acepto; si el
  // destinatario no lo puede descifrar y le queda en "Esperando este mensaje",
  // sin esto nadie se entera.
  proveedor.alCambiarEstado?.((providerMsgId, estado) => {
    if (repo.marcarEntrega(providerMsgId, estado)) {
      log.debug({ providerMsgId, estado }, 'acuse de recibo');
    }
  });

  proveedor.alRecibir(({ from, texto, nombre }) => {
    servicioLeads.registrarRespuesta(from, texto, nombre).catch((e) => {
      log.error({ from, err: String(e.message || e) }, 'fallo procesando un mensaje entrante');
    });
  });
  const app = crearServidor({ cfg, repo, cola, proveedor, servicioLeads, scheduler, logger: log });

  return { cfg, db, repo, proveedor, cola, limites, servicioLeads, scheduler, embudo, scorer, app, logger: log };
}

module.exports = { construir };
