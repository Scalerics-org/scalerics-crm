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
  // En produccion son 2, para que las opciones abarquen varios dias. Aca va
  // alto a proposito: los tests de abajo miden que se ELIGE dentro de un dia
  // —que no se ofrezca lo ocupado, lo que se solapa, lo que cae antes del piso—
  // y con el tope de produccion se cortarian antes de llegar al caso. El
  // reparto entre dias tiene su propio test, que baja el tope a 2.
  AGENDA_MAX_POR_DIA: 10,
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
    const ahora = instanteLocal('2026-08-19', 12, 30, TZ);
    const r = await a.horariosDisponibles(ahora);

    assert.equal(horas(r.slots)[0], '15:30', 'lo antes posible es 15:30');
    // Los que siguen son de otros dias —desde que las opciones abarcan— pero
    // ninguno puede caer antes del piso de aviso.
    const piso = new Date(ahora.getTime() + CFG.AGENDA_AVISO_MIN_HORAS * 3600_000);
    for (const s of r.slots) assert.ok(s >= piso, `${s.toISOString()} cae antes del piso`);
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

/**
 * El bug del 2-9, tal cual paso probando el bot:
 *
 *   → Tenemos estos horarios: 12:00, 12:30, 13:00, 13:30, 14:30. ¿Cuál te viene bien?
 *   ← 15:00
 *   → Perfecto. Jueves 3 a las 12:00. Te paso el link...
 *
 * Pidio una hora que no estaba en la lista y el bot le agendo otra, y se la
 * confirmo como si fuera la que pidio. Con un cliente real, eso es alguien
 * conectandose a una hora y nosotros a otra.
 *
 * El modelo elige de un enum cerrado con los horarios ofrecidos, asi que cuando
 * el que el lead quiere no esta no puede decir "ninguno": devuelve el que menos
 * le disgusta. Ahora el codigo lo verifica.
 */
test('si pide una hora que no se le ofrecio, no se le agenda otra', async () => {
  const google = googleFalso();
  // El modelo elige de un enum cerrado con los horarios ofrecidos: cuando el
  // que el lead quiere no esta, en vez de "ninguno" devuelve el primero. Eso es
  // exactamente lo que hizo el 2-9, asi que el stub lo imita.
  const modelo = stubModelo({ datos: COMPLETO });
  const original = modelo.pedir.bind(modelo);
  modelo.pedir = async (args) => {
    if (args.herramienta?.nombre === 'elegir') {
      const opciones = args.herramienta.parametros.properties.opcion.enum;
      return { texto: null, argumentos: { opcion: opciones[0] } };
    }
    return original(args);
  };

  const s = await conLead({
    modelo,
    AGENDA_OFRECE_HORARIOS: 'true',
    _google: google.fetch,
  });

  const responder = async (t) => {
    await s.servicioLeads.registrarRespuesta('59899123456', t);
    await s.cola.vacia();
    return s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  };

  await responder(DIJO_TODO);
  assert.equal(s.repo.leadPorTelefono('59899123456').fsm_state, S.HORARIOS_OFRECIDOS);

  // La franja es 12 a 16, asi que las 23 no se ofrecieron nunca.
  const msgs = await responder('23:00');

  const l = s.repo.leadPorTelefono('59899123456');
  assert.equal(l.meeting_time, null, 'no agendo nada');
  assert.equal(l.fsm_state, S.HORARIOS_OFRECIDOS, 'sigue esperando que elija');
  assert.equal(msgs.at(-1), '[horario_no_entendido]', 'le vuelve a mostrar los que hay');
  assert.ok(!google.llamadas.some((c) => c.url.includes('/events?')), 'y no toco el calendario');
});

/**
 * Antes se devolvia el PRIMER dia con hueco y se cortaba ahi, asi que el lead
 * veia cinco horarios de un solo dia y ninguna forma de pedir otro. Si mañana
 * no le sirve —que es lo normal cuando alguien tiene un negocio— no tiene nada
 * que elegir, y el bot le da a entender que no se puede agendar mas adelante.
 */
test('los horarios ofrecidos abarcan varios dias, no solo el primero', async () => {
  const google = googleFalso();
  const a = crearAgenda({ cfg: { ...CFG, AGENDA_MAX_OPCIONES: 6, AGENDA_MAX_POR_DIA: 2 }, fetch: google.fetch });

  const r = await a.horariosDisponibles(new Date('2026-09-03T09:00:00Z'));

  const dias = new Set(r.slots.map((d) => enZona(d, TZ).dia));
  assert.ok(dias.size >= 2, `esperaba varios dias, vinieron ${[...dias].join(', ')}`);
  assert.ok(r.slots.length <= 6);
  for (const dia of dias) {
    const enEseDia = r.slots.filter((d) => enZona(d, TZ).dia === dia);
    assert.ok(enEseDia.length <= 2, `${dia} trajo ${enEseDia.length}, el tope es 2`);
  }
  // Ordenados: el lead los lee como una lista de "lo mas pronto primero".
  const ordenados = [...r.slots].sort((x, y) => x - y);
  assert.deepEqual(r.slots.map(Number), ordenados.map(Number));
});

/**
 * Los horarios ahora vienen de varios dias, asi que decir "libres para el
 * jueves" y listar horas sueltas seria mentira: el lead elegiria "13:00"
 * creyendo que es el jueves cuando es el viernes.
 */
test('el contexto que recibe el modelo dice el dia de cada horario', async () => {
  const google = googleFalso();
  const modelo = stubModelo({ datos: COMPLETO });
  const s = await conLead({ modelo, AGENDA_OFRECE_HORARIOS: 'true', _google: google.fetch });

  await s.servicioLeads.registrarRespuesta('59899123456', DIJO_TODO);
  await s.cola.vacia();

  const prompt = modelo.llamadas
    .map((a) => a.mensajes?.[0]?.content || '')
    .find((c) => c.includes('oferta_con_horarios'));
  assert.ok(prompt, 'se le pidio el mensaje de oferta');

  const ofrecidos = JSON.parse(s.repo.leadPorTelefono('59899123456').horarios_ofrecidos);
  const dias = new Set(ofrecidos.map((iso) => enZona(new Date(iso), TZ).dia));
  assert.ok(dias.size >= 2, 'la prueba necesita horarios de varios dias');

  for (const iso of ofrecidos) {
    const d = new Date(iso);
    const hora = new Intl.DateTimeFormat('es-UY', {
      timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(d);
    const numeroDeDia = String(Number(enZona(d, TZ).dia.slice(8)));
    const linea = prompt.split('\n').find((l) => l.includes(hora) && l.includes(numeroDeDia));
    assert.ok(linea, `${hora} tiene que aparecer junto a su dia (${numeroDeDia})`);
  }
});

test('libreEn dice si un hueco puntual esta libre', () => {
  return (async () => {
    const g = googleFalso({ ocupados: ['13:00-13:30'] });
    const a = crearAgenda({ cfg: CFG, fetch: g.fetch });

    assert.equal(await a.libreEn(instanteLocal('2026-08-19', 13, 0, TZ)), false);
    assert.equal(await a.libreEn(instanteLocal('2026-08-19', 15, 0, TZ)), true);
  })();
});

test('si Google no contesta, libreEn dice null y no inventa', () => {
  return (async () => {
    // Devolver true agendaria encima de algo; devolver false perderia una
    // reunion que si se podia. Null deja que el que llama decida.
    const g = googleFalso({ fallaFreeBusy: true });
    const a = crearAgenda({ cfg: CFG, fetch: g.fetch });
    assert.equal(await a.libreEn(instanteLocal('2026-08-19', 15, 0, TZ)), null);
  })();
});

/**
 * Lo que paso el 2-9 despues de arreglar lo anterior:
 *
 *   → Te dejo los horarios: jue 12:00, jue 12:30, vie 12:00, vie 12:30, lun 12:00
 *   ← Jueves a las 5 de la mañana
 *   → [la misma lista]
 *   ← No tienen disponible jueves 8:30?
 *   → [la misma lista otra vez]
 *
 * Dos cosas mal. La lista son cinco sugerencias repartidas en dias, pero en una
 * franja de 12 a 16 cada media hora entran OCHO por dia: pedir las 13:00 —que
 * estan libres— se contestaba con la misma lista. Y cuando la hora de verdad no
 * se puede, repetir la lista sin decir por que se lee como que el bot no
 * escucha.
 */
function conHoraPedida(hhmm, extra = {}) {
  const [h, m] = hhmm.split(':').map(Number);
  const modelo = stubModelo({ datos: COMPLETO });
  const original = modelo.pedir.bind(modelo);
  modelo.pedir = async (args) => {
    if (args.herramienta?.nombre === 'elegir') return { texto: null, argumentos: { opcion: 'ninguno' } };
    if (args.herramienta?.nombre === 'momento') {
      return { texto: null, argumentos: { dia: extra.dia || '2026-09-03', hora: h, minuto: m } };
    }
    return original(args);
  };
  return modelo;
}

test('una hora libre que no estaba en la lista igual se agenda', async () => {
  const google = googleFalso();
  const s = await conLead({
    modelo: conHoraPedida('15:00'),
    AGENDA_OFRECE_HORARIOS: 'true',
    _google: google.fetch,
    ahora: instanteLocal('2026-09-03', 9, 0, TZ),
  });

  await s.servicioLeads.registrarRespuesta('59899123456', DIJO_TODO);
  await s.cola.vacia();
  await s.servicioLeads.registrarRespuesta('59899123456', 'las 15:00 me sirve más');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono('59899123456');
  assert.ok(l.meeting_time, 'agendo');
  assert.equal(enZona(new Date(l.meeting_time), TZ).hora, 15, 'la hora que pidio');
});

test('una hora fuera de la franja se rechaza diciendo por que', async () => {
  const google = googleFalso();
  const s = await conLead({
    modelo: conHoraPedida('05:00'),
    AGENDA_OFRECE_HORARIOS: 'true',
    _google: google.fetch,
    ahora: instanteLocal('2026-09-03', 9, 0, TZ),
  });

  await s.servicioLeads.registrarRespuesta('59899123456', DIJO_TODO);
  await s.cola.vacia();
  await s.servicioLeads.registrarRespuesta('59899123456', 'jueves a las 5 de la mañana');
  await s.cola.vacia();

  const msgs = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.equal(s.repo.leadPorTelefono('59899123456').meeting_time, null, 'no agendo nada');
  assert.equal(msgs.at(-1), '[horario_fuera_de_franja]', 'le explica, no repite la lista');
});

/**
 * Los horarios que se muestran se reparten a lo largo del dia, no son los
 * primeros de la fila.
 *
 * Con la franja de 12 a 16 cada media hora hay ocho huecos por dia. Mostrando
 * los dos primeros, el lead ve "12:00 o 12:30" y entiende que a la tarde no
 * atendemos — aunque las 15:00 esten libres y ahora se las podamos agendar si
 * las pide. La sugerencia tiene que dejar ver el rango.
 */
test('las sugerencias de un dia se reparten a lo largo de la franja', () => {
  return (async () => {
    const g = googleFalso();
    const a = crearAgenda({
      cfg: { ...CFG, AGENDA_MAX_OPCIONES: 3, AGENDA_MAX_POR_DIA: 3 },
      fetch: g.fetch,
    });
    const r = await a.horariosDisponibles(MIERCOLES_9AM);

    const hs = horas(r.slots);
    assert.equal(hs.length, 3);
    assert.equal(hs[0], '12:00', 'la primera es la mas temprana');
    assert.equal(hs.at(-1), '15:30', 'la ultima es la mas tardia');
    assert.ok(!hs.includes('12:30'), 'no son las tres primeras pegadas');
  })();
});

/**
 * Que Google deje de contestar no puede pasar en silencio.
 *
 * Hoy `horariosDisponibles` devuelve null, queda un warn en el log y el embudo
 * cae al camino del link de Calendly. Funciona —el lead igual puede agendar—
 * pero nadie se entera de que la agenda se cayo, y el bot deja de hacer lo
 * unico que lo diferencia. El token de Google es el que sostiene todo esto: si
 * se revoca o vence, esto es lo unico que lo va a delatar.
 */
test('si la agenda falla, el equipo se entera', async () => {
  const google = googleFalso({ fallaFreeBusy: true });
  const s = await conLead({
    modelo: stubModelo({ datos: COMPLETO }),
    AGENDA_OFRECE_HORARIOS: 'true',
    _google: google.fetch,
  });

  await s.servicioLeads.registrarRespuesta('59899123456', DIJO_TODO);
  await s.cola.vacia();

  const alEquipo = s.proveedor.getEnviados()
    .filter((e) => e.to !== '59899123456')
    .map((e) => e.texto);
  assert.ok(alEquipo.some((t) => /agenda/i.test(t)), `no hubo aviso: ${JSON.stringify(alEquipo)}`);

  // Y el lead no se queda sin respuesta: sigue por el camino del link.
  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.equal(alLead.at(-1), '[link_reunion]');
});

test('no repite el aviso en cada lead', async () => {
  const google = googleFalso({ fallaFreeBusy: true });
  const s = await conLead({
    modelo: stubModelo({ datos: COMPLETO }),
    AGENDA_OFRECE_HORARIOS: 'true',
    _google: google.fetch,
  });

  await s.servicioLeads.registrarRespuesta('59899123456', DIJO_TODO);
  await s.cola.vacia();
  await s.servicioLeads.registrarRespuesta('59899123456', DIJO_TODO);
  await s.cola.vacia();

  const avisos = s.proveedor.getEnviados()
    .filter((e) => e.to !== '59899123456' && /agenda/i.test(e.texto));
  assert.equal(avisos.length, 1, 'uno solo, no uno por conversacion');
});

/**
 * En una franja de 07 a 20 cada media hora hay 25 huecos por dia. Mostrar tres
 * hace parecer que no hay lugar; listar los 25 es ilegible por WhatsApp.
 *
 * Se dicen en tramos, como lo diria una persona: "de 7 a 14 y de 14:30 a 20".
 */
test('los horarios libres se agrupan en tramos', () => {
  return (async () => {
    const g = googleFalso({ ocupados: ['14:00-14:30'] });
    const a = crearAgenda({
      cfg: { ...CFG, AGENDA_DESDE: '12:00', AGENDA_HASTA: '16:00' },
      fetch: g.fetch,
    });
    const r = await a.horariosDisponibles(MIERCOLES_9AM);

    // 12:00 a 14:00 (la de 13:30 termina 14:00) y despues 14:30 a 16:00.
    assert.equal(r.bloques.length, 2);
    assert.equal(horas([r.bloques[0].desde])[0], '12:00');
    assert.equal(horas([r.bloques[0].hasta])[0], '14:00');
    assert.equal(horas([r.bloques[1].desde])[0], '14:30');
    assert.equal(horas([r.bloques[1].hasta])[0], '16:00');
  })();
});

test('un dia entero libre es un solo tramo', () => {
  return (async () => {
    const g = googleFalso();
    const a = crearAgenda({ cfg: { ...CFG, AGENDA_DESDE: '12:00', AGENDA_HASTA: '16:00' }, fetch: g.fetch });
    const r = await a.horariosDisponibles(MIERCOLES_9AM);

    const delMiercoles = r.bloques.filter((b) => enZona(b.desde, TZ).dia === '2026-08-19');
    assert.equal(delMiercoles.length, 1);
    assert.equal(horas([delMiercoles[0].desde])[0], '12:00');
    assert.equal(horas([delMiercoles[0].hasta])[0], '16:00');
  })();
});
