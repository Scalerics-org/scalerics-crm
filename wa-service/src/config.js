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

  /**
   * Numeros del equipo. Coma-separado.
   *
   * El equipo reserva en Calendly a nombre del cliente y a veces pone un
   * telefono propio en el formulario. El bot lee ese campo como "el telefono
   * del que agendo", asi que el recordatorio de la reunion le llega al del
   * equipo en vez de al cliente. Paso con una reunion de La Vaca Encantada: el
   * formulario tenia el numero de Juan y el recordatorio le llego a el.
   *
   * No es lo mismo que AM_PHONES, que es a quien se le avisa de los leads. Un
   * numero puede estar en las dos listas o en una sola.
   */
  EQUIPO_TELEFONOS: z.string().default(''),
  DEFAULT_COUNTRY_CODE: z.string().default('598'),
  TZ: z.string().default('America/Montevideo'),

  // Embudo de calificacion. Sin OPENAI_API_KEY el scoring cae a reglas.
  OPENAI_API_KEY: z.string().default(''),
  CALENDLY_LINK: z.string().default('https://calendly.com/scalerics/consultoriagratuita'),
  FUNNEL_ENABLED: booleanoDeEnv.default(true),

  // 72h segun el superprompt. Solo sale si el lead NO agendo en Calendly.
  FOLLOWUP_DELAY_HOURS: z.coerce.number().nonnegative().default(72),
  FOLLOWUP_JITTER_MINUTES: z.coerce.number().nonnegative().default(90),

  // Recordatorios de una reunion agendada. El del dia antes solo se programa si
  // al agendar falta mas de un dia.
  REMINDER_DAY_BEFORE_HOURS: z.coerce.number().nonnegative().default(24),
  REMINDER_MINUTES_BEFORE: z.coerce.number().nonnegative().default(30),

  /**
   * Las esperas de la cola de salida.
   *
   * Venian calibradas para mandar en frio a listas de gente que no habia
   * escrito: ahi la lentitud es proteccion. Hoy el bot SOLO contesta a quien
   * escribe primero, y ese caso es al reves —el que pregunta esta esperando— y
   * ademas es el que menos se parece a un bot spameando.
   *
   * Con los valores viejos, el primer mensaje de un lead tardaba de 30 a 90
   * segundos en tener respuesta: la ficha al equipo salia adelante y entre
   * mensaje y mensaje habia de 12 a 45 segundos. Nada de eso es la IA, que
   * tarda alrededor de un segundo y medio.
   *
   * Lo que de verdad frena un baneo son los topes por hora y por dia de mas
   * abajo, que se quedan como estan.
   */
  DELAY_AM_MIN_MS: z.coerce.number().nonnegative().default(0),
  DELAY_AM_MAX_MS: z.coerce.number().nonnegative().default(250),

  DELAY_WELCOME_MIN_MS: z.coerce.number().nonnegative().default(500),
  DELAY_WELCOME_MAX_MS: z.coerce.number().nonnegative().default(1500),
  DELAY_BETWEEN_MIN_MS: z.coerce.number().nonnegative().default(800),
  DELAY_BETWEEN_MAX_MS: z.coerce.number().nonnegative().default(2000),

  // "escribiendo..." antes de cada mensaje. Se apaga solo en tests: sacarlo en
  // produccion es justamente lo que hace que el envio parezca de bot. Es,
  // ademas, lo unico de todo esto que WhatsApp nombra como senial: le importa
  // que el indicador exista, no cuanto dure.
  TYPING_ENABLED: booleanoDeEnv.default(true),

  /**
   * Cuanto dura el "escribiendo...".
   *
   * El techo era de 6 segundos, pero el factor aleatorio se aplica DESPUES de
   * recortar, asi que el maximo real eran 7,8: casi ocho segundos de teatro en
   * cada respuesta. Con estos valores el indicador sigue apareciendo —que es lo
   * unico que WhatsApp mira— pero dura alrededor de un segundo.
   */
  TYPING_MS_POR_CARACTER: z.coerce.number().nonnegative().default(20),
  TYPING_TECHO_MS: z.coerce.number().nonnegative().default(900),
  TYPING_PAUSA_MIN_MS: z.coerce.number().nonnegative().default(120),
  TYPING_PAUSA_MAX_MS: z.coerce.number().nonnegative().default(350),

  /**
   * "escribiendo..." en los mensajes internos. Apagado: la ficha al equipo va
   * adelante de la respuesta al lead, y simular que alguien la tipea le suma
   * segundos a la espera del que si esta mirando el telefono.
   */
  TYPING_INTERNO: booleanoDeEnv.default(false),

  // ── anti-baneo ──────────────────────────────────────────────────────────
  /**
   * Los dos topes no miden lo mismo, y por eso no se mueven juntos.
   *
   * El total por hora son casi todos mensajes DENTRO de conversaciones que
   * abrio la otra persona: contestarle a alguien que te escribio es el trafico
   * de menor riesgo que existe. Treinta era demasiado poco —una conversacion
   * nueva son unos cinco mensajes al cliente, o sea seis conversaciones por
   * hora— y al pasarse, el lead siete espera quince minutos. Eso tira abajo
   * todo el trabajo de bajar la respuesta a cuatro segundos.
   *
   * El de contactos nuevos NO se toca. Ese si mide lo que a WhatsApp le
   * importa: el primer mensaje a un numero que nunca escribio. Es el unico de
   * los tres que vigila algo que se parece a spam, y doce por hora sigue siendo
   * mas de lo que se usa hoy, que es cero.
   */
  MAX_MSGS_PER_HOUR: z.coerce.number().int().positive().default(90),
  MAX_NEW_CONTACTS_PER_HOUR: z.coerce.number().int().positive().default(12),
  MAX_MSGS_PER_DAY: z.coerce.number().int().positive().default(400),
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
   * Segundo y medio alcanza porque la proteccion real ya no es la espera.
   * Antes esto tenia que durar mas que el tipeo de la persona, porque una tanda
   * partida en dos turnos significaba dos respuestas y la segunda escrita sin
   * ver la primera. Ahora, cuando arranca un turno nuevo se cancela la
   * respuesta del anterior aunque el worker ya la tenga en la mano, asi que una
   * tanda partida cuesta una llamada a la API de mas y nada mas.
   *
   * No baja a cero: sin ninguna espera, "hola" y "necesito una web" escritos
   * seguidos son dos llamadas al modelo en vez de una, y se paga por las dos.
   */
  AGRUPAR_ENTRANTES_MS: z.coerce.number().nonnegative().default(1500),

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
  /**
   * Si el bot ofrece horarios y agenda el, o manda el link de Calendly para
   * que el cliente se agende solo.
   *
   * Apagado: el cliente se agenda solo. Elegir entre cinco horarios es mas
   * facil que abrir una pagina, pero el formulario de Calendly pregunta cosas
   * —empresa, que necesita, telefono— que sirven para preparar la reunion, y
   * ademas manda la confirmacion y los avisos por su cuenta.
   *
   * Esto NO apaga la agenda: el bot sigue leyendo el calendario para enterarse
   * de quien agendo y mandarle los recordatorios. Solo deja de ofrecer y
   * reservar. Se prende de nuevo sin revertir nada.
   */
  AGENDA_OFRECE_HORARIOS: booleanoDeEnv.default(false),

  /**
   * Mirar el calendario cada tanto para enterarse de quien agendo en Calendly.
   *
   * Es la unica via: el bot no tiene IP publica, asi que el webhook de Calendly
   * no lo alcanza. El cruce con el lead se hace por el telefono que Calendly
   * pregunta en el formulario y escribe en el evento.
   */
  /**
   * Cuanto silencio del lead se toma como que se fue de la conversacion, en
   * minutos. Cero lo apaga.
   *
   * Una hora es corto para un follow-up y largo para una pausa: el que estaba
   * contestando y para una hora entera, se fue. No reemplaza al seguimiento de
   * las 72 horas, que es otra cosa —ese es para el que nunca contesto—.
   *
   * Solo se le arma a quien contesto al menos una vez: al que nunca dijo nada
   * no se lo puede derivar por irse de una conversacion que no tuvo.
   */
  ABANDONO_MINUTOS: z.coerce.number().nonnegative().default(60),

  /**
   * Cada cuanto, como mucho, se le recuerda al equipo que un lead ya derivado
   * sigue escribiendo. Cero apaga el aviso.
   *
   * Que el bot se calle con quien esta en manos de una persona es correcto: dos
   * voces contestando lo mismo es peor que una. Lo que no puede pasar es que
   * ademas nadie se entere. Un lead escribio seis dias despues de que lo
   * derivaran y del lado de adentro no quedo mas rastro que una linea de log.
   *
   * Con tope, porque el que manda cuatro mensajes seguidos no necesita cuatro
   * avisos.
   */
  AVISO_HUMANO_MINUTOS: z.coerce.number().nonnegative().default(60),

  /**
   * Copias de la base hechas por SQLite, aparte de los snapshots de Fly.
   *
   * Fly saca uno por dia y guarda cinco, lo que cubre que se muera el disco.
   * Esto cubre lo otro: mas historia que cinco dias, y un archivo consistente
   * —un snapshot del volumen copia la base abierta, con el journal a medio
   * escribir, y aca el journal llega a ser mas grande que la base—.
   *
   * Vacio en BACKUP_DIR lo apaga.
   */
  BACKUP_DIR: z.string().default('/data/backups'),
  BACKUP_HORAS: z.coerce.number().nonnegative().default(24),
  BACKUP_GUARDAR: z.coerce.number().int().nonnegative().default(14),

  /**
   * A donde se copian los respaldos afuera de Fly.
   *
   * Los snapshots del volumen y las copias en /data protegen de un disco roto
   * o de un bug, pero las tres cosas viven en la misma cuenta de Fly: si se
   * pierde el acceso, se pierde todo junto.
   *
   * R2 habla S3, asi que si algun dia se cambia de proveedor el codigo sirve
   * igual. Sin credenciales queda apagado y no rompe nada.
   */
  R2_ACCOUNT_ID: z.string().default(''),
  R2_BUCKET: z.string().default(''),
  R2_ACCESS_KEY_ID: z.string().default(''),
  R2_SECRET_ACCESS_KEY: z.string().default(''),
  R2_PREFIX: z.string().default('wa-service'),

  /**
   * Cuantas horas tiene que pasar para repetir un aviso interno IDENTICO, y
   * cuantos avisos internos como mucho por hora.
   *
   * Los avisos al equipo no pasan por los topes anti-baneo —son contacto
   * interno y no tiene sentido frenarlos— pero eso los dejaba sin techo de
   * ningun tipo. Un bucle en el vigilante de reservas mando 489 avisos en dos
   * dias: eran 5 textos repetidos 98 veces cada uno, y nada los freno.
   *
   * El corte que importa es el del texto identico: un aviso se repite palabra
   * por palabra solo cuando algo esta en bucle, porque lleva el nombre, la hora
   * y lo que dijo el lead. Eso solo habria dejado el incidente en 5 mensajes.
   *
   * El tope por hora es la red para un bucle que ademas varie el texto, y va
   * alto a proposito: cien avisos en una hora es una cifra que el trabajo
   * normal no alcanza ni en el mejor dia. Un techo que silencia avisos de
   * verdad es tan malo como no tener techo — el equipo se entera de los leads
   * por estos mensajes.
   */
  AVISO_REPETIDO_HORAS: z.coerce.number().nonnegative().default(24),
  MAX_INTERNOS_PER_HOUR: z.coerce.number().int().nonnegative().default(100),

  /**
   * Cuantos mensajes como mucho a la MISMA persona en un dia.
   *
   * Es el unico guardia que habria agarrado el incidente de los 489. Fue un
   * goteo —24 mensajes por hora durante dos dias— asi que el volumen total
   * nunca se vio raro; lo raro era que todos iban al mismo telefono.
   *
   * Ochenta en un dia a una sola persona no es una conversacion, es algo
   * trabado. Una charla real del embudo son cinco o seis mensajes, y el dia mas
   * cargado del equipo no llega ni a la mitad de este numero.
   */
  MAX_POR_DESTINATARIO_DIA: z.coerce.number().int().nonnegative().default(80),

  /**
   * El que dice "mas adelante": cuantos dias despues se le vuelve a escribir
   * cuando no dio una referencia clara. Cero apaga la pausa entera.
   *
   * Catorce y no siete: al que dijo "estoy viendo presupuestos", escribirle a
   * la semana se lee como no haberlo escuchado. El piso y el techo estan para
   * que ninguna interpretacion rara del modelo termine en "te escribo mañana"
   * ni en un job para dentro de dos años.
   */
  NURTURE_DEFAULT_DIAS: z.coerce.number().nonnegative().default(14),
  NURTURE_MIN_DIAS: z.coerce.number().positive().default(2),
  NURTURE_MAX_DIAS: z.coerce.number().positive().default(180),

  /**
   * En que motivos el BOT puede descalificar solo. Coma-separado, vacio lo
   * apaga entero.
   *
   * No es todo o nada porque los cuatro motivos no se parecen. Que alguien
   * mande un CV o se haya equivocado de numero no admite lectura: es lo que es.
   * En cambio "me esta ofreciendo algo" y sobre todo "pide algo que no
   * hacemos" son juicios, y ahi el modelo puede errarle — decidir que una app
   * movil no es lo nuestro, por ejemplo—.
   *
   * La asimetria manda: equivocarse poniendo a alguien en pausa cuesta unos
   * dias; equivocarse descalificando cuesta el cliente. Asi que el bot decide
   * en los casos claros y los dudosos quedan anotados para que los mire una
   * persona.
   */
  DESCALIFICACION_AUTOMATICA: z.string().default('trabajo,numero_equivocado'),

  RESERVAS_VIGILAR: booleanoDeEnv.default(true),
  RESERVAS_INTERVALO_MIN: z.coerce.number().int().positive().default(5),
  RESERVAS_DIAS_ADELANTE: z.coerce.number().int().positive().default(60),

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
    equipo: cfg.EQUIPO_TELEFONOS.split(',').map((p) => p.trim()).filter(Boolean),
    descalificaSolo: cfg.DESCALIFICACION_AUTOMATICA.split(',').map((m) => m.trim()).filter(Boolean),
  });
}

module.exports = { cargar, esquema };
