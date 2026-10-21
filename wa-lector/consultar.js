'use strict';

/**
 * Lo que usa Bruno (la sesión de Claude Code) para leer los chats de Juan.
 * Imprime JSON. Nunca muestra a los contactos excluidos.
 *
 *   node consultar.js nuevos               chats con mensajes desde la última vez, con contexto
 *   node consultar.js marcar <hasta>       ya procesé hasta ese momento (el "hasta" de nuevos)
 *   node consultar.js chat "<nombre o número>"   los últimos mensajes con alguien
 *   node consultar.js estado               cuántos chats y mensajes hay, y el último
 */

const base = require('./base');

function main(args) {
  const dir = base.carpeta();
  const db = base.abrir(dir);
  const excluidos = base.leerExcluidos(dir);
  const [cmd, arg] = args;
  let salida;
  if (cmd === 'nuevos') {
    salida = base.chatsNuevos(db, excluidos);
  } else if (cmd === 'marcar' && Number(arg) > 0) {
    base.marcar(db, 'procesado_hasta', Number(arg));
    salida = { ok: true };
  } else if (cmd === 'chat' && arg) {
    salida = base.chat(db, excluidos, arg);
  } else if (cmd === 'estado') {
    const fila = db.prepare('SELECT COUNT(*) AS mensajes, COUNT(DISTINCT chat) AS chats, MAX(ts) AS ultimo FROM mensajes').get();
    salida = {
      chats: Number(fila.chats), mensajes: Number(fila.mensajes),
      ultimo: fila.ultimo ? new Date(Number(fila.ultimo) * 1000).toISOString() : null,
      vinculado: require('node:fs').existsSync(require('node:path').join(dir, 'auth', 'creds.json')),
    };
  } else {
    console.error(require('node:fs').readFileSync(__filename, 'utf8').split('*/')[0]);
    process.exit(1);
  }
  process.stdout.write(JSON.stringify(salida, null, 2) + '\n');
}

main(process.argv.slice(2));
