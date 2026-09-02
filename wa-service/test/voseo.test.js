'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { corregir } = require('../src/ia/voseo');
const { conLead, stubModelo } = require('./helpers');

const TEL = '59899123456';

/**
 * El prompt prohibe el tuteo con todas las letras y hasta lista los verbos uno
 * por uno. Aun asi salio "¿Qué necesitas?" en la primera conversacion de
 * prueba. Es la misma leccion que el tamaño del equipo y las fechas: pedirle al
 * modelo una conversion mecanica falla aunque se la expliques con ejemplos.
 */
test('corrige el tuteo que se le escapa al modelo', () => {
  assert.equal(corregir('¿Qué necesitas?').texto, '¿Qué necesitás?');
  assert.equal(corregir('¿A qué te dedicas?').texto, '¿A qué te dedicás?');
  assert.equal(corregir('Si tienes dudas, dime').texto, 'Si tenés dudas, decime');
  assert.equal(corregir('¿Cómo te llamas?').texto, '¿Cómo te llamás?');
  assert.equal(corregir('¿Puedes el martes?').texto, '¿Podés el martes?');
});

/**
 * El \\b de JavaScript mira solo [A-Za-z0-9_], asi que una palabra acentuada no
 * tiene borde donde va el acento. Con \\b, "tú" nunca coincidia y quedaba sin
 * corregir justo el pronombre que mas delata.
 */
test('tambien las palabras con acento', () => {
  assert.equal(corregir('¿Tú qué preferís?').texto, '¿Vos qué preferís?');
  assert.equal(corregir('Contame tú').texto, 'Contame vos');
});

test('respeta la mayúscula de la palabra original', () => {
  assert.equal(corregir('Tienes razón').texto, 'Tenés razón');
  assert.equal(corregir('tienes razón').texto, 'tenés razón');
});

/**
 * La lista es corta a proposito: solo formas donde el tuteo no puede ser otra
 * cosa. Corregir de mas rompe frases validas, y eso es peor que un "tienes".
 */
test('no toca palabras que solo se parecen', () => {
  assert.equal(corregir('Las esperas son largas').texto, 'Las esperas son largas');
  assert.equal(corregir('Tenemos varias cuentas').texto, 'Tenemos varias cuentas');
  assert.equal(corregir('Mandanos tus preguntas').texto, 'Mandanos tus preguntas');
  assert.equal(corregir('Mantenimiento y sostenes').texto, 'Mantenimiento y sostenes');
});

test('lo que ya está en voseo queda igual', () => {
  const bien = 'Contame de tu negocio, ¿qué necesitás? Si querés, podés escribirme.';
  const r = corregir(bien);
  assert.equal(r.texto, bien);
  assert.deepEqual(r.corregidos, []);
});

test('dice qué corrigió, para poder ver si el modelo se va seguido', () => {
  const r = corregir('Si tienes tiempo, dime qué necesitas');
  assert.deepEqual(r.corregidos.sort(), ['dime', 'necesitas', 'tienes']);
});

// ── enchufado a la conversación ──────────────────────────────────────────────

test('lo que le llega al lead sale en voseo aunque el modelo tutee', async () => {
  const s = await conLead({
    modelo: stubModelo({ respuestas: { conversacion: '¿Qué necesitas? Si tienes dudas, dime.' } }),
  });
  await s.servicioLeads.registrarRespuesta(TEL, 'hola');
  await s.cola.vacia();

  const alLead = s.proveedor.getEnviados().filter((e) => e.to === TEL).map((e) => e.texto);
  const conTuteo = alLead.filter((t) => /necesitas|tienes|dime/.test(t));
  assert.equal(conTuteo.length, 0, `salió con tuteo: ${conTuteo.join(' | ')}`);
});

/**
 * El mensaje de oferta del 2-9 salio con "Elige el horario que te quede bien".
 * La lista tenia los presentes —"eliges"— pero no los imperativos, que son
 * justo los que aparecen cuando el bot pide algo, o sea casi siempre.
 *
 * Van aparte de los otros cambios porque no se pueden reemplazar en cualquier
 * lado: en español el imperativo de tú y la tercera persona se escriben igual.
 * "Escribe" es las dos cosas, y una de ellas —"alguien del equipo te escribe"—
 * es una frase que este bot manda de verdad. Por eso solo se corrigen cuando
 * arrancan la frase, que es donde no pueden ser otra cosa.
 */
test('corrige los imperativos, que es donde el bot mas tutea', () => {
  assert.equal(corregir('Elige el horario que te quede bien').texto,
    'Elegí el horario que te quede bien');
  assert.equal(corregir('Escribe cuando quieras').texto, 'Escribí cuando quieras');
  assert.equal(corregir('Espera un momento').texto, 'Esperá un momento');
  assert.equal(corregir('Avisa si no podés').texto, 'Avisá si no podés');
  assert.equal(corregir('Confirma y listo').texto, 'Confirmá y listo');
  assert.equal(corregir('Contesta cuando puedas').texto, 'Contestá cuando puedas');
  assert.equal(corregir('Responde este mensaje').texto, 'Respondé este mensaje');
  assert.equal(corregir('Mira el link').texto, 'Mirá el link');
  assert.equal(corregir('Manda lo que tengas').texto, 'Mandá lo que tengas');
  assert.equal(corregir('Envía la info cuando puedas').texto, 'Enviá la info cuando puedas');
});

test('el imperativo tambien cuenta despues de un punto o un salto de linea', () => {
  assert.equal(corregir('Ya tenés el link. Elige el que te sirva').texto,
    'Ya tenés el link. Elegí el que te sirva');
  assert.equal(corregir(`Estos son los horarios:
Elige uno`).texto,
    `Estos son los horarios:
Elegí uno`);
});

/**
 * El caso que hace que esto no pueda ser un reemplazo global. Son frases que el
 * bot escribe todo el tiempo cuando deriva a una persona.
 */
test('no toca la tercera persona, que se escribe igual que el imperativo', () => {
  assert.equal(corregir('Alguien del equipo te escribe en breve').texto,
    'Alguien del equipo te escribe en breve');
  assert.equal(corregir('El equipo te confirma la reunión').texto,
    'El equipo te confirma la reunión');
  assert.equal(corregir('Ahora te contesta un compañero').texto,
    'Ahora te contesta un compañero');
});
