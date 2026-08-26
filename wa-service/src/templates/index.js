'use strict';

const crypto = require('node:crypto');
const { clasificar } = require('./rubros');
const M = require('./messages');
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

function contexto(lead, calendly = '') {
  return {
    primerNombre: primerNombre(lead.nombre),
    necesidadCorta: necesidadCorta(lead.necesidad),
    calendly,
  };
}

/**
 * Renderizador de los textos que le llegan al lead.
 *
 * Necesita el link de Calendly porque, desde la decision de priorizar
 * conversion, va dentro del primer mensaje y del follow-up.
 */
function crearTextosLead({ calendlyLink = '' } = {}) {
  return {
    /**
     * @param {'bienvenida'|'followup'} tipo
     * @param {number} [variante] indice fijo; si no se pasa, se sortea.
     */
    render(lead, tipo, variante) {
      const clave = lead.rubro_norm || clasificar(lead.rubro);
      const plantilla = M.PLANTILLAS[clave] || M.PLANTILLAS.generico;
      const variantes = plantilla[tipo] || M.PLANTILLAS.generico[tipo];
      const i = Number.isInteger(variante) ? variante % variantes.length : alAzar(variantes.length);
      return variantes[i](contexto(lead, calendlyLink));
    },

    recordatorioDiaAntes: (lead, datos) =>
      M.recordatorioDiaAntes(contexto(lead, calendlyLink), datos),
    recordatorio30Minutos: (lead, datos) =>
      M.recordatorio30Minutos(contexto(lead, calendlyLink), datos),
  };
}

module.exports = {
  crearTextosLead,
  clasificar,
  necesidadCorta,
  fichaAM: M.fichaAM,
  avisoRespuesta: M.avisoRespuesta,
  avisoContactoNuevo: M.avisoContactoNuevo,
  avisoSinRespuesta: M.avisoSinRespuesta,
  avisoDerivacion: M.avisoDerivacion,
  avisoReunionAgendada: M.avisoReunionAgendada,
  avisoSigueEscribiendo: M.avisoSigueEscribiendo,
  resumenEmbudo: M.resumenEmbudo,
};
