'use strict';

/**
 * Contrato de proveedor. Ningun archivo fuera de src/providers/ importa Baileys
 * ni habla con WhatsApp: todo pasa por esta interfaz.
 *
 * Criterio de disenio: cambiar WA_PROVIDER no debe requerir tocar nada fuera de
 * src/providers/ y el .env.
 *
 * @typedef {Object} Capacidades
 * @property {boolean} typingIndicator   soporta "escribiendo..."
 * @property {boolean} textoLibre        puede mandar texto libre sin plantilla aprobada
 * @property {boolean} grupos            puede mandar a un JID de grupo (...@g.us)
 *
 * @typedef {Object} MensajeEntrante
 * @property {string} from    E.164 sin '+'
 * @property {string} texto
 * @property {string} id      id del mensaje en el proveedor
 *
 * @typedef {Object} Proveedor
 * @property {string} nombre
 * @property {Capacidades} capacidades
 * @property {() => Promise<void>} conectar
 * @property {() => Promise<void>} desconectar
 * @property {() => {conectado: boolean, qr?: string, telefono?: string}} estado
 * @property {(to: string, texto: string) => Promise<{id: string}>} enviarTexto
 * @property {(to: string, estado: 'composing'|'paused'|'available') => Promise<void>} setPresencia
 * @property {(handler: (m: MensajeEntrante) => void) => void} alRecibir
 */

module.exports = {};
