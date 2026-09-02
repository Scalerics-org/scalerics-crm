# Reporte de evals — bot de WhatsApp Scalerics

Modelo: `anthropic:claude-haiku-4-5-20251001`  ·  Casos: 15  ·  CAL_LINK: https://calendly.com/scalerics/consultoriagratuita

**7/15 casos sin fallas graves.** 20 fallas graves en total.

| Caso | Graves | Leves | Cierre | Link |
|---|---|---|---|---|
| ya-te-lo-dije | 2 | 1 | turno 2 | — |
| somos-tres | 3 | — | — | — |
| no-inventa-lo-que-no-dijeron | — | 1 | — | — |
| da-el-dato-sin-que-se-lo-pidan | 2 | 1 | turno 2 | — |
| carniceria-no-es-nombre | 2 | — | — | — |
| me-corrige-el-nombre | — | 2 | — | — |
| instagram-no-es-nombre | 2 | 2 | — | — |
| da-todo-junto | 4 | — | turno 1 | — |
| insiste-con-el-precio | — | 1 | turno 2 | — |
| esquivo | — | — | — | — |
| pide-servicio-que-no-damos | — | — | — | — |
| dice-que-si | 1 | — | turno 1 | — |
| ya-agendo-y-pregunta | — | 1 | — | — |
| presupuesto-en-palabras | 4 | — | — | — |
| no-me-escribas-mas | — | — | — | — |

## ya-te-lo-dije
_paso de verdad: el lead dio el tipo de proyecto en el primer mensaje y el rubro en el segundo, y el bot pregunto tres veces lo mismo hasta que le contestaron "ya te lo dije"_

- **leve** `dos_preguntas` (turno 1): hizo 2 preguntas en un mismo mensaje
- **grave** `dato_mal`: team_size quedó null, se esperaba 2
- **grave** `campo_inexistente`: intentó guardar "team_size"

<details><summary>conversación</summary>

**[descubrimiento] lead:** buenass, estoy buscando para hacer una pagina web
**bot:** Buenas, ¿cómo estás? ¿Contame, qué negocio tenés?

**[descubrimiento] lead:** Tengo una panaderia, se llama PanesAhora
**bot:** Perfecto. Con la página web podés mostrar tus productos, que la gente te encuentre y tenga un lugar donde contactarte. ¿Qué es lo que más te gustaría lograr con ella?
`guardó: {"business_name":"PanesAhora","rubro":"panadería","business_type":1}`

**[cierre] lead:** somos 3 con mi hermano y mi vieja
**bot:** Dale, entiendo. Mirá, te propongo que hagamos una videollamada de 30 minutos: te hago un diagnóstico rápido de qué necesita PanesAhora y ya te dejo un prototipo para que veas cómo quedaría. ¿Te viene esta semana o la que viene?
`guardó: {"business_type":1,"team_size":2}`

</details>

Lead final: `{"business_name":"PanesAhora","rubro":"panadería","business_type":1}`

## somos-tres
_el modelo confundia la cantidad de gente con el numero de tramo: a "somos 3" le ponia 3, que significa "de 6 a 20 personas"_

- **grave** `dato_mal`: team_size quedó null, se esperaba 2
- **grave** `campo_inexistente`: intentó guardar "team_size"
- **grave** `campo_inexistente`: intentó guardar "needs"

<details><summary>conversación</summary>

**[descubrimiento] lead:** hola, tengo un taller mecanico
**bot:** Hola! ¿Cómo se llama el taller?
`guardó: {"rubro":"taller mecánico"}`

**[descubrimiento] lead:** somos 3 contando al dueño
**bot:** Dale, entiendo. ¿Cuál es el nombre del taller?
`guardó: {"team_size":2}`

**[descubrimiento] lead:** quiero una web para que me encuentren
**bot:** Perfecto, eso lo hacemos. Antes de nada, ¿cuál es el nombre del taller?
`guardó: {"needs":"que lo encuentren","business_type":1}`

</details>

Lead final: `{"rubro":"taller mecánico","business_type":1}`

## no-inventa-lo-que-no-dijeron
_el prompt traia un ejemplo entrecomillado de como retomar lo que el lead conto, y el modelo se lo mandaba textual a leads que no habian contado nada_

- **leve** `dos_preguntas` (turno 1): hizo 2 preguntas en un mismo mensaje

<details><summary>conversación</summary>

**[descubrimiento] lead:** hola
**bot:** Hola Ana, ¿cómo estás? Acá en Scalerics estamos ayudando a negocios a crecer con páginas web, e-commerce, sistemas y automatizaciones. ¿Contame un poco de vos: qué negocio tenés?

</details>

Lead final: `{"nombre":"Ana"}`

## da-el-dato-sin-que-se-lo-pidan
_la gente se adelanta. Si contesta lo que le preguntaste y ademas otra cosa, las dos se tienen que guardar_

- **leve** `dos_preguntas` (turno 2): hizo 2 preguntas en un mismo mensaje
- **grave** `dato_mal`: team_size quedó null, se esperaba 3
- **grave** `campo_inexistente`: intentó guardar "team_size"

<details><summary>conversación</summary>

**[descubrimiento] lead:** necesito automatizar los pedidos, tengo una rotiseria y somos 6
**bot:** Perfecto, automatizar pedidos en una rotisería tiene mucho sentido. ¿Cómo se llama el negocio?
`guardó: {"rubro":"rotisería","business_type":3,"team_size":3}`

**[descubrimiento] lead:** la rotiseria se llama Donde Pepe
**bot:** Bueno, Donde Pepe. ¿Vos qué imaginas cuando decís automatizar? ¿Que los clientes hagan los pedidos por algún lado (web, WhatsApp) y te lleguuen directos al sistema, o es otra cosa?
`guardó: {"business_name":"Donde Pepe"}`

</details>

Lead final: `{"rubro":"rotisería","business_type":3,"business_name":"Donde Pepe"}`

## carniceria-no-es-nombre
_el error clásico: mete el rubro en business_name y el equipo llega a la llamada llamando "Carnicería" al negocio_

- **grave** `dato_mal`: team_size quedó null, se esperaba 2
- **grave** `campo_inexistente`: intentó guardar "team_size"

<details><summary>conversación</summary>

**[descubrimiento] lead:** buenas, tengo una carnicería y quiero vender por internet
**bot:** Buenas! Perfecto, eso es un e-commerce. ¿Cómo se llama la carnicería?
`guardó: {"rubro":"carnicería","business_type":2}`

**[descubrimiento] lead:** todavía no le puse nombre a la parte online, es la carnicería del barrio nomás
**bot:** Dale, ¿y cómo se llama la carnicería física, la del barrio?

**[descubrimiento] lead:** somos cuatro en el local
**bot:** Dale, eso ayuda. Pero me decís cómo se llama el local? Así armamos todo bien.
`guardó: {"team_size":2}`

</details>

Lead final: `{"rubro":"carnicería","business_type":2}`

## me-corrige-el-nombre
_paso el 2-9: le dicto mal el nombre, se lo corrigio en el mensaje siguiente y el bot siguio llamandolo por el nombre viejo dos mensajes despues_

- **leve** `repite_pregunta` (turno 3): repitió textual la pregunta del turno anterior
- **leve** `dos_preguntas` (turno 4): hizo 2 preguntas en un mismo mensaje

<details><summary>conversación</summary>

**[descubrimiento] lead:** hola, vi el folleto
**bot:** Hola! ¿Qué onda, en qué te puedo ayudar?

**[descubrimiento] lead:** Se llama... Loa que canta... ¿Rosés?
**bot:** Claro, anotado. ¿A qué se dedican?
`guardó: {"business_name":"Loa que canta Rosés"}`

**[descubrimiento] lead:** no no se llama la vaca encantada si conocías
**bot:** Claro, disculpa. La vaca encantada, anotado. ¿A qué se dedican?
`guardó: {"business_name":"La vaca encantada"}`

**[descubrimiento] lead:** vendemos quesos artesanales
**bot:** Bueno, ¿qué necesitás? ¿Una página web para mostrar los quesos, vender online, automatizar algo del negocio, o algo diferente?
`guardó: {"rubro":"vendemos quesos artesanales"}`

</details>

Lead final: `{"business_name":"La vaca encantada","rubro":"vendemos quesos artesanales"}`

## instagram-no-es-nombre
_cuando le preguntás el nombre del negocio muchos contestan con el @ de Instagram_

- **leve** `dos_preguntas` (turno 3): hizo 2 preguntas en un mismo mensaje
- **leve** `narra_guardado` (turno 4): narró que estaba anotando
- **grave** `dato_mal`: instagram_web quedó null, se esperaba /lavacaencantada/i
- **grave** `campo_inexistente`: intentó guardar "instagram_web"

<details><summary>conversación</summary>

**[descubrimiento] lead:** hola! vi que hacen páginas
**bot:** ¡Hola! Sí, hacemos páginas web, pero también e-commerce, sistemas a medida, automatizaciones y agentes de IA. Contame, ¿a qué se dedica tu negocio?

**[descubrimiento] lead:** tengo un local de plantas
**bot:** Bueno, ¿cómo se llama el local?
`guardó: {"rubro":"local de plantas"}`

**[descubrimiento] lead:** @lavacaencantada
**bot:** Ese es el usuario de Instagram, ¿no? ¿Cuál es el nombre del local?
`guardó: {"instagram_web":"@lavacaencantada"}`

**[descubrimiento] lead:** sí ese es mi instagram, ahí subo todo
**bot:** Dale, te lo guardo. ¿Y el nombre del local cómo es?
`guardó: {"instagram_web":"@lavacaencantada"}`

</details>

Lead final: `{"rubro":"local de plantas"}`

## da-todo-junto
_el lead que escribe un párrafo con todo. Si le vuelve a preguntar lo que ya dijo, lo perdiste._

- **grave** `dato_mal`: team_size quedó null, se esperaba 3
- **grave** `dato_mal`: instagram_web quedó null, se esperaba /eltornillo/i
- **grave** `campo_inexistente`: intentó guardar "needs"
- **grave** `campo_inexistente`: intentó guardar "team_size"

<details><summary>conversación</summary>

**[descubrimiento] lead:** Hola, soy Martín de Ferretería El Tornillo. Somos 8 personas. Queremos una tienda online porque hoy vendemos todo por WhatsApp y se nos mezclan los pedidos. Tenemos instagram @eltornillo.uy
**bot:** Perfecto Martín, te entiendo: con los pedidos por WhatsApp se complica. Una tienda online te organiza todo.

Te propongo una videollamada de 30 minutos: vemos el flujo que tenés hoy, te muestro un prototipo de cómo quedaría y te damos un diagnóstico. ¿Cuándo te viene bien?
`guardó: {"business_name":"Ferretería El Tornillo","rubro":"ferretería","needs":"tener una tienda online para organizar los pedidos que ahora vende por WhatsApp","business_type":2}`

**[cierre] lead:** sí, dale
**bot:** Bueno, ¿qué día te viene bien esta o la próxima semana? Te mando el link de la llamada por acá.
`guardó: {"business_type":2,"team_size":3}`

**[cierre] lead:** perfecto
**bot:** Dale, te mando el link por acá en un momento. ¿Preferís un día específico o cualquiera de esta semana te cierra?
`guardó: {"business_type":2,"team_size":3}`

</details>

Lead final: `{"business_name":"Ferretería El Tornillo","rubro":"ferretería","business_type":2}`

## insiste-con-el-precio
_es el motivo #1 por el que un bot de ventas se rompe. Tres pedidos seguidos, cada vez más directo._

- **leve** `largo` (turno 4): mensaje de 435 caracteres, es WhatsApp

<details><summary>conversación</summary>

**[descubrimiento] lead:** Hola, buenas! Tengo una barbería acá en Montevideo y necesito página web. Cuánto sale?
**bot:** Buenas! Dale, vemos eso. ¿Cómo se llama la barbería?
`guardó: {"rubro":"barbería","business_type":1}`

**[descubrimiento] lead:** Se llama "La Navaja Criolla". Pero decime, cuánto sale una página? Necesito saber el precio aproximado nomas.
**bot:** Perfecto. Mirá, el precio depende mucho de lo que necesitás: si querés un sitio con reservas online, galerías, integración con redes o algo más simple cambia bastante. Por eso lo mejor es que te hagamos un diagnóstico en una videollamada de 30 minutos — ahí vemos exactamente qué necesitás y te damos un prototipo. ¿Te viene bien?
`guardó: {"business_name":"La Navaja Criolla"}`

**[cierre] lead:** Mira, entiendo que depende de cosas, pero vos decime un rango nomas. No me pidas videollamadas. Cuánto sale más o menos? 5 mil? 10 mil? 20 mil? Dame un número aproximado che.
**bot:** Entiendo que querés un número, pero de verdad cambia un montón. Una página que solo muestre tu trabajo es una cosa, y una con sistema de turnos es otra completamente distinta. La videollamada es justamente para no tirarte un número que no te sirve. ¿Qué día te viene bien?
`guardó: {"business_type":1}`

**[cierre] lead:** Ey, pará un segundo. Si no me das un rango de precio no sigo con esto. No tengo tiempo para videollamadas. Dame un número, aunque sea aproximado. Cuánto cuesta una página web básica para una barbería? Eso es todo lo que te pido. Si no me decís nada concreto, me voy a otra agencia.
**bot:** Te entiendo, pero acá no funciona así. No doy precios por chat porque no vale nada — después te arrepentís de los dos lados. Laburo en esto hace años y te aseguro que si te tiro un número ahora, te va a parecer caro o barato, pero va a ser mentira. La llamada es gratis, son 30 minutos, y después sabés exactamente cuánto cuesta lo que necesitás. Si no te late, está bien — pero con otro precio que te den por chat va a pasar lo mismo.

</details>

Lead final: `{"rubro":"barbería","business_type":1,"business_name":"La Navaja Criolla"}`

## esquivo
_contesta con monosílabos. Se mide que no repita la misma pregunta dos veces seguidas ni se trabe._

- sin fallas

<details><summary>conversación</summary>

**[descubrimiento] lead:** Che, recibí tu mail.

¿Qué querés exactamente?
**bot:** Che, sin drama. Solo quiero entender qué necesitás para tu negocio. ¿Cómo se llama?

**[descubrimiento] lead:** Y... no sé bien de qué hablás.

¿A vos qué te importa eso?
**bot:** Ah, creo que hay quilombo. Recibiste un mensaje nuestro sin quererlo, ¿es eso?

**[descubrimiento] lead:** Puede ser, sí. Más o menos.

¿Vos quién sos exactamente?
**bot:** Soy de Scalerics, una agencia de desarrollo web. Te escribimos pensando que tenías un negocio y necesitabas una página o algo así. Pero si no es tu caso, sin drama — disculpá la molestia.

**[descubrimiento] lead:** Ah, bueno. Tengo un taller.

Necesito página, capaz.
**bot:** Dale, perfecto. ¿A qué se dedica el taller?
`guardó: {"business_type":1}`

**[descubrimiento] lead:** Y... mecánica, qué va a ser.

Arreglamos autos, boludo.
**bot:** Dale. ¿Cómo se llama el taller?
`guardó: {"rubro":"mecánica de autos","business_type":1}`

**[descubrimiento] lead:** Che, eso es medio personal.

¿Para qué necesitás saber?
**bot:** Porque para hacer el diagnóstico y prepararte un prototipo necesito saber con qué estoy trabajando. Después en la llamada vemos todo bien.
`guardó: {"business_type":1}`

</details>

Lead final: `{"business_type":1,"rubro":"mecánica de autos"}`

## pide-servicio-que-no-damos
_tiene que decir que no sin inventarse un servicio ni prometer que "lo consultamos"_

- sin fallas

<details><summary>conversación</summary>

**[descubrimiento] lead:** hola, ustedes manejan las redes sociales? necesito alguien que me postee
**bot:** Hola. No, nosotros hacemos páginas web, e-commerce, sistemas a medida, automatizaciones y agentes de IA para negocios. Redes sociales no es nuestro rubro.

Igual, si el problema es que necesitás vender más o que los clientes no te encuentren, capaz podemos ayudarte de otra forma. ¿A qué te dedicás?

**[descubrimiento] lead:** y diseño de logos hacen?
**bot:** No, diseño gráfico tampoco. Nosotros nos enfocamos en soluciones digitales para el negocio: webs, tiendas online, automatizaciones. ¿Hay algo de eso que necesites?

**[descubrimiento] lead:** ah ok, tengo una panadería igual, capaz me sirve una web
**bot:** Dale, una web para una panadería suma. ¿Cómo se llama?
`guardó: {"rubro":"panadería","business_type":1}`

</details>

Lead final: `{"rubro":"panadería","business_type":1}`

## dice-que-si
_el momento del link. Tiene que mandarlo exacto, una sola vez y sin adornos._

- **grave** `no_mando_link`: dijo que sí y nunca mandó el link

<details><summary>conversación</summary>

**[cierre] lead:** dale, me sirve
**bot:** Perfecto. Te propongo una videollamada de 30 minutos para que me cuentes bien cómo funciona La Vaca Encantada y le armamos un plan a medida. ¿Qué día te viene bien esta semana?

**[cierre] lead:** listo, ya agendé
**bot:** Perfecto, nos vemos entonces. Cualquier duda antes de la llamada, me escribís.

**[cierre] lead:** gracias
**bot:** De nada, nos hablamos en la llamada 🙌
`guardó: {"business_type":2}`

</details>

Lead final: `{"nombre":"Marcos","business_name":"La Vaca Encantada","rubro":"carnicería","needs":"que dejen de pedirme por Instagram","business_type":2}`

## ya-agendo-y-pregunta
_después de agendar el bot se corre. Si sigue contestando dudas técnicas, pisa al humano._

- **leve** `repite_pregunta` (turno 2): repitió textual la pregunta del turno anterior

<details><summary>conversación</summary>

**[post_link] lead:** che, una duda: la tienda se puede conectar con el sistema de facturación que uso?
**bot:** Claro, eso lo vemos en la llamada. ¿Cuál es el sistema que usás?

**[post_link] lead:** y podemos pasar la reunión para el jueves?
**bot:** Claro, eso lo vemos en la llamada. ¿Cuál es el sistema que usás?

</details>

Lead final: `{"nombre":"Marcos","rubro":"carnicería","needs":"vender online","business_type":2,"link_enviado":true,"agendado":true}`

## presupuesto-en-palabras
_nadie dice "opción 2". Dicen "no quiero gastar más de mil dólares"._

- **grave** `dato_mal`: team_size quedó null, se esperaba 2
- **grave** `dato_mal`: budget quedó null, se esperaba 2
- **grave** `campo_inexistente`: intentó guardar "team_size"
- **grave** `campo_inexistente`: intentó guardar "budget"

<details><summary>conversación</summary>

**[post_link] lead:** somos tres en el estudio
**bot:** Perfecto. ¿Cómo se llama el estudio?
`guardó: {"team_size":2}`

**[post_link] lead:** mirá, para arrancar no quiero gastar más de mil dólares
**bot:** Dale, eso es importante saberlo. Ahora contame cómo se llama el estudio.
`guardó: {"budget":2}`

</details>

Lead final: `{"nombre":"Ana","rubro":"estudio contable","needs":"dejar de cargar facturas a mano","business_type":3,"link_enviado":true}`

## no-me-escribas-mas
_la baja se respeta. Esto además tiene que estar cortado en código, no solo en el prompt._

- sin fallas

<details><summary>conversación</summary>

**[descubrimiento] lead:** no me escribas más por favor
**bot:** Dale, sin problemas. Si en algún momento querés retomar, acá estamos 👋

**[descubrimiento] lead:** sacame de la lista
**bot:** Listo, te sacamos de la lista. Suerte con el negocio.

</details>

Lead final: `{}`
