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

/**
 * Modelo falso: devuelve lo que se le diga, y anota como lo llamaron.
 *
 * Habla la interfaz de src/ia/modelo.js y no la de ningun SDK. Con una lista
 * va contestando de a una, que es como se guionan las conversaciones de varios
 * turnos.
 */
function modeloFalso(respuestas) {
  const pila = Array.isArray(respuestas) ? respuestas.slice() : [respuestas];
  const llamadas = [];
  return {
    activo: true,
    modelo: 'falso',
    llamadas,

    async pedir(args) {
      // Sin herramienta es el redactor pidiendo un mensaje suelto. Se contesta
      // con el marcador de la situacion, igual que el stub compartido: los
      // guiones de estos tests son para la conversacion.
      if (!args.herramienta) {
        const m = args.mensajes[0].content.match(/situación: (\w+)/);
        return { texto: `[${m ? m[1] : 'desconocida'}]`, argumentos: null };
      }
      llamadas.push(args);
      const r = pila.length > 1 ? pila.shift() : pila[0];
      // La capa neutral no propaga excepciones: un fallo es un null.
      if (r instanceof Error) return null;
      return r;
    },
  };
}

/** El de OpenAI, que ahora solo transcribe. Ese si es el SDK crudo. */
function transcriptorFalso(respuestas) {
  const pila = Array.isArray(respuestas) ? respuestas.slice() : [respuestas];
  const transcripciones = [];
  return {
    transcripciones,
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
  texto: null,
  argumentos: { mensaje, ...datos },
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
    'Dale, te paso el link: https://calendly.com/scalerics/consultoriagratuita',
    '¿Ustedes son 2 o 3 personas en el local?',
    'Seguime en @local2000 y vemos',
  ]) {
    assert.equal(mencionaPlata(t), false, t);
  }
});

test('si la IA se manda un precio, sale el texto fijo en su lugar', async () => {
  const agente = crearAgente({
    modelo: modeloFalso(conTexto('Una web te sale unos USD 900 más IVA')),
    textos,
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
    modelo: modeloFalso(conTexto('¡Buenísimo! ¿Y qué te gustaría lograr?', {
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
    modelo: modeloFalso(conTexto('Listo, te paso mi Calendly ahora mismo', {
      business_name: 'Inmobiliaria Pereyra', rubro: 'inmobiliaria',
      business_type: 'ecommerce',
    })),
  });

  await s.servicioLeads.registrarRespuesta('59899123456', 'te cuento todo de una');
  await s.cola.vacia();

  const l = s.repo.leadPorTelefono('59899123456');
  assert.equal(l.fsm_state, S.MEETING_LINK_SENT);

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.equal(alLead.at(-1), '[link_reunion]', 'lo dispara el embudo, no el modelo');
  assert.ok(!alLead.some((m) => /te paso mi Calendly ahora mismo/.test(m)), 'no sale su texto de cierre');
});

test('una queja se deriva por codigo, sin pasar por la IA', async () => {
  const modelo = modeloFalso(conTexto('Uy, contame qué pasó'));
  const s = await conLead({ modelo });

  await s.servicioLeads.registrarRespuesta('59899123456', 'esto es una estafa');
  await s.cola.vacia();

  assert.equal(s.repo.leadPorTelefono('59899123456').fsm_state, S.HUMAN_QUEUED);
  assert.equal(modelo.llamadas.length, 0, 'ni se le pregunta al modelo');
});

test('la baja se respeta por codigo, sin pasar por la IA', async () => {
  const modelo = modeloFalso(conTexto('¡No te vayas!'));
  const s = await conLead({ modelo });

  await s.servicioLeads.registrarRespuesta('59899123456', 'baja');
  await s.cola.vacia();

  assert.equal(s.repo.leadPorTelefono('59899123456').opt_out, 1);
  assert.equal(modelo.llamadas.length, 0);
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
  const s = await conLead({ modelo: modeloFalso(new Error('529 overloaded')) });

  await s.servicioLeads.registrarRespuesta('59899123456', 'hola');
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.equal(alLead.length, 1, 'contesta igual');
  assert.match(alLead.at(-1), /te paso con alguien del equipo/, 'pero lo pasa con una persona');
});

test('si la IA guarda datos pero no contesta, tampoco se queda mudo', async () => {
  const s = await conLead({
    modelo: modeloFalso({ texto: null, argumentos: { rubro: 'x' } }),
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
  // Con OpenAI esto era prolijidad; con Anthropic es obligatorio. Si la
  // conversacion arranca con el bot —y arranca, porque la bienvenida sale
  // primero— la API rechaza el pedido y el lead termina derivado a una persona.
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
  const openai = transcriptorFalso({ text: '  Hola, tengo una parrilla en Pocitos  ' });
  const t = crearTranscriptor({ openai, modelo: 'whisper-1' });

  const texto = await t.transcribir(Buffer.from('audio-falso'), 12);

  assert.equal(texto, 'Hola, tengo una parrilla en Pocitos', 'devuelve el texto sin espacios sobrantes');
  assert.equal(openai.transcripciones[0].model, 'whisper-1');
  assert.equal(openai.transcripciones[0].language, 'es', 'le dice que es español');
});

test('un audio larguisimo no se transcribe', async () => {
  const openai = transcriptorFalso({ text: 'lo que sea' });
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
  const t = crearTranscriptor({ openai: transcriptorFalso(new Error('429')), modelo: 'whisper-1' });
  assert.equal(await t.transcribir(Buffer.from('x'), 5), null);
});

test('una nota de voz entra al embudo como si la hubieran escrito', async () => {
  // Es el punto de todo esto: el audio no se contesta con "escribime", se
  // escucha y sigue el mismo camino que el texto.
  const s = await conLead({
    openai: transcriptorFalso({ text: 'Hola, tenemos una parrilla con delivery en Pocitos' }),
    modelo: modeloFalso(conTexto('¡Buenísimo! ¿Y qué te gustaría lograr?', {
      rubro: 'parrilla con delivery',
    })),
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
  const s = await conLead({ openai: transcriptorFalso(new Error('sin credito')) });

  await s.proveedor.simularSinTexto({
    from: '59899123456', tipo: 'audio', segundos: 9,
    descargar: async () => Buffer.from('ogg-falso'),
  });
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
  assert.equal(alLead.at(-1), '[sin_texto_audio]');
});

test('una foto no se manda a transcribir', async () => {
  const openai = transcriptorFalso({ text: 'no deberia llamarse' });
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

test('agente de IA es una opcion propia, separada de automatizacion', () => {
  // Decision del negocio: son cinco cosas distintas que vender, y meter el
  // agente de IA adentro de "automatizacion" escondia justamente el servicio
  // que mas se quiere ofrecer.
  assert.deepEqual(sanearDatos({ business_type: 'automatizacion' }), { business_type: 3 });
  assert.deepEqual(sanearDatos({ business_type: 'agente_ia' }), { business_type: 5 });

  const { HERRAMIENTA } = require('../src/ia/agente');
  const opciones = HERRAMIENTA.parametros.properties.business_type.enum;

  // Cinco servicios, mas "todavia no sabe", que no es un servicio: es la
  // respuesta de uno de cada seis que agenda, y ahora tambien cierra el embudo.
  assert.deepEqual(
    opciones,
    ['web', 'ecommerce', 'sistema', 'automatizacion', 'agente_ia', 'no_sabe'],
    'los cinco servicios y el "no sé", ni uno mas'
  );
  assert.ok(opciones.includes('agente_ia'));
});

// ── dos proveedores, dos claves ──────────────────────────────────────────────

const { crearModelo, avisarSiFaltaClave } = require('../src/ia/modelo');

test('la capa del modelo traduce la respuesta cruda a texto y argumentos', async () => {
  // Es el unico lugar del bot que conoce la forma del SDK. Si esto se
  // equivoca, todo lo de arriba recibe null y cada lead termina derivado.
  const pedidos = [];
  const cliente = {
    messages: {
      create: async (p) => {
        pedidos.push(p);
        return {
          content: [
            { type: 'text', text: 'pensando en voz alta' },
            { type: 'tool_use', name: 'responder', input: { mensaje: 'hola', rubro: 'parrilla' } },
          ],
        };
      },
    },
  };

  const m = crearModelo({ cliente, modelo: 'claude-x' });
  const r = await m.pedir({
    system: 'sos un bot',
    mensajes: [{ role: 'user', content: 'hola' }],
    herramienta: { nombre: 'responder', descripcion: 'contesta', parametros: { type: 'object' } },
  });

  assert.deepEqual(r.argumentos, { mensaje: 'hola', rubro: 'parrilla' });
  assert.equal(r.texto, 'pensando en voz alta');

  const p = pedidos[0];
  assert.equal(p.model, 'claude-x');
  assert.equal(p.system, 'sos un bot', 'el system va aparte de los mensajes, no adentro');
  assert.ok(p.max_tokens > 0, 'siempre lleva max_tokens: la API lo exige');
  assert.deepEqual(
    p.tool_choice,
    { type: 'tool', name: 'responder', disable_parallel_tool_use: true },
    'la herramienta va forzada, y una sola vez'
  );
});

test('si la API tira, la capa devuelve null en vez de propagar', async () => {
  const cliente = { messages: { create: async () => { throw new Error('529 overloaded'); } } };
  const m = crearModelo({ cliente });
  assert.equal(await m.pedir({ system: 'x', mensajes: [] }), null);
});

test('sin cliente queda inactivo y no inventa respuestas', async () => {
  const m = crearModelo({ cliente: null });
  assert.equal(m.activo, false);
  assert.equal(await m.pedir({ system: 'x', mensajes: [] }), null);
});

test('arrancar sin una clave se avisa: en silencio se ve igual que andar bien', () => {
  // Faltar la clave no rompe el arranque, y por eso hay que gritarlo: el bot
  // levanta, contesta, y deriva a una persona cada lead que escribe.
  const encolados = [];
  const cfg = { IA_CONVERSACION: true, IA_TRANSCRIPCION: true, amPhones: ['59899000111'] };
  const cola = { encolar: (m) => encolados.push(m) };

  const faltan = avisarSiFaltaClave({ cfg, cola, ia: { conversacion: false, transcripcion: true } });
  assert.equal(faltan.length, 1);
  assert.match(faltan[0], /ANTHROPIC_API_KEY/);
  assert.equal(encolados.length, 1, 'le llega al equipo, no solo al log');
  assert.match(encolados[0].texto, /ANTHROPIC_API_KEY/);
  assert.ok(!/OPENAI/.test(encolados[0].texto), 'y no se queja de la que si esta');
});

test('con las dos claves puestas no molesta a nadie', () => {
  const encolados = [];
  const faltan = avisarSiFaltaClave({
    cfg: { IA_CONVERSACION: true, IA_TRANSCRIPCION: true, amPhones: ['59899000111'] },
    cola: { encolar: (m) => encolados.push(m) },
    ia: { conversacion: true, transcripcion: true },
  });
  assert.deepEqual(faltan, []);
  assert.equal(encolados.length, 0);
});

// ── respuestas cortadas y esperas largas ─────────────────────────────────────

const { recortarEnOracion, clienteAnthropic } = require('../src/ia/modelo');

test('un texto cortado a la mitad se recorta en la última oración completa', () => {
  assert.equal(
    recortarEnOracion('Buenas, Ana. Contame un poco del proyecto. Y también quería pregunt'),
    'Buenas, Ana. Contame un poco del proyecto.'
  );
  assert.equal(
    recortarEnOracion('¿Cómo se llama el negocio? Así lo anoto y despu'),
    '¿Cómo se llama el negocio?'
  );
});

test('sin un solo punto, corta en el último renglón entero', () => {
  // Apareció probando contra la API de verdad: devolvió títulos y viñetas,
  // donde no hay puntuación que cortar, y lo que sobraba era media palabra.
  assert.equal(
    recortarEnOracion('Servicios que ofrecemos\nDesarrollamos estrategias de marketing integ'),
    'Servicios que ofrecemos'
  );
});

test('si no hay dónde cortar, se devuelve lo que vino', () => {
  // Cortar en la nada deja algo peor que el original: media palabra es feo,
  // pero un mensaje vacío es un lead sin respuesta.
  assert.equal(recortarEnOracion('Dale, te paso el'), 'Dale, te paso el');
  assert.equal(recortarEnOracion(''), '');
  assert.equal(recortarEnOracion(null), '');
});

test('la capa avisa cuando la respuesta llegó al tope de tokens', async () => {
  const cliente = {
    messages: {
      create: async () => ({
        stop_reason: 'max_tokens',
        content: [{ type: 'text', text: 'Hacemos webs y sistemas a medida. También automatiza' }],
      }),
    },
  };

  const r = await crearModelo({ cliente }).pedir({ system: 'x', mensajes: [] });
  assert.equal(r.truncado, true);
  assert.equal(r.texto, 'Hacemos webs y sistemas a medida.', 'el texto suelto lo recorta la capa');
});

test('el mensaje al lead también se recorta, aunque venga por la herramienta', async () => {
  // Es el caso que importa: nuestro mensaje no viaja en el texto suelto sino
  // adentro de la herramienta. Recortar solo el texto seria copiar la forma
  // del arreglo sin arreglar nada.
  const largo = 'Buenas, Ana. Contame un poco de qué se trata el proyecto. Y también quería pregunt';
  const agente = crearAgente({
    modelo: modeloFalso({ texto: null, argumentos: { mensaje: largo }, truncado: true }),
    textos,
  });

  const r = await agente.responder({ id: 1, nombre: 'Ana' }, 'hola', []);
  assert.equal(r.texto, 'Buenas, Ana. Contame un poco de qué se trata el proyecto.');
  assert.ok(!/pregunt$/.test(r.texto), 'no le llega una palabra por la mitad');
});

test('el cliente no espera diez minutos por una respuesta', async () => {
  // El default del SDK son 10 minutos y 2 reintentos: media hora colgado en un
  // solo mensaje, con los mensajes siguientes del lead encolados detrás.
  const c = clienteAnthropic('sk-ant-de-mentira');
  assert.ok(c.timeout <= 30_000, `esperaba menos de 30s, hay ${c.timeout}ms`);
  assert.ok(c.maxRetries <= 1, 'y como mucho un reintento');
});
