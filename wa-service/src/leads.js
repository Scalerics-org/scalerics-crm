'use strict';

const { normalizar } = require('./telefono');
const plantillas = require('./templates');
const { entre } = require('./outbound/queue');

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

      logger?.info({ leadId, meeting_time, recordatorios }, 'reunion agendada');
      return { recordatorios, followup_cancelado: true };
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

      return repo.leadPorId(lead.id);
    },
  };
}

module.exports = { crearServicioLeads };
