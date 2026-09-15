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

  /**
   * Los que cuentan como contestarle al lead, y por lo tanto no esperan al
   * horario comercial.
   *
   * Es una lista aparte de CONVERSACIONALES a proposito: esa dice que se
   * DESCARTA cuando el lead vuelve a escribir, y el saludo no se descarta nunca
   * —si se pierde, el lead nunca se entera de quien le habla—.
   *
   * El saludo entro aca el 14-9. Susana escribio un domingo 18:53, el bot le
   * contesto al instante y charlaron; al otro dia 09:19 le llego "¡Buenas! Soy
   * el agente comercial de Scalerics, la idea es hacerte unas preguntas
   * introductorias", despues de toda la conversacion. A CD Montevideo le paso
   * igual un sabado 07:04: converso, agendo, y a las 09:20 le llego la
   * presentacion.
   *
   * El saludo del formulario NO se ve afectado y sigue esperando al horario:
   * `esRespuesta` pide ademas que el lead haya escrito recien, y el del
   * formulario sale sin que nadie haya escrito nada. La distincion ya estaba;
   * faltaba dejar pasar el kind.
   */
  const CONTESTAN = new Set(['manual', 'welcome']);

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

  /**
   * Un aviso interno que no tiene que salir.
   *
   * Los avisos al equipo no pasan por los topes anti-baneo, y eso esta bien
   * —son contacto interno—, pero los dejaba sin techo de ningun tipo. Un bucle
   * en el vigilante de reservas mando 489 avisos en dos dias: 5 textos
   * repetidos 98 veces cada uno, toda la noche, y nada lo freno.
   *
   * Dos cortes, y hacen falta los dos. El texto identico ataja el bucle exacto:
   * un aviso lleva el nombre, la hora y lo que dijo el lead, asi que dos
   * iguales son el mismo aviso dos veces. El tope por hora ataja el bucle que
   * ademas varie el texto, que el primero no veria.
   */
  /** Formato de SQLite, que es contra lo que se comparan las fechas guardadas. */
  const haceMs = (ms) => new Date(ahora().getTime() - ms).toISOString().slice(0, 19).replace('T', ' ');

  function avisoQueSobra(item) {
    /**
     * El guardia que de verdad hacia falta.
     *
     * El incidente de los 489 mensajes fue un goteo: 24 por hora durante dos
     * dias. Ninguna alarma de volumen lo agarra, porque el volumen total era
     * normal. Lo anormal era a QUIEN: todos al mismo telefono.
     *
     * Vale para todos los mensajes, no solo los internos: ochenta mensajes en
     * un dia a la misma persona no es una conversacion, es algo trabado.
     */
    if (cfg.MAX_POR_DESTINATARIO_DIA
      && repo.enviadosA(item.to, haceMs(24 * 3600_000)) >= cfg.MAX_POR_DESTINATARIO_DIA) {
      return 'demasiados mensajes a la misma persona en un dia';
    }

    /**
     * Al lead SI se le puede repetir un mensaje, y es a proposito.
     *
     * Se probo cortarlo —el bot habia contestado dos veces palabra por palabra
     * "Confirmado, Gonza. Nos vemos pronto en la llamada"— pero el corte deja
     * al lead sin respuesta, y eso es peor. Si alguien contesta algo que no se
     * entiende y hay que volver a preguntarle lo mismo, el bot tiene que poder.
     *
     * Un mensaje repetido queda feo; el silencio pierde al lead.
     */
    if (!INTERNO.has(item.kind)) return null;

    if (cfg.AVISO_REPETIDO_HORAS
      && repo.avisoIdenticoReciente(item.texto, cfg.AVISO_REPETIDO_HORAS)) {
      return 'ya salio uno igual';
    }

    if (cfg.MAX_INTERNOS_PER_HOUR
      && repo.internosDesde(haceMs(3600_000)) >= cfg.MAX_INTERNOS_PER_HOUR) {
      return 'demasiados avisos en una hora';
    }
    return null;
  }

  async function procesar(item) {
    const sobra = avisoQueSobra(item);
    if (sobra) {
      logger?.warn(
        { kind: item.kind, leadId: item.leadId, motivo: sobra },
        'aviso al equipo descartado: algo lo esta repitiendo'
      );
      return;
    }

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
      destino: item.to,
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

  /**
   * Guarda un mensaje que quedo esperando, o corre la hora del que ya estaba.
   * @returns {number|null} el id con el que quedo guardado
   */
  function persistir(item) {
    if (!repo?.guardarSaliente) return item.salienteId ?? null;
    try {
      if (item.salienteId) {
        repo.correrSaliente(item.salienteId, item.noAntesDe);
        return item.salienteId;
      }
      return repo.guardarSaliente(item);
    } catch (e) {
      // Que no se pueda guardar no puede tumbar el envio: en el peor caso se
      // comporta como antes, que es perderlo si el servicio se reinicia.
      logger?.error({ err: String(e.message || e) }, 'no se pudo guardar el saliente que espera');
      return item.salienteId ?? null;
    }
  }

  /** Ya no espera mas: salio, se descarto, o dejo de tener sentido. */
  function olvidar(item) {
    if (!item?.salienteId || !repo?.borrarSaliente) return;
    try {
      repo.borrarSaliente(item.salienteId);
    } catch (e) {
      logger?.error({ err: String(e.message || e) }, 'no se pudo borrar el saliente ya enviado');
    }
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
          // Es una respuesta si el lead escribio recien. No hace falta que
          // nadie lo marque al encolar: se mira la conversacion, que es lo que
          // define si esto contesta algo o aparece de la nada.
          esRespuesta: CONTESTAN.has(item.kind)
            && repo.escribioHaceMenos(item.leadId, cfg.RESPUESTA_VENTANA_MIN, ahora()),
          // Lo que el lead reservo: un recordatorio de su propia reunion no es
          // salida en frio y no puede esperar a la apertura, porque para
          // entonces ya no sirve.
          //
          // Lo que escribe una persona desde el panel del CRM tampoco espera. El
          // 14-9 Juan escribio a las 22:36 y la cola se lo guardo hasta las 9 de
          // la manana: la decision de mandarlo a esa hora la tomo el, no el bot.
          // Los topes por hora y por dia siguen corriendo igual.
          esAcordado: item.acordado === true || item.humano === true,
          ahora: ahora(),
        });

        if (!veredicto.ok) {
          const demoraMin = Math.round((veredicto.reintentarEn - ahora()) / 60_000);

          /**
           * Hay mensajes que solo sirven en el momento.
           *
           * "No te entendi el archivo" contestado trece horas despues es ruido:
           * el lead no tiene forma de saber a que se refiere. Paso el 2-9 con
           * un sticker que llego 22:36, fuera del horario de envio: la cola lo
           * reprogramo para la manana siguiente, como hace con todo.
           *
           * La regla de horario esta bien —no se le escribe a nadie a las 3am—
           * pero un mensaje reactivo que no sale a tiempo no hay que guardarlo:
           * hay que tirarlo.
           */
          if (item.venceEnMin) {
            const nacido = item.encoladoEn ? new Date(item.encoladoEn) : ahora();
            const vence = nacido.getTime() + item.venceEnMin * 60_000;
            if (veredicto.reintentarEn.getTime() > vence) {
              logger?.info(
                { kind: item.kind, motivo: veredicto.motivo, demoraMin },
                'mensaje descartado: ya no sirve tan tarde'
              );
              olvidar(item);
              continue;
            }
          }

          logger?.warn(
            { kind: item.kind, motivo: veredicto.motivo, demoraMin },
            'mensaje reprogramado, no descartado'
          );
          // Y se guarda, porque a partir de aca puede tener que esperar horas.
          // El 6-9 dos bienvenidas esperaron desde el domingo de madrugada hasta
          // el lunes a las 9, la maquina se reciclo en el medio, y las dos se
          // perdieron sin dejar rastro.
          const guardado = persistir({ ...item, noAntesDe: veredicto.reintentarEn });
          items.push({
            ...item, delayMs: 0, noAntesDe: veredicto.reintentarEn,
            reprogramado: true, salienteId: guardado,
          });
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
          // Salio, o fallo y quedo registrado como fallido. En los dos casos ya
          // no espera nada: si sigue guardado, el proximo arranque lo repite.
          olvidar(item);
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

  /**
   * Lo que quedo esperando en la corrida anterior.
   *
   * La cola vive en memoria y eso alcanza para lo que sale en segundos. Lo que
   * espera al horario comercial puede esperar treinta horas, y en el medio la
   * maquina se recicla. Ver la migracion 019.
   */
  if (repo?.salientesPendientes) {
    try {
      for (const guardado of repo.salientesPendientes()) items.push({ ...guardado, seq: seq++ });
      if (items.length) {
        logger?.info({ cuantos: items.length }, 'se retoman los mensajes que quedaron esperando');
        queueMicrotask(() => loop().catch((e) => logger?.error({ err: String(e) }, 'loop de cola')));
      }
    } catch (e) {
      logger?.error({ err: String(e.message || e) }, 'no se pudieron leer los mensajes que esperaban');
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
      const sobra = (i) => i.leadId === leadId && CONVERSACIONALES.has(i.kind);
      // Los que estaban guardados por haber quedado esperando se borran tambien:
      // si no, el proximo arranque los revive despues de haberlos descartado.
      for (const i of items) if (sobra(i)) olvidar(i);
      items = items.filter((i) => !sobra(i));

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
