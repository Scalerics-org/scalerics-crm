'use strict';

const fs = require('node:fs');
const path = require('node:path');

/**
 * Proveedor Baileys: cliente NO OFICIAL que se vincula como dispositivo de
 * WhatsApp Web. Meta puede banear el numero sin aviso ni apelacion.
 *
 * Es el unico archivo del servicio que sabe que Baileys existe. Todo lo demas
 * habla con la interfaz de providers/types.js.
 *
 * Se importa de forma dinamica porque baileys es ESM y el resto del servicio es
 * CommonJS: con require() Node lo carga igual pero tira un warning experimental.
 */

/**
 * Backoff de reconexion. Se espera cada vez mas —insistir en loop contra
 * WhatsApp acelera el baneo— pero NO se deja de intentar: el ultimo valor se
 * repite indefinidamente.
 *
 * Antes se rendia despues del tercer intento. Una caida de red a las 3 de la
 * manana dejaba el bot mudo hasta que alguien lo notara a mano, que es
 * exactamente lo que paso.
 */
const BACKOFF_MS = [30_000, 5 * 60_000, 30 * 60_000];

function jidDeTelefono(telefono) {
  // Los grupos ya vienen con su sufijo; un telefono suelto va a @s.whatsapp.net.
  return String(telefono).includes('@') ? String(telefono) : `${telefono}@s.whatsapp.net`;
}

function telefonoDeJid(jid) {
  return String(jid || '').split('@')[0].split(':')[0];
}

/**
 * Telefono real del que manda un mensaje.
 *
 * WhatsApp migro a direccionamiento @lid: remoteJid puede traer un
 * identificador de dispositivo (227771510997245@lid) en vez del numero, que es
 * justamente lo que oculta. El numero real viaja aparte, en senderPn o
 * participantPn.
 *
 * @returns {string|null} E.164 sin '+', o null si es un LID sin numero asociado.
 */
function telefonoDelMensaje(key) {
  const pn = key?.senderPn || key?.participantPn;
  if (pn) return telefonoDeJid(pn);

  const jid = String(key?.remoteJid || '');
  // Sin senderPn no hay forma de saber a quien corresponde: atribuirselo a
  // alguien por el LID seria peor que descartarlo.
  if (jid.endsWith('@lid')) return null;

  const tel = telefonoDeJid(jid);
  return tel || null;
}

/** Saca el texto de un mensaje entrante, sea plano o con formato. */
function textoDeMensaje(msg) {
  const m = msg?.message;
  if (!m) return '';
  return (
    m.conversation ||
    m.extendedTextMessage?.text ||
    m.imageMessage?.caption ||
    m.videoMessage?.caption ||
    m.buttonsResponseMessage?.selectedDisplayText ||
    m.listResponseMessage?.title ||
    ''
  );
}

/**
 * @param {object} deps.buscarMensaje (providerMsgId) => texto|null. Lo usa
 *   Baileys para reenviar un mensaje que el destinatario no pudo descifrar.
 */
function crear(cfg, { logger, buscarMensaje = null } = {}) {
  let sock = null;
  let conectado = false;
  let qrActual = null;
  let telefonoPropio = null;
  let desdeCuando = null;
  let handler = null;
  let alActualizarEstado = null;
  let alPerderConexion = null;
  let intentos = 0;
  let cerrandoAProposito = false;
  let baileys = null;
  // Cuantas veces WhatsApp pidio reenviar cada mensaje. Dos pedidos del mismo
  // significan que la sesion con ese dispositivo no se recupera sola.
  const reintentosPorMensaje = new Map();

  async function cargarBaileys() {
    if (!baileys) baileys = await import('baileys');
    return baileys;
  }

  function limpiarAuth() {
    try {
      fs.rmSync(cfg.BAILEYS_AUTH_DIR, { recursive: true, force: true });
      fs.mkdirSync(cfg.BAILEYS_AUTH_DIR, { recursive: true });
    } catch (e) {
      logger?.error({ err: String(e.message || e) }, 'no se pudo limpiar el directorio de sesion');
    }
  }

  async function conectar() {
    const b = await cargarBaileys();
    const makeWASocket = b.default?.default ?? b.default ?? b.makeWASocket;
    const { useMultiFileAuthState, fetchLatestBaileysVersion, Browsers, DisconnectReason } = b;

    fs.mkdirSync(cfg.BAILEYS_AUTH_DIR, { recursive: true });
    const { state, saveCreds } = await useMultiFileAuthState(cfg.BAILEYS_AUTH_DIR);
    const { version } = await fetchLatestBaileysVersion();

    // [plataforma, nombre, version]. El del medio es lo que muestra el telefono
    // en "Dispositivos vinculados", y se manda al vincular: cambiarlo despues
    // no renombra una sesion ya activa, hay que desvincular y escanear de nuevo.
    const [plataforma, , versionSO] = Browsers.appropriate('Desktop');
    const browser = [plataforma, cfg.BAILEYS_DEVICE_NAME, versionSO];

    sock = makeWASocket({
      version,
      auth: state,
      browser,
      // El log de baileys es ruidosisimo en info; solo interesan los errores.
      logger: require('pino')({ level: 'error' }),
      markOnlineOnConnect: false,
      syncFullHistory: false,

      /**
       * Cuando un dispositivo del destinatario no puede descifrar un mensaje,
       * WhatsApp pide que se lo reenvien y baileys llama aca para recuperar el
       * contenido original y volver a cifrarlo para ese dispositivo.
       *
       * Sin esto no hay nada que reenviar y el mensaje queda en "Esperando este
       * mensaje" para siempre en el dispositivo que fallo — que es exactamente
       * lo que pasaba: llegaba a WhatsApp Web pero no al celular.
       */
      getMessage: async (key) => {
        const texto = buscarMensaje?.(key?.id);
        if (!texto) {
          logger?.warn({ id: key?.id }, 'reintento de descifrado: no se encontro el mensaje');
          return undefined;
        }

        // Si el mismo mensaje se pide dos veces, reenviarlo de nuevo no va a
        // servir: la sesion con ese dispositivo esta rota. Se borran sus claves
        // para que el proximo envio renegocie desde cero.
        const veces = (reintentosPorMensaje.get(key.id) || 0) + 1;
        reintentosPorMensaje.set(key.id, veces);
        if (veces >= 2) {
          const tel = telefonoDelMensaje(key) || telefonoDeJid(key.remoteJid);
          logger?.warn({ id: key.id, veces, tel }, 'reintentos repetidos: se reinicia el cifrado');
          reiniciarCifrado(tel);
          reintentosPorMensaje.delete(key.id);
        }

        logger?.info({ id: key?.id, veces }, 'reenviando mensaje por pedido de reintento');
        return { conversation: texto };
      },
    });

    logger?.info({ browser }, 'conectando a WhatsApp');
    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (u) => {
      const { connection, lastDisconnect, qr } = u;

      if (qr) {
        qrActual = qr;
        logger?.warn('hay un QR pendiente de escanear en GET /session/qr');
      }

      if (connection === 'open') {
        conectado = true;
        qrActual = null;
        intentos = 0;
        desdeCuando = new Date().toISOString();
        telefonoPropio = telefonoDeJid(sock.user?.id);
        logger?.info({ telefono: telefonoPropio }, 'WhatsApp conectado');
        return;
      }

      if (connection === 'close') {
        conectado = false;
        if (cerrandoAProposito) return;

        const codigo = lastDisconnect?.error?.output?.statusCode;

        // loggedOut es terminal: la sesion se cerro del otro lado. Reintentar
        // no sirve y re-vincular seguido es senial sospechosa; hay que escanear
        // un QR nuevo a mano.
        if (codigo === DisconnectReason.loggedOut) {
          logger?.error('sesion cerrada desde el telefono: hay que re-escanear el QR');
          limpiarAuth();
          return;
        }

        // Despues de vincular, WhatsApp pide reiniciar la conexion. Es normal.
        if (codigo === DisconnectReason.restartRequired) {
          logger?.info('reinicio pedido por WhatsApp, reconectando');
          conectar().catch((e) => logger?.error({ err: String(e.message || e) }, 'fallo al reconectar'));
          return;
        }

        // Se sigue reintentando siempre, con el ultimo intervalo como techo.
        const espera = BACKOFF_MS[Math.min(intentos, BACKOFF_MS.length - 1)];
        intentos += 1;
        const nivel = intentos > BACKOFF_MS.length ? 'error' : 'warn';
        logger?.[nivel](
          { codigo, intentos, esperaMs: espera },
          'conexion caida, se reintenta con backoff'
        );
        alPerderConexion?.(intentos, espera);
        setTimeout(() => {
          conectar().catch((e) => logger?.error({ err: String(e.message || e) }, 'fallo al reconectar'));
        }, espera).unref?.();
      }
    });

    /**
     * Acuses de recibo. Sin esto, "enviado" solo significa que Baileys acepto
     * el mensaje: si el destinatario no lo puede descifrar y le queda en
     * "Esperando este mensaje", nadie se entera.
     * 2 = entregado en el dispositivo, 3/4 = leido.
     */
    sock.ev.on('messages.update', (updates) => {
      if (!alActualizarEstado) return;
      for (const u of updates) {
        const s = u.update?.status;
        if (s === undefined || s === null) continue;
        const estado = s >= 3 ? 'read' : s === 2 ? 'delivered' : null;
        if (estado && u.key?.id) alActualizarEstado(u.key.id, estado);
      }
    });

    sock.ev.on('messages.upsert', ({ messages, type }) => {
      logger?.debug({ type, cantidad: messages?.length, hayHandler: Boolean(handler) }, 'upsert');

      if (type !== 'notify' || !handler) return;
      for (const msg of messages) {
        const jid = msg.key?.remoteJid;

        if (msg.key?.fromMe) {
          logger?.debug({ jid }, 'entrante ignorado: es propio');
          continue;
        }
        // Los grupos no son leads: el embudo es uno a uno.
        if (jid?.endsWith('@g.us')) {
          logger?.debug({ jid }, 'entrante ignorado: es de grupo');
          continue;
        }

        const texto = textoDeMensaje(msg);
        if (!texto) {
          logger?.debug(
            { jid, tipos: Object.keys(msg.message || {}) },
            'entrante ignorado: sin texto reconocible'
          );
          continue;
        }

        const from = telefonoDelMensaje(msg.key);
        if (!from) {
          logger?.warn({ jid }, 'entrante descartado: LID sin telefono asociado');
          continue;
        }

        logger?.info({ from }, 'mensaje entrante');
        // pushName es el nombre que la persona tiene puesto en WhatsApp. Para
        // quien escribe al numero sin pasar por el formulario, es lo unico que
        // hay para saludarlo por su nombre.
        handler({ from, texto, id: msg.key.id, nombre: msg.pushName || '' });
      }
    });
  }

  /**
   * Borra las sesiones de Signal guardadas para un destinatario, en todos sus
   * dispositivos. El proximo mensaje renegocia las claves desde cero.
   *
   * Hace falta porque WhatsApp cifra por dispositivo: si la sesion con uno solo
   * se corrompe, ese aparato ve "Esperando este mensaje" para siempre mientras
   * los demas leen bien. Reenviar no alcanza — se recifra con la misma sesion
   * rota. Ademas, desde la migracion a @lid, un mismo contacto puede tener
   * sesiones bajo dos identidades y hay que limpiar las dos.
   *
   * @returns {string[]} las sesiones borradas.
   */
  function reiniciarCifrado(telefono) {
    const dir = cfg.BAILEYS_AUTH_DIR;
    const prefijo = `session-${telefono}.`;
    let borradas = [];
    try {
      borradas = fs.readdirSync(dir).filter((f) => f.startsWith(prefijo));
      for (const f of borradas) fs.rmSync(path.join(dir, f), { force: true });
    } catch (e) {
      logger?.error({ err: String(e.message || e) }, 'no se pudieron borrar las sesiones');
      return [];
    }
    logger?.warn({ telefono, borradas: borradas.length }, 'sesiones de cifrado reiniciadas');
    return borradas.map((f) => f.replace(/^session-|\.json$/g, ''));
  }

  return {
    nombre: 'baileys',
    reiniciarCifrado,
    // Baileys manda texto libre y soporta grupos; es justamente lo que lo hace
    // atractivo y lo que lo pone del lado no oficial.
    capacidades: { typingIndicator: true, textoLibre: true, grupos: true },

    conectar,

    async desconectar() {
      cerrandoAProposito = true;
      try {
        await sock?.end();
      } finally {
        conectado = false;
        sock = null;
        cerrandoAProposito = false;
      }
    },

    estado() {
      return {
        conectado,
        qr: qrActual ? true : undefined,
        telefono: telefonoPropio || cfg.BAILEYS_PHONE || undefined,
        desde: desdeCuando || undefined,
      };
    },

    /** El QR crudo, para renderizarlo. null si ya esta vinculado. */
    qrCrudo: () => qrActual,

    /** Cierra sesion del lado de WhatsApp y borra las credenciales locales. */
    async cerrarSesion() {
      cerrandoAProposito = true;
      try {
        await sock?.logout();
      } catch (e) {
        logger?.warn({ err: String(e.message || e) }, 'logout fallo, se limpia igual');
      } finally {
        conectado = false;
        sock = null;
        cerrandoAProposito = false;
        limpiarAuth();
      }
    },

    async enviarTexto(to, texto) {
      if (!sock || !conectado) throw new Error('WhatsApp no esta conectado');
      const r = await sock.sendMessage(jidDeTelefono(to), { text: texto });
      return { id: r?.key?.id || '' };
    },

    async setPresencia(to, estadoPresencia) {
      if (!sock || !conectado) return;
      try {
        await sock.sendPresenceUpdate(estadoPresencia, jidDeTelefono(to));
      } catch (e) {
        // La presencia es cosmetica: que falle no puede frenar el envio.
        logger?.debug({ err: String(e.message || e) }, 'no se pudo actualizar la presencia');
      }
    },

    alRecibir(fn) {
      handler = fn;
    },

    /** Se llama con (idDelProveedor, "delivered"|"read") al llegar el acuse. */
    alCambiarEstado(fn) {
      alActualizarEstado = fn;
    },

    /** Se llama con (intentos, esperaMs) cada vez que se cae la conexion. */
    alDesconectarse(fn) {
      alPerderConexion = fn;
    },
  };
}

module.exports = { crear, jidDeTelefono, telefonoDeJid, telefonoDelMensaje, textoDeMensaje, BACKOFF_MS };
