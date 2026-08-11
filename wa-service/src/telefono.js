'use strict';

/**
 * Normaliza a E.164 sin '+'. Es el unico lugar donde se decide que es un telefono
 * valido: el resto del servicio asume que ya viene normalizado.
 *
 * Uruguay: los celulares son 09XXXXXXX (9 digitos con el 0 inicial). El 0 es de
 * marcado nacional y no va en E.164, asi que 099123456 -> 59899123456.
 *
 * @returns {string|null} el numero normalizado, o null si no se pudo interpretar.
 */
function normalizar(crudo, codigoPais = '598') {
  if (typeof crudo !== 'string') return null;

  const tieneMas = crudo.trim().startsWith('+');
  let d = crudo.replace(/\D/g, '');
  if (!d) return null;

  // Ya viene en internacional: se respeta tal cual.
  if (tieneMas || d.startsWith('00')) {
    d = d.replace(/^00/, '');
    return d.length >= 8 ? d : null;
  }

  // Ya trae el codigo de pais adelante y largo plausible.
  if (d.startsWith(codigoPais) && d.length >= codigoPais.length + 8) {
    return d;
  }

  // Nacional con 0 de marcado: 099123456 -> 99123456
  if (d.startsWith('0')) d = d.slice(1);

  if (d.length < 7) return null;
  return codigoPais + d;
}

/** Primer token del nombre, capitalizado. Para los saludos. */
function primerNombre(nombre) {
  const token = String(nombre || '').trim().split(/\s+/)[0] || '';
  if (!token) return '';
  return token.charAt(0).toUpperCase() + token.slice(1).toLowerCase();
}

module.exports = { normalizar, primerNombre };
