'use strict';

const { S, ESTADOS_CON_OPCIONES, PALABRAS_GLOBALES } = require('./states');
const { TRANSICIONES, OPCIONES, CAMPO_RESPUESTA } = require('./transitions');
const { primerNombre } = require('../telefono');
const plantillas = require('../templates');
const { detectar, ETIQUETA } = require('./derivacion');

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
function crearEmbudo({ repo, cola, textos, scorer, logger, cfg = { amPhones: [] }, crmNotify = null, ahora = () => new Date() }) {

  /**
   * Deriva a un humano y le manda el contexto: quien es, por que, y los ultimos
   * mensajes. Sin el historial, quien atiende arranca a ciegas.
   */
  function derivar(lead, motivo) {
    // Solo el motivo: el flag human_requested lo pone el handler de
    // HUMAN_QUEUED. Si se marcara aca, ese handler creeria que ya estaba
    // derivado y no le avisaria al lead que lo estan pasando con alguien.
    repo.actualizarFunnel(lead.id, { motivo_derivacion: motivo });
    for (const am of cfg.amPhones) {
      cola.encolar({
        to: am,
        texto: plantillas.avisoDerivacion(repo.leadPorId(lead.id), {
          motivo: ETIQUETA[motivo] || motivo,
          historial: repo.ultimosMensajes(lead.id, 6),
        }),
        kind: 'am_notice',
        leadId: lead.id,
      });
    }
    logger?.info({ leadId: lead.id, motivo }, 'conversacion derivada a un humano');
  }

  /** El AM se entera de como termino el embudo, gane o pierda. */
  function avisarDesenlace(leadId, desenlace) {
    const fresco = repo.leadPorId(leadId);
    for (const am of cfg.amPhones) {
      cola.encolar({
        to: am, texto: plantillas.resumenEmbudo(fresco, desenlace),
        kind: 'am_notice', leadId,
      });
    }
  }

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
    if (!texto) return;

    // El rubro se clasifica al guardarlo, igual que cuando llega del formulario:
    // rubro_norm es lo que elige el gancho del follow-up. Sin esto, al lead que
    // escribio directo al WhatsApp se le manda siempre el texto generico.
    if (campo.campo === 'rubro') {
      repo.actualizarFunnel(lead.id, { rubro: texto, rubro_norm: plantillas.clasificar(texto) });
      return;
    }
    repo.actualizarFunnel(lead.id, { [campo.campo]: texto });
  }

  async function alEntrar(lead, estado, entrada) {
    switch (estado) {
      case S.MENU:
        // Se saluda a la PERSONA, no a la empresa. Con business_name adelante,
        // despues de la pregunta del negocio el menu decia "Hola Inmobiliaria
        // Pereyra". El nombre de la empresa es dato para el CRM, no un saludo.
        decir(lead, textos.MENU(primerNombre(lead.nombre)));
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

        avisarDesenlace(lead.id, r.recommended_action);

        if (r.recommended_action === 'meeting') {
          decir(lead, textos.MEETING_OFFER(primerNombre(fresco.nombre)));
          return S.MEETING_SENT;
        }
        // El mensaje lo manda el handler del estado destino, no este: a NURTURE
        // tambien se llega desde MEETING_INFO, y si el texto saliera solo desde
        // aca ese camino terminaba en silencio.
        if (r.recommended_action === 'nurture') return alEntrar(fresco, S.NURTURE, entrada);
        return alEntrar(fresco, S.DISQUALIFIED, entrada);
      }

      case S.MEETING_SENT:
        decir(lead, textos.MEETING_LINK);
        return estado;

      case S.MEETING_INFO:
        decir(lead, textos.MORE_INFO);
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
        // lead.fsm_state es todavia el estado anterior: _transicionar lo guarda
        // recien despues. Al que califico y dijo "todavia no" no se le contesta
        // que su caso "se va a mirar a ver si encaja" — ya encajo, lo que falta
        // es el momento.
        decir(lead, lead.fsm_state === S.MEETING_INFO ? textos.NOT_NOW : textos.NURTURE);
        return estado;

      case S.DISQUALIFIED:
        decir(lead, textos.DISQUALIFIED);
        return estado;

      default:
        decir(lead, textos.MENU(primerNombre(lead.nombre)));
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

      if (global) {
        if (global === S.HUMAN_QUEUED) derivar(lead, 'pedido');
        return this._transicionar(lead, entrada, global);
      }

      // Casos que el superprompt manda derivar sin excepcion. Van antes de la
      // tabla de transiciones: aplican en cualquier punto del embudo.
      const disparador = detectar(textoCrudo);
      if (disparador) {
        if (disparador.motivo === 'queja') {
          decir(lead, textos.QUEJA);
          derivar(lead, 'queja');
          return this._transicionar(lead, entrada, S.HUMAN_QUEUED);
        }

        if (disparador.motivo === 'facturacion') {
          decir(lead, textos.FACTURACION);
          derivar(lead, 'facturacion');
          return this._transicionar(lead, entrada, S.HUMAN_QUEUED);
        }

        // Precio: la primera vez se contesta el criterio, sin dar numeros. Si
        // vuelve a preguntar es que no se conformo, y ahi va a un humano.
        const consultas = (lead.consultas_precio || 0) + 1;
        repo.actualizarFunnel(lead.id, { consultas_precio: consultas });

        if (consultas === 1) {
          decir(lead, textos.PRECIO);
          return lead.fsm_state || S.NEW;
        }
        derivar(lead, 'precio');
        return this._transicionar(lead, entrada, S.HUMAN_QUEUED);
      }

      const actual = lead.fsm_state || S.NEW;
      const mapa = TRANSICIONES[actual] || {};
      const siguiente = mapa[entrada] ?? mapa['*'];

      if (!siguiente) return this._transicionar(lead, entrada, S.MENU);

      // Respuesta invalida en un estado que espera un numero.
      if (ESTADOS_CON_OPCIONES.has(actual) && siguiente === actual) {
        const reintentos = (lead.fsm_retries || 0) + 1;

        if (reintentos >= MAX_REINTENTOS) {
          repo.actualizarFunnel(lead.id, { fsm_retries: 0 });
          derivar(lead, 'invalidos');
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
