'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { sanearDatos } = require('../src/ia/agente');
const { prometeAgendar } = require('../src/ia/promesas');
const { CAMPOS_FUNNEL } = require('../src/db/repo');

// ── el dato respaldado por el valor, no solo por la cita ─────────────────────

/**
 * Medido el 10-9: el modelo cita bien el contenido y parafrasea la forma. El
 * lead escribe "para mi barbería acá en Montevideo" y la cita dice "tengo una
 * barbería acá en Montevideo". El rubro era correcto y se tiraba, y el bot
 * volvia a preguntar lo que ya le habian dicho.
 */
test('un dato con la cita parafraseada se guarda si el valor esta en el mensaje', () => {
  const limpio = sanearDatos({
    rubro: 'barbería',
    rubro_dicho: 'tengo una barbería acá en Montevideo',
  }, { entrante: 'necesito una página web para mi barbería acá en Montevideo' });

  assert.deepEqual(limpio, { rubro: 'barbería' });
});

/**
 * "nada" aparece de verdad en cualquier conversacion ("no, nada que ver"), asi
 * que ni una cita perfecta lo convierte en un rubro.
 */
test('el relleno no se guarda aunque venga con cita perfecta', () => {
  for (const relleno of ['nada', 'no sabe', 'ninguno', 'n/a']) {
    const limpio = sanearDatos({ rubro: relleno, rubro_dicho: relleno }, { entrante: `mmm ${relleno} la verdad` });
    assert.deepEqual(limpio, {}, relleno);
  }
});

/** Una o dos letras aparecen por casualidad en cualquier mensaje. */
test('un valor muy corto no pasa por el valor: necesita cita', () => {
  const limpio = sanearDatos({ business_name: 'La', business_name_dicho: 'otra cosa' }, { entrante: 'la verdad no sé' });
  assert.deepEqual(limpio, {});
});

test('el valor se busca como palabra entera', () => {
  const limpio = sanearDatos({ rubro: 'pan', rubro_dicho: 'algo que no dijo' }, { entrante: 'tengo una panadería' });
  assert.deepEqual(limpio, {});
});

/**
 * La lista de campos es una sola. El arnes de evals armaba la suya con las tres
 * preguntas de descubrimiento y rechazaba columnas que el bot guarda todos los
 * dias, como si el modelo las hubiera inventado.
 */
test('la lista de campos del embudo incluye los que no son de descubrimiento', () => {
  for (const campo of ['team_size', 'budget', 'instagram_web', 'needs', 'bot_pausado_hasta', 'dia_en_foco']) {
    assert.ok(CAMPOS_FUNNEL.includes(campo), campo);
  }
  assert.ok(!CAMPOS_FUNNEL.includes('telefono'), 'el telefono no lo escribe el embudo');
});

// ── prometer un link que no se manda ─────────────────────────────────────────

/**
 * Costo un lead el 13-9. Susana, salon de belleza, ya habia dicho cuando podia
 * y recibio esto. No habia ningun link y nunca salio ninguno.
 */
test('agarra la promesa de link de la conversacion con Susana', () => {
  const texto = 'Los horarios los maneja el sistema y se ven directamente cuando agendás. '
    + 'Entrá al link que te voy a pasar en un momento y elegí el que te venga bien.';
  assert.equal(prometeAgendar(texto), 'prometio un link que no mando');
});

test('y las otras formas de prometer el link', () => {
  for (const texto of [
    'Ahora te paso el link para que elijas',
    'Te voy a mandar el enlace por acá',
    'En un rato te llega el link',
    'Te comparto un link en breve',
  ]) {
    assert.equal(prometeAgendar(texto), 'prometio un link que no mando', texto);
  }
});

/** El bot no puede mandar "otro mensaje despues": o el link va en este, o no hay. */
test('pasar el link en el mismo mensaje esta bien', () => {
  assert.notEqual(
    prometeAgendar('Te paso el link para elegir horario: https://calendly.com/scalerics/consultoriagratuita'),
    'prometio un link que no mando'
  );
});
