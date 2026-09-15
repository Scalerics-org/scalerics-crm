'use strict';

const plantillas = require('../templates');
const { correspondeDerivar } = require('../funnel/abandono');

const INTERVALO_MS = 5 * 60 * 1000; // cada 5 minutos

/**
 * Cuanto tiene que faltar para que el recordatorio del dia antes siga teniendo
 * sentido.
 *
 * Un recordatorio que sale tarde miente. Si el servicio estuvo caido unas
 * horas, los dos jobs quedan vencidos y salen juntos: al lead le llegan "mañana
 * tenemos la videollamada" y "es en 30 minutos" con segundos de diferencia, uno
 * atras del otro. Pasó exactamente asi en una prueba.
 *
 * Con menos de seis horas por delante, el del dia antes ya no aporta nada: el
 * de media hora cubre el caso y dice la verdad.
 */
const RECORDATORIO_DIA_ANTES_MIN_HORAS = 6;

/** "mañana", "en 3 días", "en 5 horas". Para que el modelo no lo adivine. */
function cuantoFalta(ms) {
  const horas = ms / 3600_000;
  if (horas < 1) return `en ${Math.max(1, Math.round(ms / 60_000))} minutos`;
  if (horas < 20) return `en ${Math.round(horas)} horas`;
  const dias = Math.round(horas / 24);
  return dias <= 1 ? 'mañana' : `en ${dias} días`;
}

/**
 * Reemplaza a BullMQ: los jobs viven en SQLite y esto los levanta.
 *
 * correrVencidos() recibe la fecha por parametro a proposito: los tests
 * adelantan el reloj pasando un valor, sin tocar timers.
 */
// Cuanto se corre un job cuando la IA no pudo escribir el mensaje.
const REINTENTO_MIN = 30;

function crearScheduler({ repo, cola, cfg, redactor = null, embudo = null, limites = null, logger, ahora = () => new Date() }) {

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

  const SITUACION = {
    followup: 'followup',
    reminder_24h: 'recordatorio_dia_antes',
    reminder_30m: 'recordatorio_30min',
    nurture: 'nurture_vuelta',
  };

  /**
   * Todos estos mensajes los escribe la IA. Si no puede, el job NO se manda con
   * un texto fijo: se reprograma. Nadie esta esperando en tiempo real de este
   * lado —son mensajes que arranca el servicio, no respuestas— asi que
   * conviene mandarlo bien media hora despues que mandarlo enlatado ahora.
   *
   * @returns {Promise<boolean>} false si hay que reintentar mas adelante.
   */
  async function ejecutar(job, lead, momento) {
    // El abandono no manda un mensaje y ya: le escribe al lead, le avisa al
    // agente comercial con el contexto y deja la conversacion en manos de una
    // persona. Todo eso vive en el embudo, que es quien sabe derivar.
    if (job.type === 'abandono') {
      if (!embudo?.derivarPorAbandono) return true;
      return embudo.derivarPorAbandono(lead.id);
    }

    const situacion = SITUACION[job.type];
    if (!situacion) return true;

    // Se le dice cuanto falta ademas de la fecha: si solo ve la fecha, el
    // modelo completa el resto por su cuenta y dice "mañana" aunque falten tres
    // dias. La cuenta la hace el codigo, que no se equivoca.
    const falta = lead.meeting_time
      ? cuantoFalta(new Date(lead.meeting_time) - momento)
      : '';
    const extra = lead.meeting_time
      ? `La reunión es ${fechaLegible(lead.meeting_time)}, o sea ${falta}.${lead.meeting_url ? ` El link para entrar es ${lead.meeting_url}` : ''}`
      : '';

    const texto = await redactor?.escribir(lead, situacion, extra);
    if (!texto) return false;

    // Un recordatorio no es salida en frio: el lead reservo ese horario. Por
    // eso no espera a la apertura —el 14-9 uno quedo guardado desde el domingo
    // y salio el lunes 56 minutos antes de la reunion, diciendo "mañana"— y por
    // eso ademas vence: el texto dice cuanto falta, y un recordatorio que sale
    // tarde no llega tarde, miente. Pasada la reunion no hay nada que recordar.
    const esRecordatorio = job.type.startsWith('reminder_') && lead.meeting_time;
    const faltanMin = esRecordatorio
      ? Math.floor((new Date(lead.meeting_time) - momento) / 60_000)
      : 0;

    cola.encolar({
      to: lead.telefono,
      texto,
      kind: job.type === 'followup' ? 'followup' : 'manual',
      leadId: lead.id,
      ...(esRecordatorio ? { acordado: true, venceEnMin: Math.max(1, faltanMin), encoladoEn: momento } : {}),
    });

    if (job.type === 'followup') {
      repo.actualizarLead(lead.id, {
        status: 'followed_up',
        followup_sent_at: momento.toISOString(),
      });
      avisarAM(lead, plantillas.avisoSinRespuesta(lead, cfg.FOLLOWUP_DELAY_HOURS));
    }

    // La pausa termino: vuelve al embudo. Si se quedara en NURTURE, su
    // respuesta llegaria a un estado donde el bot no le cierra nada, y ademas
    // no se le podria volver a programar otra pausa mas adelante.
    if (job.type === 'nurture') {
      repo.actualizarFunnel(lead.id, { fsm_state: 'CONVERSANDO', fsm_retries: 0 });
    }
    return true;
  }

  async function correrVencidos(momento = ahora()) {
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

      // Un recordatorio que sale tarde miente, y mejor no mandarlo.
      if (job.type.startsWith('reminder_')) {
        const faltanMs = new Date(lead.meeting_time) - momento;

        if (faltanMs <= 0) {
          repo.marcarJob(job.id, 'cancelled', 'la reunion ya paso');
          continue;
        }
        if (job.type === 'reminder_24h'
          && faltanMs < RECORDATORIO_DIA_ANTES_MIN_HORAS * 3600_000) {
          repo.marcarJob(job.id, 'cancelled', 'el del dia antes salio tarde, lo cubre el de 30 min');
          continue;
        }
      }

      // Entre que se armo el reloj y ahora, el lead pudo agendar, pedir una
      // persona o darse de baja. En cualquiera de esos casos ya no se fue de
      // la conversacion: se fue a otro lado, y derivarlo seria ruido.
      /**
       * De madrugada no se deriva a nadie.
       *
       * El 6-9 un lead escribio tres veces a las dos y media de la mañana de un
       * domingo, se durmio en la pregunta del menu, y a las 3:37 el bot le dijo
       * que le pasaba el caso al equipo. La ficha al comercial salio a la misma
       * hora, cuando no habia nadie para leerla.
       *
       * La ventana de horario no lo frenaba porque el mensaje de derivacion
       * viaja como `manual`, y `manual` cuenta como respuesta si el lead
       * escribio en las ultimas RESPUESTA_VENTANA_MIN. El abandono salta a los
       * ABANDONO_MINUTOS, que es la mitad: siempre caia adentro de esa ventana
       * y siempre la esquivaba. No fue mala suerte.
       *
       * Y una hora de silencio a las dos de la mañana no es abandono: se
       * durmio. Asi que a la apertura se le escribe para retomar. Si ya se le
       * insistio una vez y volvio a callarse, ahi si se deriva — pero en hora.
       */
      if (job.type === 'abandono' && limites && !limites.enHorario(momento)) {
        const apertura = limites.proximaApertura(momento);
        if (!lead.followup_sent_at) {
          repo.programarJob(lead.id, 'followup', apertura.toISOString());
          repo.marcarJob(job.id, 'cancelled', 'se callo fuera de hora: se retoma a la apertura');
        } else {
          repo.reprogramarJob(job.id, apertura.toISOString());
        }
        continue;
      }

      if (job.type === 'abandono' && !correspondeDerivar(lead, repo.caracteresDelLead(lead.id))) {
        repo.marcarJob(job.id, 'cancelled', 'ya no corresponde derivar');
        continue;
      }

      // Entre que se puso en pausa y hoy, el lead pudo volver por su cuenta,
      // agendar o pasar a una persona. Escribirle "quedamos en que te escribia"
      // a alguien que ya esta conversando queda pesimo.
      if (job.type === 'nurture' && lead.fsm_state !== 'NURTURE') {
        repo.marcarJob(job.id, 'cancelled', 'el lead ya salio de la pausa');
        continue;
      }

      try {
        if (!await ejecutar(job, lead, momento)) {
          // La IA no pudo escribirlo. Se corre el job en vez de perderlo.
          const reintento = new Date(momento.getTime() + REINTENTO_MIN * 60_000);
          repo.reprogramarJob(job.id, reintento.toISOString());
          logger?.warn({ jobId: job.id, tipo: job.type }, 'no se pudo redactar, se reprograma');
          continue;
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
