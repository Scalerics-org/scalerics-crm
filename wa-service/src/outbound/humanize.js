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
 */
function duracionTyping(texto, { msPorCaracter = 40, techoMs = 6000 } = {}) {
  const base = Math.min(String(texto || '').length * msPorCaracter, techoMs);
  const factor = 0.7 + entre(0, 60) / 100;
  return Math.round(base * factor);
}

const dormir = (ms) => (ms > 0 ? new Promise((r) => setTimeout(r, ms)) : Promise.resolve());

/**
 * Simula que alguien escribe: muestra "escribiendo...", espera proporcional al
 * texto, lo apaga y hace una pausa corta antes de mandar.
 */
async function simularEscritura(proveedor, to, texto, cfg) {
  if (!cfg.TYPING_ENABLED || !proveedor.capacidades.typingIndicator || !texto) return;
  await proveedor.setPresencia(to, 'composing');
  await dormir(duracionTyping(texto));
  await proveedor.setPresencia(to, 'paused');
  await dormir(entre(400, 1200));
}

module.exports = { entre, jitter, duracionTyping, simularEscritura, dormir };
