'use strict';

/**
 * Casos sinteticos. Cada uno es una conversacion que sabemos que pasa en la
 * vida real y que sabemos que el bot puede arruinar de una forma especifica.
 *
 * Dos formas de manejar al lead:
 *  - `libreto`: turnos escritos a mano. Deterministico y barato. Se usa cuando
 *    lo que se mide es una extraccion puntual ("carniceria" no es un nombre).
 *    Contra: si el bot pregunta otra cosa, el turno siguiente no le contesta —
 *    da igual, lo que se evalua es lo que el bot hace, no que la charla fluya.
 *  - `persona`: un segundo modelo hace de lead. Mas realista y mas ruidoso.
 *    Se usa cuando lo que se mide es una conducta sostenida (esquivar, insistir
 *    con el precio). Corre varias veces si queres una medicion estable.
 *
 * `espera` son campos que TIENEN que quedar guardados asi.
 * `prohibido` son campos que tienen que quedar vacios, o con un valor distinto
 * al que el modelo va a querer ponerles. Es la mitad mas importante: un dato
 * inventado hace mas dano que un dato faltante.
 */

const CASOS = [
  // ── casos que salieron de fallas reales en produccion ─────────────────────

  {
    id: 'ya-te-lo-dije',
    porque: 'paso de verdad: el lead dio el tipo de proyecto en el primer mensaje y el rubro en el segundo, y el bot pregunto tres veces lo mismo hasta que le contestaron "ya te lo dije"',
    libreto: [
      'buenass, estoy buscando para hacer una pagina web',
      'Tengo una panaderia, se llama PanesAhora',
      'somos 3 con mi hermano y mi vieja',
    ],
    espera: {
      business_type: 1,
      business_name: /panesahora/i,
      rubro: /panader/i,
      team_size: 2,
    },
  },

  {
    id: 'somos-tres',
    porque: 'el modelo confundia la cantidad de gente con el numero de tramo: a "somos 3" le ponia 3, que significa "de 6 a 20 personas"',
    libreto: [
      'hola, tengo un taller mecanico',
      'somos 3 contando al dueño',
      'quiero una web para que me encuentren',
    ],
    espera: { team_size: 2, rubro: /taller|mecanic/i },
  },

  {
    id: 'no-inventa-lo-que-no-dijeron',
    porque: 'el prompt traia un ejemplo entrecomillado de como retomar lo que el lead conto, y el modelo se lo mandaba textual a leads que no habian contado nada',
    lead: { nombre: 'Ana' },
    libreto: ['hola'],
    prohibido: {
      rubro: 'nunca dijo a que se dedica',
      needs: 'nunca dijo que necesita',
      business_name: 'nunca dijo como se llama',
    },
  },

  {
    id: 'da-el-dato-sin-que-se-lo-pidan',
    porque: 'la gente se adelanta. Si contesta lo que le preguntaste y ademas otra cosa, las dos se tienen que guardar',
    libreto: [
      'necesito automatizar los pedidos, tengo una rotiseria y somos 6',
      'la rotiseria se llama Donde Pepe',
    ],
    espera: {
      business_type: 3,
      team_size: 3,
      rubro: /roti/i,
      business_name: /pepe/i,
    },
  },

  {
    id: 'carniceria-no-es-nombre',
    porque: 'el error clásico: mete el rubro en business_name y el equipo llega a la llamada llamando "Carnicería" al negocio',
    libreto: [
      'buenas, tengo una carnicería y quiero vender por internet',
      'todavía no le puse nombre a la parte online, es la carnicería del barrio nomás',
      'somos cuatro en el local',
    ],
    espera: { rubro: /carnic/i, business_type: 2, team_size: 2 },
    prohibido: { business_name: 'debe quedar vacío: nunca dijo cómo se llama' },
  },

  {
    id: 'me-corrige-el-nombre',
    porque: 'paso el 2-9: le dicto mal el nombre, se lo corrigio en el mensaje siguiente y el bot siguio llamandolo por el nombre viejo dos mensajes despues',
    libreto: [
      'hola, vi el folleto',
      'Se llama... Loa que canta... ¿Rosés?',
      'no no se llama la vaca encantada si conocías',
      'vendemos quesos artesanales',
    ],
    espera: { business_name: /vaca encantada/i, rubro: /queso/i },
  },

  {
    id: 'instagram-no-es-nombre',
    porque: 'cuando le preguntás el nombre del negocio muchos contestan con el @ de Instagram',
    libreto: [
      'hola! vi que hacen páginas',
      'tengo un local de plantas',
      '@lavacaencantada',
      'sí ese es mi instagram, ahí subo todo',
    ],
    espera: { instagram_web: /lavacaencantada/i },
    prohibido: { business_name: 'un @ de Instagram no es el nombre del negocio' },
  },

  {
    id: 'da-todo-junto',
    porque: 'el lead que escribe un párrafo con todo. Si le vuelve a preguntar lo que ya dijo, lo perdiste.',
    libreto: [
      'Hola, soy Martín de Ferretería El Tornillo. Somos 8 personas. Queremos una tienda online porque hoy vendemos todo por WhatsApp y se nos mezclan los pedidos. Tenemos instagram @eltornillo.uy',
      'sí, dale',
      'perfecto',
    ],
    espera: {
      business_name: /tornillo/i,
      rubro: /ferreter/i,
      business_type: 2,
      team_size: 3,
      instagram_web: /eltornillo/i,
    },
    debeCerrarEn: 2,
  },

  {
    id: 'insiste-con-el-precio',
    porque: 'es el motivo #1 por el que un bot de ventas se rompe. Tres pedidos seguidos, cada vez más directo.',
    persona: `Sos un uruguayo dueño de una barbería. Escribís por WhatsApp a una agencia.
Lo único que te importa es saber cuánto sale una página web y no vas a soltar el tema.
Turno 1: preguntá cuánto sale una página. Turno 2: insistí, decí que solo querés un número aproximado.
Turno 3: ponete un poco brusco, decí que si no te dicen un rango no seguís.
Turno 4: aflojá un poco pero volvé a pedir el número.
Escribí corto, como en WhatsApp, con voseo uruguayo. Nunca rompas el personaje.`,
    turnos: 4,
    espera: { rubro: /barber/i },
  },

  {
    id: 'esquivo',
    porque: 'contesta con monosílabos. Se mide que no repita la misma pregunta dos veces seguidas ni se trabe.',
    persona: `Sos un uruguayo desconfiado que escribió a una agencia y ahora contesta a desgano.
Contestá siempre en menos de seis palabras. "ta", "puede ser", "más o menos", "y...", "no sé".
Esquivá dos veces cualquier pregunta antes de contestarla.
Si te preguntan por plata, decí "depende" y no des un número.
Tenés un taller mecánico, pero recién lo decís si te preguntan dos veces.
Voseo uruguayo. Nunca rompas el personaje.`,
    turnos: 6,
  },

  {
    id: 'pide-servicio-que-no-damos',
    porque: 'tiene que decir que no sin inventarse un servicio ni prometer que "lo consultamos"',
    libreto: [
      'hola, ustedes manejan las redes sociales? necesito alguien que me postee',
      'y diseño de logos hacen?',
      'ah ok, tengo una panadería igual, capaz me sirve una web',
    ],
    espera: { rubro: /panader/i },
    prohibido: {},
  },

  {
    id: 'dice-que-si',
    porque: 'el momento del link. Tiene que mandarlo exacto, una sola vez y sin adornos.',
    lead: {
      nombre: 'Marcos',
      business_name: 'La Vaca Encantada',
      rubro: 'carnicería',
      needs: 'que dejen de pedirme por Instagram',
      business_type: 2,
    },
    libreto: ['dale, me sirve', 'listo, ya agendé', 'gracias'],
    debeMandarLink: true,
  },

  {
    id: 'ya-agendo-y-pregunta',
    porque: 'después de agendar el bot se corre. Si sigue contestando dudas técnicas, pisa al humano.',
    lead: {
      nombre: 'Marcos',
      rubro: 'carnicería',
      needs: 'vender online',
      business_type: 2,
      link_enviado: true,
      agendado: true,
    },
    libreto: [
      'che, una duda: la tienda se puede conectar con el sistema de facturación que uso?',
      'y podemos pasar la reunión para el jueves?',
    ],
    noDebeResponderTecnico: true,
  },

  {
    id: 'presupuesto-en-palabras',
    porque: 'nadie dice "opción 2". Dicen "no quiero gastar más de mil dólares".',
    lead: {
      nombre: 'Ana',
      rubro: 'estudio contable',
      needs: 'dejar de cargar facturas a mano',
      business_type: 3,
      link_enviado: true,
    },
    libreto: [
      'somos tres en el estudio',
      'mirá, para arrancar no quiero gastar más de mil dólares',
    ],
    espera: { team_size: 2, budget: 2 },
  },

  {
    id: 'no-me-escribas-mas',
    porque: 'la baja se respeta. Esto además tiene que estar cortado en código, no solo en el prompt.',
    libreto: ['no me escribas más por favor', 'sacame de la lista'],
    noDebePreguntar: true,
  },
];

module.exports = { CASOS };
