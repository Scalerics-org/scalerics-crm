'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { construirRedaccion } = require('../src/ia/prompt');
const { cambiaLaNecesidad } = require('../src/ia/necesidad');
const { crearRedactor } = require('../src/ia/redactor');

/**
 * Charla de Los Sopranos, 22-9 a las 21:40. El lead ya habia dicho que queria
 * una pagina web (business_type = 1), y el pitch salio:
 *
 *   → "...agilizar los pedidos que te llegan... un prototipo de como quedaria
 *      tu sistema de pedidos online."
 *   ← "dije pagina web"
 *
 * Dos causas, las dos en lo que el modelo lee:
 *  1. el prompt le pasaba "Tipo de proyecto: 1." —un numero de opcion que no
 *     significa nada para el— y
 *  2. le pasaba ademas el "gancho" del rubro (gastronomia: "un sistema de
 *     pedidos online"), que es lo que se les dice a los que todavia no dijeron
 *     que necesitan. Con un rubro con gancho y un dato que no entendia, el
 *     modelo se quedo con el gancho.
 * Y el control que existe contra esto (cambiaLaNecesidad) solo miraba el texto
 * libre `needs`, no el tipo de proyecto, y solo conocia "tienda online".
 */
const SOPRANOS = {
  id: 1, nombre: 'Juan', business_name: 'Los Sopranos', rubro: 'pizzeria',
  rubro_norm: 'gastronomia', business_type: 1,
};

test('el prompt le dice al modelo QUE necesita, no un numero de opcion', () => {
  const p = construirRedaccion(SOPRANOS, 'oferta_con_horarios');
  assert.match(p, /Página web/);
  assert.doesNotMatch(p, /Tipo de proyecto: 1\b/);
});

test('con la necesidad ya dicha, el gancho del rubro no va al prompt', () => {
  const p = construirRedaccion(SOPRANOS, 'oferta_con_horarios');
  assert.doesNotMatch(p, /sistema de pedidos/i);
  assert.doesNotMatch(p, /Gancho útil/);
});

test('con el texto libre de lo que busca, tampoco va el gancho', () => {
  const p = construirRedaccion(
    { ...SOPRANOS, business_type: null, needs: 'pagina web' }, 'oferta_con_horarios',
  );
  assert.doesNotMatch(p, /Gancho útil/);
});

test('con el formulario de Meta como unica pista, tampoco va el gancho', () => {
  const p = construirRedaccion(
    { ...SOPRANOS, business_type: null, necesidad: 'Crear mi página web' }, 'oferta_con_horarios',
  );
  assert.doesNotMatch(p, /Gancho útil/);
});

test('el gancho SI va cuando todavia no dijo que necesita', () => {
  for (const business_type of [null, 6]) {
    const p = construirRedaccion({ ...SOPRANOS, business_type }, 'oferta_con_horarios');
    assert.match(p, /Gancho útil para su rubro: .*sistema de pedidos online/, `business_type=${business_type}`);
  }
});

test('el prompt cierra que hable de lo que pidio y no de otra cosa', () => {
  const p = construirRedaccion(SOPRANOS, 'oferta_con_horarios');
  assert.match(p, /hablale de eso/i);
});

// ── el control sobre la salida ──────────────────────────────────────────────

const PITCH_DEL_22_9 = 'Vi que recibís muchos pedidos por WhatsApp. En la videollamada de 30 minutos '
  + 'el equipo te arma un prototipo de cómo quedaría tu sistema de pedidos online.';

test('cambiaLaNecesidad: un "sistema de pedidos" a quien pidió página web es cambiarle lo que pidió', () => {
  assert.equal(cambiaLaNecesidad('', PITCH_DEL_22_9, 1), true, 'sin texto libre, solo el tipo guardado');
  assert.equal(cambiaLaNecesidad('pagina web', PITCH_DEL_22_9, 1), true);
  assert.equal(cambiaLaNecesidad('pagina web', PITCH_DEL_22_9), true, 'y sin tipo, por el texto libre');
});

test('cambiaLaNecesidad: hablar de la página web está bien, y sin necesidad dicha no hay nada que cambiar', () => {
  const bien = 'Con la videollamada el equipo te arma un prototipo de tu página web.';
  assert.equal(cambiaLaNecesidad('', bien, 1), false);
  assert.equal(cambiaLaNecesidad('', PITCH_DEL_22_9, null), false, 'no dijo nada: no hay contra que comparar');
  assert.equal(cambiaLaNecesidad('', PITCH_DEL_22_9, 6), false, 'todavia no sabe');
});

test('cambiaLaNecesidad: quien pidió un e-commerce o un sistema puede oír de pedidos online', () => {
  assert.equal(cambiaLaNecesidad('', PITCH_DEL_22_9, 2), false);
  assert.equal(cambiaLaNecesidad('', PITCH_DEL_22_9, 4), false);
  assert.equal(cambiaLaNecesidad('tienda online', PITCH_DEL_22_9, 1), false, 'el texto libre dice tienda: manda ese');
});

/**
 * De punta a punta por el redactor: el modelo repite el pitch del 22-9, el
 * codigo lo detecta por el tipo guardado, se lo pide de nuevo diciendole que
 * el lead pidio una pagina web, y sale el segundo.
 */
test('el redactor no deja salir un pitch que se despega de la necesidad guardada', async () => {
  const prompts = [];
  const respuestas = [PITCH_DEL_22_9, 'El equipo te prepara un prototipo de tu página web en la videollamada de 30 minutos.'];
  const modelo = {
    activo: true,
    async pedir({ mensajes }) {
      prompts.push(mensajes[0].content);
      return { texto: respuestas[prompts.length - 1] };
    },
  };
  const redactor = crearRedactor({ modelo });

  const texto = await redactor.escribir(SOPRANOS, 'oferta_con_horarios');

  assert.equal(prompts.length, 2, 'se le pidió de nuevo');
  assert.match(prompts[1], /OJO: el lead pidió página web/i);
  assert.match(texto, /página web/);
  assert.doesNotMatch(texto, /sistema de pedidos/);
});

test('el redactor deja pasar un pitch que sí habla de lo pedido, sin pedirlo de nuevo', async () => {
  let llamadas = 0;
  const modelo = {
    activo: true,
    async pedir() { llamadas++; return { texto: 'Te armamos un prototipo de tu página web en la videollamada.' }; },
  };
  const texto = await crearRedactor({ modelo }).escribir(SOPRANOS, 'oferta_con_horarios');
  assert.equal(llamadas, 1);
  assert.match(texto, /página web/);
});
