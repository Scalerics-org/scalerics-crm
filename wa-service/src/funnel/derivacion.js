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
  // El segundo intento casi nunca repite la palabra "precio": el que ya
  // escucho "depende del alcance" pide un numero directo. Sin estas, la
  // insistencia no se detectaba y el bot volvia al embudo como si nada.
  'tirame un numero', 'tirame un rango', 'dame un numero', 'decime un numero',
  'algun numero', 'un numero aproximado', 'un rango', 'mas o menos cuanto',
  'un estimado', 'aunque sea aproximado',
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
  // Como habla de lo suyo el que vende algo. "Vendemos contenido a buen precio"
  // disparaba la respuesta de precios el 3-9: el lead estaba describiendo su
  // negocio y el bot le contesto que el presupuesto depende del alcance.
  'buen precio', 'buenos precios', 'mejor precio', 'mejores precios',
  'precio accesible', 'precios accesibles', 'a precio de', 'precio justo',
  'precio bajo', 'precios bajos', 'precio mas bajo', 'precios mas bajos',
  'precio de costo',
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
 * Un mensaje que es para una persona del equipo, no para el embudo.
 *
 * El numero del bot es el WhatsApp de la empresa, y por ahi escribe tambien
 * gente que no es un lead: proveedores, socios, alguien con quien Juan ya tiene
 * una reunion. El 18-9, 3:09 de la mañana:
 *
 *   ← Hola Juan podemos mover nuestra reunión para el Lunes?
 *   → ¡Buenas! Soy el agente comercial de Scalerics…
 *   → Claro, sin problema. Decime el nombre de tu negocio…
 *   ← Agendaste con nosotros de hecho. Soy David, la mano derecha de Alexis
 *   → Uh, perdón — me cruzaste los cables. Vos sos del equipo, no un lead.
 *
 * Le acepto mover una reunion que no conocia, le invento quien era, y Juan no
 * se entero de nada. Eso no se arregla pidiendole al modelo que se de cuenta:
 * se detecta aca y la conversacion pasa entera a la persona.
 *
 * Mismo criterio que el resto del archivo: si se equivoca, que sea derivando de
 * mas. Que un lead de verdad que saluda a Juan por su nombre le llegue a Juan
 * no le hace daño a nadie.
 */
const VERBOS_DE_MOVER = [
  'mover', 'movemos', 'moverla', 'moverlo', 'cambiar', 'cambiamos', 'pasar',
  'pasamos', 'reprogramar', 'reprogramamos', 'reagendar', 'reagendamos',
  'posponer', 'postergar', 'correr', 'corremos', 'adelantar', 'atrasar',
  'suspender', 'cancelar', 'cancelamos',
];
const REUNIONES = ['reunion', 'llamada', 'videollamada', 'meet', 'call', 'meeting', 'cita', 'charla'];

/** "Agendaste con nosotros": la reunion la pidio alguien de aca. */
const REUNION_NUESTRA = ['agendaste con nosotros', 'agendamos con ustedes', 'agendaron con nosotros',
  'nuestra reunion', 'la reunion que tenemos', 'la reunion que teniamos', 'la reunion del', 'la llamada que tenemos'];

/**
 * @param {string} textoCrudo
 * @param {{nombres?: string[], tieneReunion?: boolean}} contexto
 * @returns {{motivo: 'para_una_persona'|'reunion_ajena', nombre: string|null}|null}
 */
function paraUnaPersona(textoCrudo, { nombres = [], tieneReunion = false } = {}) {
  const t = normalizar(textoCrudo).trim();
  if (!t) return null;

  // Lo llama por el nombre al principio: "Hola Juan", "Juan,", "buenas juan!".
  for (const nombre of nombres) {
    const n = normalizar(nombre).trim();
    if (!n) continue;
    const saludo = new RegExp(
      `^(?:(?:hola|holaa+|buenas|buen dia|buenos dias|buenas tardes|buenas noches|che|hey|ey|que tal)[\\s,!.]*)?${n}(?![a-z])`
    );
    if (saludo.test(t)) return { motivo: 'para_una_persona', nombre };
  }

  // Una reunion que el bot no tiene: el que agendo lo sabe una persona.
  if (!tieneReunion) {
    const hablaDeReunion = REUNIONES.some((r) => new RegExp(`(?<![a-z])${r}(?![a-z])`).test(t));
    const quiereMoverla = VERBOS_DE_MOVER.some((v) => new RegExp(`(?<![a-z])${v}(?![a-z])`).test(t));
    if ((hablaDeReunion && quiereMoverla) || alguna(t, REUNION_NUESTRA)) {
      return { motivo: 'reunion_ajena', nombre: null };
    }
  }

  return null;
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
  abandono: 'dejo de contestar en el medio',
  necesidad: 'no se entiende que necesita, ni preguntandole de nuevo',
  reprograma: 'quiere mover una reunion ya agendada',
  para_una_persona: 'le escribio a una persona del equipo por su nombre: no parece un lead',
  reunion_ajena: 'habla de una reunion que el bot no tiene registrada (¿la agendo alguien del equipo?)',
};

module.exports = {
  detectar, paraUnaPersona, normalizar, ETIQUETA, PRECIO, PRECIO_DE_PRODUCTO, QUEJA, FACTURACION,
};
