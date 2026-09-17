'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { conLead, stubModelo } = require('./helpers');

const TEL = '59899123456';

const enHoras = (n) => new Date(Date.now() + n * 3600_000);

/** Deja al lead con una reunion y sus dos recordatorios vencidos. */
async function conReunionEn(s, horas) {
  const l = s.repo.leadPorTelefono(TEL);
  s.repo.registrarReunion(l.id, {
    meetingTime: enHoras(horas).toISOString(),
    meetingUrl: 'https://meet.google.com/x',
    ahoraIso: new Date().toISOString(),
  });
  const vencido = new Date(Date.now() - 3600_000).toISOString();
  for (const tipo of ['reminder_24h', 'reminder_30m']) {
    s.repo.encolarJob(l.id, tipo, vencido);
    s.repo.db.prepare('UPDATE jobs SET run_at = ? WHERE lead_id = ? AND type = ?')
      .run(vencido, l.id, tipo);
  }
  await s.cola.vacia();
  s.proveedor.limpiar();
  return l;
}

const estados = (s, leadId) => Object.fromEntries(
  s.repo.db.prepare('SELECT type, status FROM jobs WHERE lead_id = ?').all(leadId)
    .map((j) => [j.type, j.status])
);

/**
 * Un recordatorio que sale tarde miente.
 *
 * Si el servicio estuvo caido unas horas, los dos jobs quedan vencidos y salen
 * juntos: al lead le llegan "mañana tenemos la videollamada" y "es en 30
 * minutos" con segundos de diferencia. Paso exactamente asi en una prueba.
 */
test('si los dos quedaron vencidos, sale solo el que dice la verdad', async () => {
  const s = await conLead();
  const l = await conReunionEn(s, 0.5);

  await s.scheduler.correrVencidos();
  await s.cola.vacia();

  const j = estados(s, l.id);
  assert.equal(j.reminder_24h, 'cancelled', 'el del dia antes ya no aplica');
  assert.equal(j.reminder_30m, 'done');

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === TEL);
  assert.equal(alLead.length, 1, 'uno solo, no dos que se contradicen');
});

test('el del dia antes sale cuando de verdad falta un dia', async () => {
  const s = await conLead();
  const l = await conReunionEn(s, 25);

  await s.scheduler.correrVencidos();
  await s.cola.vacia();

  assert.equal(estados(s, l.id).reminder_24h, 'done');
});

test('a una reunion que ya paso no se le recuerda nada', async () => {
  const s = await conLead();
  const l = await conReunionEn(s, -2);

  await s.scheduler.correrVencidos();
  await s.cola.vacia();

  const j = estados(s, l.id);
  assert.equal(j.reminder_24h, 'cancelled');
  assert.equal(j.reminder_30m, 'cancelled');
  assert.equal(s.proveedor.getEnviados().filter((e) => e.to === TEL).length, 0);
});

/**
 * El modelo decia "Mañana tenemos la videollamada" para una reunion que era en
 * tres dias: el prompt lo tenia clavado y el modelo obedecio. Ahora la cuenta la
 * hace el codigo y se la pasa hecha.
 */
test('al redactor se le dice cuánto falta, no solo la fecha', async () => {
  const modelo = stubModelo();
  const s = await conLead({ modelo });
  await conReunionEn(s, 25);

  await s.scheduler.correrVencidos();
  await s.cola.vacia();

  const pedido = JSON.stringify(modelo.llamadas);
  assert.match(pedido, /o sea/, 'el contexto lleva cuánto falta, ya calculado');
  assert.match(pedido, /mañana/, 'y para 25 horas dice mañana');
});
