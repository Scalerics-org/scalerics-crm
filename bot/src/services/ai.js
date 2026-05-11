const Anthropic = require('@anthropic-ai/sdk');
const config = require('../config');

let client = null;

function getClient() {
  if (!client && config.ANTHROPIC_API_KEY) {
    client = new Anthropic({ apiKey: config.ANTHROPIC_API_KEY });
  }
  return client;
}

const BUSINESS_LABELS = {
  1: 'Agencia/Consultora', 2: 'E-commerce', 3: 'Servicios profesionales',
  4: 'SaaS/Software', 5: 'Otro',
};
const PROBLEM_LABELS = {
  1: 'No responde a tiempo', 2: 'Tareas repetitivas',
  3: 'Sin métricas', 4: 'No puede escalar', 5: 'Otro',
};
const TEAM_LABELS = { 1: 'Solo yo', 2: '2-5 personas', 3: '6-20 personas', 4: 'Más de 20' };
const BUDGET_LABELS = { 1: 'Presupuesto disponible', 2: 'Depende del ROI', 3: 'Aún no' };
const URGENCY_LABELS = { 1: 'Este mes', 2: '2-3 meses', 3: 'Evaluando' };

function scoreByRules(lead) {
  let score = 0;
  if (lead.budget === 1) score += 3;
  else if (lead.budget === 2) score += 1;

  if (lead.urgency === 1) score += 3;
  else if (lead.urgency === 2) score += 1;

  if (lead.team_size >= 2) score += 2;
  if ([1, 4].includes(lead.business_type)) score += 2;

  const priority = score >= 7 ? 'high' : score >= 4 ? 'medium' : 'low';
  const recommended_action = score >= 7 ? 'meeting' : score >= 4 ? 'nurture' : 'disqualify';

  return {
    score: Math.min(score, 10),
    priority,
    recommended_action,
    reason: 'Calculado por reglas (sin IA)',
  };
}

async function scoreLead(lead) {
  const anthropic = getClient();

  // Fallback to rules if no API key
  if (!anthropic) {
    console.warn('No ANTHROPIC_API_KEY set — using rule-based scoring');
    return scoreByRules(lead);
  }

  const prompt = `Sos un calificador de leads para una agencia de desarrollo web y software (Scalerics) que vende a PyMEs uruguayas.

Analizá estas respuestas y devolvé un JSON:

Tipo de proyecto: ${BUSINESS_LABELS[lead.business_type] || 'No especificado'}
Tamaño del equipo: ${TEAM_LABELS[lead.team_size] || 'No especificado'}
Presupuesto: ${BUDGET_LABELS[lead.budget] || 'No especificado'}
Colores de marca: ${lead.colors || 'No especificado'}
Instagram/web: ${lead.instagram_web || 'Ninguno'}
Necesidades: ${lead.needs || 'No especificado'}

Devolvé SOLO este JSON, sin texto adicional:
{"score":<1-10>,"priority":"<high|medium|low>","recommended_action":"<meeting|nurture|disqualify>","reason":"<una línea>"}

Criterios: score 8-10 = presupuesto disponible + necesidades claras + ya tiene presencia online. Score 5-7 = potencial condicional. Score 1-4 = sin presupuesto ni claridad.`;

  try {
    const message = await anthropic.messages.create({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 150,
      messages: [{ role: 'user', content: prompt }],
    });

    const text = message.content[0]?.text || '';
    const json = JSON.parse(text.trim());

    return {
      score: Math.min(Math.max(parseInt(json.score, 10), 1), 10),
      priority: ['high', 'medium', 'low'].includes(json.priority) ? json.priority : 'medium',
      recommended_action: ['meeting', 'nurture', 'disqualify'].includes(json.recommended_action)
        ? json.recommended_action
        : 'nurture',
      reason: json.reason || '',
    };
  } catch (err) {
    console.error('AI scoring failed, falling back to rules:', err.message);
    return scoreByRules(lead);
  }
}

// Interpret free-text input as a numbered option (fallback for non-numeric answers)
async function interpretFreeText(question, options, userInput) {
  const anthropic = getClient();
  if (!anthropic) return 'unknown';

  const prompt = `El usuario respondió con texto libre a esta pregunta de calificación.
Pregunta: "${question}"
Opciones válidas: ${options}
Respuesta del usuario: "${userInput}"

Devolvé SOLO el número de opción más cercana (1, 2, 3, etc.) o "unknown". Sin explicación.`;

  try {
    const message = await anthropic.messages.create({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 10,
      messages: [{ role: 'user', content: prompt }],
    });
    return message.content[0]?.text?.trim() || 'unknown';
  } catch {
    return 'unknown';
  }
}

module.exports = { scoreLead, interpretFreeText, scoreByRules };
