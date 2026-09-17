'use strict';

/**
 * Lee la tabla jev_sombra y dice si conviene dejar decidir a Jev.
 *
 * Abre la base en SOLO LECTURA y no corre migraciones: se puede correr contra
 * una copia del volumen sin miedo a tocar nada.
 *
 *   npm run jev:informe                      (usa DB_PATH del .env)
 *   npm run jev:informe -- /ruta/a/wa.db
 *
 * OJO CON UNA COSA, que es la que puede hacer tomar la decision al reves:
 * `coincide` mide ACUERDO CON EL BOT, no acierto. Cuando el bot se equivoca
 * —que es justo el problema que motivo todo esto— el desacuerdo es Jev
 * acertando. Por eso el informe no llama "precision" al acuerdo y termina
 * listando los desacuerdos para que los lea una persona.
 */

require('dotenv').config();
const Database = require('better-sqlite3');
const { NECESIDAD } = require('../src/ia/sombra');

const USD_POR_MILLON = 0.042;
const UMBRALES = [0.5, 0.6, 0.7, 0.8, 0.9];

/** business_type del bot -> la etiqueta con la que lo nombra Jev. */
const TIPO_A_ETIQUETA = Object.fromEntries(
  Object.entries(NECESIDAD).map(([etiqueta, v]) => [v.tipo, etiqueta])
);
const etiquetaBot = (t) => (t === null || t === undefined ? 'sin dato' : (TIPO_A_ETIQUETA[t] || `tipo ${t}`));

const pct = (parte, total) => (total ? `${((parte / total) * 100).toFixed(0)}%` : '—');

function percentil(valores, p) {
  if (!valores.length) return null;
  const orden = [...valores].sort((a, b) => a - b);
  return orden[Math.min(orden.length - 1, Math.floor((p / 100) * orden.length))];
}

function tabla(filas, columnas) {
  const anchos = columnas.map((c, i) => Math.max(c.length, ...filas.map((f) => String(f[i]).length)));
  const linea = (celdas) => '  ' + celdas.map((c, i) => String(c).padEnd(anchos[i])).join('  ');
  console.log(linea(columnas));
  console.log('  ' + anchos.map((a) => '─'.repeat(a)).join('  '));
  for (const f of filas) console.log(linea(f));
}

function main() {
  const ruta = process.argv[2] || process.env.DB_PATH || './data/scalerics-wa.db';
  const db = new Database(ruta, { readonly: true, fileMustExist: true });

  const hay = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='jev_sombra'").get();
  if (!hay) {
    console.log(`No hay tabla jev_sombra en ${ruta}: el modo sombra todavia no corrio.`);
    return;
  }

  const filas = db.prepare('SELECT * FROM jev_sombra ORDER BY id').all().map((f) => ({
    ...f,
    bot: f.bot ? JSON.parse(f.bot) : null,
    jev: f.jev ? JSON.parse(f.jev) : null,
  }));

  console.log(`\nBase: ${ruta}`);
  if (!filas.length) {
    console.log('La tabla esta vacia. Prender JEV_MODO=sombra y esperar leads.');
    return;
  }
  const desde = filas[0].creado_en;
  const hasta = filas.at(-1).creado_en;
  const leads = new Set(filas.map((f) => f.lead_id).filter(Boolean));
  console.log(`Periodo: ${desde} a ${hasta} (UTC) · ${filas.length} consultas · ${leads.size} leads\n`);

  // ── 1. por decision ────────────────────────────────────────────────────────
  console.log('ACUERDO CON EL BOT (no es precision: ver el final)');
  tabla(
    ['necesidad', 'oferta'].map((d) => {
      const f = filas.filter((x) => x.decision === d);
      const si = f.filter((x) => x.coincide === 1).length;
      const no = f.filter((x) => x.coincide === 0).length;
      const nulo = f.filter((x) => x.coincide === null).length;
      return [d, f.length, `${si} (${pct(si, f.length)})`, `${no} (${pct(no, f.length)})`, nulo];
    }),
    ['decision', 'corridas', 'coincide', 'no coincide', 'sin comparar']
  );

  // ── 2. matriz de necesidad ─────────────────────────────────────────────────
  const nec = filas.filter((x) => x.decision === 'necesidad' && x.jev?.necesidad?.choice);
  if (nec.length) {
    console.log('\nQUE GUARDO EL BOT  vs  QUE ELIGIO JEV');
    const cruces = new Map();
    for (const f of nec) {
      const clave = `${etiquetaBot(f.bot?.business_type ?? null)}|${f.jev.necesidad.choice}`;
      const actual = cruces.get(clave) || { n: 0, conf: [] };
      actual.n += 1;
      if (typeof f.confianza === 'number') actual.conf.push(f.confianza);
      cruces.set(clave, actual);
    }
    tabla(
      [...cruces.entries()]
        .sort((a, b) => b[1].n - a[1].n)
        .map(([clave, v]) => {
          const [bot, jev] = clave.split('|');
          const media = v.conf.length ? (v.conf.reduce((s, x) => s + x, 0) / v.conf.length).toFixed(2) : '—';
          return [bot, jev, v.n, bot === jev ? '' : '← desacuerdo', media];
        }),
      ['bot', 'jev', 'casos', '', 'conf. media']
    );
  }

  // ── 3. la curva del umbral ────────────────────────────────────────────────
  console.log('\nCURVA DEL UMBRAL (solo necesidad; es lo que decide JEV_UMBRAL)');
  const conConf = nec.filter((f) => typeof f.confianza === 'number');
  tabla(
    UMBRALES.map((u) => {
      const arriba = conConf.filter((f) => f.confianza >= u);
      const deAcuerdo = arriba.filter((f) => f.coincide === 1).length;
      const noClaro = arriba.filter((f) => f.jev?.necesidad?.choice === 'no_queda_claro').length;
      return [
        u.toFixed(1), `${arriba.length} (${pct(arriba.length, conConf.length)})`,
        `${deAcuerdo} (${pct(deAcuerdo, arriba.length)})`,
        arriba.length - deAcuerdo, noClaro,
      ];
    }),
    ['umbral', 'casos por encima', 'de acuerdo con el bot', 'en desacuerdo', 'de esos, "no_queda_claro"']
  );
  console.log('  Con JEV_MODO=decide, "en desacuerdo" es la cantidad de veces que Jev habria');
  console.log('  cambiado lo que hizo el bot. Mirar abajo si esos cambios eran mejoras.');

  // ── 4. latencia ───────────────────────────────────────────────────────────
  const ms = filas.map((f) => f.ms).filter((x) => typeof x === 'number');
  if (ms.length) {
    console.log(`\nLATENCIA  p50 ${percentil(ms, 50)}ms · p95 ${percentil(ms, 95)}ms · max ${Math.max(...ms)}ms`);
  }

  // ── 5. costo ──────────────────────────────────────────────────────────────
  const tokens = filas.map((f) => f.input_tokens).filter((x) => typeof x === 'number');
  if (!tokens.length) {
    console.log('\nCOSTO  sin datos: estas filas son anteriores a la migracion 021 (input_tokens).');
  } else {
    const total = tokens.reduce((s, x) => s + x, 0);
    const usd = (total / 1e6) * USD_POR_MILLON;
    const porLead = leads.size ? total / leads.size : 0;
    console.log(`\nCOSTO  ${total.toLocaleString('es-UY')} tokens de entrada = USD ${usd.toFixed(4)}`);
    console.log(`  ${Math.round(porLead)} tokens por lead → USD ${((porLead * 1000) / 1e6 * USD_POR_MILLON).toFixed(2)} cada 1.000 leads`);
    if (tokens.length < filas.length) {
      console.log(`  (${filas.length - tokens.length} consultas sin tokens, anteriores a la migracion 021)`);
    }
  }

  // ── 6. los desacuerdos, para leer a mano ──────────────────────────────────
  const desacuerdos = filas
    .filter((f) => f.coincide === 0 && typeof f.confianza === 'number')
    .sort((a, b) => b.confianza - a.confianza)
    .slice(0, 10);

  if (desacuerdos.length) {
    console.log('\nLOS 10 DESACUERDOS MAS CONFIADOS');
    console.log('Aca es donde se decide: en cada uno, ¿tenia razon el bot o Jev?\n');
    const mensajes = db.prepare(`
      SELECT direction, body FROM messages
      WHERE lead_id = ? AND kind != 'am_notice' AND body IS NOT NULL AND body != ''
      ORDER BY id DESC LIMIT 6
    `);
    for (const f of desacuerdos) {
      const lead = f.lead_id
        ? db.prepare('SELECT nombre, business_name FROM leads WHERE id = ?').get(f.lead_id)
        : null;
      console.log(`─ lead ${f.lead_id} · ${lead?.business_name || lead?.nombre || 'sin nombre'} · ${f.decision} · confianza ${f.confianza.toFixed(2)} · ${f.creado_en}`);
      if (f.decision === 'necesidad') {
        console.log(`  bot: ${etiquetaBot(f.bot?.business_type ?? null)}   jev: ${f.jev?.necesidad?.choice}`);
      } else {
        console.log(`  p(inventa)=${f.jev?.inventa?.noul}  situacion: ${f.bot?.situacion}`);
        console.log(`  mensaje: ${String(f.bot?.texto || '').replace(/\s+/g, ' ').slice(0, 160)}`);
      }
      for (const m of mensajes.all(f.lead_id).reverse()) {
        console.log(`    ${m.direction === 'in' ? 'lead' : 'bot '} | ${String(m.body).replace(/\s+/g, ' ').slice(0, 110)}`);
      }
      console.log('');
    }
  }

  console.log('COMO LEERLO');
  console.log('  El acuerdo NO es precision. El bot es el que se venia equivocando: el 11/9 guardo');
  console.log('  "pagina web" cuando el lead habia dicho lo que HACE, y el 12/9 califico con un "Sii".');
  console.log('  En esos dos casos lo correcto es que Jev NO coincida. Por eso el numero que importa');
  console.log('  no es el acuerdo sino cuantos de los desacuerdos de arriba son Jev teniendo razon.');
  db.close();
}

main();
