'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { paraUnaPersona } = require('../src/funnel/derivacion');
const { crearTextos } = require('../src/templates/funnel');
const { montar, conLead, stubModelo, stubTranscriptor } = require('./helpers');
const { S } = require('../src/funnel/states');

const NOMBRES = { nombres: ['Juan'] };
const PARA_JUAN = { motivo: 'para_una_persona', nombre: 'Juan' };
const AJENA = { motivo: 'reunion_ajena', nombre: null };
const AM = '59899000111';

// ═════════════════════════════════════════════════════════════════════════════
// La deteccion — lo que SI tiene que agarrar
// ═════════════════════════════════════════════════════════════════════════════

test('agarra el mensaje real del 18-9', () => {
  assert.deepEqual(paraUnaPersona('Hola Juan podemos mover nuestra reunión para el Lunes?', NOMBRES), PARA_JUAN);
});

test('agarra las formas comunes de saludar a Juan', () => {
  for (const texto of [
    'Juan, te paso el contrato firmado',
    'juan te llamo mañana',
    'JUAN!!',
    'Juan?',
    'buenas juan! cómo va',
    'Buen día Juan',
    'buenos dias juan',
    'buenas tardes, Juan',
    'buenas noches juan',
    'hola, juan',
    'holaaa juan',
    'che juan',
    'hey Juan',
    'que tal juan',
  ]) {
    assert.deepEqual(paraUnaPersona(texto, NOMBRES), PARA_JUAN, texto);
  }
});

test('agarra el saludo con signos de apertura o un emoji adelante', () => {
  for (const texto of ['¡Hola Juan!', '¿Juan, podemos hablar?', '👋 Hola Juan', '  hola juan  ', '...juan']) {
    assert.deepEqual(paraUnaPersona(texto, NOMBRES), PARA_JUAN, texto);
  }
});

test('agarra dos o tres saludos seguidos antes del nombre', () => {
  for (const texto of ['Hola buen día Juan', 'hola que tal juan', 'hola, buenas, buen dia juan']) {
    assert.deepEqual(paraUnaPersona(texto, NOMBRES), PARA_JUAN, texto);
  }
});

test('ignora acentos y mayusculas, en el mensaje y en la config', () => {
  assert.deepEqual(paraUnaPersona('Hola Juán', NOMBRES), PARA_JUAN);
  assert.deepEqual(paraUnaPersona('hola jose', { nombres: ['José'] }), { motivo: 'para_una_persona', nombre: 'José' });
  assert.deepEqual(paraUnaPersona('HOLA JOSÉ', { nombres: ['jose'] }), { motivo: 'para_una_persona', nombre: 'jose' });
});

test('con varios nombres devuelve el que saludo', () => {
  const cfg = { nombres: ['Juan', 'Alexis', 'Juan Pablo'] };
  assert.equal(paraUnaPersona('Alexis, ¿cómo va?', cfg).nombre, 'Alexis');
  assert.equal(paraUnaPersona('hola juan pablo', cfg).nombre, 'Juan', 'el primero de la lista que coincide');
  assert.equal(paraUnaPersona('hola juan pablo', { nombres: ['Juan Pablo'] }).nombre, 'Juan Pablo', 'nombres compuestos');
});

test('agarra los pedidos de mover una reunion que no conocemos', () => {
  for (const texto of [
    'podemos mover la reunión al lunes?',
    '¿Se puede pasar la llamada para el jueves?',
    'Tenemos que reprogramar la videollamada',
    'hay que cancelar la reunion de mañana',
    'podemos posponer la cita?',
    'adelantamos? quisiera adelantar la charla',
    'can we move the meeting? digo, mover la meeting',
    'hay que cambiar la call',
    'reagendamos la reunión?',
  ]) {
    assert.deepEqual(paraUnaPersona(texto, NOMBRES), AJENA, texto);
  }
});

test('agarra al que dice que la reunion la agendamos nosotros', () => {
  for (const texto of [
    'Agendaste con nosotros de hecho',
    'es por nuestra reunión',
    'te escribo por la reunion que tenemos',
    'sobre la reunión del jueves',
  ]) {
    assert.deepEqual(paraUnaPersona(texto, NOMBRES), AJENA, texto);
  }
});

/** El nombre manda siempre: saludar a Juan es para Juan, tenga o no reunion. */
test('el saludo a Juan cuenta aunque el lead ya tenga reunion', () => {
  assert.deepEqual(paraUnaPersona('Hola Juan', { ...NOMBRES, tieneReunion: true }), PARA_JUAN);
});

// ═════════════════════════════════════════════════════════════════════════════
// La deteccion — lo que NO tiene que agarrar
// ═════════════════════════════════════════════════════════════════════════════

test('un lead comun no dispara nada', () => {
  for (const texto of [
    'Hola, quiero una web para mi tienda',
    '¡Hola! ¿cómo puedo agendar mi demo?',
    'buenas, vendo ropa y quiero vender online',
    'Hola buen día, tengo una barbería',
    'Buenas tardes',
    'ok',
  ]) {
    assert.equal(paraUnaPersona(texto, NOMBRES), null, texto);
  }
});

test('un nombre parecido, o Juan en el medio de la frase, no cuenta', () => {
  for (const texto of [
    'Hola Juana, quiero una web',
    'Juancito me pasó el número',
    'Juanjo, ¿qué tal?',
    'me recomendó Juan que les escriba',
    'gracias juan',
    'hola, soy Juan y tengo una panadería',
    'San Juan tiene una tienda nueva',
  ]) {
    assert.equal(paraUnaPersona(texto, NOMBRES), null, texto);
  }
});

test('hablar de reuniones o de cambios, por separado, no alcanza', () => {
  for (const texto of [
    'quiero cambiar mi página web',
    'necesito una llamada para ver precios',
    'me gustaría agendar una reunión',
    'quiero pasar a una tienda online',
    'felicitaciones, quiero cambiar el logo', // "cita" adentro de otra palabra
    'tengo reuniones todo el día',
  ]) {
    assert.equal(paraUnaPersona(texto, NOMBRES), null, texto);
  }
});

/**
 * Al que esta eligiendo horario, "¿podemos pasar la llamada al martes?" le
 * contesta el embudo: es quien tiene los horarios.
 */
test('si el bot ya le ofrecio o le agendo una reunion, moverla es cosa del embudo', () => {
  for (const texto of ['¿podemos pasar la llamada al martes?', 'nuestra reunión es mañana, no?']) {
    assert.equal(paraUnaPersona(texto, { ...NOMBRES, tieneReunion: true }), null, texto);
  }
});

// ═════════════════════════════════════════════════════════════════════════════
// Entradas raras: nunca puede tirar
// ═════════════════════════════════════════════════════════════════════════════

test('texto vacio, nulo o que no es texto devuelve null sin tirar', () => {
  for (const texto of [undefined, null, '', '   ', '\n\t', 0, 42, {}, []]) {
    assert.equal(paraUnaPersona(texto, NOMBRES), null, JSON.stringify(texto));
  }
});

test('sin nombres configurados solo queda la regla de la reunion', () => {
  assert.equal(paraUnaPersona('Hola Juan'), null);
  assert.equal(paraUnaPersona('Hola Juan', { nombres: [] }), null);
  assert.equal(paraUnaPersona('Hola Juan', { nombres: ['', '   '] }), null);
  assert.deepEqual(paraUnaPersona('podemos mover la reunión?'), AJENA);
});

/**
 * El nombre viene de la config y termina adentro de una expresion regular. Un
 * "[" o un "(" en EQUIPO_NOMBRES hacia tirar este chequeo —que corre con cada
 * mensaje— y el bot dejaba de contestarle a todo el mundo.
 */
test('un nombre con caracteres raros en la config no rompe nada', () => {
  const raros = { nombres: ['[x', 'Juan (CEO)', 'a+b', 'c*', '\\', '.*', '?'] };
  for (const texto of ['hola que tal', 'Hola Juan', 'cualquier cosa', '.*']) {
    assert.doesNotThrow(() => paraUnaPersona(texto, raros), texto);
  }
  assert.equal(paraUnaPersona('hola que tal', { nombres: ['.*'] }), null, 'no se toma como comodin');
  assert.equal(paraUnaPersona('hola b', { nombres: ['a+b'] }), null, 'el + es literal');
  assert.deepEqual(paraUnaPersona('hola a+b', { nombres: ['a+b'] }), { motivo: 'para_una_persona', nombre: 'a+b' });
});

test('un mensaje muy largo no cuelga el chequeo', () => {
  const largo = 'hola '.repeat(20000) + 'juan';
  const t0 = Date.now();
  paraUnaPersona(largo, NOMBRES);
  paraUnaPersona('mover '.repeat(20000), NOMBRES);
  assert.ok(Date.now() - t0 < 1000, `tardo ${Date.now() - t0}ms`);
});

// ═════════════════════════════════════════════════════════════════════════════
// De punta a punta
// ═════════════════════════════════════════════════════════════════════════════

const DAVID = '31687379000';
const alContacto = (s, tel = DAVID) => s.proveedor.getEnviados().filter((e) => e.to === tel).map((e) => e.texto);
const alEquipo = (s) => s.proveedor.getEnviados().filter((e) => e.to === AM).map((e) => e.texto).join('\n');

async function escribe(s, texto, tel = DAVID) {
  await s.servicioLeads.registrarRespuesta(tel, texto, 'David');
  await s.cola.vacia();
}

// ── lo que tiene que pasar ───────────────────────────────────────────────────

test('"Hola Juan…" no entra al embudo: una linea y el chat pasa a Juan', async () => {
  const modelo = stubModelo();
  const s = await montar({ modelo });

  await escribe(s, 'Hola Juan podemos mover nuestra reunión para el Lunes?');

  assert.deepEqual(alContacto(s), [crearTextos().paraUnaPersona('Juan')],
    'ni la presentacion de agente comercial, ni preguntas, ni "claro, sin problema"');
  const l = s.repo.leadPorTelefono(DAVID);
  assert.equal(l.fsm_state, S.HUMAN_QUEUED);
  assert.equal(l.human_requested, 1);
  assert.equal(l.motivo_derivacion, 'para_una_persona');
  assert.match(alEquipo(s), /le escribio a una persona del equipo/);
  assert.match(alEquipo(s), /mover nuestra reunión/, 'con lo que dijo, para que Juan sepa de que se trata');
  assert.equal(modelo.llamadas.length, 0, 'el modelo ni se entera: no hay nada que conversar');
});

test('lo que manda despues le llega a Juan y el bot no le contesta mas', async () => {
  const s = await montar({ modelo: stubModelo() });

  await escribe(s, 'Hola Juan podemos mover nuestra reunión para el Lunes?');
  s.proveedor.limpiar();
  await escribe(s, 'Agendaste con nosotros de hecho. Soy David, la mano derecha de Alexis');
  await escribe(s, 'Lol');

  assert.equal(alContacto(s).length, 0, 'el bot se calla');
  assert.match(alEquipo(s), /mano derecha de Alexis/, 'y Juan lo ve');
});

test('sin el nombre, una reunion que no conocemos tambien va a una persona', async () => {
  const s = await montar({ modelo: stubModelo() });

  await escribe(s, 'Buenas, ¿podemos pasar la llamada del viernes al lunes?');

  assert.deepEqual(alContacto(s), [crearTextos().paraUnaPersona(null)]);
  assert.equal(s.repo.leadPorTelefono(DAVID).motivo_derivacion, 'reunion_ajena');
  assert.match(alEquipo(s), /reunion que el bot no tiene registrada/);
});

test('los nombres salen de la config', async () => {
  const s = await montar({ modelo: stubModelo(), EQUIPO_NOMBRES: 'Juan, Alexis' });
  await escribe(s, 'Alexis, te mando lo que hablamos');
  assert.deepEqual(alContacto(s), [crearTextos().paraUnaPersona('Alexis')]);
});

test('un lead que ya venia conversando y saluda a Juan tambien pasa a Juan', async () => {
  const s = await conLead({ modelo: stubModelo() });
  const TEL = '59899123456';
  await escribe(s, 'hola, tengo una inmobiliaria', TEL);
  s.proveedor.limpiar();

  await escribe(s, 'Juan, te llamo mañana para ver lo del presupuesto', TEL);

  assert.deepEqual(alContacto(s, TEL), [crearTextos().paraUnaPersona('Juan')]);
  assert.equal(s.repo.leadPorTelefono(TEL).motivo_derivacion, 'para_una_persona');
});

test('sin IA igual funciona: la linea es fija y no necesita al modelo', async () => {
  const s = await montar({ sinIA: true });
  await escribe(s, 'Hola Juan, ¿movemos la reunión?');
  assert.deepEqual(alContacto(s), [crearTextos().paraUnaPersona('Juan')]);
});

test('de madrugada la linea sale igual: esta contestando a alguien que escribio', async () => {
  const domingo3am = new Date('2026-08-09T06:00:00Z');
  const s = await montar({ modelo: stubModelo(), BUSINESS_HOURS: '09:00-19:00', BUSINESS_DAYS: 'mon-sat' }, domingo3am);

  await escribe(s, 'Hola Juan podemos mover nuestra reunión para el Lunes?');

  assert.deepEqual(alContacto(s), [crearTextos().paraUnaPersona('Juan')]);
});

test('si el saludo llega por audio, tambien pasa a Juan', async () => {
  const s = await montar({
    modelo: stubModelo(),
    openai: stubTranscriptor({ respuestas: { transcripcion: 'Hola Juan, te quería decir que movamos la reunión' } }),
  });

  await s.proveedor.simularSinTexto({
    from: DAVID, tipo: 'audio', segundos: 4, id: 'wamid.audio-juan',
    descargar: async () => Buffer.from('ogg'),
  });
  await s.agrupador.vaciar();
  await s.cola.vacia();

  assert.deepEqual(alContacto(s), [crearTextos().paraUnaPersona('Juan')]);
});

test('si viene en el epigrafe de una foto, tambien pasa a Juan', async () => {
  const s = await montar({ modelo: stubModelo() });

  await s.proveedor.simularEntrante({
    from: DAVID, texto: 'Juan, mirá cómo quedó', id: 'wamid.foto-juan', nombre: 'David',
    tipo: 'imagen', nombreArchivo: '', segundos: 0, descargar: async () => Buffer.from('jpg'),
  });
  await s.agrupador.vaciar();
  await s.cola.vacia();

  assert.deepEqual(alContacto(s), [crearTextos().paraUnaPersona('Juan')]);
});

test('sin telefonos del equipo configurados no se rompe: la linea sale igual', async () => {
  const s = await montar({ modelo: stubModelo(), AM_PHONES: '' });
  await escribe(s, 'Hola Juan');
  assert.deepEqual(alContacto(s), [crearTextos().paraUnaPersona('Juan')]);
  assert.equal(s.repo.leadPorTelefono(DAVID).human_requested, 1);
});

// ── lo que NO tiene que pasar ────────────────────────────────────────────────

test('un lead comun sigue recibiendo la presentacion y el embudo', async () => {
  const s = await montar({ modelo: stubModelo() });
  await escribe(s, 'Hola, quiero una web para mi barbería');

  assert.equal(alContacto(s)[0], crearTextos().BIENVENIDA);
  assert.equal(s.repo.leadPorTelefono(DAVID).human_requested, 0);
});

test('al dado de baja no se le contesta nada, ni para pasarlo a Juan', async () => {
  const s = await conLead({ modelo: stubModelo() });
  const TEL = '59899123456';
  s.repo.actualizarFunnel(s.repo.leadPorTelefono(TEL).id, { opt_out: 1 });
  s.proveedor.limpiar();

  await escribe(s, 'Hola Juan', TEL);

  assert.equal(alContacto(s, TEL).length, 0);
});

test('con el bot pausado no se le contesta: la persona ya esta en el chat', async () => {
  const s = await conLead({ modelo: stubModelo() });
  const TEL = '59899123456';
  s.repo.actualizarFunnel(s.repo.leadPorTelefono(TEL).id, {
    bot_pausado_hasta: new Date(Date.now() + 3600_000).toISOString(),
  });
  s.proveedor.limpiar();

  await escribe(s, 'Hola Juan, ¿movemos la reunión?', TEL);

  assert.equal(alContacto(s, TEL).length, 0);
});

test('si ya estaba con una persona, no le repite la linea', async () => {
  const s = await montar({ modelo: stubModelo() });
  await escribe(s, 'Hola Juan');
  s.proveedor.limpiar();

  await escribe(s, 'Hola Juan, ¿estás?');

  assert.equal(alContacto(s).length, 0);
});

test('al que ya tiene reunion con el bot, mover la reunion no lo deriva por esta regla', async () => {
  const s = await conLead({ modelo: stubModelo() });
  const TEL = '59899123456';
  const l = s.repo.leadPorTelefono(TEL);
  s.repo.registrarReunion(l.id, {
    meetingTime: new Date(Date.now() + 48 * 3600_000).toISOString(),
    meetingUrl: 'https://meet.google.com/x',
    ahoraIso: new Date().toISOString(),
  });
  s.repo.actualizarFunnel(l.id, { fsm_state: S.SCHEDULED });
  s.proveedor.limpiar();

  await escribe(s, '¿podemos mover la reunión al lunes?', TEL);

  const despues = s.repo.leadPorTelefono(TEL);
  assert.notEqual(despues.motivo_derivacion, 'reunion_ajena', 'esa reunion la conoce el bot');
  assert.ok(!alContacto(s, TEL).includes(crearTextos().paraUnaPersona(null)));
});

test('el saludo en un mensaje y el pedido en otro: una sola linea, no dos', async () => {
  const s = await montar({ modelo: stubModelo() });

  await escribe(s, 'Hola Juan');
  await escribe(s, 'podemos mover nuestra reunión para el lunes?');

  assert.deepEqual(alContacto(s), [crearTextos().paraUnaPersona('Juan')]);
});
