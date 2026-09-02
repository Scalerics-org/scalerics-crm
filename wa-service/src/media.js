'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

/**
 * Los archivos que manda el lead: por ahora las notas de voz.
 *
 * Antes se transcribian y el .ogg se tiraba. En el panel del CRM quedaba el
 * texto y nada mas: no se podia escuchar el original ni se notaba siquiera que
 * habia sido un audio. Y cuando la transcripcion sale mal —que pasa, con audio
 * corto y acento rioplatense— eso es la diferencia entre entender al lead y no.
 *
 * Viven en el volumen, al lado de la base, que es lo mismo que hace el bot de
 * la bloquera con su carpeta de uploads. Una nota de voz pesa unos 20KB.
 *
 * La transcripcion queda para siempre; el audio no. Guardar la voz de gente sin
 * necesidad no aporta nada, asi que se borra a los 90 dias.
 */

const TIPOS = {
  ogg: 'audio/ogg',
  mp3: 'audio/mpeg',
  m4a: 'audio/mp4',
  wav: 'audio/wav',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
};

function crearMedia({ dir, diasRetencion = 90, logger = null } = {}) {
  fs.mkdirSync(dir, { recursive: true });

  /**
   * El nombre llega por la URL, asi que lo elige quien pide. Sin esto, un
   * "../../data/wa.db" saca la base entera por un endpoint pensado para audios.
   */
  function rutaSegura(nombre) {
    const limpio = String(nombre || '');
    if (!limpio || limpio !== path.basename(limpio)) return null;
    return path.join(dir, limpio);
  }

  return {
    dir,

    /** @returns {string} el nombre con el que quedo guardado. */
    guardar(buffer, extension = 'ogg') {
      const ext = String(extension).replace(/[^a-z0-9]/gi, '').toLowerCase() || 'bin';
      const nombre = `${Date.now()}-${crypto.randomBytes(6).toString('hex')}.${ext}`;
      fs.writeFileSync(path.join(dir, nombre), buffer);
      return nombre;
    },

    /** @returns {Buffer|null} null si no existe o si el nombre no es un nombre. */
    leer(nombre) {
      const ruta = rutaSegura(nombre);
      if (!ruta) return null;
      try {
        return fs.readFileSync(ruta);
      } catch {
        return null;
      }
    },

    contentType(nombre) {
      const ext = path.extname(String(nombre || '')).slice(1).toLowerCase();
      return TIPOS[ext] || 'application/octet-stream';
    },

    /** @returns {number} cuantos borro. */
    limpiar(ahora = new Date()) {
      const corte = ahora.getTime() - diasRetencion * 24 * 3600_000;
      let borrados = 0;
      for (const nombre of fs.readdirSync(dir)) {
        const ruta = path.join(dir, nombre);
        try {
          if (fs.statSync(ruta).mtimeMs >= corte) continue;
          fs.unlinkSync(ruta);
          borrados += 1;
        } catch (e) {
          logger?.warn({ nombre, err: String(e.message || e) }, 'no se pudo borrar un archivo viejo');
        }
      }
      if (borrados) logger?.info({ borrados, diasRetencion }, 'archivos viejos borrados');
      return borrados;
    },
  };
}

module.exports = { crearMedia, TIPOS };
