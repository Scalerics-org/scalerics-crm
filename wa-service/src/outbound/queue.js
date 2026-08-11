'use strict';

const crypto = require('node:crypto');

const PRIORIDAD = { am_notice: 0, welcome: 1, manual: 1, followup: 2 };

const dormir = (ms) => (ms > 0 ? new Promise((r) => setTimeout(r, ms)) : Promise.resolve());

/** Entero uniforme en [min, max]. crypto en vez de Math.random. */
function entre(min, max) {
  if (max <= min) return min;
  return min + crypto.randomInt(0, max - min + 1);
}

/**
 * Cola de salida con un unico worker: nunca hay dos envios simultaneos.
 *
 * Orden: prioridad primero (la ficha al AM va antes que un follow-up), y a
 * igual prioridad, orden de llegada.
 */
function crearCola({ proveedor, repo, cfg, logger }) {
  const items = [];
  let corriendo = false;
  let esperandoVacio = [];
  let seq = 0;

  function notificarVacio() {
    if (items.length === 0 && !corriendo) {
      esperandoVacio.forEach((r) => r());
      esperandoVacio = [];
    }
  }

  async function procesar(item) {
    const esPrimerContacto = !repo.yaFueContactado(item.to);

    if (cfg.TYPING_ENABLED && proveedor.capacidades.typingIndicator && item.texto) {
      // "escribiendo..." proporcional al largo, con techo: un texto largo no
      // puede dejar el indicador tres minutos prendido.
      const ms = Math.min(item.texto.length * 40, 6000);
      await proveedor.setPresencia(item.to, 'composing');
      await dormir(Math.round(ms * (0.7 + crypto.randomInt(0, 61) / 100)));
      await proveedor.setPresencia(item.to, 'paused');
      await dormir(entre(400, 1200));
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
      repo.registrarEnvio(item.to, esPrimerContacto);
      logger?.info({ kind: item.kind, to: item.to, leadId: item.leadId }, 'mensaje enviado');
    } catch (e) {
      repo.db.prepare('UPDATE messages SET status = ?, error = ? WHERE id = ?')
        .run('failed', String(e.message || e), msgId);
      logger?.error({ kind: item.kind, to: item.to, err: String(e.message || e) }, 'envio fallido');
      throw e;
    }
  }

  async function loop() {
    if (corriendo) return;
    corriendo = true;
    try {
      while (items.length) {
        items.sort((a, b) => PRIORIDAD[a.kind] - PRIORIDAD[b.kind] || a.seq - b.seq);
        const item = items.shift();

        if (item.delayMs) await dormir(item.delayMs);

        try {
          await procesar(item);
        } catch {
          // El error ya quedo en messages.status='failed' y en el log.
          // No se corta la cola por un destinatario.
        }

        if (items.length) await dormir(entre(cfg.DELAY_BETWEEN_MIN_MS, cfg.DELAY_BETWEEN_MAX_MS));
      }
    } finally {
      corriendo = false;
      notificarVacio();
    }
  }

  return {
    /**
     * @param {{to: string, texto: string, kind: string, leadId?: number, delayMs?: number}} item
     */
    encolar(item) {
      items.push({ ...item, seq: seq++ });
      queueMicrotask(() => loop().catch((e) => logger?.error({ err: String(e) }, 'loop de cola')));
    },

    /** Resuelve cuando no queda nada pendiente. Para tests. */
    vacia() {
      if (items.length === 0 && !corriendo) return Promise.resolve();
      return new Promise((r) => esperandoVacio.push(r));
    },

    pendientes: () => items.length,
  };
}

module.exports = { crearCola, entre };
