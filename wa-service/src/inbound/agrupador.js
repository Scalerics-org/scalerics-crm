'use strict';

/**
 * Junta los mensajes que llegan seguidos de la misma persona y los procesa como
 * uno solo, de a un turno por vez.
 *
 * Sin esto pasaba lo que se ve en cualquier conversacion real de WhatsApp: la
 * gente escribe "Necesito un" / "ecommerce" / "a medida" en tres mensajes de un
 * segundo, y el bot contestaba tres veces —una por fragmento— y encima
 * desordenado, porque las tres respuestas salian en paralelo y volvia primero
 * la que el modelo terminaba antes.
 *
 * Dos cosas arreglan eso:
 *
 * 1. La espera. Al llegar un mensaje no se procesa: se guarda y se espera un
 *    momento. Si en ese rato llega otro del mismo numero, se suman y la espera
 *    arranca de nuevo. Se procesa cuando la persona deja de escribir. Ademas
 *    ahorra llamadas a la API: tres fragmentos son un solo turno.
 *
 * 2. La fila por numero. Aunque dos tandas terminen juntas, la segunda espera a
 *    que la primera haya contestado. Asi el modelo ve el historial completo y
 *    las respuestas no se pisan.
 *
 * La espera es corta comparada con los delays de la cola de salida (12 a 45
 * segundos entre mensajes), asi que no se nota.
 */
function crearAgrupador({ procesar, esperaMs = 7000, logger = null }) {
  // telefono -> { partes, timer, nombre }
  const pendientes = new Map();
  // telefono -> promesa del turno en curso, para no atender dos a la vez
  const enCurso = new Map();

  function encolarTurno(telefono, texto, nombre) {
    const anterior = enCurso.get(telefono) || Promise.resolve();
    const turno = anterior
      .catch(() => {})
      .then(() => procesar(telefono, texto, nombre))
      .catch((e) => {
        logger?.error({ telefono, err: String(e.message || e) }, 'fallo procesando un entrante');
      })
      .finally(() => {
        // Solo se limpia si nadie encadeno otro turno mientras tanto.
        if (enCurso.get(telefono) === turno) enCurso.delete(telefono);
      });

    enCurso.set(telefono, turno);
    return turno;
  }

  function disparar(telefono) {
    const p = pendientes.get(telefono);
    if (!p) return;
    pendientes.delete(telefono);
    clearTimeout(p.timer);

    if (p.partes.length > 1) {
      logger?.info({ telefono, partes: p.partes.length }, 'mensajes agrupados en un turno');
    }
    encolarTurno(telefono, p.partes.join('\n'), p.nombre);
  }

  return {
    /** Un mensaje entrante. No se procesa ya: se acumula. */
    recibir({ from, texto, nombre }) {
      const limpio = String(texto || '').trim();
      if (!limpio) return;

      const p = pendientes.get(from) || { partes: [], timer: null, nombre: '' };
      p.partes.push(limpio);
      // El pushName del ultimo gana: es el mas fresco.
      if (nombre) p.nombre = nombre;

      clearTimeout(p.timer);
      p.timer = setTimeout(() => disparar(from), esperaMs);
      // Que un mensaje pendiente no mantenga vivo el proceso al apagarlo.
      p.timer.unref?.();
      pendientes.set(from, p);
    },

    /** Procesa ya lo que haya pendiente. Para los tests y para el apagado. */
    async vaciar() {
      for (const telefono of [...pendientes.keys()]) disparar(telefono);
      await Promise.all([...enCurso.values()]);
    },

    pendientes: () => pendientes.size,
  };
}

module.exports = { crearAgrupador };
