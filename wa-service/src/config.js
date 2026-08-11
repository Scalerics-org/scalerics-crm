'use strict';

require('dotenv').config();
const { z } = require('zod');

// z.coerce.boolean() no sirve para variables de entorno: hace Boolean("false"),
// que es true. Cualquier valor no vacio quedaria en true.
const booleanoDeEnv = z
  .union([z.boolean(), z.string()])
  .transform((v) =>
    typeof v === 'boolean' ? v : !['false', '0', 'no', 'off', ''].includes(v.trim().toLowerCase())
  );

// Falla al arrancar si falta algo, en vez de romper a mitad de un envio.
const esquema = z.object({
  PORT: z.coerce.number().int().positive().default(8080),
  NODE_ENV: z.string().default('development'),
  LOG_LEVEL: z.string().default('info'),
  WA_API_KEY: z.string().min(16, 'WA_API_KEY tiene que tener al menos 16 caracteres'),

  // Token del panel WA del CRM. Es el mismo ADMIN_TOKEN que ya usa routes/wa.py,
  // que autentica con el header x-admin-token y no con x-api-key.
  ADMIN_TOKEN: z.string().default(''),
  // Para avisarle al CRM cuando un lead califica.
  CRM_API_URL: z.string().default(''),
  CRM_ADMIN_TOKEN: z.string().default(''),

  WA_PROVIDER: z.enum(['baileys', 'mock']).default('mock'),
  DB_PATH: z.string().default('./data/scalerics-wa.db'),

  BAILEYS_AUTH_DIR: z.string().default('./auth'),

  // Coma-separado. Puede ser un JID de grupo (...@g.us) cuando el proveedor lo soporte.
  AM_PHONES: z.string().default(''),
  DEFAULT_COUNTRY_CODE: z.string().default('598'),
  TZ: z.string().default('America/Montevideo'),

  // Embudo de calificacion. Sin ANTHROPIC_API_KEY el scoring cae a reglas.
  ANTHROPIC_API_KEY: z.string().default(''),
  CALENDLY_LINK: z.string().default('https://calendly.com/scalerics/diagnostico'),
  FUNNEL_ENABLED: booleanoDeEnv.default(true),

  FOLLOWUP_DELAY_HOURS: z.coerce.number().nonnegative().default(24),
  FOLLOWUP_JITTER_MINUTES: z.coerce.number().nonnegative().default(90),

  // La ficha al AM es contacto interno: sale casi sin demora.
  DELAY_AM_MIN_MS: z.coerce.number().nonnegative().default(1000),
  DELAY_AM_MAX_MS: z.coerce.number().nonnegative().default(3000),

  DELAY_WELCOME_MIN_MS: z.coerce.number().nonnegative().default(8000),
  DELAY_WELCOME_MAX_MS: z.coerce.number().nonnegative().default(25000),
  DELAY_BETWEEN_MIN_MS: z.coerce.number().nonnegative().default(12000),
  DELAY_BETWEEN_MAX_MS: z.coerce.number().nonnegative().default(45000),

  // "escribiendo..." antes de cada mensaje. Se apaga solo en tests: sacarlo en
  // produccion es justamente lo que hace que el envio parezca de bot.
  TYPING_ENABLED: booleanoDeEnv.default(true),
});

function cargar(env = process.env) {
  const r = esquema.safeParse(env);
  if (!r.success) {
    const detalle = r.error.issues.map((i) => `  ${i.path.join('.')}: ${i.message}`).join('\n');
    throw new Error(`Configuracion invalida:\n${detalle}`);
  }
  const cfg = r.data;

  if (cfg.DELAY_WELCOME_MIN_MS > cfg.DELAY_WELCOME_MAX_MS) {
    throw new Error('DELAY_WELCOME_MIN_MS no puede ser mayor que DELAY_WELCOME_MAX_MS');
  }
  if (cfg.DELAY_BETWEEN_MIN_MS > cfg.DELAY_BETWEEN_MAX_MS) {
    throw new Error('DELAY_BETWEEN_MIN_MS no puede ser mayor que DELAY_BETWEEN_MAX_MS');
  }

  return Object.freeze({
    ...cfg,
    amPhones: cfg.AM_PHONES.split(',').map((p) => p.trim()).filter(Boolean),
  });
}

module.exports = { cargar, esquema };
