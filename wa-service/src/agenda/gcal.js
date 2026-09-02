'use strict';

/**
 * Agenda contra Google Calendar: que horarios hay libres y reservar uno.
 *
 * Se usa Google y no Calendly porque la API de Calendly no deja reservar en
 * nombre de otro — se pueden listar horarios, pero la reserva la tiene que
 * completar el lead en la pagina de ellos, con nombre y mail. Eso deja el flujo
 * a mitad de camino: el bot muestra horarios y despues igual manda un link.
 *
 * Con Google el lead elige un horario por WhatsApp y queda agendado ahi mismo,
 * con el link de Meet en el mismo mensaje.
 */

const TOKEN_URL = 'https://oauth2.googleapis.com/token';
const API = 'https://www.googleapis.com/calendar/v3';

// Google esta detras de Cloudflare para algunos endpoints y rechaza clientes
// sin User-Agent. Calendly lo hace tambien: costo un 403 con codigo 1010 que
// parecia un problema de credenciales y no lo era.
const AGENTE = 'scalerics-wa/1.0';

/** Partes de una fecha en una zona horaria, sin librerias. */
function enZona(fecha, tz) {
  const p = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
    weekday: 'short',
  }).formatToParts(fecha);
  const v = (t) => p.find((x) => x.type === t)?.value;
  return {
    dia: `${v('year')}-${v('month')}-${v('day')}`,
    hora: parseInt(v('hour'), 10) % 24,
    minuto: parseInt(v('minute'), 10),
    diaSemana: v('weekday').toLowerCase().slice(0, 3),
  };
}

/**
 * El instante UTC que corresponde a una hora local de un dia.
 *
 * Se resuelve por aproximacion y correccion en vez de hardcodear el offset:
 * Uruguay no tiene horario de verano hoy, pero lo tuvo, y un offset fijo es una
 * bomba de tiempo que explota en la madrugada del cambio.
 */
function instanteLocal(dia, hora, minuto, tz) {
  const [a, m, d] = dia.split('-').map(Number);
  let t = Date.UTC(a, m - 1, d, hora, minuto);
  for (let i = 0; i < 3; i++) {
    const p = enZona(new Date(t), tz);
    const deriva = (p.hora * 60 + p.minuto) - (hora * 60 + minuto);
    if (deriva === 0) break;
    // La deriva puede cruzar la medianoche: se normaliza al rango [-12h, +12h].
    const min = ((deriva + 720) % 1440) - 720;
    t -= min * 60_000;
  }
  return new Date(t);
}

const DIAS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];

/**
 * Elige `cuantos` de una lista, repartidos de punta a punta.
 *
 * Siempre entran el primero y el ultimo: son los que le dicen al lead entre que
 * horas se puede. Los del medio se toman a paso parejo.
 */
function repartir(lista, cuantos) {
  if (cuantos >= lista.length) return lista.slice();
  if (cuantos <= 1) return lista.slice(0, cuantos);
  const salida = [];
  for (let i = 0; i < cuantos; i++) {
    salida.push(lista[Math.round((i * (lista.length - 1)) / (cuantos - 1))]);
  }
  return salida;
}

/**
 * @param {object} cfg  GCAL_*, TZ, y la ventana de atencion
 * @param {object} deps.fetch  para poder probarlo sin salir a internet
 */
function crearAgenda({ cfg, logger = null, fetch: _fetch = globalThis.fetch } = {}) {
  const tz = cfg.TZ || 'America/Montevideo';
  const activo = Boolean(cfg.GCAL_CLIENT_ID && cfg.GCAL_CLIENT_SECRET && cfg.GCAL_REFRESH_TOKEN);

  let token = null;
  let tokenVence = 0;

  async function accessToken() {
    // Se renueva un minuto antes de que venza: pedirlo justo al filo hace que
    // una llamada lenta salga con el token ya expirado.
    if (token && Date.now() < tokenVence - 60_000) return token;

    const r = await _fetch(TOKEN_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': AGENTE },
      body: new URLSearchParams({
        client_id: cfg.GCAL_CLIENT_ID,
        client_secret: cfg.GCAL_CLIENT_SECRET,
        refresh_token: cfg.GCAL_REFRESH_TOKEN,
        grant_type: 'refresh_token',
      }),
    });
    if (!r.ok) throw new Error(`no se pudo renovar el token de Google: ${r.status}`);
    const j = await r.json();
    token = j.access_token;
    tokenVence = Date.now() + (j.expires_in || 3600) * 1000;
    return token;
  }

  async function api(ruta, opciones = {}) {
    const t = await accessToken();
    const r = await _fetch(API + ruta, {
      ...opciones,
      headers: {
        Authorization: `Bearer ${t}`,
        'Content-Type': 'application/json',
        'User-Agent': AGENTE,
        ...(opciones.headers || {}),
      },
    });
    if (!r.ok) throw new Error(`Google respondio ${r.status}: ${(await r.text()).slice(0, 200)}`);
    return r.json();
  }

  /** Los tramos ocupados del calendario en un rango. */
  async function ocupado(desde, hasta) {
    const j = await api('/freeBusy', {
      method: 'POST',
      body: JSON.stringify({
        timeMin: desde.toISOString(),
        timeMax: hasta.toISOString(),
        timeZone: tz,
        items: [{ id: cfg.GCAL_CALENDAR_ID }],
      }),
    });
    const cal = j.calendars?.[cfg.GCAL_CALENDAR_ID];
    if (cal?.errors?.length) throw new Error(`freeBusy: ${JSON.stringify(cal.errors)}`);
    return (cal?.busy || []).map((b) => ({ desde: new Date(b.start), hasta: new Date(b.end) }));
  }

  return {
    activo,

    /**
     * Los eventos del calendario en un rango.
     *
     * showDeleted para ver tambien las bajas: una reunion cancelada tiene que
     * cancelar sus recordatorios, y si no se piden, desaparece sin dejar rastro
     * y los recordatorios salen igual.
     */
    async listarEventos({ desde, hasta, max = 250 }) {
      if (!activo) return [];
      const q = new URLSearchParams({
        timeMin: desde.toISOString(),
        timeMax: hasta.toISOString(),
        singleEvents: 'true',
        showDeleted: 'true',
        orderBy: 'startTime',
        maxResults: String(max),
      });
      const j = await api(`/calendars/${encodeURIComponent(cfg.GCAL_CALENDAR_ID)}/events?${q}`);
      return j.items || [];
    },

    /**
     * Horarios libres del proximo dia habil que tenga alguno.
     *
     * Se ofrece un solo dia y no una lista de varios: cinco horarios de un dia
     * se eligen de un vistazo, quince repartidos en tres dias se leen como un
     * formulario.
     *
     * @returns {Promise<{dia: string, slots: Date[]}|null>}
     */
    async horariosDisponibles(ahora = new Date()) {
      if (!activo) return null;

      const [hIni, mIni] = String(cfg.AGENDA_DESDE).split(':').map(Number);
      const [hFin, mFin] = String(cfg.AGENDA_HASTA).split(':').map(Number);
      const paso = cfg.AGENDA_PASO_MIN;
      const duracion = cfg.AGENDA_DURACION_MIN;
      const habiles = new Set(String(cfg.AGENDA_DIAS).split(',').map((d) => d.trim()));

      // No se ofrece nada demasiado pronto: a nadie le sirve una reunion en
      // veinte minutos, y aceptarla suena a que no hay nadie del otro lado.
      const piso = new Date(ahora.getTime() + cfg.AGENDA_AVISO_MIN_HORAS * 3600_000);
      const techo = new Date(ahora.getTime() + cfg.AGENDA_DIAS_ADELANTE * 86400_000);

      let ocupados;
      try {
        ocupados = await ocupado(piso, techo);
      } catch (e) {
        logger?.warn({ err: String(e.message || e) }, 'no se pudo leer la agenda');
        return null;
      }

      const libre = (inicio) => {
        const fin = new Date(inicio.getTime() + duracion * 60_000);
        return !ocupados.some((o) => inicio < o.hasta && fin > o.desde);
      };

      /**
       * Se juntan de varios dias, con un tope por dia.
       *
       * Antes se devolvia el PRIMER dia con hueco y se cortaba ahi: el lead
       * veia cinco horarios de un mismo dia y ninguna forma de pedir otro. Si
       * mañana no le sirve —que es lo normal cuando alguien tiene un negocio—
       * no tiene nada que elegir, y el mensaje da a entender que no se puede
       * agendar mas adelante.
       *
       * El tope por dia es lo que hace que las opciones abarquen. Sin el, las
       * cinco se las come el primer dia igual que antes.
       */
      const elegidos = [];
      const primerDia = { dia: null };
      const finDelDia = hFin * 60 + mFin;

      for (let d = 0; d <= cfg.AGENDA_DIAS_ADELANTE; d++) {
        if (elegidos.length >= cfg.AGENDA_MAX_OPCIONES) break;

        const ref = new Date(ahora.getTime() + d * 86400_000);
        const { dia } = enZona(ref, tz);
        const { diaSemana } = enZona(instanteLocal(dia, 12, 0, tz), tz);
        if (!habiles.has(diaSemana)) continue;

        const delDia = [];
        for (let min = hIni * 60 + mIni; min + duracion <= finDelDia; min += paso) {
          const inicio = instanteLocal(dia, Math.floor(min / 60), min % 60, tz);
          if (inicio < piso) continue;
          if (libre(inicio)) delDia.push(inicio);
        }

        if (!delDia.length) continue;
        // Se reparten a lo largo del dia en vez de tomar los primeros.
        //
        // Con la franja de 12 a 16 cada media hora hay ocho huecos. Mostrando
        // los dos primeros, el lead ve "12:00 o 12:30" y entiende que a la
        // tarde no atendemos —aunque las 15:00 esten libres y se las podamos
        // agendar si las pide—. Repartidas, la sugerencia deja ver el rango.
        const sugeridos = repartir(delDia, cfg.AGENDA_MAX_POR_DIA);
        delDia.length = 0;
        delDia.push(...sugeridos);
        if (!primerDia.dia) primerDia.dia = dia;
        elegidos.push(...delDia.slice(0, cfg.AGENDA_MAX_OPCIONES - elegidos.length));
      }

      if (!elegidos.length) return null;
      // `dia` queda por compatibilidad: es el del primer horario. Lo que se le
      // muestra al lead sale de los slots, que ya traen su fecha cada uno.
      return { dia: primerDia.dia, slots: elegidos };
    },

    /**
     * Reserva un horario. Vuelve a mirar la agenda antes de crear el evento:
     * entre que se le ofrecieron los horarios y contesto pudo pasar cualquier
     * cosa, y confirmarle una reunion sobre algo ya ocupado es peor que
     * pedirle que elija de nuevo.
     *
     * @returns {Promise<{ok: true, inicio: Date, meetUrl: string}|{ok: false, motivo: 'ocupado'|'error'}>}
     */
    /**
     * Si un hueco puntual esta libre. Para las horas que propone el lead y que
     * no estaban en la lista: los cinco horarios que se le muestran son
     * sugerencias repartidas en dias, no todo lo que hay.
     *
     * @returns {Promise<boolean|null>} null si no se pudo preguntar. Ni true ni
     *   false: devolver true agendaria encima de algo y false perderia una
     *   reunion que si se podia. El que llama decide que hacer con la duda.
     */
    async libreEn(inicio) {
      if (!activo) return null;
      const fin = new Date(inicio.getTime() + cfg.AGENDA_DURACION_MIN * 60_000);
      try {
        const ocupados = await ocupado(inicio, fin);
        return !ocupados.some((o) => inicio < o.hasta && fin > o.desde);
      } catch (e) {
        logger?.warn({ err: String(e.message || e) }, 'no se pudo consultar un horario puntual');
        return null;
      }
    },

    async reservar({ inicio, nombre, telefono, resumen }) {
      if (!activo) return { ok: false, motivo: 'error' };

      const fin = new Date(inicio.getTime() + cfg.AGENDA_DURACION_MIN * 60_000);

      try {
        const choques = await ocupado(inicio, fin);
        if (choques.length) return { ok: false, motivo: 'ocupado' };
      } catch (e) {
        logger?.warn({ err: String(e.message || e) }, 'no se pudo verificar el horario');
        return { ok: false, motivo: 'error' };
      }

      try {
        const evento = await api(
          `/calendars/${encodeURIComponent(cfg.GCAL_CALENDAR_ID)}/events?conferenceDataVersion=1`,
          {
            method: 'POST',
            body: JSON.stringify({
              summary: `Diagnóstico Scalerics — ${nombre || telefono}`,
              description: [
                resumen || '',
                '',
                `WhatsApp: https://wa.me/${telefono}`,
                'Agendado por el bot desde WhatsApp.',
              ].join('\n'),
              start: { dateTime: inicio.toISOString(), timeZone: tz },
              end: { dateTime: fin.toISOString(), timeZone: tz },
              // El link de Meet lo crea Google: pedirlo aca evita tener que
              // mandarle despues un segundo mensaje con el link.
              conferenceData: {
                createRequest: {
                  requestId: `wa-${telefono}-${inicio.getTime()}`,
                  conferenceSolutionKey: { type: 'hangoutsMeet' },
                },
              },
            }),
          }
        );

        return {
          ok: true,
          inicio,
          meetUrl: evento.hangoutLink || evento.conferenceData?.entryPoints?.[0]?.uri || '',
          eventId: evento.id,
        };
      } catch (e) {
        logger?.error({ err: String(e.message || e) }, 'no se pudo crear el evento');
        return { ok: false, motivo: 'error' };
      }
    },

    // Se exportan para los tests.
    _enZona: enZona,
    _instanteLocal: instanteLocal,
  };
}

module.exports = { crearAgenda, enZona, instanteLocal, DIAS };
