'use strict';

/**
 * Lo que guarda el lector de WhatsApp de Juan, en su notebook (pedido del 21/10).
 *
 * Bruno, el agente de la Oficina, lee de acá los chats uno a uno de Juan para
 * armarle recordatorios. Los chats NUNCA salen de la notebook: lo que llega al
 * CRM es solo el recordatorio que arma Bruno.
 *
 * Los contactos de la lista de excluidos (excluidos.txt, uno por línea, nombre
 * o número) no se guardan. Si un mensaje llega antes de saber cómo tiene Juan
 * agendado al contacto, se guarda, y en cuanto se sabe el nombre se borra todo
 * lo de ese contacto (`purgarExcluidos`).
 *
 * Ojo con el direccionamiento @lid de WhatsApp: muchos chats llegan con un
 * identificador (`lid:...`) en vez del número, y entonces no se sabe cómo los
 * agendó Juan. El 21/10 se colaron así mensajes de dos excluidos. Por eso un
 * chat sin número también queda afuera si el nombre que se puso la persona es
 * el mismo que el de un excluido (mejor perder un chat ajeno que leer uno de
 * ellos), y apenas aparece el número (`unirLid`) se junta con su contacto.
 */

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { DatabaseSync } = require('node:sqlite');

function carpeta() {
  const base = process.env.WA_LECTOR_DIR
    || path.join(process.env.APPDATA || path.join(os.homedir(), '.config'), 'Scalerics', 'wa-lector');
  fs.mkdirSync(base, { recursive: true });
  return base;
}

function normalizar(texto) {
  return String(texto || '')
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function soloDigitos(texto) {
  return String(texto || '').replace(/\D/g, '');
}

/** Lee excluidos.txt: líneas vacías y las que empiezan con # no cuentan. */
function leerExcluidos(dir) {
  const archivo = path.join(dir, 'excluidos.txt');
  if (!fs.existsSync(archivo)) return [];
  return fs.readFileSync(archivo, 'utf8').split(/\r?\n/)
    .map((l) => l.trim()).filter((l) => l && !l.startsWith('#'));
}

/**
 * Si un contacto está en la lista. Un número se compara por los últimos 8
 * dígitos (094053389 y 59894053389 son el mismo). Un nombre tiene que aparecer
 * entero en el nombre del contacto, palabra por palabra: "Javo" excluye a
 * "Javo Pérez" pero no a "Javier".
 */
function estaExcluido(excluidos, { telefono, nombres = [] }) {
  const tel = soloDigitos(telefono);
  const nombresNorm = nombres.map(normalizar).filter(Boolean).map((n) => ` ${n} `);
  return excluidos.some((e) => {
    const digitos = soloDigitos(e);
    if (digitos.length >= 7 && digitos.length === e.replace(/[\s+()-]/g, '').length) {
      return tel && tel.slice(-8) === digitos.slice(-8);
    }
    const n = normalizar(e);
    return n && nombresNorm.some((nom) => nom.includes(` ${n} `));
  });
}

function abrir(dir = carpeta()) {
  const db = new DatabaseSync(path.join(dir, 'chats.db'));
  db.exec(`
    PRAGMA journal_mode = WAL;
    CREATE TABLE IF NOT EXISTS contactos (
      chat        TEXT PRIMARY KEY,          -- teléfono, o el LID si no se conoce el número
      telefono    TEXT,
      agendado    TEXT,                      -- cómo lo tiene Juan en el teléfono
      perfil      TEXT,                      -- el nombre que se puso la persona
      actualizado INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS mensajes (
      id      TEXT PRIMARY KEY,              -- id de WhatsApp
      chat    TEXT NOT NULL,
      de_juan INTEGER NOT NULL,
      texto   TEXT NOT NULL,
      ts      INTEGER NOT NULL               -- segundos, hora de WhatsApp
    );
    CREATE INDEX IF NOT EXISTS ix_mensajes_chat ON mensajes(chat, ts);
    CREATE TABLE IF NOT EXISTS marcas (
      nombre TEXT PRIMARY KEY,
      valor  INTEGER NOT NULL
    );
  `);
  // Los chats por LID de antes del 21/10 se guardaban sin prefijo.
  for (const c of db.prepare("SELECT chat FROM contactos WHERE telefono IS NULL AND chat NOT LIKE 'lid:%'").all()) {
    db.prepare('UPDATE mensajes SET chat = ? WHERE chat = ?').run('lid:' + c.chat, c.chat);
    db.prepare('UPDATE OR IGNORE contactos SET chat = ? WHERE chat = ?').run('lid:' + c.chat, c.chat);
  }
  return db;
}

function contacto(db, chat) {
  return db.prepare('SELECT * FROM contactos WHERE chat = ?').get(chat) || null;
}

function nombresDe(c) {
  return c ? [c.agendado, c.perfil].filter(Boolean) : [];
}

/** El nombre que se pusieron los excluidos que Juan tiene agendados. */
function perfilesExcluidos(db, excluidos) {
  return new Set(db.prepare('SELECT * FROM contactos WHERE agendado IS NOT NULL AND perfil IS NOT NULL').all()
    .filter((c) => estaExcluido(excluidos, { telefono: c.telefono, nombres: [c.agendado] }))
    .map((c) => normalizar(c.perfil)).filter(Boolean));
}

function chatExcluido(excluidos, c, perfiles, telefono) {
  if (estaExcluido(excluidos, { telefono: telefono || c?.telefono, nombres: nombresDe(c) })) return true;
  const sinNumero = !(telefono || c?.telefono);
  return Boolean(sinNumero && c?.perfil && perfiles.has(normalizar(c.perfil)));
}

function guardarContacto(db, { chat, telefono, agendado, perfil }) {
  if (!chat) return;
  const previo = contacto(db, chat);
  db.prepare(`
    INSERT INTO contactos (chat, telefono, agendado, perfil, actualizado) VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(chat) DO UPDATE SET
      telefono = COALESCE(excluded.telefono, contactos.telefono),
      agendado = COALESCE(excluded.agendado, contactos.agendado),
      perfil = COALESCE(excluded.perfil, contactos.perfil),
      actualizado = excluded.actualizado
  `).run(chat, telefono || previo?.telefono || null, agendado || null, perfil || null, Date.now());
}

/** Guarda un mensaje si el contacto no está excluido. Devuelve si lo guardó. */
function guardarMensaje(db, excluidos, m) {
  if (!m.id || !m.chat || !m.texto) return false;
  if (m.perfil && !m.de_juan) guardarContacto(db, { chat: m.chat, telefono: m.telefono, perfil: m.perfil });
  const c = contacto(db, m.chat);
  if (chatExcluido(excluidos, c, perfilesExcluidos(db, excluidos), m.telefono)) return false;
  db.prepare('INSERT OR IGNORE INTO mensajes (id, chat, de_juan, texto, ts) VALUES (?, ?, ?, ?, ?)')
    .run(m.id, m.chat, m.de_juan ? 1 : 0, String(m.texto).slice(0, 4000), Number(m.ts) || Math.floor(Date.now() / 1000));
  return true;
}

/** Borra todo lo de los contactos que ahora sabemos que están en la lista. */
function purgarExcluidos(db, excluidos) {
  let borrados = 0;
  const perfiles = perfilesExcluidos(db, excluidos);
  for (const c of db.prepare('SELECT * FROM contactos').all()) {
    if (chatExcluido(excluidos, c, perfiles)) {
      borrados += Number(db.prepare('DELETE FROM mensajes WHERE chat = ?').run(c.chat).changes);
    }
  }
  return borrados;
}

function nombreVisible(c, chat) {
  return c?.agendado || c?.perfil || c?.telefono || chat;
}

/**
 * Los chats con mensajes nuevos desde la última marca, cada uno con sus
 * últimos mensajes para entender el contexto. Filtra los excluidos otra vez por
 * si la lista cambió después de guardar.
 */
function chatsNuevos(db, excluidos, { contexto = 15, horasMax = 72 } = {}) {
  const desde = Math.max(marca(db, 'procesado_hasta'), Math.floor(Date.now() / 1000) - horasMax * 3600);
  const chats = db.prepare('SELECT chat, MAX(ts) AS ultimo FROM mensajes WHERE ts > ? GROUP BY chat ORDER BY ultimo')
    .all(desde);
  const salida = [];
  let hasta = desde;
  const perfiles = perfilesExcluidos(db, excluidos);
  for (const { chat, ultimo } of chats) {
    const c = contacto(db, chat);
    hasta = Math.max(hasta, Number(ultimo));
    if (chatExcluido(excluidos, c, perfiles)) continue;
    const mensajes = db.prepare('SELECT de_juan, texto, ts FROM mensajes WHERE chat = ? ORDER BY ts DESC LIMIT ?')
      .all(chat, contexto).reverse();
    salida.push({ chat, contacto: nombreVisible(c, chat), telefono: c?.telefono || null, mensajes: mensajes.map(fila) });
  }
  return { hasta, chats: salida };
}

function chat(db, excluidos, quien, cantidad = 30) {
  const q = normalizar(quien);
  const digitos = soloDigitos(quien);
  const candidatos = db.prepare('SELECT * FROM contactos').all().filter((c) =>
    (digitos.length >= 6 && soloDigitos(c.telefono).endsWith(digitos.slice(-8)))
    || (q && nombresDe(c).some((n) => normalizar(n).includes(q))));
  const perfiles = perfilesExcluidos(db, excluidos);
  return candidatos
    .filter((c) => !chatExcluido(excluidos, c, perfiles))
    .map((c) => ({
      chat: c.chat, contacto: nombreVisible(c, c.chat), telefono: c.telefono || null,
      mensajes: db.prepare('SELECT de_juan, texto, ts FROM mensajes WHERE chat = ? ORDER BY ts DESC LIMIT ?')
        .all(c.chat, cantidad).reverse().map(fila),
    }))
    .filter((c) => c.mensajes.length);
}

/** Apareció el número de un chat que teníamos por LID: se junta con su contacto. */
function unirLid(db, lid, telefono) {
  const chatLid = 'lid:' + lid;
  const previo = contacto(db, chatLid);
  db.prepare('UPDATE mensajes SET chat = ? WHERE chat = ?').run(telefono, chatLid);
  if (previo) {
    guardarContacto(db, { chat: telefono, telefono, perfil: previo.perfil });
    db.prepare('DELETE FROM contactos WHERE chat = ?').run(chatLid);
  }
}

function fila(m) {
  return { de: m.de_juan ? 'Juan' : 'contacto', texto: m.texto, cuando: new Date(Number(m.ts) * 1000).toISOString() };
}

function marca(db, nombre) {
  return Number(db.prepare('SELECT valor FROM marcas WHERE nombre = ?').get(nombre)?.valor || 0);
}

function marcar(db, nombre, valor) {
  db.prepare('INSERT INTO marcas (nombre, valor) VALUES (?, ?) ON CONFLICT(nombre) DO UPDATE SET valor = excluded.valor')
    .run(nombre, Number(valor));
}

module.exports = {
  carpeta, normalizar, leerExcluidos, estaExcluido, abrir, guardarContacto, guardarMensaje,
  purgarExcluidos, chatsNuevos, chat, marca, marcar, unirLid,
};
