'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { crearR2 } = require('./r2');

/**
 * Copias de la base, hechas por SQLite y no por el sistema de archivos.
 *
 * Fly saca un snapshot del volumen por dia y los guarda cinco. Eso cubre que se
 * muera el disco, pero no cubre tres cosas:
 *
 * - Cinco dias es poco. Si un bug borra datos y nadie lo nota en una semana, la
 *   version buena ya no existe.
 * - Viven adentro de Fly. Si se pierde el acceso a la cuenta, se pierden con
 *   ella. Ya pasó una vez con otra app.
 * - Un snapshot del volumen copia el archivo tal como esta en ese instante, con
 *   la base abierta y el journal a medio escribir. En este servicio el journal
 *   llega a ser mas grande que la base. SQLite casi siempre se recupera de eso,
 *   pero "casi siempre" no es lo que se quiere de un respaldo.
 *
 * VACUUM INTO se lo pide a SQLite, que sabe esperar a estar consistente y de
 * paso deja el archivo compactado. Es la unica forma de tener una copia que se
 * abre sin sorpresas.
 */

/** Una copia por dia: correr dos veces el mismo dia pisa la anterior. */
function nombreDelDia(ahora) {
  return `wa-${ahora.toISOString().slice(0, 10)}.db`;
}

/**
 * @returns {{archivo: string, bytes: number}|null} null si no se pudo.
 */
function hacerBackup({ db, cfg, logger = null, ahora = () => new Date() }) {
  if (!cfg.BACKUP_DIR) return null;

  try {
    fs.mkdirSync(cfg.BACKUP_DIR, { recursive: true });
    const destino = path.join(cfg.BACKUP_DIR, nombreDelDia(ahora()));

    // VACUUM INTO falla si el archivo ya existe.
    if (fs.existsSync(destino)) fs.unlinkSync(destino);
    db.prepare('VACUUM INTO ?').run(destino);

    const bytes = fs.statSync(destino).size;
    logger?.info({ archivo: path.basename(destino), kb: Math.round(bytes / 1024) }, 'respaldo hecho');

    borrarViejos({ cfg, logger });
    return { archivo: destino, bytes };
  } catch (e) {
    // Un respaldo que falla no puede tirar abajo el servicio, pero tiene que
    // gritar: un respaldo que falla en silencio es igual que no tenerlo.
    logger?.error({ err: String(e.message || e) }, 'NO SE PUDO HACER EL RESPALDO');
    return null;
  }
}

/** Deja solo las ultimas N copias. El volumen es de 1 GB, no infinito. */
function borrarViejos({ cfg, logger = null }) {
  if (!cfg.BACKUP_GUARDAR) return 0;

  const archivos = fs.readdirSync(cfg.BACKUP_DIR)
    .filter((f) => /^wa-\d{4}-\d{2}-\d{2}\.db$/.test(f))
    .sort()
    .reverse();

  let borrados = 0;
  for (const viejo of archivos.slice(cfg.BACKUP_GUARDAR)) {
    fs.unlinkSync(path.join(cfg.BACKUP_DIR, viejo));
    borrados += 1;
  }
  if (borrados) logger?.info({ borrados }, 'respaldos viejos borrados');
  return borrados;
}

/** Lo que hay guardado, del mas nuevo al mas viejo. */
function listar(cfg) {
  if (!cfg.BACKUP_DIR || !fs.existsSync(cfg.BACKUP_DIR)) return [];
  return fs.readdirSync(cfg.BACKUP_DIR)
    .filter((f) => /^wa-\d{4}-\d{2}-\d{2}\.db$/.test(f))
    .sort()
    .reverse()
    .map((f) => {
      const s = fs.statSync(path.join(cfg.BACKUP_DIR, f));
      return { archivo: f, kb: Math.round(s.size / 1024), hecho: s.mtime.toISOString() };
    });
}

/**
 * Arranca el respaldo periodico. Hace uno al arrancar tambien: si el servicio
 * se reinicia todos los dias a la misma hora, esperar el intervalo completo
 * significaria no respaldar nunca.
 */
function arrancarBackups({ db, cfg, logger = null, fetch: _fetch = globalThis.fetch }) {
  if (!cfg.BACKUP_DIR || !cfg.BACKUP_HORAS) return { parar() {} };

  const r2 = crearR2({ cfg, logger, fetch: _fetch });

  /** El respaldo local y, si hay credenciales, la copia afuera de Fly. */
  const respaldar = async () => {
    const r = hacerBackup({ db, cfg, logger });
    if (!r || !r2.activo) return r;
    await r2.subir(path.basename(r.archivo), fs.readFileSync(r.archivo));
    return r;
  };

  respaldar();
  const timer = setInterval(respaldar, cfg.BACKUP_HORAS * 3600_000);
  timer.unref?.();

  logger?.info(
    { dir: cfg.BACKUP_DIR, cadaHoras: cfg.BACKUP_HORAS, guarda: cfg.BACKUP_GUARDAR, r2: r2.activo },
    'respaldos activos'
  );
  return { parar() { clearInterval(timer); }, respaldar };
}

module.exports = { hacerBackup, borrarViejos, listar, arrancarBackups, nombreDelDia };
