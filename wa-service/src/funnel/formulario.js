'use strict';

/**
 * Lee el formulario de Meta cuando llega como el primer mensaje del lead.
 *
 * Cuando alguien completa el formulario de un anuncio, WhatsApp le manda al
 * bot un mensaje armado con las preguntas y respuestas, "Etiqueta: valor" una
 * por linea, en un orden que varia. El bot lo trataba como texto libre y se lo
 * pasaba entero al modelo, que a veces lo lee mal: a Patricia (22-9) le quedo
 * business_type = 6 ("Todavia no sabe") aunque el formulario decia clarito
 * "Crear mi ecommerce" — lo mas probable es que una respuesta vaga posterior
 * ("Quisiera vender de todo un poco") hizo que el modelo guardara no_sabe y
 * pisara el ecommerce.
 *
 * Esto se lee en codigo, sin IA: mas confiable para un formato fijo, y no
 * depende de que el modelo interprete bien un bloque de texto que en realidad
 * son datos estructurados.
 */

/** minusculas, sin acentos. Mismo criterio que derivacion.js. */
function normalizar(texto) {
  return String(texto || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '');
}

// Que etiqueta es cada linea. Los valores son claves internas, no lo que
// termina guardado: eso lo decide cada mapeo de abajo.
const ETIQUETAS = [
  { clave: 'buscar', prueba: /buscas/ },
  { clave: 'presupuesto', prueba: /presupuesto/ },
  { clave: 'objetivo', prueba: /objetivo/ },
  { clave: 'nombre_negocio', prueba: /como se llama.*negocio|nombre de tu negocio|nombre del negocio/ },
  // Va despues de nombre_negocio: "full name" no tiene "negocio", pero por las
  // dudas que un formulario distinto lo mencione, esta lista es en orden.
  { clave: 'nombre_persona', prueba: /full name|nombre completo/ },
  { clave: 'telefono', prueba: /phone|telefono|whatsapp/ },
  { clave: 'email', prueba: /email|correo/ },
  { clave: 'ciudad', prueba: /\bcity\b|ciudad/ },
];

function claveDeEtiqueta(etiquetaNormalizada) {
  const fila = ETIQUETAS.find((e) => e.prueba.test(etiquetaNormalizada));
  return fila ? fila.clave : null;
}

/**
 * ¿Que buscas para tu negocio? -> business_type (1 a 6, ver TIPO_PROYECTO en
 * src/ia/agente.js). El orden de estos chequeos importa: es el mismo orden
 * que la respuesta mas especifica primero, para que "tienda online con un
 * agente que atienda" no quede en cualquiera de las dos.
 */
function mapearBusiness(valorNormalizado) {
  if (/tienda|ecommerce|vender online/.test(valorNormalizado)) return 2;
  if (/automatiz/.test(valorNormalizado)) return 3;
  if (/\bagente\b|\bia\b|\bchatbot\b|\bbot\b/.test(valorNormalizado)) return 5;
  if (/\bsistema\b|\bsoftware\b|\bapp\b|a medida|turnos|reservas/.test(valorNormalizado)) return 4;
  if (/\bweb\b|pagina|sitio/.test(valorNormalizado)) return 1;
  if (/no se|no lo se|aun no|todavia no/.test(valorNormalizado)) return 6;
  return null;
}

/** Junta separadores de miles/decimales para sacar un numero de un texto libre. */
function primerNumero(texto) {
  const m = String(texto).match(/[\d][\d.,]*/);
  if (!m) return null;
  const limpio = m[0].replace(/[.,]/g, '');
  const n = parseInt(limpio, 10);
  return Number.isFinite(n) ? n : null;
}

/**
 * ¿Contas con un presupuesto? -> budget (1 <500, 2 500-3000, 3 >3000, 4 no
 * sabe). Sin numero y sin "no se", se deja vacio: adivinar un tramo de algo
 * que no se dijo es peor que no guardar nada.
 */
function mapearPresupuesto(valorNormalizado) {
  if (/no se|no lo se|aun no|todavia no|sin presupuesto/.test(valorNormalizado)) return 4;
  const n = primerNumero(valorNormalizado);
  if (n === null) return undefined;
  if (n < 500) return 1;
  if (n <= 3000) return 2;
  return 3;
}

/**
 * @param {unknown} texto el mensaje entrante, tal cual llega.
 * @returns {{business_type?: number, business_name?: string, budget?: number,
 *   needs?: string, nombre?: string}|null} null si no parece un formulario.
 */
function leerFormulario(texto) {
  if (typeof texto !== 'string' || !texto.trim()) return null;

  // Un mensaje "enorme" no tiene por que ser un formulario, y no hace falta
  // leerlo entero para decidirlo: las primeras líneas ya traen las etiquetas.
  const lineas = texto.slice(0, 4000).split(/\r?\n/);

  const valores = {};
  let etiquetasVistas = 0;

  for (const linea of lineas) {
    const m = /^\s*([^:\n]{1,80}):\s*(.+?)\s*$/.exec(linea);
    if (!m) continue;
    const [, etiquetaCruda, valorCrudo] = m;
    const clave = claveDeEtiqueta(normalizar(etiquetaCruda));
    if (!clave) continue;
    etiquetasVistas += 1;
    // Si la misma etiqueta se repite, se queda con la primera: no hay forma
    // honesta de elegir entre dos respuestas contradictorias.
    if (!(clave in valores)) valores[clave] = valorCrudo.trim();
  }

  // Con una sola etiqueta reconocida no alcanza: cualquier mensaje que
  // mencione "presupuesto" de pasada no es un formulario.
  if (etiquetasVistas < 2) return null;

  const salida = {};

  if (valores.buscar) {
    const tipo = mapearBusiness(normalizar(valores.buscar));
    if (tipo !== null) salida.business_type = tipo;
    salida.needs = valores.objetivo
      ? `${valores.buscar} · objetivo: ${valores.objetivo}`
      : valores.buscar;
  }

  if (valores.presupuesto) {
    const budget = mapearPresupuesto(normalizar(valores.presupuesto));
    if (budget !== undefined) salida.budget = budget;
  }

  if (valores.nombre_negocio) salida.business_name = valores.nombre_negocio;
  if (valores.nombre_persona) salida.nombre = valores.nombre_persona;

  // Etiquetas reconocidas pero sin ningun campo util (p.ej. solo telefono y
  // email, sin nombre ni necesidad): no hay nada que guardar.
  return Object.keys(salida).length ? salida : null;
}

module.exports = { leerFormulario, mapearBusiness, mapearPresupuesto };
