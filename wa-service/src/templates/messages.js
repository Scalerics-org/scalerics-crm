'use strict';

/**
 * Textos de los mensajes. Marketing edita aca sin tocar logica.
 *
 * Reglas de redaccion, que no son estilo sino anti-reporte:
 * - Cada rubro tiene 2-3 variantes. Mandar el mismo texto identico a muchos
 *   numeros es de las senales mas fuertes de spam.
 * - Sin bloques en mayusculas, un emoji como maximo, y SIN LINKS en el primer
 *   mensaje: los links en el primer contacto disparan filtros.
 * - Cuatro lineas como maximo, y termina en pregunta abierta: que el lead
 *   conteste abre la ventana de conversacion y baja el riesgo.
 */

const GENERICO = {
  bienvenida: [
    (l) => `Hola ${l.primerNombre}! Soy del equipo de Scalerics 👋
Vi que nos escribiste desde la web${l.necesidadCorta ? ` por ${l.necesidadCorta}` : ''}.
Trabajamos con negocios como el tuyo automatizando lo que hoy se hace a mano.
¿Te sirve si te muestro en 15 minutos cómo lo resolvemos? ¿Qué día te queda cómodo?`,
    (l) => `Hola ${l.primerNombre}, ¿cómo va? Te escribo de Scalerics.
Nos llegó tu consulta${l.necesidadCorta ? ` sobre ${l.necesidadCorta}` : ''} y me quedé con ganas de entenderla mejor.
¿Tenés un rato esta semana para una llamada corta y te cuento qué haríamos?`,
  ],
  followup: [
    (l) => `Hola ${l.primerNombre}, ¿pudiste ver mi mensaje?
Si preferís te paso un caso corto de un cliente parecido y lo mirás con calma. Avisame.`,
    (l) => `${l.primerNombre}, no quiero ser insistente 😅
Si el momento no es ahora, decime y te escribo más adelante. Y si querés avanzar, agendamos cuando digas.`,
  ],
};

const PLANTILLAS = {
  inmobiliaria: {
    bienvenida: [
      (l) => `Hola ${l.primerNombre}! Soy del equipo de Scalerics 👋
Vi que nos escribiste desde la web${l.necesidadCorta ? ` por ${l.necesidadCorta}` : ''}.
Trabajamos bastante con inmobiliarias, sobre todo automatizando el seguimiento de consultas para que ninguna quede sin responder.
¿Te sirve si te muestro en 15 minutos cómo lo resolvemos? ¿Qué día te queda cómodo?`,
      (l) => `Hola ${l.primerNombre}, te escribo de Scalerics.
En inmobiliarias lo que más vemos es consultas que se pierden entre WhatsApp, mail y el portal.
Eso se puede ordenar y automatizar bastante. ¿Charlamos 15 minutos esta semana?`,
    ],
    followup: [
      (l) => `Hola ${l.primerNombre}, ¿pudiste ver mi mensaje?
Si querés te dejo un caso corto de una inmobiliaria que bajó el tiempo de respuesta de 4 horas a 2 minutos. Avisame y te lo paso.`,
      (l) => `${l.primerNombre}, te dejo la puerta abierta.
Si en algún momento querés ver cómo ordenar el seguimiento de consultas, escribime y lo vemos.`,
    ],
  },

  salud: {
    bienvenida: [
      (l) => `Hola ${l.primerNombre}! Soy del equipo de Scalerics 👋
Vi tu consulta desde la web${l.necesidadCorta ? ` sobre ${l.necesidadCorta}` : ''}.
Con consultorios y clínicas trabajamos mucho el tema turnos y recordatorios, que es donde se pierde más tiempo.
¿Te viene bien una llamada corta esta semana?`,
      (l) => `Hola ${l.primerNombre}, te escribo de Scalerics.
Lo que más nos piden en salud es reducir las ausencias a los turnos y dejar de confirmar uno por uno a mano.
¿Querés que te muestre cómo queda funcionando? ¿Qué día te sirve?`,
    ],
    followup: [
      (l) => `Hola ${l.primerNombre}, ¿llegaste a ver mi mensaje?
Si querés te cuento en dos líneas cómo una clínica bajó las ausencias con recordatorios automáticos.`,
      (l) => `${l.primerNombre}, sin apuro.
Si más adelante querés ver el tema turnos y recordatorios, escribime y lo charlamos.`,
    ],
  },

  gastronomia: {
    bienvenida: [
      (l) => `Hola ${l.primerNombre}! Soy del equipo de Scalerics 👋
Nos escribiste desde la web${l.necesidadCorta ? ` por ${l.necesidadCorta}` : ''}.
En gastronomía solemos ayudar con pedidos y reservas, para no depender de contestar cada mensaje a mano.
¿Te sirve una llamada corta esta semana?`,
      (l) => `Hola ${l.primerNombre}, te escribo de Scalerics.
Vimos tu consulta. Con restaurantes lo que mejor funciona es automatizar reservas y pedidos por WhatsApp.
¿Charlamos 15 minutos y te muestro?`,
    ],
    followup: [
      (l) => `Hola ${l.primerNombre}, ¿pudiste verlo?
Si querés te paso un ejemplo de cómo queda el flujo de pedidos andando. Avisame.`,
      (l) => `${l.primerNombre}, quedo por acá.
Cuando quieras retomar el tema pedidos o reservas, escribime.`,
    ],
  },

  retail: {
    bienvenida: [
      (l) => `Hola ${l.primerNombre}! Soy del equipo de Scalerics 👋
Vi tu consulta desde la web${l.necesidadCorta ? ` sobre ${l.necesidadCorta}` : ''}.
Con comercios trabajamos catálogo online y atención automatizada, que es lo que más tiempo libera.
¿Te viene bien una llamada corta?`,
      (l) => `Hola ${l.primerNombre}, te escribo de Scalerics.
Lo que más nos piden en comercio es vender sin tener que contestar cada consulta a mano.
¿Querés que te muestre cómo lo armamos? ¿Qué día te queda bien?`,
    ],
    followup: [
      (l) => `Hola ${l.primerNombre}, ¿viste mi mensaje?
Si querés te muestro una tienda que armamos hace poco y lo comparás con lo que tenés hoy.`,
      (l) => `${l.primerNombre}, sin problema si no es el momento.
Cuando quieras verlo, escribime y lo retomamos.`,
    ],
  },

  servicios_profesionales: {
    bienvenida: [
      (l) => `Hola ${l.primerNombre}! Soy del equipo de Scalerics 👋
Nos llegó tu consulta desde la web${l.necesidadCorta ? ` sobre ${l.necesidadCorta}` : ''}.
Con estudios y consultoras lo que más ordenamos es la captación de clientes y el seguimiento, que suele quedar disperso.
¿Te sirve una llamada corta esta semana?`,
      (l) => `Hola ${l.primerNombre}, te escribo de Scalerics.
En estudios profesionales el cuello de botella suele ser el seguimiento de consultas nuevas.
¿Charlamos 15 minutos y te cuento cómo lo resolvemos?`,
    ],
    followup: [
      (l) => `Hola ${l.primerNombre}, ¿pudiste ver mi mensaje?
Si querés te cuento en corto cómo lo hicimos con un estudio parecido al tuyo.`,
      (l) => `${l.primerNombre}, quedo atento.
Si más adelante querés ordenar el tema captación y seguimiento, escribime.`,
    ],
  },

  educacion: {
    bienvenida: [
      (l) => `Hola ${l.primerNombre}! Soy del equipo de Scalerics 👋
Vi tu consulta desde la web${l.necesidadCorta ? ` sobre ${l.necesidadCorta}` : ''}.
Con institutos y academias trabajamos inscripciones y seguimiento de interesados, que es donde más se cae gente.
¿Te viene bien una llamada corta?`,
      (l) => `Hola ${l.primerNombre}, te escribo de Scalerics.
Lo que más piden los institutos es no perder a los que consultan y nunca se inscriben.
¿Querés que te muestre cómo se automatiza? ¿Qué día te queda cómodo?`,
    ],
    followup: [
      (l) => `Hola ${l.primerNombre}, ¿llegaste a verlo?
Si querés te paso un caso de una academia que mejoró bastante la conversión de consultas a inscripciones.`,
      (l) => `${l.primerNombre}, sin apuro.
Cuando quieras retomar, escribime y lo vemos.`,
    ],
  },

  automotriz: {
    bienvenida: [
      (l) => `Hola ${l.primerNombre}! Soy del equipo de Scalerics 👋
Nos escribiste desde la web${l.necesidadCorta ? ` por ${l.necesidadCorta}` : ''}.
Con automotoras y talleres trabajamos el seguimiento de consultas por unidad y los turnos de servicio.
¿Te sirve una llamada corta esta semana?`,
      (l) => `Hola ${l.primerNombre}, te escribo de Scalerics.
En el rubro automotor lo que más se pierde son las consultas que llegan fuera de hora y nadie retoma.
¿Charlamos 15 minutos y te muestro cómo lo resolvemos?`,
    ],
    followup: [
      (l) => `Hola ${l.primerNombre}, ¿pudiste ver mi mensaje?
Si querés te cuento cómo una automotora dejó de perder consultas de fin de semana.`,
      (l) => `${l.primerNombre}, quedo por acá.
Cuando quieras verlo, escribime y lo retomamos.`,
    ],
  },

  generico: GENERICO,
};

/** Ficha del lead para el account manager. Es contacto interno: sin restricciones. */
function fichaAM(lead, { waLink }) {
  const lineas = [
    '🔔 Nuevo lead — ' + (lead.origen === 'chat' ? 'chat de la web' : 'formulario web'),
    `👤 ${lead.nombre}`,
  ];
  if (lead.rubro) lineas.push(`🏢 ${lead.rubro}`);
  lineas.push(`📱 ${lead.telefonoLegible}`);
  if (lead.necesidad) lineas.push(`💬 "${lead.necesidad}"`);
  lineas.push(`🕐 ${lead.fechaLegible}`);
  lineas.push(waLink);
  return lineas.join('\n');
}

function avisoRespuesta(lead, texto) {
  const recorte = String(texto || '').slice(0, 200);
  return `💬 ${lead.nombre} respondió: "${recorte}"`;
}

/** Alguien escribio al numero sin pasar por el formulario. No es una respuesta. */
function avisoContactoNuevo(lead, texto) {
  const recorte = String(texto || '').slice(0, 200);
  return [
    '🔔 Nuevo contacto por WhatsApp',
    `👤 ${lead.nombre || 'sin nombre'}`,
    `📱 +${lead.telefono}`,
    `💬 "${recorte}"`,
    `wa.me/${lead.telefono}`,
  ].join('\n');
}

function avisoSinRespuesta(lead) {
  return `⏰ ${lead.nombre} no respondió en 24h — follow-up enviado.`;
}

module.exports = { PLANTILLAS, fichaAM, avisoRespuesta, avisoContactoNuevo, avisoSinRespuesta };
