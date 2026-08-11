'use strict';

const { S, ESTADOS_CON_OPCIONES, PALABRAS_GLOBALES } = require('./states');
const { TRANSICIONES, OPCIONES, CAMPO_RESPUESTA } = require('./transitions');

const MAX_REINTENTOS = 4;

function normalizar(texto) {
  return String(texto || '')
    .trim()
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '');
}

function palabraGlobal(entrada) {
  for (const [palabra, destino] of Object.entries(PALABRAS_GLOBALES)) {
    if (entrada.includes(palabra)) return destino;
  }
  return null;
}

/**
 * Motor del embudo. Portado de bot/src/fsm/engine.js con dos cambios:
 *
 * - Sin Redis: el estado y el contador de reintentos van en la fila del lead,
 *   que pasa a ser la unica fuente de verdad. Antes habia dos (Redis y Postgres)
 *   y el codigo tenia que elegir cual creer.
 * - No manda mensajes directo: los encola, asi pasan por los mismos delays y
 *   limites que el resto del servicio.
 */
function crearEmbudo({ repo, cola, textos, scorer, logger, crmNotify = null, ahora = () => new Date() }) {

  function decir(lead, texto) {
    cola.encolar({ to: lead.telefono, texto, kind: 'manual', leadId: lead.id });
  }

  /**
   * La normalizacion existe para enrutar ("1", "menu", "baja"), no para
   * guardar: los campos de texto libre se persisten crudos. El original
   * guardaba la version normalizada, asi que "la atención de mañana" quedaba
   * como "la atencion de manana" — degradado justo en el texto que despues
   * alimenta el prompt del scoring y se le muestra al AM.
   */
  function guardarRespuesta(lead, estado, entrada, crudo) {
    const campo = CAMPO_RESPUESTA[estado];
    if (!campo || !entrada) return;

    if (campo.numerico) {
      const valor = parseInt(entrada, 10);
      if (Number.isNaN(valor)) return;
      repo.actualizarFunnel(lead.id, { [campo.campo]: valor });
      return;
    }

    const texto = String(crudo ?? entrada).trim();
    if (texto) repo.actualizarFunnel(lead.id, { [campo.campo]: texto });
  }

  async function alEntrar(lead, estado, entrada) {
    switch (estado) {
      case S.MENU:
        decir(lead, textos.MENU(lead.business_name || lead.nombre));
        return estado;

      case S.QUAL_0: decir(lead, textos.QUAL_0); return estado;
      case S.QUAL_1: decir(lead, textos.QUAL_1); return estado;
      case S.QUAL_2: decir(lead, textos.QUAL_2); return estado;
      case S.QUAL_3: decir(lead, textos.QUAL_3); return estado;
      case S.QUAL_4: decir(lead, textos.QUAL_4); return estado;
      case S.QUAL_5: decir(lead, textos.QUAL_5); return estado;
      case S.QUAL_6: decir(lead, textos.QUAL_6); return estado;

      case S.SCORED: {
        const fresco = repo.leadPorId(lead.id);
        const r = await scorer.calificar(fresco);
        repo.actualizarFunnel(lead.id, {
          score: r.score, priority: r.priority, score_reason: r.reason,
        });
        logger?.info({ leadId: lead.id, score: r.score, accion: r.recommended_action }, 'lead calificado');

        if (r.recommended_action === 'meeting') {
          decir(lead, textos.MEETING_OFFER(fresco.business_name || fresco.nombre));
          return S.MEETING_SENT;
        }
        if (r.recommended_action === 'nurture') {
          decir(lead, textos.NURTURE);
          return S.NURTURE;
        }
        decir(lead, textos.DISQUALIFIED);
        return S.DISQUALIFIED;
      }

      case S.MEETING_SENT:
        decir(lead, entrada === '2' ? textos.MORE_INFO : textos.MEETING_LINK);
        return estado;

      case S.SCHEDULED:
        decir(lead, textos.SCHEDULED);
        return estado;

      case S.HUMAN_QUEUED:
        if (!lead.human_requested) {
          repo.actualizarFunnel(lead.id, { human_requested: 1 });
          decir(lead, textos.HUMAN_QUEUED);
          logger?.info({ leadId: lead.id, estadoPrevio: lead.fsm_state }, 'lead pide humano');
        }
        return estado;

      case S.OPT_OUT:
        repo.actualizarFunnel(lead.id, { opt_out: 1 });
        repo.actualizarLead(lead.id, { status: 'closed' });
        repo.cancelarJobs(lead.id, 'followup');
        decir(lead, textos.OPT_OUT);
        return estado;

      case S.NURTURE:
      case S.DISQUALIFIED:
        return estado; // el mensaje ya salio desde SCORED

      default:
        decir(lead, textos.MENU(lead.nombre));
        return S.MENU;
    }
  }

  return {
    /**
     * Procesa un mensaje entrante del lead.
     * @returns {string|null} el estado en que quedo, o null si se ignoro.
     */
    async procesar(leadId, textoCrudo) {
      const lead = repo.leadPorId(leadId);
      if (!lead) return null;

      const entrada = normalizar(textoCrudo);

      // Dado de baja: silencio absoluto.
      if (lead.opt_out) return null;

      const global = palabraGlobal(entrada);

      // Con un humano a cargo, lo unico que reactiva el bot es pedir el menu.
      if (lead.human_requested) {
        if (global !== S.MENU) return null;
        repo.actualizarFunnel(lead.id, { human_requested: 0 });
        return this._transicionar(lead, entrada, S.MENU);
      }

      if (global) return this._transicionar(lead, entrada, global);

      const actual = lead.fsm_state || S.NEW;
      const mapa = TRANSICIONES[actual] || {};
      const siguiente = mapa[entrada] ?? mapa['*'];

      if (!siguiente) return this._transicionar(lead, entrada, S.MENU);

      // Respuesta invalida en un estado que espera un numero.
      if (ESTADOS_CON_OPCIONES.has(actual) && siguiente === actual) {
        const reintentos = (lead.fsm_retries || 0) + 1;

        if (reintentos >= MAX_REINTENTOS) {
          repo.actualizarFunnel(lead.id, { fsm_retries: 0 });
          return this._transicionar(lead, entrada, S.HUMAN_QUEUED);
        }

        repo.actualizarFunnel(lead.id, { fsm_retries: reintentos });
        decir(lead, reintentos === 1 ? textos.INVALID_1(OPCIONES[actual] || '') : textos.INVALID_2);
        return actual;
      }

      guardarRespuesta(lead, actual, entrada, textoCrudo);
      repo.actualizarFunnel(lead.id, { fsm_retries: 0 });
      return this._transicionar(lead, entrada, siguiente);
    },

    async _transicionar(lead, entrada, destino) {
      const final = await alEntrar(repo.leadPorId(lead.id), destino, entrada);
      repo.actualizarFunnel(lead.id, { fsm_state: final });

      // El CRM se entera cuando el lead califica o pide un humano — los dos
      // momentos en que alguien del equipo tiene que hacer algo.
      if (crmNotify && (final === S.MEETING_SENT || final === S.HUMAN_QUEUED)) {
        await crmNotify.leadCalifico(lead.id);
      }
      return final;
    },
  };
}

module.exports = { crearEmbudo, MAX_REINTENTOS, normalizar };
