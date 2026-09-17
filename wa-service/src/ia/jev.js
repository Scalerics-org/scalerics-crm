'use strict';

/**
 * Cliente de Jev, el modelo de decisiones de TypeSafe.
 *
 * Jev no escribe texto: recibe un estado y preguntas tipadas —elegir una opcion
 * (choice), puntuar (score), si o no (noul)— y devuelve cada respuesta con
 * probabilidades y confianza. Es un solo endpoint HTTP, asi que va con fetch y
 * sin SDK.
 *
 * Igual que el aviso al CRM, nunca tira: una falla de Jev no puede tocar la
 * conversacion con el lead. Cualquier error termina en `null` y en el log.
 */
function crearJev({
  apiKey, url, modelo, timeoutMs = 5000, fetch: fetchImpl = globalThis.fetch, logger = null,
} = {}) {
  const activo = Boolean(apiKey && fetchImpl);

  return {
    activo,

    /**
     * @param {string|object|Array} state lo que Jev tiene que mirar.
     * @param {Record<string, object>} questions preguntas por nombre.
     * @returns {Promise<{answers: object, model: string, ms: number}|null>}
     */
    async preguntar(state, questions) {
      if (!activo) return null;
      const t0 = Date.now();
      try {
        const r = await fetchImpl(url, {
          method: 'POST',
          headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json; charset=utf-8' },
          body: JSON.stringify({ model: modelo, state, questions }),
          signal: AbortSignal.timeout(timeoutMs),
        });
        if (!r.ok) {
          logger?.warn({ status: r.status }, 'jev rechazo la consulta');
          return null;
        }
        const cuerpo = await r.json();
        if (!cuerpo?.answers) {
          logger?.warn('jev contesto sin answers');
          return null;
        }
        return { answers: cuerpo.answers, model: cuerpo.model || modelo, ms: Date.now() - t0 };
      } catch (e) {
        logger?.warn({ err: String(e.message || e) }, 'no se pudo consultar a jev');
        return null;
      }
    },
  };
}

module.exports = { crearJev };
