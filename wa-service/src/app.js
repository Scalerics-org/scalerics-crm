'use strict';

const { abrir } = require('./db');
const { crearRepo } = require('./db/repo');
const { crearProveedor } = require('./providers');
const { crearCola } = require('./outbound/queue');
const { crearServicioLeads } = require('./leads');
const { crearScheduler } = require('./scheduler/followup');
const { crearServidor } = require('./http/server');
const { crear: crearLogger } = require('./logger');

/**
 * Arma el servicio entero y devuelve las piezas.
 * Los tests lo llaman con una config a medida y :memory: como base.
 */
function construir(cfg, { logger } = {}) {
  const log = logger || crearLogger({
    level: cfg.LOG_LEVEL,
    produccion: cfg.NODE_ENV === 'production',
  });

  const db = abrir(cfg.DB_PATH);
  const repo = crearRepo(db);
  const proveedor = crearProveedor(cfg, { logger: log });
  const cola = crearCola({ proveedor, repo, cfg, logger: log });
  const servicioLeads = crearServicioLeads({ repo, cola, cfg, logger: log });
  const scheduler = crearScheduler({ repo, cola, cfg, logger: log });
  const app = crearServidor({ cfg, repo, cola, proveedor, servicioLeads, scheduler, logger: log });

  return { cfg, db, repo, proveedor, cola, servicioLeads, scheduler, app, logger: log };
}

module.exports = { construir };
