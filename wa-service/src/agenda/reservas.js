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
 * Compara dos fechas por el instante que representan, no por el texto.
 *
 * El bot guarda "2026-09-23T13:00:00.000Z" (UTC) y Google devuelve
 * "2026-09-23T10:00:00-03:00" (con offset de Montevideo): mismo instante,
 * texto distinto. Comparar los strings a mano las trataria como reservas
 * diferentes.
 */
function mismoInstante(a, b) {
  if (!a || !b) return false;
  const ta = Date.parse(a);
  const tb = Date.parse(b);
  if (Number.isNaN(ta) || Number.isNaN(tb)) return false;
  return ta === tb;
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

    // Primero las bajas, que no dependen de con cual reunion se queda el lead.
    const activos = [];
    for (const ev of eventos) {
      const tel = telefonoDe(ev, cfg.DEFAULT_COUNTRY_CODE);
      if (!tel) continue;
      r.conTelefono += 1;

      /**
       * El formulario trae un telefono del equipo, no del cliente.
       *
       * Cuando el equipo reserva a nombre de un cliente y pone su propio
       * numero, el bot lo lee como "el que agendo" y le manda a esa persona el
       * recordatorio del cliente. Paso con una reunion de La Vaca Encantada: el
       * formulario tenia el numero de Juan y el recordatorio le llego a el.
       *
       * No se puede arreglar solo: no sabemos el telefono del cliente. Lo unico
       * honesto es no registrarla y decirlo en el log, para que se note que esa
       * reunion no tiene recordatorio.
       */
      if (cfg.equipo?.includes(tel)) {
        logger?.warn(
          { tel, evento: ev.summary },
          'la reserva tiene un telefono del equipo: no se le van a mandar recordatorios a nadie'
        );
        continue;
      }

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
      if (inicio) activos.push({ ev, lead, inicio });
    }

    /**
     * De cada lead, la reunion mas proxima y nada mas.
     *
     * Un lead puede tener dos reservas —reservo de nuevo sin cancelar la
     * anterior— y ahi la que importa es la que viene primero. Sin esta parte,
     * las dos se pisaban: cada una veia el meeting_event_id de la otra, se daba
     * por no registrada y se registraba de nuevo. En produccion eso fue un
     * aviso al equipo cada cinco minutos durante horas.
     */
    const proxima = new Map();
    for (const a of activos) {
      const actual = proxima.get(a.lead.id);
      if (!actual || a.inicio < actual.inicio) proxima.set(a.lead.id, a);
    }

    for (const { ev, lead, inicio } of proxima.values()) {
      /**
       * La reunion la agendo el propio bot: ya esta registrada.
       *
       * El evento que crea el bot lleva "WhatsApp: wa.me/…" en la descripcion,
       * y CAMPO_TELEFONO lo lee como el telefono del formulario de Calendly.
       * Sin esto, cinco minutos despues de agendar el vigilante la registraba
       * de nuevo: al equipo le llegaba "Reunión agendada" dos veces —paso con
       * TODAS las que agendo el bot: Andres el 11-9, CD Montevideo el 12-9,
       * Patricia el 22-9— y se pisaba la hora en que se habia agendado.
       *
       * Mismo evento y mismo instante: se deja marcada como vista y se sigue.
       * Si la movieron de hora, el instante cambia y si es una reserva nueva.
       */
      if (lead.meeting_event_id === ev.id && mismoInstante(lead.meeting_time, inicio)) {
        repo.reservaEsNueva(ev.id, inicio);
        continue;
      }

      // La red de seguridad: aunque la eleccion de arriba fallara, una reserva
      // ya registrada no se vuelve a registrar nunca.
      if (!repo.reservaEsNueva(ev.id, inicio)) continue;

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

module.exports = { crearVigilanteDeReservas, telefonoDe, estaCancelado, mismoInstante };
