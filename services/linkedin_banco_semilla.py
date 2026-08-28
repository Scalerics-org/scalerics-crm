"""Los posts de LinkedIn ya escritos.

Antes el cron le pedia a Claude que escribiera cada post en el momento. Eso
gastaba API de Anthropic dos veces por semana, todas las semanas, para siempre.
Ahora los textos estan aca: escritos una vez, guardados, y el cron solo elige
cual le toca a cada corrida.

Cada entrada es (tema, angulo, texto, frase):

- `tema`   el titulo, tomado de _LINKEDIN_TEMAS_SEMILLA en database.py
- `angulo` "concreto" (el hecho, con detalle de alguien que estuvo ahi) o
           "implicancia" (que revela ese hecho sobre como trabajan las
           empresas). Cada tema tiene los dos, asi el par que sale en el mismo
           mail nunca suena igual.
- `texto`  el post entero, tal cual se publica
- `frase`  la linea que va en la imagen de la tarjeta

Para agregar posts nuevos: escribilos respetando las reglas de voz del
REGLAS_DE_VOZ de services/linkedin_posts.py y pasalos por validar_borrador()
antes de commitear. Hay un test que corre el validador sobre toda esta lista,
asi que un post que rompa las reglas no llega a produccion.
"""

LINKEDIN_BANCO_SEMILLA = [
    # ── Por qué tu negocio no aparece en Google Maps ───────────────────────
    (
        "Por qué tu negocio no aparece en Google Maps",
        "concreto",
        "La ficha de Google de un taller mecánico tenía el nombre, la dirección "
        "y nada más. Sin horario, sin teléfono, sin una sola foto, sin la "
        "categoría cargada. Aparecía en las búsquedas por nombre y en ninguna "
        "otra.\n\n"
        "Completarla lleva una tarde. Categoría principal bien elegida, horario "
        "real incluidos los feriados, el teléfono como texto y no adentro de "
        "una imagen, diez fotos del local y del trabajo terminado, y una "
        "descripción escrita para alguien que todavía no sabe si sos lo que "
        "busca.\n\n"
        "Google ordena por cercanía, relevancia y prominencia. Sobre la "
        "cercanía no se puede hacer nada. Las otras dos son datos que alguien "
        "tiene que cargar, y casi nunca están cargados.\n\n"
        "#SEOLocal",
        "Completar la ficha lleva una tarde",
    ),
    (
        "Por qué tu negocio no aparece en Google Maps",
        "implicancia",
        "La ficha de Google de una empresa suele estar incompleta, y eso rara "
        "vez es un descuido de marketing. Es el síntoma de que nadie es dueño "
        "de ese activo.\n\n"
        "Pasa siempre igual. La ficha la creó alguien que ya no trabaja ahí, o "
        "la generó Google sola con datos de la calle. Nadie la actualiza "
        "porque nadie la tiene anotada como propia. El día que cambia el "
        "horario de verano, la ficha sigue diciendo lo de antes y el cliente "
        "llega a la puerta cerrada.\n\n"
        "Lo mismo corre para el dominio, la casilla de contacto y la cuenta de "
        "Instagram. Los activos digitales se comportan como cualquier otro "
        "activo de una empresa: sin un responsable con nombre y apellido, se "
        "deterioran solos.\n\n"
        "La pregunta útil no es si la ficha está completa hoy. Es quién la "
        "mira una vez por trimestre.\n\n"
        "#SEOLocal #GestiónEmpresarial",
        "Sin un responsable con nombre, se deterioran solos",
    ),

    # ── Qué es un CRM y por qué tu Excel no lo es ──────────────────────────
    (
        "Qué es un CRM y por qué tu Excel no lo es",
        "concreto",
        "Una planilla de clientes de una distribuidora tenía 900 filas, seis "
        "colores de fondo y tres columnas llamadas estado, estado 2 y estado "
        "final. Nadie sabía cuál era la buena.\n\n"
        "Un CRM hace tres cosas que esa planilla no hace. Avisa: cuando un "
        "cliente lleva veinte días sin respuesta, aparece solo en la lista del "
        "día. Recuerda: cada llamada, cada mail y cada precio que se ofreció "
        "quedan pegados a la ficha y no en la cabeza del vendedor. Y lo ve el "
        "equipo: dos personas escriben al mismo tiempo sin pisarse y sin "
        "mandarse el archivo por WhatsApp.\n\n"
        "La planilla no está mal hecha. Está haciendo un trabajo para el que "
        "nunca fue pensada.\n\n"
        "#CRM",
        "Nadie sabía cuál era la columna buena",
    ),
    (
        "Qué es un CRM y por qué tu Excel no lo es",
        "implicancia",
        "Toda planilla de clientes que crece termina igual: columnas "
        "duplicadas, colores que entiende una sola persona y una copia "
        "distinta en cada computadora.\n\n"
        "Eso no pasa por desprolijidad. Pasa porque una planilla guarda "
        "estados y un negocio necesita guardar historia. El estado dice dónde "
        "está el cliente hoy. La historia dice por qué llegó ahí, qué se le "
        "ofreció, quién lo atendió y qué le prometieron. Cuando solo se guarda "
        "el estado, cada persona reconstruye la historia de memoria, y las "
        "memorias nunca coinciden.\n\n"
        "Ahí aparecen los colores. Son un intento de meter historia en una "
        "herramienta que solo tiene celdas.\n\n"
        "El día que se va el vendedor que sabía leer esos colores, la "
        "información se va con él.\n\n"
        "#CRM #GestiónEmpresarial",
        "Los colores los entiende una sola persona",
    ),

    # ── Cuánto tarda de verdad una tienda online ───────────────────────────
    (
        "Cuánto tarda de verdad una tienda online",
        "concreto",
        "Una tienda online de 300 productos se programa en dos o tres semanas. "
        "Cargar los 300 productos lleva más que eso.\n\n"
        "Cada producto necesita nombre, descripción, precio, stock, categoría, "
        "peso para calcular el envío y al menos una foto decente. Multiplicado "
        "por 300. Y antes de cargar hay que decidir cosas que nunca estuvieron "
        "decididas: si el precio va con IVA incluido, qué pasa con lo que se "
        "vende por kilo, cómo se llama la categoría donde entran los tres "
        "artículos que no entran en ninguna.\n\n"
        "Los plazos que se van de las manos casi nunca se van por el código. "
        "Se van porque el catálogo real no existía en ningún lado antes de "
        "empezar.\n\n"
        "#Ecommerce",
        "Cargar el catálogo lleva más que programarlo",
    ),
    (
        "Cuánto tarda de verdad una tienda online",
        "implicancia",
        "El plazo de una tienda online se estima sobre el desarrollo, y el "
        "desarrollo es la parte que menos se atrasa.\n\n"
        "Lo que se atrasa es el catálogo, y no por volumen. Se atrasa porque "
        "cargarlo obliga a decidir. Precio con IVA o sin IVA. Un producto que "
        "se vende por kilo y también por unidad. Una categoría para las tres "
        "cosas que no encajan en ninguna. Cada una de esas decisiones estaba "
        "pendiente desde hacía años y el negocio funcionaba igual, porque la "
        "persona del mostrador las resolvía de memoria y distinto cada vez.\n\n"
        "Una tienda online no tolera eso. Necesita una respuesta escrita antes "
        "de la primera venta.\n\n"
        "Por eso digitalizar ordena aunque ordenar no fuera el objetivo: "
        "obliga a poner por escrito lo que vivía en la cabeza de alguien.\n\n"
        "#Ecommerce #TransformaciónDigital",
        "Digitalizar obliga a decidir lo que estaba pendiente",
    ),

    # ── El costo de no tener web cuando te buscan por el nombre ────────────
    (
        "El costo de no tener web cuando te buscan por el nombre",
        "concreto",
        "Alguien escucha el nombre de una empresa, lo busca en Google y "
        "encuentra tres cosas: una ficha sin actualizar, un Facebook con la "
        "última publicación de 2019 y un listado de guía telefónica.\n\n"
        "La conclusión que saca no es que no tengan web. Es que eso ya no "
        "existe.\n\n"
        "Una web de una sola página alcanza para evitarlo: qué hace la "
        "empresa, dónde está, cómo se la contacta y a qué hora atiende. Eso es "
        "todo. No hace falta un catálogo, ni un blog, ni un chat.\n\n"
        "El punto no es vender por internet. Alcanza con que el que ya decidió "
        "llamarte encuentre confirmación de que sigue habiendo alguien del "
        "otro lado.\n\n"
        "#PresenciaDigital",
        "El cliente asume que cerraste",
    ),
    (
        "El costo de no tener web cuando te buscan por el nombre",
        "implicancia",
        "Una búsqueda por nombre es la búsqueda de alguien que ya está "
        "convencido. Le pasaron el dato, vio el camión, se lo recomendó un "
        "proveedor. No está comparando: está confirmando.\n\n"
        "Y es la búsqueda que menos se cuida. Se invierte en llegar a gente "
        "nueva mientras el que ya venía decidido se choca con un Facebook "
        "abandonado y saca sus propias conclusiones.\n\n"
        "Toda empresa recibe dos tipos de visitante. El que descubre y el que "
        "verifica. El primero necesita argumentos. El segundo necesita señales "
        "de vida: un horario correcto, una foto de este año, un teléfono que "
        "alguien atiende.\n\n"
        "Las señales de vida cuestan mucho menos que los argumentos, y se "
        "descuidan mucho más.\n\n"
        "#PresenciaDigital #MarketingDigital",
        "El que ya venía decidido también te mira",
    ),

    # ── WhatsApp Business no es lo mismo que WhatsApp ──────────────────────
    (
        "WhatsApp Business no es lo mismo que WhatsApp",
        "concreto",
        "Un comercio atendía 80 consultas por día desde un WhatsApp común. Las "
        "mismas cinco preguntas, escritas a mano cada vez.\n\n"
        "WhatsApp Business es gratis y tiene tres cosas que el común no tiene. "
        "Catálogo: los productos con foto y precio adentro del chat, así el "
        "cliente elige sin que nadie le mande nada. Respuestas rápidas: se "
        "escribe una abreviatura y sale el texto completo de costos y plazos. "
        "Etiquetas: cada conversación queda marcada como consulta, pedido "
        "pendiente o entregado, y la lista de lo que falta se ve de un "
        "vistazo.\n\n"
        "Migrar no borra el historial ni cambia el número. Son diez minutos.\n\n"
        "Lo que cambia es cuánto volumen aguanta la misma persona sin "
        "equivocarse.\n\n"
        "#WhatsAppBusiness",
        "Las mismas cinco preguntas, escritas a mano cada vez",
    ),
    (
        "WhatsApp Business no es lo mismo que WhatsApp",
        "implicancia",
        "El límite de atender por WhatsApp casi nunca es la cantidad de "
        "mensajes. Es cuántas veces por día alguien escribe la misma respuesta "
        "de memoria.\n\n"
        "Escrita de memoria, esa respuesta cambia. El costo de envío que se "
        "dijo el martes no es el que se dice el jueves. El plazo que dio una "
        "persona no es el que da la otra. Nadie miente: cada uno recuerda "
        "distinto, y el cliente se queda con la versión que le tocó.\n\n"
        "Las respuestas rápidas y el catálogo resuelven eso antes que la "
        "velocidad. Una vez que la respuesta está escrita en un solo lugar, "
        "deja de depender de quién atiende y de qué día es.\n\n"
        "La consistencia es lo que hace que una empresa parezca más grande de "
        "lo que es.\n\n"
        "#WhatsAppBusiness #AtenciónAlCliente",
        "Escrita de memoria, esa respuesta cambia",
    ),

    # ── Por qué el pago online sube el ticket promedio ─────────────────────
    (
        "Por qué el pago online sube el ticket promedio",
        "concreto",
        "Una tienda que solo coordinaba por WhatsApp pasó a cobrar online. El "
        "ticket promedio subió, y no porque subieran los precios.\n\n"
        "Lo que pasa es esto. Cuando el pago se arregla después, cada venta "
        "tiene una segunda conversación: el costo de envío, la forma de pago, "
        "cuándo pasa el cadete. En esa conversación el cliente vuelve a "
        "decidir, y bastante seguido decide sacar un producto del pedido o "
        "dejarlo para más adelante.\n\n"
        "Con el pago hecho en el momento esa conversación no existe. El envío "
        "ya estaba calculado en el total y el cliente ya lo aceptó.\n\n"
        "El que ya pagó no negocia el envío.\n\n"
        "#Ecommerce",
        "El que ya pagó no negocia el envío",
    ),
    (
        "Por qué el pago online sube el ticket promedio",
        "implicancia",
        "Cada paso que se agrega entre la decisión de comprar y el cobro es "
        "una oportunidad de arrepentirse, y las oportunidades de arrepentirse "
        "se usan.\n\n"
        "Por eso el pago online mueve el ticket promedio sin tocar los "
        "precios. No convence mejor: cierra antes. La venta queda hecha "
        "mientras el cliente todavía está convencido, en vez de quedar "
        "abierta hasta que alguien conteste el mensaje del costo de envío.\n\n"
        "Lo mismo corre para cualquier proceso de una empresa. Entre el "
        "momento en que alguien decide y el momento en que la decisión queda "
        "firme, todo lo que se interponga tiene un costo. Un formulario de "
        "más, una firma que espera, una autorización que llega el lunes.\n\n"
        "Contar cuántos pasos hay entre la decisión y el hecho suele ser más "
        "útil que contar cuánta gente entró.\n\n"
        "#Ecommerce #Ventas",
        "Cada paso de más es una oportunidad de arrepentirse",
    ),

    # ── Qué mirar antes de contratar a alguien que te haga la web ──────────
    (
        "Qué mirar antes de contratar a alguien que te haga la web",
        "concreto",
        "Antes de firmar por una web conviene tener cinco cosas por escrito.\n\n"
        "Quién queda como titular del dominio: tiene que ser la empresa, con "
        "la casilla de la empresa como contacto. Dónde vive el hosting y con "
        "qué cuenta se paga. Qué se entrega exactamente, cantidad de páginas "
        "incluida. Qué pasa después: quién actualiza precios o textos y cuánto "
        "sale cada cambio. Y cómo se sale: si mañana se cambia de proveedor, "
        "qué se lleva la empresa.\n\n"
        "Ninguna de las cinco es técnica. Las cinco se pueden preguntar sin "
        "saber nada de programación, y las cinco se descubren tarde cuando no "
        "se preguntaron.\n\n"
        "#DesarrolloWeb",
        "Las cinco se descubren tarde si no se preguntan",
    ),
    (
        "Qué mirar antes de contratar a alguien que te haga la web",
        "implicancia",
        "Casi todos los problemas con una web contratada aparecen después de "
        "la entrega, y casi ninguno es técnico.\n\n"
        "El sitio funciona. Lo que no está resuelto es quién puede cambiarle "
        "un precio, a nombre de quién quedó el dominio, con qué cuenta se paga "
        "el hosting y qué pasa el día que quien lo hizo deja de contestar. "
        "Nada de eso se ve mientras las cosas van bien.\n\n"
        "Contratar desarrollo se parece más a contratar un servicio continuo "
        "que a comprar un objeto. Un objeto se entrega y termina. Una web "
        "sigue viva: vence el dominio, cambia el precio, se rompe algo, "
        "alguien tiene que estar.\n\n"
        "Por eso las preguntas que más rinden en la primera reunión no son "
        "sobre tecnología. Son sobre titularidad, mantenimiento y salida.\n\n"
        "#DesarrolloWeb #GestiónEmpresarial",
        "Los problemas aparecen después de la entrega",
    ),

    # ── El dominio es tuyo, no de quien te hizo la web ─────────────────────
    (
        "El dominio es tuyo, no de quien te hizo la web",
        "concreto",
        "El dominio de una empresa se puede consultar en un minuto. Se busca "
        "el nombre en el registro y aparece a nombre de quién está y qué "
        "casilla figura como contacto.\n\n"
        "Vale la pena mirarlo. Si aparece el nombre de la agencia o de quien "
        "programó el sitio, el dominio no es de la empresa: está prestado. Y "
        "el dominio es la dirección donde vive la web y por donde pasan los "
        "correos.\n\n"
        "Cambiar el titular es un trámite, no una pelea, siempre que quien lo "
        "tiene conteste. El momento de hacerlo es ahora, con buena relación, y "
        "no el día que hay que cambiar de proveedor.\n\n"
        "#Dominios",
        "Si está a nombre de otro, está prestado",
    ),
    (
        "El dominio es tuyo, no de quien te hizo la web",
        "implicancia",
        "Un dominio a nombre de quien hizo la web rara vez es mala fe. Suele "
        "ser que alguien lo compró rápido para poder empezar, con su propia "
        "tarjeta, y nadie lo revisó nunca más.\n\n"
        "Ahí está lo interesante. Los activos digitales de una empresa se "
        "crean en el medio de un proyecto, apurados, y quedan a nombre de "
        "quien estaba a mano. El dominio, la cuenta de hosting, el usuario de "
        "Google, la casilla desde donde salen los mails automáticos, el número "
        "de WhatsApp.\n\n"
        "Ninguno figura en ningún inventario. No se compraron, se fueron "
        "creando. Y cada uno es una llave de la empresa que hoy tiene alguien "
        "que capaz ya no trabaja ahí.\n\n"
        "Hacer la lista de esas llaves lleva una tarde y casi nunca se hace.\n\n"
        "#Dominios #GestiónEmpresarial",
        "Cada llave quedó a nombre de quien estaba a mano",
    ),

    # ── Tres números que deberías saber de tu propio negocio ───────────────
    (
        "Tres números que deberías saber de tu propio negocio",
        "concreto",
        "Tres números, y casi ninguna empresa los tiene a mano.\n\n"
        "Cuánto vendés: no la facturación del año, la del mes pasado, separada "
        "por producto o por rubro. Cuánto cuesta conseguir un cliente: lo que "
        "se gastó en publicidad, en tiempo de vendedores y en muestras, "
        "dividido por la cantidad de clientes nuevos. Cuánto vuelve: qué "
        "porcentaje de los que compraron una vez compró de nuevo en el año.\n\n"
        "El primero suele estar. El segundo casi nunca. El tercero es el que "
        "más decisiones cambia y el que menos se calcula.\n\n"
        "No hace falta un sistema para tenerlos. Hace falta que alguien los "
        "calcule una vez por mes, siempre igual.\n\n"
        "#Gestión",
        "El que más decisiones cambia es el tercero",
    ),
    (
        "Tres números que deberías saber de tu propio negocio",
        "implicancia",
        "Cuando una empresa no sabe cuánto le cuesta conseguir un cliente, las "
        "decisiones de publicidad se toman por sensación. Y la sensación "
        "siempre dice lo mismo: lo que se vio funcionar la última vez.\n\n"
        "El problema de fondo no es la falta de datos. Los datos están, "
        "repartidos entre el banco, la planilla del contador, la cabeza del "
        "vendedor y el panel de Instagram. Lo que falta es alguien que los "
        "junte en el mismo lugar una vez por mes.\n\n"
        "Ahí aparece la diferencia entre tener información y tener información "
        "comparable. Un número suelto no dice nada. El mismo número calculado "
        "igual doce veces seguidas dice todo: si mejora, si empeora y desde "
        "cuándo.\n\n"
        "La constancia del cálculo vale más que la precisión del cálculo.\n\n"
        "#Gestión #Datos",
        "Un número suelto no dice nada",
    ),

    # ── Automatizar no es reemplazar gente ─────────────────────────────────
    (
        "Automatizar no es reemplazar gente",
        "concreto",
        "En un depósito una persona pasaba dos horas por día copiando pedidos "
        "del mail a una planilla. No decidía nada: copiaba.\n\n"
        "Eso se automatiza. El pedido entra, se carga solo, y si algo no "
        "coincide queda marcado para que alguien lo mire. Las dos horas "
        "vuelven.\n\n"
        "Lo que no se automatiza es lo que esa misma persona hace el resto del "
        "día: darse cuenta de que un cliente pidió el doble de lo habitual y "
        "llamarlo antes de despachar. Explicarle a otro por qué le conviene el "
        "producto más barato. Decidir a quién se entrega primero cuando no "
        "alcanza el stock.\n\n"
        "La máquina se queda con lo repetido. La persona se queda con lo que "
        "necesita criterio.\n\n"
        "#Automatización",
        "La máquina se queda con lo repetido",
    ),
    (
        "Automatizar no es reemplazar gente",
        "implicancia",
        "Toda automatización que sale bien arranca con la misma pregunta, y no "
        "es qué tarea sacar. Es en qué parte del día alguien está haciendo "
        "algo donde no decide nada.\n\n"
        "Copiar datos de un lado a otro. Reescribir la misma respuesta. "
        "Revisar si llegó un archivo. Nada de eso necesita criterio, y todo "
        "eso se hace igual el lunes que el viernes.\n\n"
        "El error habitual es empezar por lo más visible en vez de por lo más "
        "repetido. Lo visible suele ser justo lo que necesita criterio, y ahí "
        "las automatizaciones fallan feo: deciden mal y nadie se entera hasta "
        "que el cliente reclama.\n\n"
        "Cuando se empieza por lo repetido la gente no se siente reemplazada. "
        "Se siente aliviada, que es lo que hace que la herramienta se use.\n\n"
        "#Automatización #Procesos",
        "Empezá por lo repetido, no por lo visible",
    ),

    # ── Cuándo conviene una web y cuándo alcanza con Instagram ─────────────
    (
        "Cuándo conviene una web y cuándo alcanza con Instagram",
        "concreto",
        "Instagram alcanza mientras se cumplan dos cosas: el catálogo entra en "
        "una grilla de fotos y alguien contesta los mensajes en el horario en "
        "que la gente pregunta.\n\n"
        "Cuando el catálogo pasa de treinta o cuarenta productos, la grilla "
        "deja de servir. Nadie baja hasta el posteo de hace ocho meses para "
        "ver si todavía está ese modelo, y el precio que figura ahí ya no es "
        "el de hoy.\n\n"
        "Cuando las preguntas llegan a las once de la noche o el domingo, "
        "tampoco alcanza. Un catálogo con precios contesta solo.\n\n"
        "Hasta ahí, Instagram y un WhatsApp Business bien armado hacen el "
        "trabajo, y gastar en una web es apurarse.\n\n"
        "#PresenciaDigital",
        "El límite lo pone el catálogo y el horario",
    ),
    (
        "Cuándo conviene una web y cuándo alcanza con Instagram",
        "implicancia",
        "La pregunta de si hace falta una web casi siempre se responde mirando "
        "el producto, y se responde mejor mirando cuándo pregunta la gente.\n\n"
        "Una empresa donde las consultas llegan de nueve a seis, en días "
        "hábiles, puede vivir años con Instagram y WhatsApp. Una donde la "
        "mitad de las preguntas cae fuera de hora tiene un problema distinto: "
        "cada consulta sin responder envejece hasta la mañana siguiente, y una "
        "parte se pierde en el camino.\n\n"
        "Una web no vende sola. Lo que hace es contestar cuando no hay nadie. "
        "Precio, horario, stock, cómo se llega, si hacen envíos.\n\n"
        "Por eso la decisión no depende del tamaño. Depende de cuánta de la "
        "demanda ocurre cuando el local está cerrado.\n\n"
        "#PresenciaDigital #Ecommerce",
        "Una web contesta cuando no hay nadie",
    ),

    # ── Por qué los formularios de contacto no reciben nada ────────────────
    (
        "Por qué los formularios de contacto no reciben nada",
        "concreto",
        "Un formulario de contacto que no recibe nada casi siempre está "
        "mandando bien. El problema está del otro lado.\n\n"
        "Los tres motivos, en orden de frecuencia. El correo va a una casilla "
        "que nadie abre, de esas que se crean el día que se hace el sitio. El "
        "mensaje llega pero cae en spam, porque el sitio manda desde un "
        "dominio que no está autorizado a mandar. O el formulario no manda a "
        "ningún lado: quedó apuntando a la casilla de quien programó la web.\n\n"
        "Se prueba en dos minutos. Alguien completa el formulario con un texto "
        "reconocible y después busca ese texto en la casilla, carpeta de spam "
        "incluida.\n\n"
        "#DesarrolloWeb",
        "Casi siempre el formulario manda bien",
    ),
    (
        "Por qué los formularios de contacto no reciben nada",
        "implicancia",
        "El formulario de contacto es la única parte de una web que nadie "
        "vuelve a probar después del primer día.\n\n"
        "Todo lo demás se ve. Si una foto no carga, se nota. Si un precio "
        "quedó viejo, alguien avisa. El formulario falla en silencio: sigue "
        "mostrando el mensaje de gracias, el visitante se va convencido de que "
        "escribió, y del otro lado no pasa nada.\n\n"
        "Eso lo vuelve el punto ciego más caro de un sitio, porque falla justo "
        "con la gente que más interés tenía.\n\n"
        "La costumbre que lo resuelve es aburrida y funciona: mandarse un "
        "mensaje a través del propio formulario una vez por mes, igual que se "
        "revisa que el matafuego tenga carga.\n\n"
        "#DesarrolloWeb #Ventas",
        "El formulario falla en silencio",
    ),

    # ── El stock que no cuadra sale caro dos veces ─────────────────────────
    (
        "El stock que no cuadra sale caro dos veces",
        "concreto",
        "El stock que no cuadra cuesta plata dos veces.\n\n"
        "La primera cuando se vende algo que no está. Alguien pagó, hay que "
        "avisarle, devolverle o hacerlo esperar, y esa persona no vuelve a "
        "comprar con la misma confianza.\n\n"
        "La segunda es más silenciosa. Es el producto que sí está y figura en "
        "cero, así que nadie lo ofrece. Queda en el depósito ocupando lugar y "
        "plata, mientras el vendedor le dice al cliente que no hay.\n\n"
        "La primera se nota porque el cliente reclama. La segunda no la "
        "reclama nadie, y bastante seguido es la más grande de las dos.\n\n"
        "#Stock",
        "El que sí está y figura en cero no reclama",
    ),
    (
        "El stock que no cuadra sale caro dos veces",
        "implicancia",
        "En casi todos los depósitos el stock del sistema y el de la góndola "
        "no coinciden, y el número que se usa para decidir termina siendo el "
        "que alguien recuerda.\n\n"
        "Eso no se arregla con un inventario. Un inventario deja los números "
        "iguales un día. Lo que hace que se vuelvan a separar es que hay "
        "movimientos que nadie registra: la muestra que se llevó el vendedor, "
        "el producto roto que se tiró, el cambio que entró y volvió a la "
        "góndola sin pasar por ningún papel.\n\n"
        "Mientras esos movimientos no tengan dónde anotarse, el sistema va a "
        "mentir de nuevo en tres semanas.\n\n"
        "Por eso ordenar el stock empieza por listar todas las formas en que "
        "un producto puede salir del depósito. Suelen ser más de las que están "
        "contempladas.\n\n"
        "#Stock #Procesos",
        "Un inventario los deja iguales un solo día",
    ),

    # ── Qué hace un sistema de gestión que no hace una planilla ────────────
    (
        "Qué hace un sistema de gestión que no hace una planilla",
        "concreto",
        "Una planilla y un sistema de gestión se parecen hasta que pasan tres "
        "cosas.\n\n"
        "Dos personas escriben al mismo tiempo. En la planilla compartida una "
        "pisa a la otra, o aparece una copia con el nombre de quien la abrió "
        "último. En un sistema las dos guardan y los dos cambios quedan.\n\n"
        "Alguien pregunta qué pasó con un pedido de marzo. La planilla tiene "
        "el estado de hoy. El sistema tiene quién lo cambió, cuándo y qué "
        "decía antes.\n\n"
        "Entra alguien nuevo. En la planilla ve todo, costos incluidos. En un "
        "sistema ve lo que le toca.\n\n"
        "Historial, permisos y trabajo simultáneo. Eso es lo que se compra.\n\n"
        "#Sistemas",
        "Historial, permisos y trabajo simultáneo",
    ),
    (
        "Qué hace un sistema de gestión que no hace una planilla",
        "implicancia",
        "Toda empresa que crece llega al mismo punto: la planilla sigue "
        "funcionando, pero ya nadie confía del todo en ella.\n\n"
        "La desconfianza no viene de los errores. Viene de que no se puede "
        "reconstruir nada. Cuando un número está raro, no hay forma de saber "
        "si lo cambió alguien, cuándo, ni qué decía antes. Entonces se "
        "pregunta, y la respuesta llega de memoria.\n\n"
        "Ahí es donde un sistema cambia algo real, y no es la prolijidad. Es "
        "que las conversaciones dejan de ser sobre qué pasó y pasan a ser "
        "sobre qué hacer.\n\n"
        "Una empresa que discute los datos en cada reunión está gastando el "
        "tiempo caro de todos en algo que una herramienta resuelve sola.\n\n"
        "#Sistemas #GestiónEmpresarial",
        "Ya nadie confía del todo en la planilla",
    ),

    # ── Facturación electrónica en Uruguay sin dolor de cabeza ─────────────
    (
        "Facturación electrónica en Uruguay sin dolor de cabeza",
        "concreto",
        "La factura electrónica ya está resuelta: hay proveedores autorizados "
        "que la emiten y cobran por documento o por plan.\n\n"
        "La decisión que importa es otra. Si el sistema que ya usás se integra "
        "con el emisor o no.\n\n"
        "Sin integración, alguien carga la venta dos veces: una en el sistema "
        "y otra en la web del emisor. Funciona, y es un error de tipeo "
        "esperando su turno, todos los días.\n\n"
        "Con integración, la venta se carga una vez y la factura sale sola con "
        "los datos que ya están cargados.\n\n"
        "Antes de elegir emisor conviene preguntar dos cosas: si tiene API y "
        "si quien mantiene tu sistema ya la usó.\n\n"
        "#FacturaciónElectrónica",
        "Sin integración alguien carga la venta dos veces",
    ),
    (
        "Facturación electrónica en Uruguay sin dolor de cabeza",
        "implicancia",
        "La factura electrónica se trata como un trámite, y es la primera vez "
        "que muchas empresas tienen todas sus ventas en formato de datos.\n\n"
        "Ahí está lo que casi nadie usa. Cada comprobante tiene fecha, "
        "cliente, producto, cantidad e importe, estructurado, sin que nadie lo "
        "cargue a mano en ningún lado. Es exactamente la información que hace "
        "falta para saber qué se vende, a quién y cada cuánto.\n\n"
        "La mayoría la emite, la archiva y no la vuelve a mirar. Queda ahí, "
        "completa y ordenada, esperando que alguien la lea.\n\n"
        "Cumplir con la obligación es el piso. El dato ya lo estás generando "
        "igual.\n\n"
        "#FacturaciónElectrónica #Datos",
        "El dato ya lo estás generando igual",
    ),

    # ── Cómo se ve tu negocio desde un celular ─────────────────────────────
    (
        "Cómo se ve tu negocio desde un celular",
        "concreto",
        "Más de la mitad de las visitas a un sitio entra desde un teléfono, y "
        "en el teléfono se ve otra cosa.\n\n"
        "Lo que más falla, en orden. El teléfono no es un enlace: se ve el "
        "número pero al tocarlo no llama. El menú se desarma o tapa el "
        "contenido. Los textos quedan chicos y hay que agrandar con los dedos "
        "para leer un precio. Y el formulario pide datos con un teclado que "
        "tapa justo el campo que se está completando.\n\n"
        "Se revisa sin herramientas: abrir el sitio en el propio teléfono e "
        "intentar hacer lo que haría un cliente. Encontrar el horario, ver un "
        "precio, mandar un mensaje.\n\n"
        "Si algo cuesta, ya está encontrado.\n\n"
        "#DiseñoWeb",
        "Abrilo en tu teléfono e intentá comprar",
    ),
    (
        "Cómo se ve tu negocio desde un celular",
        "implicancia",
        "Casi todas las webs se diseñan mirando una pantalla grande y se usan "
        "desde un teléfono, parado, con una mano y con poca paciencia.\n\n"
        "Esa diferencia explica más problemas que cualquier detalle técnico. "
        "En la pantalla grande todo entra y todo se ve a la vez, así que "
        "agregar una sección más no cuesta nada. En el teléfono cada sección "
        "empuja a la siguiente hacia abajo, y lo que quedó abajo no existe.\n\n"
        "Por eso las webs que funcionan en el teléfono terminan siendo más "
        "cortas, no más adaptadas. La restricción obliga a decidir qué es lo "
        "primero que alguien tiene que ver, y esa decisión mejora también la "
        "versión grande.\n\n"
        "Diseñar para la pantalla chica es una forma de ordenar prioridades.\n\n"
        "#DiseñoWeb #Ecommerce",
        "Lo que quedó abajo no existe",
    ),

    # ── Cuando tu web tarda más de tres segundos ya perdiste visitas ───────
    (
        "Cuando tu web tarda más de tres segundos ya perdiste visitas",
        "concreto",
        "Tres segundos es el umbral donde se empieza a perder gente, y casi "
        "siempre el culpable son las fotos.\n\n"
        "Una foto sacada con el celular pesa cuatro o cinco megas. Diez de "
        "esas en la portada son cincuenta megas que el visitante baja con sus "
        "datos móviles, parado en la calle. La misma foto, achicada al ancho "
        "real en que se muestra y guardada en un formato moderno, pesa cien "
        "veces menos y se ve igual.\n\n"
        "Es el arreglo más barato que tiene una web y el que más se posterga, "
        "porque no se nota desde una oficina con fibra.\n\n"
        "Se mide gratis con PageSpeed Insights de Google, poniendo la "
        "dirección del sitio.\n\n"
        "#Rendimiento",
        "No se nota desde una oficina con fibra",
    ),
    (
        "Cuando tu web tarda más de tres segundos ya perdiste visitas",
        "implicancia",
        "La velocidad de un sitio se trata como un tema técnico y se decide "
        "como uno de negocio, casi siempre sin darse cuenta.\n\n"
        "Cada elemento que se suma a una portada tiene un costo en segundos: "
        "el video de fondo, el chat flotante, el aviso de cookies, el mapa "
        "incrustado, las tres tipografías. Ninguno parece caro por separado y "
        "todos se aprueban por separado.\n\n"
        "Después el sitio tarda seis segundos y se busca la causa en el "
        "hosting.\n\n"
        "Lo que ordena esa discusión es preguntar, por cada agregado, a cuánta "
        "gente le sirve. El chat flotante lo usa una fracción de los "
        "visitantes. La demora la pagan todos.\n\n"
        "Un sitio rápido casi nunca es el que se optimizó. Es el que dijo que "
        "no varias veces.\n\n"
        "#Rendimiento #DiseñoWeb",
        "La demora la pagan todos",
    ),

    # ── Las reseñas de Google se responden todas ───────────────────────────
    (
        "Las reseñas de Google se responden todas",
        "concreto",
        "Las reseñas de Google se responden todas, y las malas primero.\n\n"
        "Una respuesta a una reseña mala no está escrita para quien la dejó. "
        "Está escrita para el que va a leer las dos cosas dentro de seis "
        "meses, cuando esté decidiendo. Ese lector no compara la reseña contra "
        "nada: compara cómo reaccionó la empresa.\n\n"
        "La fórmula que funciona es corta. Agradecer, reconocer el hecho "
        "concreto sin discutirlo, decir qué se hizo o se va a hacer, y ofrecer "
        "seguir por privado. Sin excusas largas y sin explicar el contexto "
        "interno, que no le interesa a nadie.\n\n"
        "Una reseña mala bien respondida convence más que diez de cinco "
        "estrellas sin una sola respuesta.\n\n"
        "#ReputaciónOnline",
        "Está escrita para el que la lea en seis meses",
    ),
    (
        "Las reseñas de Google se responden todas",
        "implicancia",
        "Un negocio con veinte reseñas de cinco estrellas y ninguna respuesta "
        "transmite menos confianza que uno con una reseña mala contestada "
        "bien.\n\n"
        "La razón es que las reseñas perfectas no se leen como información. Se "
        "leen como decoración, o directamente como algo comprado. La respuesta "
        "es lo único de esa página que no se puede fingir: muestra cómo trata "
        "la empresa a alguien que se quejó.\n\n"
        "Ahí está el valor de las reseñas malas, que casi siempre se viven "
        "como un problema a borrar. Son la única oportunidad pública de "
        "mostrar cómo se resuelve algo cuando sale mal, que es exactamente lo "
        "que quiere saber quien está por dejar una seña.\n\n"
        "Nadie espera que no falle nunca. Esperan saber qué pasa cuando "
        "falla.\n\n"
        "#ReputaciónOnline #AtenciónAlCliente",
        "Nadie espera que no falle nunca",
    ),

    # ── Qué pasa con tus datos si se rompe la computadora del mostrador ────
    (
        "Qué pasa con tus datos si se rompe la computadora del mostrador",
        "concreto",
        "Si la información de una empresa vive en la computadora del "
        "mostrador, esa empresa está a un disco roto de arrancar de cero.\n\n"
        "Un backup que sirve cumple tres condiciones. Está en otro lugar "
        "físico, no en un disco externo arriba del mismo escritorio. Es "
        "automático, porque el que depende de que alguien se acuerde el "
        "viernes deja de hacerse en marzo. Y se probó al menos una vez "
        "restaurando un archivo, porque un backup que nunca se restauró es una "
        "carpeta con nombre de backup.\n\n"
        "La tercera es la que casi nunca se cumple y la única que da "
        "certeza.\n\n"
        "#Backups",
        "Un backup que nunca se restauró no es un backup",
    ),
    (
        "Qué pasa con tus datos si se rompe la computadora del mostrador",
        "implicancia",
        "La pérdida de datos casi nunca llega por donde se la espera. No es el "
        "incendio ni el robo: es un disco que deja de arrancar un martes "
        "cualquiera.\n\n"
        "Y el daño no se mide en archivos. Se mide en cuántos días de trabajo "
        "hay que rehacer y en cuántas cosas dejan de poder responderse. Qué le "
        "debe cada cliente. Qué se pidió y no llegó. Qué precio se acordó en "
        "marzo.\n\n"
        "Por eso la pregunta útil no es si hay backup. Es cuánto tiempo puede "
        "estar la empresa sin esa información antes de que se note afuera. "
        "Para algunas son días. Para otras, dos horas.\n\n"
        "Esa respuesta es la que decide cuánto conviene gastar, y se puede "
        "contestar sin saber nada de tecnología.\n\n"
        "#Backups #Continuidad",
        "Cuánto podés estar sin esa información",
    ),

    # ── Por qué pedir presupuesto por entregable y no por horas ────────────
    (
        "Por qué pedir presupuesto por entregable y no por horas",
        "concreto",
        "Un presupuesto por horas dice cuánto se va a trabajar. Uno por "
        "entregable dice qué se va a recibir. Solo el segundo se puede aprobar "
        "sin adivinar.\n\n"
        "La diferencia se ve al final. Con horas, la conversación de cierre es "
        "sobre cuántas se usaron y por qué fueron más de las estimadas. Con "
        "entregables, es sobre si está lo que estaba en la lista.\n\n"
        "Un buen presupuesto por entregable dice qué incluye y qué no, en "
        "cuántas etapas se entrega, qué hace falta de tu lado para cada una, y "
        "qué pasa si aparece algo nuevo en el medio.\n\n"
        "Las horas siguen existiendo. Son problema de quien las estima.\n\n"
        "#Presupuestos",
        "Las horas no te dicen qué te llevás",
    ),
    (
        "Por qué pedir presupuesto por entregable y no por horas",
        "implicancia",
        "Cuando un trabajo se cotiza por horas, el riesgo del cálculo queda "
        "del lado del cliente. Si la estimación fue corta, alguien tiene que "
        "pagar la diferencia, y esa conversación llega siempre con el trabajo "
        "empezado.\n\n"
        "Por entregable el riesgo cambia de lado. Quien estima mal absorbe su "
        "error, que es donde el error se puede corregir para la próxima.\n\n"
        "Eso explica algo que se ve seguido: el proveedor que cotiza por "
        "entregable pregunta bastante más antes de pasar el número. No es "
        "burocracia. Está midiendo un riesgo que va a ser suyo.\n\n"
        "Las preguntas incómodas de la primera reunión suelen ser la mejor "
        "señal de que el presupuesto va a valer algo.\n\n"
        "#Presupuestos #GestiónEmpresarial",
        "El que absorbe el error es el que estima",
    ),

    # ── La diferencia entre una web y un catálogo online ───────────────────
    (
        "La diferencia entre una web y un catálogo online",
        "concreto",
        "Una web informa. Un catálogo online vende. La diferencia práctica es "
        "qué puede hacer alguien a las once de la noche.\n\n"
        "En una web ve los productos, se entusiasma y escribe un mensaje. Ese "
        "mensaje se contesta a la mañana. Para entonces una parte de esa gente "
        "ya compró en otro lado o se le pasaron las ganas.\n\n"
        "En un catálogo online elige, ve el precio final con el envío "
        "incluido, paga y recibe la confirmación. La venta quedó hecha "
        "mientras el local estaba cerrado.\n\n"
        "No siempre conviene el segundo: cargar y mantener un catálogo es "
        "trabajo permanente. Pero la decisión se toma mirando eso, y no cuál "
        "se ve mejor.\n\n"
        "#Ecommerce",
        "Qué puede hacer alguien a las once de la noche",
    ),
    (
        "La diferencia entre una web y un catálogo online",
        "implicancia",
        "La diferencia entre una web y un catálogo online no está en el sitio. "
        "Está en quién queda a cargo del siguiente paso.\n\n"
        "Una web deja la pelota del lado de la empresa: alguien tiene que "
        "contestar. Un catálogo la deja del lado del cliente, que puede "
        "terminar solo.\n\n"
        "Por eso un catálogo online cambia la operación más que la imagen. "
        "Aparecen preguntas que antes se resolvían hablando: cuánto sale el "
        "envío a cada zona, qué pasa si no hay stock, hasta qué hora se "
        "despacha lo del día. Todas tenían respuesta, y ninguna estaba "
        "escrita.\n\n"
        "Vender online obliga a poner esas reglas por escrito, y esa es la "
        "parte del trabajo que no se ve en la pantalla.\n\n"
        "#Ecommerce #Procesos",
        "Quién queda a cargo del siguiente paso",
    ),

    # ── Click and collect: vender online y entregar en el local ────────────
    (
        "Click and collect: vender online y entregar en el local",
        "concreto",
        "Click and collect es vender online y entregar en el local. El cliente "
        "elige, paga y pasa a retirar.\n\n"
        "Resuelve dos cosas a la vez. El costo de envío desaparece, que es "
        "donde se cae buena parte de los carritos. Y el cliente entra al "
        "local, que es donde se venden las cosas que no estaba buscando.\n\n"
        "Lo que hay que tener resuelto antes de prenderlo es corto. Un lugar "
        "en el mostrador donde esperan los pedidos ya armados. Un aviso al "
        "cliente cuando está listo, no cuando se hizo la compra. Y hasta "
        "cuándo se guarda si no lo pasa a buscar.\n\n"
        "Sin esas tres, el pedido queda dando vueltas y alguien lo vende dos "
        "veces.\n\n"
        "#ClickAndCollect",
        "El cliente entra al local, que es donde se vende",
    ),
    (
        "Click and collect: vender online y entregar en el local",
        "implicancia",
        "El click and collect se presenta como una comodidad para el cliente, "
        "y para la empresa es otra cosa: la forma más barata de vender "
        "online.\n\n"
        "No hay logística que armar, no hay costo de envío que negociar y no "
        "hay reclamo por un paquete que no llegó. Todo lo que rompe la "
        "rentabilidad de una tienda online que recién arranca queda afuera.\n\n"
        "Y trae algo que el envío no trae. El cliente aparece en el local, con "
        "el pedido ya pagado y sin apuro, en el momento exacto en que está "
        "bien predispuesto.\n\n"
        "Para muchos comercios el camino razonable no es tienda online "
        "completa. Es empezar por el retiro en el local, aprender cómo se "
        "comporta la demanda y recién ahí decidir si el envío vale la pena.\n\n"
        "#ClickAndCollect #Ecommerce",
        "La forma más barata de vender online",
    ),

    # ── Cuánto cuesta mantener una web al año ──────────────────────────────
    (
        "Cuánto cuesta mantener una web al año",
        "concreto",
        "Una web tiene tres costos anuales y conviene tenerlos escritos antes "
        "de encargarla.\n\n"
        "El dominio: se paga una vez por año y es barato. El hosting: mensual "
        "o anual, y varía según si el sitio es estático o corre una tienda con "
        "base de datos. Y el tercero, que es el que sorprende: las horas de "
        "quien la actualiza.\n\n"
        "Ese tercero es el único que depende de cómo esté hecho el sitio. Si "
        "cada cambio de precio necesita que alguien toque código, se paga por "
        "cambio para siempre. Si el sitio tiene un panel donde alguien de la "
        "empresa edita textos y precios, ese costo tiende a cero.\n\n"
        "La pregunta que ahorra plata a diez años es quién va a poder cambiar "
        "un precio.\n\n"
        "#DesarrolloWeb",
        "Quién va a poder cambiar un precio",
    ),
    (
        "Cuánto cuesta mantener una web al año",
        "implicancia",
        "El costo de una web se discute entero en el momento de encargarla, "
        "que es cuando menos se sabe cuánto va a costar tenerla.\n\n"
        "Dominio y hosting son previsibles y bajos. Lo que se lleva la plata a "
        "lo largo de los años es la dependencia: cuántas veces por año hay que "
        "pedirle a alguien de afuera un cambio que adentro nadie puede "
        "hacer.\n\n"
        "Ahí se separan dos formas de comprar lo mismo. Una entrega un sitio "
        "terminado. La otra entrega un sitio que la empresa puede mantener. La "
        "segunda cuesta un poco más al principio y bastante menos después.\n\n"
        "Esa diferencia casi nunca aparece en la comparación de presupuestos, "
        "porque el presupuesto cotiza la entrega y el costo real está en los "
        "cinco años que siguen.\n\n"
        "#DesarrolloWeb #Presupuestos",
        "El costo real está en los cinco años que siguen",
    ),

    # ── El error de tener el teléfono solo en la foto de portada ───────────
    (
        "El error de tener el teléfono solo en la foto de portada",
        "concreto",
        "Un teléfono que solo está adentro de una imagen es un teléfono que "
        "casi nadie usa.\n\n"
        "Desde un celular, un número escrito como texto se toca y llama. El "
        "mismo número dentro de una foto de portada obliga a memorizarlo, "
        "salir de la página, abrir el teclado y escribirlo. Una parte de la "
        "gente no lo hace.\n\n"
        "Pasa igual con la dirección y con el horario cuando están adentro de "
        "un flyer. Google tampoco los lee: para un buscador, esa imagen no "
        "dice nada.\n\n"
        "El arreglo lleva cinco minutos y consiste en escribir la misma "
        "información abajo de la imagen, como texto.\n\n"
        "#DiseñoWeb",
        "Nadie transcribe un número de una imagen",
    ),
    (
        "El error de tener el teléfono solo en la foto de portada",
        "implicancia",
        "Poner información adentro de una imagen es cómodo de publicar y caro "
        "de usar, y esa asimetría explica bastante de cómo se comunican las "
        "empresas.\n\n"
        "Un flyer se arma en diez minutos y queda lindo. Escribir la misma "
        "información como texto lleva más tiempo y queda menos vistoso. La "
        "primera opción gana casi siempre.\n\n"
        "El costo aparece después, repartido en pedazos chicos que nadie "
        "atribuye a esa decisión. El que no pudo tocar el teléfono. El "
        "buscador que no indexó el horario. La persona con problemas de visión "
        "que no pudo agrandar el texto.\n\n"
        "Todo lo que una empresa quiere que se use tiene que estar como texto. "
        "La imagen acompaña.\n\n"
        "#DiseñoWeb #Accesibilidad",
        "Cómodo de publicar, caro de usar",
    ),

    # ── Qué datos pedir en un formulario y cuáles sobran ───────────────────
    (
        "Qué datos pedir en un formulario y cuáles sobran",
        "concreto",
        "Cada campo que se suma a un formulario baja la cantidad de "
        "respuestas, y la mayoría de los campos están de más.\n\n"
        "Para un primer contacto alcanza con tres cosas: cómo se llama, cómo "
        "contactarlo y qué necesita. Nada más.\n\n"
        "Lo que suele sobrar: la empresa, el cargo, el rubro, el presupuesto "
        "estimado, cómo nos conoció. Todo eso es información que sirve para el "
        "CRM, y se pregunta en la conversación que viene después, cuando la "
        "persona ya está hablando.\n\n"
        "Un teléfono obligatorio también espanta. Mucha gente prefiere que le "
        "escriban primero.\n\n"
        "Si un campo no cambia lo que vas a hacer con ese mensaje, sacalo.\n\n"
        "#Formularios",
        "Cada campo de más te cuesta respuestas",
    ),
    (
        "Qué datos pedir en un formulario y cuáles sobran",
        "implicancia",
        "Los formularios largos casi nunca los pide quien atiende al cliente. "
        "Los pide quien va a mirar los datos después.\n\n"
        "Ahí está la tensión real. Cada campo extra le sirve a alguien de "
        "adentro y le cuesta a alguien de afuera, y solo el de adentro "
        "participa de la decisión.\n\n"
        "Es el mismo mecanismo que llena de pasos cualquier proceso de una "
        "empresa. La firma que se agregó por un caso puntual hace tres años. "
        "El dato que pidió una vez el contador. Ninguno se saca nunca, porque "
        "sacar algo obliga a que alguien se haga cargo.\n\n"
        "Los procesos no se complican de golpe. Se complican de a un campo por "
        "vez, siempre con una buena razón.\n\n"
        "#Formularios #Procesos",
        "Se complican de a un campo por vez",
    ),

    # ── Cómo saber si tu publicidad está funcionando ───────────────────────
    (
        "Cómo saber si tu publicidad está funcionando",
        "concreto",
        "Si no podés decir cuántos clientes trajo una campaña, no sabés si "
        "funcionó. Las reacciones y el alcance no contestan esa pregunta.\n\n"
        "Lo mínimo para saberlo son tres cosas. Un número o un enlace distinto "
        "por campaña, así se ve de dónde viene cada consulta. Alguien que "
        "anote, cuando entra un cliente nuevo, cómo llegó. Y una fecha de "
        "corte para comparar, por ejemplo el mes anterior.\n\n"
        "Con eso se calcula lo único que importa: cuánto se gastó dividido por "
        "cuántos clientes nuevos entraron.\n\n"
        "Es un número incómodo la primera vez. También es el único que permite "
        "decidir si conviene repetir.\n\n"
        "#Publicidad",
        "El alcance no dice cuántos clientes trajo",
    ),
    (
        "Cómo saber si tu publicidad está funcionando",
        "implicancia",
        "La publicidad es de lo más medible que hay y termina siendo lo que "
        "más se decide por intuición.\n\n"
        "El motivo es que las plataformas entregan métricas abundantes y "
        "fáciles, y ninguna de ellas es la que hace falta. Alcance, "
        "impresiones, interacciones: todas describen lo que pasó adentro de la "
        "plataforma. Lo que pasó en el negocio queda del otro lado, y ese lado "
        "no lo mide nadie.\n\n"
        "Cerrar esa distancia no necesita herramientas caras. Necesita que "
        "alguien pregunte cómo llegó cada cliente nuevo y lo anote en el mismo "
        "lugar todas las veces.\n\n"
        "Es la parte más aburrida del asunto y la que decide si el resto sirve "
        "para algo.\n\n"
        "#Publicidad #Datos",
        "Lo que pasó en el negocio no lo mide nadie",
    ),

    # ── Integrar el sistema de tu proveedor con el tuyo ────────────────────
    (
        "Integrar el sistema de tu proveedor con el tuyo",
        "concreto",
        "Cuando el proveedor no tiene API, el CSV sigue siendo una respuesta "
        "válida.\n\n"
        "El acuerdo es simple: el proveedor deja un archivo con códigos, "
        "precios y stock en un lugar acordado, con la misma estructura "
        "siempre. Del otro lado, un proceso lo levanta y actualiza. Puede "
        "correr una vez por día y alcanza para la mayoría de los casos.\n\n"
        "Lo que hay que dejar cerrado antes son tres cosas. Que las columnas "
        "no cambien de orden ni de nombre sin avisar. Qué pasa si un producto "
        "desaparece del archivo, que casi nunca significa lo mismo que stock "
        "cero. Y que llegue un aviso cuando el archivo no llegó.\n\n"
        "Sin esa tercera, el sistema queda mostrando precios de la semana "
        "pasada sin que nadie se entere.\n\n"
        "#Integraciones",
        "Sin aviso, quedan precios de la semana pasada",
    ),
    (
        "Integrar el sistema de tu proveedor con el tuyo",
        "implicancia",
        "Las integraciones entre empresas se traban por acuerdos, no por "
        "tecnología.\n\n"
        "Conectar dos sistemas es la parte fácil. Lo difícil es ponerse de "
        "acuerdo en qué significa cada cosa. Si el código de producto del "
        "proveedor es el mismo que el del cliente. Si el precio del archivo "
        "incluye IVA. Si un producto que desapareció de la lista está sin "
        "stock o discontinuado.\n\n"
        "Cada una de esas preguntas tiene una respuesta obvia para cada parte, "
        "y son respuestas distintas. Cuando no se hablan, la integración "
        "funciona igual y empieza a mentir despacio.\n\n"
        "Por eso las integraciones que salen bien empiezan con una reunión "
        "aburrida entre las dos empresas, y no con código.\n\n"
        "#Integraciones #Procesos",
        "Se traban por acuerdos, no por tecnología",
    ),

    # ── Por qué separar la casilla del negocio de la personal ──────────────
    (
        "Por qué separar la casilla del negocio de la personal",
        "concreto",
        "Una casilla del negocio y una personal se separan por una razón "
        "práctica: la del negocio la tiene que poder abrir más de una "
        "persona.\n\n"
        "Con una casilla propia de alguien, cuando esa persona se va de "
        "vacaciones el negocio sigue recibiendo pedidos que nadie lee. Cuando "
        "deja la empresa, se van con ella los mails, los contactos y las "
        "cuentas que estaban registradas ahí.\n\n"
        "Lo mínimo es una casilla con el dominio de la empresa para lo que "
        "entra de afuera, y que las cuentas de servicios queden a nombre de "
        "esa casilla y no de una persona.\n\n"
        "Cuesta poco por mes y evita la conversación incómoda de después.\n\n"
        "#CorreoCorporativo",
        "La del negocio la tiene que abrir más de uno",
    ),
    (
        "Por qué separar la casilla del negocio de la personal",
        "implicancia",
        "Una casilla personal usada para el negocio funciona perfecto hasta el "
        "día en que deja de funcionar, y ese día nunca avisa.\n\n"
        "El problema no es el correo. Es todo lo que se registró con ese "
        "correo. El dominio, el hosting, el Google del negocio, la cuenta del "
        "banco, la del proveedor, la de la factura electrónica. Cada servicio "
        "nuevo se dio de alta con la casilla que estaba a mano.\n\n"
        "Con el tiempo esa casilla se vuelve la llave maestra de la empresa, "
        "sin que nadie lo haya decidido, y está a nombre de una persona.\n\n"
        "Separarla temprano cuesta unos pesos por mes. Separarla tarde es un "
        "trabajo de semanas, y hay cosas que no se recuperan.\n\n"
        "#CorreoCorporativo #GestiónEmpresarial",
        "Se vuelve la llave maestra sin que nadie lo decida",
    ),

    # ── Un correo con tu dominio cuesta menos de lo que pensás ─────────────
    (
        "Un correo con tu dominio cuesta menos de lo que pensás",
        "concreto",
        "Un correo con dominio propio cuesta menos que un almuerzo por mes y "
        "por persona. Para pocas casillas hay opciones gratuitas.\n\n"
        "Lo que cambia no es técnico. Un presupuesto que llega desde una "
        "dirección con el nombre de la empresa se lee distinto que el mismo "
        "presupuesto desde una casilla gratuita con números al final. No "
        "porque el segundo sea peor, sino porque el primero muestra que hay "
        "una empresa atrás y no una persona improvisando.\n\n"
        "Con el dominio ya comprado para la web, el correo es un paso más. Y "
        "hay algo que después no se recupera: los mails viejos que quedaron en "
        "la casilla personal.\n\n"
        "#CorreoCorporativo",
        "Cambia cómo te leen los proveedores",
    ),
    (
        "Un correo con tu dominio cuesta menos de lo que pensás",
        "implicancia",
        "El correo con dominio propio se compra por imagen y sirve sobre todo "
        "para otra cosa: es lo que hace que la empresa exista aparte de las "
        "personas que la forman.\n\n"
        "Con casillas del dominio, cada rol tiene una dirección. Ventas, "
        "administración, compras. Cuando alguien entra o sale, se cambia quién "
        "la lee y el mundo de afuera no se entera de nada. Los proveedores "
        "siguen escribiendo a la misma dirección. Los mails de hace tres años "
        "siguen ahí.\n\n"
        "Sin eso, cada cambio de persona corta un hilo de conversación con "
        "alguien de afuera, y esos hilos son parte del valor de la empresa.\n\n"
        "Cuesta unos pesos por mes y es de las cosas más baratas que separan "
        "una empresa de un grupo de gente que trabaja junta.\n\n"
        "#CorreoCorporativo #GestiónEmpresarial",
        "Hace que la empresa exista aparte de la gente",
    ),

    # ── Qué es el SEO local y por qué te importa más que el otro ───────────
    (
        "Qué es el SEO local y por qué te importa más que el otro",
        "concreto",
        "El SEO local es aparecer cuando alguien busca lo que vendés cerca de "
        "donde está. Es una competencia distinta a la de posicionar una "
        "web.\n\n"
        "Tres cosas lo mueven, en este orden. La ficha de Google completa y "
        "con la categoría principal bien elegida. Las reseñas, cuántas hay y "
        "qué tan recientes son. Y que el nombre, la dirección y el teléfono "
        "aparezcan escritos igual en todos lados: la web, la ficha, las redes, "
        "las guías. Cuando no coinciden, Google duda.\n\n"
        "Es la parte del posicionamiento con mejor relación entre esfuerzo y "
        "resultado, porque la competencia son cinco o diez negocios de la "
        "zona.\n\n"
        "#SEOLocal",
        "Competís contra cinco, no contra el mundo",
    ),
    (
        "Qué es el SEO local y por qué te importa más que el otro",
        "implicancia",
        "Muchas empresas invierten en aparecer en búsquedas generales y "
        "compiten ahí contra todo el país, cuando su cliente está buscando a "
        "diez cuadras.\n\n"
        "La diferencia es enorme. En una búsqueda general se compite contra "
        "cientos de sitios con presupuestos grandes. En una búsqueda local se "
        "compite contra los que están cerca, que suelen tener la ficha a medio "
        "cargar y sin una sola reseña respondida.\n\n"
        "Ese es el punto que se pasa por alto: en lo local, el trabajo que "
        "hace falta para destacar es poco, porque casi nadie lo está "
        "haciendo.\n\n"
        "Antes de invertir en aparecer ante desconocidos conviene asegurarse "
        "de aparecer ante el que ya está a la vuelta y buscando.\n\n"
        "#SEOLocal #MarketingDigital",
        "Tu cliente está buscando a diez cuadras",
    ),

    # ── Digitalizar de a poco también es digitalizar ───────────────────────
    (
        "Digitalizar de a poco también es digitalizar",
        "concreto",
        "Digitalizar de a poco funciona. Lo que no funciona es hacerlo en "
        "cualquier orden.\n\n"
        "El orden que suele servir arranca por donde duele y termina por donde "
        "luce. Primero lo que se hace todos los días y a mano: cargar pedidos, "
        "pasar precios, buscar un dato. Después lo que se pierde: el "
        "seguimiento de los clientes que preguntaron y nadie volvió a llamar. "
        "Recién ahí lo que se ve desde afuera, la web y la tienda.\n\n"
        "Al revés también se hace, y es el camino habitual. Se empieza por la "
        "web porque es lo visible, y adentro se sigue anotando en un "
        "cuaderno.\n\n"
        "Cada paso tiene que dejar algo funcionando solo.\n\n"
        "#TransformaciónDigital",
        "Empezá por donde duele, no por donde luce",
    ),
    (
        "Digitalizar de a poco también es digitalizar",
        "implicancia",
        "Las digitalizaciones que fracasan casi nunca fracasan por la "
        "herramienta. Fracasan porque se hicieron todas juntas.\n\n"
        "Cuando cambia el sistema de ventas, el de stock y el de facturación "
        "el mismo mes, nadie puede saber qué anda mal. Todo anda mal a la vez, "
        "la gente vuelve a lo de antes en paralelo por las dudas, y a los tres "
        "meses hay dos formas de trabajar conviviendo.\n\n"
        "De a un paso por vez pasa otra cosa. Cada cambio se puede evaluar "
        "solo, la gente lo aprende sin miedo, y cuando algo falla se sabe "
        "exactamente qué fue.\n\n"
        "Ir despacio no es tener menos ambición. Es la única forma de saber "
        "qué está funcionando.\n\n"
        "#TransformaciónDigital #Procesos",
        "Todo anda mal a la vez y nadie sabe qué fue",
    ),

    # ── Las tres preguntas antes de comprar cualquier software ─────────────
    (
        "Las tres preguntas antes de comprar cualquier software",
        "concreto",
        "Tres preguntas antes de comprar cualquier software, y ninguna es "
        "sobre funciones.\n\n"
        "Quién lo va a usar. No quién lo decide ni quién lo paga: quién lo va "
        "a abrir todos los días. Si esa persona no estuvo en la demostración, "
        "el sistema arranca con un problema.\n\n"
        "Qué reemplaza. Si no reemplaza nada, se suma a lo que ya se hace y "
        "termina siendo trabajo doble. La respuesta buena nombra algo que va a "
        "dejar de hacerse.\n\n"
        "Cómo se sale. Si en un año no sirve, qué pasa con la información "
        "cargada. Si no se puede exportar, no es una compra: es una mudanza "
        "sin vuelta.\n\n"
        "#Software",
        "Si no reemplaza nada, es trabajo doble",
    ),
    (
        "Las tres preguntas antes de comprar cualquier software",
        "implicancia",
        "El software se compra mirando funciones y se abandona por razones que "
        "no figuran en ninguna lista de funciones.\n\n"
        "Se abandona porque quien lo usa nunca estuvo en la decisión. Porque "
        "se sumó a lo que ya se hacía en vez de reemplazarlo. Porque cargar "
        "los datos iniciales llevaba tres semanas que nadie tenía.\n\n"
        "Las funciones son lo más fácil de comparar y por eso ocupan toda la "
        "conversación. Casi todos los sistemas de una misma categoría hacen "
        "casi lo mismo.\n\n"
        "Lo que los diferencia de verdad es qué tan rápido alguien que no "
        "eligió el sistema puede hacer su trabajo con él. Eso no se ve en una "
        "demostración: se ve dejando que lo pruebe esa persona.\n\n"
        "#Software #GestiónEmpresarial",
        "Se abandona por lo que no figura en la lista",
    ),

    # ── El sistema que nadie usa es un gasto, no una inversión ─────────────
    (
        "El sistema que nadie usa es un gasto, no una inversión",
        "concreto",
        "Un sistema que nadie usa costó lo mismo que uno que se usa.\n\n"
        "La adopción se puede diseñar, y tiene pasos concretos. Que la gente "
        "que lo va a usar participe antes de la compra y no después. Que "
        "arranque con los datos ya cargados, porque nadie adopta un sistema "
        "vacío. Que haya una fecha en la que lo viejo deja de existir, sin "
        "convivencia indefinida. Y que alguien de adentro sea el referente al "
        "que se le pregunta, en vez de un instructivo.\n\n"
        "El error más común es capacitar una sola vez, el día de la "
        "instalación, cuando todavía nadie tiene preguntas. Las preguntas "
        "aparecen a la semana.\n\n"
        "#Adopción",
        "Nadie adopta un sistema vacío",
    ),
    (
        "El sistema que nadie usa es un gasto, no una inversión",
        "implicancia",
        "Cuando un sistema no se usa, la explicación que se da adentro casi "
        "siempre es que la gente se resiste al cambio. Casi siempre es otra "
        "cosa.\n\n"
        "La gente vuelve a lo de antes cuando lo nuevo le hace el trabajo más "
        "largo. Y al principio siempre es más largo: hay que aprender, hay que "
        "cargar cosas que antes se sabían de memoria, hay que hacer clics "
        "donde antes alcanzaba con decirle algo al de al lado.\n\n"
        "Si nadie diseñó cómo se atraviesa ese período, la comparación la hace "
        "cada uno solo, en el peor momento, y gana lo viejo.\n\n"
        "La adopción no es un problema de actitud. Es un problema de diseño, y "
        "se resuelve antes de comprar.\n\n"
        "#Adopción #Procesos",
        "Vuelven a lo viejo porque lo nuevo es más largo",
    ),

    # ── Qué mirar en un contrato de desarrollo de software ─────────────────
    (
        "Qué mirar en un contrato de desarrollo de software",
        "concreto",
        "Un contrato de desarrollo tiene que dejar claras cuatro cosas, y "
        "ninguna necesita abogado para entenderse.\n\n"
        "Qué se entrega: la lista concreta, no una descripción general. "
        "Cuándo, en etapas, y qué hace falta de tu lado para cada una. De "
        "quién es el resultado: código, diseño, dominio y contenido a nombre "
        "de la empresa. Y qué pasa después de la entrega: cuánto tiempo se "
        "corrigen errores sin costo, qué se considera error y qué se considera "
        "cambio.\n\n"
        "Esa última distinción genera casi todas las discusiones, y es la que "
        "casi nunca está escrita.\n\n"
        "#Contratos",
        "Qué es un error y qué es un cambio",
    ),
    (
        "Qué mirar en un contrato de desarrollo de software",
        "implicancia",
        "El punto más conflictivo de un contrato de desarrollo no es el precio "
        "ni el plazo. Es la frontera entre corregir y cambiar.\n\n"
        "Para quien encarga, si algo no funciona como esperaba es un error. "
        "Para quien desarrolla, si funciona como estaba escrito y ahora se "
        "quiere distinto, es un cambio. Las dos lecturas son razonables y "
        "llevan a facturas distintas.\n\n"
        "Esa frontera no se puede definir en abstracto. Se define escribiendo "
        "con suficiente detalle qué tiene que hacer el sistema, antes de "
        "empezar.\n\n"
        "Por eso la parte aburrida de un proyecto, la de escribir qué se "
        "espera, es la que evita el problema caro. Un contrato corto con una "
        "especificación vaga es un contrato largo esperando.\n\n"
        "#Contratos #GestiónEmpresarial",
        "La frontera entre corregir y cambiar",
    ),

    # ── Por qué tu tienda online no vende aunque tenga visitas ─────────────
    (
        "Por qué tu tienda online no vende aunque tenga visitas",
        "concreto",
        "Una tienda con visitas y sin ventas casi siempre pierde a la gente en "
        "el mismo tramo: entre que agregan algo al carrito y que pagan.\n\n"
        "Lo que más lo rompe, en orden. El costo de envío que aparece recién "
        "en el último paso, cuando ya se cargó todo. La obligación de crear "
        "una cuenta antes de comprar. Un formulario largo que pide datos que "
        "no hacen falta para entregar un producto. Y no ver qué medios de pago "
        "se aceptan hasta el final.\n\n"
        "Se mide sin herramientas caras: cuántos carritos se armaron y cuántos "
        "terminaron en compra. Si de diez llegan dos, el problema no está en "
        "la publicidad.\n\n"
        "#Ecommerce",
        "El problema está entre el carrito y el pago",
    ),
    (
        "Por qué tu tienda online no vende aunque tenga visitas",
        "implicancia",
        "Cuando una tienda online no vende, la primera reacción es traer más "
        "visitas. Casi siempre es la salida más cara y la menos efectiva.\n\n"
        "Traer gente cuesta plata todos los meses. Arreglar el tramo donde se "
        "pierde se hace una vez y sirve para siempre, incluida la gente que ya "
        "venía llegando.\n\n"
        "Duplicar el porcentaje de visitantes que compran vale lo mismo que "
        "duplicar las visitas, y no tiene costo recurrente.\n\n"
        "Lo que hace que igual se elija traer más gente es que ese problema se "
        "ve mejor. Las visitas se muestran en un panel. Los que se fueron en "
        "el paso del envío no aparecen en ningún lado, salvo que alguien los "
        "busque.\n\n"
        "Antes de gastar en llegar a más gente conviene mirar qué pasa con la "
        "que ya llegó.\n\n"
        "#Ecommerce #Ventas",
        "Los que se fueron no aparecen en el panel",
    ),

    # ── Los costos de envío decididos tarde matan la venta ─────────────────
    (
        "Los costos de envío decididos tarde matan la venta",
        "concreto",
        "El costo de envío es la razón más común por la que un carrito queda "
        "abandonado, y casi nunca es por el monto. Es por cuándo aparece.\n\n"
        "Un cliente que ve el costo desde el principio lo suma a la decisión y "
        "sigue. El mismo cliente, con el mismo costo mostrado en el último "
        "paso, siente que le cambiaron el precio y se va.\n\n"
        "Hay tres formas de resolverlo. Mostrar el costo por zona en la ficha "
        "del producto. Ofrecer retiro en el local como alternativa visible. O "
        "incluirlo en el precio y anunciar envío sin cargo, que funciona bien "
        "cuando el margen lo aguanta.\n\n"
        "Cualquiera de las tres funciona mejor que mostrarlo tarde.\n\n"
        "#Ecommerce",
        "No es el monto, es cuándo aparece",
    ),
    (
        "Los costos de envío decididos tarde matan la venta",
        "implicancia",
        "El costo de envío no rompe la venta por caro. La rompe porque llega "
        "tarde, y una sorpresa al final se lee como algo que se quiso "
        "esconder.\n\n"
        "Ahí hay algo que sirve fuera del comercio electrónico. Cualquier "
        "costo que aparece después de que alguien ya decidió cambia la "
        "relación, aunque el monto sea razonable. El adicional que no estaba "
        "en el presupuesto. La comisión que se menciona al firmar. El plazo "
        "que se aclara cuando ya se pagó la seña.\n\n"
        "En todos los casos el daño no es económico, es de confianza, y la "
        "confianza cuesta más que la diferencia.\n\n"
        "Poner los costos incómodos adelante hace perder algunas ventas y "
        "salva todas las demás.\n\n"
        "#Ecommerce #AtenciónAlCliente",
        "Una sorpresa al final se lee como algo escondido",
    ),

    # ── Qué información tiene que estar sí o sí en tu web ──────────────────
    (
        "Qué información tiene que estar sí o sí en tu web",
        "concreto",
        "Cuatro cosas tienen que estar en la web de cualquier empresa, y se "
        "encuentran en menos de diez segundos o no están.\n\n"
        "Qué vende, dicho en palabras que use el cliente y no la industria. "
        "Dónde está, con la dirección como texto y un mapa. Cómo se la "
        "contacta, con el teléfono tocable y un WhatsApp si se atiende por "
        "ahí. Y a qué hora abre, con los feriados contemplados.\n\n"
        "Suena obvio y falta seguido. Lo que más falta es el horario, y es lo "
        "que más se busca.\n\n"
        "La prueba es rápida: alguien que no conozca la empresa tiene que "
        "poder contestar esas cuatro cosas sin preguntarle a nadie.\n\n"
        "#DesarrolloWeb",
        "Lo que más falta es el horario",
    ),
    (
        "Qué información tiene que estar sí o sí en tu web",
        "implicancia",
        "Las webs suelen tener de más lo que la empresa quiere contar y de "
        "menos lo que el visitante vino a buscar.\n\n"
        "Es entendible: la escribe quien conoce el negocio, y quien conoce el "
        "negocio ya sabe el horario y la dirección. Lo que le resulta "
        "interesante es la historia, los valores, la trayectoria.\n\n"
        "El visitante llegó por otra cosa. Quiere saber si venden lo que "
        "necesita, si están cerca, si están abiertos y cómo se los contacta. "
        "Si eso está resuelto en diez segundos, capaz después lea el resto.\n\n"
        "Es la diferencia entre escribir para adentro y escribir para el que "
        "llega sin contexto, y explica más problemas de comunicación que "
        "cualquier detalle de diseño.\n\n"
        "#DesarrolloWeb #MarketingDigital",
        "Escribir para el que llega sin contexto",
    ),

    # ── Cómo se organiza un negocio con dos personas y cien pedidos ────────
    (
        "Cómo se organiza un negocio con dos personas y cien pedidos",
        "concreto",
        "Dos personas pueden con cien pedidos si el flujo está claro. Con el "
        "flujo confuso, no pueden con treinta.\n\n"
        "Lo que hay que definir antes de comprar cualquier herramienta son "
        "cinco cosas. Dónde entran los pedidos, y que sea un solo lugar. Quién "
        "los confirma. En qué estados puede estar un pedido, con nombres que "
        "las dos personas usen igual. Quién decide cuando no alcanza el stock. "
        "Y dónde queda anotado lo que se prometió.\n\n"
        "Con eso escrito en una hoja, cualquier herramienta sirve. Sin eso, la "
        "mejor herramienta del mercado se llena de estados que cada uno "
        "interpreta distinto.\n\n"
        "#Procesos",
        "Primero el flujo, después la herramienta",
    ),
    (
        "Cómo se organiza un negocio con dos personas y cien pedidos",
        "implicancia",
        "Cuando dos personas se saturan con el volumen, la reacción habitual "
        "es sumar una tercera o comprar un sistema. Las dos suelen llegar "
        "antes de tiempo.\n\n"
        "El volumen rara vez es el problema. El problema es cuántas veces por "
        "día alguien tiene que preguntarle algo al otro para poder seguir. "
        "Cada una de esas preguntas frena a dos personas y no queda registrada "
        "en ningún lado.\n\n"
        "Escribir el flujo elimina la mayoría de esas preguntas, porque la "
        "respuesta pasa a estar en un lugar en vez de en una cabeza.\n\n"
        "Por eso conviene ordenar antes de agrandar. Una tercera persona sobre "
        "un flujo confuso agrega una cabeza más a la que hay que "
        "preguntarle.\n\n"
        "#Procesos #GestiónEmpresarial",
        "Ordenar antes de agrandar",
    ),

    # ── Cuándo conviene hacerlo a medida y cuándo comprar hecho ────────────
    (
        "Cuándo conviene hacerlo a medida y cuándo comprar hecho",
        "concreto",
        "Lo raro del negocio se hace a medida. Todo lo demás se compra "
        "hecho.\n\n"
        "Facturación, contabilidad, correo, planillas, tienda online estándar: "
        "eso está resuelto, cuesta poco y lo mantiene otro. Hacerlo a medida "
        "es pagar de nuevo algo que ya existe.\n\n"
        "Lo que sí conviene a medida es la parte que no se parece a ninguna "
        "otra empresa. La forma particular de calcular un precio. El flujo que "
        "ningún sistema contempla porque solo lo hacen tres empresas en el "
        "país. La integración entre dos cosas que nadie más tiene juntas.\n\n"
        "La prueba es simple: si al buscarlo aparecen diez opciones, compralo. "
        "Si no aparece ninguna, ahí hay algo a medida.\n\n"
        "#Software",
        "Si aparecen diez opciones, compralo",
    ),
    (
        "Cuándo conviene hacerlo a medida y cuándo comprar hecho",
        "implicancia",
        "Hacer a medida lo que se puede comprar hecho es caro dos veces: se "
        "paga el desarrollo y después se paga el mantenimiento de algo que "
        "otro mantendría gratis.\n\n"
        "El error inverso es menos visible y más frecuente. Comprar hecho algo "
        "que no encaja, y adaptar el negocio al sistema. Ahí se pierde justo "
        "lo que diferencia a la empresa, porque el software estándar impone la "
        "forma promedio de trabajar.\n\n"
        "La pregunta que ordena esa decisión no es cuánto cuesta cada opción. "
        "Es si esa parte del trabajo es una razón por la que los clientes "
        "eligen a la empresa.\n\n"
        "Lo que hace distinta a una empresa se protege. Lo que la hace igual a "
        "todas se compra.\n\n"
        "#Software #GestiónEmpresarial",
        "Lo que te hace distinto se protege",
    ),

    # ── La transformación digital no empieza por la tecnología ─────────────
    (
        "La transformación digital no empieza por la tecnología",
        "concreto",
        "La transformación digital empieza con una hoja y una lapicera, no con "
        "un sistema.\n\n"
        "El ejercicio es escribir cómo se trabaja hoy. Un pedido, desde que "
        "entra hasta que se cobra. Quién lo toca, en qué orden, qué anota cada "
        "uno y dónde lo anota.\n\n"
        "Casi siempre pasan dos cosas. Aparecen pasos que nadie sabía que "
        "existían, hechos por costumbre y sin razón vigente. Y aparecen dos "
        "personas que describen el mismo proceso distinto, cada una convencida "
        "de que el suyo es el que se hace.\n\n"
        "Eso último es el hallazgo que más rinde, y no hay software que lo "
        "revele. Hay que escribirlo.\n\n"
        "#TransformaciónDigital",
        "Dos personas lo describen distinto",
    ),
    (
        "La transformación digital no empieza por la tecnología",
        "implicancia",
        "Comprar tecnología antes de entender el proceso es la forma más cara "
        "de descubrir el proceso.\n\n"
        "Un sistema obliga a definir cosas que estaban sin definir: cuántos "
        "estados tiene un pedido, quién aprueba qué, qué pasa cuando falta "
        "stock. Si esas definiciones no se tomaron antes, se toman durante la "
        "implementación, apurados, y casi siempre las termina tomando quien "
        "está configurando el sistema en vez de quien conoce el negocio.\n\n"
        "Ahí nacen los sistemas que no reflejan cómo trabaja la empresa, y que "
        "después se abandonan.\n\n"
        "Escribir el proceso primero cuesta unas reuniones. Descubrirlo "
        "después cuesta la implementación entera, dos veces.\n\n"
        "#TransformaciónDigital #Procesos",
        "La forma más cara de descubrir el proceso",
    ),

    # ── Por qué el mismo producto tiene tres precios en tres lugares ───────
    (
        "Por qué el mismo producto tiene tres precios en tres lugares",
        "concreto",
        "El mismo producto con tres precios distintos en la web, en el sistema "
        "y en el cartel del mostrador no es error de nadie. Es que hay tres "
        "lugares donde el precio se puede cambiar.\n\n"
        "Mientras existan esos tres lugares, se van a separar siempre. Alguien "
        "actualiza uno con el apuro del día y los otros dos quedan viejos.\n\n"
        "La salida es elegir cuál manda. Un solo lugar donde se cambia el "
        "precio, y los otros dos que lo lean de ahí. Si técnicamente no se "
        "puede, entonces una sola persona con la responsabilidad de cambiar "
        "los tres juntos, siempre.\n\n"
        "Lo que no funciona es pedir que todos tengan cuidado.\n\n"
        "#Datos",
        "Una fuente de verdad o ninguna",
    ),
    (
        "Por qué el mismo producto tiene tres precios en tres lugares",
        "implicancia",
        "Cuando el mismo dato vive en tres lugares no hay tres versiones de la "
        "verdad. No hay ninguna, porque nadie sabe cuál mirar.\n\n"
        "Y el costo no es la confusión interna, que se resuelve preguntando. "
        "El costo es que alguien le dice un precio a un cliente y después hay "
        "que sostenerlo o desdecirse. Las dos salidas cuestan.\n\n"
        "Esto se agrava con cada herramienta nueva. Cada sistema que se suma "
        "trae su propia copia de los productos, los clientes y los precios, y "
        "si nadie definió cuál manda, se suma una versión más.\n\n"
        "Por eso conviene preguntar, antes de sumar cualquier herramienta, de "
        "dónde va a leer los datos que ya existen.\n\n"
        "#Datos #Procesos",
        "No hay tres versiones de la verdad, hay ninguna",
    ),

    # ── Qué hacer con los contactos que juntaste y nunca usaste ────────────
    (
        "Qué hacer con los contactos que juntaste y nunca usaste",
        "concreto",
        "Una lista de contactos vieja vale más que una campaña nueva, y suele "
        "estar tirada en tres lugares.\n\n"
        "Están en la agenda del teléfono, en los chats de WhatsApp y en las "
        "facturas de los últimos años. Es gente que ya compró o ya preguntó: "
        "no hay que convencerla de que la empresa existe.\n\n"
        "Juntarlos lleva una tarde. Un archivo con nombre, contacto, qué "
        "compró y cuándo fue la última vez.\n\n"
        "Con eso ya se puede hacer lo más rentable que hay: escribirle a los "
        "que compraron hace más de un año. No una promoción masiva, un mensaje "
        "que diga algo puntual y verdadero.\n\n"
        "#Clientes",
        "Ya compraron, no hay que convencerlos",
    ),
    (
        "Qué hacer con los contactos que juntaste y nunca usaste",
        "implicancia",
        "Conseguir un cliente nuevo cuesta varias veces más que hacer volver a "
        "uno que ya compró, y casi todo el presupuesto de marketing se va en "
        "lo primero.\n\n"
        "La razón no es económica. Es que los clientes nuevos se cuentan y los "
        "que dejaron de venir no.\n\n"
        "Nadie tiene un panel que diga cuántos clientes de hace dos años no "
        "volvieron este año. Ese número existe en las facturas y no lo mira "
        "nadie, así que la pérdida es invisible y la ganancia es visible.\n\n"
        "La lista de los que dejaron de comprar es probablemente el activo "
        "comercial más valioso que una empresa tiene sin usar, y se arma con "
        "datos que ya están cargados.\n\n"
        "#Clientes #Ventas",
        "Los que dejaron de venir no se cuentan",
    ),
]
