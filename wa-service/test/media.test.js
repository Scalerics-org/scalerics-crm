'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const { crearMedia } = require('../src/media');

function dirTemporal() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'wa-media-'));
}

test('guarda el archivo y devuelve con que nombre quedo', () => {
  const dir = dirTemporal();
  const m = crearMedia({ dir });

  const nombre = m.guardar(Buffer.from('ogg-falso'), 'ogg');

  assert.match(nombre, /\.ogg$/);
  assert.equal(fs.readFileSync(path.join(dir, nombre), 'utf8'), 'ogg-falso');
});

test('dos audios iguales no se pisan', () => {
  const m = crearMedia({ dir: dirTemporal() });
  assert.notEqual(m.guardar(Buffer.from('x'), 'ogg'), m.guardar(Buffer.from('x'), 'ogg'));
});

test('leer devuelve el contenido', () => {
  const m = crearMedia({ dir: dirTemporal() });
  const nombre = m.guardar(Buffer.from('hola'), 'ogg');
  assert.equal(m.leer(nombre).toString(), 'hola');
});

/**
 * El nombre llega por la URL, asi que lo elige quien pide. Sin esto, un
 * "../../data/wa.db" saca la base entera por un endpoint pensado para audios.
 */
test('un nombre con ruta adentro no sale del directorio', () => {
  const m = crearMedia({ dir: dirTemporal() });
  for (const malo of ['../wa.db', '../../etc/passwd', 'sub/dir.ogg', '/etc/passwd']) {
    assert.equal(m.leer(malo), null, malo);
  }
});

test('leer algo que no existe devuelve null, no explota', () => {
  const m = crearMedia({ dir: dirTemporal() });
  assert.equal(m.leer('no-existe.ogg'), null);
});

/**
 * La transcripcion queda para siempre; el audio no. Guardar voz de gente sin
 * necesidad no aporta nada y el volumen no es infinito.
 */
test('limpiar borra los viejos y deja los nuevos', () => {
  const dir = dirTemporal();
  const m = crearMedia({ dir, diasRetencion: 90 });

  const viejo = m.guardar(Buffer.from('viejo'), 'ogg');
  const nuevo = m.guardar(Buffer.from('nuevo'), 'ogg');
  const hace100dias = new Date(Date.now() - 100 * 24 * 3600_000);
  fs.utimesSync(path.join(dir, viejo), hace100dias, hace100dias);

  assert.equal(m.limpiar(), 1, 'borro uno');
  assert.equal(m.leer(viejo), null);
  assert.equal(m.leer(nuevo).toString(), 'nuevo');
});

test('el tipo de contenido sale de la extension', () => {
  const m = crearMedia({ dir: dirTemporal() });
  assert.equal(m.contentType('nota.ogg'), 'audio/ogg');
  assert.equal(m.contentType('foto.jpg'), 'image/jpeg');
  assert.equal(m.contentType('raro.xyz'), 'application/octet-stream');
});

// ── de punta a punta: del audio a la fila del mensaje ────────────────────────

const { conLead, stubModelo, stubTranscriptor } = require('./helpers');

const TEL = '59899123456';

test('la nota de voz queda guardada y colgada del mensaje transcripto', async () => {
  const s = await conLead({
    modelo: stubModelo(),
    openai: stubTranscriptor({ respuestas: { transcripcion: 'tengo una panadería' } }),
  });

  await s.proveedor.simularSinTexto({
    from: TEL, tipo: 'audio', segundos: 5,
    descargar: async () => Buffer.from('ogg-de-prueba'),
  });
  await s.agrupador.vaciar();
  await s.cola.vacia();

  const lead = s.repo.leadPorTelefono(TEL);
  const entrante = s.repo.mensajesDeLead(lead.id).filter((m) => m.direction === 'in').at(-1);

  assert.equal(entrante.body, 'tengo una panadería', 'el texto sigue siendo la transcripcion');
  const medios = JSON.parse(entrante.media);
  assert.equal(medios.length, 1);
  assert.equal(medios[0].tipo, 'audio');
  assert.equal(s.media.leer(medios[0].archivo).toString(), 'ogg-de-prueba',
    'y el original se puede escuchar');
});
