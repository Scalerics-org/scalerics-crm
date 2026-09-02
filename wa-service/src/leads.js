'use strict';

const { normalizar } = require('./telefono');
const { botActivo, pausarHasta, HORAS_PAUSA } = require('./funnel/pausa');
const plantillas = require('./templates');
const { entre } = require('./outbound/queue');
const { correspondeDerivar } = require('./funnel/abandono');

/**
 * Orquesta el alta de un lead: ficha al AM, bienvenida al lead y follow-up
 * programado. Es el corazon del servicio.
 */
function crearServicioLeads({ repo, cola, cfg, logger, textos, redactor = null, embudo = null, scheduler = null, ahora = () => new Date() }) {

  function fechaLegible(d) {
    return d.toLocaleString('es-UY', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
      timeZone: cfg.TZ,
    });
  }

  /**
   * El lead esta con una persona y sigue escribiendo: que alguien se entere.
   *
   * Que el bot se calle es correcto —dos voces contestando lo mismo es peor que
   * una— pero callarse Y no avisar convierte el traspaso en un pozo. Paso de
   * verdad: un lead derivado escribio seis dias despues y del lado de adentro
   * no quedo mas rastro que una linea de log.
   *
   * Con tope: el que manda cuatro mensajes seguidos no necesita cuatro avisos.
   */
  function avisarQueSigueEscribiendo(lead, texto) {
    if (!cfg.AVISO_HUMANO_MINUTOS || !cfg.amPhones.length) return;

    const ultimo = lead.humano_avisado_at ? Date.parse(lead.humano_avisado_at) : 0;
    if (ahora().getTime() - ultimo < cfg.AVISO_HUMANO_MINUTOS * 60_000) return;

    // created_at viene como 'YYYY-MM-DD HH:MM:SS' y es UTC, pero sin decirlo:
    // parsearlo tal cual lo toma como hora local y da unas horas de error.
    const desde = repo.ultimoSalienteAl(lead.id);
    const horas = desde
      ? Math.max(0, Math.round((ahora().getTime() - Date.parse(`${desde.replace(' ', 'T')}Z`)) / 3600_000))
      : 0;

    for (const am of cfg.amPhones) {
      cola.encolar({
        to: am,
        texto: plantillas.avisoSigueEscribiendo(lead, texto, horas),
        kind: 'am_notice',
        leadId: lead.id,
      });
    }
    repo.actualizarLead(lead.id, { humano_avisado_at: ahora().toISOString() });
    logger?.info({ leadId: lead.id, horas }, 'lead derivado que sigue escribiendo: se le avisa al equipo');
  }

  /**
   * Arranca de nuevo el reloj del abandono, o lo apaga si ya no corresponde.
   *
   * Va al final de cada turno entrante, que es el unico punto por donde pasan
   * todos los caminos. Que se reinicie en cada mensaje es lo importante: el
   * reloj mide silencio desde lo ultimo que se hablo, no desde el principio de
   * la conversacion.
   *
   * Solo para quien contesto al menos una vez —esto corre desde
   * registrarRespuesta—: al que nunca dijo nada no se lo puede derivar por
   * irse de una conversacion que no tuvo. De ese se ocupa el seguimiento de
   * las 72 horas.
   */
  function armarAbandono(lead) {
    if (!cfg.ABANDONO_MINUTOS || !lead) return;

    if (!correspondeDerivar(lead)) {
      repo.cancelarJobs(lead.id, 'abandono');
      return;
    }

    const cuando = new Date(ahora().getTime() + cfg.ABANDONO_MINUTOS * 60_000);
    repo.programarJob(lead.id, 'abandono', cuando.toISOString());
  }

  /**
   * @param {string|null} telefonoE164 null si no se pudo normalizar. No alcanza
   *   con mirar lead.telefono: ahi se guarda el crudo justamente cuando fallo.
   */
  function notificarAM(lead, telefonoE164, telefonoCrudo) {
    if (!cfg.amPhones.length) {
      logger?.warn('AM_PHONES vacio: el lead no se notifica a nadie');
      return;
    }
    const texto = plantillas.fichaAM({
      nombre: lead.nombre,
      rubro: lead.rubro,
      necesidad: lead.necesidad,
      origen: lead.origen,
      telefonoLegible: telefonoE164 ? `+${telefonoE164}` : `${telefonoCrudo} (no reconocido)`,
      fechaLegible: fechaLegible(ahora()),
    }, {
      // Sin numero valido no hay link: un wa.me roto es peor que no ponerlo.
      waLink: telefonoE164 ? `wa.me/${telefonoE164}` : '⚠️ el teléfono no se pudo interpretar, hay que contactarlo a mano',
    });

    for (const am of cfg.amPhones) {
      // Prioridad alta y casi sin demora: el AM tiene que enterarse ya.
      cola.encolar({
        to: am, texto, kind: 'am_notice', leadId: lead.id,
        delayMs: entre(cfg.DELAY_AM_MIN_MS, cfg.DELAY_AM_MAX_MS),
      });
    }
    repo.actualizarLead(lead.id, { am_notified_at: ahora().toISOString() });
  }

  return {
    /**
     * Alta de un lead nuevo. Idempotente por external_id.
     * @returns {{lead: object, yaExistia: boolean, welcomeEnSegundos: number|null}}
     */
    async alta(datos) {
      if (datos.external_id) {
        const previo = repo.leadPorExternalId(datos.external_id);
        if (previo) return { lead: previo, yaExistia: true, welcomeEnSegundos: null };
      }

      const telefono = normalizar(datos.telefono, cfg.DEFAULT_COUNTRY_CODE);
      const rubroNorm = plantillas.clasificar(datos.rubro);

      const lead = repo.crearLead({
        external_id: datos.external_id,
        nombre: datos.nombre,
        rubro: datos.rubro,
        rubro_norm: rubroNorm,
        // Si no se pudo normalizar se guarda lo que vino: el lead no se pierde.
        telefono: telefono || String(datos.telefono || '').trim(),
        necesidad: datos.necesidad,
        origen: datos.origen,
        status: telefono ? 'new' : 'failed',
      });

      // La ficha al AM sale siempre, incluso si el telefono del lead es basura:
      // que un dato venga mal no puede hacer que el lead se pierda.
      notificarAM(lead, telefono, datos.telefono);

      if (!telefono) {
        logger?.warn({ leadId: lead.id }, 'telefono no normalizable: no se le escribe al lead');
        return { lead, yaExistia: false, welcomeEnSegundos: null };
      }

      // El saludo es fijo por decision del negocio. Todo lo que viene despues
      // —cada pregunta, cada respuesta— lo sigue escribiendo el modelo.
      const delay = entre(cfg.DELAY_WELCOME_MIN_MS, cfg.DELAY_WELCOME_MAX_MS);
      cola.encolar({
        to: telefono,
        texto: textos.BIENVENIDA,
        kind: 'welcome',
        leadId: lead.id,
        delayMs: delay,
      });
      repo.actualizarLead(lead.id, { status: 'welcomed', welcomed_at: ahora().toISOString() });

      // Follow-up a las 24h con jitter, para que no salgan todos a la misma hora.
      const jitterMs = entre(-cfg.FOLLOWUP_JITTER_MINUTES, cfg.FOLLOWUP_JITTER_MINUTES) * 60_000;
      const runAt = new Date(ahora().getTime() + cfg.FOLLOWUP_DELAY_HOURS * 3600_000 + jitterMs);
      repo.encolarJob(lead.id, 'followup', runAt.toISOString());

      return {
        lead: repo.leadPorId(lead.id),
        yaExistia: false,
        welcomeEnSegundos: Math.round(delay / 1000),
      };
    },

    /**
     * El lead agendo la consultoria en Calendly. Cancela el follow-up —solo se
     * insiste a quien NO agendo— y programa los recordatorios.
     */
    registrarReunion(leadId, { meeting_time, meeting_url }) {
      const lead = repo.registrarReunion(leadId, {
        meetingTime: meeting_time,
        meetingUrl: meeting_url,
        ahoraIso: ahora().toISOString(),
      });

      const recordatorios = scheduler ? scheduler.programarRecordatorios(lead, ahora()) : [];

      const cuando = new Date(meeting_time).toLocaleString('es-UY', {
        weekday: 'long', day: '2-digit', month: '2-digit',
        hour: '2-digit', minute: '2-digit', timeZone: cfg.TZ,
      });
      for (const am of cfg.amPhones) {
        cola.encolar({
          to: am,
          texto: plantillas.avisoReunionAgendada(lead, { cuando, link: meeting_url }),
          kind: 'am_notice',
          leadId: lead.id,
        });
      }

      // Agendar cancela la derivacion por haberse ido.
      //
      // Al que se derivo porque dejo de contestar y despues reserva, el motivo
      // se le cayo solo: volvio, y de la mejor manera. Sin esto el bot se queda
      // mudo con alguien que acaba de agendar — paso de verdad: reservo,
      // escribio "Gracias!" y no le contesto nadie.
      //
      // Solo ese motivo. Al que se derivo por una queja o por facturacion,
      // agendar no le resuelve nada: esa conversacion sigue siendo de la
      // persona que la tomo.
      const previo = repo.leadPorId(leadId);
      if (previo?.human_requested && previo.motivo_derivacion === 'abandono') {
        repo.actualizarFunnel(leadId, { human_requested: 0, motivo_derivacion: null });
        logger?.info({ leadId }, 'agendo: se le devuelve la conversacion al bot');
      }

      // El embudo tiene que saber que ya agendo.
      //
      // Sin esto el lead se quedaba en MEETING_LINK_SENT —el estado de "tiene
      // el link y todavia no reservo"— aunque hubiera reservado. Si volvia a
      // escribir, el bot le contestaba que ya tenia el link, y al segundo
      // mensaje lo derivaba a una persona por insistir. Justo al que hizo lo
      // que se le pidio.
      repo.actualizarFunnel(leadId, { fsm_state: 'SCHEDULED', fsm_retries: 0 });

      logger?.info({ leadId, meeting_time, recordatorios }, 'reunion agendada');
      return { recordatorios, followup_cancelado: true };
    },

    /**
     * Escribiste vos al lead desde el telefono, no el bot.
     *
     * Dos cosas: se guarda el mensaje —si no, en el CRM la conversacion queda
     * con agujeros— y el bot se calla en ese chat por unas horas. La pausa
     * vence sola: el pedido era "que pare cuando yo entro, y que si el cliente
     * vuelve a escribir a los dias le conteste".
     *
     * El eco de lo que manda el propio bot llega marcado igual que esto, asi
     * que el que llama tiene que filtrarlo antes (ver app.js). Si no, el bot se
     * callaria solo cada vez que contesta.
     */
    registrarSalienteManual({ telefono, texto, id = null }) {
      const tel = normalizar(telefono, cfg.DEFAULT_COUNTRY_CODE) || telefono;
      const lead = repo.leadPorTelefono(tel);
      // No creamos un lead desde un saliente: si le escribiste a alguien que no
      // esta en la base, no es un lead del embudo.
      if (!lead) return null;

      repo.registrarMensaje({
        lead_id: lead.id, direction: 'out', kind: 'manual', body: texto || '',
        provider: 'mano', provider_msg_id: id, status: 'sent', destino: tel,
      });
      repo.actualizarFunnel(lead.id, { bot_pausado_hasta: pausarHasta(ahora()) });
      logger?.info({ leadId: lead.id, horas: HORAS_PAUSA }, 'escribiste vos, el bot se pausa');
      return repo.leadPorId(lead.id);
    },

    /**
     * El lead contesto, o alguien escribio al numero por primera vez: se cancela
     * el follow-up, se avisa al AM y la conversacion sigue en el embudo.
     *
     * @param {string} [nombreWa] nombre de perfil de WhatsApp, para los que
     *   escriben al numero sin haber pasado por el formulario.
     */
    async registrarRespuesta(telefono, texto, nombreWa = '') {
      let lead = repo.leadPorTelefono(telefono);

      // Nadie con ese telefono: escribio al numero directo, sin formulario de
      // por medio (un QR, un anuncio, el numero en la web). Se da de alta y
      // entra al embudo igual, como hacia el bot viejo con findOrCreate.
      // Sin esto, quien escribe al WhatsApp de la empresa recibe silencio.
      if (!lead) {
        lead = repo.crearLead({
          nombre: nombreWa || '',
          telefono,
          origen: 'wa',
          status: 'replied',
        });
        logger?.info({ leadId: lead.id, nombre: nombreWa || '(sin nombre)' }, 'lead nuevo por WhatsApp');
      }

      repo.registrarMensaje({
        lead_id: lead.id, direction: 'in', kind: 'reply', body: texto,
        provider: 'entrante', status: 'delivered',
      });

      // Apagado desde el panel, o pausado porque entraste vos al chat desde el
      // telefono. Igual que con human_requested: el bot se calla pero el que
      // esta atendiendo tiene que enterarse de que le escribieron.
      if (!botActivo(lead, ahora())) {
        avisarQueSigueEscribiendo(lead, texto);
        return repo.leadPorId(lead.id);
      }

      // Ya lo atiende una persona: el bot no le contesta, pero el que lo tiene
      // a cargo tiene que saber que le escribio.
      if (lead.human_requested) {
        avisarQueSigueEscribiendo(lead, texto);
        return repo.leadPorId(lead.id);
      }

      // El saludo salia solo por el camino del formulario. Al que escribe
      // directo al numero —un QR, un anuncio, el numero en la web— el bot le
      // arrancaba a preguntar sin presentarse: del otro lado aparece un
      // desconocido pidiendo datos del negocio.
      //
      // Va antes de la respuesta, no en lugar de ella: recibe la presentacion y
      // ademas lo que vino a preguntar.
      if (!lead.welcomed_at) {
        cola.encolar({
          to: telefono,
          texto: textos.BIENVENIDA,
          kind: 'welcome',
          leadId: lead.id,
        });
        repo.actualizarLead(lead.id, { welcomed_at: ahora().toISOString() });
        lead = repo.leadPorId(lead.id);
      }

      // Lo que el bot tenia escrito y sin mandar quedo viejo: el lead acaba de
      // decir algo nuevo, y la respuesta que sale ahora la escribe el modelo
      // viendo eso tambien. Sin esto llegan las dos, desordenadas — el bot
      // pregunta el nombre del negocio despues de que ya se lo dijeron.
      cola.descartarPendientesDe(lead.id);

      // El aviso al AM sale una sola vez, en la primera respuesta. Despues el
      // lead puede mandar diez mensajes contestando el embudo y no tiene
      // sentido avisar por cada uno.
      if (!lead.replied_at) {
        repo.actualizarLead(lead.id, { status: 'replied', replied_at: ahora().toISOString() });
        repo.cancelarJobs(lead.id, 'followup');

        for (const am of cfg.amPhones) {
          cola.encolar({
            to: am,
            texto: lead.origen === 'wa'
              ? plantillas.avisoContactoNuevo(lead, texto)
              : plantillas.avisoRespuesta(lead, texto),
            kind: 'am_notice',
            leadId: lead.id,
          });
        }
      }

      if (embudo && cfg.FUNNEL_ENABLED) {
        await embudo.procesar(lead.id, texto);
      }

      armarAbandono(repo.leadPorId(lead.id));
      return repo.leadPorId(lead.id);
    },
  };
}

module.exports = { crearServicioLeads };
