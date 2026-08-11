'use strict';

const fs = require('node:fs');
const path = require('node:path');
const Database = require('better-sqlite3');

/**
 * Abre la base y corre las migraciones. Devuelve la conexion de better-sqlite3,
 * que es sincrona: no hay await en toda la capa de datos.
 */
function abrir(dbPath) {
  if (dbPath !== ':memory:') {
    fs.mkdirSync(path.dirname(dbPath), { recursive: true });
  }
  const db = new Database(dbPath);

  // WAL deja leer mientras se escribe; con un solo proceso alcanza y sobra.
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');

  migrar(db);
  return db;
}

function migrar(db) {
  const dir = path.join(__dirname, 'migrations');
  const archivos = fs.readdirSync(dir).filter((f) => f.endsWith('.sql')).sort();
  for (const archivo of archivos) {
    db.exec(fs.readFileSync(path.join(dir, archivo), 'utf8'));
  }
}

module.exports = { abrir };
