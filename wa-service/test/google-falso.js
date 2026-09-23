'use strict';

const { enZona, instanteLocal } = require('../src/agenda/gcal');

const TZ = 'America/Montevideo';

const aTramos = (dia, rangos) => rangos.map((r) => {
  const [a, b] = r.split('-');
  const [ha, ma] = a.split(':').map(Number);
  const [hb, mb] = b.split(':').map(Number);
  return {
    start: instanteLocal(dia, ha, ma, TZ).toISOString(),
    end: instanteLocal(dia, hb, mb, TZ).toISOString(),
  };
});

/**
 * Google falso.
 *
 * `ocupados` son tramos locales ['13:30-14:00', ...] del dia en que empieza el
 * rango que se pide: no filtra por rango, asi que vale para tests de un solo
 * dia. `ocupadosDia` son tramos de un dia concreto, {'2026-09-23': ['12:00-16:00']},
 * y salen siempre: es lo que hace falta para probar que un dia lleno no
 * aparece en una lista de varios dias sin que se llenen todos.
 */
function googleFalso({ ocupados = [], ocupadosDia = {}, fallaFreeBusy = false, eventoCreado = null } = {}) {
  const llamadas = [];
  return {
    llamadas,
    fetch: async (url, opciones = {}) => {
      llamadas.push({ url, body: opciones.body });

      if (url.includes('oauth2')) {
        return { ok: true, json: async () => ({ access_token: 'tok', expires_in: 3600 }) };
      }

      if (url.includes('/freeBusy')) {
        if (fallaFreeBusy) return { ok: false, status: 500, text: async () => 'boom' };
        const { timeMin } = JSON.parse(opciones.body);
        const dia = enZona(new Date(timeMin), TZ).dia;
        const busy = [
          ...aTramos(dia, ocupados),
          ...Object.entries(ocupadosDia).flatMap(([d, rangos]) => aTramos(d, rangos)),
        ];
        return { ok: true, json: async () => ({ calendars: { 'agenda@scalerics': { busy } } }) };
      }

      // crear evento
      return {
        ok: true,
        json: async () => eventoCreado || { id: 'ev1', hangoutLink: 'https://meet.google.com/abc-defg-hij' },
      };
    },
  };
}

module.exports = { googleFalso };
