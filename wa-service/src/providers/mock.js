'use strict';

/**
 * Proveedor de mentira: no manda nada, guarda todo en memoria.
 * Sirve para dev y para los tests, que pueden correr el flujo entero sin
 * tocar WhatsApp ni necesitar un numero.
 */
function crear({ logger } = {}) {
  const enviados = [];
  const presencias = [];
  let handler = null;
  let conectado = false;
  let contador = 0;

  return {
    nombre: 'mock',
    capacidades: { typingIndicator: true, textoLibre: true, grupos: true },

    async conectar() {
      conectado = true;
      logger?.info('proveedor mock conectado — no se envia nada de verdad');
    },

    async desconectar() {
      conectado = false;
    },

    estado() {
      return { conectado, telefono: '000000000000' };
    },

    async enviarTexto(to, texto) {
      if (!conectado) throw new Error('proveedor no conectado');
      contador += 1;
      const id = `mock-${contador}`;
      enviados.push({ to, texto, id, at: new Date().toISOString() });
      return { id };
    },

    async setPresencia(to, estadoPresencia) {
      presencias.push({ to, estado: estadoPresencia });
    },

    alRecibir(fn) {
      handler = fn;
    },

    // ── solo para tests ────────────────────────────────────────────────────
    getEnviados: () => enviados.slice(),
    getPresencias: () => presencias.slice(),
    limpiar: () => { enviados.length = 0; presencias.length = 0; },
    /** Simula que entra un mensaje del lead. */
    simularEntrante: (m) => handler && handler(m),
  };
}

module.exports = { crear };
