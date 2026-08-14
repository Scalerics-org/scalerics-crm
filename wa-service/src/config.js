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
  // Nombre que aparece en "Dispositivos vinculados" del telefono.
  // Se manda al vincular: cambiarlo despues no renombra una sesion ya activa.
  BAILEYS_DEVICE_NAME: z.string().default('Chrome'),

  // Coma-separado. Puede ser un JID de grupo (...@g.us) cuando el proveedor lo soporte.
  AM_PHONES: z.string().default(''),
  DEFAULT_COUNTRY_CODE: z.string().default('598'),
  TZ: z.string().default('America/Montevideo'),

  // Embudo de calificacion. Sin ANTHROPIC_API_KEY el scoring cae a reglas.
  ANTHROPIC_API_KEY: z.string().default(''),
  CALENDLY_LINK: z.string().default('https://calendly.com/scalerics/diagnostico'),
  FUNNEL_ENABLED: booleanoDeEnv.default(true),

  // 72h segun el superprompt. Solo sale si el lead NO agendo en Calendly.
  FOLLOWUP_DELAY_HOURS: z.coerce.number().nonnegative().default(72),
  FOLLOWUP_JITTER_MINUTES: z.coerce.number().nonnegative().default(90),

  // Recordatorios de una reunion agendada. El del dia antes solo se programa si
  // al agendar falta mas de un dia.
  REMINDER_DAY_BEFORE_HOURS: z.coerce.number().nonnegative().default(24),
  REMINDER_MINUTES_BEFORE: z.coerce.number().nonnegative().default(30),

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

  // ── anti-baneo ──────────────────────────────────────────────────────────
  MAX_MSGS_PER_HOUR: z.coerce.number().int().positive().default(30),
  // El que mas pesa: primer mensaje a un numero que nunca escribio.
  MAX_NEW_CONTACTS_PER_HOUR: z.coerce.number().int().positive().default(12),
  MAX_MSGS_PER_DAY: z.coerce.number().int().positive().default(200),
  // Fecha de alta del numero (YYYY-MM-DD). Vacio = sin rampa de warm-up.
  WARMUP_START_DATE: z.string().default(''),

  BUSINESS_HOURS: z.string().default('09:00-19:00'),
  BUSINESS_DAYS: z.string().default('mon-sat'),

  /**
   * Con IA_CONVERSACION la parte de averiguar la conduce el modelo en vez del
   * embudo de preguntas fijas. Requiere ANTHROPIC_API_KEY: sin clave se ignora
   * y sigue el embudo, no rompe nada.
   *
   * Haiku alcanza para esto y sale una fraccion de Sonnet. Si las respuestas
   * quedan cortas de calidad, se cambia por claude-sonnet-5 aca y listo.
   */
  IA_CONVERSACION: booleanoDeEnv.default(true),
  IA_MODELO: z.string().default('claude-haiku-4-5-20251001'),

  // Cada cuanto, como mucho, se le pide a alguien que escriba en vez de mandar
  // audios. Quien manda cuatro seguidos no necesita cuatro disculpas.
  AVISO_SIN_TEXTO_MINUTOS: z.coerce.number().nonnegative().default(30),

  /**
   * Lo que se le dice al lead cuando queda esperando a una persona. No se
   * deriva de BUSINESS_HOURS: ese rango es cuando el bot tiene permitido
   * mandar, y hoy esta abierto de par en par para probar. Este es cuando hay
   * alguien del otro lado, que es otra cosa.
   */
  HORARIO_ATENCION: z.string().default('Lun a sáb, 9 a 19hs'),

  CIRCUIT_BREAKER_FAILS: z.coerce.number().int().positive().default(3),
  CIRCUIT_BREAKER_WINDOW_MIN: z.coerce.number().int().positive().default(10),
  CIRCUIT_BREAKER_PAUSE_MIN: z.coerce.number().int().positive().default(30),
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
