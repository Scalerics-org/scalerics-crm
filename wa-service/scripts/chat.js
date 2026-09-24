#!/usr/bin/env node
'use strict';

/**
 * Conversar con el bot desde la terminal, sin tocar WhatsApp.
 *
 * Levanta el servicio entero —la IA de verdad, el embudo de verdad, el
 * calendario si esta configurado— pero con el proveedor mock: en vez de mandar
 * los mensajes a WhatsApp, los imprime aca.
 *
 * Sirve para lo que hasta ahora habia que probar contra el numero real de la
 * empresa: cada prueba salia del mismo WhatsApp que usan los clientes, gastaba
 * cupo anti-baneo y dejaba conversaciones que despues habia que reiniciar.
 *
 *   node scripts/chat.js                  arranca de cero
 *   node scripts/chat.js --tel 099111222  con otro numero
 *   node scripts/chat.js --nombre Ana     con otro nombre de perfil
 *   node scripts/chat.js --seguir         no borra lo de la corrida anterior
 *
 * Adentro:
 *   /estado    que sabe el bot de vos y en que parte del embudo esta
 *   /jobs      lo que quedo programado (seguimientos, recordatorios, pausas)
 *   /reiniciar volver a empezar
 *   /salir
 */

const path = require('node:path');
const fs = require('node:fs');
const readline = require('node:readline');

// La base de dev va aparte de la de produccion, y por defecto se borra en cada
// corrida: probar tiene que ser barato, y arrastrar estado de la vez anterior
// es como se llega a "me anda distinto y no se por que".
const RAIZ = path.join(__dirname, '..');

const args = process.argv.slice(2);
const tomar = (nombre, porDefecto) => {
  const i = args.indexOf(nombre);
  return i >= 0 && args[i + 1] ? args[i + 1] : porDefecto;
};
const TELEFONO = tomar('--tel', '59899000001');

// Una base por numero, no una sola compartida: asi se pueden tener dos chats
// abiertos a la vez —probar el mismo cambio como dos personas distintas— sin
// que uno le pise el archivo al otro.
const BASE = path.join(RAIZ, 'data', `chat-${TELEFONO.replace(/\D/g, '')}.db`);
// El nombre va al perfil de WhatsApp, y el bot lo usa para saludar. Con un
// placeholder como "Vos" el saludo sale "Genial, Vos" y arruina la prueba.
const NOMBRE = tomar('--nombre', 'Gonza');
const SEGUIR = args.includes('--seguir');

fs.mkdirSync(path.dirname(BASE), { recursive: true });
if (!SEGUIR) {
  try {
    // El -wal tambien: borrar solo el .db y dejar el journal es como se vacia
    // una base sin querer.
    for (const f of [BASE, `${BASE}-wal`, `${BASE}-shm`]) {
      if (fs.existsSync(f)) fs.unlinkSync(f);
    }
  } catch (e) {
    // En Windows no se puede borrar un archivo que otro proceso tiene abierto.
    // Antes esto salia como un stack trace de fs.unlinkSync, que no le dice a
    // nadie que lo unico que pasa es que hay otro chat abierto.
    console.error(`\n  No se pudo empezar de cero: ${path.basename(BASE)} está en uso.`);
    console.error('  Cerrá el otro chat, o abrí este con otro número:\n');
    console.error('    npm run chat -- --tel 099222333\n');
    process.exit(1);
  }
}

// Antes de cargar la config: el proveedor mock y la base de dev mandan.
process.env.WA_PROVIDER = 'mock';
process.env.DB_PATH = BASE;
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent';
// Sin esperas: en la terminal no hay a quien disimularle que hay un bot.
process.env.AGRUPAR_ENTRANTES_MS = '0';
process.env.TYPING_ENABLED = 'false';
process.env.DELAY_AM_MIN_MS = '0';
process.env.DELAY_AM_MAX_MS = '0';
process.env.DELAY_WELCOME_MIN_MS = '0';
process.env.DELAY_WELCOME_MAX_MS = '0';
process.env.DELAY_BETWEEN_MIN_MS = '0';
process.env.DELAY_BETWEEN_MAX_MS = '0';
// Ni topes ni horario comercial: si no, probar a las once de la noche no anda.
process.env.MAX_MSGS_PER_HOUR = '100000';
process.env.MAX_MSGS_PER_DAY = '100000';
process.env.MAX_NEW_CONTACTS_PER_HOUR = '100000';
process.env.BUSINESS_HOURS = '00:00-23:59';
process.env.BUSINESS_DAYS = 'mon-sun';
// El aviso al equipo se ve en pantalla como [equipo], no se le manda a nadie.
process.env.AM_PHONES = process.env.AM_PHONES || '59899000111';

const { cargar } = require('../src/config');
const { construir } = require('../src/app');

const C = {
  gris: (t) => `\x1b[90m${t}\x1b[0m`,
  verde: (t) => `\x1b[32m${t}\x1b[0m`,
  cyan: (t) => `\x1b[36m${t}\x1b[0m`,
  amarillo: (t) => `\x1b[33m${t}\x1b[0m`,
  negrita: (t) => `\x1b[1m${t}\x1b[0m`,
};

async function main() {
  const cfg = cargar();
  const s = construir(cfg);
  await s.proveedor.conectar();

  const esAM = (to) => cfg.amPhones.includes(to);
  let mostrados = 0;

  /** Imprime lo que el bot mando desde la ultima vez. */
  function mostrarSalida() {
    const todos = s.proveedor.getEnviados();
    for (const m of todos.slice(mostrados)) {
      const cuerpo = String(m.texto);
      if (esAM(m.to)) {
        console.log(C.gris(`   [equipo] ${cuerpo.replace(/\n/g, '\n            ')}`));
      } else {
        console.log(C.verde(`   bot › ${cuerpo.replace(/\n/g, '\n         ')}`));
      }
    }
    mostrados = todos.length;
  }

  function estado() {
    const l = s.repo.leadPorTelefono(TELEFONO);
    if (!l) return console.log(C.gris('   (todavía no existe el lead)'));

    const campos = [
      ['estado', l.fsm_state],
      ['negocio', l.business_name],
      ['rubro', l.rubro],
      ['tipo de proyecto', l.business_type],
      ['necesita', l.needs],
      ['reunión', l.meeting_time],
      ['en pausa desde', l.nurture_desde],
      ['motivo de pausa', l.nurture_motivo],
      ['no es cliente', l.no_cliente_motivo],
      ['derivado por', l.motivo_derivacion],
      ['consultas de precio', l.consultas_precio],
    ].filter(([, v]) => v !== null && v !== undefined && v !== '' && v !== 0);

    console.log(C.cyan('   ── qué sabe el bot ──'));
    for (const [k, v] of campos) console.log(C.cyan(`   ${k.padEnd(20)} ${v}`));
  }

  function jobs() {
    const l = s.repo.leadPorTelefono(TELEFONO);
    if (!l) return console.log(C.gris('   (nada)'));
    const filas = s.repo.db
      .prepare("SELECT type, run_at FROM jobs WHERE lead_id = ? AND status = 'pending' ORDER BY run_at")
      .all(l.id);
    if (!filas.length) return console.log(C.gris('   (nada programado)'));
    console.log(C.cyan('   ── programado ──'));
    for (const f of filas) console.log(C.cyan(`   ${f.type.padEnd(14)} ${f.run_at}`));
  }

  console.log('');
  console.log(`${C.negrita('  Chat con el bot')}${C.gris(`  ·  no toca WhatsApp  ·  ${TELEFONO}`)}`);
  console.log(C.gris(`  como ${NOMBRE}  ·  /estado  /jobs  /reiniciar  /salir`));
  console.log(C.gris(`  ${'─'.repeat(58)}`));
  console.log('');

  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  rl.setPrompt(C.amarillo('  vos › '));
  rl.prompt();

  // Se leen las lineas con for-await y no con rl.on('line'): el manejador de
  // evento no espera a que termine el turno, asi que con varias lineas seguidas
  // el proceso llegaba al final antes de imprimir las respuestas.
  let n = 0;
  for await (const linea of rl) {
    const texto = linea.trim();

    if (texto === '/salir') break;
    if (!texto) { rl.prompt(); continue; }

    if (texto === '/estado') { estado(); rl.prompt(); continue; }
    if (texto === '/jobs') { jobs(); rl.prompt(); continue; }
    if (texto === '/reiniciar') {
      const l = s.repo.leadPorTelefono(TELEFONO);
      if (l) s.repo.reiniciarLead(l.id, new Date().toISOString());
      s.proveedor.limpiar();
      mostrados = 0;
      console.log(C.gris('   (reiniciado)'));
      rl.prompt();
      continue;
    }

    try {
      // Entra por el mismo camino que un mensaje de WhatsApp: proveedor ->
      // agrupador -> embudo. Probar por un atajo no probaria lo mismo.
      //
      // El id lleva un contador ademas de la hora: dos mensajes en el mismo
      // milisegundo compartirian id, y el dedup de entrantes tomaria el segundo
      // por un reenvio de WhatsApp y lo tiraria.
      n += 1;
      s.proveedor.simularEntrante({
        from: TELEFONO, texto, nombre: NOMBRE, id: `chat-${Date.now()}-${n}`,
      });
      await s.agrupador.vaciar();
      await s.cola.vacia();
    } catch (e) {
      console.log(C.gris(`   [error] ${e.message}`));
    }

    mostrarSalida();
    rl.prompt();
  }

  rl.close();
  console.log(C.gris('\n  chau\n'));
}

main().catch((e) => {
  console.error('\n  No se pudo arrancar:', e.message);
  if (/ANTHROPIC|OPENAI/i.test(e.message)) {
    console.error('  Falta ANTHROPIC_API_KEY en wa-service/.env');
    console.error('  (OPENAI_API_KEY es aparte, y solo hace falta para los audios)\n');
  }
  process.exit(1);
});
