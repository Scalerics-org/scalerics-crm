'use strict';

const pino = require('pino');

/**
 * Deja visibles los ultimos 4 digitos y tapa el resto: 59899123456 -> 5989****3456.
 * Sirve para poder seguir un caso en los logs sin dejar la agenda entera escrita en disco.
 */
function taparTelefono(valor) {
  if (typeof valor !== 'string') return valor;
  const digitos = valor.replace(/\D/g, '');
  if (digitos.length < 8) return '****';
  return `${digitos.slice(0, 4)}****${digitos.slice(-4)}`;
}

function crear({ level = 'info', produccion = true } = {}) {
  return pino({
    level,
    // En produccion los telefonos no van enteros al log; en dev sirve verlos completos.
    redact: produccion
      ? {
          paths: ['telefono', '*.telefono', 'to', '*.to', 'phone', '*.phone'],
          censor: (valor) => taparTelefono(valor),
        }
      : undefined,
  });
}

module.exports = { crear, taparTelefono };
