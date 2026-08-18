'use strict';

/**
 * Deteccion de los casos que el superprompt (§4) manda derivar a un humano
 * sin excepcion.
 *
 * Es por palabras clave, no por IA: un bot que decide mal aca cuesta caro, y
 * prefiero falsos positivos (deriva de mas) a falsos negativos (contesta el bot
 * una queja). Derivar de mas solo molesta al AM; derivar de menos deja al bot
 * hablando de plata o discutiendo con alguien enojado.
 *
 * Lo que NO se detecta, a proposito:
 * - "Pregunta algo tecnico que no esta en el documento": no hay forma honesta
 *   de reconocerlo con palabras clave. Requiere el scoring con IA.
 * - "Quiere cambiar el alcance de un proyecto ya cerrado": el servicio no sabe
 *   que proyectos hay cerrados; esa informacion vive en el CRM.
 */

/** minusculas, sin acentos. */
function normalizar(texto) {
  return String(texto || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '');
}

const PRECIO = [
  'cuanto sale', 'cuanto cuesta', 'cuanto vale', 'precio', 'precios',
  'cotizacion', 'cotizar', 'presupuesto aproximado', 'tarifa',
  'cuanto me sale', 'cuanto seria', 'que valor',
];

/**
 * "precio" aparece tambien cuando el lead describe lo que necesita — una tienda
 * con lista de precios es un requerimiento, no una consulta comercial. Estas
 * frases ganan sobre la deteccion de precio.
 */
const PRECIO_DE_PRODUCTO = [
  'lista de precios', 'listas de precios', 'cargar precios', 'actualizar precios',
  'gestionar precios', 'catalogo de precios', 'mostrar precios', 'poner precios',
  'subir precios', 'con precios',
];

const QUEJA = [
  'reclamo', 'queja', 'estafa', 'estafador', 'vergüenza', 'verguenza',
  'pesimo', 'horrible', 'malisimo', 'una porqueria', 'no funciona nada',
  'estoy enojado', 'estoy molesto', 'harto', 'indignado', 'inaceptable',
  'me estan cargando', 'tomando el pelo', 'denuncia', 'abogado',
  'se atraso', 'atrasado', 'nunca me contestaron', 'no me contestan',
];

const FACTURACION = [
  'factura', 'facturacion', 'boleta', 'recibo', 'transferencia',
  'cuando cobran', 'como se paga', 'medio de pago', 'forma de pago',
  'seña', 'senia', 'deposito', 'rut', 'iva', 'contado', 'cuotas',
  'no me llego la factura', 'pagar',
];

function alguna(texto, frases) {
  return frases.some((f) => texto.includes(f));
}

/**
 * @returns {{motivo: 'precio'|'queja'|'facturacion', frase: string}|null}
 */
function detectar(textoCrudo) {
  const t = normalizar(textoCrudo);
  if (!t.trim()) return null;

  // La queja va primero: alguien enojado por un precio es una queja, no una
  // consulta comercial, y se trata distinto.
  if (alguna(t, QUEJA)) return { motivo: 'queja', frase: QUEJA.find((f) => t.includes(f)) };
  if (alguna(t, FACTURACION)) return { motivo: 'facturacion', frase: FACTURACION.find((f) => t.includes(f)) };

  if (alguna(t, PRECIO) && !alguna(t, PRECIO_DE_PRODUCTO)) {
    return { motivo: 'precio', frase: PRECIO.find((f) => t.includes(f)) };
  }

  return null;
}

const ETIQUETA = {
  precio: 'insiste con el precio',
  queja: 'queja o reclamo',
  facturacion: 'consulta de facturación o pagos',
  invalidos: 'no entendió las opciones varias veces',
  pedido: 'pidió hablar con una persona',
  post_oferta: 'sigue escribiendo despues de recibir el link',
  agenda: 'no se pudo agendar la reunion',
  sin_ia: 'la IA no pudo responder',
};

module.exports = { detectar, normalizar, ETIQUETA, PRECIO, PRECIO_DE_PRODUCTO, QUEJA, FACTURACION };
