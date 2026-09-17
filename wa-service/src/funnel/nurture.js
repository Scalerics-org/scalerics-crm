'use strict';

/**
 * Cuando volver a escribirle al que dijo "mas adelante".
 *
 * Al modelo se le pide en que cajon cae lo que escucho, no la fecha. Es la
 * leccion que costo cara con el tamaño del equipo: se le pedia el tramo y a
 * "somos 3" le ponia 3, que significaba "de 6 a 20 personas". Se le explico con
 * ejemplos dos veces y lo seguia errando, porque se le estaba pidiendo una
 * conversion y no una observacion.
 *
 * Aca pasaria lo mismo con las fechas, y peor: una fecha mal calculada no se
 * nota al revisar: se nota tres meses despues, cuando el mensaje sale o no sale.
 */

/** Cajon -> dias. Ninguno es cero, asi que el || de abajo es seguro. */
const DIAS = {
  unos_dias: 5,
  unas_semanas: 14,
  un_mes: 30,
  varios_meses: 75,
  // Dijo que no es el momento pero no dio ninguna referencia.
  sin_fecha: null,
};

/** Los cajones que el modelo puede devolver, para el enum de la herramienta. */
const CAJONES = Object.keys(DIAS);

/**
 * @param {string} aplaza  el cajon que dijo el modelo
 * @param {Date} ahora
 * @param {object} cfg  NURTURE_DEFAULT_DIAS, NURTURE_MIN_DIAS, NURTURE_MAX_DIAS
 * @returns {Date} cuando escribirle
 */
function cuandoVolver(aplaza, ahora, cfg) {
  const dias = DIAS[aplaza] || cfg.NURTURE_DEFAULT_DIAS;
  // El piso y el techo se aplican aca adentro y no en quien llama, para que
  // ningun camino se los saltee.
  const acotado = Math.min(Math.max(dias, cfg.NURTURE_MIN_DIAS), cfg.NURTURE_MAX_DIAS);
  return new Date(ahora.getTime() + acotado * 86_400_000);
}

module.exports = { cuandoVolver, CAJONES, DIAS };
