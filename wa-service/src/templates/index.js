'use strict';

const crypto = require('node:crypto');
const { clasificar } = require('./rubros');
const { PLANTILLAS, fichaAM, avisoRespuesta, avisoContactoNuevo, avisoSinRespuesta, resumenEmbudo } = require('./messages');
const { primerNombre } = require('../telefono');

/** Entero uniforme en [0, max). crypto en vez de Math.random: sin valores redondos. */
function alAzar(max) {
  return max <= 1 ? 0 : crypto.randomInt(0, max);
}

/** Necesidad recortada a ~60 caracteres, en minuscula, para meter en una frase. */
function necesidadCorta(necesidad) {
  const t = String(necesidad || '').replace(/\s+/g, ' ').trim().toLowerCase();
  if (!t) return '';
  if (t.length <= 60) return t;
  const cortado = t.slice(0, 60);
  const ultimoEspacio = cortado.lastIndexOf(' ');
  return (ultimoEspacio > 30 ? cortado.slice(0, ultimoEspacio) : cortado) + '…';
}

function contexto(lead) {
  return {
    primerNombre: primerNombre(lead.nombre),
    necesidadCorta: necesidadCorta(lead.necesidad),
  };
}

/**
 * Elige una variante del texto para el lead.
 * @param {'bienvenida'|'followup'} tipo
 * @param {number} [variante] indice fijo; si no se pasa, se sortea.
 */
function render(lead, tipo, variante) {
  const clave = lead.rubro_norm || clasificar(lead.rubro);
  const plantilla = PLANTILLAS[clave] || PLANTILLAS.generico;
  const variantes = plantilla[tipo] || PLANTILLAS.generico[tipo];
  const i = Number.isInteger(variante) ? variante % variantes.length : alAzar(variantes.length);
  return variantes[i](contexto(lead));
}

module.exports = {
  render,
  clasificar,
  necesidadCorta,
  fichaAM,
  avisoRespuesta,
  avisoContactoNuevo,
  avisoSinRespuesta,
  resumenEmbudo,
};
