"""Banco de ideas para Instagram (@scalerics_). Escrito el 17/9/2026.

Cada idea tiene un `pilar`, para que la semana no repita el mismo angulo:
- `dolor`: un problema concreto de un dueno de pyme y lo que cuesta no
  resolverlo.
- `servicio`: que hace Scalerics y que cambia para el cliente.
- `objecion`: las dudas que frenan la llamada ("es caro", "soy chico").
- `consejo`: algo util por si solo, que posiciona como quien sabe.
- `caso`: como resolvemos un problema real, sin nombrar al cliente ni
  prometer resultados que no estan medidos.
- `propio`: lo que usamos en Scalerics (el CRM), que es verificable.

No hay casos de clientes: se suman cuando haya casos reales y permiso para
contarlos. Nada de cifras inventadas: donde hay un numero, es una pregunta o
una referencia que no promete un resultado.

Formatos: `carrusel` (2 a 10 imagenes), `imagen` y `historia`. En los titulos,
lo que va entre asteriscos sale en verde.
"""

CTA = ("👉 Agendá una videollamada de 15 minutos desde el link del perfil "
       "o escribinos DEMO por mensaje.")
TAGS = "#Scalerics #PymesUY #Automatización #TransformaciónDigital #EmprendedoresUY #NegociosUY"


def _c(*parrafos, tags=TAGS):
    return "\n\n".join(parrafos + (CTA, tags))


def _s(titulo, texto=None, etiqueta=None, cta=None):
    s = {"titulo": titulo}
    if etiqueta:
        s["etiqueta"] = etiqueta
    if texto:
        s["texto"] = texto
    if cta:
        s["cta"] = cta
    return s


BANCO = [
    # ── carruseles ───────────────────────────────────────────────────────────
    {"clave": "car-excel-senales", "formato": "carrusel", "pilar": "dolor",
     "slides": [
         _s("5 señales de que tu *Excel* ya te frena", "Si te pasan dos o más, es momento de un sistema.", "Guía rápida"),
         _s("Versiones que *se pisan*", "Final, final2, final-ahora-sí. Nadie sabe cuál es la buena.", "Señal 1"),
         _s("Una sola persona *la entiende*", "Si esa persona no está, nadie puede tocar la planilla.", "Señal 2"),
         _s("Cargás lo mismo *dos veces*", "En la planilla, en el sistema de facturación y en el WhatsApp.", "Señal 3"),
         _s("Los números llegan *tarde*", "Para saber cómo viene el mes, alguien tiene que armar un reporte.", "Señal 4"),
         _s("Una fórmula rota *nadie la ve*", "Y las decisiones se toman sobre un dato equivocado.", "Señal 5"),
         _s("¿Te pasó *alguna?*", "En 15 minutos te mostramos cómo quedaría tu operación en un sistema propio.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "El Excel es la mejor herramienta para arrancar. El problema es cuando la empresa crece y la planilla sigue siendo el centro de todo.",
         "Versiones que se pisan, fórmulas que alguien rompe sin querer y una sola persona que entiende cómo funciona. Cada una de esas cosas cuesta horas y errores que no aparecen en ninguna factura.",
         "Un sistema a medida mantiene lo que ya te sirve y le saca todo lo que hoy se hace a mano.")},

    {"clave": "car-whatsapp-ventas", "formato": "carrusel", "pilar": "dolor",
     "slides": [
         _s("Tu WhatsApp está *perdiendo ventas*", "Y no te das cuenta porque nadie las cuenta.", "Pasa todos los días"),
         _s("Consultas que *llegan de noche*", "A las 23 alguien quiere comprar. A las 9 ya le compró a otro.", "1"),
         _s("Mensajes que *se pierden*", "Entre grupos, audios y chats personales, un pedido queda sin responder.", "2"),
         _s("Las mismas preguntas, *veinte veces*", "Precios, horarios, envíos. Tu equipo contesta lo mismo todo el día.", "3"),
         _s("Nadie hace *seguimiento*", "El que preguntó y no compró no vuelve a saber de vos.", "4"),
         _s("Todo eso *se puede automatizar*", "Respuestas al instante, pedidos ordenados y seguimiento solo, sin perder el trato humano.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "WhatsApp es donde se vende en Uruguay. También es donde más ventas se pierden sin que nadie lo note.",
         "Una consulta que se responde a la mañana siguiente, un pedido que queda enterrado en un chat o un cliente que preguntó precio y nunca recibió un segundo mensaje.",
         "Con un asistente automático y un tablero de pedidos, cada consulta tiene respuesta y cada venta tiene seguimiento.")},

    {"clave": "car-web-folleto", "formato": "carrusel", "pilar": "servicio",
     "slides": [
         _s("Tu web, ¿*vende* o solo *existe?*", "La diferencia está en lo que pasa cuando alguien entra.", "Web que vende"),
         _s("Carga *rápido*", "Si tarda, la persona se va antes de ver qué hacés.", "1"),
         _s("Dice *qué hacés* en 5 segundos", "Sin vueltas: qué ofrecés, para quién y cómo contactarte.", "2"),
         _s("Captura el *dato*", "Un formulario simple o un botón a WhatsApp en cada sección.", "3"),
         _s("Responde *sola*", "La consulta entra al sistema y recibe respuesta aunque no estés mirando.", "4"),
         _s("Tu web tiene que *trabajar para vos*", "Te mostramos cómo quedaría la tuya en una videollamada.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "La mayoría de las webs de pymes son un folleto: informan, pero no venden. La persona entra, mira y se va sin dejar rastro, y esa visita ya la pagaste.",
         "Una web que vende hace cuatro cosas: carga rápido, se entiende al instante, captura el contacto y responde aunque no estés.",
         "Diseñamos webs pensadas para convertir y conectadas con el resto de tu negocio."),
     },

    {"clave": "car-automatizar-primero", "formato": "carrusel", "pilar": "consejo",
     "slides": [
         _s("¿Qué *automatizar* primero?", "Tres preguntas para encontrar la tarea que más te conviene.", "Consejo"),
         _s("¿Se repite *todas las semanas?*", "Si la hacés una vez al año, no vale la pena. Si es diaria, sí.", "Pregunta 1"),
         _s("¿Sigue *siempre los mismos pasos?*", "Copiar, pegar, avisar, cargar. Eso lo hace mejor un sistema.", "Pregunta 2"),
         _s("¿Un error ahí *cuesta plata?*", "Facturas, cobros, stock y pedidos van primero.", "Pregunta 3"),
         _s("Tres *sí* = empezá por ahí", "Te ayudamos a armar el mapa de automatización de tu empresa.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "No hace falta automatizar todo de una. Lo que conviene es empezar por la tarea que más tiempo y errores te cuesta.",
         "La regla es simple: si se repite seguido, sigue siempre los mismos pasos y un error ahí te cuesta plata, es la primera candidata.",
         "Guardá este post para revisarlo con tu equipo.")},

    {"clave": "car-caro-objecion", "formato": "carrusel", "pilar": "objecion",
     "slides": [
         _s("«Un sistema a medida *es caro*»", "Hagamos la cuenta al revés.", "Lo escuchamos seguido"),
         _s("¿Cuántas horas *por semana?*", "Sumá las que tu equipo pasa copiando datos, armando reportes y buscando información.", "Paso 1"),
         _s("Multiplicalas *por el sueldo*", "Eso ya lo estás pagando, todos los meses, aunque no aparezca en una factura.", "Paso 2"),
         _s("Sumá los *errores*", "Un pedido mal cargado, una factura que no se cobró, un cliente que se fue.", "Paso 3"),
         _s("Lo caro es *seguir igual*", "En la videollamada te ayudamos a hacer esta cuenta con tus números.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Es la duda más común, y es razonable. Pero la comparación no es entre pagar un sistema o no pagar nada.",
         "Hoy ya estás pagando: en horas de tu equipo, en errores y en ventas que se pierden. Solo que ese costo no llega en una factura.",
         "Si querés, hacemos la cuenta juntos con los números de tu empresa.")},

    {"clave": "car-cobros", "formato": "carrusel", "pilar": "dolor",
     "slides": [
         _s("¿Cuánto tiempo pasás *persiguiendo pagos?*", "Cobrar no debería ser un trabajo aparte.", "Cobros"),
         _s("Facturas *una por una*", "Armar, enviar y registrar a mano cada mes.", "Hoy"),
         _s("Recordatorios *incómodos*", "Escribirle a un cliente para pedirle plata, otra vez.", "Hoy"),
         _s("No saber *quién debe*", "La información está en tres lugares distintos.", "Hoy"),
         _s("Con un sistema, *se hace solo*", "Facturas automáticas, recordatorios amables y un tablero con lo que falta cobrar.", "Mañana"),
         _s("Cobrá sin *perseguir*", "Te mostramos cómo funcionaría en tu empresa.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Facturar, recordar y controlar pagos es de las tareas que más tiempo consumen en una pyme, y de las que más incomodan.",
         "Automatizar el ciclo de cobro no es solo ahorrar horas: es dejar de depender de que alguien se acuerde de escribir.",
         "Un tablero te muestra quién pagó, quién no y cuánto falta, sin armar ninguna planilla.")},

    {"clave": "car-stock", "formato": "carrusel", "pilar": "servicio",
     "slides": [
         _s("¿Sabés cuánto *stock* tenés *hoy?*", "No el de la última planilla. El de hoy.", "Stock"),
         _s("Se vende lo que *no hay*", "Y después hay que llamar al cliente para cancelar.", "El problema"),
         _s("Se compra de *más*", "Plata inmovilizada en productos que no rotan.", "El problema"),
         _s("Stock *en tiempo real*", "Cada venta descuenta sola, en el local, en la web y en el WhatsApp.", "La solución"),
         _s("*Alertas* antes de quedarte sin", "El sistema avisa cuándo reponer y qué se está vendiendo más.", "La solución"),
         _s("Tu stock, *siempre al día*", "Te mostramos cómo quedaría con tus productos.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Vender algo que no tenés o tener plata parada en productos que no se mueven son dos caras del mismo problema: un stock que no está actualizado.",
         "Con un sistema conectado, cada venta descuenta sola, venga de donde venga, y te avisa cuándo reponer.",
         "Ideal para comercios, distribuidoras y tiendas online.")},

    {"clave": "car-crm-cartera", "formato": "carrusel", "pilar": "servicio",
     "slides": [
         _s("Tu cartera de clientes *vale oro*", "¿La tenés ordenada o repartida en chats?", "CRM"),
         _s("Saber *a quién llamar hoy*", "Sin depender de la memoria de nadie.", "Qué te da un CRM"),
         _s("Qué le *prometiste* a cada cliente", "Todo el historial en un solo lugar.", "Qué te da un CRM"),
         _s("Cuánto *te deja* cada uno", "Para saber dónde poner el esfuerzo.", "Qué te da un CRM"),
         _s("Un CRM *a tu medida*", "Con los pasos de tu venta, no con los de una plantilla genérica.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Un CRM bien implementado no es una base de datos más. Es saber a quién llamar hoy, qué le prometiste la semana pasada y cuánto te deja cada cliente.",
         "Lo armamos con los pasos reales de tu proceso de venta, para que tu equipo lo use de verdad.")},

    {"clave": "car-soy-chico", "formato": "carrusel", "pilar": "objecion",
     "slides": [
         _s("«Mi empresa es *muy chica* para un sistema»", "Justamente por eso.", "Mito"),
         _s("Si sos pocos, *cada hora cuenta*", "No hay nadie a quien delegarle las tareas repetitivas.", "1"),
         _s("Crecer con orden es *más fácil*", "Ordenar a los 5 empleados es más simple que a los 30.", "2"),
         _s("No hace falta *hacer todo*", "Se empieza por una sola tarea y se suma de a poco.", "3"),
         _s("El tamaño *no es excusa*", "Contanos cómo trabajan y te decimos por dónde empezar.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Muchos dueños creen que la tecnología a medida es para empresas grandes. En realidad, una pyme es la que más lo nota.",
         "Cuando son pocos, cada hora que se va en tareas repetitivas es una hora que nadie dedica a vender.",
         "Y no hace falta hacer todo junto: se arranca por un proceso y se crece desde ahí.")},

    {"clave": "car-ecommerce", "formato": "carrusel", "pilar": "servicio",
     "slides": [
         _s("¿Tu tienda online *vende sola?*", "O cada venta termina en un chat.", "Ecommerce"),
         _s("Catálogo *siempre actualizado*", "Precios y stock conectados con tu local.", "1"),
         _s("Pago *en el momento*", "Sin transferencias que hay que confirmar a mano.", "2"),
         _s("Pedido *directo al sistema*", "Sin copiar datos de un lado a otro.", "3"),
         _s("Aviso *automático* al cliente", "Confirmación, preparación y envío, sin escribir un mensaje.", "4"),
         _s("Una tienda que *trabaja sola*", "Te mostramos cómo sería la tuya.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Tener tienda online no alcanza si después cada pedido se gestiona a mano.",
         "Una tienda bien conectada toma el pago, descuenta el stock, carga el pedido y le avisa al cliente sin que nadie intervenga.",
         "Así vendés más sin sumar trabajo a tu equipo.")},

    {"clave": "car-dueno-cuello", "formato": "carrusel", "pilar": "dolor",
     "slides": [
         _s("¿Tu negocio *depende de vos* para todo?", "Hacé este test de 30 segundos.", "Test"),
         _s("¿Aprobás *cada pedido?*", "Si no estás, ¿se frena?", "1"),
         _s("¿Sos el único que *sabe los números?*", "Ventas, cobros, stock.", "2"),
         _s("¿Te escriben *de noche y en vacaciones?*", "Porque nadie más puede responder.", "3"),
         _s("Más de un sí: *sos el techo*", "Un sistema reparte lo repetitivo para que vos te ocupes de decidir.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Cuando todo pasa por el dueño, el crecimiento tiene un límite: sus horas.",
         "Un sistema no reemplaza tu criterio. Se encarga de lo repetitivo para que tu equipo pueda avanzar sin esperarte y vos puedas pensar en crecer.")},

    {"clave": "car-proceso-trabajo", "formato": "carrusel", "pilar": "consejo",
     "slides": [
         _s("¿Cómo es *trabajar con Scalerics?*", "Sin sorpresas, en cuatro pasos.", "Nuestro proceso"),
         _s("*Videollamada* de 15 minutos", "Nos contás cómo trabajan y qué te está frenando.", "Paso 1"),
         _s("*Demo* con tu caso", "Te mostramos cómo quedaría, antes de decidir nada.", "Paso 2"),
         _s("*Propuesta* clara", "Qué se hace, en cuánto tiempo y a qué precio.", "Paso 3"),
         _s("*Desarrollo* y acompañamiento", "Entregas por etapas y soporte cuando lo necesitás.", "Paso 4"),
         _s("El primer paso *es gratis*", "Reservá tu videollamada y lo vemos juntos.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Mucha gente no consulta porque no sabe qué pasa después. Así trabajamos:",
         "1. Una videollamada corta para entender tu negocio.\n2. Una demo con tu caso real.\n3. Una propuesta clara, con tiempos y precio.\n4. El desarrollo por etapas, con acompañamiento.",
         "Sin compromiso hasta que veas cómo quedaría.")},

    # ── imagenes ─────────────────────────────────────────────────────────────
    {"clave": "img-celular-apagado", "formato": "imagen", "pilar": "dolor",
     "slides": [_s("Si apagás el celular, ¿tu negocio *sigue funcionando?*", "Si todo pasa por vos, el techo de tu empresa sos vos.")],
     "caption": _c(
         "Pedidos, dudas del equipo, aprobaciones, cobros. Si todo llega a tu teléfono, tu negocio no descansa nunca, y vos tampoco.",
         "Diseñamos sistemas que se ocupan de lo repetitivo para que tu equipo avance sin depender de tu WhatsApp.")},

    {"clave": "img-consulta-venta", "formato": "imagen", "pilar": "dolor",
     "slides": [_s("Cada consulta sin responder es una *venta perdida*", "El cliente no espera: le compra al que contesta primero.")],
     "caption": _c(
         "La velocidad de respuesta es de lo que más pesa al momento de elegir a quién comprarle.",
         "Con respuestas automáticas en WhatsApp e Instagram, cada consulta recibe atención al instante, a cualquier hora, y tu equipo retoma la conversación cuando hace falta.")},

    {"clave": "img-horas-repetitivas", "formato": "imagen", "pilar": "dolor",
     "slides": [_s("¿Cuántas horas por semana se van en *copiar y pegar?*", "Esas horas las pagás igual. Solo que no hacen crecer tu empresa.")],
     "caption": _c(
         "Cargar los mismos datos dos veces, pasar información de una planilla a otra, armar el mismo reporte todos los lunes.",
         "Nada de eso hace crecer tu negocio: solo lo mantiene funcionando. Y es exactamente lo que un sistema puede hacer por vos.",
         "Contanos cuáles son esas tareas en tu empresa.")},

    {"clave": "img-competencia-app", "formato": "imagen", "pilar": "servicio",
     "slides": [_s("Tu competencia ya vende *desde una app*", "Vos, ¿seguís mandando el catálogo en PDF?")],
     "caption": _c(
         "Una app propia no es cosa de multinacionales. Para muchas pymes es la forma más simple de que el cliente vuelva a comprar: catálogo actualizado, pedido en dos toques y avisos cuando hay novedades.",
         "Desarrollamos apps a medida, pensadas para cómo vende tu negocio.")},

    {"clave": "img-persona-clave", "formato": "imagen", "pilar": "dolor",
     "slides": [_s("Si falta *una persona*, ¿tu operación se frena?", "Cuando el proceso vive en la cabeza de alguien, tu negocio cuelga de un hilo.")],
     "caption": _c(
         "Pasa en muchas pymes: hay una persona que sabe cómo se hace todo. Cuando se enferma o se toma vacaciones, la operación se traba.",
         "Pasar ese conocimiento a un sistema no reemplaza a nadie: le saca peso de encima y le da tranquilidad a toda la empresa.")},

    {"clave": "img-software-medida", "formato": "imagen", "pilar": "servicio",
     "slides": [_s("Dejá de adaptar tu negocio *al software*", "El software tiene que adaptarse a cómo trabajás vos.")],
     "caption": _c(
         "Los sistemas genéricos obligan a cambiar tu forma de trabajar para que encaje en sus pantallas. Y lo que no encaja termina otra vez en una planilla.",
         "Desarrollamos software a medida sobre tu operación real: tus pasos, tus reportes, tus reglas.")},

    {"clave": "img-numeros-mes", "formato": "imagen", "pilar": "dolor",
     "slides": [_s("¿Sabés cómo viene el mes *sin armar una planilla?*", "Ventas, cobros y gastos, en una sola pantalla y al día.")],
     "caption": _c(
         "Si para saber cómo viene el mes alguien tiene que juntar datos de tres lugares, la información siempre llega tarde.",
         "Un tablero conectado a tu operación te muestra los números de hoy, desde el celular, sin pedirle nada a nadie.")},

    {"clave": "img-tiempo-objecion", "formato": "imagen", "pilar": "objecion",
     "slides": [_s("«No tengo tiempo para *implementar un sistema*»", "Por eso lo hacemos nosotros, por etapas y sin frenar tu operación.")],
     "caption": _c(
         "Es la razón más común para seguir igual. Pero implementar un sistema no tiene que ser un proyecto que te coma meses.",
         "Trabajamos por etapas, empezando por lo que más te cuesta hoy, y nos ocupamos de la migración de tus datos para que tu equipo siga trabajando mientras tanto.")},

    {"clave": "img-web-visita", "formato": "imagen", "pilar": "servicio",
     "slides": [_s("Cada visita a tu web es un *posible cliente*", "¿La estás tratando como tal?")],
     "caption": _c(
         "Si tu web no captura el contacto ni responde rápido, cada visita que pagaste con publicidad se va sin dejar rastro.",
         "Armamos webs que convierten: claras, rápidas y conectadas con tu WhatsApp y tu sistema de ventas.")},

    {"clave": "img-empezar-chico", "formato": "imagen", "pilar": "consejo",
     "slides": [_s("No automatices todo. *Empezá por una tarea.*", "La que más se repite y más errores te genera.")],
     "caption": _c(
         "El error más común al digitalizar una empresa es querer cambiar todo de golpe.",
         "Funciona mejor al revés: elegir un proceso, automatizarlo bien, medir el resultado y recién ahí sumar el siguiente.",
         "Si no sabés por cuál empezar, lo vemos juntos en 15 minutos.")},

    {"clave": "img-seguimiento", "formato": "imagen", "pilar": "consejo",
     "slides": [_s("La mayoría de las ventas *se cierran en el seguimiento*", "¿Quién le escribe al que preguntó y no compró?")],
     "caption": _c(
         "Muchas veces el cliente no dijo que no: simplemente nadie volvió a escribirle.",
         "Un sistema de seguimiento te recuerda a quién contactar y cuándo, o le envía un mensaje automático en el momento justo.")},

    {"clave": "img-facturacion", "formato": "imagen", "pilar": "servicio",
     "slides": [_s("Facturar a mano es *tiempo que no vuelve*", "Automatizá facturas, recordatorios y seguimiento de pagos.")],
     "caption": _c(
         "Armar cada factura, enviarla, anotar si se pagó y recordar a quien no pagó. Todos los meses, una por una.",
         "Con el ciclo de cobro automatizado, tu equipo solo mira un tablero con lo que falta cobrar.")},

    {"clave": "img-demo-sin-compromiso", "formato": "imagen", "pilar": "objecion",
     "slides": [_s("Mirá cómo quedaría tu sistema *antes de decidir*", "Una videollamada de 15 minutos. Sin compromiso.",
                   cta="Agendá tu videollamada")],
     "caption": _c(
         "Es difícil decidir una inversión sin ver el resultado. Por eso empezamos al revés: primero entendemos tu negocio y te mostramos cómo quedaría.",
         "Si te sirve, avanzamos. Si no, te llevás ideas concretas para ordenar tu operación.")},

    # ── historias ────────────────────────────────────────────────────────────
    {"clave": "his-pregunta-excel", "formato": "historia", "pilar": "dolor",
     "slides": [_s("¿Tu empresa *todavía* funciona con Excel?", "Si la respuesta es sí, tenemos que hablar.", "Pregunta rápida", "Escribinos DEMO")],
     "caption": "Historia: pregunta sobre Excel."},
    {"clave": "his-horas", "formato": "historia", "pilar": "dolor",
     "slides": [_s("¿Cuántas horas se van en *copiar y pegar?*", "Esas tareas las puede hacer un sistema.", "Pensalo un segundo", "Escribinos DEMO")],
     "caption": "Historia: horas en tareas repetitivas."},
    {"clave": "his-whatsapp-noche", "formato": "historia", "pilar": "dolor",
     "slides": [_s("Son las 23. Alguien *quiere comprarte.*", "¿Quién le responde?", None, "Escribinos DEMO")],
     "caption": "Historia: consultas fuera de horario."},
    {"clave": "his-videollamada", "formato": "historia", "pilar": "objecion",
     "slides": [_s("15 minutos pueden *ordenar tu empresa*", "Videollamada gratuita para ver qué automatizar primero.", "Agenda abierta", "Link en el perfil")],
     "caption": "Historia: invitación a la videollamada."},
    {"clave": "his-cuello", "formato": "historia", "pilar": "dolor",
     "slides": [_s("Si todo pasa por vos, *el techo sos vos*", "Un sistema reparte lo repetitivo.", None, "Escribinos DEMO")],
     "caption": "Historia: dueño cuello de botella."},
    {"clave": "his-web", "formato": "historia", "pilar": "servicio",
     "slides": [_s("¿Tu web te trae *clientes?*", "O solo está ahí.", "Pregunta rápida", "Escribinos WEB")],
     "caption": "Historia: web que vende."},
    {"clave": "his-stock", "formato": "historia", "pilar": "servicio",
     "slides": [_s("Stock al día, *sin planillas*", "Cada venta descuenta sola, en todos tus canales.", "Automatización", "Escribinos DEMO")],
     "caption": "Historia: stock en tiempo real."},
    {"clave": "his-cobros", "formato": "historia", "pilar": "servicio",
     "slides": [_s("Cobrá *sin perseguir* a nadie", "Facturas y recordatorios automáticos.", "Automatización", "Escribinos DEMO")],
     "caption": "Historia: cobros automáticos."},
    {"clave": "his-chico", "formato": "historia", "pilar": "objecion",
     "slides": [_s("¿Muy chico para un sistema? *Al revés.*", "Cuando son pocos, cada hora cuenta más.", None, "Escribinos DEMO")],
     "caption": "Historia: objeción de tamaño."},
    {"clave": "his-app", "formato": "historia", "pilar": "servicio",
     "slides": [_s("Tu catálogo en una *app propia*", "Pedidos en dos toques, sin PDFs.", "Apps a medida", "Escribinos APP")],
     "caption": "Historia: app propia."},
    {"clave": "his-proceso", "formato": "historia", "pilar": "consejo",
     "slides": [_s("Videollamada, demo, *propuesta clara*", "Así empezamos. Sin compromiso.", "Cómo trabajamos", "Link en el perfil")],
     "caption": "Historia: proceso de trabajo."},
    {"clave": "his-una-tarea", "formato": "historia", "pilar": "consejo",
     "slides": [_s("No automatices todo. *Empezá por una.*", "La que más se repite.", "Consejo", "Escribinos DEMO")],
     "caption": "Historia: empezar por una tarea."},
    {"clave": "his-persona-clave", "formato": "historia", "pilar": "dolor",
     "slides": [_s("¿Y si mañana falta *esa persona?*", "Que tu operación no dependa de nadie.", "Pregunta rápida", "Escribinos DEMO")],
     "caption": "Historia: persona clave."},
    {"clave": "his-numeros", "formato": "historia", "pilar": "servicio",
     "slides": [_s("Tus números del mes, *en tu celular*", "Sin pedirle un reporte a nadie.", "Tablero", "Escribinos DEMO")],
     "caption": "Historia: tablero de números."},
    {"clave": "his-software-medida", "formato": "historia", "pilar": "servicio",
     "slides": [_s("Software que se adapta *a tu negocio*", "No al revés.", "A medida", "Escribinos DEMO")],
     "caption": "Historia: software a medida."},
    {"clave": "his-seguimiento", "formato": "historia", "pilar": "consejo",
     "slides": [_s("¿Quién le escribe al que *no compró?*", "Ahí se cierran muchas ventas.", "Consejo", "Escribinos DEMO")],
     "caption": "Historia: seguimiento."},
    # ── como lo resolvemos (sin nombres ni resultados inventados) ────────────
    {"clave": "car-caso-preventa", "formato": "carrusel", "pilar": "caso",
     "slides": [
         _s("El almacenero *no quiere* hacer inventario", "Así pensamos un sistema de preventa para una distribuidora.", "Cómo lo resolvemos"),
         _s("El pedido que *nadie cargaba*", "La idea original era que cada comercio cargara su stock en una app. Un almacenero no cuenta cajas: esa app se abandona.", "El problema"),
         _s("El sistema *sugiere* el pedido", "Mira lo que se entregó antes y lo que rota, y arma el pedido solo.", "La vuelta"),
         _s("Ajustar y enviar: *dos toques*", "El comercio abre el celular, ve el pedido armado, cambia lo que haga falta y lo manda.", "Para el comercio"),
         _s("Todo llega *ordenado*", "La distribuidora recibe los pedidos, los prepara y arma el reparto sin llamadas ni planillas.", "Para la distribuidora"),
         _s("Si el usuario tiene que hacer la cuenta, *el sistema no sirve*", "¿Tu operación tiene un paso que nadie quiere hacer? Contanos.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Un pedido común: \"quiero que mis clientes carguen su stock en una app\". Suena bien, pero un almacenero no hace inventario, y si la app depende de eso, nadie la usa.",
         "Por eso el sistema hace la cuenta: sugiere el pedido según lo que se vendió y el comercio solo ajusta y envía. La distribuidora recibe todo ordenado.",
         "Así pensamos cada sistema: la persona hace lo mínimo y el sistema deduce el resto.")},

    {"clave": "img-caso-una-accion", "formato": "imagen", "pilar": "caso",
     "slides": [_s("Si el usuario tiene que hacer la cuenta, *el sistema no sirve*", "Diseñamos para que la persona haga una sola acción. El resto lo deduce el sistema.")],
     "caption": _c(
         "Es la regla con la que arrancamos cada proyecto. Si alguien del equipo tiene que cargar el mismo dato en dos lugares, o sumar a mano lo que el sistema ya sabe, el sistema está mal diseñado.",
         "Por eso primero miramos cómo trabajan de verdad, y recién después diseñamos las pantallas.")},

    {"clave": "car-caso-cuaderno", "formato": "carrusel", "pilar": "caso",
     "slides": [
         _s("Reemplazamos *el cuaderno*, no a la persona", "Cómo encaramos un sistema para una pyme.", "Cómo lo resolvemos"),
         _s("Primero, *mirar* cómo trabajan", "Antes de proponer nada, entendemos el día a día: qué se anota, dónde y quién lo usa.", "Paso 1"),
         _s("Después, sacar *lo repetido*", "Lo que se copia de un lado a otro lo hace el sistema.", "Paso 2"),
         _s("Una pantalla por *tarea*", "Cada persona ve solo lo que necesita para lo suyo, desde el celular.", "Paso 3"),
         _s("Tu equipo sigue igual, *con menos vueltas*", "Te mostramos cómo quedaría en tu empresa.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Un sistema a medida no cambia cómo trabaja tu equipo: le saca lo que sobra.",
         "Empezamos mirando cómo se trabaja hoy, identificamos lo que se hace dos veces y armamos pantallas simples para cada tarea.")},

    # ── lo que usamos nosotros ───────────────────────────────────────────────
    {"clave": "car-propio-crm", "formato": "carrusel", "pilar": "propio",
     "slides": [
         _s("El sistema que usamos *nosotros*", "Así funciona el CRM de Scalerics por dentro.", "Detrás de escena"),
         _s("Cada consulta entra *sola*", "Los formularios de Meta llegan al CRM al instante, sin copiar nada a mano.", "1"),
         _s("Aviso *al momento*", "Cuando entra una consulta, el equipo se entera en el acto para responder rápido.", "2"),
         _s("Seguimiento *ordenado*", "Cada contacto tiene su estado y su historia: nadie queda sin respuesta.", "3"),
         _s("La publicidad, *vigilada*", "Todos los días revisa los números de los anuncios y avisa si algo se sale de lo normal.", "4"),
         _s("Lo mismo, *para tu empresa*", "Te mostramos cómo sería el tuyo.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "No recomendamos nada que no usemos. Nuestro propio CRM recibe las consultas de Meta solo, avisa al equipo en el momento, ordena el seguimiento de cada contacto y vigila la publicidad todos los días.",
         "Es el mismo tipo de sistema que armamos para nuestros clientes, adaptado a cómo vende cada uno.")},

    {"clave": "img-propio-alertas", "formato": "imagen", "pilar": "propio",
     "slides": [_s("Nuestra publicidad nos avisa *cuando algo anda mal*", "Todos los días revisa los números y manda una alerta si se desvían.")],
     "caption": _c(
         "Si invertís en publicidad, no alcanza con mirar los números una vez por semana. Nosotros armamos un sistema que revisa cada día el costo, los clics y las consultas, y nos avisa por mail cuando algo cambia.",
         "¿Querés lo mismo para tus campañas o tus ventas?")},

    {"clave": "his-caso-dos-toques", "formato": "historia", "pilar": "caso",
     "slides": [_s("Pedidos en *dos toques*", "El sistema arma el pedido. El cliente ajusta y envía.", "Cómo lo resolvemos", "Escribinos DEMO")],
     "caption": "Historia: pedido sugerido en dos toques."},

    {"clave": "his-propio-crm", "formato": "historia", "pilar": "propio",
     "slides": [_s("Usamos lo que *hacemos*", "Nuestro CRM recibe y ordena cada consulta solo.", "Detrás de escena", "Escribinos DEMO")],
     "caption": "Historia: el CRM propio."},
]
