'use strict';

/**
 * Jev en modo sombra: contesta, se anota, y no cambia nada.
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
 * Todo corre en segundo plano: quien llama no espera. `vaciar()` existe para
 * los tests, que si necesitan esperar a que se anote.
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

function crearSombra({ jev, repo, logger = null }) {
  const activa = Boolean(jev?.activo);
  const enCurso = new Set();

  /** Corre sin que nadie espere, y nunca deja una promesa rechazada suelta. */
  function enSegundoPlano(fn) {
    const p = Promise.resolve()
      .then(fn)
      .catch((e) => logger?.warn({ err: String(e.message || e) }, 'fallo el modo sombra de jev'))
      .finally(() => enCurso.delete(p));
    enCurso.add(p);
  }

  async function necesidad(lead) {
    const conversacion = repo.conversacionParaSombra(lead.id);
    if (!conversacion.some((m) => m.de === 'lead')) return;

    const r = await jev.preguntar(conversacion, {
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
    });
    if (!r) return;

    const a = r.answers.necesidad;
    const tipoJev = NECESIDAD[a?.choice]?.tipo ?? null;
    // Para el bot, no tener tipo y "no sabe" son lo mismo: no sabe que necesita.
    const tipoBot = lead.business_type ?? 6;
    const coincide = tipoJev === null ? null : tipoJev === tipoBot;

    repo.registrarSombra({
      leadId: lead.id, decision: 'necesidad',
      bot: { business_type: lead.business_type ?? null },
      jev: r.answers, coincide, confianza: a?.confidence ?? null, ms: r.ms,
    });
    if (coincide === false) {
      logger?.info(
        { leadId: lead.id, bot: lead.business_type ?? null, jev: a.choice, confianza: a.confidence },
        'jev (sombra) no coincide con la necesidad que guardo el bot'
      );
    }
  }

  async function oferta(lead, situacion, texto) {
    const conversacion = repo.conversacionParaSombra(lead.id);
    const r = await jev.preguntar(
      { conversacion, mensaje_del_bot: texto },
      {
        inventa: {
          type: 'noul',
          instructions: '¿El mensaje del bot le atribuye al lead una necesidad, un problema o un objetivo que el lead no dijo en la conversación?',
          criteria: {
            true: 'Afirma algo del lead que el lead no dijo',
            false: 'Todo lo que dice del lead sale de la conversación',
          },
        },
      }
    );
    if (!r) return;

    const p = r.answers.inventa?.noul;
    repo.registrarSombra({
      leadId: lead.id, decision: 'oferta',
      bot: { situacion, texto },
      jev: r.answers,
      // El bot lo mando: para el bot no inventaba nada.
      coincide: typeof p === 'number' ? p < 0.5 : null,
      confianza: typeof p === 'number' ? Math.abs(p - 0.5) * 2 : null,
      ms: r.ms,
    });
    if (typeof p === 'number' && p >= 0.5) {
      logger?.info({ leadId: lead.id, situacion, probabilidad: p }, 'jev (sombra) cree que la oferta inventa algo del lead');
    }
  }

  return {
    activa,
    alCalificar(lead) {
      if (activa && lead) enSegundoPlano(() => necesidad(lead));
    },
    alMandarIA(lead, situacion, texto) {
      if (activa && lead && SITUACIONES_DE_OFERTA.has(situacion)) enSegundoPlano(() => oferta(lead, situacion, texto));
    },
    async vaciar() {
      while (enCurso.size) await Promise.all([...enCurso]);
    },
  };
}

module.exports = { crearSombra, NECESIDAD, SITUACIONES_DE_OFERTA };
