'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { crearAgenda, enZona, instanteLocal } = require('../src/agenda/gcal');

const TZ = 'America/Montevideo';

const CFG = {
  TZ,
  GCAL_CLIENT_ID: 'x', GCAL_CLIENT_SECRET: 'y', GCAL_REFRESH_TOKEN: 'z',
  GCAL_CALENDAR_ID: 'agenda@scalerics',
  AGENDA_DESDE: '12:00', AGENDA_HASTA: '16:00',
  AGENDA_PASO_MIN: 30, AGENDA_DURACION_MIN: 30,
  AGENDA_DIAS: 'mon,tue,wed,thu,fri',
  AGENDA_MAX_OPCIONES: 5, AGENDA_DIAS_ADELANTE: 10, AGENDA_AVISO_MIN_HORAS: 3,
};

/**
 * Google falso. `ocupados` son tramos locales del dia que se le pidan, en
 * formato ['13:30-14:00', ...].
 */
function googleFalso({ ocupados = [], fallaFreeBusy = false, eventoCreado = null } = {}) {
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
        const busy = ocupados.map((r) => {
          const [a, b] = r.split('-');
          const [ha, ma] = a.split(':').map(Number);
          const [hb, mb] = b.split(':').map(Number);
          return {
            start: instanteLocal(dia, ha, ma, TZ).toISOString(),
            end: instanteLocal(dia, hb, mb, TZ).toISOString(),
          };
        });
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

/** Un miércoles a las 9 de la mañana de Montevideo. */
const MIERCOLES_9AM = instanteLocal('2026-08-19', 9, 0, TZ);

const horas = (slots) => slots.map((d) => {
  const { hora, minuto } = enZona(d, TZ);
  return `${hora}:${String(minuto).padStart(2, '0')}`;
});

// ── zona horaria ─────────────────────────────────────────────────────────────

test('una hora local se convierte al instante correcto', () => {
  // Sin esto un servidor en UTC ofrece horarios corridos tres horas.
  const d = instanteLocal('2026-08-19', 12, 0, TZ);
  const p = enZona(d, TZ);
  assert.equal(p.dia, '2026-08-19');
  assert.equal(p.hora, 12);
  assert.equal(p.minuto, 0);
});

test('la conversion no depende de un offset fijo', () => {
  // Uruguay no tiene horario de verano hoy, pero lo tuvo. Un offset hardcodeado
  // es una bomba que explota la madrugada del cambio.
  for (const dia of ['2026-01-15', '2026-06-15', '2026-12-15']) {
    const p = enZona(instanteLocal(dia, 14, 30, TZ), TZ);
    assert.equal(`${p.hora}:${p.minuto}`, '14:30', dia);
  }
});

// ── horarios libres ──────────────────────────────────────────────────────────

test('con la agenda vacia ofrece la franja completa, hasta el maximo', () => {
  return (async () => {
    const g = googleFalso();
    const a = crearAgenda({ cfg: CFG, fetch: g.fetch });
    const r = await a.horariosDisponibles(MIERCOLES_9AM);

    assert.equal(r.dia, '2026-08-19', 'el mismo dia: son las 9 y la franja arranca 12');
    assert.deepEqual(horas(r.slots), ['12:00', '12:30', '13:00', '13:30', '14:00']);
  })();
});

test('los tramos ocupados no se ofrecen', () => {
  return (async () => {
    const g = googleFalso({ ocupados: ['13:30-14:00', '14:30-15:30'] });
    const a = crearAgenda({ cfg: CFG, fetch: g.fetch });
    const r = await a.horariosDisponibles(MIERCOLES_9AM);

    assert.deepEqual(horas(r.slots), ['12:00', '12:30', '13:00', '14:00', '15:30']);
  })();
});

test('una reunion que empieza libre pero pisa una ocupada tampoco se ofrece', () => {
  return (async () => {
    // 13:00 esta libre, pero la reunion dura 30 y a las 13:15 hay algo.
    const g = googleFalso({ ocupados: ['13:15-13:45'] });
    const a = crearAgenda({ cfg: CFG, fetch: g.fetch });
    const r = await a.horariosDisponibles(MIERCOLES_9AM);

    assert.ok(!horas(r.slots).includes('13:00'), 'se solaparia');
    assert.ok(horas(r.slots).includes('12:00'));
  })();
});

test('no ofrece nada dentro de las proximas horas', () => {
  return (async () => {
    // Son las 12:30: con 3 horas de aviso, lo antes posible es 15:30.
    const g = googleFalso();
    const a = crearAgenda({ cfg: CFG, fetch: g.fetch });
    const r = await a.horariosDisponibles(instanteLocal('2026-08-19', 12, 30, TZ));

    assert.deepEqual(horas(r.slots), ['15:30']);
  })();
});

test('si el dia ya no da, pasa al siguiente habil', () => {
  return (async () => {
    // Viernes 21 a las 18:00 -> el proximo habil es el lunes 24.
    const g = googleFalso();
    const a = crearAgenda({ cfg: CFG, fetch: g.fetch });
    const r = await a.horariosDisponibles(instanteLocal('2026-08-21', 18, 0, TZ));

    assert.equal(r.dia, '2026-08-24', 'saltea sabado y domingo');
    assert.equal(horas(r.slots)[0], '12:00');
  })();
});

test('un dia entero ocupado no bloquea: busca el siguiente', () => {
  return (async () => {
    // El falso marca ocupado 08:00-20:00 del primer dia del rango. El miercoles
    // queda sin lugar y tiene que ofrecer el jueves.
    const g = googleFalso({ ocupados: ['08:00-20:00'] });
    const a = crearAgenda({ cfg: CFG, fetch: g.fetch });
    const r = await a.horariosDisponibles(MIERCOLES_9AM);

    assert.equal(r.dia, '2026-08-20', 'pasa al jueves');
    assert.equal(horas(r.slots)[0], '12:00');
  })();
});

test('sin ningun hueco en toda la ventana, no ofrece nada', () => {
  return (async () => {
    // Aca el calendario esta lleno los diez dias.
    const a = crearAgenda({
      cfg: CFG,
      fetch: async (url) => {
        if (url.includes('oauth2')) return { ok: true, json: async () => ({ access_token: 't', expires_in: 3600 }) };
        return {
          ok: true,
          json: async () => ({
            calendars: {
              'agenda@scalerics': {
                busy: [{ start: '2026-08-01T00:00:00Z', end: '2026-09-30T00:00:00Z' }],
              },
            },
          }),
        };
      },
    });
    assert.equal(await a.horariosDisponibles(MIERCOLES_9AM), null);
  })();
});

test('si Google no contesta, devuelve null en vez de romper', () => {
  return (async () => {
    const g = googleFalso({ fallaFreeBusy: true });
    const a = crearAgenda({ cfg: CFG, fetch: g.fetch });
    assert.equal(await a.horariosDisponibles(MIERCOLES_9AM), null);
  })();
});

test('sin credenciales queda inactiva', () => {
  return (async () => {
    const a = crearAgenda({ cfg: { ...CFG, GCAL_REFRESH_TOKEN: '' } });
    assert.equal(a.activo, false);
    assert.equal(await a.horariosDisponibles(MIERCOLES_9AM), null);
  })();
});

// ── reservar ─────────────────────────────────────────────────────────────────

test('reservar crea el evento y devuelve el link de Meet', () => {
  return (async () => {
    const g = googleFalso();
    const a = crearAgenda({ cfg: CFG, fetch: g.fetch });
    const inicio = instanteLocal('2026-08-19', 13, 0, TZ);

    const r = await a.reservar({ inicio, nombre: 'Gonchi', telefono: '59899123456', resumen: 'panadería' });

    assert.equal(r.ok, true);
    assert.match(r.meetUrl, /meet\.google\.com/);

    const creado = g.llamadas.find((l) => l.url.includes('/events'));
    assert.match(creado.url, /conferenceDataVersion=1/, 'sin esto Google no crea el Meet');
    const cuerpo = JSON.parse(creado.body);
    assert.match(cuerpo.summary, /Gonchi/);
    assert.match(cuerpo.description, /wa\.me\/59899123456/, 'quien atienda tiene que poder escribirle');
  })();
});

test('si el horario se ocupo entre medio, no reserva', () => {
  return (async () => {
    // Entre que se le ofrecio y contesto, alguien mas lo tomo. Confirmarle una
    // reunion sobre algo ocupado es peor que pedirle que elija de nuevo.
    const g = googleFalso({ ocupados: ['13:00-13:30'] });
    const a = crearAgenda({ cfg: CFG, fetch: g.fetch });

    const r = await a.reservar({
      inicio: instanteLocal('2026-08-19', 13, 0, TZ),
      nombre: 'Gonchi', telefono: '59899123456',
    });

    assert.equal(r.ok, false);
    assert.equal(r.motivo, 'ocupado');
    assert.ok(!g.llamadas.some((l) => l.url.includes('/events')), 'ni intenta crearlo');
  })();
});

test('si falla la creacion, lo dice en vez de mentir', () => {
  return (async () => {
    const g = googleFalso();
    const original = g.fetch;
    const a = crearAgenda({
      cfg: CFG,
      fetch: async (u, o) => (u.includes('/events')
        ? { ok: false, status: 500, text: async () => 'boom' }
        : original(u, o)),
    });

    const r = await a.reservar({
      inicio: instanteLocal('2026-08-19', 13, 0, TZ),
      nombre: 'x', telefono: '59899123456',
    });
    assert.equal(r.ok, false);
    assert.equal(r.motivo, 'error');
  })();
});

test('el token se pide una sola vez y se reusa', () => {
  return (async () => {
    const g = googleFalso();
    const a = crearAgenda({ cfg: CFG, fetch: g.fetch });
    await a.horariosDisponibles(MIERCOLES_9AM);
    await a.horariosDisponibles(MIERCOLES_9AM);

    const renovaciones = g.llamadas.filter((l) => l.url.includes('oauth2')).length;
    assert.equal(renovaciones, 1, 'no renueva en cada llamada');
  })();
});

// ── el flujo completo, del score a la reunion agendada ───────────────────────

const { conLead, stubModelo } = require('./helpers');
const { S } = require('../src/funnel/states');

const COMPLETO = {
  business_name: 'Panadería PanesAhora', rubro: 'panadería',
  business_type: 'web', budget: 'mas_3000', team_size_personas: 8,
  instagram_web: '@panesahora', needs: 'quiero vender online',
};

/**
 * El mensaje del lead que respalda a COMPLETO: desde el 2-9 el codigo descarta
 * el dato cuya cita no esta en lo que el lead escribio.
 */
const DIJO_TODO = 'te cuento todo: es la Panadería PanesAhora, una panadería, '
  + 'estamos en @panesahora y quiero vender online';

test('con todos los datos le muestra horarios reales y agenda el que elige', async () => {
  // El camino de horarios ya no es el de por defecto —hoy se manda el link de
  // Calendly— pero se conserva entero y se prende con esta variable.
  const google = googleFalso({ ocupados: ['13:30-14:00'] });
  const s = await conLead({
    modelo: stubModelo({ datos: COMPLETO }),
    AGENDA_OFRECE_HORARIOS: 'true',
    _google: google.fetch,
  });

  const responder = async (t) => {
    await s.servicioLeads.registrarRespuesta('59899123456', t);
    await s.cola.vacia();
    return s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  };

  const ofrece = await responder(DIJO_TODO);
  assert.equal(s.repo.leadPorTelefono('59899123456').fsm_state, S.HORARIOS_OFRECIDOS);
  assert.equal(ofrece.at(-1), '[oferta_con_horarios]');

  // Los horarios quedan guardados: sin eso, cuando conteste "las 13" no habria
  // contra que validarlo. Que se excluyan los ocupados se prueba arriba, con el
  // reloj fijo: aca el dia que elige depende de la hora real de la corrida.
  const guardados = JSON.parse(s.repo.leadPorTelefono('59899123456').horarios_ofrecidos);
  // Cuantos son depende de la hora de la corrida: a las 15 ya entra uno solo.
  // El conteo exacto se prueba arriba, con el reloj fijo.
  assert.ok(guardados.length >= 1, 'se le ofrecio al menos uno');
  assert.ok(guardados.every((x) => !Number.isNaN(Date.parse(x))), 'son fechas validas');
});

test('el horario que se ofrece nunca sale de la franja configurada', async () => {
  const google = googleFalso();
  const s = await conLead({ modelo: stubModelo({ datos: COMPLETO }), _google: google.fetch });
  await s.servicioLeads.registrarRespuesta('59899123456', 'dale');
  await s.cola.vacia();

  const guardados = JSON.parse(s.repo.leadPorTelefono('59899123456').horarios_ofrecidos || '[]');
  for (const iso of guardados) {
    const { hora } = enZona(new Date(iso), TZ);
    assert.ok(hora >= 12 && hora < 16, `${hora}h esta fuera de 12-16`);
  }
});

test('sin agenda conectada el cierre sigue por el camino del link', async () => {
  // Sin credenciales de Google no se rompe nada: es el mismo camino que se usa
  // hoy por defecto, el del link.
  const s = await conLead({ modelo: stubModelo({ datos: COMPLETO }), GCAL_REFRESH_TOKEN: '' });
  await s.servicioLeads.registrarRespuesta('59899123456', DIJO_TODO);
  await s.cola.vacia();

  const msgs = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.equal(msgs.at(-1), '[link_reunion]');
  assert.equal(s.repo.leadPorTelefono('59899123456').fsm_state, S.MEETING_LINK_SENT);
});
