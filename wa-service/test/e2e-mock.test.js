'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { CLAVE, LEAD, montar } = require('./helpers');


function postLead(s, body = LEAD) {
  return s.app.inject({
    method: 'POST', url: '/leads',
    headers: { 'x-api-key': CLAVE }, payload: body,
  });
}

test('POST /leads responde 202 y crea el lead', async () => {
  const s = await montar();
  const r = await postLead(s);

  assert.equal(r.statusCode, 202);
  const body = r.json();
  assert.equal(body.ok, true);
  assert.equal(body.status, 'queued');

  const lead = s.repo.leadPorId(body.lead_id);
  assert.equal(lead.nombre, 'Martín Pereyra');
  assert.equal(lead.telefono, '59899123456', 'el telefono se normaliza a E.164 sin +');
  assert.equal(lead.rubro_norm, 'inmobiliaria');
});

test('manda la ficha al AM y la bienvenida al lead', async () => {
  const s = await montar();
  await postLead(s);
  await s.cola.vacia();

  const enviados = s.proveedor.getEnviados();
  assert.equal(enviados.length, 2);

  const ficha = enviados.find((e) => e.to === '59899000111');
  assert.ok(ficha, 'la ficha va al AM configurado');
  assert.match(ficha.texto, /Nuevo lead/);
  assert.match(ficha.texto, /Martín Pereyra/);
  assert.match(ficha.texto, /Inmobiliaria/);
  assert.match(ficha.texto, /\+59899123456/);
  assert.match(ficha.texto, /seguimiento de consultas de alquiler/);
  assert.match(ficha.texto, /wa\.me\/59899123456/);

  const bienvenida = enviados.find((e) => e.to === '59899123456');
  assert.ok(bienvenida, 'la bienvenida va al lead');
  // El saludo es fijo por decision del negocio: el mismo para todos, y avisa
  // de entrada que vienen preguntas.
  const { crearTextos } = require('../src/templates/funnel');
  assert.equal(bienvenida.texto, crearTextos().BIENVENIDA);
  assert.match(bienvenida.texto, /agente comercial de Scalerics/);
  assert.ok(!/https?:\/\//.test(bienvenida.texto), 'sin links: es el primer contacto');
});

test('la ficha al AM sale antes que la bienvenida', async () => {
  const s = await montar();
  await postLead(s);
  await s.cola.vacia();

  const enviados = s.proveedor.getEnviados();
  assert.equal(enviados[0].to, '59899000111', 'am_notice tiene prioridad sobre welcome');
});

test('a las 72h sin respuesta sale el follow-up', async () => {
  const s = await montar();
  await postLead(s);
  await s.cola.vacia();
  s.proveedor.limpiar();

  // A las 25 horas todavia no: el plazo pasa a 72h.
  const en25Horas = new Date(Date.now() + 25 * 3600 * 1000);
  assert.equal(await s.scheduler.correrVencidos(en25Horas), 0);

  const en73Horas = new Date(Date.now() + 73 * 3600 * 1000);
  assert.equal(await s.scheduler.correrVencidos(en73Horas), 1);
  await s.cola.vacia();

  const enviados = s.proveedor.getEnviados();
  const followup = enviados.find((e) => e.to === '59899123456');
  assert.ok(followup, 'le llega el follow-up al lead');
  assert.equal(followup.texto, '[followup]');

  const avisoAM = enviados.find((e) => e.to === '59899000111');
  assert.match(avisoAM.texto, /no respondió en 72h/);

  const lead = s.repo.leadPorTelefono('59899123456');
  assert.equal(lead.status, 'followed_up');
});

test('si el lead responde se cancela el follow-up y se avisa al AM', async () => {
  const s = await montar();
  await postLead(s);
  await s.cola.vacia();
  s.proveedor.limpiar();

  await s.servicioLeads.registrarRespuesta('59899123456', 'Sí, me interesa. ¿Cuánto sale?');
  await s.cola.vacia();

  const lead = s.repo.leadPorTelefono('59899123456');
  assert.equal(lead.status, 'replied');
  assert.ok(lead.replied_at);

  const aviso = s.proveedor.getEnviados().find((e) => e.to === '59899000111');
  assert.match(aviso.texto, /respondió/);
  assert.match(aviso.texto, /Cuánto sale/);

  // El follow-up de las 72h ya no sale: ese es para el que nunca contesto.
  s.proveedor.limpiar();
  const en73Horas = new Date(Date.now() + 73 * 3600 * 1000);
  await s.scheduler.correrVencidos(en73Horas);
  await s.cola.vacia();

  const textos = s.proveedor.getEnviados().map((e) => e.texto);
  assert.ok(!textos.some((t) => /followup/.test(t)), 'no se le insiste a quien ya contesto');
  // Pero pregunto una vez y desaparecio: eso es irse de la conversacion, y va
  // a una persona.
  assert.ok(textos.some((t) => /derivado_por_abandono/.test(t)), 'lo levanta el agente comercial');
});

test('el mismo external_id no duplica el lead', async () => {
  const s = await montar();
  const primera = await postLead(s);
  const segunda = await postLead(s);

  assert.equal(primera.statusCode, 202);
  assert.equal(segunda.statusCode, 200);
  assert.equal(segunda.json().status, 'ya_existia');
  assert.equal(segunda.json().lead_id, primera.json().lead_id);

  await s.cola.vacia();
  assert.equal(s.proveedor.getEnviados().length, 2, 'no se reenvia nada');
});

test('un telefono ilegible igual notifica al AM y marca el lead como fallido', async () => {
  const s = await montar();
  const r = await postLead(s, { ...LEAD, external_id: 'lead_x', telefono: 'no tengo' });

  assert.equal(r.statusCode, 202);
  assert.equal(r.json().status, 'telefono_invalido');
  await s.cola.vacia();

  const enviados = s.proveedor.getEnviados();
  assert.equal(enviados.length, 1, 'solo la ficha al AM');
  assert.equal(enviados[0].to, '59899000111');
  assert.match(enviados[0].texto, /no reconocido/);

  const lead = s.repo.leadPorId(r.json().lead_id);
  assert.equal(lead.status, 'failed');
});

test('un rubro desconocido usa la plantilla generica', async () => {
  const s = await montar();
  await postLead(s, { ...LEAD, external_id: 'lead_y', rubro: 'Tornería industrial' });
  await s.cola.vacia();

  const lead = s.repo.leadPorTelefono('59899123456');
  assert.equal(lead.rubro_norm, 'generico');

  const bienvenida = s.proveedor.getEnviados().find((e) => e.to === '59899123456');
  // El saludo no depende del rubro: es el mismo para todos.
  const { crearTextos } = require('../src/templates/funnel');
  assert.equal(bienvenida.texto, crearTextos().BIENVENIDA);
});

test('sin x-api-key no se entra', async () => {
  const s = await montar();
  const r = await s.app.inject({ method: 'POST', url: '/leads', payload: LEAD });
  assert.equal(r.statusCode, 401);
});

test('/health no pide clave y reporta el proveedor', async () => {
  const s = await montar();
  const r = await s.app.inject({ method: 'GET', url: '/health' });
  assert.equal(r.statusCode, 200);
  assert.equal(r.json().provider, 'mock');
  assert.equal(r.json().connected, true);
});

test('rechaza un alta sin nombre ni telefono', async () => {
  const s = await montar();
  const r = await s.app.inject({
    method: 'POST', url: '/leads',
    headers: { 'x-api-key': CLAVE }, payload: { rubro: 'salud' },
  });
  assert.equal(r.statusCode, 400);
  assert.match(r.json().error, /nombre/);
});

test('el link de Calendly va en el follow-up, no en la bienvenida', async () => {
  // Ya no se puede mirar el texto —lo escribe la IA— asi que se mira la
  // instruccion. Un link en el primer mensaje a alguien que nunca te escribio
  // es de las seniales de spam mas fuertes, y esa regla tiene que estar dicha.
  const { situaciones } = require('../src/ia/prompt');
  const s = situaciones('https://calendly.com/scalerics/diagnostico');

  const { crearTextos } = require('../src/templates/funnel');
  assert.ok(!crearTextos().BIENVENIDA.includes('calendly.com'), 'la bienvenida no lo lleva');
  assert.match(s.followup, /calendly\.com/, 'al follow-up si');
});

test('ningun objetivo trae una frase de ejemplo copiable', () => {
  // Esto paso de verdad con la bienvenida: el objetivo daba un ejemplo
  // entrecomillado de como retomar lo que el lead conto, y el modelo se lo
  // mandaba textual a leads que no habian contado nada. La instruccion que
  // buscaba evitar el invento era la que lo causaba.
  //
  // La regla que quedo: los ejemplos de FORMA sirven ("tenés" y no "tienes");
  // los de CONTENIDO se copian. Vale para las 17 situaciones, no solo la que
  // fallo.
  const { situaciones } = require('../src/ia/prompt');
  const todas = situaciones('https://calendly.com/x');

  for (const [nombre, objetivo] of Object.entries(todas)) {
    const frases = objetivo.match(/"[^"]{25,}"/g) || [];
    assert.deepEqual(
      frases, [],
      `la situacion "${nombre}" trae una frase entrecomillada larga que el modelo puede copiar: ${frases.join(' | ')}`
    );
  }
});


test('el que escribe directo al numero tambien recibe la bienvenida', async () => {
  // Salia solo por el camino del formulario. Al que escribe al numero el bot le
  // arrancaba a preguntar sin presentarse: del otro lado aparece un desconocido
  // pidiendo datos del negocio.
  const s = await montar();
  const { crearTextos } = require('../src/templates/funnel');

  s.proveedor.simularEntrante({ from: '59891234567', texto: 'Buenas, quiero una pagina web', id: 'w.1' });
  await s.agrupador.vaciar();
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59891234567').map((e) => e.texto);
  assert.equal(alLead[0], crearTextos().BIENVENIDA, 'primero se presenta');
  assert.equal(alLead[1], '[conversacion]', 'y despues contesta lo que preguntó');
});

test('la bienvenida no se repite en el segundo mensaje', async () => {
  const s = await montar();
  const { crearTextos } = require('../src/templates/funnel');

  s.proveedor.simularEntrante({ from: '59891234567', texto: 'hola', id: 'w.1' });
  await s.agrupador.vaciar();
  await s.cola.vacia();
  s.proveedor.limpiar();

  s.proveedor.simularEntrante({ from: '59891234567', texto: 'tengo una panaderia', id: 'w.2' });
  await s.agrupador.vaciar();
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === '59891234567').map((e) => e.texto);
  assert.ok(!alLead.includes(crearTextos().BIENVENIDA), 'ya se presentó');
});
