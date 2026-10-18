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

# ── tanda del 17/10/2026 ─────────────────────────────────────────────────────
# Investigacion de lo que publican agencias de software, proveedores de IA y
# de fidelizacion, y fechas de Uruguay. Las cifras llevan la fuente en el
# caption; las de proveedores de EE. UU. lo dicen. Las de calendario estan
# escritas para servir cualquier semana: el banco elige sin mirar la fecha.
# Cerrar pidiendo que lo guarden o lo manden suma guardados (Metricool 2026).

GUARDA = "📌 Guardalo para tenerlo a mano."

BANCO += [
    # ── software y automatización ───────────────────────────────────────────
    {"clave": "car-no-automatizar", "formato": "carrusel", "pilar": "propio",
     "slides": [
         _s("Lo que *no* automatizaríamos", "Somos una empresa de automatización y te lo decimos igual.", "Contracorriente"),
         _s("La primera charla con *un cliente grande*", "Ahí se gana la confianza. Eso lo hace una persona.", "1"),
         _s("Un reclamo *enojado*", "Primero escuchar. Después, si querés, el sistema ordena el seguimiento.", "2"),
         _s("Un proceso que *cambia cada semana*", "Si todavía no tiene forma, automatizarlo es congelar el desorden.", "3"),
         _s("Algo que pasa *dos veces por año*", "No vale lo que cuesta armarlo.", "4"),
         _s("Automatizamos lo repetitivo *para que te quede tiempo para esto*", "Te ayudamos a separar una cosa de la otra.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "No todo se tiene que automatizar. La primera charla con un cliente, un reclamo delicado o un proceso que todavía cambia todas las semanas necesitan a una persona.",
         "Lo que sí conviene sacarle a tu equipo es lo que se repite, sigue siempre los mismos pasos y cuesta plata cuando sale mal.",
         "Así decidimos qué automatizar en cada empresa.")},

    {"clave": "car-glosario", "formato": "carrusel", "pilar": "consejo",
     "slides": [
         _s("Glosario *sin humo*", "Lo que te va a decir cualquier proveedor de software, explicado con el almacén.", "Guardalo"),
         _s("API = *el repartidor*", "Lleva los datos de un sistema a otro, sin que nadie los copie.", "1"),
         _s("Integración = *que se hablen*", "La caja, la planilla y la e-factura con los mismos datos.", "2"),
         _s("Bot = *el que contesta lo repetido*", "Horarios, precios, envíos. Lo raro te lo pasa a vos.", "3"),
         _s("Nube = *tus datos no viven en la compu del mostrador*", "Si se rompe la PC, no perdés nada.", "4"),
         _s("Mantenimiento = *que no desaparezcan*", "Ajustes, copias de seguridad y alguien que responde si algo falla.", "5"),
         _s("¿Qué palabra te dijeron *y no entendiste?*", "Comentala y la explicamos.", cta="Escribinos DEMO"),
     ],
     "caption": _c(
         "API, integración, bot, nube. Son palabras que aparecen en cualquier presupuesto de software y casi nadie las explica.",
         "Acá van en una línea cada una, con ejemplos de una pyme.",
         GUARDA)},

    {"clave": "car-medida-enlatado", "formato": "carrusel", "pilar": "servicio",
     "slides": [
         _s("¿A medida o *enlatado?*", "Cuál te conviene, sin vueltas.", "Guía rápida"),
         _s("Tu proceso es *estándar*", "Facturar, cobrar, liquidar sueldos: hay sistemas hechos que andan bien.", "Enlatado"),
         _s("Tu proceso es *lo que te diferencia*", "Cómo armás un pedido o atendés a tu cliente: ahí conviene a medida.", "A medida"),
         _s("Pagás 3 sistemas *que no se hablan*", "Muchas veces no hace falta cambiar ninguno: hace falta conectarlos.", "Integración"),
         _s("A veces *te recomendamos un enlatado*", "Y te ayudamos a conectarlo con lo tuyo.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "No todo tiene que ser a medida. Si tu proceso es igual al de cualquier empresa, un sistema hecho suele alcanzar.",
         "El desarrollo a medida tiene sentido en lo que te hace distinto, y la integración cuando ya tenés varios sistemas que no se hablan.",
         "En la primera charla te decimos cuál de los tres te conviene.")},

    {"clave": "car-sistemas-no-hablan", "formato": "carrusel", "pilar": "dolor",
     "slides": [
         _s("Tus sistemas *no se hablan*", "Y alguien hace de puente todos los días.", "Pasa en muchas pymes"),
         _s("Cada uno *por su lado*", "El banco, la e-factura, la tienda online y la planilla.", "Hoy"),
         _s("Alguien *copia y pega*", "De uno a otro, todos los días. Y a veces se equivoca.", "Hoy"),
         _s("¿Cuántas veces se escribe *el mismo dato?*", "Contalas en tu empresa. Suelen ser más de las que pensás.", "Pregunta"),
         _s("Un pedido que se carga *una vez*", "Y llega solo a facturación, al depósito y al reporte.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Tener buenos sistemas no alcanza si no se hablan entre ellos. Entonces alguien de tu equipo pasa el día copiando datos de uno a otro.",
         "Una integración hace ese trabajo sola: el dato se carga una vez y llega a todos lados.")},

    {"clave": "car-vida-pedido", "formato": "carrusel", "pilar": "dolor",
     "slides": [
         _s("Un día en la vida *de un pedido*", "Contá cuántas veces se copia.", "Pasa en muchas pymes"),
         _s("9:14 · Entra *por WhatsApp*", "Copias: 0.", "1"),
         _s("9:40 · Alguien lo pasa *a la planilla*", "Copias: 1.", "2"),
         _s("11:00 · El depósito *lo vuelve a anotar*", "Copias: 2.", "3"),
         _s("16:30 · Administración *lo copia para facturar*", "Copias: 3. Y en cada copia puede haber un error.", "4"),
         _s("El mismo pedido, *cargado una sola vez*", "Te mostramos cómo quedaría en tu empresa.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Un pedido que entra por WhatsApp puede copiarse tres o cuatro veces antes de facturarse. Cada copia es tiempo y una chance de error.",
         "Con un sistema, el pedido se carga una vez y el resto lo ve al instante.")},

    {"clave": "img-error-carga", "formato": "imagen", "pilar": "dolor",
     "slides": [_s("¿Cuánto te cuesta *un error de carga?*", "Un precio viejo, una dirección mal anotada, un pedido duplicado.")],
     "caption": _c(
         "Los errores de carga no aparecen en ninguna factura, pero se pagan: un presupuesto con precio viejo, un envío a la dirección equivocada, un pedido que se prepara dos veces.",
         "Casi siempre pasan por lo mismo: mucha gente copiando datos a mano. Un solo lugar donde se carga cada dato, con controles, los evita.")},

    {"clave": "car-sistema-viejo", "formato": "carrusel", "pilar": "dolor",
     "slides": [
         _s("Tu sistema de hace 10 años *ya no da más*", "Cuatro señales.", "Señales"),
         _s("Solo anda *en una PC*", "Si esa compu se rompe, se frena la empresa.", "1"),
         _s("Nadie sabe *quién lo hizo*", "Y nadie se anima a tocarlo.", "2"),
         _s("No se conecta *con nada*", "Ni con la e-factura, ni con la web, ni con el celular.", "3"),
         _s("Lo completás *con planillas*", "Lo que el sistema no hace, se hace en Excel por fuera.", "4"),
         _s("Se puede migrar *sin parar la empresa*", "Te contamos cómo lo hacemos.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Muchos sistemas viejos siguen andando, pero cada año cuesta más sostenerlos: dependen de una sola PC, nadie sabe cómo se hicieron y no se conectan con nada nuevo.",
         "Migrar no es empezar de cero: se pasa por partes, sin frenar la operación.")},

    {"clave": "car-despues-entregar", "formato": "carrusel", "pilar": "objecion",
     "slides": [
         _s("¿Y si *se rompe?*", "Qué pasa después de entregar un sistema.", "Lo preguntan siempre"),
         _s("Nos enteramos *antes que vos*", "El sistema manda alertas cuando algo falla.", "1"),
         _s("Copias de seguridad *todos los días*", "Si algo sale mal, se vuelve atrás.", "2"),
         _s("Cambia tu negocio, *cambia el sistema*", "Un precio nuevo, un producto, un paso más: se ajusta.", "3"),
         _s("No desaparecemos *al entregar*", "Contanos qué te preocupa.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Es una de las dudas más razonables: ¿qué pasa si el sistema falla un sábado o si mi negocio cambia?",
         "Para eso está el mantenimiento: alertas, copias de seguridad y ajustes cuando hacen falta. Un sistema a medida tiene que acompañar a la empresa, no quedarse congelado el día que se entrega.")},

    {"clave": "car-preguntas-proveedor", "formato": "carrusel", "pilar": "objecion",
     "slides": [
         _s("Preguntas para hacerle *a cualquier proveedor de software*", "También a nosotros.", "Antes de contratar"),
         _s("¿De quién son *el código y los datos?*", "Tienen que ser tuyos.", "1"),
         _s("¿Qué pasa si algo se rompe *un sábado?*", "Que te digan quién responde y cómo.", "2"),
         _s("¿Cuánto sale *mantenerlo por mes?*", "El precio de arranque no es el único.", "3"),
         _s("¿Lo puedo ver *andando antes de pagar todo?*", "Entregas por partes, no una caja negra al final.", "4"),
         _s("¿Se conecta *con lo que ya uso?*", "E-factura, banco, WhatsApp, tu planilla.", "5"),
         _s("Si te contestan *claro*, vas bien", "A nosotros preguntanos lo mismo.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Contratar un desarrollo es una decisión importante, y estas preguntas te ahorran sorpresas.",
         "Hacéselas a cualquier proveedor, también a nosotros.",
         "📌 Guardalo o mandáselo a tu socio.")},

    {"clave": "img-nadie-lo-usa", "formato": "imagen", "pilar": "consejo",
     "slides": [_s("Compraste el sistema *y nadie lo usa*", "Pasa cuando nadie lo pidió, es complicado o no hubo capacitación.")],
     "caption": _c(
         "Un sistema que el equipo no usa es plata perdida. Casi siempre falla por lo mismo: se eligió sin preguntarle a quien lo tiene que usar, tiene pantallas complicadas o nadie lo explicó.",
         "Lo que funciona: sumar al equipo desde el primer día, arrancar por un solo proceso y pensar cada pantalla para la persona que carga los datos.")},

    {"clave": "car-primera-reunion", "formato": "carrusel", "pilar": "objecion",
     "slides": [
         _s("Las preguntas de *la primera reunión*", "Y lo que respondemos.", "Preguntas frecuentes"),
         _s("¿Tengo que *cambiar todo?*", "No. Arrancamos por lo que más te cuesta y lo demás sigue igual.", "1"),
         _s("¿Cuánto *demora?*", "Depende del proyecto. Siempre hay una primera parte andando rápido.", "2"),
         _s("¿Mi equipo *va a saber usarlo?*", "Lo diseñamos mirando cómo trabajan, y los acompañamos al arrancar.", "3"),
         _s("¿Funciona *con e-factura?*", "Sí, se conecta con tu proveedor de facturación electrónica.", "4"),
         _s("¿Qué tengo que *tener preparado?*", "Nada. Solo contarnos cómo trabajás hoy.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Estas son las preguntas que más escuchamos en la primera videollamada.",
         "Si tenés otra, escribinos y la respondemos.")},

    {"clave": "car-llaves-pyme", "formato": "carrusel", "pilar": "consejo",
     "slides": [
         _s("Las 6 llaves *que tu pyme no puede perder*", "Revisá a nombre de quién está cada una.", "Guardalo"),
         _s("El *dominio* de tu web", "¿Está a nombre de la empresa o de quien la hizo?", "1"),
         _s("El administrador de *Meta Business*", "Si se va esa persona, perdés Instagram y los anuncios.", "2"),
         _s("El *mail principal*", "Y cómo se recupera si alguien se olvida la clave.", "3"),
         _s("El *certificado digital* para facturar", "Quién lo tiene y cuándo vence.", "4"),
         _s("El *respaldo* de tus datos", "¿Dónde está la copia si se rompe la compu?", "5"),
         _s("Las *contraseñas*", "En un gestor, no en un post-it ni en un chat.", "6"),
         _s("¿Alguna *no la tenés clara?*", "Te ayudamos a ordenarlas.", cta="Escribinos DEMO"),
     ],
     "caption": _c(
         "Muchas pymes descubren que no tienen el control de su dominio o de su cuenta de Instagram el día que lo necesitan.",
         "Revisá estas seis cosas con tu equipo: a nombre de quién están, quién tiene acceso y cómo se recuperan.",
         GUARDA)},

    # ── servicios por rubro ──────────────────────────────────────────────────
    {"clave": "car-turno-no-vino", "formato": "carrusel", "pilar": "servicio",
     "slides": [
         _s("El turno *que no vino*", "Un hueco en la agenda es plata que no vuelve.", "Clínicas, peluquerías, talleres"),
         _s("Recordatorio *24 horas antes*", "Con dos botones: confirmo o reprogramo.", "1"),
         _s("Reprogramar *sin llamar*", "El cliente elige otro horario desde el celular.", "2"),
         _s("Lista de espera *que avisa sola*", "Si alguien cancela, el siguiente se entera al instante.", "3"),
         _s("¿Cuántos turnos *se te pierden por mes?*", "Te mostramos cómo quedaría tu agenda.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "En una clínica, una peluquería o un taller, cada turno que no viene es un hueco que ya no se recupera.",
         "Un recordatorio automático con opción de confirmar o reprogramar, y una lista de espera que avisa sola, llenan esos huecos sin que nadie tenga que llamar.")},

    {"clave": "img-en-que-anda", "formato": "imagen", "pilar": "servicio",
     "slides": [_s("«¿En qué anda *mi pedido?*»", "Si tu cliente lo puede ver solo, tu equipo deja de atender esa llamada.")],
     "caption": _c(
         "Las llamadas para preguntar por un pedido interrumpen a tu equipo todo el día.",
         "Con un portal simple, el cliente ve el estado (confirmado, en preparación, enviado) y recibe un aviso en cada cambio. Sirve para distribuidoras, talleres, imprentas y cualquiera que entregue después de vender.")},

    {"clave": "img-resenas-google", "formato": "imagen", "pilar": "servicio",
     "slides": [_s("El cliente contento *no deja reseña* si no se la pedís", "Un mensaje automático unos días después de la compra lo cambia.")],
     "caption": _c(
         "Las reseñas de Google pesan cuando alguien te busca por primera vez, y casi siempre las dejan los que tuvieron un problema.",
         "Un mensaje automático unos días después de la compra le pide la reseña al cliente contento. Y si alguien responde que algo salió mal, la queja te llega a vos antes que a Google.")},

    {"clave": "car-compras-proveedores", "formato": "carrusel", "pilar": "dolor",
     "slides": [
         _s("Compras a proveedores: *el lado olvidado*", "Se ordena la venta y la compra sigue a mano.", "Dolor"),
         _s("Pedidos *por WhatsApp*", "Sin registro de qué se pidió, a quién y a qué precio.", "1"),
         _s("Llega *distinto a lo pedido*", "Y nadie lo nota hasta que falta algo.", "2"),
         _s("La factura *no coincide*", "Ni con lo pedido ni con lo recibido.", "3"),
         _s("Orden de compra *automática*", "Pedido, recepción y factura comparados solos, con alertas de vencimientos.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Muchas empresas ordenan cómo venden y dejan las compras a proveedores en el chat.",
         "Una orden de compra que se genera sola y se compara contra lo que llegó y lo que se facturó evita pagar de más y quedarte sin mercadería.")},

    {"clave": "car-barraca-presupuesto", "formato": "carrusel", "pilar": "caso",
     "slides": [
         _s("Barraca: el presupuesto *que tarda dos días*", "Un ejemplo de cómo lo pensamos.", "Cómo lo resolvemos"),
         _s("«¿Me pasás precio de *40 bolsas de pórtland?*»", "Y 2 m³ de arena, con entrega en Shangrilá.", "El pedido"),
         _s("Hoy: *buscar, llamar, escribir*", "La lista de precios, el flete, el stock. Todo a mano.", "Hoy"),
         _s("Con un formulario, *el PDF sale solo*", "Precios actualizados, flete por zona y validez del presupuesto.", "Mañana"),
         _s("El que contesta primero *suele llevarse la obra*", "¿Tu negocio presupuesta a mano? Contanos.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "En una barraca, el cliente pide precio a varias a la vez. El que contesta primero, con un presupuesto claro, suele llevarse la obra.",
         "Un formulario que arma el presupuesto con la lista de precios y el flete al día lo resuelve en minutos.",
         "Es un ejemplo de cómo pensamos estos sistemas.")},

    {"clave": "img-inmobiliaria-domingo", "formato": "imagen", "pilar": "dolor",
     "slides": [_s("Inmobiliaria: la consulta *del domingo a las 23*", "El interesado le escribió a tres. ¿Quién le contesta primero?")],
     "caption": _c(
         "Las consultas por alquileres y ventas llegan cuando la gente tiene tiempo: de noche y los fines de semana.",
         "Un asistente puede preguntar zona, presupuesto y tipo de garantía, mostrar opciones y dejar la visita agendada. El lunes llegás con interesados ya filtrados.")},

    {"clave": "car-estudio-contable", "formato": "carrusel", "pilar": "servicio",
     "slides": [
         _s("Estudio contable: *perseguir papeles* a fin de mes", "Tus clientes mandan fotos borrosas por WhatsApp.", "Para contadores"),
         _s("Los documentos llegan *por todos lados*", "WhatsApp, mail, en mano. Y siempre falta alguno.", "Hoy"),
         _s("Un lugar que *recibe y archiva*", "Cada comprobante queda guardado con nombre y cliente.", "1"),
         _s("Aviso automático *de lo que falta*", "El cliente sabe qué mandar sin que lo llames.", "2"),
         _s("Fin de mes *sin perseguir a nadie*", "Te mostramos cómo funcionaría en tu estudio.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Para un estudio contable, el cierre de mes empieza persiguiendo papeles.",
         "Un portal o un asistente que recibe, nombra y archiva cada documento, y le avisa al cliente lo que falta, devuelve esas horas al trabajo que importa.",
         "📌 Mandáselo a tu contador.")},

    {"clave": "car-3-automatizaciones-rubro", "formato": "carrusel", "pilar": "consejo",
     "slides": [
         _s("Automatizaciones *por rubro*", "Ideas concretas para empezar.", "Guardalo"),
         _s("Inmobiliaria", "Aviso a interesados cuando entra una propiedad que buscan, vencimientos de contratos y fichas que se arman solas.", "Rubro 1"),
         _s("Taller mecánico", "Aviso de auto listo, recordatorio de service y presupuesto aprobado desde el celular.", "Rubro 2"),
         _s("Clínica", "Confirmación de turnos, recordatorio de controles y formularios previos online.", "Rubro 3"),
         _s("Distribuidora", "Pedido sugerido, hoja de ruta por zona y aviso de deuda vencida.", "Rubro 4"),
         _s("¿Qué rubro *hacemos después?*", "Comentalo y armamos el próximo.", cta="Escribinos DEMO"),
     ],
     "caption": _c(
         "Cada rubro tiene sus tareas repetidas. Acá van tres por rubro que suelen dar resultado rápido.",
         "Si el tuyo no está, comentalo y lo sumamos.",
         GUARDA)},

    # ── inteligencia artificial ──────────────────────────────────────────────
    {"clave": "car-chatbot-agente", "formato": "carrusel", "pilar": "servicio",
     "slides": [
         _s("Chatbot vs. agente: *no es lo mismo*", "Y conviene saber cuál te están ofreciendo.", "IA sin humo"),
         _s("El chatbot *sigue un guion*", "Si preguntan X, responde Y.", "Chatbot"),
         _s("El agente *recibe un objetivo*", "Y decide los pasos para cumplirlo.", "Agente"),
         _s("Un ejemplo", "Recibe la consulta, revisa el stock, cotiza, agenda la entrega y te avisa.", "Agente"),
         _s("Por eso el agente *lleva límites*", "Qué puede hacer solo y qué te tiene que consultar.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Muchas veces se le dice «IA» a cualquier respuesta automática. Pero un chatbot que sigue un guion y un agente que decide los pasos para cumplir un objetivo son cosas distintas.",
         "Un agente puede hacer mucho más, y por eso se diseña con límites claros.")},

    {"clave": "car-ia-no-deberia", "formato": "carrusel", "pilar": "objecion",
     "slides": [
         _s("Lo que la IA *no debería hacer* en tu negocio", "Si te la venden para esto, desconfiá.", "Límites"),
         _s("Aprobar *descuentos grandes*", "Sin que vos lo sepas.", "1"),
         _s("Contestar *reclamos delicados*", "Eso pasa a una persona.", "2"),
         _s("Tocar *plata* sin registro", "Todo lo que hace tiene que quedar anotado.", "3"),
         _s("*Inventar* precios", "Los lee de tu lista, no los imagina.", "4"),
         _s("Bien hecha, *sabe cuándo pasarte la conversación*", "Así la diseñamos.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "La IA puede atender, ordenar y hacer seguimiento. Pero hay cosas que no debería hacer sola.",
         "Un buen sistema sabe hasta dónde llega y cuándo pasarle el caso a una persona.")},

    {"clave": "car-mitos-ia", "formato": "carrusel", "pilar": "objecion",
     "slides": [
         _s("3 mitos de la IA *que frenan a las pymes*", "Y lo que pasa en realidad.", "Mito vs. realidad"),
         _s("«Es para *empresas grandes*»", "Hoy una pyme puede tener un asistente en WhatsApp sin un equipo técnico.", "Mito 1"),
         _s("«Va a *reemplazar a mi equipo*»", "Le saca lo repetitivo para que hagan lo que importa.", "Mito 2"),
         _s("«Necesito *saber programar*»", "Necesitás saber cómo funciona tu negocio. Lo técnico lo hacemos nosotros.", "Mito 3"),
         _s("Primero orden, *después IA*", "Te mostramos por dónde empezar.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "La IA dejó de ser algo de empresas grandes, pero estas tres ideas siguen frenando a muchas pymes.",
         "Lo importante no es la herramienta: es tener claro qué tarea le vas a sacar de encima a tu equipo.")},

    {"clave": "img-ia-desorden", "formato": "imagen", "pilar": "consejo",
     "slides": [_s("La IA *no arregla* un negocio desordenado", "Primero los datos ordenados. Después el agente.")],
     "caption": _c(
         "«No hay adopción de IA exitosa sin una digitalización de base resuelta», dijo Fernando Vargas, del BID, al presentar una encuesta sobre IA en pymes argentinas (Mercado, 2025).",
         "Si tus precios, clientes y pedidos están en cinco lugares distintos, la IA va a responder con datos viejos. Por eso empezamos ordenando.")},

    {"clave": "img-ceos-ia", "formato": "imagen", "pilar": "consejo",
     "slides": [_s("Solo el 6% de los CEO uruguayos dice que la IA *ya le trajo ingresos*", "En el mundo es el 30%. ¿La tuya ahorra o vende?")],
     "caption": _c(
         "Según la encuesta global de PwC a CEO (publicada por El Observador), solo el 6% de los CEO uruguayos dice que la IA ya le generó ingresos extra. En el mundo, el 30%.",
         "La mayoría la usa para ahorrar tiempo. Para que venda, tiene que contestar rápido, hacer seguimiento y estar conectada a tus datos.")},

    {"clave": "car-dia-agente", "formato": "carrusel", "pilar": "servicio",
     "slides": [
         _s("Un día de un agente *de WhatsApp*", "En una pyme cualquiera.", "Detrás de escena"),
         _s("8:00", "Contesta las consultas que entraron de noche.", "Mañana"),
         _s("11:00", "Agenda una visita y te la deja en el calendario.", "Mediodía"),
         _s("15:00", "Le avisa a un cliente que su pedido está listo.", "Tarde"),
         _s("19:00", "Te pasa a vos el caso raro, con todo el contexto.", "Noche"),
         _s("23:00", "Sigue contestando. Vos, no.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Un agente de WhatsApp no reemplaza a tu equipo: se ocupa de lo repetido y te pasa lo que necesita criterio.",
         "Así se ve un día normal.")},

    {"clave": "car-prompts-duenos", "formato": "carrusel", "pilar": "consejo",
     "slides": [
         _s("5 usos de la IA *para dueños*", "Para probar hoy con ChatGPT o similar.", "Guardalo"),
         _s("Resumir *los reclamos del mes*", "Pegá los mensajes y pedile los tres temas que más se repiten.", "1"),
         _s("Contestar *una reseña negativa*", "Pedile una respuesta amable, corta y sin excusas.", "2"),
         _s("Comparar *dos presupuestos*", "Que te marque diferencias de precio, plazos y lo que no incluye.", "3"),
         _s("Armar *el guion de una promo*", "Para WhatsApp, en tono uruguayo y en tres líneas.", "4"),
         _s("Ordenar *tus pendientes*", "Pasale la lista y que la ordene por lo que más impacta.", "5"),
         _s("¿Y si lo hace *conectado a tus datos?*", "Eso es lo que armamos.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "No hace falta un sistema para empezar a usar IA. Estas cinco tareas las podés probar hoy.",
         "Cuando se repiten todos los días, conviene que la IA lo haga sola y con tus datos. Ahí entramos nosotros.",
         GUARDA)},

    {"clave": "car-preguntar-ia", "formato": "carrusel", "pilar": "objecion",
     "slides": [
         _s("Qué preguntarle *a quien te venda IA*", "Cinco preguntas antes de firmar.", "Antes de contratar"),
         _s("¿Con qué datos *responde?*", "Tiene que usar los tuyos, no inventar.", "1"),
         _s("¿Qué hace *cuando no sabe?*", "La respuesta correcta: te pasa la conversación.", "2"),
         _s("¿Cómo mido *si funciona?*", "Consultas atendidas, ventas, tiempo de respuesta.", "3"),
         _s("¿De quién son *los datos?*", "Tuyos.", "4"),
         _s("¿Lo puedo *apagar?*", "Siempre. Preguntanos lo mismo a nosotros.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "Hoy cualquiera vende IA. Estas preguntas te ayudan a distinguir una herramienta seria de una demo linda.",
         "📌 Guardalo para la próxima reunión con un proveedor.")},

    {"clave": "car-ia-supervision", "formato": "carrusel", "pilar": "propio",
     "slides": [
         _s("IA con *supervisión*", "Así ponemos un agente a trabajar.", "Cómo trabajamos"),
         _s("Primero *sugiere*", "El agente propone la respuesta y vos la aprobás.", "Paso 1"),
         _s("Después *aprende*", "Ajustamos lo que no le salió bien.", "Paso 2"),
         _s("Recién ahí *gana autonomía*", "Tarea por tarea, cuando acierta seguido.", "Paso 3"),
         _s("Todo queda *registrado*", "Podés ver qué hizo y por qué.", cta="Agendá tu videollamada"),
     ],
     "caption": _c(
         "No soltamos un agente de un día para el otro. Arranca sugiriendo, vos aprobás, y va ganando autonomía a medida que demuestra que acierta.",
         "Así la IA trabaja para tu negocio sin sorpresas.")},

    # ── Scalerics Fidelidad ──────────────────────────────────────────────────
    {"clave": "car-fid-7-por-ciento", "formato": "carrusel", "pilar": "dolor",
     "slides": [
         _s("El 7% de tus clientes puede hacer *la mitad de tus ventas*", "Dato de Toast y Resy, restaurantes de EE. UU.", "Restaurantes"),
         _s("Los habituales *son pocos y valen mucho*", "¿Sabés quiénes son los tuyos?", "1"),
         _s("Sin datos, son *caras conocidas*", "Y cuando dejan de venir, nadie se entera.", "2"),
         _s("Con el *QR de la boleta*", "Tu cliente suma puntos y vos sabés quién vino, cuándo y cuánto gastó.", "3"),
         _s("Conocé a tus habituales *por nombre*", "Así funciona Scalerics Fidelidad.", cta="Escribinos DEMO"),
     ],
     "caption": _c(
         "Según el Regulars Report 2026 de Toast y Resy (datos de restaurantes de EE. UU.), el 7% de los clientes puede generar hasta la mitad de los pedidos.",
         "En la mayoría de los locales, esos clientes son caras conocidas sin datos. Con Scalerics Fidelidad se identifican con el QR de la boleta, sin formularios ni apps.")},

    {"clave": "img-fid-retener", "formato": "imagen", "pilar": "consejo",
     "slides": [_s("¿Cuánto ponés en anuncios *y cuánto en los que ya vinieron?*", "Retener un 5% más puede subir las ganancias entre 25% y 95% (Bain).")],
     "caption": _c(
         "Frederick Reichheld, de Bain, calculó que subir la retención de clientes un 5% puede aumentar las ganancias entre 25% y 95% (citado en Harvard Business Review).",
         "Salir a buscar clientes nuevos es necesario. Pero muchas veces la plata más fácil está en que vuelvan los que ya te conocen.")},

    {"clave": "car-fid-descuento", "formato": "carrusel", "pilar": "dolor",
     "slides": [
         _s("El descuento que le regala margen *al que igual iba a venir*", "El problema del 2x1 para todos.", "Restaurantes"),
         _s("El 2x1 *para todos*", "Lo usa también tu cliente fiel, que venía igual.", "1"),
         _s("Plata que *se va*", "Pagás el descuento y no ganás una visita más.", "2"),
         _s("Un premio *puntual*", "Al cliente que se está alejando, con lo que le gusta.", "3"),
         _s("Medí *contra un grupo de control*", "Así sabés si la promo trajo gente o solo regaló margen.", cta="Escribinos DEMO"),
     ],
     "caption": _c(
         "Los descuentos generales son fáciles de lanzar y difíciles de medir. Muchas veces terminan pagándole un descuento a quien iba a venir igual.",
         "Thanx, un proveedor de fidelización de EE. UU., reporta que los premios dirigidos rinden más que los descuentos genéricos. Lo importante es medir siempre contra un grupo que no recibe la oferta.")},

    {"clave": "img-fid-recordar", "formato": "imagen", "pilar": "consejo",
     "slides": [_s("Lo que más valora tu cliente *no son los puntos*", "Es que lo recuerden: su nombre y lo que pide siempre.")],
     "caption": _c(
         "En una encuesta de Toast y Resy a 1.500 comensales de EE. UU., casi la mitad dijo que lo que más valora es que el personal lo recuerde. Solo el 30% dice recibir ese trato siempre.",
         "Los datos de fidelidad sirven para eso: que el mozo sepa quién tiene enfrente y qué le gusta.")},

    {"clave": "car-fid-grupo-control", "formato": "carrusel", "pilar": "propio",
     "slides": [
         _s("¿Tu promo funcionó *o el cliente igual iba a venir?*", "Para eso existe el grupo de control.", "Cómo medimos"),
         _s("Una pizzería *manda una oferta*", "A 9 de cada 10 clientes.", "1"),
         _s("A 1 de cada 10 *no le manda nada*", "Ese es el grupo de control.", "2"),
         _s("Se compara *cuánto gastó cada grupo*", "La diferencia es lo que trajo la oferta de verdad.", "3"),
         _s("Si no le gana al grupo de control, *no sirve*", "Así medimos Scalerics Fidelidad todos los meses.", cta="Escribinos DEMO"),
     ],
     "caption": _c(
         "Mandar una promo y ver que vino gente no alcanza: quizás iban a venir igual.",
         "Por eso dejamos siempre un grupo de clientes sin oferta. La diferencia entre los dos grupos es la plata que de verdad trajo la campaña, y es el número que le mostramos al restaurante cada mes.")},

    {"clave": "img-fid-45-dias", "formato": "imagen", "pilar": "consejo",
     "slides": [_s("Cliente que no vuelve hace 45 días: *¿qué le mandás?*", "«Te guardamos tu milanesa» le gana a «20% OFF».")],
     "caption": _c(
         "Cuando un cliente habitual deja de venir, casi nadie se da cuenta a tiempo.",
         "Con los datos de cada visita se detecta solo, y se le puede mandar algo personal: lo que pide siempre, un postre o una entrada, en vez de un descuento para todos. Después se mide cuántos volvieron.")},

    # ── calendario (sirven cualquier semana) ─────────────────────────────────
    {"clave": "car-dias-pico", "formato": "carrusel", "pilar": "consejo",
     "slides": [
         _s("Checklist para *los días de más consultas*", "Black Friday, fiestas, Día de la Madre, temporada.", "Guardalo"),
         _s("Respuestas *listas*", "Precio, envío, horario, medios de pago y cambios.", "1"),
         _s("Link de pago *a mano*", "Nada de «pasame tu mail y te mando».", "2"),
         _s("Horario *avisado*", "En el estado de WhatsApp y en la bio.", "3"),
         _s("Una persona *a cargo del canal*", "Ese día, alguien responde y nadie supone que lo hace otro.", "4"),
         _s("¿Y si contesta *solo?*", "Te mostramos cómo.", cta="Escribinos DEMO"),
     ],
     "caption": _c(
         "Las fechas comerciales llenan el WhatsApp de consultas, y la que no se contesta rápido se va a otro lado.",
         "Revisá esta lista una semana antes de cada fecha fuerte.",
         GUARDA)},

    {"clave": "car-aguinaldo", "formato": "carrusel", "pilar": "consejo",
     "slides": [
         _s("Aguinaldo: *lo que tenés que saber*", "Se paga dos veces por año.", "Guardalo"),
         _s("Junio y *diciembre*", "El de junio cubre de diciembre a mayo. El de diciembre, de junio a noviembre.", "Cuándo"),
         _s("Total del período *dividido 12*", "Sobre lo nominal que cobró la persona en esos seis meses.", "Cómo se calcula"),
         _s("Error típico: *olvidarse de lo variable*", "Comisiones y horas extra también entran.", "Ojo"),
         _s("¿Que el sistema *lo calcule solo?*", "Escribinos.", cta="Escribinos DEMO"),
     ],
     "caption": _c(
         "El aguinaldo se paga en junio y en diciembre, y es la doceava parte de lo que la persona cobró en los seis meses anteriores (MTSS).",
         "Confirmá siempre las fechas de pago de cada año con tu contador o en el sitio del MTSS.",
         GUARDA)},

    {"clave": "img-vacaciones-atiende", "formato": "imagen", "pilar": "dolor",
     "slides": [_s("Tu negocio atiende *mientras estás de vacaciones*", "Consultas de noche, de afuera y en otro idioma: alguien tiene que contestar.")],
     "caption": _c(
         "En temporada, las consultas llegan a cualquier hora, en dólares y desde números de Argentina o Brasil.",
         "Una respuesta automática en español y portugués, con reservas o pedidos que quedan agendados, hace que no pierdas al que escribe cuando vos no podés.")},

    {"clave": "car-feriados", "formato": "carrusel", "pilar": "servicio",
     "slides": [
         _s("Feriados *sin el celular en la mano*", "Carnaval, Turismo o un fin de semana largo.", "Antes de irte"),
         _s("Mensaje de ausencia *con fecha de vuelta*", "Que nadie se quede esperando sin saber.", "1"),
         _s("Un formulario para *dejar el pedido*", "Queda agendado y no se pierde en el chat.", "2"),
         _s("Los vencimientos *agendados*", "Si un vencimiento cae en feriado, pasa al día hábil siguiente. Igual anotalo.", "3"),
         _s("El día que volvés, *todo ordenado*", "Te mostramos cómo dejarlo andando solo.", cta="Escribinos DEMO"),
     ],
     "caption": _c(
         "Cerrar unos días no tiene por qué significar perder pedidos.",
         "Con un mensaje claro, un lugar donde dejar el pedido y los pendientes ordenados para el día de la vuelta, el negocio sigue recibiendo aunque no estés.",
         GUARDA)},

    {"clave": "car-fechas-comerciales", "formato": "carrusel", "pilar": "propio",
     "slides": [
         _s("Las fechas que *mueven tu año*", "Y qué hacer 10 días antes de cada una.", "Guardalo"),
         _s("Día de la Madre *y del Padre*", "Avisá a tus clientes con tiempo y prepará el stock.", "1"),
         _s("Día del Niño", "Las consultas empiezan dos semanas antes.", "2"),
         _s("Black Friday y *Cyber Monday*", "Atención lista y link de pago a mano.", "3"),
         _s("Fiestas y *Reyes*", "Horarios especiales avisados y entregas organizadas.", "4"),
         _s("Las fechas exactas *las publica la Cámara de Comercio*", "Cada año. ¿Querés que te avisemos las del tuyo?", cta="Escribinos DEMO"),
     ],
     "caption": _c(
         "Cada año, la Cámara de Comercio y Servicios del Uruguay publica el calendario comercial oficial con las fechas de la Madre, el Padre y el Niño.",
         "Diez días antes de cada una es el momento de preparar mensajes, stock y atención.",
         GUARDA)},

    # ── historias ───────────────────────────────────────────────────────────
    {"clave": "his-no-automatizar", "formato": "historia", "pilar": "propio",
     "slides": [_s("Hay cosas que *no automatizaríamos*", "Un reclamo enojado lo atiende una persona.", "Contracorriente", "Escribinos DEMO")],
     "caption": "Historia: lo que no automatizaríamos."},
    {"clave": "his-glosario-api", "formato": "historia", "pilar": "consejo",
     "slides": [_s("API = *el repartidor*", "Lleva los datos de un sistema a otro sin que nadie los copie.", "Glosario sin humo", "Escribinos DEMO")],
     "caption": "Historia: glosario, API."},
    {"clave": "his-mismo-dato", "formato": "historia", "pilar": "dolor",
     "slides": [_s("¿Cuántas veces se escribe *el mismo dato* en tu empresa?", "1-2, 3-4, 5 o más.", "Pregunta", "Escribinos DEMO")],
     "caption": "Historia: el mismo dato cargado varias veces."},
    {"clave": "his-sistema-viejo", "formato": "historia", "pilar": "dolor",
     "slides": [_s("¿Tu sistema *solo anda en una PC?*", "Se puede migrar sin parar la empresa.", "Señal", "Escribinos DEMO")],
     "caption": "Historia: sistema viejo."},
    {"clave": "his-mantenimiento", "formato": "historia", "pilar": "objecion",
     "slides": [_s("No desaparecemos *al entregar*", "Alertas, copias de seguridad y ajustes.", "Después del proyecto", "Escribinos DEMO")],
     "caption": "Historia: mantenimiento."},
    {"clave": "his-llaves", "formato": "historia", "pilar": "consejo",
     "slides": [_s("¿A nombre de quién está *tu dominio?*", "Revisalo hoy.", "Las llaves de tu pyme", "Escribinos DEMO")],
     "caption": "Historia: las llaves de la pyme."},
    {"clave": "his-turno", "formato": "historia", "pilar": "servicio",
     "slides": [_s("Recordatorio *24 h antes*", "Confirmo o reprogramo, en un toque.", "Turnos", "Escribinos DEMO")],
     "caption": "Historia: recordatorio de turnos."},
    {"clave": "his-pedido-estado", "formato": "historia", "pilar": "servicio",
     "slides": [_s("«¿En qué anda *mi pedido?*»", "Que tu cliente lo vea solo.", "Autogestión", "Escribinos DEMO")],
     "caption": "Historia: estado del pedido."},
    {"clave": "his-resenas", "formato": "historia", "pilar": "servicio",
     "slides": [_s("Pedí la reseña *en automático*", "Unos días después de la compra.", "Google", "Escribinos DEMO")],
     "caption": "Historia: reseñas de Google."},
    {"clave": "his-presupuesto", "formato": "historia", "pilar": "caso",
     "slides": [_s("El que contesta primero *se lleva la obra*", "Presupuestos en minutos, no en días.", "Barracas", "Escribinos DEMO")],
     "caption": "Historia: presupuesto rápido."},
    {"clave": "his-contador", "formato": "historia", "pilar": "servicio",
     "slides": [_s("Fin de mes *sin perseguir papeles*", "Un lugar que recibe y archiva cada comprobante.", "Contadores", "Escribinos DEMO")],
     "caption": "Historia: estudio contable."},
    {"clave": "his-chatbot-agente", "formato": "historia", "pilar": "servicio",
     "slides": [_s("El chatbot sigue un guion. *El agente decide.*", "¿Cuál te conviene?", "IA sin humo", "Escribinos DEMO")],
     "caption": "Historia: chatbot vs. agente."},
    {"clave": "his-ia-usa", "formato": "historia", "pilar": "propio",
     "slides": [_s("¿Tu negocio *ya usa IA?*", "Sí, no, o probé y la dejé.", "Pregunta", "Escribinos DEMO")],
     "caption": "Historia: pregunta sobre IA."},
    {"clave": "his-ia-limites", "formato": "historia", "pilar": "objecion",
     "slides": [_s("Bien hecha, la IA *sabe cuándo pasarte la conversación*", "Así la diseñamos.", "Límites", "Escribinos DEMO")],
     "caption": "Historia: límites de la IA."},
    {"clave": "his-agente-noche", "formato": "historia", "pilar": "servicio",
     "slides": [_s("23:00. El agente sigue contestando. *Vos no.*", "Un asistente de WhatsApp para tu pyme.", "Agentes", "Escribinos DEMO")],
     "caption": "Historia: el agente de noche."},
    {"clave": "his-fid-habituales", "formato": "historia", "pilar": "dolor",
     "slides": [_s("¿Sabés quiénes son *tus clientes habituales?*", "Con el QR de la boleta, sí.", "Restaurantes", "Escribinos DEMO")],
     "caption": "Historia: clientes habituales."},
    {"clave": "his-fid-control", "formato": "historia", "pilar": "propio",
     "slides": [_s("¿Tu promo trajo gente *o venían igual?*", "Medimos contra un grupo de control.", "Fidelidad", "Escribinos DEMO")],
     "caption": "Historia: grupo de control."},
    {"clave": "his-fid-recordar", "formato": "historia", "pilar": "consejo",
     "slides": [_s("Tu cliente quiere que *lo recuerdes*", "Su nombre y lo que pide siempre.", "Restaurantes", "Escribinos DEMO")],
     "caption": "Historia: recordar al cliente."},
    {"clave": "his-dias-pico", "formato": "historia", "pilar": "consejo",
     "slides": [_s("¿Tu WhatsApp está listo *para el día de más consultas?*", "Respuestas, link de pago y alguien a cargo.", "Checklist", "Escribinos DEMO")],
     "caption": "Historia: días de más consultas."},
    {"clave": "his-feriado", "formato": "historia", "pilar": "servicio",
     "slides": [_s("Cerrás el feriado, *tu negocio no desaparece*", "Mensaje de ausencia y pedidos agendados.", "Feriados", "Escribinos DEMO")],
     "caption": "Historia: feriados."},
]
