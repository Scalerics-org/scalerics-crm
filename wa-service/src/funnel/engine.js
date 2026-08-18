'use strict';

const { S, palabraGlobal } = require('./states');
const { TRANSICIONES } = require('./transitions');
const plantillas = require('../templates');
const { detectar, ETIQUETA } = require('./derivacion');

/**
 * "ya agende", "ya reserve". En primera persona y en pasado a proposito: con
 * "agendamos?" —que lo dice el que todavia NO reservo— seria justo al reves.
 * La entrada llega normalizada, sin acentos.
 */
const YA_AGENDO = /\b(ya\s+)?(agende|reserve|coordine|saque\s+(el\s+)?turno|lo\s+saque)\b/;

function normalizar(texto) {
  return String(texto || '')
    .trim()
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '');
}

/**
 * Estados donde la IA todavia esta averiguando. Al salir de aca se califica y
 * se ofrece —o no— la reunion, y esa decision es del score, no del modelo.
 */
const FASE_CALIFICACION = new Set([S.NEW, S.CONVERSANDO, S.NURTURE, S.DISQUALIFIED]);

/**
 * De la oferta en adelante la IA sigue conversando pero ya no puede volver a
 * ofrecer: el salto a SCORED solo corre mientras califica. Conversar no es
 * decidir.
 */
const FASE_CIERRE = new Set([S.MEETING_SENT, S.MEETING_INFO, S.SCHEDULED]);

/**
 * Motor del embudo.
 *
 * Ya no decide QUE decir —eso lo escribe la IA— sino CUANDO decir algo y a
 * quien le toca: al modelo o a una persona. Las decisiones que quedaron en
 * codigo son las que no pueden depender de que un modelo obedezca una
 * instruccion: la baja, la derivacion por queja o facturacion, el limite de una
 * sola consulta de precio, y que la reunion se ofrezca por score.
 */
function crearEmbudo({
  repo, cola, textos, scorer, logger, cfg = { amPhones: [] },
  crmNotify = null, agente = null, redactor = null, agenda = null, ahora = () => new Date(),
}) {
  const CALENDLY = cfg.CALENDLY_LINK || '';

  function decir(lead, texto) {
    cola.encolar({ to: lead.telefono, texto, kind: 'manual', leadId: lead.id });
  }

  /**
   * Le pide a la IA el mensaje de una situacion y lo manda.
   * @returns {Promise<boolean>} false si no se pudo escribir.
   */
  async function decirIA(lead, situacion, extra = '') {
    const texto = await redactor?.escribir(lead, situacion, extra);
    if (!texto) return false;
    decir(lead, texto);
    return true;
  }

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
          historial: repo.ultimosMensajes(lead.id, 6, lead.conversacion_desde),
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

  /**
   * Persiste lo que extrajo la IA. El rubro se clasifica al guardarlo, igual
   * que cuando llega del formulario: rubro_norm es lo que elige el gancho.
   */
  function guardarCampos(leadId, campos) {
    const conNorm = campos.rubro
      ? { ...campos, rubro_norm: plantillas.clasificar(campos.rubro) }
      : campos;
    repo.actualizarFunnel(leadId, conNorm);
  }

  /** Los horarios que se le mostraron, tal como quedaron guardados. */
  function leerHorarios(lead) {
    try {
      return JSON.parse(lead.horarios_ofrecidos || '[]').map((s) => new Date(s));
    } catch (e) {
      return [];
    }
  }

  /**
   * Los horarios en palabras, para que el modelo los escriba y para que sepa
   * cuales son los validos. Se le pasan como contexto, no como texto final.
   */
  function describirHorarios({ slots }) {
    const dia = new Intl.DateTimeFormat('es-UY', {
      timeZone: cfg.TZ, weekday: 'long', day: 'numeric', month: 'long',
    }).format(slots[0]);
    const horas = slots.map((d) => new Intl.DateTimeFormat('es-UY', {
      timeZone: cfg.TZ, hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(d));
    return `Horarios libres para el ${dia}: ${horas.join(', ')}. Son los únicos que podés ofrecer.`;
  }

  /**
   * Cual de los horarios ofrecidos eligio. Lo interpreta el modelo —entiende
   * "las 13", "la primera", "a la una y media"— pero solo puede devolver uno de
   * los que existen: la lista va como enum, asi que no puede inventar una hora.
   */
  async function elegirHorario(lead, entrada, ofrecidos) {
    if (!agente?.activo) return null;

    const opciones = ofrecidos.map((d) => d.toISOString());
    const etiquetas = ofrecidos.map((d) => new Intl.DateTimeFormat('es-UY', {
      timeZone: cfg.TZ, hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(d));

    const elegido = await agente.elegirDeLista({
      texto: entrada,
      opciones,
      etiquetas,
      instruccion: 'El lead esta eligiendo uno de los horarios que se le ofrecieron. Devolvé cual, o "ninguno" si no se entiende o si contesta otra cosa.',
    });
    if (!elegido) return null;

    const i = opciones.indexOf(elegido);
    return i >= 0 ? ofrecidos[i] : null;
  }

  /** Deja la reunion registrada: recordatorios, aviso al AM y estado. */
  function servicioReunion(lead, r) {
    repo.registrarReunion(lead.id, {
      meetingTime: r.inicio.toISOString(),
      meetingUrl: r.meetUrl,
      ahoraIso: ahora().toISOString(),
    });
    repo.actualizarFunnel(lead.id, { meeting_event_id: r.eventId || null });

    for (const am of cfg.amPhones) {
      cola.encolar({
        to: am,
        texto: plantillas.avisoReunionAgendada(repo.leadPorId(lead.id), {
          cuando: new Intl.DateTimeFormat('es-UY', {
            timeZone: cfg.TZ, weekday: 'long', day: 'numeric', month: 'long',
            hour: '2-digit', minute: '2-digit', hour12: false,
          }).format(r.inicio),
          link: r.meetUrl,
        }),
        kind: 'am_notice',
        leadId: lead.id,
      });
    }
    logger?.info({ leadId: lead.id, cuando: r.inicio.toISOString() }, 'reunion agendada por el bot');
  }

  /**
   * La IA no pudo escribir y hay alguien esperando. No se disimula con un texto
   * armado: se lo pasa a una persona. Es la unica salida honesta sin el modelo.
   */
  function sinIA(lead, motivo) {
    logger?.warn({ leadId: lead.id, motivo }, 'la IA no respondio, va a una persona');
    derivar(lead, 'sin_ia');
    repo.actualizarFunnel(lead.id, { human_requested: 1, fsm_state: S.HUMAN_QUEUED });
    decir(lead, textos.SIN_IA);
    return S.HUMAN_QUEUED;
  }

  async function alEntrar(lead, estado, entrada) {
    switch (estado) {
      case S.SCORED: {
        const fresco = repo.leadPorId(lead.id);
        const r = await scorer.calificar(fresco);
        repo.actualizarFunnel(lead.id, {
          score: r.score, priority: r.priority, score_reason: r.reason,
        });
        logger?.info({ leadId: lead.id, score: r.score, accion: r.recommended_action }, 'lead calificado');

        avisarDesenlace(lead.id, r.recommended_action);

        const destino = {
          meeting: S.MEETING_SENT,
          nurture: S.NURTURE,
        }[r.recommended_action] || S.DISQUALIFIED;

        return alEntrar(fresco, destino, entrada);
      }

      case S.MEETING_SENT: {
        // Con agenda conectada se le muestran horarios reales en vez de un
        // link: elegir entre cinco opciones es mas facil que abrir una pagina,
        // y el que abre una pagina muchas veces no vuelve.
        const libres = agenda?.activo ? await agenda.horariosDisponibles(ahora()) : null;

        if (libres?.slots?.length) {
          const iso = libres.slots.map((d) => d.toISOString());
          repo.actualizarFunnel(lead.id, { horarios_ofrecidos: JSON.stringify(iso) });
          if (!await decirIA(lead, 'oferta_con_horarios', describirHorarios(libres))) {
            return sinIA(lead, 'oferta_con_horarios');
          }
          return S.HORARIOS_OFRECIDOS;
        }

        // Sin agenda —o sin ningun hueco— se ofrece la reunion sin horarios y
        // se sigue por el camino del link, que es el que ya existia.
        if (!await decirIA(lead, 'oferta_reunion')) return sinIA(lead, 'oferta_reunion');
        return estado;
      }

      /**
       * Eligio —o no— uno de los horarios que se le mostraron.
       *
       * Cual eligio lo resuelve el modelo, que entiende "las 13" y "la de la
       * una y media"; que ese horario exista y siga libre lo verifica el
       * codigo. El modelo interpreta, el codigo confirma.
       */
      case S.HORARIOS_OFRECIDOS: {
        const ofrecidos = leerHorarios(lead);
        if (!ofrecidos.length) return alEntrar(lead, S.MEETING_SENT, entrada);

        const elegido = await elegirHorario(lead, entrada, ofrecidos);
        if (!elegido) {
          // No se entendio cual quiere. Se le vuelve a preguntar con los mismos
          // horarios: pedirle que elija de nuevo es mejor que agendar el que no era.
          if (!await decirIA(lead, 'horario_no_entendido', describirHorarios({ slots: ofrecidos })))
            return sinIA(lead, 'horario_no_entendido');
          return estado;
        }

        const r = await agenda.reservar({
          inicio: elegido,
          nombre: lead.business_name || lead.nombre,
          telefono: lead.telefono,
          resumen: lead.needs || lead.necesidad || '',
        });

        if (r.motivo === 'ocupado') {
          // Se lo tomaron entre medio. Se buscan nuevos y se le explica.
          const nuevos = await agenda.horariosDisponibles(ahora());
          if (nuevos?.slots?.length) {
            repo.actualizarFunnel(lead.id, {
              horarios_ofrecidos: JSON.stringify(nuevos.slots.map((d) => d.toISOString())),
            });
            await decirIA(lead, 'horario_ocupado', describirHorarios(nuevos));
            return estado;
          }
          derivar(lead, 'agenda');
          return alEntrar(lead, S.HUMAN_QUEUED, entrada);
        }

        if (!r.ok) {
          // Falló la agenda. No se le promete nada: va a una persona.
          derivar(lead, 'agenda');
          return alEntrar(lead, S.HUMAN_QUEUED, entrada);
        }

        servicioReunion(lead, r);
        return alEntrar(repo.leadPorId(lead.id), S.SCHEDULED, entrada);
      }

      case S.MEETING_INFO:
        if (!await decirIA(lead, 'mas_info')) return sinIA(lead, 'mas_info');
        return estado;

      /**
       * Se llega aca la primera vez para mandar el link, y despues cada vez que
       * el lead escribe teniendolo. La diferencia importa: lead.fsm_state es
       * todavia el estado anterior, asi que se sabe si recien entro o si esta
       * insistiendo.
       */
      case S.MEETING_LINK_SENT: {
        if (lead.fsm_state !== S.MEETING_LINK_SENT) {
          if (!await decirIA(lead, 'link_reunion')) return sinIA(lead, 'link_reunion');
          repo.actualizarFunnel(lead.id, { fsm_retries: 0 });
          return estado;
        }

        if (YA_AGENDO.test(entrada)) {
          // No se cancela el follow-up: la verdad la trae el webhook de
          // Calendly. Si de veras reservo, ese lo cancela; si se confundio, el
          // follow-up es exactamente lo que hay que mandarle.
          if (!await decirIA(lead, 'ya_agendo')) return sinIA(lead, 'ya_agendo');
          repo.actualizarFunnel(lead.id, { fsm_retries: 0 });
          return estado;
        }

        // Ya tiene el link y sigue escribiendo: quiere otra cosa. Se le
        // pregunta una vez y despues va a una persona.
        const insistencias = (lead.fsm_retries || 0) + 1;
        if (insistencias >= 2) {
          derivar(lead, 'post_oferta');
          return alEntrar(lead, S.HUMAN_QUEUED, entrada);
        }
        repo.actualizarFunnel(lead.id, { fsm_retries: insistencias });
        if (!await decirIA(lead, 'ya_tiene_link')) return sinIA(lead, 'ya_tiene_link');
        return estado;
      }

      case S.SCHEDULED: {
        // Solo al confirmarse. Repetirlo en cada mensaje posterior es el loop
        // que tenia MEETING_SENT.
        if (lead.fsm_state === S.SCHEDULED) return estado;

        const cuando = lead.meeting_time
          ? new Intl.DateTimeFormat('es-UY', {
            timeZone: cfg.TZ, weekday: 'long', day: 'numeric', month: 'long',
            hour: '2-digit', minute: '2-digit', hour12: false,
          }).format(new Date(lead.meeting_time))
          : '';
        const extra = cuando
          ? `Quedó agendada para el ${cuando}. El link de la videollamada es ${lead.meeting_url || '(sin link)'}`
          : '';

        await decirIA(lead, lead.meeting_event_id ? 'reunion_agendada' : 'reunion_confirmada', extra);
        return estado;
      }

      case S.HUMAN_QUEUED:
        if (!lead.human_requested) {
          repo.actualizarFunnel(lead.id, { human_requested: 1 });
          if (!await decirIA(lead, 'derivacion')) decir(lead, textos.SIN_IA);
          logger?.info({ leadId: lead.id, estadoPrevio: lead.fsm_state }, 'lead pasa a una persona');
        }
        return estado;

      case S.OPT_OUT:
        repo.actualizarFunnel(lead.id, { opt_out: 1 });
        repo.actualizarLead(lead.id, { status: 'closed' });
        repo.cancelarJobs(lead.id, 'followup');
        // Este es fijo a proposito: tiene que salir aunque no haya IA, y no se
        // le da al modelo la chance de intentar retenerlo.
        decir(lead, textos.OPT_OUT);
        return estado;

      case S.NURTURE:
        // lead.fsm_state es todavia el estado anterior. Al que califico y dijo
        // "todavia no" no se le contesta que su caso "se va a mirar a ver si
        // encaja" — ya encajo, lo que falta es el momento.
        await decirIA(lead, lead.fsm_state === S.MEETING_INFO ? 'no_ahora' : 'nurture');
        return estado;

      case S.DISQUALIFIED:
        await decirIA(lead, 'descartado');
        return estado;

      default:
        return estado;
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

      // Con un humano a cargo, el bot no vuelve solo. Lo devuelve el CRM con
      // POST /api/leads/phone/:phone/release.
      if (lead.human_requested) return null;

      if (global) {
        if (global === S.HUMAN_QUEUED) derivar(lead, 'pedido');
        return this._transicionar(lead, entrada, global);
      }

      // Casos que el superprompt manda derivar sin excepcion. Van antes de la
      // IA: aplican en cualquier punto y no se delegan.
      const disparador = detectar(textoCrudo);
      if (disparador) {
        if (disparador.motivo === 'queja') {
          // Una queja no la escribe el modelo si puede evitarse, pero tampoco
          // se calla: si no hay IA, el texto de derivacion alcanza.
          derivar(lead, 'queja');
          return this._transicionar(lead, entrada, S.HUMAN_QUEUED);
        }

        if (disparador.motivo === 'facturacion') {
          if (!await decirIA(lead, 'facturacion')) decir(lead, textos.SIN_IA);
          derivar(lead, 'facturacion');
          return this._transicionar(lead, entrada, S.HUMAN_QUEUED);
        }

        // Precio: la primera vez se contesta el criterio, sin dar numeros. Si
        // vuelve a preguntar es que no se conformo, y ahi va a un humano.
        const consultas = (lead.consultas_precio || 0) + 1;
        repo.actualizarFunnel(lead.id, { consultas_precio: consultas });

        if (consultas === 1) {
          // Si la IA no puede, sale el texto del superprompt tal cual: es el
          // unico mensaje donde las palabras exactas estan dictadas.
          if (!await decirIA(lead, 'precio')) decir(lead, textos.PRECIO);
          return lead.fsm_state || S.NEW;
        }
        derivar(lead, 'precio');
        return this._transicionar(lead, entrada, S.HUMAN_QUEUED);
      }

      const actual = lead.fsm_state || S.NEW;
      const califica = FASE_CALIFICACION.has(actual);

      if (agente?.activo && (califica || FASE_CIERRE.has(actual))) {
        const r = await agente.responder(lead, textoCrudo, repo.ultimosMensajes(lead.id, 20, lead.conversacion_desde), actual);
        if (r) return this._conversar(lead, entrada, r, { actual, puedeCerrar: califica });
        return sinIA(lead, 'conversacion');
      }

      // Sin IA no hay embudo que lo atienda: la unica salida es una persona.
      if (!agente?.activo) return sinIA(lead, 'sin_clave');

      const mapa = TRANSICIONES[actual] || {};
      return this._transicionar(lead, entrada, mapa['*'] || S.CONVERSANDO);
    },

    /**
     * Un turno conducido por la IA: guarda lo que extrajo y contesta. Cuando ya
     * no falta ningun dato, el cierre lo hace el codigo — ofrecer la reunion
     * sale del score, no de lo que le parezca al modelo.
     */
    async _conversar(lead, entrada, { texto, datos }, { actual, puedeCerrar }) {
      if (Object.keys(datos).length) {
        guardarCampos(lead.id, datos);
        logger?.info({ leadId: lead.id, campos: Object.keys(datos) }, 'la IA extrajo datos');
      }

      if (puedeCerrar) {
        const fresco = repo.leadPorId(lead.id);
        if (!agente.faltantes(fresco).length) return this._transicionar(fresco, entrada, S.SCORED);
      }

      decir(lead, texto);

      // Quien decide mandar el link es la IA —lo tiene en las instrucciones de
      // su etapa— pero quien se entera de que salio tiene que ser el codigo.
      // Si no, nadie sabe que el lead ya lo tiene y se lo puede volver a
      // mandar indefinidamente, que es justo el loop que habia antes.
      const mandoElLink = CALENDLY && texto.includes(CALENDLY);
      const destino = mandoElLink ? S.MEETING_LINK_SENT : (puedeCerrar ? S.CONVERSANDO : actual);
      repo.actualizarFunnel(lead.id, { fsm_state: destino, fsm_retries: 0 });
      return destino;
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

module.exports = { crearEmbudo, normalizar, FASE_CALIFICACION, FASE_CIERRE };
