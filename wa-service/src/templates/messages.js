'use strict';

/**
 * Textos de los mensajes. Marketing edita aca sin tocar logica.
 *
 * Los ganchos por rubro salen del superprompt de Scalerics.
 *
 * Reglas de redaccion, que no son estilo sino anti-reporte:
 * - Cada rubro tiene 2 variantes y se sortea una por lead. Mandar el mismo
 *   texto identico a muchos numeros es de las senales mas fuertes de spam.
 * - Sin mayusculas sostenidas, un emoji como maximo, sin exclamaciones
 *   multiples. Frases cortas.
 *
 * DONDE VA EL LINK DE CALENDLY: en el follow-up, no en la bienvenida.
 *
 * El primer mensaje termina en pregunta abierta. Que el lead conteste hace dos
 * cosas: baja el riesgo de baneo (un link en el primer contacto en frio es de
 * las senales de spam mas fuertes) y —lo que mas pesa— establece la sesion de
 * cifrado. Todo lo que quedo sin entregar en las pruebas fue a numeros que
 * nunca nos habian escrito; a los que contestaron primero llega siempre.
 *
 * El follow-up si lo lleva: para entonces ya hubo un primer contacto.
 */

/** Gancho especifico por rubro, del superprompt. */
const GANCHOS = {
  gastronomia: 'Solemos ayudar a locales como el tuyo a armar un sistema de pedidos online que se integra con lo que ya usan.',
  salud: 'Para consultorios y centros como el tuyo, lo más pedido es un sistema de turnos online con recordatorios automáticos.',
  retail: 'Para comercios como el tuyo, lo típico es una tienda online con cobro integrado, lista para vender desde el primer día.',
  servicios_profesionales: 'Para este tipo de negocio, lo que más piden es una web que transmita confianza y un sistema simple para gestionar presupuestos.',
  inmobiliaria: 'Con inmobiliarias lo que más ordenamos es el seguimiento de consultas, para que ninguna quede sin responder.',
  educacion: 'Con institutos y academias trabajamos inscripciones y seguimiento de interesados, que es donde más gente se cae.',
  automotriz: 'Con automotoras y talleres armamos el seguimiento de consultas por unidad y los turnos de servicio.',
};

const GENERICO = {
  bienvenida: [
    (l) => `Hola ${l.primerNombre}, somos Scalerics.
Nos llegó tu consulta${l.necesidadCorta ? ` sobre ${l.necesidadCorta}` : ''}.
Contanos un poco más de lo que necesitás y te decimos cómo te podemos ayudar.
¿Tenés 15 minutos esta semana para una videollamada con el equipo? ¿Qué día te queda cómodo?`,
    (l) => `Hola ${l.primerNombre}, te escribo de Scalerics.
Vimos lo que nos dejaste${l.necesidadCorta ? ` sobre ${l.necesidadCorta}` : ''} y queremos entenderlo bien antes de proponerte nada.
¿Charlamos 15 minutos esta semana? Decime qué día te sirve y coordinamos.`,
  ],
  followup: [
    (l) => `Hola ${l.primerNombre}, te escribimos hace unos días${l.necesidadCorta ? ` por ${l.necesidadCorta}` : ''}.
¿Seguís interesado en que hablemos? Cualquier horario te acomodamos.
${l.calendly}`,
  ],
};

/** Arma las variantes de bienvenida y el follow-up de un rubro. */
function plantillaDeRubro(gancho) {
  return {
    bienvenida: [
      (l) => `Hola ${l.primerNombre}, somos Scalerics.
Vimos que nos escribiste${l.necesidadCorta ? ` por ${l.necesidadCorta}` : ''}.
${gancho}
¿Tenés 15 minutos esta semana para una videollamada rápida con el equipo? ¿Qué día te queda cómodo?`,
      (l) => `Hola ${l.primerNombre}, te escribo de Scalerics.
${gancho}
Si te sirve, coordinamos una llamada corta. ¿Qué día de esta semana te viene bien?`,
    ],
    followup: [
      (l) => `Hola ${l.primerNombre}, te escribimos hace unos días${l.necesidadCorta ? ` por ${l.necesidadCorta}` : ''}.
¿Seguís interesado en que hablemos? Cualquier horario te acomodamos.
${l.calendly}`,
    ],
  };
}

const PLANTILLAS = Object.fromEntries(
  Object.entries(GANCHOS).map(([clave, gancho]) => [clave, plantillaDeRubro(gancho)])
);
PLANTILLAS.generico = GENERICO;

// ── recordatorios de reunion ────────────────────────────────────────────────

function recordatorioDiaAntes(lead, { cuando, link }) {
  return [
    `📅 Hola ${lead.primerNombre}, te recuerdo la llamada con Scalerics.`,
    `Es mañana ${cuando}.`,
    link ? `🔗 ${link}` : null,
    'Si te surgió algo y necesitás moverla, avisame y la reprogramamos.',
  ].filter(Boolean).join('\n');
}

function recordatorio30Minutos(lead, { cuando, link }) {
  return [
    `⏰ ${lead.primerNombre}, en media hora es la llamada con Scalerics (${cuando}).`,
    link ? `🔗 ${link}` : null,
    'Nos vemos.',
  ].filter(Boolean).join('\n');
}

// ── avisos al account manager ───────────────────────────────────────────────

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

function avisoSinRespuesta(lead, horas) {
  return `⏰ ${lead.nombre || lead.telefono} no respondió en ${horas}h — follow-up enviado.`;
}

function avisoReunionAgendada(lead, { cuando, link }) {
  return [
    '🗓 Reunión agendada',
    `👤 ${lead.business_name || lead.nombre || lead.telefono}`,
    `🕐 ${cuando}`,
    link ? `🔗 ${link}` : null,
    `wa.me/${lead.telefono}`,
  ].filter(Boolean).join('\n');
}

const TIPO_PROYECTO = {
  1: 'Página web', 2: 'E-commerce', 3: 'Automatización', 4: 'App a medida',
};
const PRESUPUESTO = {
  1: 'menos de $500', 2: '$500 a $3.000', 3: 'más de $3.000', 4: 'no lo tiene claro',
};
const EQUIPO = { 1: 'solo', 2: '2-5 personas', 3: '6-20 personas', 4: 'más de 20' };

/**
 * Resumen para el AM cuando el lead termina el embudo. Sin esto, un lead que
 * contesta las seis preguntas y no llega al umbral de reunion queda en la base
 * y nadie del equipo se entera de que existio.
 */
function resumenEmbudo(lead, desenlace) {
  const titulo = {
    meeting: '🎯 Lead calificado — se le ofreció reunión',
    nurture: '🌱 Lead en pausa — no llegó al umbral de reunión',
    disqualify: '📋 Lead descartado por el embudo',
  }[desenlace] || '📋 Lead terminó el embudo';

  const lineas = [titulo, `👤 ${lead.business_name || lead.nombre || 'sin nombre'}`];
  if (lead.business_type) lineas.push(`🛠 ${TIPO_PROYECTO[lead.business_type] || lead.business_type}`);
  if (lead.budget) lineas.push(`💵 ${PRESUPUESTO[lead.budget] || lead.budget}`);
  if (lead.team_size) lineas.push(`👥 ${EQUIPO[lead.team_size] || lead.team_size}`);
  if (lead.instagram_web) lineas.push(`🔗 ${lead.instagram_web}`);
  if (lead.needs) lineas.push(`💬 "${String(lead.needs).slice(0, 200)}"`);
  lineas.push(`⭐ score ${lead.score ?? '?'}`);
  lineas.push(`wa.me/${lead.telefono}`);
  return lineas.join('\n');
}

module.exports = {
  PLANTILLAS,
  GANCHOS,
  fichaAM,
  avisoRespuesta,
  avisoContactoNuevo,
  avisoSinRespuesta,
  avisoReunionAgendada,
  resumenEmbudo,
  recordatorioDiaAntes,
  recordatorio30Minutos,
};
