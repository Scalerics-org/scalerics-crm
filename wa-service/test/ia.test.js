'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { montar, conLead, CLAVE } = require('./helpers');
const { crearAgente, sanearDatos, aMensajes, tramoDeEquipo } = require('../src/ia/agente');
const { mencionaPlata } = require('../src/ia/precio');
const { construirSystem, faltantes } = require('../src/ia/prompt');
const { crearTextos } = require('../src/templates/funnel');
const { S } = require('../src/funnel/states');

const textos = crearTextos();

/** Cliente falso: devuelve lo que se le diga, y anota como lo llamaron. */
function openaiFalso(respuestas, { falla = null } = {}) {
  const pila = Array.isArray(respuestas) ? respuestas.slice() : [respuestas];
  const llamadas = [];
  const transcripciones = [];
  return {
    llamadas,
    transcripciones,
    chat: {
      completions: {
        create: async (args) => {
          // Sin tools es el redactor pidiendo un mensaje suelto. Se contesta
          // con el marcador de la situacion, igual que el stub compartido: los
          // guiones de estos tests son para la conversacion.
          if (!args.tools) {
            if (falla) throw new Error(falla);
            const m = args.messages[0].content.match(/situación: (\w+)/);
            return { choices: [{ message: { content: `[${m ? m[1] : 'desconocida'}]` } }] };
          }
          llamadas.push(args);
          const r = pila.length > 1 ? pila.shift() : pila[0];
          if (r instanceof Error) throw r;
          return r;
        },
      },
    },
    audio: {
      transcriptions: {
        create: async (args) => {
          transcripciones.push(args);
          const r = pila.length > 1 ? pila.shift() : pila[0];
          if (r instanceof Error) throw r;
          return r;
        },
      },
    },
  };
}

/** La respuesta viene toda por la herramienta, mensaje incluido. */
const conTexto = (mensaje, datos = {}) => ({
  choices: [{
    message: {
      content: null,
      tool_calls: [{
        type: 'function',
        function: { name: 'responder', arguments: JSON.stringify({ mensaje, ...datos }) },
      }],
    },
  }],
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
    openai: openaiFalso(conTexto('Una web te sale unos USD 900 más IVA')),
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
    business_type: 'ecommerce',
    budget: 'lo que sea',        // fuera del enum
    team_size_personas: 'muchos', // no es numero
    score: 10,                   // no es suyo
    fsm_state: 'SCORED',         // menos todavia
    needs: '',
  });

  assert.deepEqual(limpio, { business_name: 'Parrilla El Fogón', business_type: 2 });
});

test('el tramo de equipo lo calcula el codigo, no el modelo', () => {
  // El modelo confundia la cantidad con la escala: a "somos 3" le ponia 3, que
  // significa "de 6 a 20 personas". Se le explico con ejemplos y lo seguia
  // errando, porque es una conversion y no una observacion. Ahora informa lo
  // que escucho y el tramo lo arma esto.
  assert.equal(tramoDeEquipo(1), 1, 'solo el');
  assert.equal(tramoDeEquipo(3), 2, 'somos 3 -> de 2 a 5');
  assert.equal(tramoDeEquipo(5), 2);
  assert.equal(tramoDeEquipo(6), 3);
  assert.equal(tramoDeEquipo(20), 3);
  assert.equal(tramoDeEquipo(21), 4);
  assert.equal(tramoDeEquipo(0), null, 'un numero imposible no se guarda');
  assert.equal(tramoDeEquipo('tres'), null);
});

test('el tipo de proyecto y el presupuesto se piden por nombre, no por numero', () => {
  assert.deepEqual(sanearDatos({ business_type: 'web' }), { business_type: 1 });
  assert.deepEqual(sanearDatos({ business_type: 'sistema' }), { business_type: 4 });
  assert.deepEqual(sanearDatos({ budget: 'mas_3000' }), { budget: 3 });
  assert.deepEqual(sanearDatos({ budget: 'no_sabe' }), { budget: 4 });
  assert.deepEqual(sanearDatos({ business_type: 'otra cosa' }), {}, 'fuera del enum no entra');
});

test('el rubro que extrae la IA queda clasificado, como el del formulario', async () => {
  const s = await conLead({
    openai: openaiFalso(conTexto('¡Buenísimo! ¿Y qué te gustaría lograr?', {
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
    openai: openaiFalso(conTexto('Listo, te paso mi Calendly ahora mismo', {
      business_name: 'Inmobiliaria Pereyra', rubro: 'inmobiliaria',
      business_type: 'ecommerce',
    })),
  });

  await s.servicioLeads.registrarRespuesta('59899123456', 'te cuento todo de una');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono('59899123456');
  assert.equal(l.fsm_state, S.MEETING_SENT);

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.equal(alLead.at(-1), '[oferta_reunion]', 'la dispara el score, no el modelo');
  assert.ok(!alLead.some((m) => /te paso mi Calendly ahora mismo/.test(m)), 'no sale su texto de cierre');
});

test('una queja se deriva por codigo, sin pasar por la IA', async () => {
  const openai = openaiFalso(conTexto('Uy, contame qué pasó'));
  const s = await conLead({ openai });

  await s.servicioLeads.registrarRespuesta('59899123456', 'esto es una estafa');
  await s.cola.vacia();

  assert.equal(s.repo.leadPorTelefono('59899123456').fsm_state, S.HUMAN_QUEUED);
  assert.equal(openai.llamadas.length, 0, 'ni se le pregunta al modelo');
});

test('la baja se respeta por codigo, sin pasar por la IA', async () => {
  const openai = openaiFalso(conTexto('¡No te vayas!'));
  const s = await conLead({ openai });

  await s.servicioLeads.registrarRespuesta('59899123456', 'baja');
  await s.cola.vacia();

  assert.equal(s.repo.leadPorTelefono('59899123456').opt_out, 1);
  assert.equal(openai.llamadas.length, 0);
});

// ── que pasa cuando la IA no esta ────────────────────────────────────────────

test('sin clave el agente queda inactivo y el lead va a una persona', async () => {
  const s = await conLead({ sinIA: true });
  await s.servicioLeads.registrarRespuesta('59899123456', 'hola');
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.match(alLead.at(-1), /te paso con alguien del equipo/, 'sin IA va a una persona');
  assert.equal(s.repo.leadPorTelefono('59899123456').fsm_state, S.HUMAN_QUEUED);
});

test('si la API falla, el lead no se queda sin respuesta', async () => {
  const s = await conLead({ openai: openaiFalso(new Error('529 overloaded')) });

  await s.servicioLeads.registrarRespuesta('59899123456', 'hola');
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.equal(alLead.length, 1, 'contesta igual');
  assert.match(alLead.at(-1), /te paso con alguien del equipo/, 'pero lo pasa con una persona');
});

test('si la IA guarda datos pero no contesta, tampoco se queda mudo', async () => {
  const s = await conLead({
    openai: openaiFalso({ content: [{ type: 'tool_use', name: 'guardar_datos', input: { rubro: 'x' } }] }),
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
  assert.ok(!/presupuesto/i.test(sys.split('# Qué te falta averiguar')[1] || ''), 'no pide presupuesto');
  assert.match(sys, /qué necesita/, 'si pide lo que falta');
  assert.match(sys, /No decís precios/);
});

test('faltantes se vacia con los tres datos', () => {
  // Bajo de siete a tres: nombre, rubro y que necesita. Presupuesto, equipo y
  // redes salieron del embudo — se ven en la reunion.
  assert.equal(faltantes({}).length, 3);
  assert.equal(faltantes({ business_name: 'x', rubro: 'y', business_type: 1 }).length, 0);
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

test('un audio que no se puede bajar igual recibe respuesta', async () => {
  // Antes textoDeMensaje devolvia '' y el entrante se descartaba: la persona
  // mandaba una nota de voz y no pasaba nada.
  const s = await montar();
  await s.proveedor.simularSinTexto({ from: '59899123456', tipo: 'audio' });
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.equal(alLead.length, 1);
  assert.equal(alLead[0], '[sin_texto_audio]');
});

test('cuatro audios seguidos no son cuatro disculpas', async () => {
  const s = await montar();
  for (let i = 0; i < 4; i++) await s.proveedor.simularSinTexto({ from: '59899123456', tipo: 'audio' });
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

// ── transcripcion de audios ──────────────────────────────────────────────────

const { crearTranscriptor } = require('../src/ia/transcripcion');

test('manda el audio tal cual: WhatsApp usa OGG y la API lo acepta', async () => {
  const openai = openaiFalso({ text: '  Hola, tengo una parrilla en Pocitos  ' });
  const t = crearTranscriptor({ openai, modelo: 'whisper-1' });

  const texto = await t.transcribir(Buffer.from('audio-falso'), 12);

  assert.equal(texto, 'Hola, tengo una parrilla en Pocitos', 'devuelve el texto sin espacios sobrantes');
  assert.equal(openai.transcripciones[0].model, 'whisper-1');
  assert.equal(openai.transcripciones[0].language, 'es', 'le dice que es español');
});

test('un audio larguisimo no se transcribe', async () => {
  const openai = openaiFalso({ text: 'lo que sea' });
  const t = crearTranscriptor({ openai, modelo: 'whisper-1', maxSegundos: 300 });

  assert.equal(await t.transcribir(Buffer.from('x'), 900), null);
  assert.equal(openai.transcripciones.length, 0, 'ni se llama a la API');
});

test('sin clave no transcribe y no rompe', async () => {
  const t = crearTranscriptor({ openai: null, modelo: 'whisper-1' });
  assert.equal(t.activo, false);
  assert.equal(await t.transcribir(Buffer.from('x'), 5), null);
});

test('si la transcripcion falla devuelve null en vez de tirar', async () => {
  const t = crearTranscriptor({ openai: openaiFalso(new Error('429')), modelo: 'whisper-1' });
  assert.equal(await t.transcribir(Buffer.from('x'), 5), null);
});

test('una nota de voz entra al embudo como si la hubieran escrito', async () => {
  // Es el punto de todo esto: el audio no se contesta con "escribime", se
  // escucha y sigue el mismo camino que el texto.
  const s = await conLead({
    openai: openaiFalso([
      { text: 'Hola, tenemos una parrilla con delivery en Pocitos' },
      conTexto('¡Buenísimo! ¿Y qué te gustaría lograr?', { rubro: 'parrilla con delivery' }),
    ]),
  });

  await s.proveedor.simularSinTexto({
    from: '59899123456', tipo: 'audio', segundos: 9,
    descargar: async () => Buffer.from('ogg-falso'),
  });
  // El audio transcripto entra por el agrupador, igual que un mensaje escrito.
  await s.agrupador.vaciar();
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono('59899123456');
  assert.equal(l.rubro, 'parrilla con delivery', 'lo que dijo por audio quedo guardado');

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.match(alLead.at(-1), /qué te gustaría lograr/);
  assert.ok(!alLead.some((m) => /no puedo escuchar audios/.test(m)), 'no le pide que escriba');
});

test('si no se puede transcribir, le pide que escriba', async () => {
  const s = await conLead({ openai: openaiFalso(new Error('sin credito')) });

  await s.proveedor.simularSinTexto({
    from: '59899123456', tipo: 'audio', segundos: 9,
    descargar: async () => Buffer.from('ogg-falso'),
  });
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.equal(alLead.at(-1), '[sin_texto_audio]');
});

test('una foto no se manda a transcribir', async () => {
  const openai = openaiFalso({ text: 'no deberia llamarse' });
  const s = await conLead({ openai });
  s.proveedor.limpiar();

  await s.proveedor.simularSinTexto({
    from: '59899123456', tipo: 'imagen',
    descargar: async () => Buffer.from('jpg'),
  });
  await s.cola.vacia();

  assert.equal(openai.transcripciones.length, 0);
  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.equal(alLead.at(-1), '[sin_texto_archivo]');
});
