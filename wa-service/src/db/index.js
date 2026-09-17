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

/**
 * Corre las migraciones pendientes y anota cuales se aplicaron.
 *
 * Hace falta el registro porque SQLite no tiene ADD COLUMN IF NOT EXISTS: sin
 * esto, la segunda vez que arranca el servicio la migracion del embudo falla
 * con "duplicate column name" y no levanta.
 */
function migrar(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      archivo    TEXT PRIMARY KEY,
      aplicada_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
  `);

  const yaAplicadas = new Set(
    db.prepare('SELECT archivo FROM schema_migrations').all().map((r) => r.archivo)
  );

  const dir = path.join(__dirname, 'migrations');
  const archivos = fs.readdirSync(dir).filter((f) => f.endsWith('.sql')).sort();

  const anotar = db.prepare('INSERT INTO schema_migrations (archivo) VALUES (?)');
  for (const archivo of archivos) {
    if (yaAplicadas.has(archivo)) continue;
    const sql = fs.readFileSync(path.join(dir, archivo), 'utf8');
    db.transaction(() => {
      db.exec(sql);
      anotar.run(archivo);
    })();
  }
}

module.exports = { abrir };
