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

  /**
   * En que interfaz escucha. Por defecto solo localhost.
   *
   * Estaba en 0.0.0.0, que en una laptop no cambia nada pero en un VPS deja el
   * puerto abierto a internet: cualquiera puede pegarle al /health, ver si hay
   * algo y probar claves contra /leads. Si el CRM corre en la misma maquina,
   * localhost alcanza y no hace falta abrir nada. Si corre afuera, va un nginx
   * con TLS adelante y esto sigue en localhost.
   */
  HOST: z.string().default('127.0.0.1'),
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

  // Embudo de calificacion. Sin OPENAI_API_KEY el scoring cae a reglas.
  OPENAI_API_KEY: z.string().default(''),
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
   * embudo de preguntas fijas. Requiere OPENAI_API_KEY: sin clave se ignora y
   * sigue el embudo, no rompe nada.
   *
   * gpt-4o-mini alcanza para esto y sale una fraccion de los modelos grandes.
   * Si las respuestas quedan cortas de calidad, se cambia el modelo aca.
   */
  IA_CONVERSACION: booleanoDeEnv.default(true),
  IA_MODELO: z.string().default('gpt-4o-mini'),

  /**
   * Notas de voz a texto. Las de WhatsApp vienen en OGG/Opus, que la API acepta
   * tal cual: no hace falta convertir nada.
   *
   * El corte por duracion es por costo y por sentido — nadie describe su
   * negocio en diez minutos, y si lo hace conviene que lo escuche una persona.
   */
  IA_TRANSCRIPCION: booleanoDeEnv.default(true),
  IA_MODELO_AUDIO: z.string().default('whisper-1'),
  MAX_AUDIO_SEGUNDOS: z.coerce.number().positive().default(300),

  // Cada cuanto, como mucho, se le pide a alguien que escriba en vez de mandar
  // audios. Quien manda cuatro seguidos no necesita cuatro disculpas.
  AVISO_SIN_TEXTO_MINUTOS: z.coerce.number().nonnegative().default(30),

  /**
   * Cuanto se espera antes de contestar, por si siguen escribiendo. La gente
   * manda "Necesito un" / "ecommerce" / "a medida" en tres mensajes seguidos;
   * sin esta espera el bot contesta tres veces y desordenado.
   *
   * Es corto al lado de los delays de la cola de salida, asi que no se nota.
   */
  AGRUPAR_ENTRANTES_MS: z.coerce.number().nonnegative().default(7000),

  /**
   * Lo que se le dice al lead cuando queda esperando a una persona. No se
   * deriva de BUSINESS_HOURS: ese rango es cuando el bot tiene permitido
   * mandar, y hoy esta abierto de par en par para probar. Este es cuando hay
   * alguien del otro lado, que es otra cosa.
   */
  HORARIO_ATENCION: z.string().default('Lun a sáb, 9 a 19hs'),

  /**
   * Agenda contra Google Calendar. El bot muestra horarios reales y reserva
   * ahi mismo, en vez de mandar un link.
   *
   * Es Google y no Calendly porque la API de Calendly no deja reservar en
   * nombre de otro: se pueden listar horarios, pero la reserva la completa el
   * lead en la pagina de ellos. Con eso el flujo termina igual en un link, que
   * es justo lo que se queria evitar.
   */
  GCAL_CLIENT_ID: z.string().default(''),
  GCAL_CLIENT_SECRET: z.string().default(''),
  GCAL_REFRESH_TOKEN: z.string().default(''),
  GCAL_CALENDAR_ID: z.string().default('primary'),

  // La franja que se ofrece. Son horas de reunion, no de atencion: el bot
  // contesta todo el dia, pero solo agenda aca.
  AGENDA_DESDE: z.string().default('12:00'),
  AGENDA_HASTA: z.string().default('16:00'),
  AGENDA_PASO_MIN: z.coerce.number().int().positive().default(30),
  AGENDA_DURACION_MIN: z.coerce.number().int().positive().default(30),
  AGENDA_DIAS: z.string().default('mon,tue,wed,thu,fri'),
  // Cuantos horarios se muestran. Mas de cinco deja de ser una eleccion y pasa
  // a ser una lista que hay que leer.
  AGENDA_MAX_OPCIONES: z.coerce.number().int().positive().default(5),
  AGENDA_DIAS_ADELANTE: z.coerce.number().int().positive().default(10),
  // No se ofrece nada antes de este plazo: una reunion en veinte minutos no le
  // sirve a nadie y suena a que no hay nadie del otro lado.
  AGENDA_AVISO_MIN_HORAS: z.coerce.number().nonnegative().default(3),

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
