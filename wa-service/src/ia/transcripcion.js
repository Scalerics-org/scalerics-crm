'use strict';

const { toFile } = require('openai');

/**
 * Notas de voz a texto.
 *
 * Las de WhatsApp vienen en OGG/Opus, que es uno de los formatos que acepta la
 * API, asi que el buffer se manda tal cual — sin ffmpeg ni conversion.
 *
 * Sin clave esto queda inactivo y al que manda un audio se le pide que escriba,
 * que es lo que hacia antes de existir este modulo.
 */

/** Un audio largo no es un lead contando su negocio: es otra cosa. */
function crearTranscriptor({ openai = null, modelo, maxSegundos = 300, logger = null } = {}) {
  return {
    activo: Boolean(openai),

    /**
     * @param {Buffer} audio
     * @param {number} segundos duracion que reporta WhatsApp, 0 si no la manda.
     * @returns {Promise<string|null>} el texto, o null si no se pudo.
     */
    async transcribir(audio, segundos = 0) {
      if (!openai) return null;

      if (!audio || !audio.length) {
        logger?.warn('audio vacio, no hay nada que transcribir');
        return null;
      }
      // El corte es por costo y por sentido: nadie describe su negocio en diez
      // minutos, y si lo hace conviene que lo escuche una persona.
      if (segundos && segundos > maxSegundos) {
        logger?.info({ segundos, maxSegundos }, 'audio demasiado largo, no se transcribe');
        return null;
      }

      try {
        const r = await openai.audio.transcriptions.create({
          file: await toFile(audio, 'nota.ogg', { type: 'audio/ogg' }),
          model: modelo,
          language: 'es',
        });
        const texto = String(r?.text || '').trim();
        if (!texto) {
          logger?.info('la transcripcion vino vacia');
          return null;
        }
        return texto;
      } catch (e) {
        logger?.warn({ err: String(e.message || e) }, 'fallo la transcripcion del audio');
        return null;
      }
    },
  };
}

module.exports = { crearTranscriptor };
