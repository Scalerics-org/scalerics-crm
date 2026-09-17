'use strict';

/**
 * El unico control que se aplica a TODO lo que sale hacia un lead, lo escriba
 * el modelo conversando o redactando un mensaje suelto.
 *
 * El superprompt prohibe dar precios y el prompt se lo dice, pero bajo presion
 * ("dale, tirame un numero") un LLM cede — y una cifra dicha por WhatsApp
 * despues la tiene que sostener alguien en la llamada. Por eso se verifica la
 * salida en vez de confiar en la instruccion.
 *
 * Se piden las dos cosas juntas, moneda y cifra, para no bloquear "el precio
 * depende del alcance", que es exactamente lo que si tiene que poder decir.
 */
const MONEDA = /\$|u\$s|usd|d[oó]lar|\bpesos?\b|euro/i;
const CIFRA = /\d|\b(mil|cien|doscientos|quinientos)\b/i;

function mencionaPlata(texto) {
  // Sin links ni arrobas: "calendly.com/scalerics" y "@local2000" no son precios.
  const limpio = String(texto)
    .replace(/https?:\/\/\S+/gi, ' ')
    .replace(/\b[\w.-]+\.(com|uy|net|org)\S*/gi, ' ')
    .replace(/@\S+/g, ' ');

  if (MONEDA.test(limpio) && CIFRA.test(limpio)) return true;
  // Una cifra grande y suelta ("arranca en 1500") tampoco pasa.
  return /\b\d[\d.,]{2,}\b/.test(limpio);
}

module.exports = { mencionaPlata };
