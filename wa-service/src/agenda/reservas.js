'use strict';

const { normalizar } = require('../telefono');

/**
 * Se entera de que un lead agendo, leyendo el calendario.
 *
 * Desde que el bot dejo de reservar el mismo, no hay forma de que sepa que
 * alguien agendo: el lead abre Calendly, elige un horario y eso pasa entero
 * afuera del bot. Y sin saberlo no puede mandar los recordatorios, ni dejar de
 * insistirle a alguien que ya reservo.
 *
 * Las dos vias obvias estan tapadas. El webhook de Calendly no sirve porque el
 * bot no tiene IP publica. La API de Calendly pide un plan pago y un token mas
 * para mantener.
 *
 * Pero Calendly escribe cada reserva en el Google Calendar, al que el bot ya
 * entra, y el formulario pregunta el telefono. La respuesta viaja al evento:
 *
 *   Empresa: La Linda
 *   ¿Qué necesitás?: Página web
 *   Teléfono / WhatsApp: +598 95 872 579
 *
 * Con eso el cruce con el lead es exacto, por telefono, sin adivinar por
 * nombre. Si algun dia sacan esa pregunta del formulario, esto deja de
 * encontrar reservas —y hay que enterarse—, asi que se avisa en el log.
 */

// "Teléfono / WhatsApp: +598 95 872 579". Tolera como se llame la pregunta:
// lo que importa es que la etiqueta hable de un telefono y traiga dos puntos.
const CAMPO_TELEFONO = /^[^\n:]{0,60}(?:tel[eé]fono|whatsapp|celular|cel\b)[^\n:]{0,40}:[ \t]*(.+)$/im;

/**
 * Calendly no borra la reunion cancelada: le deja "Cancelado:" adelante y la
 * mantiene en el calendario. Google si las marca, cuando se borran de verdad.
 */
const CANCELADO = /^cancelad[oa]\b/i;

function telefonoDe(evento, codigoPais) {
  const texto = String(evento.description || '').replace(/<[^>]+>/g, ' ');
  const m = CAMPO_TELEFONO.exec(texto);
  if (!m) return null;
  return normalizar(m[1].trim(), codigoPais);
}

function estaCancelado(evento) {
  return evento.status === 'cancelled' || CANCELADO.test(String(evento.summary || '').trim());
}

/**
 * @param {object} deps.servicioLeads  para registrarReunion, que es lo que
 *   cancela el seguimiento, avisa al equipo y programa los recordatorios.
 */
function crearVigilanteDeReservas({
  agenda, repo, servicioLeads, cfg, logger = null, ahora = () => new Date(),
}) {
  /** La reunion se cayo: se le borra y se le cancelan los recordatorios. */
  function desagendar(lead) {
    repo.actualizarFunnel(lead.id, { meeting_time: null, meeting_event_id: null });
    repo.cancelarJobs(lead.id, 'reminder_24h');
    repo.cancelarJobs(lead.id, 'reminder_30m');
    logger?.info({ leadId: lead.id }, 'la reunion se cancelo: se cancelan los recordatorios');
  }

  async function revisar(momento = ahora()) {
    const nada = { eventos: 0, conTelefono: 0, agendadas: 0, canceladas: 0 };
    if (!agenda?.activo) return nada;

    // Un rato para atras tambien: una reunion que acaba de empezar sigue
    // siendo una reserva que hay que registrar.
    const desde = new Date(momento.getTime() - 60 * 60_000);
    const hasta = new Date(momento.getTime() + cfg.RESERVAS_DIAS_ADELANTE * 86_400_000);

    let eventos;
    try {
      eventos = await agenda.listarEventos({ desde, hasta });
    } catch (e) {
      logger?.warn({ err: String(e.message || e) }, 'no se pudo leer el calendario');
      return nada;
    }

    const r = { ...nada, eventos: eventos.length };

    for (const ev of eventos) {
      const tel = telefonoDe(ev, cfg.DEFAULT_COUNTRY_CODE);
      if (!tel) continue;
      r.conTelefono += 1;

      const lead = repo.leadPorTelefono(tel);
      if (!lead) continue;

      if (estaCancelado(ev)) {
        // Solo si es LA reunion que tenia: una cancelada vieja no puede
        // borrarle la que reservo despues.
        if (lead.meeting_event_id === ev.id) {
          desagendar(lead);
          r.canceladas += 1;
        }
        continue;
      }

      const inicio = ev.start?.dateTime;
      if (!inicio) continue;

      // Ya registrada, con la misma hora: no se toca. Sin esto, cada vuelta
      // volveria a avisarle al equipo y a reprogramar los recordatorios.
      if (lead.meeting_event_id === ev.id && lead.meeting_time === inicio) continue;

      servicioLeads.registrarReunion(lead.id, {
        meeting_time: inicio,
        meeting_url: ev.hangoutLink || '',
      });
      repo.actualizarFunnel(lead.id, { meeting_event_id: ev.id });
      r.agendadas += 1;
      logger?.info({ leadId: lead.id, inicio }, 'agendo en Calendly: recordatorios programados');
    }

    // Ninguna reserva trae telefono: o no hay reservas, o le sacaron la
    // pregunta al formulario y esto quedo ciego sin fallar.
    if (r.eventos && !r.conTelefono) {
      logger?.warn(
        { eventos: r.eventos },
        'ninguna reunion del calendario trae telefono: revisar la pregunta del formulario de Calendly'
      );
    }
    return r;
  }

  let timer = null;

  return {
    revisar,
    arrancar() {
      if (timer || !agenda?.activo || !cfg.RESERVAS_VIGILAR) return;
      const cada = cfg.RESERVAS_INTERVALO_MIN * 60_000;
      timer = setInterval(() => {
        revisar().catch((e) => logger?.error({ err: String(e.message || e) }, 'vigilante de reservas'));
      }, cada);
      timer.unref?.();
      logger?.info({ cadaMin: cfg.RESERVAS_INTERVALO_MIN }, 'vigilante de reservas de Calendly activo');
    },
    parar() {
      if (timer) clearInterval(timer);
      timer = null;
    },
  };
}

module.exports = { crearVigilanteDeReservas, telefonoDe, estaCancelado };
