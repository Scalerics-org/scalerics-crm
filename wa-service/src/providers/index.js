'use strict';

const mock = require('./mock');

/**
 * Fabrica de proveedores segun WA_PROVIDER.
 * baileys se agrega en la Fase 3, cuando exista el numero secundario; hasta
 * entonces pedirlo falla con un mensaje claro en vez de romper raro.
 */
function crearProveedor(cfg, deps = {}) {
  switch (cfg.WA_PROVIDER) {
    case 'mock':
      return mock.crear(deps);

    case 'baileys': {
      let baileys;
      try {
        baileys = require('./baileys');
      } catch (e) {
        throw new Error(
          'WA_PROVIDER=baileys pero el proveedor todavia no esta implementado ' +
          '(Fase 3, necesita el numero secundario). Usar WA_PROVIDER=mock mientras tanto.'
        );
      }
      return baileys.crear(cfg, deps);
    }

    default:
      throw new Error(`WA_PROVIDER desconocido: ${cfg.WA_PROVIDER}`);
  }
}

module.exports = { crearProveedor };
