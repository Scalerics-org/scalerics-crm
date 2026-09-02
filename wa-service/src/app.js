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
const { crearNotificadorCRM } = require('./crm-notify');
const { crearAgrupador } = require('./inbound/agrupador');

/**
 * Arma el servicio entero y devuelve las piezas.
 * Los tests lo llaman con una config a medida y :memory: como base.
 */
function construir(cfg, {
  logger, ahora = () => new Date(), openai: clienteIA = null,
  modelo: clienteModelo = null, google = null,
} = {}) {
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

  /**
   * Dos proveedores, cada uno en lo suyo: Anthropic conversa, OpenAI
   * transcribe. Anthropic no hace audio, y Whisper es lo unico que se le pide
   * a OpenAI. Falta una clave y lo otro sigue andando.
   *
   * Sin ANTHROPIC_API_KEY el bot no conversa y el embudo deriva a una persona.
   * Sin OPENAI_API_KEY no se transcriben audios y se le pide al lead que
   * escriba. Ninguna de las dos rompe el arranque.
   */
  let openai = clienteIA;
  if (!openai && cfg.OPENAI_API_KEY) {
    const OpenAI = require('openai');
    openai = new OpenAI({ apiKey: cfg.OPENAI_API_KEY });
  }

  // `deps.modelo` entra ya armado —es lo que inyectan los tests— y solo si no
  // viene se construye uno contra la API de verdad. Los tests hablan con la
  // capa neutral y no con el SDK de ningun proveedor: esa es toda la diferencia
  // entre cambiar de proveedor tocando un archivo o tocando la suite entera.
  const { crearModelo, clienteAnthropic } = require('./ia/modelo');
  const modeloIA =
    clienteModelo ||
    crearModelo({
      cliente: clienteAnthropic(cfg.ANTHROPIC_API_KEY),
      modelo: cfg.IA_MODELO,
      logger: log,
    });
  const scorer = crearScorer({ modelo: cfg.IA_CONVERSACION ? modeloIA : null, logger: log });
  const textos = crearTextos({ horarioAtencion: cfg.HORARIO_ATENCION });
  const crmNotify = crearNotificadorCRM({ cfg, repo, logger: log });

  // Sin clave el agente queda inactivo y el embudo de preguntas fijas atiende
  // igual. Es a proposito: el dia que se cargue la clave se enciende solo.
  const agente = require('./ia/agente').crearAgente({
    modelo: cfg.IA_CONVERSACION ? modeloIA : null,
    textos,
    calendly: cfg.CALENDLY_LINK,
    logger: log,
  });
  const redactor = require('./ia/redactor').crearRedactor({
    modelo: cfg.IA_CONVERSACION ? modeloIA : null,
    calendly: cfg.CALENDLY_LINK,
    logger: log,
  });
  const transcriptor = require('./ia/transcripcion').crearTranscriptor({
    openai: cfg.IA_TRANSCRIPCION ? openai : null,
    modelo: cfg.IA_MODELO_AUDIO,
    maxSegundos: cfg.MAX_AUDIO_SEGUNDOS,
    logger: log,
  });
  log.info(
    {
      conversacion: agente.activo,
      redaccion: redactor.activo,
      transcripcion: transcriptor.activo,
      modelo: agente.activo ? cfg.IA_MODELO : null,
    },
    'capa de IA'
  );

  // Agenda contra Google Calendar. Sin credenciales queda inactiva y el cierre
  // vuelve al camino del link, que es el que existia antes.
  const agenda = require('./agenda/gcal').crearAgenda({ cfg, logger: log, ...(google ? { fetch: google } : {}) });
  log.info({ agenda: agenda.activo }, 'agenda');

  // Se entera de quien agendo en Calendly leyendo el calendario. Necesita
  // servicioLeads, que se arma abajo, asi que se cablea despues.
  const { crearVigilanteDeReservas } = require('./agenda/reservas');

  const embudo = crearEmbudo({
    repo, cola, textos, scorer, logger: log, cfg, crmNotify, agente, redactor, agenda, ahora,
  });

  const scheduler = crearScheduler({ repo, cola, cfg, redactor, embudo, logger: log, ahora });
  const servicioLeads = crearServicioLeads({
    repo, cola, cfg, logger: log, textos, redactor, embudo, scheduler, ahora,
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

  // Si la conexion no vuelve despues de varios intentos, el canal esta mudo y
  // alguien tiene que enterarse. Solo se avisa una vez por caida.
  let avisadoCaida = false;
  proveedor.alDesconectarse?.((intentos, esperaMs) => {
    if (intentos < 3 || avisadoCaida) return;
    avisadoCaida = true;
    for (const am of cfg.amPhones) {
      cola.encolar({
        to: am,
        texto: [
          '⚠️ El bot de WhatsApp perdió la conexión y no logra reconectar.',
          `Lleva ${intentos} intentos; sigue reintentando cada ${Math.round(esperaMs / 60000)} min.`,
          'Mientras tanto hay que contestar a mano.',
        ].join('\n'),
        kind: 'am_notice',
      });
    }
  });

  // Los entrantes no van directo al embudo: pasan por el agrupador, que junta
  // los fragmentos de una misma tanda y los atiende de a uno.
  const agrupador = crearAgrupador({
    procesar: (from, texto, nombre) => servicioLeads.registrarRespuesta(from, texto, nombre),
    esperaMs: cfg.AGRUPAR_ENTRANTES_MS,
    logger: log,
  });
  proveedor.alRecibir((m) => {
    // WhatsApp reenvia lo no confirmado cuando el bot reconecta. Sin este
    // filtro, una caida a mitad de turno hace que el lead reciba dos respuestas
    // al mismo mensaje, y desordenadas.
    if (!repo.entranteEsNuevo(m.id)) {
      log.debug({ from: m.from, id: m.id }, 'entrante repetido, se descarta');
      return;
    }
    agrupador.recibir(m);
  });

  // Audios, fotos y archivos. No se puede leer el contenido, pero contestar
  // algo es mejor que el silencio. Se avisa una vez cada tanto y no en cada
  // mensaje: quien manda cuatro audios seguidos no necesita cuatro disculpas.
  const avisadoSinTexto = new Map();
  proveedor.alRecibirSinTexto?.(async ({ from, tipo, nombre, segundos, descargar, id }) => {
    if (!repo.entranteEsNuevo(id)) return;
    const lead = repo.leadPorTelefono(from);
    // Al que se dio de baja o esta con una persona no se le escribe igual.
    if (lead?.opt_out || lead?.human_requested) return;

    // Una nota de voz se transcribe y sigue el mismo camino que si la hubieran
    // escrito. Es lo que mas cambia en Uruguay, donde media conversacion de
    // WhatsApp son audios.
    if (tipo === 'audio' && transcriptor.activo && descargar) {
      try {
        const texto = await transcriptor.transcribir(await descargar(), segundos);
        if (texto) {
          log.info({ from, segundos, largo: texto.length }, 'audio transcripto');
          agrupador.recibir({ from, texto, nombre });
          return;
        }
      } catch (e) {
        log.warn({ from, err: String(e.message || e) }, 'no se pudo transcribir, se le pide que escriba');
      }
    }

    // No se pudo leer: se avisa una vez cada tanto y no en cada mensaje, que
    // quien manda cuatro audios seguidos no necesita cuatro disculpas.
    const ultimo = avisadoSinTexto.get(from) || 0;
    if (Date.now() - ultimo < cfg.AVISO_SIN_TEXTO_MINUTOS * 60_000) return;
    avisadoSinTexto.set(from, Date.now());

    const situacion = tipo === 'audio' ? 'sin_texto_audio' : 'sin_texto_archivo';
    const texto = await redactor.escribir(lead || { telefono: from }, situacion);
    if (!texto) {
      log.warn({ from, tipo }, 'no se pudo redactar el aviso de entrante sin texto');
      return;
    }
    cola.encolar({ to: from, texto, kind: 'manual', leadId: lead?.id ?? null });
    log.info({ from, tipo }, 'entrante sin texto: se le pide que escriba');
  });
  const app = crearServidor({ cfg, repo, cola, proveedor, servicioLeads, scheduler, embudo, logger: log });

  const vigilanteReservas = crearVigilanteDeReservas({
    agenda, repo, servicioLeads, cfg, logger: log, ahora,
  });

  return {
    cfg, db, repo, proveedor, cola, limites, servicioLeads,
    scheduler, embudo, scorer, agrupador, app, vigilanteReservas, logger: log,
    ia: { conversacion: agente.activo, transcripcion: transcriptor.activo },
  };
}

module.exports = { construir };
