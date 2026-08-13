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

/** Backoff de reconexion. Insistir en loop contra WhatsApp acelera el baneo. */
const BACKOFF_MS = [30_000, 5 * 60_000, 30 * 60_000];

function jidDeTelefono(telefono) {
  // Los grupos ya vienen con su sufijo; un telefono suelto va a @s.whatsapp.net.
  return String(telefono).includes('@') ? String(telefono) : `${telefono}@s.whatsapp.net`;
}

function telefonoDeJid(jid) {
  return String(jid || '').split('@')[0].split(':')[0];
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

function crear(cfg, { logger } = {}) {
  let sock = null;
  let conectado = false;
  let qrActual = null;
  let telefonoPropio = null;
  let desdeCuando = null;
  let handler = null;
  let intentos = 0;
  let cerrandoAProposito = false;
  let baileys = null;

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

    sock = makeWASocket({
      version,
      auth: state,
      // Un navegador de escritorio comun: es lo que espera ver el servidor.
      browser: Browsers.appropriate('Desktop'),
      // El log de baileys es ruidosisimo en info; solo interesan los errores.
      logger: require('pino')({ level: 'error' }),
      markOnlineOnConnect: false,
      syncFullHistory: false,
    });

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

        if (intentos >= BACKOFF_MS.length) {
          logger?.error({ intentos }, 'se agotaron los reintentos de conexion, no se insiste mas');
          return;
        }

        const espera = BACKOFF_MS[intentos];
        intentos += 1;
        logger?.warn({ codigo, intentos, esperaMs: espera }, 'conexion caida, se reintenta con backoff');
        setTimeout(() => {
          conectar().catch((e) => logger?.error({ err: String(e.message || e) }, 'fallo al reconectar'));
        }, espera).unref?.();
      }
    });

    sock.ev.on('messages.upsert', ({ messages, type }) => {
      if (type !== 'notify' || !handler) return;
      for (const msg of messages) {
        if (msg.key?.fromMe) continue;
        // Los grupos no son leads: el embudo es uno a uno.
        if (msg.key?.remoteJid?.endsWith('@g.us')) continue;

        const texto = textoDeMensaje(msg);
        if (!texto) continue;

        handler({
          from: telefonoDeJid(msg.key.remoteJid),
          texto,
          id: msg.key.id,
        });
      }
    });
  }

  return {
    nombre: 'baileys',
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
  };
}

module.exports = { crear, jidDeTelefono, telefonoDeJid, textoDeMensaje, BACKOFF_MS };
