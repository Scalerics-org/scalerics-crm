'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const Database = require('better-sqlite3');
const { hacerBackup, borrarViejos, listar } = require('../src/backup');

/** Una base de verdad en un directorio temporal, con su carpeta de respaldos. */
function conBase() {
  const raiz = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-backup-'));
  const db = new Database(path.join(raiz, 'wa.db'));
  db.exec('CREATE TABLE leads (id INTEGER PRIMARY KEY, nombre TEXT)');
  db.prepare('INSERT INTO leads (nombre) VALUES (?)').run('Panadería PanesAhora');
  return { db, cfg: { BACKUP_DIR: path.join(raiz, 'backups'), BACKUP_GUARDAR: 3 }, raiz };
}

test('el respaldo se puede abrir y tiene los datos', () => {
  const { db, cfg } = conBase();
  const r = hacerBackup({ db, cfg });
  assert.ok(r, 'se hizo');

  // Lo que importa de un respaldo no es que exista el archivo: es que se abra.
  const copia = new Database(r.archivo, { readonly: true });
  assert.equal(copia.prepare('SELECT COUNT(*) AS n FROM leads').get().n, 1);
  assert.equal(copia.prepare('SELECT nombre FROM leads').get().nombre, 'Panadería PanesAhora');
});

/**
 * VACUUM INTO falla si el archivo ya existe, asi que sin borrar antes el
 * segundo respaldo del dia se caia y quedaba el de la mañana como si fuera el
 * ultimo.
 */
test('correrlo dos veces el mismo dia pisa el anterior', () => {
  const { db, cfg } = conBase();
  hacerBackup({ db, cfg });

  db.prepare('INSERT INTO leads (nombre) VALUES (?)').run('Barbería El Corte');
  const r = hacerBackup({ db, cfg });
  assert.ok(r, 'el segundo tambien se hizo');

  const copia = new Database(r.archivo, { readonly: true });
  assert.equal(copia.prepare('SELECT COUNT(*) AS n FROM leads').get().n, 2, 'con lo nuevo');
  assert.equal(listar(cfg).length, 1, 'y sigue habiendo uno solo del dia');
});

test('se guardan los ultimos y se borran los viejos', () => {
  const { db, cfg } = conBase();
  for (const dia of ['01', '02', '03', '04', '05']) {
    hacerBackup({ db, cfg, ahora: () => new Date(`2026-08-${dia}T12:00:00Z`) });
  }

  const quedan = listar(cfg).map((b) => b.archivo);
  assert.equal(quedan.length, 3, 'BACKUP_GUARDAR = 3');
  assert.deepEqual(quedan, ['wa-2026-08-05.db', 'wa-2026-08-04.db', 'wa-2026-08-03.db'], 'los mas nuevos');
});

test('sin BACKUP_DIR no hace nada y no rompe', () => {
  const { db } = conBase();
  assert.equal(hacerBackup({ db, cfg: { BACKUP_DIR: '' } }), null);
});

/**
 * Un respaldo que falla en silencio es igual que no tenerlo, pero tampoco puede
 * tirar abajo el servicio: el bot tiene que seguir contestando.
 */
test('si el respaldo falla, avisa y devuelve null sin tirar', () => {
  const { db, cfg } = conBase();
  db.close();

  let grito = false;
  const r = hacerBackup({ db, cfg, logger: { error: () => { grito = true; }, info: () => {} } });

  assert.equal(r, null);
  assert.ok(grito, 'queda registrado que fallo');
});

test('borrarViejos no explota si no hay nada', () => {
  const { cfg } = conBase();
  fs.mkdirSync(cfg.BACKUP_DIR, { recursive: true });
  assert.equal(borrarViejos({ cfg }), 0);
});
