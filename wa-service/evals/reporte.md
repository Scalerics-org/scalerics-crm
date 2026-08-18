# Reporte de evals — bot de WhatsApp Scalerics

Modelo: `openai:gpt-4o-mini`  ·  Casos: 14  ·  CAL_LINK: https://calendly.com/scalerics/diagnostico

**10/14 casos sin fallas graves.** 4 fallas graves en total.

| Caso | Graves | Leves | Cierre | Link |
|---|---|---|---|---|
| ya-te-lo-dije | — | 2 | — | — |
| somos-tres | — | 2 | — | — |
| no-inventa-lo-que-no-dijeron | — | 1 | — | — |
| da-el-dato-sin-que-se-lo-pidan | — | — | — | — |
| carniceria-no-es-nombre | — | — | — | — |
| instagram-no-es-nombre | 1 | 4 | — | — |
| da-todo-junto | 1 | 1 | — | — |
| insiste-con-el-precio | — | 5 | — | — |
| esquivo | — | 3 | — | — |
| pide-servicio-que-no-damos | — | 3 | — | — |
| dice-que-si | 1 | — | — | — |
| ya-agendo-y-pregunta | 1 | 2 | — | — |
| presupuesto-en-palabras | — | 1 | — | — |
| no-me-escribas-mas | — | — | — | — |

## ya-te-lo-dije
_paso de verdad: el lead dio el tipo de proyecto en el primer mensaje y el rubro en el segundo, y el bot pregunto tres veces lo mismo hasta que le contestaron "ya te lo dije"_

- **leve** `voseo` (turno 1): se le escapó el tuteo: "tu negocio"
- **leve** `voseo` (turno 3): se le escapó el tuteo: "manejas"

<details><summary>conversación</summary>

**[descubrimiento] lead:** buenass, estoy buscando para hacer una pagina web
**bot:** Buenass! ¿Cómo se llama tu negocio y a qué se dedica?
`guardó: {"business_type":1}`

**[descubrimiento] lead:** Tengo una panaderia, se llama PanesAhora
**bot:** Genial, PanesAhora se escucha delicioso 😋. ¿Cuántas personas trabajan en tu panadería?
`guardó: {"business_name":"PanesAhora","rubro":"panadería","business_type":1}`

**[descubrimiento] lead:** somos 3 con mi hermano y mi vieja
**bot:** Perfecto! ¿Qué presupuesto manejas para la página web?
`guardó: {"business_name":"PanesAhora","rubro":"panadería","team_size":2}`

</details>

Lead final: `{"business_type":1,"business_name":"PanesAhora","rubro":"panadería","team_size":2}`

## somos-tres
_el modelo confundia la cantidad de gente con el numero de tramo: a "somos 3" le ponia 3, que significa "de 6 a 20 personas"_

- **leve** `dos_preguntas` (turno 2): hizo 2 preguntas en un mismo mensaje
- **leve** `voseo` (turno 3): se le escapó el tuteo: "Tienes"

<details><summary>conversación</summary>

**[descubrimiento] lead:** hola, tengo un taller mecanico
**bot:** ¡Genial! ¿Cómo se llama tu taller?

**[descubrimiento] lead:** somos 3 contando al dueño
**bot:** Buenísimo, gracias por compartirlo. ¿Qué tipo de proyecto estás pensando hacer? ¿Una página web, un e-commerce, alguna app o algo diferente?
`guardó: {"rubro":"taller mecánico","team_size":2}`

**[descubrimiento] lead:** quiero una web para que me encuentren
**bot:** Perfecto, una web es una buena opción. ¿Tienes un presupuesto en mente para esto?
`guardó: {"business_type":1,"team_size":2}`

</details>

Lead final: `{"rubro":"taller mecánico","team_size":2,"business_type":1}`

## no-inventa-lo-que-no-dijeron
_el prompt traia un ejemplo entrecomillado de como retomar lo que el lead conto, y el modelo se lo mandaba textual a leads que no habian contado nada_

- **leve** `dos_preguntas` (turno 1): hizo 2 preguntas en un mismo mensaje

<details><summary>conversación</summary>

**[descubrimiento] lead:** hola
**bot:** ¡Hola! ¿Cómo estás? ¿En qué puedo ayudarte hoy?

</details>

Lead final: `{"nombre":"Ana"}`

## da-el-dato-sin-que-se-lo-pidan
_la gente se adelanta. Si contesta lo que le preguntaste y ademas otra cosa, las dos se tienen que guardar_

- sin fallas

<details><summary>conversación</summary>

**[descubrimiento] lead:** necesito automatizar los pedidos, tengo una rotiseria y somos 6
**bot:** ¡Genial! ¿Cómo se llama tu rotisería?
`guardó: {"rubro":"rotisería","business_type":3,"team_size":3}`

**[descubrimiento] lead:** la rotiseria se llama Donde Pepe
**bot:** ¡Perfecto! Entonces tenés un equipo de 6. ¿Qué presupuesto manejás para la automatización?
`guardó: {"business_name":"Donde Pepe","rubro":"rotisería","team_size":3}`

</details>

Lead final: `{"rubro":"rotisería","business_type":3,"team_size":3,"business_name":"Donde Pepe"}`

## carniceria-no-es-nombre
_el error clásico: mete el rubro en business_name y el equipo llega a la llamada llamando "Carnicería" al negocio_

- sin fallas

<details><summary>conversación</summary>

**[descubrimiento] lead:** buenas, tengo una carnicería y quiero vender por internet
**bot:** Genial, contame un poco más sobre tu carnicería. ¿Cómo se llama?
`guardó: {"rubro":"carnicería","business_type":2}`

**[descubrimiento] lead:** todavía no le puse nombre a la parte online, es la carnicería del barrio nomás
**bot:** Perfecto, la carnicería del barrio suena muy bien. ¿Qué presupuesto tenés en mente para el proyecto?
`guardó: {"rubro":"carnicería","business_type":2}`

**[descubrimiento] lead:** somos cuatro en el local
**bot:** Buen dato, ¡gracias! ¿Y qué presupuesto tenés en mente para tu proyecto?
`guardó: {"team_size":2}`

</details>

Lead final: `{"rubro":"carnicería","business_type":2,"team_size":2}`

## instagram-no-es-nombre
_cuando le preguntás el nombre del negocio muchos contestan con el @ de Instagram_

- **leve** `voseo` (turno 2): se le escapó el tuteo: "tu local"
- **leve** `dos_preguntas` (turno 3): hizo 2 preguntas en un mismo mensaje
- **leve** `dos_preguntas` (turno 4): hizo 2 preguntas en un mismo mensaje
- **leve** `repite_pregunta` (turno 4): repitió textual la pregunta del turno anterior
- **grave** `dato_inventado`: business_name quedó "@lavacaencantada" — un @ de Instagram no es el nombre del negocio

<details><summary>conversación</summary>

**[descubrimiento] lead:** hola! vi que hacen páginas
**bot:** ¡Hola! Sí, hacemos páginas web y también e-commerce, apps y automatizaciones. ¿De qué tipo de proyecto estás pensando?

**[descubrimiento] lead:** tengo un local de plantas
**bot:** ¡Qué bueno! ¿Cómo se llama tu local?
`guardó: {"rubro":"local de plantas"}`

**[descubrimiento] lead:** @lavacaencantada
**bot:** Buen nombre para el local. ¿Qué tipo de proyecto necesitás? ¿Una página web, un e-commerce o algo más?
`guardó: {"business_name":"@lavacaencantada"}`

**[descubrimiento] lead:** sí ese es mi instagram, ahí subo todo
**bot:** Genial, gracias por compartir tu Instagram. ¿Qué tipo de proyecto necesitás? ¿Una página web, un e-commerce o algo más?
`guardó: {"instagram_web":"@lavacaencantada"}`

</details>

Lead final: `{"rubro":"local de plantas","business_name":"@lavacaencantada","instagram_web":"@lavacaencantada"}`

## da-todo-junto
_el lead que escribe un párrafo con todo. Si le vuelve a preguntar lo que ya dijo, lo perdiste._

- **leve** `repite_pregunta` (turno 3): repitió textual la pregunta del turno anterior
- **grave** `cierre_tardio`: llegó a cierre en el turno —, se esperaba en 2 o antes

<details><summary>conversación</summary>

**[descubrimiento] lead:** Hola, soy Martín de Ferretería El Tornillo. Somos 8 personas. Queremos una tienda online porque hoy vendemos todo por WhatsApp y se nos mezclan los pedidos. Tenemos instagram @eltornillo.uy
**bot:** Genial Martín, parece que tienen un buen proyecto en mente. ¿Te gustaría agendar una videollamada de 30 minutos para charlar sobre las ideas que tienen y cómo podemos ayudar?
`guardó: {"business_name":"Ferretería El Tornillo","rubro":"ferretería","instagram_web":"@eltornillo.uy","business_type":2,"team_size":3}`

**[descubrimiento] lead:** sí, dale
**bot:** ¿Qué presupuesto manejan para la tienda online?
`guardó: {"business_name":"Ferretería El Tornillo","instagram_web":"@eltornillo.uy","business_type":2,"team_size":3}`

**[descubrimiento] lead:** perfecto
**bot:** ¿Qué presupuesto manejan para la tienda online?
`guardó: {"business_name":"Ferretería El Tornillo","rubro":"ferretería","instagram_web":"@eltornillo.uy","business_type":2,"team_size":3}`

</details>

Lead final: `{"business_name":"Ferretería El Tornillo","rubro":"ferretería","instagram_web":"@eltornillo.uy","business_type":2,"team_size":3}`

## insiste-con-el-precio
_es el motivo #1 por el que un bot de ventas se rompe. Tres pedidos seguidos, cada vez más directo._

- **leve** `voseo` (turno 1): se le escapó el tuteo: "tu negocio"
- **leve** `voseo` (turno 2): se le escapó el tuteo: "tu negocio"
- **leve** `repite_pregunta` (turno 2): repitió textual la pregunta del turno anterior
- **leve** `voseo` (turno 3): se le escapó el tuteo: "tu negocio"
- **leve** `repite_pregunta` (turno 3): repitió textual la pregunta del turno anterior

<details><summary>conversación</summary>

**[descubrimiento] lead:** Hola, ¿cómo están? Quería saber cuánto sale una página web. Gracias.
**bot:** ¡Hola! Estamos bien, gracias. Antes de hablar de precios, contame un poco más sobre tu negocio. ¿A qué te dedicás?
`guardó: {"business_type":1,"budget":4}`

**[descubrimiento] lead:** Entiendo, pero solo quiero saber un número aproximado de cuánto sale una página web.
**bot:** Entiendo que busques un número, pero primero necesito saber más sobre tu negocio. ¿A qué te dedicás?
`guardó: {"business_type":1,"budget":4}`

**[descubrimiento] lead:** Sí, pero me gustaría que me des un rango de precios. Solo quiero saber cuánto podría salir.
**bot:** Entiendo, pero lo más importante es saber sobre tu negocio para brindarte un servicio a medida. ¿A qué te dedicás?
`guardó: {"business_type":1,"budget":4}`

**[descubrimiento] lead:** Mirá, ya te dije que tengo una barbería. No me estás dando un rango de precios, y si no me decís algo, no sigo.
**bot:** Disculpá, entiendo tu frustración. Ahora que sé que tenés una barbería, ¿podés contarme cuántas personas trabajan en el negocio?
`guardó: {"rubro":"barbería","business_type":1,"budget":4}`

</details>

Lead final: `{"business_type":1,"budget":4,"rubro":"barbería"}`

## esquivo
_contesta con monosílabos. Se mide que no repita la misma pregunta dos veces seguidas ni se trabe._

- **leve** `voseo` (turno 1): se le escapó el tuteo: "tu negocio"
- **leve** `voseo` (turno 3): se le escapó el tuteo: "tu negocio"
- **leve** `voseo` (turno 6): se le escapó el tuteo: "necesitas"

<details><summary>conversación</summary>

**[descubrimiento] lead:** Hola, ¿qué tal? Quisiera información.
**bot:** Hola, todo bien, ¿y vos? Contame un poco de tu negocio, así puedo ayudarte mejor.

**[descubrimiento] lead:** Más o menos, ¿y...? ¿Qué negocio?
**bot:** Entiendo, no hay problema. ¿Tenés un negocio que te gustaría mejorar con una página web, e-commerce o algo así?

**[descubrimiento] lead:** No sé, ¿puede ser? Ta...
**bot:** No hay drama, cuando quieras contarme más, estoy acá. ¿Cómo se llama tu negocio?

**[descubrimiento] lead:** No sé, ¿y...? No es relevante.
**bot:** Está bien, no hay apuro. Si en algún momento te surgen dudas o precisás asesoramiento, contame. ¿Te gustaría que agendemos una llamada para charlar más a fondo?

**[descubrimiento] lead:** Puede ser, pero... no estoy seguro.
**bot:** Sin problema, estamos para ayudarte cuando lo necesites. Si en algún momento decidís que querés charlar, solo avisame.

**[descubrimiento] lead:** Ta, lo pensaré... ¿Y?
**bot:** Claro, tomate tu tiempo. Si necesitas cualquier cosa, aquí estoy. ¿Querés contarme qué tipo de proyecto te interesa, si es que te decidís?

</details>

Lead final: `{}`

## pide-servicio-que-no-damos
_tiene que decir que no sin inventarse un servicio ni prometer que "lo consultamos"_

- **leve** `voseo` (turno 1): se le escapó el tuteo: "tu negocio"
- **leve** `voseo` (turno 2): se le escapó el tuteo: "tu negocio"
- **leve** `repite_pregunta` (turno 2): repitió textual la pregunta del turno anterior

<details><summary>conversación</summary>

**[descubrimiento] lead:** hola, ustedes manejan las redes sociales? necesito alguien que me postee
**bot:** Hola! Actualmente no manejamos redes sociales, pero podemos ayudarte a crear una página web o algún otro proyecto. ¿A qué se dedica tu negocio?

**[descubrimiento] lead:** y diseño de logos hacen?
**bot:** No hacemos diseño de logos, pero podemos ayudarte con una página web o un e-commerce. ¿A qué se dedica tu negocio?

**[descubrimiento] lead:** ah ok, tengo una panadería igual, capaz me sirve una web
**bot:** ¡Genial! Una web podría ser muy útil para tu panadería. ¿Qué te gustaría lograr con ella?
`guardó: {"rubro":"panadería"}`

</details>

Lead final: `{"rubro":"panadería"}`

## dice-que-si
_el momento del link. Tiene que mandarlo exacto, una sola vez y sin adornos._

- **grave** `no_mando_link`: dijo que sí y nunca mandó el link

<details><summary>conversación</summary>

**[descubrimiento] lead:** dale, me sirve
**bot:** ¿Cuánta gente trabaja en La Vaca Encantada?
`guardó: {"business_name":"La Vaca Encantada","rubro":"carnicería"}`

**[descubrimiento] lead:** listo, ya agendé
**bot:** ¡Genial! Entonces espero que hablemos pronto. Hablame un poco más: ¿qué presupuesto manejás para el proyecto?
`guardó: {"business_name":"La Vaca Encantada","rubro":"carnicería","needs":"que dejen de pedirme por Instagram"}`

**[descubrimiento] lead:** gracias
**bot:** ¡Gracias a vos! Espero que tengamos una buena charla. ¿Qué presupuesto manejás para el proyecto?
`guardó: {"business_name":"La Vaca Encantada","rubro":"carnicería","needs":"que dejen de pedirme por Instagram"}`

</details>

Lead final: `{"nombre":"Marcos","business_name":"La Vaca Encantada","rubro":"carnicería","needs":"que dejen de pedirme por Instagram","business_type":2}`

## ya-agendo-y-pregunta
_después de agendar el bot se corre. Si sigue contestando dudas técnicas, pisa al humano._

- **leve** `voseo` (turno 1): se le escapó el tuteo: "tu negocio"
- **leve** `voseo` (turno 2): se le escapó el tuteo: "tu negocio"
- **grave** `no_derivo`: no derivó al equipo

<details><summary>conversación</summary>

**[post_link] lead:** che, una duda: la tienda se puede conectar con el sistema de facturación que uso?
**bot:** Claro, podemos ayudar a integrar la tienda con tu sistema de facturación. ¿Cómo se llama tu negocio?
`guardó: {"rubro":"carnicería","needs":"vender online","business_type":2}`

**[post_link] lead:** y podemos pasar la reunión para el jueves?
**bot:** Perfecto, el jueves está bien. Por cierto, ¿cómo se llama tu negocio?
`guardó: {"rubro":"carnicería","needs":"vender online","business_type":2}`

</details>

Lead final: `{"nombre":"Marcos","rubro":"carnicería","needs":"vender online","business_type":2,"link_enviado":true,"agendado":true}`

## presupuesto-en-palabras
_nadie dice "opción 2". Dicen "no quiero gastar más de mil dólares"._

- **leve** `repite_pregunta` (turno 2): repitió textual la pregunta del turno anterior

<details><summary>conversación</summary>

**[post_link] lead:** somos tres en el estudio
**bot:** Buenísimo, ya tengo eso. ¿Cómo se llama el estudio contable?
`guardó: {"team_size":2}`

**[post_link] lead:** mirá, para arrancar no quiero gastar más de mil dólares
**bot:** Perfecto, ya tengo eso. ¿Cómo se llama el estudio contable?
`guardó: {"budget":2,"team_size":2}`

</details>

Lead final: `{"nombre":"Ana","rubro":"estudio contable","needs":"dejar de cargar facturas a mano","business_type":3,"link_enviado":true,"team_size":2,"budget":2}`

## no-me-escribas-mas
_la baja se respeta. Esto además tiene que estar cortado en código, no solo en el prompt._

- sin fallas

<details><summary>conversación</summary>

**[descubrimiento] lead:** no me escribas más por favor
**bot:** Entiendo, no hay problema. Si en algún momento necesitás algo, aquí estoy para ayudarte. ¡Que tengas un buen día!

**[descubrimiento] lead:** sacame de la lista
**bot:** Te pido disculpas por la molestia. Si alguna vez necesitás ayuda, aquí estaré.

</details>

Lead final: `{}`
