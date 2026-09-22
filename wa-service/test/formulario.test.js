'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { leerFormulario, mapearBusiness, mapearPresupuesto } = require('../src/funnel/formulario');
const { montar, stubModelo } = require('./helpers');
const { resumenEmbudo } = require('../src/templates/messages');

/**
 * El formulario real de Patricia (22-9), anonimizado. El bot la dejo con
 * business_type=6 ("Todavia no sabe") aunque escribio "Crear mi ecommerce":
 * la respuesta vaga de despues ("Quisiera vender de todo un poco") piso el
 * dato bueno.
 */
const FORM_PATRICIA = `¡Hola! Completé el formulario y me gustaría obtener más información sobre tu negocio.

¿Que es lo que buscás para tu negocio?: Crear mi ecommerce
¿Contás con un presupuesto para este proyecto?: Aún no lo se
¿Cuál es tu objetivo para este año?: Crecer
¿Cómo se llama tu negocio?: Ventas Patybell
Full name: Patricia Ejemplo
Phone number: +598 99 000 111
Email: patricia@ejemplo.com
City: Cerro Colorado`;

const FORM_SEBASTIAN = `¡Hola! Completé el formulario y me gustaría obtener más información sobre tu negocio.

¿Que es lo que buscás para tu negocio?: Automatizaciones
¿Contás con un presupuesto para este proyecto?: 2000
¿Cuál es tu objetivo para este año?: Ordenarme
¿Cómo se llama tu negocio?: Sebastián Cabrera- Fisioterapeuta
Full name: Sebastian Cabrera
Phone number: +598 99 000 222
Email: sebastian@ejemplo.com
City: Montevideo`;

test('el formulario de Patricia: ecommerce, no sabe presupuesto, nombre y negocio', () => {
  const r = leerFormulario(FORM_PATRICIA);
  assert.ok(r);
  assert.equal(r.business_type, 2, 'ecommerce');
  assert.equal(r.budget, 4, 'no sabe');
  assert.equal(r.business_name, 'Ventas Patybell');
  assert.equal(r.nombre, 'Patricia Ejemplo');
  assert.match(r.needs, /Crear mi ecommerce/);
  assert.match(r.needs, /objetivo: Crecer/);
});

test('el formulario de Sebastian: automatizaciones, presupuesto con numero', () => {
  const r = leerFormulario(FORM_SEBASTIAN);
  assert.ok(r);
  assert.equal(r.business_type, 3, 'automatizaciones');
  assert.equal(r.budget, 2, '2000 cae en el tramo 500-3000');
  assert.equal(r.business_name, 'Sebastián Cabrera- Fisioterapeuta');
  assert.equal(r.nombre, 'Sebastian Cabrera');
});

test('el orden de las lineas no importa', () => {
  const lineas = FORM_PATRICIA.split('\n');
  const mezclado = [lineas[0], ...lineas.slice(1).reverse()].join('\n');
  const r = leerFormulario(mezclado);
  assert.equal(r.business_type, 2);
  assert.equal(r.business_name, 'Ventas Patybell');
});

test('etiquetas sin acento tambien se reconocen', () => {
  const texto = [
    'Que es lo que buscas para tu negocio?: Pagina web',
    'Como se llama tu negocio?: Mi Negocio',
  ].join('\n');
  const r = leerFormulario(texto);
  assert.equal(r.business_type, 1, 'web');
  assert.equal(r.business_name, 'Mi Negocio');
});

test('formulario incompleto: solo dos etiquetas, igual se reconoce', () => {
  const texto = '¿Que es lo que buscás para tu negocio?: Un sistema de turnos\nFull name: Ana';
  const r = leerFormulario(texto);
  assert.ok(r);
  assert.equal(r.business_type, 4, 'sistema/turnos');
  assert.equal(r.nombre, 'Ana');
});

test('un mensaje comun no es un formulario', () => {
  assert.equal(leerFormulario('Hola, quiero mas info sobre precios porfa'), null);
  assert.equal(leerFormulario('Necesito un presupuesto para mi negocio'), null, 'sin "Etiqueta:" no hay nada que reconocer');
});

test('una sola etiqueta con formato "Etiqueta: valor" no alcanza para ser formulario', () => {
  assert.equal(leerFormulario('Presupuesto: 500'), null);
  assert.equal(leerFormulario('¿Contás con un presupuesto para este proyecto?: 500'), null);
});

test('valores raros, vacios o sin match no rompen nada', () => {
  assert.equal(leerFormulario(''), null);
  assert.equal(leerFormulario('   '), null);
  assert.equal(leerFormulario(null), null);
  assert.equal(leerFormulario(undefined), null);
  assert.equal(leerFormulario(12345), null);
  assert.equal(leerFormulario({}), null);
  assert.equal(leerFormulario([]), null);
  assert.doesNotThrow(() => leerFormulario('a'.repeat(200_000)));

  // Dos etiquetas conocidas, pero con valores que no dicen nada guardable.
  const r = leerFormulario('Phone number: +598\nEmail: nada@nada.com');
  assert.equal(r, null, 'sin nombre ni necesidad ni presupuesto, no hay nada que guardar');
});

test('no pisa datos existentes: eso lo decide quien llama, leerFormulario solo lee', () => {
  // leerFormulario no sabe nada del lead: el "no pisar" lo hace registrarRespuesta
  // llenando solo los campos vacios. Este test deja constancia de que el modulo
  // siempre devuelve lo que encontro, sin mirar el estado previo.
  const r = leerFormulario('¿Que es lo que buscás para tu negocio?: Pagina web\nFull name: Juan');
  assert.equal(r.business_type, 1);
  assert.equal(r.nombre, 'Juan');
});

// ── mapeos, sueltos ──────────────────────────────────────────────────────────

test('mapearBusiness: cada categoria', () => {
  assert.equal(mapearBusiness('crear mi ecommerce'), 2);
  assert.equal(mapearBusiness('quiero una tienda online'), 2);
  assert.equal(mapearBusiness('automatizaciones'), 3);
  assert.equal(mapearBusiness('un agente de ia'), 5);
  assert.equal(mapearBusiness('quiero un chatbot'), 5);
  assert.equal(mapearBusiness('un sistema de reservas'), 4);
  assert.equal(mapearBusiness('software a medida'), 4);
  assert.equal(mapearBusiness('una pagina web'), 1);
  assert.equal(mapearBusiness('un sitio'), 1);
  assert.equal(mapearBusiness('todavia no se'), 6);
  assert.equal(mapearBusiness('quisiera vender de todo un poco'), null, 'ambiguo: no se adivina');
});

test('mapearBusiness no confunde "ia" adentro de otra palabra', () => {
  assert.equal(mapearBusiness('tenia pensado algo'), null, '"tenia" no es "ia"');
  assert.equal(mapearBusiness('bibliografia del proyecto'), null);
});

test('mapearBusiness no confunde "bot" adentro de otra palabra', () => {
  assert.equal(mapearBusiness('quiero un robot'), null, '"robot" no es "bot"');
});

// ── cableado en leads.js: nunca pisa lo que ya estaba ───────────────────────

test('un formulario que llega despues no pisa datos ya guardados', async () => {
  const TEL_WIRING = '59899555666';
  const s = await montar();
  await s.servicioLeads.registrarRespuesta(TEL_WIRING, 'hola');
  await s.cola.vacia();

  const antes = s.repo.leadPorTelefono(TEL_WIRING);
  s.repo.actualizarFunnel(antes.id, { business_name: 'Ya tenia este nombre', budget: 3 });

  await s.servicioLeads.registrarRespuesta(TEL_WIRING, FORM_PATRICIA);
  await s.cola.vacia();

  const despues = s.repo.leadPorTelefono(TEL_WIRING);
  assert.equal(despues.business_name, 'Ya tenia este nombre', 'no se piso');
  assert.equal(despues.budget, 3, 'no se piso');
  // Lo que SI estaba vacio (business_type, needs) se llena igual.
  assert.equal(despues.business_type, 2);
});

test('un formulario que llega primero SI llena los campos vacios', async () => {
  const TEL_WIRING = '59899777888';
  const s = await montar();

  await s.servicioLeads.registrarRespuesta(TEL_WIRING, FORM_PATRICIA);
  await s.cola.vacia();

  const lead = s.repo.leadPorTelefono(TEL_WIRING);
  assert.equal(lead.business_name, 'Ventas Patybell');
  assert.equal(lead.business_type, 2);
  assert.equal(lead.nombre, 'Patricia Ejemplo');
});

// ── de punta a punta: el caso real de Patricia ──────────────────────────────

const TEL = '59899222333';

/**
 * El formulario decia "Crear mi ecommerce". La respuesta vaga de despues
 * ("Quisiera vender de todo un poco") hacia que el modelo volviera a elegir
 * "todavia no sabe" (6) y se lo pisara. Con el formulario leido en codigo y
 * la guardia de guardarCampos, el dato bueno sobrevive y el aviso al equipo
 * ya no dice "Todavía no sabe".
 */
test('de punta a punta: el formulario de Patricia sobrevive a la respuesta vaga posterior', async () => {
  // El modelo siempre "confunde" y dice que no sabe: es la peor version del
  // bug, para probar que la guardia alcanza igual.
  const modelo = stubModelo({
    datos: { business_type: 'no_sabe', business_type_dicho: 'de todo un poco' },
  });
  const s = await montar({ modelo });

  await s.servicioLeads.registrarRespuesta(TEL, FORM_PATRICIA);
  await s.cola.vacia();

  let lead = s.repo.leadPorTelefono(TEL);
  assert.equal(lead.business_type, 2, 'el formulario dejo ecommerce, ni el modelo lo piso en el mismo turno');

  await s.servicioLeads.registrarRespuesta(TEL, 'Quisiera vender de todo un poco');
  await s.cola.vacia();

  lead = s.repo.leadPorTelefono(TEL);
  assert.equal(lead.business_type, 2, 'la respuesta vaga no piso el dato del formulario');

  const aviso = resumenEmbudo(lead, 'nurture');
  assert.doesNotMatch(aviso, /Todavía no sabe/);
  assert.match(aviso, /E-commerce/);
});

test('mapearPresupuesto: tramos', () => {
  assert.equal(mapearPresupuesto('no se'), 4);
  assert.equal(mapearPresupuesto('aun no lo se'), 4);
  assert.equal(mapearPresupuesto('300'), 1);
  assert.equal(mapearPresupuesto('500'), 2);
  assert.equal(mapearPresupuesto('$1.500'), 2);
  assert.equal(mapearPresupuesto('3000'), 2);
  assert.equal(mapearPresupuesto('3001'), 3);
  assert.equal(mapearPresupuesto('mucho'), undefined, 'sin numero y sin "no se": no se adivina');
});

/**
 * Correccion pedida en la revision del PR #89: mapearPresupuesto tomaba el
 * primer numero sin mirar el calificador. "Mas de USD 3000" y "Menos de USD
 * 500" son, casi seguro, las opciones textuales reales del formulario de
 * Meta —calcan los bordes de nuestros propios tramos— y las dos daban 2 en
 * vez de 3 y 1.
 */
test('mapearPresupuesto: "mas de" y "arriba de" usan el tramo de arriba', () => {
  assert.equal(mapearPresupuesto('mas de usd 3000'), 3, 'no 2: es MAS de 3000');
  assert.equal(mapearPresupuesto('mas de 500'), 2);
  assert.equal(mapearPresupuesto('arriba de 3000'), 3);
  assert.equal(mapearPresupuesto('3000+'), 3);
  assert.equal(mapearPresupuesto('+3000'), 3);
});

test('mapearPresupuesto: "menos de", "hasta" y "debajo de" usan el tramo de abajo', () => {
  assert.equal(mapearPresupuesto('menos de usd 500'), 1, 'no 2: es MENOS de 500');
  assert.equal(mapearPresupuesto('hasta 500'), 1);
  assert.equal(mapearPresupuesto('debajo de 500'), 1);
  assert.equal(mapearPresupuesto('menos de 3000'), 2);
});

test('mapearPresupuesto: un rango usa el punto medio', () => {
  assert.equal(mapearPresupuesto('500 - 3000'), 2);
  assert.equal(mapearPresupuesto('entre 500 y 3000'), 2);
  assert.equal(mapearPresupuesto('100-400'), 1);
  assert.equal(mapearPresupuesto('4000-6000'), 3);
});

test('mapearPresupuesto: formatos de moneda comunes', () => {
  assert.equal(mapearPresupuesto('usd 1.500'), 2);
  assert.equal(mapearPresupuesto('u$s 2000'), 2);
  assert.equal(mapearPresupuesto('1,500'), 2, 'coma de miles, no decimal');
  assert.equal(mapearPresupuesto('3k'), 2, '"3k" son 3000, tramo 2 (limite de arriba)');
  assert.equal(mapearPresupuesto('400 dolares'), 1);
});

test('mapearPresupuesto: lo que no se entiende queda vacio, no se inventa', () => {
  assert.equal(mapearPresupuesto('lo que haga falta'), undefined);
  assert.equal(mapearPresupuesto(''), undefined);
});

/**
 * De punta a punta, con el texto tal cual lo escribiria el formulario:
 * mayusculas, acentos y el signo de pregunta de la etiqueta.
 */
test('leerFormulario: "Más de USD 3000" en el formulario real da tramo 3', () => {
  const texto = [
    '¿Que es lo que buscás para tu negocio?: Página web',
    '¿Contás con un presupuesto para este proyecto?: Más de USD 3000',
  ].join('\n');
  assert.equal(leerFormulario(texto).budget, 3);
});

test('leerFormulario: "Menos de USD 500" en el formulario real da tramo 1', () => {
  const texto = [
    '¿Que es lo que buscás para tu negocio?: Página web',
    '¿Contás con un presupuesto para este proyecto?: Menos de USD 500',
  ].join('\n');
  assert.equal(leerFormulario(texto).budget, 1);
});
