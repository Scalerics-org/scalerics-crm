'use strict';

const { cargar } = require('../src/config');
const { construir } = require('../src/app');

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
    CALENDLY_LINK: 'https://calendly.com/scalerics/diagnostico',
    // Sin esperas: los delays ya tienen sus propios tests.
    DELAY_AM_MIN_MS: '0', DELAY_AM_MAX_MS: '0',
    DELAY_WELCOME_MIN_MS: '0', DELAY_WELCOME_MAX_MS: '0',
    DELAY_BETWEEN_MIN_MS: '0', DELAY_BETWEEN_MAX_MS: '0',
    TYPING_ENABLED: 'false',
    FOLLOWUP_JITTER_MINUTES: '0',
    BUSINESS_HOURS: '00:00-23:59',
    BUSINESS_DAYS: 'sun-sat',
    ...extra,
  });
}

/**
 * Servicio montado y conectado.
 * @param {Date} [reloj] congela el tiempo, para probar horario comercial.
 */
async function montar(extra, reloj) {
  const s = construir(cfgTest(extra), {
    logger: null,
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
  s.servicioLeads.alta(datos);
  await s.cola.vacia();
  s.proveedor.limpiar();
  return s;
}

module.exports = { CLAVE, ADMIN, LEAD, cfgTest, montar, conLead };
