"""Los posts de LinkedIn ya escritos (banco nuevo, 4/11/2026).

Reemplaza al banco de agosto (84 microanécdotas de "un taller", "una planilla
de 900 filas"). Pedido de Juan al ver el primer posteo automático: "No vayas
tan específico con las cosas, podés usar casos de éxito, todo lo que tenemos",
"tenemos que ser muy profesionales" y "no los nombres de los clientes".

Cada entrada es (tema, angulo, objetivo, texto, frase):

- `tema`     el título, único: el cron no repite tema en la misma corrida
- `angulo`   "caso" (algo que armamos, contado por rubro y sin nombrar al
             cliente), "scalerics" (qué hacemos y cómo trabajamos) o "general"
             (un tema de negocio, útil por sí solo)
- `objetivo` lo que persigue el post, una clave de OBJETIVOS
- `texto`    el post entero, tal cual se publica
- `frase`    la línea que va en la imagen de la tarjeta

Pedido de Juan (5/11/2026): que cada post persiga un objetivo: construir
marca, dar confianza, traer clientes, mostrar casos de éxito y hablar del
equipo, del fundador y del CIE. Los posts se escriben agrupados en _POSTS y
salen intercalados por objetivo (ver _intercalar al final), así cada semana
mezcla varios. Reglas: nunca el nombre de un cliente, nada de cifras o
resultados que no estén medidos, y lo que todavía no funciona se cuenta como
lo que viene. Lo demás lo chequea validar_borrador() de
services/linkedin_posts.py, y hay un test que lo corre sobre toda la lista.

Los posts del banco viejo quedan en la base marcados como retirados (ver
seed_linkedin_banco en database.py): no vuelven a salir.
"""

OBJETIVOS = {
    "caso": "Caso de éxito",
    "marca": "Construir marca",
    "clientes": "Traer clientes",
    "equipo": "Equipo",
    "confianza": "Dar confianza",
}

_POSTS = [
    (
        "Club de clientes para una pizzería",
        "caso",
        "caso",
        "Una pizzería quería que sus clientes volvieran más seguido. Tenía buena comida y gente "
        "conforme, pero no sabía quién venía, cada cuánto ni qué pedía.\n\n"
        "Con Scalerics Fidelidad armamos su club de clientes. El cliente escanea el QR del ticket y "
        "suma puntos en cada visita, sin tarjetas ni apps que descargar. Además, cuenta qué pidió, "
        "y así el local empieza a conocer los gustos de cada uno.\n\n"
        "Con esa información, el paso siguiente es proponerle a cada cliente lo que tiene más chances "
        "de traerlo de vuelta, en el momento justo. Y para saber si funciona de verdad, una parte de los "
        "clientes queda sin propuesta: la diferencia entre los dos grupos es lo que el club suma.\n\n"
        "Fidelizar no es regalar puntos. Es conocer a quien ya eligió tu negocio.\n\n"
        "#Fidelizacion #Gastronomia",
        "Fidelizar es conocer a quien ya te eligió",
    ),
    (
        "Captar o fidelizar",
        "general",
        "marca",
        "Muchas empresas invierten casi todo su presupuesto de marketing en conseguir clientes nuevos. "
        "Publicidad, promociones, descuentos de bienvenida. Y casi nada en los que ya compraron.\n\n"
        "Es una decisión que rara vez se toma a conciencia. Pasa porque al cliente nuevo se lo puede "
        "medir: cuánto costó, de dónde vino. Al que vuelve, en cambio, nadie lo cuenta. Si no se sabe "
        "quién volvió, tampoco se sabe a quién se perdió.\n\n"
        "El primer paso no es una campaña. Es saber quiénes son tus clientes, cada cuánto vuelven y "
        "qué los hace volver. Con eso, cada peso de marketing se puede poner donde rinde.\n\n"
        "#Fidelizacion #Marketing",
        "Al cliente que vuelve nadie lo cuenta",
    ),
    (
        "Lo que usamos en Scalerics",
        "scalerics",
        "confianza",
        "En Scalerics trabajamos con el mismo tipo de sistema que armamos para nuestros clientes.\n\n"
        "Los contactos que llegan por la publicidad entran solos al CRM, con su historia. El "
        "seguimiento avisa a quién hay que llamar y cuándo. Los cobros mensuales salen con tarjeta "
        "y se anotan en finanzas sin pasar por una planilla. Y una parte del marketing la hacen "
        "agentes de inteligencia artificial: revisan la pauta todos los días, avisan cuando algo se "
        "desvía y publican el contenido, como este posteo.\n\n"
        "No es tecnología por la tecnología misma. Es que el equipo pase el tiempo hablando con "
        "clientes y no copiando datos de un lado a otro.\n\n"
        "#Automatizacion #CRM",
        "Usamos lo mismo que armamos",
    ),
    (
        "El tiempo de respuesta",
        "general",
        "marca",
        "Cuando alguien completa un formulario o escribe por WhatsApp, en ese momento está pensando "
        "en tu negocio. Unas horas después, probablemente ya habló con otro.\n\n"
        "La velocidad de respuesta casi nunca depende de las ganas del equipo. Depende de que el "
        "contacto llegue a alguien que lo vea a tiempo. Si el aviso entra a una casilla que se revisa "
        "a la tarde, o a un celular que también recibe los mensajes de la familia, se pierde.\n\n"
        "Por eso, antes de pensar en más publicidad, conviene mirar el camino que hace un contacto "
        "desde que llega hasta que alguien lo atiende. Cada paso manual en ese camino es tiempo, y "
        "el tiempo es lo que decide quién se queda con el cliente.\n\n"
        "#Ventas #AtencionAlCliente",
        "El contacto que espera, se va",
    ),
    (
        "Preventa para distribuidoras",
        "caso",
        "caso",
        "En una distribuidora, buena parte del día se va en tomar pedidos: llamadas, mensajes, "
        "planillas que hay que pasar en limpio antes de armar el reparto.\n\n"
        "Para distribuidoras armamos un sistema de preventa. Cada comercio recibe en el celular su "
        "pedido sugerido de la semana, armado con lo que suele comprar. Lo ajusta, lo confirma y listo.\n\n"
        "Del otro lado, la distribuidora ve los pedidos entrar en vivo, arma el depósito de forma "
        "consolidada, imprime los remitos y ordena la ruta de reparto. También ve qué clientes "
        "dejaron de comprar un producto, que suele ser la primera señal de que se están yendo con otro.\n\n"
        "El vendedor deja de ser un tomador de pedidos y vuelve a vender.\n\n"
        "#Distribucion #Automatizacion",
        "El vendedor vuelve a vender",
    ),
    (
        "Planillas que crecen",
        "general",
        "marca",
        "Casi todos los sistemas de gestión empiezan como una planilla. Y está bien: una planilla es "
        "rápida, flexible y la entiende cualquiera.\n\n"
        "El problema aparece cuando la usan varias personas a la vez. Cada una agrega una columna, "
        "aparecen copias con nombres distintos y nadie está seguro de cuál es la última versión. La "
        "planilla sigue funcionando, pero ya no se le puede creer del todo.\n\n"
        "Ese es el momento de pasar a un sistema. No porque la planilla sea mala, sino porque el "
        "negocio creció y necesita un lugar donde cada dato esté una sola vez, con un responsable y "
        "con la historia de quién lo cambió.\n\n"
        "#Gestion #Procesos",
        "Cuando la planilla ya no se puede creer",
    ),
    (
        "Software a medida",
        "scalerics",
        "clientes",
        "En Scalerics hacemos software a medida: sistemas de gestión, tiendas online, apps de "
        "fidelización y automatizaciones para empresas que ya probaron lo enlatado y no les alcanzó.\n\n"
        "El punto de partida nunca es la tecnología. Es entender cómo trabaja el negocio hoy: quién "
        "carga qué, dónde se traba, qué se hace dos veces. Recién con eso claro se decide qué "
        "construir, y muchas veces la respuesta es más chica de lo que el cliente imaginaba.\n\n"
        "Construimos por etapas, con algo funcionando desde las primeras semanas, para que el equipo "
        "lo use y lo corrija antes de seguir sumando.\n\n"
        "#SoftwareAMedida #Uruguay",
        "Primero entender cómo trabaja el negocio",
    ),
    (
        "El cliente que desaparece",
        "general",
        "marca",
        "La mayoría de los clientes que se pierden no se quejan. Simplemente dejan de venir.\n\n"
        "En un comercio con muchos clientes es imposible notarlo a ojo. El que venía todas las "
        "semanas y hace un mes que no aparece se confunde entre todos los demás. Cuando alguien se da "
        "cuenta, ya es tarde para preguntarle qué pasó.\n\n"
        "Con datos de visitas o de compras, en cambio, se puede ver. Un sistema que avise cuando un "
        "cliente habitual cortó su ritmo le da al negocio la oportunidad de hacer algo: un mensaje, "
        "una propuesta, una pregunta. Muchas veces alcanza con eso para que vuelva.\n\n"
        "#Fidelizacion #Datos",
        "El cliente que se va no se queja",
    ),
    (
        "Club de socios para un bar",
        "caso",
        "caso",
        "Para bares y hamburgueserías armamos clubes de socios que se usan desde el celular, sin "
        "descargar nada.\n\n"
        "El socio entra con su cédula, ve sus puntos y las recompensas que puede canjear, con foto de "
        "cada una. En el local, el mozo registra la visita en dos pasos, sin frenar el servicio. Y el "
        "dueño tiene un panel con los socios, cuántos vuelven y qué canjean.\n\n"
        "La clave está en que nadie tenga que cambiar su forma de trabajar para usarlo. Si sumar "
        "puntos lleva más de unos segundos en la caja, el club muere en la primera noche de mucho "
        "movimiento.\n\n"
        "#Fidelizacion #Gastronomia",
        "Si lleva más de unos segundos, no se usa",
    ),
    (
        "Automatizar lo repetitivo",
        "general",
        "marca",
        "En cualquier empresa hay tareas que alguien hace todos los días igual: pasar datos de un "
        "lado a otro, mandar el mismo recordatorio, armar el mismo informe.\n\n"
        "Esas tareas no son difíciles, y por eso nadie las cuestiona. Pero se comen horas de gente "
        "que podría estar atendiendo clientes, y son justamente las que más errores tienen, porque "
        "se hacen en piloto automático.\n\n"
        "Un buen ejercicio es anotar durante una semana todo lo que se copia, se reenvía o se "
        "recalcula a mano. Esa lista suele ser el mejor punto de partida para automatizar, mucho "
        "antes que cualquier herramienta de moda.\n\n"
        "#Automatizacion #Productividad",
        "Anotá todo lo que se copia a mano",
    ),
    (
        "Cobrar sin perseguir",
        "scalerics",
        "clientes",
        "En Scalerics los abonos mensuales se cobran solos con tarjeta. El cliente carga su tarjeta "
        "una vez y cada mes el cobro sale y queda anotado en finanzas, sin que nadie tenga que mandar "
        "un recordatorio ni conciliar un depósito.\n\n"
        "Lo armamos para nosotros porque perseguir pagos es de esas tareas que nadie quiere hacer y "
        "que, cuando se atrasa, complica todo lo demás. Y es el mismo tipo de solución que armamos "
        "para empresas con cuotas, abonos o servicios mensuales.\n\n"
        "Cobrar a tiempo no debería depender de que alguien se acuerde.\n\n"
        "#Finanzas #Automatizacion",
        "Cobrar a tiempo no depende de acordarse",
    ),
    (
        "Medir con grupo de control",
        "general",
        "marca",
        "Cuando una promoción funciona, ¿cómo se sabe que esos clientes no iban a volver igual?\n\n"
        "Es la pregunta que casi nadie se hace. Se lanza un descuento, las ventas suben y se da por "
        "hecho que fue gracias al descuento. A veces es así. Otras veces era fin de mes, o hacía buen "
        "tiempo, o simplemente esos clientes ya venían.\n\n"
        "La forma honesta de medirlo es dejar una parte de los clientes sin la propuesta y comparar. "
        "Lo que vuelve el grupo que la recibió, menos lo que vuelve el que no, es lo que la promoción "
        "realmente sumó. Es un poco más de trabajo y es la única manera de saber si se está "
        "invirtiendo bien.\n\n"
        "#Marketing #Datos",
        "Lo que suma es la diferencia",
    ),
    (
        "Páginas web en tres versiones",
        "caso",
        "caso",
        "Cuando un comercio nos pide una página web, antes de programar nada le mostramos su inicio "
        "en tres estilos distintos: uno oscuro y elegante, uno claro y limpio, y uno más vibrante.\n\n"
        "Las tres muestran lo mismo: sus servicios, sus horarios, cómo contactarlo. Lo que cambia es "
        "cómo se siente. Y es mucho más fácil elegir viendo que imaginando a partir de una "
        "descripción.\n\n"
        "Ese paso ahorra idas y vueltas, pero sobre todo hace que el cliente sienta la página como "
        "propia desde el principio. Una web que el dueño no siente suya termina desactualizada en "
        "pocos meses.\n\n"
        "#PaginasWeb #DiseñoWeb",
        "Es más fácil elegir viendo",
    ),
    (
        "El formulario largo",
        "general",
        "marca",
        "Cada pregunta que se agrega a un formulario tiene un costo: algunas personas lo abandonan "
        "en ese punto.\n\n"
        "Pasa con los formularios de contacto, con los de la publicidad y con los registros de las "
        "apps. Cuando una empresa ve que llegan menos contactos, suele mirar primero la publicidad. "
        "Pero muchas veces la gente sigue llegando igual y lo que cambió es cuántos terminan de "
        "completar.\n\n"
        "Antes de sacar preguntas, conviene saber cuáles se usan de verdad para atender mejor al "
        "contacto y cuáles están ahí porque alguna vez parecieron útiles. Pedir menos al principio y "
        "preguntar el resto en la primera conversación suele funcionar mejor.\n\n"
        "#Marketing #Ventas",
        "Cada pregunta tiene un costo",
    ),
    (
        "Agentes que revisan la pauta",
        "scalerics",
        "marca",
        "En Scalerics la publicidad tiene un agente que la revisa todos los días. Mira cuánto cuesta "
        "cada contacto, compara con los días anteriores y manda un mail cuando algo se desvía.\n\n"
        "Una vez, el costo por contacto subió y la primera sospecha fue que la publicidad se había "
        "encarecido. Los datos mostraron otra cosa: la gente seguía haciendo clic, pero menos personas "
        "terminaban el formulario. El problema estaba en otro lugar.\n\n"
        "Para eso sirve un agente así. No reemplaza a quien maneja la pauta: le ahorra el trabajo de "
        "mirar los números a diario y le marca dónde vale la pena prestar atención.\n\n"
        "#InteligenciaArtificial #Marketing",
        "Los datos mostraron otra cosa",
    ),
    (
        "El dueño como cuello de botella",
        "general",
        "marca",
        "En muchas empresas todo pasa por el dueño. Los precios, las excepciones, los clientes "
        "importantes, las dudas del equipo.\n\n"
        "Al principio es lógico: nadie conoce el negocio como quien lo armó. Pero con el tiempo, el "
        "negocio queda limitado a las horas que esa persona tiene en el día. Cuando se va de "
        "vacaciones, todo se frena.\n\n"
        "Parte de la salida es delegar, y parte es que la información deje de vivir en la cabeza de "
        "una persona. Cuando las reglas están escritas en un sistema, el equipo puede resolver solo "
        "lo que antes tenía que preguntar, y el dueño vuelve a tener tiempo para pensar el negocio.\n\n"
        "#Gestion #Liderazgo",
        "Cuando todo pasa por una sola persona",
    ),
    (
        "Puntos con el ticket",
        "caso",
        "caso",
        "Uno de los mayores obstáculos de un programa de fidelización es pedirle al cliente que haga "
        "algo extra: descargar una app, llevar una tarjeta, dar sus datos en la caja.\n\n"
        "En los clubes de clientes que armamos para gastronomía, el cliente suma puntos escaneando el "
        "código QR del ticket que ya recibe. No hay que instalar nada ni frenar la fila.\n\n"
        "Cuanto menos tiene que hacer el cliente, más gente se suma. Y cuanta más gente se suma, más "
        "útil es la información que el negocio empieza a tener.\n\n"
        "#Fidelizacion #Gastronomia",
        "Cuanto menos tiene que hacer el cliente, mejor",
    ),
    (
        "Software enlatado o a medida",
        "general",
        "marca",
        "Para la mayoría de las tareas comunes, un software enlatado alcanza y sobra. Contabilidad, "
        "facturación, correo: hay productos buenos, probados y baratos.\n\n"
        "El software a medida tiene sentido en otro lugar: en lo que hace distinto a tu negocio. La "
        "forma particular de tomar pedidos, de armar un presupuesto o de atender a un cliente. Ahí, "
        "adaptar el negocio a una herramienta genérica suele salir más caro que construir algo que "
        "se adapte al negocio.\n\n"
        "La pregunta útil no es cuál de los dos es mejor. Es qué parte de tu operación es igual a la "
        "de todos y cuál es la que te diferencia.\n\n"
        "#SoftwareAMedida #Gestion",
        "Lo que te hace distinto merece lo propio",
    ),
    (
        "Recordatorios por WhatsApp",
        "scalerics",
        "marca",
        "Buena parte de los compromisos con clientes se arreglan por WhatsApp: una llamada pendiente, "
        "un presupuesto para mandar el jueves, un pago que quedó para fin de mes. Y después hay que "
        "acordarse.\n\n"
        "En Scalerics un agente lee esas conversaciones y carga los recordatorios solo. Cuando alguien "
        "dice que lo llamemos la semana que viene, el recordatorio queda agendado sin que nadie lo "
        "tenga que anotar.\n\n"
        "WhatsApp es donde se hacen los acuerdos, y un sistema que entienda eso evita que se pierdan "
        "entre cientos de mensajes.\n\n"
        "#WhatsApp #InteligenciaArtificial",
        "Los acuerdos se hacen por WhatsApp",
    ),
    (
        "Inteligencia artificial útil",
        "general",
        "marca",
        "La inteligencia artificial está en todas las conversaciones, y es fácil perderse entre "
        "promesas.\n\n"
        "Donde hoy rinde de verdad es en tareas acotadas y repetitivas: leer mensajes y ordenarlos, "
        "resumir información, revisar números todos los días, redactar un primer borrador. Cosas que "
        "una persona puede hacer, pero que llevan tiempo y nadie disfruta.\n\n"
        "El error más común es empezar por la herramienta. Conviene empezar por el problema: qué "
        "tarea consume horas, qué decisión se toma tarde por falta de información. Si la respuesta "
        "involucra leer o resumir mucho, ahí la inteligencia artificial probablemente tenga algo para "
        "aportar.\n\n"
        "#InteligenciaArtificial #Productividad",
        "Empezar por el problema, no por la herramienta",
    ),
    (
        "Qué pidió cada cliente",
        "caso",
        "caso",
        "El ticket de un restaurante dice cuánto gastó el cliente, pero no qué comió. Y para hacerle "
        "una propuesta que le interese, eso es lo que importa.\n\n"
        "En los clubes de clientes que armamos para gastronomía, después de sumar sus puntos el "
        "cliente puede contar qué pidió, eligiendo con fotos de la carta. Lleva unos segundos y le "
        "suma puntos extra.\n\n"
        "Con el tiempo, el local sabe quién vuelve por la misma pizza de siempre y quién prueba "
        "algo nuevo cada vez. Esa diferencia cambia qué conviene ofrecerle a cada uno.\n\n"
        "#Fidelizacion #Datos",
        "Saber qué pidió cambia qué ofrecerle",
    ),
    (
        "Los datos que ya existen",
        "general",
        "marca",
        "Muchas empresas tienen más información de la que creen. Está en las ventas, en los mensajes "
        "con clientes, en las planillas del depósito. El problema es que está repartida y nadie la "
        "mira junta.\n\n"
        "Antes de pensar en nuevas herramientas para recolectar datos, conviene preguntarse qué se "
        "podría saber con lo que ya hay. Cada cuánto compra un cliente, qué productos se venden "
        "juntos, en qué día de la semana se concentran los pedidos.\n\n"
        "Muchas veces, ordenar lo que existe y verlo en un mismo lugar ya cambia las decisiones de "
        "todos los días.\n\n"
        "#Datos #Gestion",
        "Más información de la que se cree",
    ),
    (
        "Contenido con agentes",
        "scalerics",
        "marca",
        "El contenido de Scalerics en Instagram y LinkedIn lo preparan agentes de inteligencia "
        "artificial, siguiendo la línea de marca que define el equipo.\n\n"
        "Los agentes arman las piezas de la semana, las revisan contra reglas claras de tono y "
        "estilo, y las publican en horario. Antes de publicar, cada pieza pasa controles: que el "
        "texto cumpla las reglas, que tenga su imagen y que no repita algo ya publicado. Si algo no "
        "pasa, no sale y el equipo recibe un aviso.\n\n"
        "Así la marca mantiene una presencia constante sin que nadie tenga que frenar su día para "
        "subir un posteo.\n\n"
        "#Marketing #InteligenciaArtificial",
        "Presencia constante, sin frenar el día",
    ),
    (
        "El seguimiento",
        "general",
        "marca",
        "Un contacto que no compró hoy no está perdido. Muchas veces solo no era el momento.\n\n"
        "Lo que suele pasar es que nadie vuelve a escribirle. El contacto queda en una lista, la "
        "semana se llena de urgencias y cuando alguien se acuerda, pasaron meses. A veces vuelve "
        "solo, pero entonces ya no se sabe qué se habló la primera vez.\n\n"
        "Un buen seguimiento no es insistir. Es tener anotado qué necesitaba esa persona, cuándo "
        "conviene volver a hablarle y que el sistema lo recuerde por el equipo. Los contactos que "
        "vuelven después de meses son más comunes de lo que parece.\n\n"
        "#Ventas #CRM",
        "Un contacto de hoy puede ser un cliente en meses",
    ),
    (
        "Pedido sugerido",
        "caso",
        "caso",
        "En el sistema de preventa que armamos para distribuidoras, el comercio no arranca de un "
        "pedido en blanco. Recibe uno sugerido, armado con lo que suele comprar cada semana.\n\n"
        "Parece un detalle, pero cambia todo. Revisar un pedido lleva un minuto; armarlo de cero "
        "lleva mucho más, y es justo cuando se olvidan productos. Con la sugerencia, el comercio "
        "ajusta lo que cambió y confirma.\n\n"
        "Para la distribuidora, además, es una forma de notar a tiempo cuando un cliente deja de "
        "pedir algo que siempre llevaba.\n\n"
        "#Distribucion #Ventas",
        "Revisar un pedido lleva un minuto",
    ),
    (
        "Empezar chico",
        "general",
        "marca",
        "Los proyectos de software que más fallan son los que intentan resolver todo de una vez. "
        "Meses de desarrollo, una gran presentación y un sistema que llega tarde para lo que el "
        "negocio necesitaba.\n\n"
        "Lo que funciona mejor es empezar por un problema concreto y tener algo andando en pocas "
        "semanas. El equipo lo usa, aparecen cosas que nadie había previsto y se corrige con "
        "información real.\n\n"
        "Cada etapa deja algo que ya sirve. Y si en el camino el negocio cambia de prioridad, lo "
        "construido no se pierde.\n\n"
        "#SoftwareAMedida #Proyectos",
        "Algo andando en pocas semanas",
    ),
    (
        "Cómo trabajamos",
        "scalerics",
        "confianza",
        "Un proyecto con Scalerics arranca con una conversación sobre el negocio, no sobre "
        "tecnología: qué se quiere resolver, cómo se trabaja hoy y qué haría que el proyecto valga "
        "la pena.\n\n"
        "Después mostramos cómo quedaría, con pantallas que se pueden ver y tocar. Recién cuando eso "
        "está claro empezamos a construir, por etapas, con entregas que el cliente usa desde el "
        "principio.\n\n"
        "Y el trabajo no termina en la entrega. Los sistemas que hacemos se siguen ajustando a medida "
        "que el negocio cambia, porque un sistema que no acompaña al negocio deja de usarse.\n\n"
        "#SoftwareAMedida #Uruguay",
        "Primero ver cómo quedaría",
    ),
    (
        "Responder rápido sin estar pegado al celular",
        "general",
        "marca",
        "Atender rápido no significa que alguien tenga que estar todo el día mirando el celular.\n\n"
        "Una parte de las consultas se repite siempre: horarios, precios, cómo llegar, si hay "
        "disponibilidad. Esas pueden tener respuesta inmediata y automática. Las demás, las que "
        "necesitan a una persona, tienen que llegar a quien corresponde con todo el contexto, para no "
        "empezar la conversación de cero.\n\n"
        "El objetivo no es que el cliente hable con una máquina. Es que nunca se quede esperando y "
        "que cuando hable con alguien, esa persona ya sepa qué necesita.\n\n"
        "#AtencionAlCliente #Automatizacion",
        "Que nadie se quede esperando",
    ),
    (
        "El panel del dueño",
        "caso",
        "caso",
        "En los clubes de clientes que armamos para gastronomía, el dueño tiene un panel propio. "
        "Ahí ve cuántos socios tiene, cómo vienen las visitas y qué recompensas se canjean más.\n\n"
        "Lo diseñamos pensando en alguien que tiene cinco minutos entre un servicio y otro. Nada de "
        "informes largos: lo importante arriba y los detalles a un toque.\n\n"
        "Un panel que el dueño no abre no sirve de nada. Por eso la prueba de que está bien hecho es "
        "simple: que lo mire seguido sin que nadie se lo recuerde.\n\n"
        "#Fidelizacion #Datos",
        "Un panel que se abre sin recordatorio",
    ),
    (
        "La información en la cabeza de alguien",
        "general",
        "marca",
        "En casi todas las empresas hay alguien que sabe cosas que no están escritas en ningún lado. "
        "Qué cliente paga tarde, qué proveedor falla en diciembre, cómo se arma el pedido especial.\n\n"
        "Mientras esa persona está, todo funciona. El problema aparece cuando se enferma, se toma "
        "vacaciones o se va. De golpe, el negocio descubre cuánto dependía de lo que alguien "
        "recordaba.\n\n"
        "Un buen sistema de gestión, más allá de ordenar los números, sirve para eso: para que lo "
        "que el negocio aprendió quede guardado en el negocio y no en una persona.\n\n"
        "#Gestion #Procesos",
        "Lo que el negocio aprendió, guardado en el negocio",
    ),
    (
        "Scalerics Fidelidad",
        "scalerics",
        "clientes",
        "Scalerics Fidelidad es nuestro club de clientes para gastronomía y comercios, con la marca "
        "de cada negocio.\n\n"
        "El cliente suma puntos con el ticket, sin descargar nada. El negocio ve quién vuelve, cada "
        "cuánto y qué pide. Y el objetivo es que con esa información se le pueda ofrecer a cada "
        "cliente lo que lo trae de vuelta, midiendo con un grupo de control cuánto suma el club de "
        "verdad.\n\n"
        "Lo construimos porque los programas de puntos tradicionales suelen quedarse en el regalo. "
        "Lo valioso no son los puntos: es lo que el negocio aprende de sus clientes.\n\n"
        "#Fidelizacion #Gastronomia",
        "Lo valioso es lo que el negocio aprende",
    ),
    (
        "Precios que nadie actualiza",
        "general",
        "marca",
        "Cuando los precios viven en varios lugares, tarde o temprano dejan de coincidir. La lista "
        "del vendedor dice una cosa, la página web otra y el sistema de facturación una tercera.\n\n"
        "Cada diferencia termina en una conversación incómoda con un cliente, o en un margen que se "
        "pierde sin que nadie lo note. Y el problema crece con cada aumento, porque actualizar todo "
        "a mano es justo la tarea que se posterga.\n\n"
        "La solución no es más disciplina. Es que el precio esté cargado en un solo lugar y que todo "
        "lo demás lo tome de ahí.\n\n"
        "#Gestion #Ventas",
        "El precio, cargado en un solo lugar",
    ),
    (
        "Depósito y reparto",
        "caso",
        "caso",
        "En una distribuidora, cuando los pedidos llegan por distintos canales, armar el depósito es "
        "un rompecabezas diario: juntar todo, sumar cantidades, separar por cliente y ordenar el "
        "camión.\n\n"
        "En el sistema de preventa que armamos para distribuidoras, los pedidos confirmados se juntan "
        "solos. El depósito ve cuánto preparar de cada producto en total, imprime los remitos de cada "
        "cliente y el reparto sale con la ruta ordenada.\n\n"
        "El trabajo físico sigue siendo el mismo. Lo que desaparece es el tiempo de pasar en limpio, "
        "sumar y verificar, que es donde se cuelan los errores.\n\n"
        "#Distribucion #Logistica",
        "Los pedidos se juntan solos",
    ),
    (
        "Medir lo que importa",
        "general",
        "marca",
        "Es fácil llenar un informe de números: visitas a la web, seguidores, alcance de cada "
        "publicación. Se ven bien y casi nunca cambian una decisión.\n\n"
        "Los números que importan son los que están cerca de la plata. Cuántos contactos llegaron, "
        "cuántos se convirtieron en clientes, cuántos clientes volvieron. Y cuánto costó cada uno.\n\n"
        "Si un indicador sube o baja y nadie haría nada distinto, probablemente no hace falta "
        "mirarlo todas las semanas. Mejor pocos números que alguien mire de verdad que un tablero "
        "lleno que nadie abre.\n\n"
        "#Datos #Marketing",
        "Pocos números que se miren de verdad",
    ),
    (
        "Tiendas online",
        "scalerics",
        "clientes",
        "En Scalerics armamos tiendas online pensadas para que el negocio las pueda manejar solo.\n\n"
        "La parte visible es la vidriera, pero el trabajo de fondo está en otro lado: que el catálogo "
        "esté bien cargado, que el stock coincida con el depósito, que los pedidos lleguen ordenados "
        "y que los pagos y los envíos funcionen sin intervención.\n\n"
        "Una tienda que hay que atender a mano cada vez que entra un pedido termina siendo una carga. "
        "Por eso, antes de diseñar, preguntamos cómo se va a operar todos los días.\n\n"
        "#Ecommerce #SoftwareAMedida",
        "Una tienda que el negocio maneje solo",
    ),
    (
        "Digitalizar no es comprar sistemas",
        "general",
        "marca",
        "Muchas empresas tienen varios sistemas que no se hablan entre sí. Uno para facturar, otro "
        "para el stock, una planilla para los clientes y el WhatsApp para todo lo demás.\n\n"
        "Cada herramienta, por separado, cumple su función. Pero alguien tiene que pasar los datos de "
        "una a otra, y ahí se pierde tiempo y aparecen las diferencias.\n\n"
        "Antes de sumar otra herramienta, conviene mirar cómo se conectan las que ya están. Muchas "
        "veces el salto más grande no viene de un sistema nuevo, sino de que los que existen "
        "compartan la información.\n\n"
        "#TransformacionDigital #Procesos",
        "Que los sistemas se hablen entre sí",
    ),
    (
        "El CRM por dentro",
        "caso",
        "caso",
        "El CRM con el que trabaja Scalerics lo construimos nosotros, y es el mejor ejemplo de cómo "
        "pensamos un sistema.\n\n"
        "No empezó completo. Primero fueron los contactos de la publicidad entrando solos. Después el "
        "seguimiento, el calendario y las finanzas. Más tarde los cobros con tarjeta y los agentes "
        "que revisan la pauta y publican el contenido.\n\n"
        "Cada parte se sumó cuando hizo falta y se ajustó con el uso de todos los días. Es la misma "
        "forma en que armamos los sistemas de nuestros clientes: de a una etapa, sobre lo que ya "
        "funciona.\n\n"
        "#CRM #SoftwareAMedida",
        "De a una etapa, sobre lo que ya funciona",
    ),
    (
        "Promociones para todos",
        "general",
        "marca",
        "La promoción para todos los clientes es la más fácil de lanzar y la más cara de sostener. "
        "Se le regala el descuento también a quien iba a comprar igual.\n\n"
        "Cuando se conoce a los clientes, se puede hacer algo mejor. Al que viene todas las semanas "
        "quizás no hace falta ofrecerle nada. Al que hace un mes que no aparece, sí. Y al que siempre "
        "pide lo mismo, tal vez le interese probar algo nuevo.\n\n"
        "Personalizar no requiere campañas complicadas. Requiere saber quién es quién, y eso es lo "
        "que un buen club de clientes le da al negocio.\n\n"
        "#Fidelizacion #Marketing",
        "No a todos les sirve lo mismo",
    ),
    (
        "Automatizaciones a medida",
        "scalerics",
        "clientes",
        "Muchas de las cosas que armamos en Scalerics no son sistemas completos. Son automatizaciones "
        "que conectan lo que la empresa ya usa.\n\n"
        "Que un contacto de la publicidad llegue directo a quien lo tiene que atender. Que un pago "
        "recibido se anote solo en las finanzas. Que el informe de la semana llegue por mail sin que "
        "nadie lo arme. Que un cliente reciba un recordatorio antes de su cita.\n\n"
        "Son proyectos más chicos, que se ponen en marcha rápido y que liberan horas de trabajo "
        "desde el primer día.\n\n"
        "#Automatizacion #Productividad",
        "Conectar lo que la empresa ya usa",
    ),
    (
        "Lo que el cliente espera",
        "general",
        "marca",
        "Los clientes comparan la experiencia con tu negocio con la mejor experiencia que tuvieron en "
        "cualquier lado, no con la de tu competencia directa.\n\n"
        "Si en una app pidieron comida en dos toques y la siguieron en el mapa, esperan algo parecido "
        "cuando hacen un pedido, piden un presupuesto o consultan una reserva. No porque sean "
        "exigentes, sino porque ya saben que se puede.\n\n"
        "No hace falta tener los recursos de una gran empresa para estar a la altura. Hace falta "
        "resolver bien lo que el cliente hace más seguido: consultar, pedir, pagar y saber en qué "
        "está su pedido.\n\n"
        "#AtencionAlCliente #TransformacionDigital",
        "Resolver bien lo que el cliente hace seguido",
    ),
    # ── 5/11/2026: equipo, fundador, CIE, confianza y clientes ──────────────
    (
        "Quiénes hacemos Scalerics",
        "scalerics",
        "equipo",
        "Detrás de cada proyecto de Scalerics hay un equipo, no una persona trabajando sola.\n\n"
        "Somos un equipo uruguayo de desarrollo, diseño y gestión comercial. Quien arma la propuesta "
        "trabaja codo a codo con quien la programa, y el cliente habla directamente con las personas "
        "que construyen su proyecto, sin intermediarios ni pedidos que se pierden en el camino.\n\n"
        "Elegimos trabajar así porque conocimos de cerca los dos extremos. La agencia grande, con "
        "procesos lentos y poco trato directo. Y el freelance, accesible pero sin continuidad cuando "
        "algo falla después de la entrega. Scalerics está en el medio: la estructura de un equipo con "
        "el trato de persona a persona.\n\n"
        "#Equipo #Uruguay",
        "Un equipo, con trato de persona a persona",
    ),
    (
        "Por qué nació Scalerics",
        "scalerics",
        "equipo",
        "Scalerics empezó con una observación: la mayoría de las empresas sigue llevando su operación "
        "a mano. Pedidos por WhatsApp sin ningún sistema atrás, turnos anotados en un cuaderno, "
        "planillas que entiende una sola persona.\n\n"
        "No era falta de ganas de digitalizarse. Era que el camino parecía fuera de alcance: "
        "presupuestos pensados para grandes corporaciones, o soluciones sueltas que después nadie "
        "mantenía.\n\n"
        "Juan Pereyra fundó Scalerics para cambiar eso. La idea sigue siendo la misma: que cualquier "
        "empresa pueda pasar de lo manual a lo digital sin armar un equipo técnico propio, con precio "
        "fijo, plazos cortos y alguien que se haga responsable después de la entrega.\n\n"
        "#Emprendimiento #Digitalizacion",
        "De lo manual a lo digital, sin equipo técnico propio",
    ),
    (
        "Hablar con quien construye",
        "scalerics",
        "equipo",
        "En Scalerics, las personas que escuchan el problema del cliente son las mismas que después lo "
        "ven resuelto en pantalla.\n\n"
        "Parece un detalle, pero cambia el resultado. Cada vez que un pedido pasa de mano en mano se "
        "pierde algo: un matiz, una excepción, la razón por la que algo se hace de cierta forma. "
        "Cuando quien construye habla con quien va a usar el sistema, esas cosas aparecen a tiempo y "
        "no cuando el proyecto ya está terminado.\n\n"
        "Por eso nuestros clientes tienen un canal directo con el equipo, también después de la "
        "entrega. Si algo hay que ajustar, saben a quién escribirle.\n\n"
        "#SoftwareAMedida #AtencionAlCliente",
        "Hablar directo con quien construye",
    ),
    (
        "Un equipo con agentes de IA",
        "scalerics",
        "equipo",
        "En Scalerics el equipo tiene compañeros que no son personas.\n\n"
        "Trabajamos con agentes de inteligencia artificial que se ocupan de tareas concretas: revisan "
        "la publicidad todos los días, preparan el contenido, ordenan los contactos que llegan y avisan "
        "cuando algo necesita atención. Cada agente tiene una tarea acotada y alguien del equipo que lo "
        "supervisa.\n\n"
        "Eso libera a las personas para lo que ninguna herramienta hace bien: entender el negocio de "
        "cada cliente, decidir qué conviene construir y estar disponibles cuando hace falta.\n\n"
        "Lo probamos primero en casa. Recién después lo llevamos a los proyectos de nuestros clientes.\n\n"
        "#InteligenciaArtificial #Equipo",
        "Lo probamos primero en casa",
    ),
    (
        "Lo que disfrutamos de programar",
        "scalerics",
        "equipo",
        "Al equipo de Scalerics le gusta programar, y hay un momento que disfruta más que ninguno: "
        "cuando un cliente usa por primera vez algo que antes hacía a mano.\n\n"
        "Ese momento casi nunca llega con lo más complejo del proyecto. Llega con un pedido que entra "
        "solo, un recordatorio que sale sin que nadie lo mande, o un número que antes llevaba una tarde "
        "calcular y ahora está en pantalla al abrir el sistema.\n\n"
        "Buscamos ese momento en cada proyecto. Es la mejor señal de que entendimos el problema y de "
        "que lo que construimos se va a usar todos los días.\n\n"
        "#SoftwareAMedida #Equipo",
        "Lo que antes se hacía a mano, resuelto",
    ),
    (
        "Apoyados por el CIE ORT",
        "scalerics",
        "confianza",
        "Scalerics es un emprendimiento apoyado por el CIE ORT, el Centro de Innovación y "
        "Emprendimientos de la Universidad ORT Uruguay.\n\n"
        "Para un equipo joven, ese respaldo pesa. Nos da el acompañamiento de gente con experiencia en "
        "hacer crecer empresas, una red con la que contrastar ideas y la exigencia de poner por escrito, "
        "con números, hacia dónde vamos.\n\n"
        "Para nuestros clientes también importa: trabajan con un equipo que rinde cuentas, que "
        "planifica a largo plazo y que vino para quedarse.\n\n"
        "Gracias al CIE ORT por la confianza.\n\n"
        "#Emprendimiento #Uruguay",
        "Apoyados por el CIE ORT",
    ),
    (
        "Precio fijo",
        "scalerics",
        "confianza",
        "Cuando una empresa pide un presupuesto de software, la duda que más pesa casi nunca es el "
        "precio. Es no saber cuánto va a terminar costando.\n\n"
        "En Scalerics trabajamos con precio fijo. Antes de empezar se define qué se va a construir, en "
        "qué plazo y por cuánto, y eso no cambia a mitad de camino porque algo llevó más horas de las "
        "previstas. Ese riesgo lo asumimos nosotros.\n\n"
        "Si en el camino aparece algo nuevo que vale la pena sumar, se conversa y se presupuesta aparte. "
        "Así el cliente sabe en todo momento qué está pagando y por qué.\n\n"
        "#SoftwareAMedida #Presupuestos",
        "Saber desde el principio cuánto cuesta",
    ),
    (
        "Después de la entrega",
        "scalerics",
        "confianza",
        "Para elegir quién te hace un sistema o una página web, la pregunta más importante es qué pasa "
        "cuando algo falla seis meses después.\n\n"
        "Muchas empresas ya pasaron por eso: el proyecto se entregó, quien lo hizo dejó de responder y "
        "nadie más sabe cómo está armado. Al final, se termina rehaciendo desde cero.\n\n"
        "En Scalerics el soporte viene incluido y el trabajo sigue después de la entrega. Los sistemas "
        "que construimos se ajustan a medida que el negocio cambia, y siempre hay un equipo del otro "
        "lado que los conoce por dentro.\n\n"
        "#SoftwareAMedida #Soporte",
        "Qué pasa seis meses después",
    ),
    (
        "Más de 30 negocios",
        "scalerics",
        "confianza",
        "Hoy más de 30 negocios trabajan con Scalerics, de gastronomía, comercio, distribución y "
        "servicios.\n\n"
        "Cada uno llegó con algo distinto: una página web que no tenía, un club de clientes, un sistema "
        "para dejar de tomar pedidos a mano, mails con el nombre de su empresa. Lo que tienen en común "
        "es que confiaron en un equipo para resolver algo que hasta ese momento se hacía a mano, o "
        "directamente no se hacía.\n\n"
        "Gracias a cada uno. Cada proyecto nos deja algo que después aprovecha el siguiente.\n\n"
        "#Clientes #Uruguay",
        "Más de 30 negocios ya trabajan con nosotros",
    ),
    (
        "Una web en tres semanas",
        "scalerics",
        "clientes",
        "Una página web profesional, lista en tres semanas y a precio fijo.\n\n"
        "Así hacemos las webs en Scalerics. Antes de programar te mostramos cómo quedaría tu inicio en "
        "tres estilos, elegís el que sentís propio y a partir de ahí la construimos con tus servicios, "
        "tus horarios y tus canales de contacto. El soporte viene incluido: si después hay que cambiar "
        "algo, hay a quién escribirle.\n\n"
        "Si tu empresa todavía no tiene web, o tiene una que ya no la representa, escribinos desde "
        "scalerics.com y te mostramos cómo sería la tuya.\n\n"
        "#PaginasWeb #Uruguay",
        "Tu web lista en tres semanas",
    ),
    (
        "Service as a Software",
        "scalerics",
        "clientes",
        "Muchas empresas no necesitan un proyecto de software. Necesitan que alguien se ocupe de lo "
        "digital todos los meses.\n\n"
        "Para eso armamos Service as a Software: una mensualidad fija con un equipo digital que mantiene "
        "al día tu web, tus mails y tus sistemas. Cada mes se hacen los cambios que hagan falta, tenés "
        "un WhatsApp directo con el equipo y a fin de mes recibís un resumen de lo que se hizo.\n\n"
        "Es la forma de tener un área digital sin contratar un equipo propio. Si querés saber cómo "
        "funcionaría en tu empresa, escribinos desde scalerics.com.\n\n"
        "#Digitalizacion #Servicios",
        "Un área digital sin contratar un equipo",
    ),
    (
        "Mails con tu dominio",
        "scalerics",
        "clientes",
        "Un mail que termina en @gmail.com transmite algo distinto que uno con el nombre de tu "
        "empresa.\n\n"
        "En Scalerics configuramos mails corporativos: una casilla con tu dominio para cada persona del "
        "equipo, que funciona en la computadora y en el celular, con la firma de la empresa.\n\n"
        "Es de esos cambios que parecen chicos y se notan en cada intercambio con un cliente o un "
        "proveedor, sobre todo en el primero. Si querés que tu empresa tenga los suyos, escribinos desde "
        "scalerics.com y lo dejamos andando.\n\n"
        "#MailsCorporativos #Marca",
        "Tu empresa, también en cada mail",
    ),
]


def _intercalar(posts: list) -> list:
    """Uno de cada objetivo por vuelta, en el orden de OBJETIVOS.

    Dentro de cada objetivo se respeta el orden de arriba. Cuando un objetivo
    se queda sin posts, las vueltas siguen con los demás.
    """
    colas = {o: [p for p in posts if p[2] == o] for o in OBJETIVOS}
    salida = []
    while any(colas.values()):
        for o in OBJETIVOS:
            if colas[o]:
                salida.append(colas[o].pop(0))
    return salida


LINKEDIN_BANCO_SEMILLA = _intercalar(_POSTS)
