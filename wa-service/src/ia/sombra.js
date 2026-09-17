'use strict';

/**
 * Jev dentro del embudo, en dos modos.
 *
 * `sombra`: contesta, se anota y no cambia nada. Es como se mide, con
 * conversaciones de verdad, si conviene dejarlo decidir (`npm run jev:informe`).
 *
 * `decide`: ademas de anotarse, su respuesta manda cuando la confianza alcanza
 * el umbral. Nunca al reves: por debajo del umbral, o si Jev no contesta, el
 * bot hace exactamente lo que hacia antes.
 *
 * Son dos decisiones, las dos salidas de conversaciones reales en las que el
 * modelo que conversa se equivoco y el embudo lo dejo pasar:
 *
 * 1. `necesidad` — al dar a un lead por calificado. El 11-9 el bot pregunto
 *    "¿qué tipo de software desarrollan? ¿páginas web…?", el lead contesto
 *    "Webs" —lo que ÉL hace— y quedo guardado como que necesitaba una pagina
 *    web. El 12-9 otro contesto "Sii" a una pregunta de cinco opciones. Los
 *    dos recibieron la oferta de reunion y el equipo fue sin saber que querian.
 *
 * 2. `oferta` — el mensaje que ofrece la reunion. En los dos casos de arriba
 *    arranco con "Entendí que necesitás…" y algo que el lead nunca dijo.
 *
 * En modo sombra todo corre en segundo plano y quien llama no espera. En modo
 * decide hay que esperar la respuesta —no se puede decidir sin ella— y por eso
 * el cliente trae su propio timeout: pasado ese tope se sigue sin Jev.
 */

/** Lo que Jev elige, y a que business_type del bot corresponde. */
const NECESIDAD = {
  pagina_web: { criterio: 'Una página web para mostrar el negocio', tipo: 1 },
  ecommerce: { criterio: 'Una tienda online para vender productos', tipo: 2 },
  automatizacion: { criterio: 'Automatizar tareas o procesos', tipo: 3 },
  sistema_a_medida: { criterio: 'Un sistema a medida, por ejemplo reservas, turnos o gestión', tipo: 4 },
  agente_ia: { criterio: 'Un agente de IA que atienda o venda', tipo: 5 },
  no_queda_claro: {
    criterio: 'No dijo qué necesita, dijo que no sabe, o lo que dijo describe a qué se dedica su negocio y no lo que necesita',
    tipo: 6,
  },
};

const SITUACIONES_DE_OFERTA = new Set(['oferta_con_horarios', 'oferta_reunion', 'link_reunion']);

const PREGUNTA_NECESIDAD = {
  necesidad: {
    type: 'choice',
    instructions: '¿Qué necesita el lead de la agencia?',
    criteria: Object.fromEntries(Object.entries(NECESIDAD).map(([k, v]) => [k, v.criterio])),
  },
  es_cliente: {
    type: 'noul',
    instructions: '¿Quien escribe busca contratar un servicio de la agencia?',
    criteria: { true: 'Quiere contratar algo', false: 'Viene a vender algo, busca trabajo, o es otra cosa' },
  },
};

const PREGUNTA_OFERTA = {
  inventa: {
    type: 'noul',
    instructions: '¿El mensaje del bot le atribuye al lead una necesidad, un problema o un objetivo que el lead no dijo en la conversación?',
    criteria: {
      true: 'Afirma algo del lead que el lead no dijo',
      false: 'Todo lo que dice del lead sale de la conversación',
    },
  },
};

function crearSombra({ jev, repo, logger = null, modo = 'apagado', umbral = 0.8, presupuestoMs = 3000 }) {
  const activa = Boolean(jev?.activo) && modo !== 'apagado';
  const decide = activa && modo === 'decide';
  const enCurso = new Set();
  /**
   * Leads cuya necesidad ya se decidio en este turno, para no preguntar dos
   * veces lo mismo: al cerrar el descubrimiento se decide, y un rato despues el
   * embudo entra a SCORED y llama a `alCalificar`.
   */
  const yaDecididos = new Set();

  /** Corre sin que nadie espere, y nunca deja una promesa rechazada suelta. */
  function enSegundoPlano(fn) {
    const p = Promise.resolve()
      .then(fn)
      .catch((e) => logger?.warn({ err: String(e.message || e) }, 'fallo el modo sombra de jev'))
      .finally(() => enCurso.delete(p));
    enCurso.add(p);
  }

  /**
   * Pregunta por la necesidad y anota la respuesta.
   * @returns {Promise<{choice: string, tipo: number|null, confianza: number}|null>}
   */
  async function necesidad(lead) {
    const conversacion = repo.conversacionParaSombra(lead.id);
    if (!conversacion.some((m) => m.de === 'lead')) return null;

    const r = await jev.preguntar(conversacion, PREGUNTA_NECESIDAD);
    if (!r) return null;

    const a = r.answers.necesidad;
    const tipoJev = NECESIDAD[a?.choice]?.tipo ?? null;
    // Para el bot, no tener tipo y "no sabe" son lo mismo: no sabe que necesita.
    const tipoBot = lead.business_type ?? NECESIDAD.no_queda_claro.tipo;
    const coincide = tipoJev === null ? null : tipoJev === tipoBot;

    repo.registrarSombra({
      leadId: lead.id, decision: 'necesidad',
      bot: { business_type: lead.business_type ?? null },
      jev: r.answers, coincide, confianza: a?.confidence ?? null,
      ms: r.ms, inputTokens: r.usage?.input_tokens ?? null,
    });
    if (coincide === false) {
      logger?.info(
        { leadId: lead.id, bot: lead.business_type ?? null, jev: a.choice, confianza: a.confidence },
        'jev no coincide con la necesidad que guardo el bot'
      );
    }
    return { choice: a?.choice ?? null, tipo: tipoJev, confianza: a?.confidence ?? 0 };
  }

  /**
   * Pregunta si el mensaje le atribuye al lead algo que no dijo, y lo anota.
   * @returns {Promise<{probabilidad: number}|null>}
   */
  async function oferta(lead, situacion, texto, { intento = 1 } = {}) {
    const conversacion = repo.conversacionParaSombra(lead.id);
    const r = await jev.preguntar({ conversacion, mensaje_del_bot: texto }, PREGUNTA_OFERTA);
    if (!r) return null;

    const p = r.answers.inventa?.noul;
    repo.registrarSombra({
      leadId: lead.id, decision: 'oferta',
      bot: { situacion, texto, intento },
      jev: r.answers,
      // En sombra el bot lo mando: para el bot no inventaba nada.
      coincide: typeof p === 'number' ? p < umbral : null,
      confianza: typeof p === 'number' ? Math.abs(p - 0.5) * 2 : null,
      ms: r.ms, inputTokens: r.usage?.input_tokens ?? null,
    });
    if (typeof p === 'number' && p >= umbral) {
      logger?.info({ leadId: lead.id, situacion, probabilidad: p, intento }, 'jev cree que la oferta inventa algo del lead');
    }
    return typeof p === 'number' ? { probabilidad: p, ms: r.ms } : null;
  }

  return {
    activa,
    decide,
    umbral,
    presupuestoMs,

    /**
     * Al dar por calificado: se anota, sin decidir nada.
     *
     * En decide, si la decision ya se tomo al cerrar el descubrimiento, esto no
     * vuelve a preguntar. Pero hay un camino que llega a calificado sin pasar
     * por ahi —el guardia de promesas, cuando el modelo se pone a agendar solo—
     * y por ese sigue quedando la anotacion: es justo un lead que califico sin
     * que nadie revisara que necesita, o sea el que mas interesa mirar despues.
     */
    alCalificar(lead) {
      if (!activa || !lead) return;
      if (decide && yaDecididos.delete(lead.id)) return;
      enSegundoPlano(() => necesidad(lead));
    },

    /** Modo sombra, despues de mandar la oferta. En decide se revisa antes. */
    alMandarIA(lead, situacion, texto) {
      if (activa && !decide && lead && SITUACIONES_DE_OFERTA.has(situacion)) {
        enSegundoPlano(() => oferta(lead, situacion, texto));
      }
    },

    /**
     * Modo decide: que hacer con el lead que el embudo esta por dar por
     * calificado. Se espera la respuesta —no se puede decidir sin ella— con el
     * timeout del cliente como tope.
     *
     * @returns {Promise<{pisar?: number, repreguntar?: boolean}|null>} null si
     *   Jev no contesto, o si contesto con menos confianza que el umbral: en
     *   los dos casos el bot sigue como si Jev no existiera.
     */
    async decidirNecesidad(lead) {
      if (!decide || !lead) return null;
      let r = null;
      try {
        yaDecididos.add(lead.id);
        r = await necesidad(lead);
      } catch (e) {
        logger?.warn({ leadId: lead.id, err: String(e.message || e) }, 'fallo la decision de necesidad de jev');
        return null;
      }
      if (!r || r.tipo === null || r.confianza < umbral) return null;

      if (r.choice === 'no_queda_claro') return { repreguntar: true };
      // Ya guardaba eso mismo: no hay nada que pisar.
      if (r.tipo === (lead.business_type ?? NECESIDAD.no_queda_claro.tipo)) return null;
      return { pisar: r.tipo };
    },

    /**
     * Modo decide: si el mensaje de la oferta se puede mandar.
     *
     * `gastadoMs` es lo que Jev ya demoro en este turno. Pasado el presupuesto
     * no se pregunta de nuevo —el lead esta esperando— y se contesta bloquear:
     * el texto fijo no le atribuye nada, asi que no hace falta verificarlo.
     *
     * @returns {Promise<{bloquear: boolean, probabilidad?: number, ms: number}|null>}
     */
    async revisarOferta(lead, situacion, texto, opciones = {}) {
      if (!decide || !lead || !SITUACIONES_DE_OFERTA.has(situacion)) return null;
      const { gastadoMs = 0 } = opciones;
      if (gastadoMs >= presupuestoMs) {
        logger?.warn({ leadId: lead.id, situacion, gastadoMs }, 'sin tiempo para revisar el reintento: sale el texto fijo');
        return { bloquear: true, ms: 0, sinTiempo: true };
      }
      let r = null;
      try {
        r = await oferta(lead, situacion, texto, opciones);
      } catch (e) {
        logger?.warn({ leadId: lead.id, err: String(e.message || e) }, 'fallo la revision de la oferta de jev');
        return null;
      }
      if (!r) return null;
      return { bloquear: r.probabilidad >= umbral, probabilidad: r.probabilidad, ms: r.ms };
    },

    /** Para los tests: espera lo que quedo corriendo en segundo plano. */
    async vaciar() {
      while (enCurso.size) await Promise.all([...enCurso]);
    },
  };
}

module.exports = { crearSombra, NECESIDAD, SITUACIONES_DE_OFERTA, PREGUNTA_NECESIDAD, PREGUNTA_OFERTA };
