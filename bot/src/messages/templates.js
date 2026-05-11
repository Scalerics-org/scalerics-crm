const config = require('../config');

const T = {
  // ── Bienvenida ──────────────────────────────────────────────────────────────

  WELCOME: (name) =>
    name
      ? `Hola ${name} 👋 Soy el asistente de *Scalerics*.\n\nAyudamos a negocios a automatizar sus ventas y atención al cliente en WhatsApp.\n\n¿Qué te trae por acá?\n\n*1* → ¿Qué hace Scalerics?\n*2* → Ver si mi negocio califica\n*3* → Hablar con alguien del equipo`
      : `Hola 👋 Soy el asistente de *Scalerics*.\n\nAyudamos a negocios a automatizar sus ventas y atención al cliente en WhatsApp.\n\n¿Qué te trae por acá?\n\n*1* → ¿Qué hace Scalerics?\n*2* → Ver si mi negocio califica\n*3* → Hablar con alguien del equipo`,

  // ── Menú info ────────────────────────────────────────────────────────────────

  MENU_INFO: `Buena pregunta 😄\n\nEn Scalerics construimos sistemas que trabajan por vos:\n\n✅ Responden consultas de clientes las 24hs\n✅ Califican leads y los priorizan solos\n✅ Agendan reuniones sin que vayas a tocar nada\n✅ Hacen seguimiento hasta que el cliente compra\n\nTodo conectado a WhatsApp, tu CRM y tu calendario.\n\nNuestros clientes ahorran entre 10 y 30 horas semanales en tareas manuales.\n\n¿Querés ver si aplica para tu negocio?\n\n*1* → Sí, quiero saber\n*2* → Volver al menú`,

  // ── Calificación ─────────────────────────────────────────────────────────────

  QUAL_0: `Buenísimo 😄\n\nAntes de empezar — ¿cómo se llama tu negocio y a qué se dedica?\n\n_(Ej: "Bicicletería El Rayo, vendemos bicis y accesorios en Montevideo")_`,

  QUAL_1: `Buenísimo, hacemos unas preguntas rápidas para ver si somos un buen match.\n\n¿Qué tipo de negocio tenés?\n\n*1* → Agencia o consultora\n*2* → E-commerce o tienda online\n*3* → Servicios profesionales (salud, legal, finanzas)\n*4* → SaaS o software\n*5* → Otro`,

  QUAL_2: `¿Cuál es el problema más grande que tenés hoy?\n\n*1* → Pierdo clientes porque no respondo a tiempo\n*2* → Mi equipo pierde mucho tiempo en cosas repetitivas\n*3* → No tengo visibilidad de mis ventas\n*4* → Quiero escalar pero no puedo contratar más gente\n*5* → Otro`,

  QUAL_3: `¿Cuántas personas manejan ventas o atención en tu negocio?\n\n*1* → Solo yo\n*2* → 2 a 5 personas\n*3* → 6 a 20 personas\n*4* → Más de 20`,

  QUAL_4: `¿Y cómo está el tema presupuesto?\n\nImplementar una automatización que realmente funcione suele estar entre $500 y $3.000 USD según el alcance.\n\n*1* → Tengo presupuesto disponible\n*2* → Depende del retorno que me muestren\n*3* → Todavía no, pero quiero informarme`,

  QUAL_5: `Última pregunta antes de pasarte con el equipo: ¿Para cuándo necesitás tener esto andando?\n\n*1* → Lo antes posible, este mes\n*2* → En los próximos 2 o 3 meses\n*3* → Estoy evaluando para más adelante`,

  QUAL_6: `Para preparar algo concreto antes de llamarnos — ¿qué colores usás en tu negocio?\n\n_(Ej: "azul marino y blanco", "verde oscuro", "no tengo colores definidos")_`,

  QUAL_7: `¿Tenés Instagram o algún sitio web donde pueda ver más del negocio?\n\n_(Mandame el link o usuario, o escribí "no tengo" si no hay)_`,

  // ── Transición a reunión ─────────────────────────────────────────────────────

  MEETING_OFFER: (name) =>
    `Perfecto${name ? `, ${name}` : ''} 🎯\n\nCon lo que me contaste, creo que hay bastante para trabajar juntos.\n\nEl próximo paso es una *llamada de 30 minutos* (sin costo) donde:\n\n→ Revisamos cómo está tu proceso hoy\n→ Te mostramos exactamente qué automatizaríamos\n→ Te damos un presupuesto claro\n\n¿Le damos?\n\n*1* → Sí, quiero agendar\n*2* → Primero quiero saber un poco más`,

  MEETING_LINK: `Genial 👌\n\nElegí el horario que mejor te quede:\n\n🗓️ ${config.CALENDLY_LINK}\n\nHay disponibilidad esta semana y la que viene.\n\nCuando reserves, te llega la confirmación automática con el link de la videollamada.`,

  MEETING_CONFIRMED: (day, time, link) =>
    `✅ ¡Todo listo!\n\nTu llamada está confirmada:\n📅 ${day} a las ${time}\n📍 ${link}\n\nTe aviso 24hs antes y 1 hora antes para que no se te olvide.\n\nSi necesitás cambiar el horario, avisame acá mismo. ¡Hasta pronto! 🚀`,

  // ── Reminders ────────────────────────────────────────────────────────────────

  REMINDER_24H: (day, time, link) =>
    `📅 Recordatorio\n\nMañana a las *${time}* es tu llamada con el equipo de Scalerics.\n\n🔗 ${link}\n\n¿Confirmamos? Respondé *1* para decir que sí.`,

  REMINDER_1H: (time, link) =>
    `⏰ En 1 hora es tu llamada con Scalerics (${time}).\n\n🔗 ${link}\n\n¡Nos vemos ahora!`,

  // ── Nurture (no califica ahora) ──────────────────────────────────────────────

  NURTURE: `Gracias por tu tiempo 🙏\n\nCapaz no es el momento ideal, pero no hay apuro.\n\nCuando quieras retomar, escribime *MENÚ* y arrancamos de nuevo.`,

  DISQUALIFIED: `Gracias por contestar 🙏\n\nPor ahora no tenemos exactamente lo que necesitás, pero puede cambiar.\n\nSi querés explorar opciones a futuro, escribime *MENÚ* y vemos.`,

  // ── Follow-ups ───────────────────────────────────────────────────────────────

  FOLLOWUP_4H: `Ey, ¿seguís por ahí? 😊\n\nPodés continuar cuando quieras. Respondé con el número de tu respuesta y seguimos.`,

  FOLLOWUP_24H: (name) =>
    `Hola${name ? ` ${name}` : ''} 👋 ¿Tuviste un momento para pensar?\n\nSi querés retomar la evaluación, respondé *1*.\nSi no es para vos, respondé *2* y no te molesto más.`,

  FOLLOWUP_72H: (name) =>
    `${name ? `${name}, ` : ''}última vez que te escribo por ahora 🙂\n\nSi en algún momento querés automatizar tu negocio, escribime *MENÚ* y empezamos.\n\n¡Éxitos con todo!`,

  // ── Human handoff ────────────────────────────────────────────────────────────

  HUMAN_QUEUED: `Listo, le paso tu contacto a alguien del equipo 👤\n\nTe escribe en los próximos minutos.\n\n_Horario de atención: Lun-Vie 9-18hs (GMT-3)_\n\n💡 Si querés volver al menú automático, escribí *MENÚ*.`,

  // ── Errores / inputs inválidos ───────────────────────────────────────────────

  INVALID_INPUT_1: (options) =>
    `No entendí esa respuesta 😅\n\nRespondé con el número de tu opción:\n${options}`,

  INVALID_INPUT_2: `Sigo sin entender, no hay drama 😄\n\n¿Preferís hablar con alguien del equipo?\n\n*1* → Sí, conectame con alguien\n*2* → Volver al menú`,

  // ── Opt-out ──────────────────────────────────────────────────────────────────

  OPT_OUT: `Entendido, no te escribo más 👍\n\nSi en algún momento querés retomar, escribime *MENÚ*.\n\n¡Hasta pronto!`,

  // ── Más info (después de MEETING_OFFER opción 2) ─────────────────────────────

  MORE_INFO: `Bueno, te cuento 💡\n\nScalerics es una agencia uruguaya que ayuda a negocios a automatizar sus ventas y atención.\n\nCaso real: una empresa recibe 50 consultas por día en WhatsApp. Con nuestro sistema, el 80% se resuelve solo — sin que nadie en el equipo tenga que contestar.\n\nResultado:\n→ Tu equipo se enfoca en cerrar, no en responder\n→ Más leads atendidos = más ventas\n→ Escalás sin contratar más gente\n\n¿Agendamos la llamada de diagnóstico? Es gratis y sin compromiso.\n\n*1* → Sí, agendar\n*2* → Todavía no`,
};

module.exports = T;
