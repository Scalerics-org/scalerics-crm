'use strict';

const plantillas = require('../templates');

const INTERVALO_MS = 5 * 60 * 1000; // cada 5 minutos

/**
 * Reemplaza a BullMQ: los jobs viven en SQLite y esto los levanta.
 *
 * correrVencidos() recibe la fecha por parametro a proposito: los tests
 * adelantan el reloj pasando un valor, sin tocar timers.
 */
function crearScheduler({ repo, cola, cfg, logger, ahora = () => new Date() }) {

  function correrVencidos(momento = ahora()) {
    const iso = momento.toISOString();
    const jobs = repo.jobsVencidos(iso);
    let procesados = 0;

    for (const job of jobs) {
      const lead = repo.leadPorId(job.lead_id);
      if (!lead) {
        repo.marcarJob(job.id, 'cancelled', 'lead inexistente');
        continue;
      }

      // Si contesto entre medio, el follow-up ya no corresponde.
      if (job.type === 'followup' && lead.replied_at) {
        repo.marcarJob(job.id, 'cancelled', 'el lead respondio');
        continue;
      }
      if (lead.status === 'closed') {
        repo.marcarJob(job.id, 'cancelled', 'lead dado de baja');
        continue;
      }

      try {
        if (job.type === 'followup') {
          cola.encolar({
            to: lead.telefono,
            texto: plantillas.render(lead, 'followup'),
            kind: 'followup',
            leadId: lead.id,
          });
          repo.actualizarLead(lead.id, {
            status: 'followed_up',
            followup_sent_at: momento.toISOString(),
          });

          for (const am of cfg.amPhones) {
            cola.encolar({
              to: am,
              texto: plantillas.avisoSinRespuesta(lead),
              kind: 'am_notice',
              leadId: lead.id,
            });
          }
        }
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

  let timer = null;

  return {
    correrVencidos,
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
