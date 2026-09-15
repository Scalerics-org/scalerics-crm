'use strict';

/**
 * Avisa si el link de Calendly que manda el bot no existe.
 *
 * El bot estuvo mandando /scalerics/diagnostico, que daba 404: el slug real es
 * otro. Nada fallaba —el mensaje salia perfecto, el embudo avanzaba, los logs
 * estaban limpios— y del otro lado el lead abria una pagina de error. Es el
 * peor tipo de rotura: la que no se nota desde adentro.
 *
 * Un chequeo al arrancar lo habria dicho el primer dia.
 */

const AGENTE = 'scalerics-wa/1.0';

/**
 * @returns {Promise<{ok: boolean|null, estado?: number}>} ok null significa que
 *   no se pudo verificar —sin red, Calendly caido—, que NO es lo mismo que el
 *   link estar mal. Confundirlos haria saltar la alarma cada vez que se cae la
 *   conexion, y una alarma que grita por nada deja de mirarse.
 */
async function verificarLink(url, { fetch: _fetch = globalThis.fetch, logger = null } = {}) {
  if (!url) return { ok: false };

  let r;
  try {
    r = await _fetch(url, { headers: { 'User-Agent': AGENTE }, redirect: 'follow' });
  } catch (e) {
    logger?.warn({ url, err: String(e.message || e) }, 'no se pudo verificar el link de Calendly');
    return { ok: null };
  }

  if (r.ok) {
    logger?.info({ url }, 'link de Calendly verificado');
    return { ok: true, estado: r.status };
  }

  logger?.error(
    { url, estado: r.status },
    'EL LINK DE CALENDLY NO EXISTE y el bot lo esta mandando igual'
  );
  return { ok: false, estado: r.status };
}

/**
 * Lo verifica y, si esta roto, se lo dice al equipo por WhatsApp.
 *
 * El aviso va aunque moleste en cada arranque: un log en Fly que nadie abre no
 * sirve de nada, y mientras el link este mal, cada lead que llega a la oferta
 * termina en una pagina de error.
 */
async function avisarSiEstaRoto({ cfg, cola, logger, fetch: _fetch = globalThis.fetch }) {
  const r = await verificarLink(cfg.CALENDLY_LINK, { fetch: _fetch, logger });
  if (r.ok !== false) return r;

  for (const am of cfg.amPhones) {
    cola.encolar({
      to: am,
      texto: `⚠️ El link de Calendly que manda el bot no funciona (${r.estado || 'sin respuesta'}):\n${cfg.CALENDLY_LINK}\n\nTodos los leads que lleguen a la reunión van a abrir una página de error. Hay que corregir CALENDLY_LINK.`,
      kind: 'am_notice',
    });
  }
  return r;
}

module.exports = { verificarLink, avisarSiEstaRoto };
