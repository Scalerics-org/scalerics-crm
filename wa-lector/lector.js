'use strict';

/**
 * Lector de WhatsApp de Juan (21/10). Corre en su notebook, vinculado como
 * dispositivo a su WhatsApp Business, y SOLO LEE: no manda mensajes, no marca
 * como leído y no aparece "en línea", así que el teléfono sigue avisando igual.
 *
 * No es el bot de ventas (wa-service): ese contesta solo a quien le escribe y
 * acá eso sería mandarle el embudo a cada cliente de Juan. Por eso es un
 * programa aparte que ni siquiera tiene cómo enviar.
 *
 * Guarda los chats uno a uno (sin grupos, estados ni canales) en la base de
 * base.js, menos los contactos de excluidos.txt.
 *
 *   node lector.js             se conecta y queda escuchando
 *   node lector.js --vincular  muestra el QR, vincula, baja el historial y termina
 *   node lector.js --parar     frena el lector que esté corriendo
 *
 * Baileys es un cliente NO oficial: Meta puede cerrar la sesión. Si pasa, hay
 * que volver a vincular con --vincular.
 */

const fs = require('node:fs');
const path = require('node:path');
const base = require('./base');

const BACKOFF_MS = [30_000, 5 * 60_000, 30 * 60_000];
const HISTORIAL_DIAS = 90;

const NO_LEIBLES = {
  audioMessage: '[audio]',
  videoMessage: '[video]',
  imageMessage: '[foto]',
  documentMessage: '[documento]',
  stickerMessage: '[sticker]',
  locationMessage: '[ubicación]',
  contactMessage: '[contacto]',
};

function telefonoDeJid(jid) {
  return String(jid || '').split('@')[0].split(':')[0];
}

function esUnoAUno(jid) {
  return /@(s\.whatsapp\.net|lid)$/.test(String(jid || ''));
}

/** Saca el texto, o una marca del tipo si es un audio, foto, etc. sin texto. */
function textoDeMensaje(msg) {
  let m = msg?.message;
  if (!m) return '';
  m = m.ephemeralMessage?.message || m.viewOnceMessage?.message || m.editedMessage?.message || m;
  const texto = m.conversation
    || m.extendedTextMessage?.text
    || m.imageMessage?.caption
    || m.videoMessage?.caption
    || m.documentMessage?.caption
    || '';
  if (texto) return texto;
  for (const [clave, marca] of Object.entries(NO_LEIBLES)) {
    if (m[clave]) return marca;
  }
  return '';
}

/**
 * A qué chat pertenece y con qué número. Con el direccionamiento @lid el
 * número viaja aparte (remoteJidAlt en baileys 7, senderPn en 6). Si no viene,
 * el chat se guarda por el LID y el número se completa cuando se sepa.
 */
function chatDe(key, lidAPn) {
  const jid = String(key?.remoteJid || '');
  if (!esUnoAUno(jid)) return null;
  const alt = key?.remoteJidAlt || key?.senderPn;
  let telefono = alt ? telefonoDeJid(alt) : null;
  if (!telefono && jid.endsWith('@lid')) telefono = lidAPn.get(telefonoDeJid(jid)) || null;
  if (!telefono && jid.endsWith('@s.whatsapp.net')) telefono = telefonoDeJid(jid);
  return { chat: telefono || telefonoDeJid(jid), telefono };
}

function aMensaje(msg, lidAPn) {
  const donde = chatDe(msg?.key, lidAPn);
  if (!donde) return null;
  const texto = textoDeMensaje(msg);
  if (!texto) return null;
  return {
    id: msg.key.id,
    chat: donde.chat,
    telefono: donde.telefono,
    de_juan: Boolean(msg.key.fromMe),
    texto,
    ts: Number(msg.messageTimestamp?.low ?? msg.messageTimestamp) || Math.floor(Date.now() / 1000),
    perfil: msg.pushName || null,
  };
}

/** Uno solo a la vez: dos conexiones con la misma sesión se pisan. */
function tomarTurno(dir) {
  const archivo = path.join(dir, 'lector.pid');
  const previo = Number(fs.existsSync(archivo) && fs.readFileSync(archivo, 'utf8'));
  if (previo && previo !== process.pid) {
    try {
      process.kill(previo, 0);
      return false;
    } catch {
      // El de antes ya no existe.
    }
  }
  fs.writeFileSync(archivo, String(process.pid));
  process.on('exit', () => {
    try {
      if (Number(fs.readFileSync(archivo, 'utf8')) === process.pid) fs.rmSync(archivo);
    } catch {
      // Nada que limpiar.
    }
  });
  return true;
}

function parar(dir) {
  const archivo = path.join(dir, 'lector.pid');
  const pid = Number(fs.existsSync(archivo) && fs.readFileSync(archivo, 'utf8'));
  if (!pid) return;
  try {
    process.kill(pid);
  } catch {
    // Ya no estaba corriendo.
  }
  fs.rmSync(archivo, { force: true });
}

async function main() {
  const vincular = process.argv.includes('--vincular');
  const dir = base.carpeta();
  if (process.argv.includes('--parar')) {
    parar(dir);
    return;
  }
  if (vincular) parar(dir);
  if (!tomarTurno(dir)) {
    console.log('El lector ya está corriendo.');
    return;
  }
  const db = base.abrir(dir);
  const lidAPn = new Map();
  let intentos = 0;

  const log = (...a) => console.log(new Date().toLocaleString('es-UY'), ...a);
  const excluidos = () => base.leerExcluidos(dir);

  function guardar(mensajes) {
    const lista = excluidos();
    let n = 0;
    for (const msg of mensajes) {
      const m = aMensaje(msg, lidAPn);
      if (m && base.guardarMensaje(db, lista, m)) n += 1;
    }
    return n;
  }

  function contactos(lista) {
    for (const c of lista || []) {
      const jid = String(c.id || '');
      if (!esUnoAUno(jid)) continue;
      const pn = c.phoneNumber ? telefonoDeJid(c.phoneNumber) : (jid.endsWith('@s.whatsapp.net') ? telefonoDeJid(jid) : null);
      if (c.lid && pn) lidAPn.set(telefonoDeJid(c.lid), pn);
      if (jid.endsWith('@lid') && pn) lidAPn.set(telefonoDeJid(jid), pn);
      base.guardarContacto(db, { chat: pn || telefonoDeJid(jid), telefono: pn, agendado: c.name || null, perfil: c.notify || c.verifiedName || null });
    }
    const borrados = base.purgarExcluidos(db, excluidos());
    if (borrados) log(`se borraron ${borrados} mensajes de contactos excluidos`);
  }

  async function conectar() {
    const b = await import('baileys');
    const makeWASocket = b.default?.default ?? b.default ?? b.makeWASocket;
    const { useMultiFileAuthState, fetchLatestBaileysVersion, Browsers, DisconnectReason } = b;
    const authDir = path.join(dir, 'auth');
    fs.mkdirSync(authDir, { recursive: true });
    const { state, saveCreds } = await useMultiFileAuthState(authDir);
    const { version } = await fetchLatestBaileysVersion();
    const [plataforma, , versionSO] = Browsers.appropriate('Desktop');

    const sock = makeWASocket({
      version,
      auth: state,
      browser: [plataforma, 'Oficina Scalerics', versionSO],
      logger: require('pino')({ level: 'error' }),
      // Que el teléfono de Juan siga sonando: un dispositivo "en línea" le
      // apaga las notificaciones.
      markOnlineOnConnect: false,
      syncFullHistory: true,
      shouldIgnoreJid: (jid) => !esUnoAUno(jid),
      // Nunca reenvía nada: si un dispositivo pide reintentar un mensaje de
      // Juan, que lo resuelva su teléfono.
      getMessage: async () => undefined,
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', async (u) => {
      const { connection, lastDisconnect, qr } = u;
      if (qr) {
        if (!vincular) {
          log('Falta vincular el teléfono: correr "Conectar WhatsApp a la Oficina.bat".');
          process.exit(2);
        }
        // El QR cambia cada ~20 s y solo sirve el de esta ventana. No se guarda
        // como imagen: el visor de fotos la deja bloqueada, la siguiente
        // escritura fallaba y el lector se caía a mitad del escaneo (el
        // teléfono decía "sin conexión").
        const QR = require('qrcode');
        console.clear();
        console.log(await QR.toString(qr, { type: 'terminal', small: true }));
        console.log('\nEn tu teléfono: WhatsApp Business → Dispositivos vinculados → Vincular un dispositivo,');
        console.log('y escaneá este código. Se renueva solo cada unos segundos.');
      }
      if (connection === 'open') {
        intentos = 0;
        log(`Conectado a WhatsApp (${telefonoDeJid(sock.user?.id)}). Solo lectura.`);
        if (vincular) {
          // Después de vincular, WhatsApp manda el historial en tandas. Se le da
          // tiempo y se termina: el .bat arranca el lector oculto.
          log('Vinculado. Bajando tus chats de los últimos 90 días: no cierres esta ventana...');
          setTimeout(() => {
            log('Listo.');
            process.exit(0);
          }, 4 * 60_000);
        }
      }
      if (connection === 'close') {
        const codigo = lastDisconnect?.error?.output?.statusCode;
        if (codigo === DisconnectReason.loggedOut) {
          log('Se cerró la sesión desde el teléfono. Hay que volver a vincular.');
          fs.rmSync(authDir, { recursive: true, force: true });
          process.exit(3);
        }
        if (codigo === DisconnectReason.restartRequired) {
          conectar().catch((e) => log('error al reconectar', e.message));
          return;
        }
        const espera = BACKOFF_MS[Math.min(intentos, BACKOFF_MS.length - 1)];
        intentos += 1;
        log(`Se cortó la conexión (${codigo}). Reintento en ${Math.round(espera / 1000)} s.`);
        setTimeout(() => conectar().catch((e) => log('error al reconectar', e.message)), espera);
      }
    });

    sock.ev.on('contacts.upsert', contactos);
    sock.ev.on('contacts.update', contactos);

    sock.ev.on('messaging-history.set', ({ contacts, messages }) => {
      contactos(contacts);
      const limite = Math.floor(Date.now() / 1000) - HISTORIAL_DIAS * 86400;
      const recientes = (messages || []).filter((m) => Number(m.messageTimestamp?.low ?? m.messageTimestamp) >= limite);
      const n = guardar(recientes);
      if (n) log(`historial: ${n} mensajes guardados`);
    });

    sock.ev.on('messages.upsert', ({ messages }) => {
      guardar(messages || []);
    });
  }

  await conectar();
}

if (require.main === module) {
  main().catch((e) => {
    console.error('El lector se cayó:', e.message);
    process.exit(1);
  });
}

module.exports = { textoDeMensaje, chatDe, aMensaje, esUnoAUno };
