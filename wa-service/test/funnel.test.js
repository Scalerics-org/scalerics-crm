'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { conLead } = require('./helpers');
const { S } = require('../src/funnel/states');
const { porReglas } = require('../src/funnel/scoring');

/** Manda un mensaje del lead y espera a que la cola drene. */
async function lead(s, texto) {
  await s.servicioLeads.registrarRespuesta('59899123456', texto);
  await s.cola.vacia();
  return s.proveedor.getEnviados().filter((e) => e.to === '59899123456').map((e) => e.texto);
}

const estado = (s) => s.repo.leadPorTelefono('59899123456').fsm_state;

test('la primera respuesta abre el menu', async () => {
  const s = await conLead();
  const msgs = await lead(s, 'hola');

  assert.equal(estado(s), S.MENU);
  assert.match(msgs.at(-1), /asistente de \*Scalerics\*/);
  assert.match(msgs.at(-1), /Quiero un presupuesto/);
});

test('recorre el embudo entero hasta la oferta de reunion', async () => {
  const s = await conLead();

  await lead(s, 'hola');            // → MENU
  await lead(s, '1');               // → QUAL_0 (nombre del negocio)
  assert.equal(estado(s), S.QUAL_0);

  await lead(s, 'Inmobiliaria Pereyra'); // → QUAL_1
  assert.equal(estado(s), S.QUAL_1);

  await lead(s, '2');               // e-commerce → QUAL_2
  await lead(s, '3');               // >3000 USD  → QUAL_3
  await lead(s, '3');               // 6-20 pers. → QUAL_4
  await lead(s, 'inmobiliaria');    // → QUAL_5
  await lead(s, '@inmopereyra');    // → QUAL_6
  const msgs = await lead(s, 'quiero dejar de perder consultas'); // → SCORED

  const l = s.repo.leadPorTelefono('59899123456');
  assert.equal(l.business_name, 'Inmobiliaria Pereyra');
  assert.equal(l.business_type, 2);
  assert.equal(l.budget, 3);
  assert.equal(l.team_size, 3);
  assert.equal(l.rubro, 'inmobiliaria');
  assert.equal(l.rubro_norm, 'inmobiliaria', 'se clasifica al guardarlo, como el del formulario');
  assert.equal(l.instagram_web, '@inmopereyra');
  assert.equal(l.needs, 'quiero dejar de perder consultas');

  // budget 3 (+3) + team>=2 (+2) + ecommerce (+2) + brief (+1) + rubro conocido (+1) = 9
  assert.equal(l.score, 9);
  assert.equal(l.priority, 'high');
  assert.equal(l.fsm_state, S.MEETING_SENT);
  assert.match(msgs.at(-1), /videollamada de 30 minutos/);
});

test('un lead flojo cae en nurture y no se le ofrece reunion', async () => {
  const s = await conLead();
  await lead(s, 'hola');
  await lead(s, '1');
  await lead(s, 'Kiosco Don José');
  await lead(s, '1');               // pagina web
  await lead(s, '1');               // menos de 500 USD
  await lead(s, '1');               // solo yo
  await lead(s, 'no tengo');
  await lead(s, 'no tengo');
  const msgs = await lead(s, 'algo simple');

  const l = s.repo.leadPorTelefono('59899123456');
  assert.equal(l.score, 1, 'solo suma el presupuesto minimo: el brief es muy corto');
  assert.equal(l.fsm_state, S.DISQUALIFIED);
  assert.ok(!msgs.at(-1).includes('videollamada'));
});

test('pedir el link de la reunion despues de la oferta', async () => {
  const s = await conLead();
  for (const t of ['hola', '1', 'Mi negocio', '2', '3', '3', 'inmobiliaria', '@x', 'necesito ventas']) {
    await lead(s, t);
  }
  assert.equal(estado(s), S.MEETING_SENT);

  // El "2" pide saber mas; cualquier otra cosa se toma como que quiere el link.
  const mas = await lead(s, '2');
  assert.match(mas.at(-1), /agencia uruguaya/);
  assert.equal(estado(s), S.MEETING_INFO);

  const msgs = await lead(s, '1');
  assert.match(msgs.at(-1), /calendly\.com\/scalerics\/diagnostico/);
  assert.equal(estado(s), S.MEETING_LINK_SENT);
});

test('insistir con "todavia no" corta la insistencia en vez de repetir', async () => {
  // Pasaba esto en produccion: MEETING_SENT mandaba MORE_INFO ante cualquier
  // "2" y se quedaba en el mismo estado, asi que el lead recibia el mismo
  // parrafo tantas veces como apretara la opcion.
  const s = await conLead();
  for (const t of ['hola', '1', 'Mi negocio', '2', '3', '3', 'inmobiliaria', '@x', 'necesito ventas']) {
    await lead(s, t);
  }
  await lead(s, '2');               // → MEETING_INFO, le cuenta
  s.proveedor.limpiar();

  const otra = await lead(s, '2');  // "todavia no"
  assert.equal(estado(s), S.NURTURE);
  assert.ok(!/agencia uruguaya/.test(otra.at(-1)), 'no repite el mismo texto');
  assert.match(otra.at(-1), /sin apuro/);
  assert.match(otra.at(-1), /calendly/, 'le deja el link por las dudas');

  // Y no queda encerrado: el siguiente mensaje vuelve al menu.
  const despues = await lead(s, 'hola');
  assert.equal(estado(s), S.MENU);
  assert.match(despues.at(-1), /asistente de \*Scalerics\*/);
});

test('desde "quiero saber mas" se puede volver a pedir el link', async () => {
  const s = await conLead();
  for (const t of ['hola', '1', 'Mi negocio', '2', '3', '3', 'inmobiliaria', '@x', 'necesito ventas']) {
    await lead(s, t);
  }
  await lead(s, '2');
  const msgs = await lead(s, '1');

  assert.equal(estado(s), S.MEETING_LINK_SENT);
  assert.match(msgs.at(-1), /calendly\.com\/scalerics\/diagnostico/);
});

test('el lead flojo recibe el texto de nurture, no el de "todavia no"', async () => {
  // NURTURE se alcanza por dos caminos y cada uno tiene su texto. Si el
  // handler de SCORED dejara de mandar el mensaje, este camino quedaria mudo.
  const s = await conLead();
  await lead(s, 'hola');
  await lead(s, '1');
  await lead(s, 'Kiosco Don José');
  await lead(s, '3');               // automatizacion
  await lead(s, '2');               // 500 a 3000
  await lead(s, '1');               // solo yo
  await lead(s, 'no tengo');
  await lead(s, 'no tengo');
  const msgs = await lead(s, 'algo simple');

  assert.equal(estado(s), S.NURTURE);
  assert.match(msgs.at(-1), /ya tengo todo anotado/);
});

test('respuesta invalida: reintenta y despues deriva a un humano', async () => {
  const s = await conLead();
  await lead(s, 'hola');
  await lead(s, '1');
  await lead(s, 'Mi negocio');
  assert.equal(estado(s), S.QUAL_1);

  const r1 = await lead(s, 'no se');
  assert.match(r1.at(-1), /No entendí/);
  assert.equal(estado(s), S.QUAL_1, 'no avanza');

  const r2 = await lead(s, 'ni idea');
  assert.match(r2.at(-1), /Sigo sin entender/);

  await lead(s, 'que se yo');
  const r4 = await lead(s, 'nada');
  assert.equal(estado(s), S.HUMAN_QUEUED, 'al cuarto intento pasa a un humano');
  assert.match(r4.at(-1), /le paso tu contacto a alguien del equipo/);
});

test('un numero valido resetea el contador de reintentos', async () => {
  const s = await conLead();
  await lead(s, 'hola'); await lead(s, '1'); await lead(s, 'Mi negocio');

  await lead(s, 'no se');
  assert.equal(s.repo.leadPorTelefono('59899123456').fsm_retries, 1);

  await lead(s, '1');
  assert.equal(s.repo.leadPorTelefono('59899123456').fsm_retries, 0);
  assert.equal(estado(s), S.QUAL_2);
});

test('"baja" da de baja al lead y despues hay silencio', async () => {
  const s = await conLead();
  await lead(s, 'hola');

  const msgs = await lead(s, 'baja');
  assert.equal(estado(s), S.OPT_OUT);
  assert.match(msgs.at(-1), /no te escribo más/);

  const l = s.repo.leadPorTelefono('59899123456');
  assert.equal(l.opt_out, 1);
  assert.equal(l.status, 'closed');

  // Y no se le vuelve a escribir nunca, ni siquiera el follow-up.
  s.proveedor.limpiar();
  await lead(s, 'hola?');
  assert.equal(s.proveedor.getEnviados().length, 0);

  s.scheduler.correrVencidos(new Date(Date.now() + 73 * 3600 * 1000));
  await s.cola.vacia();
  assert.equal(s.proveedor.getEnviados().length, 0, 'el follow-up tampoco sale');
});

test('"humano" congela el bot y solo "menu" lo reactiva', async () => {
  const s = await conLead();
  await lead(s, 'hola');
  await lead(s, 'quiero hablar con una persona');
  assert.equal(estado(s), S.HUMAN_QUEUED);
  assert.equal(s.repo.leadPorTelefono('59899123456').human_requested, 1);

  // Mientras hay un humano a cargo, el bot no contesta nada.
  s.proveedor.limpiar();
  await lead(s, 'hola?');
  await lead(s, '1');
  assert.equal(s.proveedor.getEnviados().length, 0);

  const msgs = await lead(s, 'menu');
  assert.equal(estado(s), S.MENU);
  assert.equal(s.repo.leadPorTelefono('59899123456').human_requested, 0);
  assert.match(msgs.at(-1), /asistente de \*Scalerics\*/);
});

test('al AM se le avisa una sola vez, no por cada mensaje del embudo', async () => {
  const s = await conLead();
  await lead(s, 'hola');
  await lead(s, '1');
  await lead(s, 'Mi negocio');

  const alAM = s.proveedor.getEnviados().filter((e) => e.to === '59899000111');
  assert.equal(alAM.length, 1);
  assert.match(alAM[0].texto, /respondió/);
});

test('siempre saluda a la persona, no a la empresa', async () => {
  // business_name adelante hacia que, despues de la pregunta del negocio, el
  // menu saludara "Hola Inmobiliaria Pereyra". La empresa es dato del CRM.
  const s = await conLead(undefined, {
    external_id: 'l1', nombre: 'Martín Pereyra', rubro: 'Inmobiliaria',
    telefono: '099123456', necesidad: 'x', origen: 'form',
  });

  await lead(s, 'hola');
  await lead(s, '1');
  await lead(s, 'Inmobiliaria Pereyra');

  const menu = await lead(s, 'menu');
  assert.match(menu.at(-1), /Hola Martín/);
  assert.ok(!menu.at(-1).includes('Inmobiliaria Pereyra'), 'no saluda con la empresa');
  assert.ok(!menu.at(-1).includes('Pereyra 👋'), 'usa el primer nombre, no el completo');

  // Y el nombre del negocio sigue guardado para el CRM.
  assert.equal(s.repo.leadPorTelefono('59899123456').business_name, 'Inmobiliaria Pereyra');
});

test('la oferta de reunion tambien va a nombre de la persona', async () => {
  const s = await conLead();
  for (const t of ['hola', '1', 'Inmobiliaria Pereyra', '2', '3', '3', 'inmobiliaria', '@x', 'ventas']) {
    await lead(s, t);
  }
  const ofertas = s.proveedor.getEnviados()
    .filter((e) => e.to === '59899123456' && e.texto.includes('videollamada'));
  assert.equal(ofertas.length, 1);
  assert.match(ofertas[0].texto, /Perfecto, Martín/);
});

test('el texto libre se guarda crudo, con acentos y mayusculas', async () => {
  // La normalizacion es para enrutar, no para guardar. El original persistia el
  // texto ya normalizado y "la atención de mañana" quedaba "la atencion de manana".
  const s = await conLead();
  await lead(s, 'hola');
  await lead(s, '1');
  await lead(s, 'Inmobiliaria Pereyra');
  await lead(s, '2'); await lead(s, '3'); await lead(s, '3');
  await lead(s, 'Venta de Autos Usados');
  await lead(s, '@InmoPereyra');
  await lead(s, 'Necesito automatizar la atención de mañana');

  const l = s.repo.leadPorTelefono('59899123456');
  assert.equal(l.business_name, 'Inmobiliaria Pereyra');
  assert.equal(l.rubro, 'Venta de Autos Usados');
  assert.equal(l.instagram_web, '@InmoPereyra');
  assert.equal(l.needs, 'Necesito automatizar la atención de mañana');
});

test('el embudo se puede apagar con FUNNEL_ENABLED', async () => {
  const s = await conLead({ FUNNEL_ENABLED: false });
  await lead(s, 'hola');

  assert.equal(estado(s), 'NEW', 'no se mueve del estado inicial');
  assert.equal(
    s.proveedor.getEnviados().filter((e) => e.to === '59899123456').length, 0,
    'no le contesta al lead'
  );
});

// ── scoring por reglas ────────────────────────────────────────────────────────

test('el scoring por reglas ya no depende de urgency', () => {
  // El original sumaba hasta 3 puntos por lead.urgency y pedia >= 7 para
  // ofrecer reunion. Nadie escribe ese campo desde que se saco la pregunta,
  // asi que casi ningun lead llegaba al umbral.
  const bueno = porReglas({ budget: 3, team_size: 3, business_type: 2 });
  assert.equal(bueno.score, 7);
  assert.equal(bueno.recommended_action, 'meeting');

  const medio = porReglas({ budget: 2, team_size: 1, business_type: 1 });
  assert.equal(medio.score, 2);
  assert.equal(medio.recommended_action, 'disqualify');

  const conPresupuesto = porReglas({ budget: 3, team_size: 1, business_type: 1 });
  assert.equal(conPresupuesto.score, 3);
  assert.equal(conPresupuesto.recommended_action, 'nurture');

  const vacio = porReglas({});
  assert.equal(vacio.score, 0);
  assert.equal(vacio.recommended_action, 'disqualify');

  // Un campo urgency suelto no cambia nada.
  assert.deepEqual(porReglas({ budget: 3, urgency: 1 }), porReglas({ budget: 3 }));
});

test('automatizacion puntua como proyecto de alcance, no como cero', () => {
  // Estaba en [2,4]: automatizacion, que es de lo que habla toda la
  // comunicacion de Scalerics, sumaba 0 y el lead terminaba descartado.
  const base = { budget: 2, team_size: 2, needs: 'necesito un sistema para gestionar stock' };

  assert.equal(porReglas({ ...base, business_type: 3 }).recommended_action, 'meeting', 'automatizacion');
  assert.equal(porReglas({ ...base, business_type: 2 }).recommended_action, 'meeting', 'ecommerce');
  assert.equal(porReglas({ ...base, business_type: 4 }).recommended_action, 'meeting', 'app a medida');
});

test('un brief escrito de verdad suma; uno de dos palabras no', () => {
  const base = { business_type: 1, budget: 2, team_size: 1 };
  assert.equal(porReglas({ ...base, needs: 'algo simple' }).score, 2);
  assert.equal(porReglas({ ...base, needs: 'necesito un sistema para gestionar el stock' }).score, 3);
});

test('el AM se entera de como termino el embudo, no solo cuando gana', async () => {
  const s = await conLead();
  for (const t of ['hola', '1', 'Kiosco', '1', '1', '1', 'no', 'no', 'algo']) await lead(s, t);

  const resumenes = s.proveedor.getEnviados()
    .filter((e) => e.to === '59899000111' && /score/.test(e.texto));
  assert.equal(resumenes.length, 1, 'llega un resumen aunque el lead no califique');
  assert.match(resumenes[0].texto, /descartado|pausa/);
  assert.match(resumenes[0].texto, /wa\.me\/59899123456/);
});

// ── datos que se le sacan al lead ────────────────────────────────────────────

test('al que escribe directo al WhatsApp se le pregunta el rubro', async () => {
  // El del formulario trae rubro; este no. Sin preguntarlo, la demo de la
  // primera llamada sale generica y el follow-up usa el gancho de nadie.
  const s = await require('./helpers').montar();
  s.proveedor.simularEntrante({ from: '59891234567', texto: 'hola', id: 'w.1', nombre: 'Jorge' });
  await new Promise((r) => setImmediate(r));
  await s.cola.vacia();

  const responder = async (t) => {
    await s.servicioLeads.registrarRespuesta('59891234567', t);
    await s.cola.vacia();
    return s.proveedor.getEnviados().filter((e) => e.to === '59891234567').map((e) => e.texto);
  };

  await responder('1');
  await responder('Parrilla El Fogón');
  await responder('1'); await responder('2'); await responder('2');
  const preg = await responder('parrilla y delivery');

  const l = s.repo.leadPorTelefono('59891234567');
  assert.equal(l.rubro, 'parrilla y delivery');
  assert.equal(l.rubro_norm, 'gastronomia', 'queda clasificado para el gancho del follow-up');

  // Y la pregunta que sigue explica para que sirve la red social.
  assert.match(preg.at(-1), /Instagram/);
  assert.match(preg.at(-1), /te muestro algo armado con tus cosas/);
});

test('el AM recibe el rubro en el resumen del embudo', async () => {
  const s = await conLead();
  for (const t of ['hola', '1', 'Mi negocio', '2', '3', '3', 'inmobiliaria', '@x', 'necesito ventas']) {
    await lead(s, t);
  }
  const resumen = s.proveedor.getEnviados()
    .filter((e) => e.to === '59899000111')
    .find((e) => /Lead calificado/.test(e.texto));

  assert.ok(resumen, 'le llega el resumen');
  assert.match(resumen.texto, /🏢 inmobiliaria/);
  assert.match(resumen.texto, /🔗 @x/, 'y la red, que es con lo que arma la demo');
});

test('el texto de "quiero saber mas" no inventa casos ni numeros', async () => {
  // El superprompt lo prohibe, y ademas cada cifra que promete el bot despues
  // la tiene que sostener alguien en la llamada. Decia "una empresa recibe 50
  // consultas por dia" y "el 80% se resuelve solo": los dos inventados.
  const { crearTextos } = require('../src/templates/funnel');
  const t = crearTextos({ calendlyLink: 'x' });

  assert.ok(!/\d+\s*%/.test(t.MORE_INFO), 'sin porcentajes');
  assert.ok(!/caso real|un cliente|una empresa/i.test(t.MORE_INFO), 'sin casos inventados');
  assert.match(t.MORE_INFO, /agencia uruguaya/);
});

test('el horario de atencion sale de la config, no del texto', async () => {
  const { crearTextos } = require('../src/templates/funnel');

  assert.match(crearTextos({ calendlyLink: 'x' }).HUMAN_QUEUED, /Lun a sáb, 9 a 19hs/);
  assert.match(
    crearTextos({ calendlyLink: 'x', horarioAtencion: 'Lun a vie, 10 a 16hs' }).HUMAN_QUEUED,
    /Lun a vie, 10 a 16hs/
  );
});

// ── el rubro en el scoring ───────────────────────────────────────────────────

test('un rubro que Scalerics sabe atender suma un punto', () => {
  const base = { budget: 2, team_size: 1, business_type: 1, needs: 'algo' };

  assert.equal(porReglas({ ...base }).score, 2, 'sin rubro');
  assert.equal(porReglas({ ...base, rubro_norm: 'generico' }).score, 2, 'no clasificado, no suma');

  for (const r of ['gastronomia', 'salud', 'retail', 'servicios_profesionales',
                   'inmobiliaria', 'educacion', 'automotriz']) {
    assert.equal(porReglas({ ...base, rubro_norm: r }).score, 3, r);
  }
});

test('ningun rubro pesa mas que otro', () => {
  // A proposito: poner uno arriba de otro seria inventar un ranking que nadie
  // midio. La tabla existe para cuando haya datos de conversion por vertical.
  const puntos = ['gastronomia', 'salud', 'retail', 'servicios_profesionales',
                  'inmobiliaria', 'educacion', 'automotriz']
    .map((r) => porReglas({ rubro_norm: r }).score);

  assert.equal(new Set(puntos).size, 1, 'todos valen lo mismo');
});

test('el rubro solo no alcanza para una reunion', () => {
  // Es un empujon, no un atajo: sin presupuesto ni proyecto sigue descartado.
  const r = porReglas({ rubro_norm: 'gastronomia' });
  assert.equal(r.score, 1);
  assert.equal(r.recommended_action, 'disqualify');
});

test('el rubro puede inclinar un lead del medio hacia la reunion', () => {
  // budget 2 (+2) + team>=2 (+2) = 4, justo debajo del umbral. Con el rubro
  // identificado llega a 5. Es el caso que el cambio busca mover.
  const medio = { budget: 2, team_size: 2, business_type: 1, needs: 'corto' };

  assert.equal(porReglas(medio).recommended_action, 'nurture');
  assert.equal(porReglas({ ...medio, rubro_norm: 'automotriz' }).recommended_action, 'meeting');
});

// ── despues de mandar el link ────────────────────────────────────────────────

/** Lleva un lead hasta tener el link de Calendly en la mano. */
async function conLink(s) {
  for (const t of ['hola', '1', 'Mi negocio', '2', '3', '3', 'inmobiliaria', '@x', 'necesito ventas']) {
    await lead(s, t);
  }
  await lead(s, 'dale');            // → MEETING_LINK_SENT, le manda el link
  assert.equal(estado(s), S.MEETING_LINK_SENT);
  s.proveedor.limpiar();
}

test('el link de Calendly no se manda dos veces', async () => {
  // Esto pasaba en produccion: MEETING_SENT contestaba el link a cualquier cosa
  // y se quedaba en si mismo, asi que un "hola" devolvia el link, y otro "hola"
  // devolvia el link, sin final.
  const s = await conLead();
  await conLink(s);

  const msgs = await lead(s, 'hola');
  assert.ok(!/calendly/.test(msgs.at(-1)), 'no repite el link');
  assert.match(msgs.at(-1), /¿Pudiste agendar\?/);
});

test('si dice que ya agendo, se le cree y se deja de insistir', async () => {
  const s = await conLead();
  await conLink(s);

  const msgs = await lead(s, 'ya agendé para el jueves');
  assert.match(msgs.at(-1), /quedamos así/);
  assert.ok(!/calendly/.test(msgs.at(-1)), 'no le pide que agende de nuevo');

  // El follow-up NO se cancela: la verdad la trae el webhook de Calendly. Si
  // se confundio y no reservo, el follow-up es justo lo que hay que mandarle.
  const l = s.repo.leadPorTelefono('59899123456');
  assert.equal(l.opt_out, 0);
});

test('"agendamos?" no se confunde con "ya agendé"', async () => {
  // El que pregunta "agendamos?" todavia NO reservo. Si se tomara como que si,
  // se le dejaria de insistir justo al que estaba por convertir.
  const s = await conLead();
  await conLink(s);

  const msgs = await lead(s, 'agendamos entonces?');
  assert.ok(!/quedamos así/.test(msgs.at(-1)));
});

test('el que sigue escribiendo con el link en la mano termina con una persona', async () => {
  const s = await conLead();
  await conLink(s);

  await lead(s, 'hola');            // primera insistencia: se le pregunta
  const msgs = await lead(s, 'hola?');  // segunda: quiere otra cosa

  assert.equal(estado(s), S.HUMAN_QUEUED);
  assert.match(msgs.at(-1), /le paso tu contacto a alguien del equipo/);
  assert.equal(s.repo.leadPorTelefono('59899123456').motivo_derivacion, 'post_oferta');
});

test('con la reunion agendada no se repite el "te esperamos"', async () => {
  const s = await conLead();
  const lid = s.repo.leadPorTelefono('59899123456').id;
  s.servicioLeads.registrarReunion(lid, {
    telefono: '59899123456',
    meeting_time: new Date(Date.now() + 48 * 3600 * 1000).toISOString(),
    meeting_url: 'https://meet.google.com/x',
  });
  await s.cola.vacia();
  s.proveedor.limpiar();

  await lead(s, 'hola');
  await lead(s, 'una consulta');
  const repetidos = s.proveedor.getEnviados()
    .filter((e) => e.to === '59899123456' && /te esperamos/.test(e.texto));
  assert.equal(repetidos.length, 0, 'ya lo dijo al confirmarse, no lo repite');
});
