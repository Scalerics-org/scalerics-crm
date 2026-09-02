'use strict';

/**
 * Corrige el tuteo que se le escapa al modelo.
 *
 * El prompt lo prohíbe con todas las letras y hasta lista los verbos uno por
 * uno —"necesitás" y no "necesitas"— y aun así salió "¿Qué necesitas?" en la
 * primera conversación de prueba. Es la misma lección que el tamaño del equipo
 * y las fechas: pedirle al modelo una conversión mecánica falla aunque se la
 * expliques con ejemplos, y el código no falla nunca.
 *
 * Un solo "tienes" delata que del otro lado no hay un uruguayo, y eso es
 * exactamente lo que el bot no puede permitirse: escribe en nombre de una
 * agencia de Montevideo.
 *
 * La lista es corta a propósito. Solo entran formas donde el tuteo no puede ser
 * otra cosa: "esperas" queda afuera porque también es un sustantivo, y
 * corregirlo rompería una frase válida. Ante la duda, no se toca — el prompt ya
 * lo intenta, y esto es la red, no el plan A.
 */

const CAMBIOS = [
  // Presente, segunda persona.
  ['tienes', 'tenés'],
  ['quieres', 'querés'],
  ['necesitas', 'necesitás'],
  ['puedes', 'podés'],
  ['sabes', 'sabés'],
  ['haces', 'hacés'],
  ['trabajas', 'trabajás'],
  ['buscas', 'buscás'],
  ['vendes', 'vendés'],
  ['manejas', 'manejás'],
  ['prefieres', 'preferís'],
  ['dices', 'decís'],
  ['piensas', 'pensás'],
  ['eres', 'sos'],
  ['conoces', 'conocés'],
  ['dedicas', 'dedicás'],
  ['llamas', 'llamás'],
  ['vienes', 'venís'],
  ['entiendes', 'entendés'],
  ['pones', 'ponés'],
  ['decides', 'decidís'],
  ['mandas', 'mandás'],
  ['ofreces', 'ofrecés'],
  ['empiezas', 'empezás'],
  ['contactas', 'contactás'],

  // Imperativos que no pueden confundirse con otra palabra.
  ['cuéntame', 'contame'],
  ['cuentame', 'contame'],
  ['dime', 'decime'],
  ['escríbeme', 'escribime'],
  ['escribeme', 'escribime'],
  ['avísame', 'avisame'],
  ['avisame', 'avisame'],

  // Pronombres.
  ['contigo', 'con vos'],
  ['tú', 'vos'],
];

/**
 * Imperativos. Van aparte porque no se pueden reemplazar en cualquier lado: en
 * español el imperativo de tú y la tercera persona del singular se escriben
 * igual. "Escribe" es las dos cosas, y una de ellas —"alguien del equipo te
 * escribe"— es una frase que este bot manda cada vez que deriva a una persona.
 *
 * Por eso solo se corrigen cuando arrancan la frase, que es donde no pueden ser
 * otra cosa: una tercera persona ahí necesitaría un sujeto delante.
 *
 * Aparecieron por "Elige el horario que te quede bien", que salió el 2-9 en el
 * mensaje de oferta. La lista de arriba tenía "eliges" pero no "elige", y el
 * imperativo es justo la forma que usa el bot cada vez que pide algo.
 *
 * Misma regla que la otra lista: ante la duda, no entra. "Toma", "cuenta" y
 * "prueba" quedan afuera porque también son sustantivos.
 */
const IMPERATIVOS = [
  ['elige', 'elegí'],
  ['escribe', 'escribí'],
  ['espera', 'esperá'],
  ['avisa', 'avisá'],
  ['confirma', 'confirmá'],
  ['contesta', 'contestá'],
  ['responde', 'respondé'],
  ['mira', 'mirá'],
  ['manda', 'mandá'],
  ['envía', 'enviá'],
  ['envia', 'enviá'],
];

/**
 * Arranque de frase: el principio del texto, o lo que sigue a un punto, un
 * signo, dos puntos o un salto de línea. Los espacios y la apertura de
 * exclamación cuentan como parte del arranque.
 */
const INICIO_DE_FRASE = '(?<=^|[.!?:\\n][\\s¡¿]*)';

/** Le devuelve a una palabra corregida la mayúscula que tenía la original. */
function conMayusculaDe(original, reemplazo) {
  if (!original || original[0] !== original[0].toUpperCase()) return reemplazo;
  return reemplazo[0].toUpperCase() + reemplazo.slice(1);
}

/**
 * @returns {{texto: string, corregidos: string[]}} qué se cambió, para poder
 *   ver en el log si el modelo se está yendo seguido al tuteo.
 */
function corregir(texto) {
  let salida = String(texto || '');
  const corregidos = [];

  for (const [tuteo, voseoForma] of CAMBIOS) {
    if (tuteo === voseoForma) continue;
    // No se usa \b: en JavaScript mira solo [A-Za-z0-9_], asi que una palabra
    // acentuada no tiene borde donde va el acento. Con \b, "tú" nunca coincidia
    // —la ú no cuenta como letra— y quedaba sin corregir justo el pronombre.
    const re = new RegExp(`(?<![\\p{L}\\p{N}])${tuteo}(?![\\p{L}\\p{N}])`, 'giu');
    salida = salida.replace(re, (encontrado) => {
      corregidos.push(encontrado);
      return conMayusculaDe(encontrado, voseoForma);
    });
  }

  for (const [tuteo, voseoForma] of IMPERATIVOS) {
    const re = new RegExp(`${INICIO_DE_FRASE}${tuteo}(?![\\p{L}\\p{N}])`, 'giu');
    salida = salida.replace(re, (encontrado) => {
      corregidos.push(encontrado);
      return conMayusculaDe(encontrado, voseoForma);
    });
  }

  return { texto: salida, corregidos };
}

module.exports = { corregir, CAMBIOS, IMPERATIVOS };
