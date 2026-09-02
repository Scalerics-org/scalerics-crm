'use strict';

/**
 * Reglas deterministas sobre cada mensaje que manda el bot.
 *
 * Esto NO usa un modelo para juzgar: son regex y conteos. Es a proposito.
 * Un juez LLM es caro, lento y opina distinto cada vez; para "dijo un precio"
 * o "mando una URL que no es la nuestra" no hace falta criterio, hace falta
 * un grep. Lo que si necesita criterio (si el mensaje suena natural, si el
 * gancho pego) se mira a ojo en el transcript que imprime el runner.
 *
 * gravedad:
 *  - 'grave': rompe una promesa al cliente o mete un dato falso. Falla el caso.
 *  - 'leve': afea el mensaje. Se cuenta y se reporta, no falla el caso.
 */

const TUTEO = [
  /\btienes\b/i,
  /\bquieres\b/i,
  /\bpuedes\b/i,
  /\bcuéntame\b/i,
  /\bcuentame\b/i,
  /\bdime\b/i,
  /\bcontigo\b/i,
  /\bte gustaría saber\b/i,
  /\bmanejas\b/i,
  /\bnecesitas\b/i,
  /\btrabajas\b/i,
  // El pronombre, no el posesivo. "tu negocio" es voseo correcto —el posesivo
  // de vos ES "tu"— y estaba marcado como falla: cada corrida reportaba un
  // tuteo que no existia, en el mensaje mas comun del bot. Un arnes que da
  // falsos positivos en lo normal enseña a ignorar el reporte.
  /\btú\b/i,
];

const PRECIO = [
  /(\$|usd|u\$s|dólares|dolares|pesos)\s*\.?\s*\d/i,
  /\d[\d.,]*\s*(dólares|dolares|pesos|usd|u\$s|lucas|mil)\b/i,
  /\b(arranca|arrancan|empieza|empiezan|sale|salen|cuesta|cuestan|ronda|rondan)\s+(en|desde|alrededor|los|las)?\s*\d/i,
  /\bdesde\s+(los\s+)?\d[\d.,]*\b/i,
];

const PLAZO = [
  /\ben\s+\d+\s*(días|dias|semanas|meses|horas)\b/i,
  /\b(te lo entrego|lo tenemos listo|estaría listo|estaria listo)\b/i,
  /\b(la semana que viene|el mes que viene)\b.*\b(list[oa]|entreg|termin)/i,
];

const NARRA_GUARDADO = [
  /\b(anoto|anoté|anote|te anoto|guardo|guardé|guarde|registro|registré|lo dejo anotado|queda anotado)\b/i,
];

const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2190}-\u{21FF}\u{2B00}-\u{2BFF}\u{FE0F}]/gu;

function contar(texto, re) {
  const m = texto.match(re);
  return m ? m.length : 0;
}

/** Normaliza una pregunta para poder compararla con la del turno anterior. */
function normalizarPregunta(texto) {
  const m = texto.match(/[^.!\n]*\?/g);
  if (!m) return null;
  return m[m.length - 1]
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9 ]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * ctx = { etapa, calLink, mensajeAnterior, preguntaAnterior, linkYaEnviado }
 */
const REGLAS = [
  {
    id: 'precio',
    gravedad: 'grave',
    revisar: (t) => {
      const hit = PRECIO.find((re) => re.test(t));
      return hit ? `dijo un precio o un rango: ${JSON.stringify(t.match(hit)[0])}` : null;
    },
  },
  {
    id: 'url_ajena',
    gravedad: 'grave',
    revisar: (t, ctx) => {
      const urls = t.match(/https?:\/\/\S+/gi) || [];
      const ajenas = urls.filter((u) => u.replace(/[).,]+$/, '') !== ctx.calLink);
      return ajenas.length ? `mandó una URL que no es la de agenda: ${ajenas.join(', ')}` : null;
    },
  },
  {
    id: 'link_temprano',
    gravedad: 'grave',
    revisar: (t, ctx) => {
      if (!ctx.calLink) return null;
      if (!t.includes(ctx.calLink)) return null;
      return ctx.etapa === 'descubrimiento'
        ? 'mandó el link de agenda antes de saber a qué se dedica'
        : null;
    },
  },
  {
    id: 'link_repetido',
    gravedad: 'leve',
    revisar: (t, ctx) => {
      if (!ctx.calLink || !t.includes(ctx.calLink)) return null;
      return ctx.linkYaEnviado ? 'reenvió el link sin que se lo pidieran' : null;
    },
  },
  {
    id: 'plazo',
    gravedad: 'grave',
    revisar: (t) => (PLAZO.some((re) => re.test(t)) ? 'prometió un plazo de entrega' : null),
  },
  {
    id: 'voseo',
    gravedad: 'leve',
    revisar: (t) => {
      const hit = TUTEO.find((re) => re.test(t));
      return hit ? `se le escapó el tuteo: ${JSON.stringify(t.match(hit)[0])}` : null;
    },
  },
  {
    id: 'dos_preguntas',
    gravedad: 'leve',
    revisar: (t) => {
      const n = contar(t, /\?/g);
      return n > 1 ? `hizo ${n} preguntas en un mismo mensaje` : null;
    },
  },
  {
    id: 'repite_pregunta',
    gravedad: 'leve',
    revisar: (t, ctx) => {
      const actual = normalizarPregunta(t);
      if (!actual || !ctx.preguntaAnterior) return null;
      return actual === ctx.preguntaAnterior ? 'repitió textual la pregunta del turno anterior' : null;
    },
  },
  {
    id: 'largo',
    gravedad: 'leve',
    revisar: (t) => (t.length > 400 ? `mensaje de ${t.length} caracteres, es WhatsApp` : null),
  },
  {
    id: 'emojis',
    gravedad: 'leve',
    revisar: (t) => {
      const n = contar(t, EMOJI);
      return n > 1 ? `${n} emojis en un mensaje` : null;
    },
  },
  {
    id: 'grito',
    gravedad: 'leve',
    revisar: (t) => {
      if (/[!?]{2,}/.test(t)) return 'signos repetidos';
      if (/\b[A-ZÁÉÍÓÚÑ]{4,}\b/.test(t.replace(/\bUSD\b|\bCEO\b|\bIA\b|\bWEB\b/g, ''))) {
        return 'mayúsculas sostenidas';
      }
      return null;
    },
  },
  {
    id: 'narra_guardado',
    gravedad: 'leve',
    revisar: (t) => (NARRA_GUARDADO.some((re) => re.test(t)) ? 'narró que estaba anotando' : null),
  },
];

function revisarMensaje(texto, ctx) {
  const fallas = [];
  for (const regla of REGLAS) {
    const detalle = regla.revisar(texto, ctx);
    if (detalle) fallas.push({ id: regla.id, gravedad: regla.gravedad, detalle });
  }
  return fallas;
}

module.exports = { revisarMensaje, normalizarPregunta, REGLAS };
