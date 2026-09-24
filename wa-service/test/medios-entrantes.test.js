'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { extensionDe, crearMedia } = require('../src/media');
const { medioDeMensaje } = require('../src/providers/baileys');
const { ADMIN, montar, conLead, stubModelo } = require('./helpers');

const TEL = '59899123456';
const NUEVO = '59899555001';

const comoElCrm = (s, method, url, payload) =>
  s.app.inject({ method, url, headers: { 'x-admin-token': ADMIN }, payload });

const entrantes = (s, tel) => {
  const l = s.repo.leadPorTelefono(tel);
  return s.repo.mensajesDeLead(l.id).filter((m) => m.direction === 'in');
};

// ── con que extension se guarda cada cosa ────────────────────────────────────

test('cada tipo de medio de WhatsApp se guarda con su extension', () => {
  assert.equal(extensionDe('audio'), 'ogg');
  assert.equal(extensionDe('imagen'), 'jpg');
  assert.equal(extensionDe('sticker'), 'webp');
  assert.equal(extensionDe('video'), 'mp4');
});

/**
 * Un PDF y una planilla llegan los dos como documentMessage. Lo unico que dice
 * la verdad es el nombre con el que lo mandaron.
 */
test('un documento conserva la extension del nombre original', () => {
  assert.equal(extensionDe('documento', 'Presupuesto.PDF'), 'pdf');
  assert.equal(extensionDe('documento', 'ventas-agosto.xlsx'), 'xlsx');
});

/**
 * La extension termina en un nombre de archivo en disco y la elige quien manda
 * el mensaje. Nada raro puede pasar de ahi.
 */
test('un nombre sin extension o con una extension rara queda como bin', () => {
  assert.equal(extensionDe('documento', 'sin-extension'), 'bin');
  assert.equal(extensionDe('documento', ''), 'bin');
  assert.equal(extensionDe('documento', 'x.<script>'), 'bin');
  assert.equal(extensionDe('documento', 'largo.extensionlarguisima'), 'bin');
  assert.equal(extensionDe('ubicacion'), 'bin');
});

test('los stickers y los PDF se sirven con su tipo, no como descarga', () => {
  const m = crearMedia({ dir: require('os').tmpdir() });
  assert.equal(m.contentType('s.webp'), 'image/webp');
  assert.equal(m.contentType('d.pdf'), 'application/pdf');
  assert.equal(m.contentType('v.mp4'), 'video/mp4');
});

// ── que medio trae un mensaje de Baileys ─────────────────────────────────────

/**
 * Una foto con epigrafe es UN mensaje de WhatsApp: la imagen y el texto juntos.
 * Antes solo se miraba el medio cuando el mensaje venia mudo, y esta foto se
 * perdia en silencio.
 */
test('el medio se detecta aunque el mensaje traiga texto', () => {
  const msg = { message: { imageMessage: { caption: 'mirá cómo quedó' } } };
  assert.deepEqual(medioDeMensaje(msg), { tipo: 'imagen', nombreArchivo: '', segundos: 0 });
});

test('del documento sale el nombre y del audio la duracion', () => {
  assert.equal(
    medioDeMensaje({ message: { documentMessage: { fileName: 'presupuesto.pdf' } } }).nombreArchivo,
    'presupuesto.pdf'
  );
  assert.equal(medioDeMensaje({ message: { audioMessage: { seconds: 12 } } }).segundos, 12);
});

test('un texto solo no trae medio', () => {
  assert.equal(medioDeMensaje({ message: { conversation: 'hola' } }), null);
  assert.equal(medioDeMensaje({}), null);
});

// ── de punta a punta ─────────────────────────────────────────────────────────

/**
 * Antes la foto sin texto se descartaba entera: el lead veia el tilde azul y en
 * el panel del CRM no habia nada que mirar.
 */
test('una foto sin texto queda en la conversacion, con el archivo', async () => {
  const s = await montar();

  await s.proveedor.simularSinTexto({
    from: NUEVO, tipo: 'imagen', id: 'wamid.foto1', nombre: 'Natalia',
    descargar: async () => Buffer.from('jpg-de-prueba'),
  });
  await s.cola.vacia();

  const [foto] = entrantes(s, NUEVO);
  assert.ok(foto, 'el numero nuevo quedo como lead y el mensaje existe');
  assert.equal(foto.body, null, 'sin texto inventado');
  const medios = JSON.parse(foto.media);
  assert.equal(medios[0].tipo, 'imagen');
  assert.match(medios[0].archivo, /\.jpg$/);
  assert.equal(s.media.leer(medios[0].archivo).toString(), 'jpg-de-prueba');
});

test('al redactor se le dice que fue una foto, no "un archivo"', async () => {
  const modelo = stubModelo();
  const s = await conLead({ modelo });

  await s.proveedor.simularSinTexto({
    from: TEL, tipo: 'imagen', id: 'wamid.foto2', descargar: async () => Buffer.from('jpg'),
  });
  await s.cola.vacia();

  const prompts = modelo.llamadas.map((l) => l.mensajes?.[0]?.content || '');
  const aviso = prompts.find((p) => p.includes('sin_texto_archivo'));
  assert.ok(aviso, 'se pidio el aviso de entrante sin texto');
  assert.match(aviso, /Lo que te mandó fue una foto\./);
});

test('una foto con epigrafe guarda el texto y la foto en la misma fila', async () => {
  const s = await conLead();

  await s.proveedor.simularEntrante({
    from: TEL, texto: 'mirá cómo quedó el local', id: 'wamid.foto3', nombre: '',
    tipo: 'imagen', nombreArchivo: '', segundos: 0,
    descargar: async () => Buffer.from('jpg-con-epigrafe'),
  });
  await s.agrupador.vaciar();
  await s.cola.vacia();

  const ultimo = entrantes(s, TEL).at(-1);
  assert.equal(ultimo.body, 'mirá cómo quedó el local');
  const medios = JSON.parse(ultimo.media);
  assert.equal(medios.length, 1);
  assert.equal(s.media.leer(medios[0].archivo).toString(), 'jpg-con-epigrafe');
});

/**
 * Con una persona atendiendo el bot se calla —un sticker no lo despierta— pero
 * el archivo tiene que quedar: el que atiende necesita verlo.
 */
test('con el bot pausado la foto se guarda y el bot no contesta', async () => {
  const s = await conLead();
  const l = s.repo.leadPorTelefono(TEL);
  s.repo.actualizarFunnel(l.id, { bot_pausado_hasta: new Date(Date.now() + 3600_000).toISOString() });
  s.proveedor.limpiar();

  await s.proveedor.simularSinTexto({
    from: TEL, tipo: 'imagen', id: 'wamid.foto4', descargar: async () => Buffer.from('jpg'),
  });
  await s.cola.vacia();

  assert.ok(JSON.parse(entrantes(s, TEL).at(-1).media)[0].archivo, 'el archivo quedo');
  assert.equal(s.proveedor.getEnviados().filter((e) => e.to === TEL).length, 0, 'al lead no le llega nada');
});

/**
 * El volumen es de 1GB y ahi vive tambien la base. Pasado el tope el mensaje
 * se registra igual, sin archivo, y el panel lo sabe porque la url viene null.
 */
test('un medio mas grande que el tope se registra sin archivo', async () => {
  const s = await conLead({ MEDIA_MAX_MB: '0.001' });

  await s.proveedor.simularSinTexto({
    from: TEL, tipo: 'video', id: 'wamid.video1', descargar: async () => Buffer.alloc(4096, 1),
  });
  await s.cola.vacia();

  const medios = JSON.parse(entrantes(s, TEL).at(-1).media);
  assert.equal(medios[0].tipo, 'video');
  assert.equal(medios[0].archivo, null);

  const conv = await comoElCrm(s, 'GET', `/api/leads/phone/${TEL}`);
  const conVideo = conv.json().messages.find((m) => m.media && m.media.length);
  assert.equal(conVideo.media[0].url, null, 'el panel no pide un archivo que no existe');
});

test('si la descarga falla, el mensaje igual queda', async () => {
  const s = await conLead();

  await s.proveedor.simularSinTexto({
    from: TEL, tipo: 'imagen', id: 'wamid.foto5',
    descargar: async () => { throw new Error('media expirada'); },
  });
  await s.cola.vacia();

  const medios = JSON.parse(entrantes(s, TEL).at(-1).media);
  assert.equal(medios[0].tipo, 'imagen');
  assert.equal(medios[0].archivo, null);
});

test('el panel recibe el nombre del documento', async () => {
  const s = await conLead();

  await s.proveedor.simularSinTexto({
    from: TEL, tipo: 'documento', nombreArchivo: 'presupuesto.pdf', id: 'wamid.doc1',
    descargar: async () => Buffer.from('%PDF-falso'),
  });
  await s.cola.vacia();

  const conv = await comoElCrm(s, 'GET', `/api/leads/phone/${TEL}`);
  const doc = conv.json().messages.find((m) => m.media && m.media.length).media[0];
  assert.equal(doc.nombre, 'presupuesto.pdf');
  assert.ok(doc.url, 'y se puede bajar');
  const r = await comoElCrm(s, 'GET', doc.url);
  assert.equal(r.headers['content-type'], 'application/pdf');
});

/**
 * Conversacion real, 14-9: Natalia escribio al numero por primera vez a las
 * 20:18, con dos fotos y nada de texto. No recibio ninguna respuesta.
 *
 * El aviso "contame por escrito" sale como respuesta solo si el lead escribio
 * hace poco, y eso se pregunta por su leadId. Pero el handler busca el lead
 * ANTES de darlo de alta, asi que para un numero nuevo el aviso se encola con
 * leadId null: no cuenta como respuesta, choca con el horario, y como vence en
 * una hora la cola lo tira en vez de mandarlo a la mañana.
 *
 * Queda como pendiente, no como falla: es un bug del codigo que corre en
 * produccion (v100) y este commit solo le pone tests a ese codigo.
 */
test('a un numero nuevo que manda solo una foto fuera de horario se le contesta', {
  todo: 'bug en v100: el aviso se encola con leadId null y se descarta fuera de horario',
}, async () => {
  const lunesNoche = new Date('2026-09-14T23:18:00Z'); // 20:18 en Montevideo
  const s = await montar({ BUSINESS_HOURS: '09:00-19:00', BUSINESS_DAYS: 'mon-sat' }, lunesNoche);

  await s.proveedor.simularSinTexto({
    from: NUEVO, tipo: 'imagen', id: 'wamid.natalia1', nombre: 'Natalia',
    descargar: async () => Buffer.from('jpg'),
  });
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().filter((e) => e.to === NUEVO).length, 1);
});
