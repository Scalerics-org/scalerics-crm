'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { montar, conLead } = require('./helpers');

const AM = '59899000111';
const LEAD_TEL = '59899123456';

/**
 * Lo que se prueba aca es la serializacion: un solo worker, nunca dos envios a
 * la vez. Los limites se suben para que no se metan en el medio.
 *
 * Antes no hacia falta subirlos, pero por la peor razon: los contadores
 * comparaban una fecha ISO contra el formato de SQLite y daban cero siempre, o
 * sea que el limite por hora no frenaba nada. Al arreglarlo, este test empezo a
 * chocar contra un tope de 30 —50 leads son 100 mensajes— y quedaba en rojo.
 * Que hayan tenido que subirse es la senial de que ahora los limites existen.
 */
test('50 leads de golpe se serializan: nunca hay dos envios simultaneos', async () => {
  const s = await montar({
    MAX_MSGS_PER_HOUR: 1000, MAX_MSGS_PER_DAY: 1000, MAX_NEW_CONTACTS_PER_HOUR: 1000,
  });
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

/**
 * El contrapeso del anterior: que el tope por hora frene DE VERDAD.
 *
 * Este guardarrail estuvo muerto sin que nadie se enterara, porque el contador
 * comparaba formatos de fecha distintos y devolvia cero. limits.test.js no lo
 * vio: prueba la logica contra un repo de mentira, y la logica estaba bien. El
 * que mentia era el SQL. Por eso este test va contra la cola y la base reales.
 */
test('el tope por hora frena, y lo que no sale se reprograma en vez de perderse', async () => {
  const s = await montar({ MAX_MSGS_PER_HOUR: 3, MAX_NEW_CONTACTS_PER_HOUR: 100 });

  for (let i = 0; i < 6; i++) {
    s.cola.encolar({ to: `5989900${String(i).padStart(4, '0')}`, texto: `hola ${i}`, kind: 'welcome' });
  }
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().length, 3, 'para al llegar al tope');
  assert.equal(s.cola.reprogramados(), 3, 'los otros esperan turno, no se descartan');
  assert.ok(s.cola.tieneDespertador(), 'y alguien los va a despertar');
});

/**
 * "Reprogramado" tiene que significar que sale despues, no que no sale.
 *
 * El loop corta cuando no queda nada que pueda salir AHORA. Un mensaje frenado
 * se queda en la lista con `noAntesDe` en el futuro, y sin un reintento armado
 * nadie lo vuelve a mirar: salia recien cuando alguien encolaba otra cosa, y si
 * no entraba ningun mensaje mas se quedaba ahi para siempre.
 *
 * Paso desapercibido porque el tope por hora no frenaba nada —contaba cero por
 * comparar dos formatos de fecha—, asi que casi nunca habia algo reprogramado.
 */
test('un mensaje frenado por el horario queda con reintento armado', async () => {
  const domingo = new Date('2026-08-09T06:00:00Z');
  const s = await montar({ BUSINESS_HOURS: '09:00-19:00', BUSINESS_DAYS: 'mon-sat' }, domingo);

  s.cola.encolar({ to: LEAD_TEL, texto: 'hola de madrugada', kind: 'welcome' });
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().length, 0);
  assert.equal(s.cola.pendientes(), 1, 'sigue en la cola');
  assert.ok(s.cola.tieneDespertador(), 'y no quedo dormida esperando que alguien encole otra cosa');
});

/**
 * La ultima ventana por donde se escapaba una respuesta vieja.
 *
 * descartarPendientesDe filtra `items`, pero el mensaje que el worker ya agarro
 * salio de esa lista: esta en el "escribiendo...". Con las esperas largas casi
 * no se notaba, porque un mensaje pasaba mucho tiempo en la cola antes de que
 * lo tomaran. Con las esperas cortas el worker lo agarra al instante, y esa
 * ventana pasa a ser la unica que importa.
 *
 * Es lo que permite que la espera de fragmentos baje a un segundo y medio: una
 * tanda partida ya no cuesta dos respuestas, cuesta una llamada de mas.
 */
test('una respuesta ya en el "escribiendo..." se cancela si el lead vuelve a escribir', async () => {
  const s = await montar({ TYPING_ENABLED: 'true', TYPING_TECHO_MS: '50' });
  const l = s.repo.crearLead({ nombre: 'Juanchi', telefono: LEAD_TEL, origen: 'wa' });

  let avisarQueEmpezo;
  const empezoATipear = new Promise((r) => { avisarQueEmpezo = r; });
  let dejarSeguir;
  const puedeSeguir = new Promise((r) => { dejarSeguir = r; });

  s.proveedor.setPresencia = async (to, estado) => {
    if (estado !== 'composing') return;
    avisarQueEmpezo();
    await puedeSeguir;
  };

  s.cola.encolar({ to: LEAD_TEL, texto: '¿cómo se llama tu negocio?', kind: 'manual', leadId: l.id });
  await empezoATipear;

  // Justo acá el lead contesta: lo que se está por mandar quedó viejo.
  s.cola.descartarPendientesDe(l.id);
  dejarSeguir();
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().length, 0, 'la pregunta vieja no llega');
});

test('pero un aviso al equipo en curso NO se cancela', async () => {
  const s = await montar({ TYPING_ENABLED: 'true', TYPING_TECHO_MS: '50', TYPING_INTERNO: 'true' });
  const l = s.repo.crearLead({ nombre: 'Juanchi', telefono: LEAD_TEL, origen: 'wa' });

  let avisarQueEmpezo;
  const empezoATipear = new Promise((r) => { avisarQueEmpezo = r; });
  let dejarSeguir;
  const puedeSeguir = new Promise((r) => { dejarSeguir = r; });

  s.proveedor.setPresencia = async (to, estado) => {
    if (estado !== 'composing') return;
    avisarQueEmpezo();
    await puedeSeguir;
  };

  s.cola.encolar({ to: AM, texto: 'nuevo contacto', kind: 'am_notice', leadId: l.id });
  await empezoATipear;

  s.cola.descartarPendientesDe(l.id);
  dejarSeguir();
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().length, 1, 'que el lead escriba no invalida el aviso');
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

// ── respuestas que quedaron viejas ───────────────────────────────────────────

test('si el lead escribe de nuevo, la respuesta vieja no sale', async () => {
  // El bug que desordenaba conversaciones enteras: entre dos mensajes pasan de
  // 12 a 45 segundos, y un lead que contesta rapido escribe antes de que salga
  // la respuesta anterior. Llegaba "¿cómo se llama tu negocio?" DESPUES de que
  // ya lo habia dicho.
  const s = await montar({ DELAY_BETWEEN_MIN_MS: '99999', DELAY_BETWEEN_MAX_MS: '99999' });
  s.cola.encolar({ to: '598991', texto: 'pregunta vieja', kind: 'manual', leadId: 7 });

  assert.equal(s.cola.descartarPendientesDe(7), 1);
});

test('la bienvenida no se descarta nunca', async () => {
  // Es la presentacion: tiene que salir aunque el lead haya escrito tres veces
  // mientras esperaba.
  const s = await montar({ DELAY_BETWEEN_MIN_MS: '99999', DELAY_BETWEEN_MAX_MS: '99999' });
  s.cola.encolar({ to: '598991', texto: 'bienvenida', kind: 'welcome', leadId: 7 });
  s.cola.encolar({ to: '598991', texto: 'respuesta', kind: 'manual', leadId: 7 });

  assert.equal(s.cola.descartarPendientesDe(7), 1, 'solo la respuesta');
});

test('los avisos al equipo sobreviven a que el lead escriba', async () => {
  const s = await montar({ DELAY_BETWEEN_MIN_MS: '99999', DELAY_BETWEEN_MAX_MS: '99999' });
  s.cola.encolar({ to: '59899000111', texto: 'lead calificado', kind: 'am_notice', leadId: 7 });
  s.cola.encolar({ to: '598991', texto: 'respuesta', kind: 'manual', leadId: 7 });

  assert.equal(s.cola.descartarPendientesDe(7), 1, 'el aviso interno queda');
});

test('no toca los pendientes de otros leads', async () => {
  const s = await montar({ DELAY_BETWEEN_MIN_MS: '99999', DELAY_BETWEEN_MAX_MS: '99999' });
  s.cola.encolar({ to: '598991', texto: 'a', kind: 'manual', leadId: 7 });
  s.cola.encolar({ to: '598992', texto: 'b', kind: 'manual', leadId: 8 });

  assert.equal(s.cola.descartarPendientesDe(7), 1);
});

test('sin leadId no descarta nada', async () => {
  const s = await montar({ DELAY_BETWEEN_MIN_MS: '99999', DELAY_BETWEEN_MAX_MS: '99999' });
  s.cola.encolar({ to: '598991', texto: 'x', kind: 'manual', leadId: null });
  assert.equal(s.cola.descartarPendientesDe(null), 0);
});

/**
 * El cupo anti-baneo esta para limitar los mensajes a desconocidos. Un aviso al
 * numero del propio equipo —un contacto guardado, con conversacion abierta hace
 * meses— es lo contrario de esa senial.
 *
 * Los avisos ya salteaban el limite pero igual se anotaban, asi que gastaban
 * cupo de los mensajes a clientes: cada conversacion consumia doble, y las que
 * se frenaban al llegar al tope eran las de los clientes.
 */
test('los avisos al equipo no gastan el cupo de los mensajes a clientes', async () => {
  const s = await montar({ MAX_MSGS_PER_HOUR: 3 });

  for (let i = 0; i < 5; i++) {
    s.cola.encolar({ to: AM, texto: `ficha ${i}`, kind: 'am_notice' });
  }
  await s.cola.vacia();
  assert.equal(s.proveedor.getEnviados().length, 5, 'los internos salen todos');

  // Y despues de esos cinco, el cupo para clientes sigue entero.
  s.proveedor.limpiar();
  for (let i = 0; i < 3; i++) {
    s.cola.encolar({ to: `5989900${String(i).padStart(4, '0')}`, texto: `hola ${i}`, kind: 'welcome' });
  }
  await s.cola.vacia();

  assert.equal(s.proveedor.getEnviados().length, 3, 'los tres del cupo salen igual');
  assert.equal(s.cola.reprogramados(), 0);
});
