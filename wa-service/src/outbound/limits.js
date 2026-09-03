'use strict';

const DIAS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];

/**
 * Rampa de warm-up: un numero recien dado de alta que arranca mandando 30
 * mensajes por dia se banea. Limite de CONTACTOS NUEVOS por dia segun la
 * antiguedad del numero.
 */
const WARMUP = [
  { hastaDia: 3, maxNuevosPorDia: 5 },
  { hastaDia: 7, maxNuevosPorDia: 15 },
  { hastaDia: 14, maxNuevosPorDia: 40 },
];

/** Hora local en la zona configurada, sin depender de la del servidor. */
function enZona(fecha, tz) {
  const partes = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, hour12: false,
    weekday: 'short', hour: '2-digit', minute: '2-digit',
  }).formatToParts(fecha);
  const buscar = (t) => partes.find((p) => p.type === t)?.value;
  return {
    hora: parseInt(buscar('hour'), 10) % 24,
    minuto: parseInt(buscar('minute'), 10),
    dia: buscar('weekday').toLowerCase().slice(0, 3),
  };
}

/** "09:00-19:00" -> {desde: 540, hasta: 1140} en minutos desde medianoche. */
function parsearHorario(rango) {
  const m = /^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$/.exec(String(rango).trim());
  if (!m) throw new Error(`BUSINESS_HOURS invalido: "${rango}" (formato esperado 09:00-19:00)`);
  return {
    desde: parseInt(m[1], 10) * 60 + parseInt(m[2], 10),
    hasta: parseInt(m[3], 10) * 60 + parseInt(m[4], 10),
  };
}

/** "mon-sat" o "mon,tue,fri" -> Set de dias. */
function parsearDias(spec) {
  const t = String(spec).trim().toLowerCase();
  const rango = /^([a-z]{3})\s*-\s*([a-z]{3})$/.exec(t);
  if (rango) {
    const i = DIAS.indexOf(rango[1]);
    const f = DIAS.indexOf(rango[2]);
    if (i < 0 || f < 0) throw new Error(`BUSINESS_DAYS invalido: "${spec}"`);
    const dias = new Set();
    for (let d = i; ; d = (d + 1) % 7) {
      dias.add(DIAS[d]);
      if (d === f) break;
    }
    return dias;
  }
  const dias = new Set(t.split(',').map((d) => d.trim()).filter(Boolean));
  if (![...dias].every((d) => DIAS.includes(d))) throw new Error(`BUSINESS_DAYS invalido: "${spec}"`);
  return dias;
}

function crearLimites({ repo, cfg, logger }) {
  const horario = parsearHorario(cfg.BUSINESS_HOURS);
  const diasHabiles = parsearDias(cfg.BUSINESS_DAYS);

  const haceHoras = (ahora, h) => new Date(ahora.getTime() - h * 3600_000).toISOString();

  function enHorario(fecha) {
    const { hora, minuto, dia } = enZona(fecha, cfg.TZ);
    if (!diasHabiles.has(dia)) return false;
    const min = hora * 60 + minuto;
    return min >= horario.desde && min < horario.hasta;
  }

  /** Proximo momento dentro del horario comercial, con jitter de 0-20 min. */
  function proximaApertura(desde) {
    const cursor = new Date(desde.getTime());
    // Avanza de a 15 minutos hasta 8 dias; con horarios sanos cae enseguida.
    for (let i = 0; i < 8 * 24 * 4; i++) {
      if (enHorario(cursor)) {
        const jitter = Math.floor(Math.random() * 20) * 60_000;
        return new Date(cursor.getTime() + jitter);
      }
      cursor.setTime(cursor.getTime() + 15 * 60_000);
    }
    return new Date(desde.getTime() + 3600_000);
  }

  /** Limite de contactos nuevos por dia segun la antiguedad del numero. */
  function cupoWarmup(ahora) {
    if (!cfg.WARMUP_START_DATE) return null;
    const alta = new Date(`${cfg.WARMUP_START_DATE}T00:00:00Z`);
    if (Number.isNaN(alta.getTime())) return null;
    const dias = Math.floor((ahora - alta) / 86_400_000) + 1;
    const tramo = WARMUP.find((t) => dias <= t.hastaDia);
    return tramo ? tramo.maxNuevosPorDia : null;
  }

  return {
    enHorario,
    proximaApertura,
    cupoWarmup,

    /**
     * @returns {{ok: true} | {ok: false, motivo: string, reintentarEn: Date}}
     */
    permitido({ esPrimerContacto, esInterno, esRespuesta = false, ahora = new Date() }) {
      // Los avisos al AM son contacto interno: sin limites ni horario.
      if (esInterno) return { ok: true };

      /**
       * La ventana frena lo que el bot INICIA, no lo que contesta.
       *
       * Es la misma distincion que hace el limite de contactos nuevos, dos
       * comentarios mas abajo: contestarle a alguien que te escribio es el
       * trafico de menor riesgo que existe, y encima la decision de estar
       * despierto a las 23:00 la tomo el lead, no nosotros. Frenarlo hasta las
       * 9 de la manana era perder el momento por nada.
       *
       * Un follow-up, un nurture o un recordatorio si esperan: esos aparecen
       * sin que nadie los haya pedido, y ahi la hora importa.
       */
      if (!esRespuesta && !enHorario(ahora)) {
        return { ok: false, motivo: 'fuera de horario', reintentarEn: proximaApertura(ahora) };
      }

      const enLaHora = repo.enviosDesde(haceHoras(ahora, 1));
      if (enLaHora >= cfg.MAX_MSGS_PER_HOUR) {
        return {
          ok: false, motivo: 'limite por hora',
          reintentarEn: new Date(ahora.getTime() + 15 * 60_000),
        };
      }

      const enElDia = repo.enviosDesde(haceHoras(ahora, 24));
      if (enElDia >= cfg.MAX_MSGS_PER_DAY) {
        return {
          ok: false, motivo: 'limite diario',
          reintentarEn: new Date(ahora.getTime() + 3600_000),
        };
      }

      if (esPrimerContacto) {
        const nuevosHora = repo.nuevosDesde(haceHoras(ahora, 1));
        if (nuevosHora >= cfg.MAX_NEW_CONTACTS_PER_HOUR) {
          return {
            ok: false, motivo: 'limite de contactos nuevos por hora',
            reintentarEn: new Date(ahora.getTime() + 15 * 60_000),
          };
        }

        const cupo = cupoWarmup(ahora);
        if (cupo !== null) {
          const nuevosDia = repo.nuevosDesde(haceHoras(ahora, 24));
          if (nuevosDia >= cupo) {
            logger?.warn({ cupo, nuevosDia }, 'cupo de warm-up agotado');
            return {
              ok: false, motivo: `warm-up (max ${cupo} contactos nuevos por dia)`,
              reintentarEn: new Date(ahora.getTime() + 3600_000),
            };
          }
        }
      }

      return { ok: true };
    },
  };
}

module.exports = { crearLimites, enZona, parsearHorario, parsearDias, WARMUP };
