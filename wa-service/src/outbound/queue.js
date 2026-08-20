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
  let despertador = null;
  // El mensaje que el worker tiene en la mano. Ya salio de `items`, asi que
  // descartarPendientesDe no lo encuentra: se lo marca por aca.
  let enProceso = null;

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

  /**
   * Vuelve a arrancar la cola cuando le toque al mensaje reprogramado mas
   * proximo.
   *
   * Sin esto un mensaje frenado —por el tope de envios, por el horario o por el
   * circuit breaker— se queda quieto indefinidamente: el loop corta al no
   * quedar nada que pueda salir AHORA y nadie lo vuelve a llamar. Salia recien
   * cuando alguien encolaba otra cosa, y si no entraba ningun mensaje mas no
   * salia nunca.
   *
   * Estuvo dormido todo este tiempo porque el tope por hora no frenaba nada
   * —comparaba dos formatos de fecha distintos y contaba cero—, asi que casi
   * nunca se reprogramaba nada. Al arreglar el contador quedo al descubierto.
   */
  function programarDespertar() {
    if (despertador) {
      clearTimeout(despertador);
      despertador = null;
    }
    if (corriendo) return;

    const momentos = items.map((i) => i.noAntesDe).filter(Boolean);
    // Con la cola pausada, lo que no es interno espera a que cierre el breaker.
    if (estaPausada() && items.some((i) => !INTERNO.has(i.kind))) momentos.push(pausadaHasta);
    if (!momentos.length) return;

    const cuando = Math.min(...momentos.map((d) => new Date(d).getTime()));
    // El margen evita despertar justo en el filo y encontrarse con que todavia
    // falta un milisegundo, que dejaria la cola dormida de nuevo.
    const enMs = Math.max(cuando - ahora().getTime(), 0) + 250;

    despertador = setTimeout(() => {
      despertador = null;
      loop().catch((e) => logger?.error({ err: String(e) }, 'loop de cola'));
    }, enMs);
    // Que un mensaje reprogramado no impida apagar el proceso.
    despertador.unref?.();
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

    await simularEscritura(proveedor, item.to, item.texto, cfg, INTERNO.has(item.kind));

    // Mientras duraba el "escribiendo...", el lead escribio de nuevo y ya hay
    // una respuesta mejor en camino. Se chequea aca, despues de la espera y
    // antes de registrar nada: si se mandara igual, llegarian las dos y la
    // primera hablaria de algo que el lead ya dijo.
    if (item.cancelado) {
      logger?.info({ kind: item.kind, leadId: item.leadId }, 'respuesta descartada en el ultimo momento: el lead escribio de nuevo');
      return;
    }

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
      // Los avisos al equipo NO cuentan para el cupo anti-baneo.
      //
      // Ya salteaban el limite —limits.js los deja pasar siempre— pero igual se
      // anotaban, asi que gastaban cupo de los mensajes a clientes. Cada
      // conversacion con un lead consumia doble: su respuesta y la ficha al
      // equipo. Con 30 por hora eso son ~4 conversaciones nuevas antes de
      // frenarse, y las que se frenan son las de los clientes.
      //
      // El cupo esta para limitar los mensajes a desconocidos. Un mensaje al
      // numero del propio equipo —un contacto guardado, con conversacion
      // abierta hace meses— es lo contrario de la senial que se quiere evitar.
      if (!INTERNO.has(item.kind)) repo.registrarEnvio(item.to, esPrimerContacto);
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
          enProceso = item;
          await procesar(item);
        } catch {
          // El fallo ya quedo en messages.status='failed', en el log y en el
          // contador del circuit breaker. No se corta la cola por uno.
        } finally {
          enProceso = null;
        }

        if (items.length) {
          await dormir(jitter(cfg.DELAY_BETWEEN_MIN_MS, cfg.DELAY_BETWEEN_MAX_MS));
        }
      }
    } finally {
      corriendo = false;
      notificarVacio();
      programarDespertar();
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
     * Entre dos mensajes hay unos segundos de espera, y un lead que contesta
     * rapido escribe de nuevo antes de que salga la respuesta anterior. Ahi la
     * conversacion se desordena: el bot le pregunta el nombre del negocio
     * despues de que ya se lo dijo, porque esa pregunta se escribio antes y
     * estaba esperando turno.
     *
     * Al acortar las esperas esto protege menos que antes —hay menos cosas
     * atrapadas en la cola para descartar— y por eso el agrupador de entrantes
     * no puede bajar mucho mas: es la otra mitad de la misma defensa.
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

      // El que ya esta en el "escribiendo..." no esta en `items` y se escapaba
      // por ahi. Con las esperas cortas esa ventana es la que mas importa: el
      // worker agarra la respuesta casi al instante de encolarse.
      if (enProceso && enProceso.leadId === leadId && CONVERSACIONALES.has(enProceso.kind)) {
        enProceso.cancelado = true;
      }

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
    /** Si hay un reintento armado. Sin esto no habria como ver que no quedo dormida. */
    tieneDespertador: () => Boolean(despertador),
    pausada: () => estaPausada(),
    /** Callback para avisar al AM cuando se abre el breaker. */
    onPausa(fn) { alPausar = fn; },
  };
}

module.exports = { crearCola, entre };
