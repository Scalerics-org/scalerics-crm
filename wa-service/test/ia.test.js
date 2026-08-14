'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { montar, conLead, CLAVE } = require('./helpers');
const { crearAgente, mencionaPlata, sanearDatos, aMensajes } = require('../src/ia/agente');
const { construirSystem, faltantes } = require('../src/ia/prompt');
const { crearTextos } = require('../src/templates/funnel');
const { S } = require('../src/funnel/states');

const textos = crearTextos({ calendlyLink: 'https://calendly.com/scalerics/diagnostico' });

/** Cliente falso: devuelve lo que se le diga, y anota como lo llamaron. */
function anthropicFalso(respuestas) {
  const pila = Array.isArray(respuestas) ? respuestas.slice() : [respuestas];
  const llamadas = [];
  return {
    llamadas,
    messages: {
      create: async (args) => {
        llamadas.push(args);
        const r = pila.length > 1 ? pila.shift() : pila[0];
        if (r instanceof Error) throw r;
        return r;
      },
    },
  };
}

const conTexto = (texto, datos = null) => ({
  content: [
    { type: 'text', text: texto },
    ...(datos ? [{ type: 'tool_use', name: 'guardar_datos', input: datos }] : []),
  ],
});

// ── el guard de precios ──────────────────────────────────────────────────────

test('detecta cuando una respuesta menciona plata', () => {
  for (const t of [
    'Una web como la que necesitás arranca en USD 800',
    'te sale unos 1.500 dólares',
    'andaría por los $2000',
    'calculá 30 mil pesos más o menos',
    'el proyecto ronda los 1200',
  ]) {
    assert.equal(mencionaPlata(t), true, t);
  }
});

test('no bloquea las respuestas que SI tiene que poder dar', () => {
  for (const t of [
    'El precio depende del alcance, por eso primero charlamos',
    'La videollamada son 30 minutos y no tiene costo',
    'Dale, te paso el link: https://calendly.com/scalerics/diagnostico',
    '¿Ustedes son 2 o 3 personas en el local?',
    'Seguime en @local2000 y vemos',
  ]) {
    assert.equal(mencionaPlata(t), false, t);
  }
});

test('si la IA se manda un precio, sale el texto fijo en su lugar', async () => {
  const agente = crearAgente({
    anthropic: anthropicFalso(conTexto('Una web te sale unos USD 900 más IVA')),
    modelo: 'x', textos,
  });
  const r = await agente.responder({ id: 1, nombre: 'Ana' }, 'cuanto sale?', []);

  assert.equal(r.precioBloqueado, true);
  assert.equal(r.texto, textos.PRECIO);
  assert.ok(!/900/.test(r.texto));
});

// ── extraccion de datos ──────────────────────────────────────────────────────

test('solo guarda los campos permitidos y con el tipo correcto', () => {
  const limpio = sanearDatos({
    business_name: '  Parrilla El Fogón  ',
    business_type: '2',
    budget: 9,              // fuera de rango
    team_size: 'muchos',    // no es numero
    score: 10,              // no es suyo
    fsm_state: 'SCORED',    // menos todavia
    needs: '',
  });

  assert.deepEqual(limpio, { business_name: 'Parrilla El Fogón', business_type: 2 });
});

test('el rubro que extrae la IA queda clasificado, como el del formulario', async () => {
  const s = await conLead({
    anthropic: anthropicFalso(conTexto('¡Buenísimo! ¿Y qué te gustaría lograr?', {
      rubro: 'parrilla y delivery',
    })),
  });

  await s.servicioLeads.registrarRespuesta('59899123456', 'tenemos una parrilla con delivery');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono('59899123456');
  assert.equal(l.rubro, 'parrilla y delivery');
  assert.equal(l.rubro_norm, 'gastronomia', 'el follow-up usa el gancho del rubro');
});

// ── quien manda ──────────────────────────────────────────────────────────────

test('con todos los datos, el cierre lo hace el codigo y no la IA', async () => {
  // La IA podria decidir ofrecer la reunion cuando le parezca. La oferta sale
  // del score, asi que su texto de cierre se descarta.
  const s = await conLead({
    anthropic: anthropicFalso(conTexto('Listo, te paso mi Calendly ahora mismo', {
      business_name: 'Inmobiliaria Pereyra', rubro: 'inmobiliaria',
      business_type: 2, budget: 3, team_size: 3,
      instagram_web: '@inmopereyra', needs: 'quiero dejar de perder consultas',
    })),
  });

  await s.servicioLeads.registrarRespuesta('59899123456', 'te cuento todo de una');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono('59899123456');
  assert.equal(l.fsm_state, S.MEETING_SENT);
  assert.equal(l.score, 8);

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.match(alLead.at(-1), /videollamada de 30 minutos/);
  assert.ok(!alLead.some((m) => /te paso mi Calendly ahora mismo/.test(m)), 'no sale su texto');
});

test('una queja se deriva por codigo, sin pasar por la IA', async () => {
  const anthropic = anthropicFalso(conTexto('Uy, contame qué pasó'));
  const s = await conLead({ anthropic });

  await s.servicioLeads.registrarRespuesta('59899123456', 'esto es una estafa');
  await s.cola.vacia();

  assert.equal(s.repo.leadPorTelefono('59899123456').fsm_state, S.HUMAN_QUEUED);
  assert.equal(anthropic.llamadas.length, 0, 'ni se le pregunta al modelo');
});

test('la baja se respeta por codigo, sin pasar por la IA', async () => {
  const anthropic = anthropicFalso(conTexto('¡No te vayas!'));
  const s = await conLead({ anthropic });

  await s.servicioLeads.registrarRespuesta('59899123456', 'baja');
  await s.cola.vacia();

  assert.equal(s.repo.leadPorTelefono('59899123456').opt_out, 1);
  assert.equal(anthropic.llamadas.length, 0);
});

// ── que pasa cuando la IA no esta ────────────────────────────────────────────

test('sin clave el agente queda inactivo y contesta el embudo de siempre', async () => {
  const s = await conLead();   // sin anthropic
  await s.servicioLeads.registrarRespuesta('59899123456', 'hola');
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.match(alLead.at(-1), /asistente de \*Scalerics\*/);
  assert.equal(s.repo.leadPorTelefono('59899123456').fsm_state, S.MENU);
});

test('si la API falla, el lead no se queda sin respuesta', async () => {
  const s = await conLead({ anthropic: anthropicFalso(new Error('529 overloaded')) });

  await s.servicioLeads.registrarRespuesta('59899123456', 'hola');
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.equal(alLead.length, 1, 'contesta igual');
  assert.match(alLead.at(-1), /asistente de \*Scalerics\*/, 'cae al embudo fijo');
});

test('si la IA guarda datos pero no contesta, tampoco se queda mudo', async () => {
  const s = await conLead({
    anthropic: anthropicFalso({ content: [{ type: 'tool_use', name: 'guardar_datos', input: { rubro: 'x' } }] }),
  });

  await s.servicioLeads.registrarRespuesta('59899123456', 'hola');
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().filter((e) => e.to === '59899123456').length, 1);
});

// ── el prompt ────────────────────────────────────────────────────────────────

test('el prompt pide solo lo que falta y no lo que ya se sabe', () => {
  const sys = construirSystem({
    nombre: 'Ana', business_name: 'Parrilla El Fogón', rubro: 'parrilla',
    rubro_norm: 'gastronomia', budget: 2,
  });

  assert.match(sys, /Parrilla El Fogón/);
  assert.match(sys, /pedidos online/, 'incluye el gancho del rubro');
  assert.ok(!/a qué se dedica el negocio/.test(sys), 'no repregunta el rubro');
  assert.ok(!/qué presupuesto maneja/.test(sys), 'ni el presupuesto');
  assert.match(sys, /si tiene Instagram, web o redes/, 'si pide lo que falta');
  assert.match(sys, /No decís precios/);
});

test('faltantes se vacia recien cuando estan los siete datos', () => {
  assert.equal(faltantes({}).length, 7);
  assert.equal(faltantes({
    business_name: 'x', rubro: 'y', business_type: 1, budget: 2,
    team_size: 3, instagram_web: '@z', needs: 'algo',
  }).length, 0);
});

test('el historial se arma alternando roles, como pide la API', () => {
  const msgs = aMensajes([
    { direction: 'out', body: 'Hola, soy Scalerics' },
    { direction: 'in', body: 'hola' },
    { direction: 'in', body: 'que hacen?' },
    { direction: 'out', body: 'Hacemos webs' },
  ], 'cuanto sale?');

  assert.equal(msgs[0].role, 'user', 'no arranca con el bot');
  for (let i = 1; i < msgs.length; i++) {
    assert.notEqual(msgs[i].role, msgs[i - 1].role, 'nunca dos seguidos del mismo rol');
  }
  assert.match(msgs.at(-1).content, /cuanto sale\?$/);
});

// ── entrantes sin texto ──────────────────────────────────────────────────────

test('a un audio se le contesta en vez de dejarlo hablando solo', async () => {
  // Antes textoDeMensaje devolvia '' y el entrante se descartaba: la persona
  // mandaba una nota de voz y no pasaba nada.
  const s = await montar();
  s.proveedor.simularSinTexto({ from: '59899123456', tipo: 'audio' });
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.equal(alLead.length, 1);
  assert.match(alLead[0], /no puedo escuchar audios/);
});

test('cuatro audios seguidos no son cuatro disculpas', async () => {
  const s = await montar();
  for (let i = 0; i < 4; i++) s.proveedor.simularSinTexto({ from: '59899123456', tipo: 'audio' });
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().filter((e) => e.to === '59899123456').length, 1);
});

test('al que se dio de baja no se le contesta el audio', async () => {
  const s = await conLead();
  await s.servicioLeads.registrarRespuesta('59899123456', 'baja');
  await s.cola.vacia();
  s.proveedor.limpiar();

  s.proveedor.simularSinTexto({ from: '59899123456', tipo: 'audio' });
  await s.cola.vacia();
  assert.equal(s.proveedor.getEnviados().length, 0);
});
