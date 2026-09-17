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
    // Con esto prendido el bot agenda el mismo y NO hay ningun link: el prompt
    // tiene que saberlo o promete uno que nunca llega.
    agendaPropia: cfg.AGENDA_OFRECE_HORARIOS,
    logger: log,
  });
  const redactor = require('./ia/redactor').crearRedactor({
    modelo: cfg.IA_CONVERSACION ? modeloIA : null,
    calendly: cfg.CALENDLY_LINK,
    logger: log,
  });
  // Los archivos que manda el lead viven al lado de la base, en el volumen. Es
  // lo mismo que hace el bot de la bloquera con su carpeta de uploads: una nota
  // de voz pesa unos 20KB y el volumen tiene 1GB.
  const { crearMedia, extensionDe } = require('./media');
  const media = crearMedia({
    dir: require('path').join(require('path').dirname(cfg.DB_PATH), 'media'),
    diasRetencion: cfg.MEDIA_DIAS_RETENCION,
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

  /**
   * Se le pregunta a Google apenas arranca, para no enterarse por un lead.
   *
   * El token de Google es lo unico que sostiene todo el camino de la agenda, y
   * cuando se cae no rompe nada visible: el embudo vuelve al link de Calendly y
   * sigue funcionando. Sin este chequeo, la primera señal seria alguien mirando
   * los logs o notando que hace dias que el bot no agenda solo.
   */
  if (agenda.activo) {
    agenda.horariosDisponibles(ahora())
      .then((r) => {
        if (r?.slots?.length) log.info({ proximo: r.slots[0].toISOString() }, 'agenda de Google responde');
        else log.error('la agenda de Google NO responde o no tiene ningun hueco: revisar GCAL_REFRESH_TOKEN');
      })
      .catch((e) => log.error({ err: String(e.message || e) }, 'la agenda de Google no responde'));
  }


  // Se entera de quien agendo en Calendly leyendo el calendario. Necesita
  // servicioLeads, que se arma abajo, asi que se cablea despues.
  const { crearVigilanteDeReservas } = require('./agenda/reservas');

  const embudo = crearEmbudo({
    repo, cola, textos, scorer, logger: log, cfg, crmNotify, agente, redactor, agenda, ahora,
    // El scheduler se arma abajo —tiene al embudo como dependencia— asi que se
    // resuelve cuando se llama y no ahora.
    recordatorios: (lead) => scheduler.programarRecordatorios(lead, ahora()),
  });

  const scheduler = crearScheduler({ repo, cola, cfg, redactor, embudo, limites, logger: log, ahora });
  const servicioLeads = crearServicioLeads({
    repo, cola, cfg, logger: log, textos, redactor, embudo, scheduler, crmNotify, ahora,
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
    procesar: (from, texto, nombre, medios) => servicioLeads.registrarRespuesta(from, texto, nombre, medios),
    esperaMs: cfg.AGRUPAR_ENTRANTES_MS,
    logger: log,
  });
  /**
   * Baja el archivo que vino con un mensaje y lo deja en el volumen.
   *
   * Siempre devuelve un descriptor, aunque no haya archivo: `archivo: null`
   * significa "mando esto pero no lo tenemos". Es lo que hace que en el panel
   * se lea "mandó un video" en vez de no aparecer nada, que era el sintoma
   * viejo. Que la descarga falle no puede costar el mensaje entero.
   */
  async function bajarMedio({ tipo, descargar, nombreArchivo = '', segundos = 0 }) {
    const medio = { tipo, archivo: null };
    if (nombreArchivo) medio.nombre = nombreArchivo;
    if (segundos) medio.segundos = segundos;
    if (!descargar) return { medio, buffer: null };

    let buffer = null;
    try {
      buffer = await descargar();
    } catch (e) {
      log.warn({ tipo, err: String(e.message || e) }, 'no se pudo bajar el medio entrante');
      return { medio, buffer: null };
    }

    // El volumen es de 1GB y ahi vive tambien la base: un video de WhatsApp
    // puede pesar 16MB. Pasado el tope se registra el mensaje sin el archivo.
    if (buffer.length > cfg.MEDIA_MAX_MB * 1024 * 1024) {
      log.warn({ tipo, mb: (buffer.length / 1024 / 1024).toFixed(1) }, 'medio demasiado grande, no se guarda');
      return { medio, buffer };
    }

    try {
      medio.archivo = media.guardar(buffer, extensionDe(tipo, nombreArchivo));
    } catch (e) {
      // Que no se pueda guardar el archivo no puede costar el mensaje.
      log.warn({ tipo, err: String(e.message || e) }, 'no se pudo guardar el medio entrante');
    }
    return { medio, buffer };
  }

  proveedor.alRecibir(async (m) => {
    // WhatsApp reenvia lo no confirmado cuando el bot reconecta. Sin este
    // filtro, una caida a mitad de turno hace que el lead reciba dos respuestas
    // al mismo mensaje, y desordenadas.
    if (!repo.entranteEsNuevo(m.id)) {
      log.debug({ from: m.from, id: m.id }, 'entrante repetido, se descarta');
      return;
    }
    // Una foto CON epigrafe es un solo mensaje de WhatsApp: el texto y la
    // imagen juntos. Antes salia por aca con el texto solo y la foto se perdia
    // en silencio, asi que el bot contestaba "mira como quedo esto" sin saber
    // que habia un esto.
    const medio = m.tipo ? (await bajarMedio(m)).medio : null;
    agrupador.recibir({ ...m, media: medio });
  });

  // Audios, fotos y archivos. No se puede leer el contenido, pero contestar
  // algo es mejor que el silencio. Se avisa una vez cada tanto y no en cada
  // mensaje: quien manda cuatro audios seguidos no necesita cuatro disculpas.
  const avisadoSinTexto = new Map();
  /**
   * Que fue lo que llego, para que el redactor pueda nombrarlo. El prompt de
   * `sin_texto_archivo` habla de "un archivo o una imagen": suficiente para un
   * PDF, raro para un sticker.
   */
  const QUE_MANDO = {
    imagen: 'Lo que te mandó fue una foto.',
    sticker: 'Lo que te mandó fue un sticker.',
    video: 'Lo que te mandó fue un video.',
    documento: 'Lo que te mandó fue un archivo.',
    ubicacion: 'Lo que te mandó fue una ubicación.',
    contacto: 'Lo que te mandó fue un contacto.',
  };
  /**
   * Un mensaje que sale de nuestro numero pero que no mando el bot: sos vos
   * escribiendole al lead desde el telefono. El bot se calla en ese chat unas
   * horas y despues vuelve solo.
   *
   * WhatsApp entrega tambien el eco de lo que mandamos nosotros, con la misma
   * marca de "propio". Ese hay que reconocerlo y dejarlo pasar: si no, el bot
   * se callaria a si mismo cada vez que contesta. Se reconoce por el id, que ya
   * quedo guardado al enviarlo.
   */
  proveedor.alSalienteManual?.(async ({ to, texto, id }) => {
    if (id && repo.cuerpoPorProviderId(id) !== null) return;
    try {
      servicioLeads.registrarSalienteManual({ telefono: to, texto, id });
    } catch (e) {
      log.warn({ to, err: String(e.message || e) }, 'no se pudo registrar el saliente manual');
    }
  });

  proveedor.alRecibirSinTexto?.(async ({ from, tipo, nombre, nombreArchivo, segundos, descargar, id }) => {
    if (!repo.entranteEsNuevo(id)) return;
    const lead = repo.leadPorTelefono(from);
    // Al que se dio de baja no se le escribe ni se le guarda nada: pidio irse.
    if (lead?.opt_out) return;
    // Con una persona atendiendo, o con el bot apagado o pausado, el bot se
    // calla —un sticker no lo despierta; antes contestaba "no me abre el
    // archivo" en un chat donde alguien acababa de entrar a escribir— pero el
    // archivo se guarda igual: el que esta atendiendo tiene que poder verlo.
    const callado = !!lead?.human_requested
      || (lead && !require('./funnel/pausa').botActivo(lead, ahora()));

    // Se baja una sola vez, sirva para transcribir o solo para el panel.
    const { medio, buffer } = await bajarMedio({ tipo, descargar, nombreArchivo, segundos });

    // Una nota de voz se transcribe y sigue el mismo camino que si la hubieran
    // escrito. Es lo que mas cambia en Uruguay, donde media conversacion de
    // WhatsApp son audios. El .ogg se guarda ademas de transcribirse: cuando la
    // transcripcion sale mal —pasa, con audio corto y acento rioplatense— poder
    // escuchar el original es la diferencia entre entender al lead y no.
    if (!callado && tipo === 'audio' && transcriptor.activo && buffer) {
      try {
        const texto = await transcriptor.transcribir(buffer, segundos);
        if (texto) {
          log.info({ from, segundos, largo: texto.length }, 'audio transcripto');
          agrupador.recibir({ from, texto, nombre, media: medio });
          return;
        }
      } catch (e) {
        log.warn({ from, err: String(e.message || e) }, 'no se pudo transcribir, se le pide que escriba');
      }
    }

    // No se pudo leer, pero llego: el mensaje queda en la conversacion igual.
    // Sin esto no existia en ningun lado y en el panel del CRM quedaba un hueco
    // — el lead mandaba la foto del local, veia el tilde azul, y del otro lado
    // no habia nada que mirar.
    try {
      servicioLeads.registrarEntranteSinTexto(from, {
        tipo, medios: [medio], nombreWa: nombre,
      });
    } catch (e) {
      log.warn({ from, tipo, err: String(e.message || e) }, 'no se pudo registrar el entrante sin texto');
    }

    // Y si el bot no tiene que hablar, ahi termina: el archivo ya quedo.
    if (callado) return;

    // Se avisa una vez cada tanto y no en cada mensaje, que quien manda cuatro
    // audios seguidos no necesita cuatro disculpas.
    const ultimo = avisadoSinTexto.get(from) || 0;
    if (Date.now() - ultimo < cfg.AVISO_SIN_TEXTO_MINUTOS * 60_000) return;
    avisadoSinTexto.set(from, Date.now());

    // Que el redactor sepa QUE llego. "No te entendi el archivo" cuando lo que
    // mandaste fue un sticker suena a error de sistema y encima a otra
    // conversacion.
    const situacion = tipo === 'audio' ? 'sin_texto_audio' : 'sin_texto_archivo';
    const texto = await redactor.escribir(lead || { telefono: from }, situacion, QUE_MANDO[tipo] || '');
    if (!texto) {
      log.warn({ from, tipo }, 'no se pudo redactar el aviso de entrante sin texto');
      return;
    }
    cola.encolar({
      to: from, texto, kind: 'manual', leadId: lead?.id ?? null,
      // Solo sirve en el momento. Si cae fuera del horario de envio y habria
      // que mandarlo a la manana siguiente, mejor no mandarlo: "no te entendi
      // el archivo" trece horas despues es ruido, el lead no tiene forma de
      // saber a que se refiere. Paso el 2-9 con un sticker de las 22:36.
      venceEnMin: cfg.AVISO_SIN_TEXTO_VENCE_MIN,
      encoladoEn: ahora(),
    });
    log.info({ from, tipo }, 'entrante sin texto: se le pide que escriba');
  });
  // Los audios viejos se borran solos. La transcripcion queda para siempre; el
  // archivo no: guardar voz de gente sin necesidad no aporta nada y el volumen
  // no es infinito. Una vez al arrancar y despues una vez por dia.
  media.limpiar();
  const limpiezaMedia = setInterval(() => media.limpiar(), 24 * 3600_000);
  limpiezaMedia.unref?.();

  const app = crearServidor({ cfg, repo, cola, proveedor, servicioLeads, scheduler, embudo, media, logger: log });

  const vigilanteReservas = crearVigilanteDeReservas({
    agenda, repo, servicioLeads, cfg, logger: log, ahora,
  });

  return {
    cfg, db, repo, proveedor, cola, limites, servicioLeads, media,
    scheduler, embudo, scorer, agrupador, app, vigilanteReservas, logger: log,
    ia: { conversacion: agente.activo, transcripcion: transcriptor.activo },
  };
}

module.exports = { construir };
