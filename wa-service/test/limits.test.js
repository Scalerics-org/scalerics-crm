'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { crearLimites, parsearHorario, parsearDias, enZona } = require('../src/outbound/limits');
const { jitter, duracionTyping, entre } = require('../src/outbound/humanize');
const { cargar } = require('../src/config');

const MIN = { WA_API_KEY: 'una-clave-bien-larga-1234' };

// Lunes 12:00, domingo 03:00 y lunes 03:00, hora de Montevideo (UTC-3).
const LUNES_MEDIODIA = new Date('2026-08-10T15:00:00Z');
const DOMINGO_MADRUGADA = new Date('2026-08-09T06:00:00Z');
const LUNES_MADRUGADA = new Date('2026-08-10T06:00:00Z');

/**
 * Repo de mentira con contadores fijos. Distingue la ventana de 1h de la de 24h
 * midiendo contra el mismo instante que usa el test, no contra Date.now(): las
 * fechas de los casos son fijas y no tienen por que ser hoy.
 */
function repoFalso({ enviosHora = 0, enviosDia = 0, nuevosHora = 0, nuevosDia = 0 } = {}, ahora) {
  const esVentanaDiaria = (desde) =>
    (ahora.getTime() - new Date(desde).getTime()) / 3600_000 > 2;
  return {
    enviosDesde: (desde) => (esVentanaDiaria(desde) ? enviosDia : enviosHora),
    nuevosDesde: (desde) => (esVentanaDiaria(desde) ? nuevosDia : nuevosHora),
  };
}

function limites(cfgExtra = {}, contadores = {}, ahora = LUNES_MEDIODIA) {
  const cfg = cargar({ ...MIN, TZ: 'America/Montevideo', ...cfgExtra });
  return crearLimites({ repo: repoFalso(contadores, ahora), cfg, logger: null });
}

test('parsea el horario y los dias', () => {
  assert.deepEqual(parsearHorario('09:00-19:00'), { desde: 540, hasta: 1140 });
  assert.throws(() => parsearHorario('9 a 19'), /BUSINESS_HOURS invalido/);

  assert.deepEqual([...parsearDias('mon-sat')].sort(), ['fri', 'mon', 'sat', 'thu', 'tue', 'wed']);
  assert.deepEqual([...parsearDias('mon,wed,fri')].sort(), ['fri', 'mon', 'wed']);
  assert.throws(() => parsearDias('lunes-viernes'), /BUSINESS_DAYS invalido/);
});

test('la hora se calcula en la zona configurada, no en la del servidor', () => {
  // 15:00 UTC son las 12:00 en Montevideo.
  assert.deepEqual(enZona(LUNES_MEDIODIA, 'America/Montevideo'), { hora: 12, minuto: 0, dia: 'mon' });
  assert.deepEqual(enZona(LUNES_MEDIODIA, 'UTC'), { hora: 15, minuto: 0, dia: 'mon' });
});

test('deja mandar en horario comercial', () => {
  const l = limites();
  assert.equal(l.permitido({ ahora: LUNES_MEDIODIA }).ok, true);
});

test('fuera de horario reprograma para la proxima apertura', () => {
  const l = limites();
  const r = l.permitido({ ahora: LUNES_MADRUGADA });

  assert.equal(r.ok, false);
  assert.equal(r.motivo, 'fuera de horario');
  assert.ok(r.reintentarEn > LUNES_MADRUGADA);
  // Cae dentro del horario del mismo lunes (mas hasta 20 min de jitter).
  const { hora } = enZona(r.reintentarEn, 'America/Montevideo');
  assert.ok(hora >= 9 && hora <= 9 + 1, `esperaba cerca de las 9, dio ${hora}`);
});

test('el domingo no se manda si BUSINESS_DAYS es mon-sat', () => {
  const l = limites();
  const r = l.permitido({ ahora: DOMINGO_MADRUGADA });
  assert.equal(r.ok, false);
  const { dia } = enZona(r.reintentarEn, 'America/Montevideo');
  assert.equal(dia, 'mon', 'se pasa para el lunes');
});

test('los avisos internos al AM ignoran horario y limites', () => {
  const l = limites({}, { enviosHora: 999, nuevosHora: 999 });
  assert.equal(l.permitido({ esInterno: true, ahora: DOMINGO_MADRUGADA }).ok, true);
});

test('corta al llegar al limite por hora', () => {
  const l = limites({ MAX_MSGS_PER_HOUR: '30' }, { enviosHora: 30 });
  const r = l.permitido({ ahora: LUNES_MEDIODIA });
  assert.equal(r.ok, false);
  assert.equal(r.motivo, 'limite por hora');
});

test('corta al llegar al limite diario', () => {
  const l = limites({ MAX_MSGS_PER_DAY: '200' }, { enviosHora: 1, enviosDia: 200 });
  const r = l.permitido({ ahora: LUNES_MEDIODIA });
  assert.equal(r.ok, false);
  assert.equal(r.motivo, 'limite diario');
});

test('el limite de contactos nuevos solo aplica al primer contacto', () => {
  const l = limites({ MAX_NEW_CONTACTS_PER_HOUR: '12' }, { nuevosHora: 12 });

  const nuevo = l.permitido({ esPrimerContacto: true, ahora: LUNES_MEDIODIA });
  assert.equal(nuevo.ok, false);
  assert.match(nuevo.motivo, /contactos nuevos/);

  const conocido = l.permitido({ esPrimerContacto: false, ahora: LUNES_MEDIODIA });
  assert.equal(conocido.ok, true, 'a quien ya escribio se le puede contestar');
});

// ── warm-up ───────────────────────────────────────────────────────────────────

test('la rampa de warm-up achica el cupo los primeros dias', () => {
  const alta = '2026-08-10';
  const dia = (n) => new Date(`2026-08-${String(9 + n).padStart(2, '0')}T15:00:00Z`);

  assert.equal(limites({ WARMUP_START_DATE: alta }).cupoWarmup(dia(1)), 5);
  assert.equal(limites({ WARMUP_START_DATE: alta }).cupoWarmup(dia(3)), 5);
  assert.equal(limites({ WARMUP_START_DATE: alta }).cupoWarmup(dia(4)), 15);
  assert.equal(limites({ WARMUP_START_DATE: alta }).cupoWarmup(dia(7)), 15);
  assert.equal(limites({ WARMUP_START_DATE: alta }).cupoWarmup(dia(8)), 40);
  assert.equal(limites({ WARMUP_START_DATE: alta }).cupoWarmup(dia(14)), 40);
  assert.equal(limites({ WARMUP_START_DATE: alta }).cupoWarmup(dia(15)), null, 'a los 15 dias se libera');
});

test('sin WARMUP_START_DATE no hay rampa', () => {
  assert.equal(limites().cupoWarmup(LUNES_MEDIODIA), null);
});

test('el warm-up frena un numero nuevo aunque no llegue al limite por hora', () => {
  const l = limites(
    { WARMUP_START_DATE: '2026-08-10', MAX_NEW_CONTACTS_PER_HOUR: '12' },
    { nuevosHora: 3, nuevosDia: 5 }
  );
  const r = l.permitido({ esPrimerContacto: true, ahora: new Date('2026-08-10T15:00:00Z') });
  assert.equal(r.ok, false);
  assert.match(r.motivo, /warm-up/);
});

// ── humanize ──────────────────────────────────────────────────────────────────

test('el jitter se agrupa al centro en vez de repartirse parejo', () => {
  const muestras = Array.from({ length: 4000 }, () => jitter(0, 1000));
  assert.ok(muestras.every((v) => v >= 0 && v <= 1000), 'respeta el rango');

  const centro = muestras.filter((v) => v > 250 && v < 750).length / muestras.length;
  // Con uniforme daria ~0.50; promediando dos sorteos da ~0.69.
  assert.ok(centro > 0.6, `esperaba concentracion al centro, dio ${centro.toFixed(2)}`);

  assert.ok(new Set(muestras).size > 100, 'no devuelve siempre lo mismo');
});

test('el typing es proporcional al texto y tiene techo', () => {
  const corto = duracionTyping('hola');
  const largo = duracionTyping('x'.repeat(500));
  assert.ok(corto < largo);
  assert.ok(largo <= 6000 * 1.3 + 1, 'no se pasa del techo ni con el jitter');

  // Dos veces el mismo texto no tarda igual.
  const veces = new Set(Array.from({ length: 50 }, () => duracionTyping('un mensaje de prueba')));
  assert.ok(veces.size > 5, 'el mismo texto varia entre envios');
});

test('entre() respeta los bordes', () => {
  assert.equal(entre(5, 5), 5);
  assert.equal(entre(10, 3), 10, 'max menor que min devuelve min');
  const v = Array.from({ length: 200 }, () => entre(2, 4));
  assert.ok(v.every((x) => x >= 2 && x <= 4));
});
