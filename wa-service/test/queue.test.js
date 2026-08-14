'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { montar, conLead } = require('./helpers');

const AM = '59899000111';
const LEAD_TEL = '59899123456';

test('50 leads de golpe se serializan: nunca hay dos envios simultaneos', async () => {
  const s = await montar();
  let enVuelo = 0;
  let maxEnVuelo = 0;

  const original = s.proveedor.enviarTexto;
  s.proveedor.enviarTexto = async (to, texto) => {
    enVuelo += 1;
    maxEnVuelo = Math.max(maxEnVuelo, enVuelo);
    await new Promise((r) => setTimeout(r, 1));
    enVuelo -= 1;
    return original.call(s.proveedor, to, texto);
  };

  for (let i = 0; i < 50; i++) {
    await s.servicioLeads.alta({
      external_id: `masivo_${i}`, nombre: `Lead ${i}`, rubro: 'salud',
      telefono: `09912${String(i).padStart(4, '0')}`, origen: 'form',
    });
  }
  await s.cola.vacia();

  assert.equal(maxEnVuelo, 1, 'un solo worker');
  assert.equal(s.proveedor.getEnviados().length, 100, '50 fichas al AM + 50 bienvenidas');
});

test('fuera de horario el mensaje se reprograma, no se pierde', async () => {
  // Domingo 03:00 en Montevideo, con horario lun-sab 09:00-19:00.
  const domingo = new Date('2026-08-09T06:00:00Z');
  const s = await montar({ BUSINESS_HOURS: '09:00-19:00', BUSINESS_DAYS: 'mon-sat' }, domingo);

  s.cola.encolar({ to: LEAD_TEL, texto: 'hola de madrugada', kind: 'welcome' });
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().length, 0, 'no se manda nada');
  assert.equal(s.cola.pendientes(), 1, 'sigue en la cola');
  assert.equal(s.cola.reprogramados(), 1, 'marcado como reprogramado');
});

test('el aviso al AM si sale de madrugada: es contacto interno', async () => {
  const domingo = new Date('2026-08-09T06:00:00Z');
  const s = await montar({ BUSINESS_HOURS: '09:00-19:00', BUSINESS_DAYS: 'mon-sat' }, domingo);

  s.cola.encolar({ to: AM, texto: 'entro un lead', kind: 'am_notice' });
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().length, 1);
  assert.equal(s.proveedor.getEnviados()[0].to, AM);
});

test('un lead que entra de madrugada igual le llega al AM, y el lead espera', async () => {
  const domingo = new Date('2026-08-09T06:00:00Z');
  const s = await montar({ BUSINESS_HOURS: '09:00-19:00', BUSINESS_DAYS: 'mon-sat' }, domingo);

  await s.servicioLeads.alta({
    external_id: 'nocturno', nombre: 'Ana', rubro: 'salud',
    telefono: '099555444', origen: 'form',
  });
  await s.cola.vacia();

  const enviados = s.proveedor.getEnviados();
  assert.equal(enviados.length, 1, 'solo la ficha al AM');
  assert.equal(enviados[0].to, AM);
  assert.equal(s.cola.reprogramados(), 1, 'la bienvenida queda para el lunes');
});

test('el circuit breaker pausa la cola tras fallos seguidos', async () => {
  const s = await conLead({ CIRCUIT_BREAKER_FAILS: '3', CIRCUIT_BREAKER_PAUSE_MIN: '30' });

  s.proveedor.enviarTexto = async () => { throw new Error('conexion caida'); };

  for (let i = 0; i < 4; i++) {
    s.cola.encolar({ to: LEAD_TEL, texto: `intento ${i}`, kind: 'manual' });
  }
  await s.cola.vacia();

  assert.equal(s.cola.pausada(), true, 'la cola queda pausada');

  const fallidos = s.repo.db
    .prepare("SELECT COUNT(*) AS n FROM messages WHERE status = 'failed'").get().n;
  assert.ok(fallidos >= 3, `quedan registrados los fallos (${fallidos})`);
});

test('la alerta del breaker le llega al AM aunque la cola quede pausada', async () => {
  // El aviso se encola en la misma cola que se acaba de pausar: si los internos
  // no pasaran, nadie se enteraria de que el canal se cayo.
  const s = await conLead({ CIRCUIT_BREAKER_FAILS: '3' });

  let permitirAM = true;
  const original = s.proveedor.enviarTexto.bind(s.proveedor);
  s.proveedor.enviarTexto = async (to, texto) => {
    if (to === AM && permitirAM) return original(to, texto);
    throw new Error('conexion caida');
  };

  for (let i = 0; i < 3; i++) {
    s.cola.encolar({ to: LEAD_TEL, texto: `intento ${i}`, kind: 'manual' });
  }
  await s.cola.vacia();

  assert.equal(s.cola.pausada(), true);
  const alAM = s.proveedor.getEnviados().filter((e) => e.to === AM);
  assert.equal(alAM.length, 1, 'el AM recibe la alerta');
  assert.match(alAM[0].texto, /se pausó por fallos repetidos/);
  assert.equal(permitirAM, true);
});

test('un envio fallido no frena a los demas', async () => {
  const s = await conLead();
  let llamadas = 0;

  const original = s.proveedor.enviarTexto.bind(s.proveedor);
  s.proveedor.enviarTexto = async (to, texto) => {
    llamadas += 1;
    if (llamadas === 1) throw new Error('fallo puntual');
    return original(to, texto);
  };

  s.cola.encolar({ to: LEAD_TEL, texto: 'primero', kind: 'manual' });
  s.cola.encolar({ to: LEAD_TEL, texto: 'segundo', kind: 'manual' });
  s.cola.encolar({ to: LEAD_TEL, texto: 'tercero', kind: 'manual' });
  await s.cola.vacia();

  const textos = s.proveedor.getEnviados().map((e) => e.texto);
  assert.ok(textos.includes('segundo') && textos.includes('tercero'));

  const fallido = s.repo.db
    .prepare("SELECT body FROM messages WHERE status = 'failed'").get();
  assert.equal(fallido.body, 'primero');
});

test('la prioridad manda: el aviso al AM se adelanta al follow-up', async () => {
  const s = await conLead();

  s.cola.encolar({ to: LEAD_TEL, texto: 'follow-up', kind: 'followup' });
  s.cola.encolar({ to: AM, texto: 'ficha', kind: 'am_notice' });
  await s.cola.vacia();

  const enviados = s.proveedor.getEnviados();
  assert.equal(enviados[0].texto, 'ficha');
  assert.equal(enviados[1].texto, 'follow-up');
});

test('cada mensaje queda registrado con su estado final', async () => {
  const s = await conLead();
  s.cola.encolar({ to: LEAD_TEL, texto: 'hola', kind: 'manual' });
  await s.cola.vacia();

  const m = s.repo.db
    .prepare("SELECT status, provider, provider_msg_id FROM messages WHERE body = 'hola'").get();
  assert.equal(m.status, 'sent');
  assert.equal(m.provider, 'mock');
  assert.ok(m.provider_msg_id);
});

test('el acuse de entrega solo avanza, nunca retrocede', async () => {
  // "sent" solo dice que el proveedor lo acepto. Si un acuse de entrega llega
  // tarde no puede pisar un "leido" ya registrado.
  const s = await conLead();
  s.cola.encolar({ to: LEAD_TEL, texto: 'hola', kind: 'manual' });
  await s.cola.vacia();

  const m = s.repo.db.prepare("SELECT provider_msg_id AS id FROM messages WHERE body='hola'").get();
  const estado = () =>
    s.repo.db.prepare('SELECT status FROM messages WHERE provider_msg_id = ?').get(m.id).status;

  assert.equal(estado(), 'sent');

  assert.equal(s.repo.marcarEntrega(m.id, 'delivered'), true);
  assert.equal(estado(), 'delivered');

  assert.equal(s.repo.marcarEntrega(m.id, 'read'), true);
  assert.equal(estado(), 'read');

  assert.equal(s.repo.marcarEntrega(m.id, 'delivered'), false, 'no retrocede');
  assert.equal(estado(), 'read');

  assert.equal(s.repo.marcarEntrega('no-existe', 'read'), false);
});

test('/health cuenta los salientes sin confirmar', async () => {
  const s = await conLead();
  s.cola.encolar({ to: LEAD_TEL, texto: 'sin acuse', kind: 'manual' });
  await s.cola.vacia();

  // Con created_at de ahora, todavia no entra en la ventana de 5 minutos.
  const enElFuturo = new Date(Date.now() + 10 * 60_000).toISOString().replace('T', ' ').slice(0, 19);
  assert.ok(s.repo.sinConfirmar(enElFuturo) >= 1, 'aparece como no confirmado');

  const m = s.repo.db.prepare("SELECT provider_msg_id AS id FROM messages WHERE body='sin acuse'").get();
  s.repo.marcarEntrega(m.id, 'delivered');
  assert.equal(
    s.repo.db.prepare("SELECT COUNT(*) n FROM messages WHERE body='sin acuse' AND status='sent'").get().n,
    0
  );
});

test('se puede recuperar el cuerpo de un saliente por su id del proveedor', async () => {
  // Lo necesita baileys para reenviar cuando el dispositivo del destinatario no
  // pudo descifrar y pide el reintento. Sin esto el mensaje queda en
  // "Esperando este mensaje" para siempre en ese dispositivo.
  const s = await conLead();
  s.cola.encolar({ to: LEAD_TEL, texto: 'texto a reenviar', kind: 'manual' });
  await s.cola.vacia();

  const m = s.repo.db
    .prepare("SELECT provider_msg_id AS id FROM messages WHERE body='texto a reenviar'").get();
  assert.equal(s.repo.cuerpoPorProviderId(m.id), 'texto a reenviar');
  assert.equal(s.repo.cuerpoPorProviderId('no-existe'), null);
});
