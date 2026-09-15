'use strict';

const crypto = require('node:crypto');

/** Entero uniforme en [min, max]. crypto en vez de Math.random. */
function entre(min, max) {
  if (max <= min) return min;
  return min + crypto.randomInt(0, max - min + 1);
}

/**
 * Jitter con forma de campana en vez de uniforme: se promedian dos sorteos.
 * Los intervalos quedan agrupados alrededor del centro con colas hacia los
 * extremos, que es como escribe una persona. Un uniforme perfecto tambien es
 * un patron.
 */
function jitter(min, max) {
  if (max <= min) return min;
  return Math.round((entre(min, max) + entre(min, max)) / 2);
}

/**
 * Cuanto dura el "escribiendo...": proporcional al largo del texto, con techo,
 * y +-30% para que dos mensajes iguales no tarden lo mismo.
 *
 * El factor se aplica DESPUES del techo, asi que el maximo real es techoMs*1.3
 * y no techoMs. Se deja asi —recortar despues aplanaria todos los mensajes
 * largos al mismo valor exacto, que es justo el patron que esto evita— pero
 * hay que tenerlo en cuenta al elegir el techo.
 */
function duracionTyping(texto, { msPorCaracter = 30, techoMs = 2000 } = {}) {
  const base = Math.min(String(texto || '').length * msPorCaracter, techoMs);
  const factor = 0.7 + entre(0, 60) / 100;
  return Math.round(base * factor);
}

const dormir = (ms) => (ms > 0 ? new Promise((r) => setTimeout(r, ms)) : Promise.resolve());

/**
 * Simula que alguien escribe: muestra "escribiendo...", espera proporcional al
 * texto, lo apaga y hace una pausa corta antes de mandar.
 *
 * @param {boolean} [esInterno] la ficha al equipo no necesita el teatro, y va
 *   adelante de la respuesta al lead: cada segundo que tarda es un segundo que
 *   espera alguien que si esta mirando el telefono.
 */
async function simularEscritura(proveedor, to, texto, cfg, esInterno = false) {
  if (!cfg.TYPING_ENABLED || !proveedor.capacidades.typingIndicator || !texto) return;
  if (esInterno && !cfg.TYPING_INTERNO) return;

  await proveedor.setPresencia(to, 'composing');
  await dormir(duracionTyping(texto, {
    msPorCaracter: cfg.TYPING_MS_POR_CARACTER,
    techoMs: cfg.TYPING_TECHO_MS,
  }));
  await proveedor.setPresencia(to, 'paused');
  await dormir(entre(cfg.TYPING_PAUSA_MIN_MS, cfg.TYPING_PAUSA_MAX_MS));
}

module.exports = { entre, jitter, duracionTyping, simularEscritura, dormir };
