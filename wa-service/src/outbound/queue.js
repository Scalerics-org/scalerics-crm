'use strict';

const { entre, jitter, simularEscritura, dormir } = require('./humanize');

const PRIORIDAD = { am_notice: 0, welcome: 1, manual: 1, followup: 2 };
const INTERNO = new Set(['am_notice']);

/**
 * Cola de salida con un unico worker: nunca hay dos envios simultaneos.
 *
 * Orden: prioridad primero (la ficha al AM va antes que un follow-up), y a
 * igual prioridad, orden de llegada.
 *
 * Si los limites o el horario no dejan mandar, el mensaje NO se descarta: se
 * reprograma con noAntesDe y se sigue con el resto de la cola.
 */
function crearCola({ proveedor, repo, cfg, logger, limites, ahora = () => new Date() }) {
  let items = [];
  let corriendo = false;
  // Lo que queda obsoleto si el lead escribe de nuevo: las respuestas del bot.
  //
  // La bienvenida NO entra: es la presentacion y tiene que salir si o si, aunque
  // el lead haya escrito tres veces mientras esperaba. Los avisos al equipo y
  // los recordatorios tampoco: que el lead escriba no los invalida.
  const CONVERSACIONALES = new Set(['manual']);

  let esperandoVacio = [];
  let seq = 0;

  // Circuit breaker: si el proveedor empieza a fallar, se para en vez de
  // insistir. Reintentar en loop contra WhatsApp acelera el baneo.
  const fallosRecientes = [];
  let pausadaHasta = null;
  let alPausar = null;

  const estaPausada = () => Boolean(pausadaHasta && ahora() < pausadaHasta);

  /** Lo que puede salir ahora: pausada, solo pasan los avisos internos. */
  function listos() {
    const t = ahora();
    return items.filter(
      (i) => (!i.noAntesDe || i.noAntesDe <= t) && (!estaPausada() || INTERNO.has(i.kind))
    );
  }

  function notificarVacio() {
    if (!listos().length && !corriendo) {
      esperandoVacio.forEach((r) => r());
      esperandoVacio = [];
    }
  }

  function registrarFallo() {
    const t = ahora().getTime();
    fallosRecientes.push(t);
    const ventana = cfg.CIRCUIT_BREAKER_WINDOW_MIN * 60_000;
    while (fallosRecientes.length && t - fallosRecientes[0] > ventana) fallosRecientes.shift();

    if (fallosRecientes.length >= cfg.CIRCUIT_BREAKER_FAILS) {
      pausadaHasta = new Date(t + cfg.CIRCUIT_BREAKER_PAUSE_MIN * 60_000);
      fallosRecientes.length = 0;
      logger?.error(
        { hasta: pausadaHasta.toISOString() },
        'circuit breaker abierto: demasiados fallos seguidos, se pausa la cola'
      );
      alPausar?.(pausadaHasta);
    }
  }

  async function procesar(item) {
    const esPrimerContacto = !repo.yaFueContactado(item.to);

    await simularEscritura(proveedor, item.to, item.texto, cfg);

    const msgId = repo.registrarMensaje({
      lead_id: item.leadId ?? null,
      direction: 'out',
      kind: item.kind,
      body: item.texto,
      provider: proveedor.nombre,
      status: 'queued',
    });

    try {
      const res = await proveedor.enviarTexto(item.to, item.texto);
      repo.db.prepare('UPDATE messages SET status = ?, provider_msg_id = ? WHERE id = ?')
        .run('sent', res.id, msgId);
      repo.registrarEnvio(item.to, esPrimerContacto);
      logger?.info({ kind: item.kind, to: item.to, leadId: item.leadId }, 'mensaje enviado');
    } catch (e) {
      repo.db.prepare('UPDATE messages SET status = ?, error = ? WHERE id = ?')
        .run('failed', String(e.message || e), msgId);
      logger?.error({ kind: item.kind, to: item.to, err: String(e.message || e) }, 'envio fallido');
      registrarFallo();
      throw e;
    }
  }

  function siguiente() {
    const candidatos = listos();
    if (!candidatos.length) return null;
    candidatos.sort((a, b) => PRIORIDAD[a.kind] - PRIORIDAD[b.kind] || a.seq - b.seq);
    const elegido = candidatos[0];
    items.splice(items.indexOf(elegido), 1);
    return elegido;
  }

  async function loop() {
    if (corriendo) return;
    corriendo = true;
    try {
      for (;;) {
        if (pausadaHasta && ahora() >= pausadaHasta) pausadaHasta = null;

        // Con la cola pausada solo salen los avisos al AM: la alerta de que el
        // canal se cayo no puede quedar atrapada en la cola que se acaba de
        // pausar. Si el proveedor esta caido del todo tampoco llegara, y para
        // eso hace falta el canal de respaldo por mail, que todavia no existe.
        const item = siguiente();
        if (!item) break;

        if (item.delayMs) await dormir(item.delayMs);

        const veredicto = limites.permitido({
          esPrimerContacto: !repo.yaFueContactado(item.to),
          esInterno: INTERNO.has(item.kind),
          ahora: ahora(),
        });

        if (!veredicto.ok) {
          const demoraMin = Math.round((veredicto.reintentarEn - ahora()) / 60_000);
          logger?.warn(
            { kind: item.kind, motivo: veredicto.motivo, demoraMin },
            'mensaje reprogramado, no descartado'
          );
          items.push({ ...item, delayMs: 0, noAntesDe: veredicto.reintentarEn, reprogramado: true });
          continue;
        }

        try {
          await procesar(item);
        } catch {
          // El fallo ya quedo en messages.status='failed', en el log y en el
          // contador del circuit breaker. No se corta la cola por uno.
        }

        if (items.length) {
          await dormir(jitter(cfg.DELAY_BETWEEN_MIN_MS, cfg.DELAY_BETWEEN_MAX_MS));
        }
      }
    } finally {
      corriendo = false;
      notificarVacio();
    }
  }

  return {
    encolar(item) {
      items.push({ ...item, seq: seq++ });
      queueMicrotask(() => loop().catch((e) => logger?.error({ err: String(e) }, 'loop de cola')));
    },

    /**
     * Saca de la cola las respuestas a un lead que todavia no salieron.
     *
     * Entre dos mensajes pasan de 12 a 45 segundos —eso es lo que hace que no
     * parezca un bot— pero un lead que contesta rapido escribe de nuevo antes
     * de que salga la respuesta anterior. Ahi la conversacion se desordena: el
     * bot le pregunta el nombre del negocio despues de que ya se lo dijo,
     * porque esa pregunta estaba escrita hace treinta segundos y esperando
     * turno.
     *
     * La respuesta nueva la escribio el modelo viendo TODO el historial,
     * incluido el ultimo mensaje. La vieja quedo obsoleta en el momento en que
     * el lead volvio a escribir, asi que se descarta.
     *
     * Solo las conversacionales: los avisos al equipo y los recordatorios no
     * los invalida que el lead escriba.
     */
    descartarPendientesDe(leadId) {
      if (!leadId) return 0;
      const antes = items.length;
      items = items.filter(
        (i) => !(i.leadId === leadId && CONVERSACIONALES.has(i.kind))
      );
      const descartados = antes - items.length;
      if (descartados) {
        logger?.info({ leadId, descartados }, 'respuestas viejas descartadas: el lead escribio de nuevo');
      }
      return descartados;
    },

    /** Resuelve cuando no queda nada que pueda salir ahora. Para tests. */
    vacia() {
      if (!listos().length && !corriendo) return Promise.resolve();
      return new Promise((r) => esperandoVacio.push(r));
    },

    pendientes: () => items.length,
    reprogramados: () => items.filter((i) => i.reprogramado).length,
    pausada: () => estaPausada(),
    /** Callback para avisar al AM cuando se abre el breaker. */
    onPausa(fn) { alPausar = fn; },
  };
}

module.exports = { crearCola, entre };
