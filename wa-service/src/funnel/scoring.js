'use strict';

const ETIQUETAS = {
  business_type: {
    1: 'Página web', 2: 'E-commerce', 3: 'Automatización', 4: 'Sistema a medida',
    5: 'Agente de IA', 6: 'Todavía no sabe',
  },
  team_size: { 1: 'Solo yo', 2: '2-5 personas', 3: '6-20 personas', 4: 'Más de 20' },
  budget: { 1: 'Menos de $500 USD', 2: '$500 a $3.000 USD', 3: 'Más de $3.000 USD', 4: 'No lo tiene claro' },
};

/**
 * Cuanto suma el rubro. Todos valen lo mismo a proposito.
 *
 * Lo que se premia no es el rubro en si sino que se haya podido identificar:
 * son los siete para los que Scalerics tiene una propuesta armada y un gancho
 * escrito. El que describe su negocio y cae en alguno es un lead al que se le
 * sabe vender; el que queda en generico puede ser cualquier cosa.
 *
 * Poner a gastronomia por encima de salud, o al reves, seria inventar un
 * ranking que nadie midio. La tabla queda para cuando haya datos de conversion
 * por vertical y se pueda ajustar con fundamento.
 */
const PESO_RUBRO = {
  gastronomia: 1,
  salud: 1,
  retail: 1,
  servicios_profesionales: 1,
  inmobiliaria: 1,
  educacion: 1,
  automotriz: 1,
};

// Con lo que el embudo realmente pregunta, el maximo alcanzable es 9.
const UMBRAL_REUNION = 5;
const UMBRAL_NURTURE = 3;

/**
 * Scoring por reglas. Es el camino que corre cuando no hay OPENAI_API_KEY.
 *
 * Difiere del original de bot/src/services/ai.js a proposito: aquel sumaba
 * hasta 3 puntos por `lead.urgency` y pedia score >= 7 para ofrecer reunion.
 * La pregunta de urgencia se saco del embudo hace tiempo y nadie escribe ese
 * campo, asi que el maximo alcanzable era exactamente 7 y solo la combinacion
 * perfecta conseguia una reunion: todo lo demas caia en nurture o descarte.
 * Aca se saca urgency del calculo y se recalibran los umbrales sobre el maximo
 * real.
 */
function porReglas(lead) {
  let score = 0;

  // Presupuesto declarado: la senial mas fuerte.
  if (lead.budget === 3) score += 3;
  else if (lead.budget === 2) score += 2;
  else if (lead.budget === 1) score += 1;

  // Equipo: mas de una persona suele significar presupuesto real.
  if (lead.team_size >= 2) score += 2;

  // Todo lo que no sea una web institucional simple es proyecto de mayor
  // alcance. Automatizacion estaba en 0 puntos, y es el servicio del que habla
  // toda la comunicacion de Scalerics: un lead que la pide terminaba descartado.
  if ([2, 3, 4].includes(lead.business_type)) score += 2;

  // Un brief escrito con contenido real es de las mejores seniales que da el
  // embudo, y las reglas eran ciegas a el. Aproxima pobremente lo que el
  // scoring con IA hace bien: sin OPENAI_API_KEY es lo unico que hay.
  if (String(lead.needs || '').trim().length >= 25) score += 1;

  // El rubro, cuando se pudo clasificar. rubro_norm lo escriben las dos vias:
  // el alta desde el formulario del CRM y la pregunta del embudo.
  score += PESO_RUBRO[lead.rubro_norm] || 0;

  const accion =
    score >= UMBRAL_REUNION ? 'meeting' : score >= UMBRAL_NURTURE ? 'nurture' : 'disqualify';
  const prioridad =
    score >= UMBRAL_REUNION ? 'high' : score >= UMBRAL_NURTURE ? 'medium' : 'low';

  return {
    score: Math.min(score, 10),
    priority: prioridad,
    recommended_action: accion,
    reason: 'Calculado por reglas (sin IA)',
  };
}

function construirPrompt(lead) {
  return `Sos un calificador de leads para una agencia de desarrollo web y software (Scalerics) que vende a PyMEs uruguayas.

Analizá estas respuestas y devolvé un JSON:

Rubro del negocio: ${lead.rubro || 'No especificado'}
Tipo de proyecto: ${ETIQUETAS.business_type[lead.business_type] || 'No especificado'}
Tamaño del equipo: ${ETIQUETAS.team_size[lead.team_size] || 'No especificado'}
Presupuesto: ${ETIQUETAS.budget[lead.budget] || 'No especificado'}
Instagram/web: ${lead.instagram_web || 'Ninguno'}
Necesidades: ${lead.needs || 'No especificado'}

Devolvé SOLO este JSON, sin texto adicional:
{"score":<1-10>,"priority":"<high|medium|low>","recommended_action":"<meeting|nurture|disqualify>","reason":"<una línea>"}

Criterios: score 8-10 = presupuesto disponible + necesidades claras + ya tiene presencia online. Score 5-7 = potencial condicional. Score 1-4 = sin presupuesto ni claridad.

Sobre el rubro: Scalerics tiene propuesta armada para gastronomía, salud, comercio/retail, servicios profesionales (estudios, contadores, abogados), inmobiliarias, educación y automotriz. Un negocio de esos es terreno conocido y suma. Uno fuera de esa lista no resta por sí solo — puede ser un gran lead — pero no suma por rubro.`;
}

/**
 * @param {object} deps.openai cliente ya construido, o null para ir por reglas.
 */
function crearScorer({ openai = null, modelo = 'gpt-4o-mini', logger = null } = {}) {
  return {
    async calificar(lead) {
      if (!openai) return porReglas(lead);

      try {
        const r = await openai.chat.completions.create({
          model: modelo,
          max_tokens: 150,
          response_format: { type: 'json_object' },
          messages: [{ role: 'user', content: construirPrompt(lead) }],
        });
        const json = JSON.parse((r.choices?.[0]?.message?.content || '').trim());

        return {
          score: Math.min(Math.max(parseInt(json.score, 10) || 0, 1), 10),
          priority: ['high', 'medium', 'low'].includes(json.priority) ? json.priority : 'medium',
          recommended_action: ['meeting', 'nurture', 'disqualify'].includes(json.recommended_action)
            ? json.recommended_action
            : 'nurture',
          reason: json.reason || '',
        };
      } catch (e) {
        logger?.warn({ err: String(e.message || e) }, 'scoring con IA fallo, se usan reglas');
        return porReglas(lead);
      }
    },
  };
}

module.exports = { crearScorer, porReglas, UMBRAL_REUNION, UMBRAL_NURTURE };
