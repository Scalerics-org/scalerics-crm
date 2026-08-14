'use strict';

/**
 * Textos del embudo de calificacion. Portados de bot/src/messages/templates.js.
 * Los de recordatorio de reunion quedan para cuando se porte esa parte.
 */
function crearTextos({ calendlyLink, horarioAtencion = 'Lun a sáb, 9 a 19hs' }) {
  return {
    MENU: (nombre) =>
      `Hola${nombre ? ` ${nombre}` : ''} 👋 Soy el asistente de *Scalerics*.\n\nSomos una agencia uruguaya de desarrollo web y software — creamos páginas web, e-commerce, apps y automatizaciones para negocios de toda escala.\n\n¿En qué te puedo ayudar?\n\n*1* → Quiero un presupuesto\n*2* → Hablar con alguien del equipo`,

    QUAL_0: 'Perfecto 😄\n\n¿Cómo se llama tu negocio?',

    QUAL_1: '¿Qué tipo de proyecto tenés en mente?\n\n*1* → Página web\n*2* → E-commerce / tienda online\n*3* → Automatización (WhatsApp, procesos internos, etc.)\n*4* → App a medida',

    QUAL_2: '¿Tenés algún rango de presupuesto aproximado? (sin compromiso, es solo para orientarnos)\n\n*1* → Menos de $500 USD\n*2* → Entre $500 y $3.000 USD\n*3* → Más de $3.000 USD\n*4* → Todavía no lo tengo claro',

    QUAL_3: '¿Cuántas personas trabajan en el negocio?\n\n*1* → Solo yo\n*2* → 2 a 5 personas\n*3* → 6 a 20 personas\n*4* → Más de 20',

    /**
     * A que se dedica. Es el dato que mas cambia lo que se le muestra despues:
     * sin esto no se sabe si es una carniceria o un estudio contable, y la demo
     * sale generica. Los leads que entran por el formulario del CRM traen el
     * rubro; los que escriben directo al WhatsApp, no — para esos esta es la
     * unica forma de saberlo.
     */
    QUAL_4: '¿A qué se dedica el negocio?\n\n_(Ej: "carnicería de barrio", "estudio contable", "vendo ropa por Instagram")_',

    /**
     * La red o la web es de donde salen las fotos, los colores y el tono para
     * armar la demo. Se le dice para que es: quien entiende que va a ver algo
     * suyo contesta con el usuario, no con un "sí, tenemos".
     */
    QUAL_5: '¿Tenés Instagram, web o alguna red donde vea tu negocio?\n\nAsí en la llamada te muestro algo armado con tus cosas, no un ejemplo genérico.\n\n_(Ej: "@mirestaurante", "www.minegocio.com", "no tengo nada todavía")_',

    QUAL_6: 'Última — ¿qué te gustaría lograr con esto?\n\n_(Contámelo en tus palabras: qué te está costando hoy o qué te gustaría que pasara)_',

    MEETING_OFFER: (nombre) =>
      `Perfecto${nombre ? `, ${nombre}` : ''} 🎯\n\nCon lo que me contaste puedo armar algo concreto para mostrarte.\n\nEl siguiente paso es una *videollamada de 30 minutos* (sin costo, sin compromiso) donde:\n\n→ Entendemos bien lo que necesitás\n→ Te mostramos ejemplos de trabajos similares\n→ Te damos un presupuesto claro\n\n¿Agendamos?\n\n*1* → Sí, quiero agendar\n*2* → Primero quiero saber más`,

    MEETING_LINK: `Genial 👌\n\nElegí el horario que mejor te quede:\n\n🗓️ ${calendlyLink}\n\nHay disponibilidad esta semana y la que viene.\n\nCuando reserves, te llega la confirmación automática con el link de la videollamada.`,

    /**
     * Antes esto contaba un "caso real" con un cliente y dos porcentajes, todo
     * inventado. El superprompt lo prohibe explicitamente, y ademas es una
     * promesa que despues alguien tiene que sostener en la llamada. Lo que
     * queda es lo unico verificable: que hacemos y como es la reunion.
     */
    MORE_INFO: 'Te cuento 💡\n\nSomos una agencia uruguaya. Hacemos páginas web, e-commerce, apps y automatizaciones — para negocios de acá, no plantillas.\n\nLa llamada son 30 minutos por videollamada. Con lo que ya me contaste, llegamos con algo armado para mostrarte y te decimos qué costaría. Si no te cierra, quedamos como amigos.\n\n*1* → Dale, agendemos\n*2* → Todavía no',

    /**
     * "Todavia no" despues de haber pedido mas informacion. Es distinto de
     * NURTURE: este lead ya califico, lo unico que dijo es que no es el momento.
     * Se le deja el link igual — si cambia de idea a las dos horas, no tiene que
     * volver a escribir para pedirlo.
     */
    NOT_NOW: `Dale, sin apuro 👍\n\nTe dejo el link igual, por si más adelante querés coordinar:\n\n🗓️ ${calendlyLink}\n\nY si preferís, en unos días te escribo para ver cómo venís.\n\nCualquier cosa, escribime *MENÚ*.`,

    // No despide: lo que conto queda anotado y alguien lo va a mirar. El texto
    // anterior ("capaz no es el momento ideal") era un portazo amable para
    // alguien que acababa de dar un brief completo.
    NURTURE: 'Gracias, ya tengo todo anotado 🙏\n\nSe lo paso al equipo para que lo mire con calma. Si encaja con lo que hacemos, te escriben para coordinar una llamada.\n\nSi mientras tanto querés adelantar algo, escribime *MENÚ*.',

    DISQUALIFIED: 'Gracias por contestar 🙏\n\nPor ahora no tenemos exactamente lo que necesitás, pero puede cambiar.\n\nSi querés explorar opciones a futuro, escribime *MENÚ* y vemos.',

    // El horario sale de la config (BUSINESS_DAYS y BUSINESS_HOURS), no de una
    // constante en el texto: escrito a mano decia "Lun-Vie 9-18" mientras el
    // servicio atendia de lunes a sabado hasta las 19.
    HUMAN_QUEUED: `Listo, le paso tu contacto a alguien del equipo 👤\n\nTe escribe en los próximos minutos.\n\n_Horario de atención: ${horarioAtencion} (GMT-3)_\n\n💡 Si querés volver al menú automático, escribí *MENÚ*.`,

    /**
     * Respuesta al primer "¿cuánto sale?". Es textual del superprompt: el bot
     * NUNCA da un numero ni un rango, aunque el lead insista. Si vuelve a
     * preguntar, se deriva a un humano.
     */
    PRECIO: 'El precio depende del alcance del proyecto, por eso el primer paso es una charla rápida para entenderlo — así te armamos un presupuesto real, no una cifra al aire.\n\n¿Te sirve que coordinemos 15 minutos esta semana?',

    /** Alguien enojado o con un reclamo: no se le explica nada, se deriva. */
    QUEJA: 'Entiendo, y perdón por la molestia. Te paso ahora mismo con alguien del equipo para que lo resuelva directo con vos.',

    /** Cobros y facturas son tema humano, sin excepcion. */
    FACTURACION: 'Eso lo maneja el equipo directamente. Te paso con la persona que lo puede ver con vos ahora.',

    INVALID_1: (opciones) =>
      `No entendí esa respuesta 😅\n\nRespondé con el número de tu opción:\n${opciones}`,

    INVALID_2: 'Sigo sin entender, no hay drama 😄\n\n¿Preferís hablar con alguien del equipo?\n\n*1* → Sí, conectame con alguien\n*2* → Volver al menú',

    OPT_OUT: 'Entendido, no te escribo más 👍\n\nSi en algún momento querés retomar, escribime *MENÚ*.\n\n¡Hasta pronto!',

    SCHEDULED: '✅ Confirmado, ¡te esperamos!',

    /**
     * Sigue escribiendo despues de recibir el link. Antes esto era el link de
     * nuevo, textual, cuantas veces escribiera. No se lo vuelve a mandar: si ya
     * lo tiene y aun asi escribe, es que quiere otra cosa.
     */
    YA_TIENE_LINK: '¿Pudiste agendar? 🗓️\n\nSi te queda más cómodo que te escriba alguien del equipo, decímelo y te paso con una persona.',

    /** Dice que ya reservo. Se le cree y se deja de insistir. */
    YA_AGENDO: 'Genial, quedamos así entonces 🙌\n\nTe va a llegar la confirmación con el link de la videollamada. Nos vemos ahí.',

    /**
     * Llego un audio, una foto o un archivo. No lo sabemos leer, pero del otro
     * lado hay alguien esperando: antes esto era silencio y el lead se quedaba
     * hablando solo.
     */
    SIN_TEXTO: (tipo) => (tipo === 'audio'
      ? 'Perdón, todavía no puedo escuchar audios 🙈\n\n¿Me lo escribís? Así te contesto bien.'
      : `Me llegó tu ${tipo}, pero por acá no lo puedo abrir 🙈\n\n¿Me contás por escrito de qué se trata?`),
  };
}

module.exports = { crearTextos };
