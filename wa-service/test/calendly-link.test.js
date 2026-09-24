'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { montar } = require('./helpers');
const { verificarLink, avisarSiEstaRoto } = require('../src/agenda/calendly');

const AM = '59899000111';
const respuesta = (status) => async () => ({ ok: status >= 200 && status < 300, status });

test('un link que existe no molesta a nadie', async () => {
  const s = await montar();
  const r = await avisarSiEstaRoto({ cfg: s.cfg, cola: s.cola, logger: null, fetch: respuesta(200) });
  await s.cola.vacia();

  assert.equal(r.ok, true);
  assert.equal(s.proveedor.getEnviados().length, 0);
});

/**
 * El bot estuvo mandando un link que daba 404. No fallaba nada: el mensaje
 * salia bien, el embudo avanzaba, los logs limpios, y del otro lado el lead
 * abria una pagina de error.
 */
test('un link roto se avisa por WhatsApp, no solo al log', async () => {
  const s = await montar();
  const r = await avisarSiEstaRoto({ cfg: s.cfg, cola: s.cola, logger: null, fetch: respuesta(404) });
  await s.cola.vacia();

  assert.equal(r.ok, false);
  const aviso = s.proveedor.getEnviados().find((e) => e.to === AM);
  assert.ok(aviso, 'le llega al equipo');
  assert.match(aviso.texto, /no funciona/);
  assert.match(aviso.texto, /CALENDLY_LINK/, 'y dice que hay que corregir');
});

/**
 * Quedarse sin red no es lo mismo que el link estar mal. Confundirlos haria
 * saltar la alarma cada vez que se cae la conexion, y una alarma que grita por
 * nada deja de mirarse.
 */
test('si no se pudo verificar, no se grita', async () => {
  const s = await montar();
  const sinRed = async () => { throw new Error('ENOTFOUND'); };
  const r = await avisarSiEstaRoto({ cfg: s.cfg, cola: s.cola, logger: null, fetch: sinRed });
  await s.cola.vacia();

  assert.equal(r.ok, null, 'no se sabe, y eso no es "esta roto"');
  assert.equal(s.proveedor.getEnviados().length, 0);
});

test('sin link configurado tambien cuenta como roto', async () => {
  const r = await verificarLink('', { fetch: respuesta(200) });
  assert.equal(r.ok, false);
});
