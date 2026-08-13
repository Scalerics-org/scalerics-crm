'use strict';

/**
 * Textos del embudo de calificacion. Portados de bot/src/messages/templates.js.
 * Los de recordatorio de reunion quedan para cuando se porte esa parte.
 */
function crearTextos({ calendlyLink }) {
  return {
    MENU: (nombre) =>
      `Hola${nombre ? ` ${nombre}` : ''} 👋 Soy el asistente de *Scalerics*.\n\nSomos una agencia uruguaya de desarrollo web y software — creamos páginas web, e-commerce, apps y automatizaciones para negocios de toda escala.\n\n¿En qué te puedo ayudar?\n\n*1* → Quiero un presupuesto\n*2* → Hablar con alguien del equipo`,

    QUAL_0: 'Perfecto 😄\n\n¿Cómo se llama tu negocio?',

    QUAL_1: '¿Qué tipo de proyecto tenés en mente?\n\n*1* → Página web\n*2* → E-commerce / tienda online\n*3* → Automatización (WhatsApp, procesos internos, etc.)\n*4* → App a medida',

    QUAL_2: '¿Tenés algún rango de presupuesto aproximado? (sin compromiso, es solo para orientarnos)\n\n*1* → Menos de $500 USD\n*2* → Entre $500 y $3.000 USD\n*3* → Más de $3.000 USD\n*4* → Todavía no lo tengo claro',

    QUAL_3: '¿Cuántas personas trabajan en el negocio?\n\n*1* → Solo yo\n*2* → 2 a 5 personas\n*3* → 6 a 20 personas\n*4* → Más de 20',

    QUAL_4: 'Última cosa — ¿qué colores tiene tu marca o negocio?\n\n_(Ej: "azul y blanco", "rojo y negro", "no tengo definidos todavía")_',

    QUAL_5: '¿Tienen Instagram, página web o redes sociales actualmente?\n\n_(Ej: "@mirestaurante", "www.minegocio.com", "no tenemos nada todavía")_',

    QUAL_6: '¿Qué es lo más importante que necesitás que haga tu proyecto?\n\n_(Describilo en tus palabras, sin limitaciones)_',

    MEETING_OFFER: (nombre) =>
      `Perfecto${nombre ? `, ${nombre}` : ''} 🎯\n\nCon lo que me contaste puedo armar algo concreto para mostrarte.\n\nEl siguiente paso es una *videollamada de 30 minutos* (sin costo, sin compromiso) donde:\n\n→ Entendemos bien lo que necesitás\n→ Te mostramos ejemplos de trabajos similares\n→ Te damos un presupuesto claro\n\n¿Agendamos?\n\n*1* → Sí, quiero agendar\n*2* → Primero quiero saber más`,

    MEETING_LINK: `Genial 👌\n\nElegí el horario que mejor te quede:\n\n🗓️ ${calendlyLink}\n\nHay disponibilidad esta semana y la que viene.\n\nCuando reserves, te llega la confirmación automática con el link de la videollamada.`,

    MORE_INFO: 'Bueno, te cuento 💡\n\nScalerics es una agencia uruguaya que ayuda a negocios a automatizar sus ventas y atención.\n\nCaso real: una empresa recibe 50 consultas por día en WhatsApp. Con nuestro sistema, el 80% se resuelve solo — sin que nadie en el equipo tenga que contestar.\n\nResultado:\n→ Tu equipo se enfoca en cerrar, no en responder\n→ Más leads atendidos = más ventas\n→ Escalás sin contratar más gente\n\n¿Agendamos la llamada de diagnóstico? Es gratis y sin compromiso.\n\n*1* → Sí, agendar\n*2* → Todavía no',

    // No despide: lo que conto queda anotado y alguien lo va a mirar. El texto
    // anterior ("capaz no es el momento ideal") era un portazo amable para
    // alguien que acababa de dar un brief completo.
    NURTURE: 'Gracias, ya tengo todo anotado 🙏\n\nSe lo paso al equipo para que lo mire con calma. Si encaja con lo que hacemos, te escriben para coordinar una llamada.\n\nSi mientras tanto querés adelantar algo, escribime *MENÚ*.',

    DISQUALIFIED: 'Gracias por contestar 🙏\n\nPor ahora no tenemos exactamente lo que necesitás, pero puede cambiar.\n\nSi querés explorar opciones a futuro, escribime *MENÚ* y vemos.',

    HUMAN_QUEUED: 'Listo, le paso tu contacto a alguien del equipo 👤\n\nTe escribe en los próximos minutos.\n\n_Horario de atención: Lun-Vie 9-18hs (GMT-3)_\n\n💡 Si querés volver al menú automático, escribí *MENÚ*.',

    INVALID_1: (opciones) =>
      `No entendí esa respuesta 😅\n\nRespondé con el número de tu opción:\n${opciones}`,

    INVALID_2: 'Sigo sin entender, no hay drama 😄\n\n¿Preferís hablar con alguien del equipo?\n\n*1* → Sí, conectame con alguien\n*2* → Volver al menú',

    OPT_OUT: 'Entendido, no te escribo más 👍\n\nSi en algún momento querés retomar, escribime *MENÚ*.\n\n¡Hasta pronto!',

    SCHEDULED: '✅ Confirmado, ¡te esperamos!',
  };
}

module.exports = { crearTextos };
