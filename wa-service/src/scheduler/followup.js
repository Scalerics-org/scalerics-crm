'use strict';

const plantillas = require('../templates');

const INTERVALO_MS = 5 * 60 * 1000; // cada 5 minutos

/**
 * Reemplaza a BullMQ: los jobs viven en SQLite y esto los levanta.
 *
 * correrVencidos() recibe la fecha por parametro a proposito: los tests
 * adelantan el reloj pasando un valor, sin tocar timers.
 */
function crearScheduler({ repo, cola, cfg, textosLead, logger, ahora = () => new Date() }) {

  function fechaLegible(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString('es-UY', {
      weekday: 'long', day: '2-digit', month: '2-digit',
      hour: '2-digit', minute: '2-digit', timeZone: cfg.TZ,
    });
  }

  function avisarAM(lead, texto) {
    for (const am of cfg.amPhones) {
      cola.encolar({ to: am, texto, kind: 'am_notice', leadId: lead.id });
    }
  }

  /** @returns {boolean} true si el job se resolvio, false si hay que cancelarlo. */
  function ejecutar(job, lead, momento) {
    const datosReunion = {
      cuando: lead.meeting_time ? fechaLegible(lead.meeting_time) : '',
      link: lead.meeting_url,
    };

    if (job.type === 'followup') {
      cola.encolar({
        to: lead.telefono,
        texto: textosLead.render(lead, 'followup'),
        kind: 'followup',
        leadId: lead.id,
      });
      repo.actualizarLead(lead.id, {
        status: 'followed_up',
        followup_sent_at: momento.toISOString(),
      });
      avisarAM(lead, plantillas.avisoSinRespuesta(lead, cfg.FOLLOWUP_DELAY_HOURS));
      return true;
    }

    if (job.type === 'reminder_24h') {
      cola.encolar({
        to: lead.telefono,
        texto: textosLead.recordatorioDiaAntes(lead, datosReunion),
        kind: 'manual',
        leadId: lead.id,
      });
      return true;
    }

    if (job.type === 'reminder_30m') {
      cola.encolar({
        to: lead.telefono,
        texto: textosLead.recordatorio30Minutos(lead, datosReunion),
        kind: 'manual',
        leadId: lead.id,
      });
      return true;
    }

    return true;
  }

  function correrVencidos(momento = ahora()) {
    const jobs = repo.jobsVencidos(momento.toISOString());
    let procesados = 0;

    for (const job of jobs) {
      const lead = repo.leadPorId(job.lead_id);
      if (!lead) {
        repo.marcarJob(job.id, 'cancelled', 'lead inexistente');
        continue;
      }

      // Dado de baja: no se le escribe mas, ni recordatorios.
      if (lead.opt_out || lead.status === 'closed') {
        repo.marcarJob(job.id, 'cancelled', 'lead dado de baja');
        continue;
      }

      if (job.type === 'followup') {
        // Agendo la consultoria: el follow-up ya no corresponde. Es la regla
        // que pidio el jefe — solo insistir si NO se concreto la reunion.
        if (lead.meeting_booked_at) {
          repo.marcarJob(job.id, 'cancelled', 'ya agendo la reunion');
          continue;
        }
        if (lead.replied_at) {
          repo.marcarJob(job.id, 'cancelled', 'el lead respondio');
          continue;
        }
      }

      // La reunion pudo haberse cancelado o movido entre medio.
      if (job.type.startsWith('reminder_') && !lead.meeting_time) {
        repo.marcarJob(job.id, 'cancelled', 'la reunion ya no existe');
        continue;
      }

      try {
        ejecutar(job, lead, momento);
        repo.marcarJob(job.id, 'done');
        procesados += 1;
      } catch (e) {
        repo.marcarJob(job.id, 'failed', String(e.message || e));
        logger?.error({ jobId: job.id, err: String(e.message || e) }, 'job fallido');
      }
    }

    if (procesados) logger?.info({ procesados }, 'jobs vencidos procesados');
    return procesados;
  }

  /**
   * Agenda los recordatorios de una reunion. El del dia antes solo si al
   * momento de agendar todavia falta mas de un dia: si alguien reserva para
   * dentro de tres horas, no tiene sentido "te recuerdo que es mañana".
   */
  function programarRecordatorios(lead, momento = ahora()) {
    const reunion = new Date(lead.meeting_time);
    if (Number.isNaN(reunion.getTime())) return [];

    const programados = [];
    const faltanMs = reunion - momento;

    const diaAntes = new Date(reunion.getTime() - cfg.REMINDER_DAY_BEFORE_HOURS * 3600_000);
    if (faltanMs > cfg.REMINDER_DAY_BEFORE_HOURS * 3600_000) {
      repo.encolarJob(lead.id, 'reminder_24h', diaAntes.toISOString());
      programados.push('reminder_24h');
    }

    const antes = new Date(reunion.getTime() - cfg.REMINDER_MINUTES_BEFORE * 60_000);
    if (antes > momento) {
      repo.encolarJob(lead.id, 'reminder_30m', antes.toISOString());
      programados.push('reminder_30m');
    }

    logger?.info({ leadId: lead.id, programados }, 'recordatorios de reunion agendados');
    return programados;
  }

  let timer = null;

  return {
    correrVencidos,
    programarRecordatorios,
    arrancar() {
      if (timer) return;
      timer = setInterval(() => {
        try {
          correrVencidos();
        } catch (e) {
          logger?.error({ err: String(e.message || e) }, 'scheduler');
        }
      }, INTERVALO_MS);
      timer.unref?.();
    },
    parar() {
      if (timer) clearInterval(timer);
      timer = null;
    },
  };
}

module.exports = { crearScheduler, INTERVALO_MS };
