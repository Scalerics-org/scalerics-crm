'use strict';

/**
 * Runner. Corre cada caso, aplica las reglas a cada mensaje del bot, compara
 * el lead final contra lo esperado y escribe evals/reporte.md.
 *
 * Uso:
 *   ANTHROPIC_API_KEY=... CAL_LINK=https://cal.com/scalerics/30min \
 *   EVAL_MODEL=<el mismo que usa ia/agente.js> node evals/correr.js
 *
 *   node evals/correr.js --caso carniceria-no-es-nombre   # uno solo
 *   node evals/correr.js --stub                            # sin gastar tokens
 *   node evals/correr.js --repeticiones 3                  # promedia el ruido
 */

require('dotenv').config();
const fs = require('fs');
const path = require('path');

const PROMPT_PATH = process.env.PROMPT_PATH || '../src/ia/prompt';
const { construirSystem, etapa, DATOS, CAL_LINK } = require(PROMPT_PATH);
const { revisarMensaje, normalizarPregunta } = require('./reglas');
const { CASOS } = require('./casos');
const { crearModeloAnthropic } = require('./modelo');

const args = process.argv.slice(2);
const flag = (n, def) => {
  const i = args.indexOf(`--${n}`);
  return i === -1 ? def : args[i + 1];
};
const usarStub = args.includes('--stub');
const soloCaso = flag('caso', null);
const REPS = Number(flag('repeticiones', 1));

const CAMPOS = new Set([...DATOS.map((d) => d.campo), 'nombre']);

function vacio(v) {
  return v === null || v === undefined || v === '';
}

/** Aplica lo que el modelo quiso guardar, descartando campos que no existen. */
function aplicarGuardados(lead, guardados) {
  const rechazados = [];
  for (const g of guardados) {
    for (const [k, v] of Object.entries(g || {})) {
      if (!CAMPOS.has(k)) {
        rechazados.push(k);
        continue;
      }
      if (vacio(v)) continue;
      lead[k] = v;
    }
  }
  return rechazados;
}

function coincide(valor, esperado) {
  if (esperado instanceof RegExp) return esperado.test(String(valor ?? ''));
  return String(valor ?? '') === String(esperado);
}

async function correrCaso(caso, modelo) {
  const lead = { ...(caso.lead || {}) };
  const turnos = caso.libreto ? caso.libreto.length : caso.turnos || 5;

  const transcript = [];
  const fallas = [];
  let preguntaAnterior = null;
  let turnoCierre = null;
  let vecesLink = 0;
  let camposInventados = [];

  for (let i = 0; i < turnos; i++) {
    const etapaAntes = etapa(lead);
    const linkYaEnviado = Boolean(lead.link_enviado);

    const userMsg = caso.libreto
      ? caso.libreto[i]
      : await modelo.lead(caso.persona, transcriptAHistorial(transcript));

    const historial = [...transcriptAHistorial(transcript), { role: 'user', content: userMsg }];
    const { texto, guardados } = await modelo.bot(construirSystem(lead), historial, null, lead);

    camposInventados.push(...aplicarGuardados(lead, guardados));

    for (const f of revisarMensaje(texto, {
      etapa: etapaAntes,
      calLink: CAL_LINK,
      preguntaAnterior,
      linkYaEnviado,
      // Saludar en el primer mensaje esta bien; en el septimo, no.
      primerTurno: i === 0,
    })) {
      fallas.push({ ...f, turno: i + 1 });
    }

    if (CAL_LINK && texto.includes(CAL_LINK)) {
      vecesLink++;
      lead.link_enviado = true; // en producción esto lo setea el código, no el modelo
    }
    preguntaAnterior = normalizarPregunta(texto);
    transcript.push({ turno: i + 1, etapa: etapaAntes, user: userMsg, bot: texto, guardados });

    if (etapa(lead) === 'cierre' && turnoCierre === null) turnoCierre = i + 1;
  }

  // --- comparaciones contra lo esperado ---
  for (const [campo, esperado] of Object.entries(caso.espera || {})) {
    if (!coincide(lead[campo], esperado)) {
      fallas.push({
        id: 'dato_mal',
        gravedad: 'grave',
        detalle: `${campo} quedó ${JSON.stringify(lead[campo] ?? null)}, se esperaba ${esperado}`,
      });
    }
  }

  for (const [campo, porque] of Object.entries(caso.prohibido || {})) {
    if (!vacio(lead[campo])) {
      fallas.push({
        id: 'dato_inventado',
        gravedad: 'grave',
        detalle: `${campo} quedó ${JSON.stringify(lead[campo])} — ${porque}`,
      });
    }
  }

  for (const k of new Set(camposInventados)) {
    fallas.push({ id: 'campo_inexistente', gravedad: 'grave', detalle: `intentó guardar "${k}"` });
  }

  const textos = transcript.map((t) => t.bot).join('\n');

  if (caso.debeMandarLink && vecesLink === 0) {
    fallas.push({ id: 'no_mando_link', gravedad: 'grave', detalle: 'dijo que sí y nunca mandó el link' });
  }
  if (caso.debeCerrarEn && (turnoCierre === null || turnoCierre > caso.debeCerrarEn)) {
    fallas.push({
      id: 'cierre_tardio',
      gravedad: 'grave',
      detalle: `llegó a cierre en el turno ${turnoCierre ?? '—'}, se esperaba en ${caso.debeCerrarEn} o antes`,
    });
  }
  if (caso.noDebeResponderTecnico) {
    if (/\b(sí, se puede|si se puede|se conecta|se integra|podés conectar|se puede integrar)\b/i.test(textos)) {
      fallas.push({ id: 'pisa_al_humano', gravedad: 'grave', detalle: 'contestó la duda técnica en vez de derivar' });
    }
    if (!/\b(equipo|se lo paso|le paso|te contact|lo ve|lo vemos)\b/i.test(textos)) {
      fallas.push({ id: 'no_derivo', gravedad: 'grave', detalle: 'no derivó al equipo' });
    }
  }
  if (caso.noDebePreguntar && /\?/.test(textos)) {
    fallas.push({ id: 'insistio', gravedad: 'grave', detalle: 'siguió preguntando después de una baja' });
  }

  return {
    id: caso.id,
    porque: caso.porque,
    lead,
    transcript,
    fallas,
    turnoCierre,
    vecesLink,
    graves: fallas.filter((f) => f.gravedad === 'grave').length,
    leves: fallas.filter((f) => f.gravedad === 'leve').length,
  };
}

function transcriptAHistorial(transcript) {
  const h = [];
  for (const t of transcript) {
    h.push({ role: 'user', content: t.user });
    if (t.bot) h.push({ role: 'assistant', content: t.bot });
  }
  return h;
}

function reporte(resultados, modeloNombre) {
  const l = [];
  l.push(`# Reporte de evals — bot de WhatsApp Scalerics\n`);
  l.push(`Modelo: \`${modeloNombre}\`  ·  Casos: ${resultados.length}  ·  CAL_LINK: ${CAL_LINK || '(sin configurar)'}\n`);

  const totalGraves = resultados.reduce((a, r) => a + r.graves, 0);
  const pasados = resultados.filter((r) => r.graves === 0).length;
  l.push(`**${pasados}/${resultados.length} casos sin fallas graves.** ${totalGraves} fallas graves en total.\n`);

  l.push(`| Caso | Graves | Leves | Cierre | Link |`);
  l.push(`|---|---|---|---|---|`);
  for (const r of resultados) {
    l.push(
      `| ${r.id} | ${r.graves || '—'} | ${r.leves || '—'} | ${r.turnoCierre ? `turno ${r.turnoCierre}` : '—'} | ${r.vecesLink || '—'} |`
    );
  }
  l.push('');

  for (const r of resultados) {
    l.push(`## ${r.id}`);
    l.push(`_${r.porque || ''}_\n`);
    if (r.fallas.length) {
      for (const f of r.fallas) {
        const t = f.turno ? ` (turno ${f.turno})` : '';
        l.push(`- **${f.gravedad}** \`${f.id}\`${t}: ${f.detalle}`);
      }
    } else {
      l.push('- sin fallas');
    }
    l.push('\n<details><summary>conversación</summary>\n');
    for (const t of r.transcript) {
      l.push(`**[${t.etapa}] lead:** ${t.user}`);
      l.push(`**bot:** ${t.bot}`);
      if (t.guardados?.length) l.push(`\`guardó: ${JSON.stringify(Object.assign({}, ...t.guardados))}\``);
      l.push('');
    }
    l.push('</details>\n');
    l.push(`Lead final: \`${JSON.stringify(r.lead)}\`\n`);
  }
  return l.join('\n');
}

async function main() {
  const modelo = usarStub
    ? require('./stub').crearModeloStub()
    : crearModeloAnthropic({
      modelo: process.env.EVAL_MODEL || process.env.IA_MODELO,
      modeloLead: process.env.EVAL_MODEL_LEAD,
      calLink: CAL_LINK,
    });
  let casos = CASOS;
  if (soloCaso) casos = CASOS.filter((c) => c.id === soloCaso);
  if (!casos.length) {
    console.error(`No hay ningún caso con id "${soloCaso}"`);
    process.exit(1);
  }

  const resultados = [];
  for (const caso of casos) {
    for (let rep = 0; rep < REPS; rep++) {
      const r = await correrCaso(caso, modelo);
      if (REPS > 1) r.id = `${r.id} #${rep + 1}`;
      resultados.push(r);
      const marca = r.graves === 0 ? 'ok  ' : 'FALLA';
      console.log(`${marca} ${r.id}  graves=${r.graves} leves=${r.leves}`);
    }
  }

  const salida = path.join(__dirname, 'reporte.md');
  fs.writeFileSync(salida, reporte(resultados, modelo.nombre));
  console.log(`\nReporte: ${salida}`);

  const graves = resultados.reduce((a, r) => a + r.graves, 0);
  process.exit(graves === 0 ? 0 : 1);
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e.message);
    process.exit(2);
  });
}

module.exports = { correrCaso, aplicarGuardados };
