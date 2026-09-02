'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { botActivo, HORAS_PAUSA } = require('../src/funnel/pausa');

const AHORA = new Date('2026-09-02T15:00:00Z');

test('por defecto el bot contesta', () => {
  assert.equal(botActivo({}, AHORA), true);
  assert.equal(botActivo({ bot_enabled: 1 }, AHORA), true);
});

/**
 * El interruptor del panel. Manda sobre todo lo demas: si esta apagado el bot
 * no contesta aunque no haya ninguna pausa corriendo.
 */
test('apagado desde el panel, no contesta', () => {
  assert.equal(botActivo({ bot_enabled: 0 }, AHORA), false);
  assert.equal(botActivo({ bot_enabled: 0, bot_pausado_hasta: null }, AHORA), false);
});

/**
 * La pausa que se pone sola cuando el dueño entra a la conversacion desde su
 * telefono. Lo importante es que VENCE: el pedido textual de Jose fue "que el
 * sistema pare solo cuando yo entro, y que si el cliente vuelve a escribir a
 * los dias le conteste".
 *
 * Un interruptor a secas obliga a acordarse de prenderlo de nuevo, y cuando
 * uno se olvida ese lead se queda sin bot sin que nadie lo note.
 */
test('la pausa calla al bot mientras corre', () => {
  const enUnaHora = new Date(AHORA.getTime() + 3600_000).toISOString();
  assert.equal(botActivo({ bot_pausado_hasta: enUnaHora }, AHORA), false);
});

test('y cuando vence, el bot vuelve solo', () => {
  const haceUnMinuto = new Date(AHORA.getTime() - 60_000).toISOString();
  assert.equal(botActivo({ bot_pausado_hasta: haceUnMinuto }, AHORA), true);
});

test('el interruptor gana sobre una pausa vencida', () => {
  const haceUnMinuto = new Date(AHORA.getTime() - 60_000).toISOString();
  assert.equal(botActivo({ bot_enabled: 0, bot_pausado_hasta: haceUnMinuto }, AHORA), false);
});

test('la pausa dura las horas configuradas', () => {
  assert.equal(HORAS_PAUSA, 12);
});

// ── el freno de verdad, contra el servicio entero ────────────────────────────

const { conLead, stubModelo } = require('./helpers');

const TEL = '59899123456';

async function escribe(s, texto) {
  await s.servicioLeads.registrarRespuesta(TEL, texto);
  await s.cola.vacia();
  return s.proveedor.getEnviados().filter((e) => e.to === TEL).map((e) => e.texto);
}

test('con el bot apagado desde el panel, el lead no recibe respuesta', async () => {
  const s = await conLead({ modelo: stubModelo() });
  s.repo.actualizarFunnel(s.repo.leadPorTelefono(TEL).id, { bot_enabled: 0 });

  assert.deepEqual(await escribe(s, 'hola, tengo una panadería'), []);
});

test('mientras corre la pausa el bot se calla, y despues vuelve', async () => {
  const s = await conLead({ modelo: stubModelo() });
  const id = s.repo.leadPorTelefono(TEL).id;

  const enUnaHora = new Date(Date.now() + 3600_000).toISOString();
  s.repo.actualizarFunnel(id, { bot_pausado_hasta: enUnaHora });
  assert.deepEqual(await escribe(s, 'hola?'), [], 'callado');

  const yaVencio = new Date(Date.now() - 60_000).toISOString();
  s.repo.actualizarFunnel(id, { bot_pausado_hasta: yaVencio });
  assert.deepEqual(await escribe(s, 'hola?'), ['[conversacion]'], 'volvio solo');
});

/**
 * El caso que hace que esto valga la pena: entras vos al chat desde el celular
 * y el bot se calla solo, sin que tengas que apagar nada.
 */
test('si escribis vos desde el telefono, el bot se pausa solo', async () => {
  const s = await conLead({ modelo: stubModelo() });

  await s.proveedor.simularSaliente({ to: TEL, texto: 'Hola Juan, te escribo yo', id: 'mano-1' });
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono(TEL);
  assert.ok(l.bot_pausado_hasta, 'quedo pausado');
  assert.deepEqual(await escribe(s, 'dale, gracias'), [], 'y el bot no contesta');
});

/**
 * Lo que manda el propio bot vuelve como eco y tambien viene marcado como
 * propio. Sin distinguirlo, el bot se callaria solo cada vez que contesta.
 */
test('el eco de lo que mando el bot no lo pausa', async () => {
  const s = await conLead({ modelo: stubModelo() });
  await escribe(s, 'hola, tengo una panadería');

  const enviado = s.proveedor.getEnviados().find((e) => e.to === TEL);
  await s.proveedor.simularSaliente({ to: TEL, texto: enviado.texto, id: enviado.id });
  await s.cola.vacia();

  assert.equal(s.repo.leadPorTelefono(TEL).bot_pausado_hasta, null, 'no se pausa a si mismo');
});

/**
 * Reiniciar es empezar de cero, y eso incluye los dos frenos. Sin esto, un lead
 * que reiniciaste justo despues de escribirle desde el telefono queda en NEW
 * pero mudo unas horas, sin nada en el panel que lo explique.
 */
test('reiniciar un lead tambien le devuelve el bot', async () => {
  const s = await conLead({ modelo: stubModelo() });
  const id = s.repo.leadPorTelefono(TEL).id;
  s.repo.actualizarFunnel(id, {
    bot_enabled: 0,
    bot_pausado_hasta: new Date(Date.now() + 3600_000).toISOString(),
  });

  s.repo.reiniciarLead(id, new Date().toISOString());

  const l = s.repo.leadPorId(id);
  assert.equal(l.bot_enabled, 1);
  assert.equal(l.bot_pausado_hasta, null);
  // Reiniciar tambien borra welcomed_at, asi que primero sale la bienvenida.
  assert.equal((await escribe(s, 'hola de nuevo')).at(-1), '[conversacion]', 'y contesta');
});

/**
 * Reiniciar es empezar de cero, y los horarios que se le habian mostrado son
 * parte de lo que hay que olvidar: si quedan, el lead reiniciado arrastra una
 * lista de otra conversacion —y peor, de antes de que se cambiara la franja de
 * atencion—. Es el mismo olvido que ya habia pasado con la reunion colgada.
 */
test('reiniciar tambien borra los horarios que se le habian ofrecido', async () => {
  const s = await conLead({ modelo: stubModelo() });
  const id = s.repo.leadPorTelefono(TEL).id;
  s.repo.actualizarFunnel(id, {
    horarios_ofrecidos: JSON.stringify(['2026-09-03T15:00:00.000Z']),
  });

  s.repo.reiniciarLead(id, new Date().toISOString());

  assert.equal(s.repo.leadPorId(id).horarios_ofrecidos, null);
});
