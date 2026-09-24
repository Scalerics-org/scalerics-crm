'use strict';

/**
 * Modelo falso, para probar el arnes sin gastar tokens ni depender de la red.
 * No sirve para evaluar la calidad del prompt: sirve para verificar que el
 * arnes detecta lo que dice detectar.
 *
 *   node evals/correr.js --stub              → bot que se porta razonablemente
 *   STUB=malo node evals/correr.js --stub    → bot que rompe todas las reglas
 *
 * Si el segundo no reporta fallas graves, el arnes esta roto.
 */

const NUMEROS = {
  uno: 1, dos: 2, tres: 3, cuatro: 4, cinco: 5, seis: 6, siete: 7, ocho: 8,
  nueve: 9, diez: 10, once: 11, doce: 12, quince: 15, veinte: 20,
};

function tramo(system, titulo) {
  const i = system.indexOf(titulo);
  if (i === -1) return '';
  const resto = system.slice(i + titulo.length);
  const j = resto.indexOf('\n# ');
  return j === -1 ? resto : resto.slice(0, j);
}

function extraer(msg) {
  const g = {};
  const rubros = [
    [/carnicer/i, 'carnicería'], [/ferreter/i, 'ferretería'], [/panader/i, 'panadería'],
    [/barber/i, 'barbería'], [/taller/i, 'taller mecánico'], [/plantas/i, 'vivero'],
    [/contable|contador/i, 'estudio contable'],
  ];
  for (const [re, v] of rubros) if (re.test(msg)) g.rubro = v;

  const ig = msg.match(/@([\w.]+)/);
  if (ig) g.instagram_web = ig[0];

  const gente = msg.match(/somos\s+(\w+)/i);
  if (gente) {
    const n = NUMEROS[gente[1].toLowerCase()] ?? Number(gente[1]);
    if (n) g.team_size = n === 1 ? 1 : n <= 5 ? 2 : n <= 20 ? 3 : 4;
  }

  if (/tienda online|vender por internet|e-?commerce|vender online/i.test(msg)) g.business_type = 2;
  else if (/automatiz|cargar facturas a mano|postear solo/i.test(msg)) g.business_type = 3;
  else if (/página|pagina web|web\b/i.test(msg)) g.business_type = 1;

  if (/mil dólares|mil dolares|1000|1\.000/i.test(msg)) g.budget = 2;

  const nom = msg.match(/soy\s+([A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)/);
  if (nom) g.nombre = nom[1];
  const neg = msg.match(/\bde\s+((?:Ferretería|Carnicería|Panadería|Estudio)\s+[\wáéíóúñ ]+?)(?=\.|,|\s+Somos|\s+somos)/);
  if (neg) g.business_name = neg[1].trim();

  if (/se nos mezclan|se me mezclan|dejen de pedirme|dejar de cargar|se mezclan los pedidos/i.test(msg)) {
    g.needs = msg.slice(0, 120);
  }
  return g;
}

function crearModeloStub() {
  const malo = process.env.STUB === 'malo';

  return {
    nombre: malo ? 'stub:malo' : 'stub:razonable',

    async bot(system, historial) {
      const ultimo = [...historial].reverse().find((m) => m.role === 'user')?.content || '';
      const guardados = [extraer(ultimo)];

      if (malo) {
        return {
          texto:
            'Hola! Tienes un negocio muy lindo 😀🔥 Una página web arranca en USD 500 y te la entregamos en 10 días. ' +
            'Mirá nuestro portfolio en https://ejemplo.com/portfolio — ¿te interesa? ¿cuándo podemos hablar? YA MISMO!!',
          guardados: [{ ...guardados[0], business_name: 'Carnicería', rubro_inventado: 'x' }],
        };
      }

      const linkM = system.match(/Link de agenda \(copialo exacto[^)]*\):\s*(\S+)/);
      const yaAgendo = /Ya agendó\./.test(system);
      const esperando = /Ya le mandaste el link/.test(system);
      const cerrando = /Ya sabés lo suficiente/.test(system);

      if (/no me escribas|sacame de la lista/i.test(ultimo)) {
        return { texto: 'Listo, no te escribo más. Cualquier cosa, acá estamos.', guardados: [] };
      }
      if (yaAgendo) {
        return {
          texto: 'Buenísimo, quedó agendada. Eso se lo paso al equipo y te contactan por acá mismo.',
          guardados,
        };
      }
      if (cerrando) {
        const dijoSi = /dale|me sirve|sí|si\b|listo/i.test(ultimo);
        if (dijoSi && linkM) return { texto: linkM[1], guardados };
        return {
          texto:
            'Entiendo, eso de que se te mezclen los pedidos es un dolor de cabeza. Te propongo una videollamada de 30 minutos, gratis y sin compromiso, para mostrarte cómo lo resolveríamos en tu caso. ¿Te sirve?',
          guardados,
        };
      }

      const pendientes = tramo(system, '# Qué te falta averiguar')
        .split('\n')
        .filter((l) => l.startsWith('- '))
        .map((l) => l.slice(2));

      if (!pendientes.length) {
        return { texto: esperando ? 'Perfecto, con eso ya está.' : 'Dale, contame más.', guardados };
      }
      return { texto: `Buenísimo. Contame ${pendientes[0]}.`, guardados };
    },

    async lead(persona, historial) {
      const guiones = {
        precio: ['hola cuánto sale una página web?', 'tengo una barbería, dale tirame un aproximado', 'si no me decís un rango no sigo', 'bueno, igual decime cuánto'],
        esquivo: ['hola', 'ta', 'puede ser', 'tengo un taller mecánico', 'depende', 'no sé'],
      };
      const clave = /barbería/i.test(persona) ? 'precio' : 'esquivo';
      const i = historial.filter((m) => m.role === 'user').length;
      return guiones[clave][i] || 'ta';
    },
  };
}

module.exports = { crearModeloStub };
