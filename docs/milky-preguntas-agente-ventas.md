# Milky (Lácteos) — preguntas para la reunión

**Qué quieren:** un agente de IA que funcione como auxiliar del equipo de ventas.
**Qué venden:** lácteos. **A quién:** comercios (B2B).

---

## Lo que ya sabemos de ellos (investigado antes de la reunión)

Datos públicos verificados. Sirven para llegar sabiendo, no para recitarlos.

- **Lácteos Milky**, Montevideo. Se presentan como 100% uruguaya, **desde 1959**
  ("+60 años en el mercado").
- **Teléfono 2209 6852** · **info@milky.com.uy**
- **No tienen web.** `milky.com.uy` muestra *"Estamos trabajando en
  www.milky.com.uy"*, hosteado por Intersys. El dominio y el mail corporativo
  existen; el sitio no.
- **Instagram [@lacteos.milky](https://www.instagram.com/lacteos.milky/):**
  ~278 seguidores, sin link en bio, sin botón de contacto. Contenido dirigido
  al consumidor final (yogur bebible, efemérides) **aunque su cliente es el
  comercio**. También tienen Facebook.
- **Catálogo amplio (~25 SKU visibles en un solo revendedor):** muzzarella,
  danbo, cuartirolo, provolone, cheddar en barra, queso sandwich, magro,
  semiduro trébol, rallado en hebras, ricotta, manteca, salsa cheddar, dulce de
  leche. **También distribuyen marcas de terceros** (membrillo Los Nietitos).
- **Dos formatos conviviendo:** horma/bloque de 2–5 kg (gastronomía y comercio)
  y presentación de 150–500 g (góndola). Son dos listas de precios y dos tipos
  de cliente.
- **Venden por peso variable.** Aparecen como *"media horma x 2 kg **aprox**"*.
  Esto es lo más importante de toda la investigación → ver ⭐1.
- Llegan a góndola de **Devoto** y a revendedores online (La Casa del Queso,
  Salón del Vino Club).

**Ojo:** circula el dato de que "una empresa mexicana absorbió a Milky en 2007".
Casi seguro se confunde con Lacthosa (Honduras/México), que usa la misma marca.
**No lo menciones como un hecho.** Si querés, preguntá suelto: *"¿son
independientes o están dentro de un grupo?"*.

**Objetivo de la reunión:** salir con (a) el trabajo concreto del agente,
(b) de dónde saca precio, stock y peso, (c) qué pasa cuando no sabe, y
(d) decisor y fecha.

---

## ⭐ Las 7 que no podés no hacer

1. **"Cuando un comercio pide 'media horma de muzzarella', ¿el precio final se
   sabe recién cuando la pesan?"**
   → **Es LA pregunta de este proyecto.** Si venden por peso variable, el
   agente **no puede cerrar un importe**. Puede tomar el pedido en kg o piezas
   y confirmar el total al pesar. Definilo ahora o vas a prometer algo que
   físicamente no se puede.

2. **"¿Hay una lista de precios o cada comercio tiene la suya?"**
   → En B2B lácteo casi siempre hay precio por canal (mayorista, minorista,
   gastronomía) más bonificaciones por volumen. Si el precio depende del
   cliente, el agente no puede tirar precios sin saber con quién habla → hace
   falta identificar al comercio antes de cotizar. Cambia todo el diseño.

3. **"¿Tienen días y zonas de reparto? ¿Hasta qué hora se puede pedir para que
   salga mañana?"**
   → Si la respuesta es sí, ahí está el caso de uso más automatizable y de
   menor riesgo: *"¿qué día pasás por Maldonado?"*, *"¿llego para el reparto de
   mañana?"*. Eso lo contesta un agente perfecto, sin inventar nada.

4. **"¿El agente responde solo o le sugiere la respuesta al vendedor?"**
   → Copiloto vs autónomo. Es lo que más mueve precio, riesgo y tiempo de
   aprobación. Con precio por cliente y peso variable, **copiloto es lo
   correcto para arrancar** — llevá esa recomendación armada.

5. **"¿Con qué toman el pedido hoy y dónde termina cargado?"**
   → Preventista con planilla, WhatsApp al depósito, mail, teléfono. Y después
   alguien lo pasa a un sistema. El agente tiene que dejar el pedido donde ya
   termina hoy, no en un lugar nuevo. Si no, nadie lo usa.

6. **"¿Los comercios compran en cuenta corriente? ¿El agente puede tomarle un
   pedido a alguien que debe?"**
   → Si venden a crédito, hay una regla de negocio dura que el agente tiene que
   respetar. Y si puede consultar saldo, es una funcionalidad que vende sola —
   pero necesita acceso al sistema de cobranzas.

7. **"¿Quién decide contratar y para cuándo lo necesitan andando?"**
   → Sin esto la reunión no cierra.

---

## 1. El producto y el catálogo

- Vi que manejan de todo: quesos, ricotta, manteca, dulce de leche, salsa
  cheddar. **¿Cuántos SKU tienen en total?**
- ¿Cuáles fabrican ustedes y cuáles distribuyen de otras marcas?
  *(vi membrillo Los Nietitos en su lista — confirmá el modelo)*
- ¿Cuánto pesa el top 10 sobre la venta total? *(en lácteos suele ser
  muzzarella + danbo la mitad de la facturación)*
- ¿El catálogo cambia seguido: precios, faltantes, estacionalidad?
- ¿Quién actualiza la lista de precios y cada cuánto?
- **¿Me pueden pasar la lista de precios como está hoy?** *(Excel, PDF, foto de
  WhatsApp — como sea. Es el insumo número uno del agente.)*

## 2. Peso variable y unidades (seguí insistiendo después de ⭐1)

- ¿Se pide en kilos, en hormas, en cajas o depende del producto?
- ¿Cuánta diferencia hay entre lo pedido y lo entregado? *(pidió 2 kg, le
  fueron 2,150)*
- ¿El comercio acepta esa diferencia o hay que avisarle antes?
- ¿Hay productos que sí son de peso fijo? *(rallado 150 g, ricotta 400 g)*
  → Esos el agente sí puede cotizar exacto. Puede ser el arranque.

## 3. Cómo entran los pedidos hoy

- ¿Por dónde escriben los comercios: WhatsApp, teléfono, el preventista?
- ¿Cuántos pedidos por día? ¿Cuántos comercios activos tienen?
- ¿Cuántas personas atienden? ¿Es su único trabajo?
- ¿En qué horario piden? ¿Les escriben a la noche o el finde y se pierde?
- ¿Cuánto tardan en contestar, honestamente?
- ¿Cuántos pedidos se pierden o llegan tarde para el reparto?
  → **Este número justifica el precio. Insistí hasta tenerlo.**
- ¿Cuál es la pregunta que más cansa al equipo?
- **¿Me muestran las últimas 20 conversaciones reales?** *(pedilo en vivo, en
  el celular — es lo más valioso de la reunión)*

## 4. Reparto, logística y frío

- ¿Cuántas zonas de reparto tienen? ¿Solo Montevideo o interior también?
- ¿Reparto propio o tercerizado?
- ¿Hay mínimo de compra por zona o por pedido?
- ¿Cuál es la hora de corte para entrar en el reparto del día siguiente?
- ¿Qué pasa si el comercio no está cuando llega el camión?
- ¿El repartidor cobra? ¿Trae la mercadería y la factura juntas?

## 5. Perecedero: vencimientos, canjes y reclamos

Esto es propio del rubro y cambia el alcance.

- ¿Manejan canje o devolución de mercadería próxima a vencer?
- ¿Cuántos reclamos por mes tienen? *(llegó mal, faltó, vino cortado)*
- ¿El agente tiene que atender esos reclamos o solo pedidos?
  → **Recomendación: sacalo del primer alcance.** Un reclamo mal contestado
  por un bot quema la relación con el comercio.
- ¿Hay productos con rotación tan corta que no se pueden pedir con
  anticipación?

## 6. Qué tiene que hacer el agente (alcance)

Preguntá una por una. Cada "sí" es precio.

- ¿Responder qué productos hay y en qué presentación?
- ¿Decir precio? *(depende de ⭐2)*
- ¿Decir si hay stock?
- ¿Decir qué día pasa el reparto por su zona?
- ¿Tomar el pedido y pasarlo al vendedor/depósito?
- ¿Tomar el pedido completo y cargarlo solo?
- ¿Repetir el último pedido del comercio? *("lo de siempre")*
- **¿Escribirle al comercio que hace 10 días no pide?**
  → Este es el que más plata les hace. En B2B de consumo la recompra es
  predecible: si un almacén pide muzzarella cada 8 días y van 14, hay una venta
  perdida. Un agente que detecta eso y avisa **es venta proactiva, no atención
  al cliente**. Levantalo aunque no lo pidan.
- ¿Empujar un producto nuevo o una promo a los comercios que encajan?
- ¿Consultar saldo o estado de cuenta?

**Cerrá con:** *"De todo esto, ¿cuál es el único que si no lo hace, no vale la
pena?"* → ese es el MVP.

## 7. De dónde saca la información

- ¿Qué sistema usan para stock y pedidos? ¿ERP, Excel, papel?
- ¿Con qué facturan? **¿Ya emiten CFE (factura electrónica DGI)?**
  → En Uruguay es obligatorio, así que hay un sistema atrás. Ese sistema tiene
  los clientes, los precios y los productos. Averiguá **cuál** y si tiene API.
- ¿El stock es consultable en tiempo real o es "fijate en cámara"?
- Si el agente dice que hay stock y no hay, ¿qué pasa?
- ¿Quién es el proveedor/técnico que maneja esos sistemas?
- ¿Tienen un CRM o la relación con cada comercio está en la cabeza del
  vendedor?

## 8. El equipo de ventas

- ¿Tienen preventistas con ruta? ¿Cuántos?
- ¿El vendedor cobra comisión por sus comercios?
  → **Si sí, cuidado.** Un agente que toma pedidos "sin el vendedor" le toca el
  bolsillo y lo van a sabotear. La respuesta correcta es que el agente le
  *alimenta* pedidos al vendedor y se los acredita.
- ¿El equipo sabe de este proyecto? ¿Qué opinan?
- ¿Quién va a revisar lo que responde el agente las primeras semanas?

## 9. Límites, tono y errores

- ¿Cómo quieren que hable con el comercio? ¿De usted, de vos?
- ¿Puede dar descuento o negociar? ¿Hasta cuánto?
- ¿Puede comprometer una fecha de entrega?
- Cuando no sabe, ¿deriva, pregunta, o dice que no sabe?
- ¿A quién deriva y cómo se entera esa persona?
- ¿El comercio tiene que saber que habla con una IA?
- ¿Qué es lo peor que podría llegar a decir? ¿Qué los haría apagarlo?

## 10. El canal (WhatsApp)

- **¿Por qué número atienden hoy? ¿Es un celular con WhatsApp común o WhatsApp
  Business API?**
  → Casi seguro es un celular. Automatizar en serio requiere API oficial: alta
  en Meta, verificación de la empresa, costo por conversación y un proveedor.
  **Decilo en la reunión, no en el presupuesto.**
- ¿Un número solo o cada vendedor con el suyo?
- ¿Quieren conservar el número actual? *(se puede migrar, pero el celular deja
  de poder usarlo)*
- ¿Les llegan pedidos también por Instagram o mail?

## 11. Cómo se mide que funcionó

- Si a tres meses esto anduvo bárbaro, ¿qué cambió?
- ¿Qué prefieren: **vender más** a los comercios que ya tienen, **contestar más
  rápido**, o **liberarle horas al equipo**? → son tres productos distintos, no
  dejes que digan "las tres".
- ¿Cuánto es un pedido promedio? ¿Cada cuánto recompra un comercio?
- ¿Cuántos comercios activos tienen y cuántos dejaron de comprar este año?
  → Reactivar comercios dormidos suele ser el ROI más claro y más fácil de
  demostrar.

## 12. Cierre comercial

- ¿Quién decide? ¿Alguien más tiene que verlo?
- ¿Para cuándo lo necesitan andando? ¿Los apura algo?
- Si siguen seis meses como están, ¿qué pasa?
- ¿Tienen presupuesto pensado o un rango?
- ¿Entienden que hay costo mensual? *(modelo + WhatsApp API + mantenimiento)*
- ¿Prefieren arrancar con un solo caso de uso o todo junto?
- Acordá la próxima instancia **con fecha** antes de cortar.

---

## La otra venta: no tienen web

`milky.com.uy` es una página en construcción. Una empresa de 1959 que le vende
a comercios y llega a góndola de Devoto **no tiene sitio**.

Manejalo con cuidado: **son dos ventas distintas y no conviene mezclarlas en la
misma cotización.** Sugerencia de cómo entrarle, casi al pasar:

- *"Vi que milky.com.uy está en construcción. ¿Hace mucho?"*
- *"¿Los comercios nuevos cómo los encuentran hoy?"*
- *"¿Les sirve tener el catálogo con precios online, aunque sea privado con
  usuario para cada comercio?"*
  → Esto es interesante porque **es el mismo insumo que necesita el agente de
  IA**: catálogo estructurado + lista de precios. Una cosa alimenta a la otra.
  Es un argumento honesto, no un upsell forzado.
- El mail `info@milky.com.uy` funciona, así que el dominio está vivo. Igual
  preguntá quién lo tiene y si están al día — más de una vez el dominio está a
  nombre del sobrino que hizo la web en 2015.

También: el Instagram tiene ~278 seguidores y comunica al consumidor final,
cuando su cliente es el comercio. **No es el pedido de ellos — no lo conviertas
en el tema de la reunión.** Guardalo para la segunda.

---

## Señales a escuchar

| Si dicen… | Significa… |
|---|---|
| "nos escriben a toda hora y no damos abasto" | Dolor de cobertura horaria. El caso más fácil de vender y demostrar. |
| "siempre preguntan lo mismo: qué día paso, cuánto sale" | FAQ + reparto. Automatizable sin riesgo. Ese es el MVP. |
| "cada comercio tiene su precio" | No puede cotizar hasta identificar al cliente. Sacá "decir precio" del MVP. |
| "el vendedor cobra comisión" | Diseñá el agente **a favor** del vendedor o te lo boicotean. |
| "el pedido lo toma el preventista en la ruta" | Copiloto para el preventista, no reemplazo. |
| "se nos caen comercios y nos enteramos tarde" | ⭐ Vendé la recompra proactiva. Es el mayor ROI del proyecto. |
| "usamos el WhatsApp de la empresa desde el celular" | No hay API. Sumá tiempo, costo y alta en Meta al plan. |
| "el stock lo sabe el de cámara" | No hay fuente de verdad. El agente no puede prometer disponibilidad. |
| "que también atienda los reclamos" | Otro producto. Cotizalo aparte o se te va el alcance. |
| "ya probamos un bot y lo sacamos" | Preguntá por qué. Casi siempre: respondía cualquier cosa o no derivaba. |

---

## Para llevar a la reunión

- Un agente andando para mostrar, en vez de explicar.
- El celular listo para pedirles capturas de conversaciones reales.
- **Pedí la lista de precios y el listado de comercios activos.** Sin eso no hay
  cotización seria.
- Anotar los números que definen el precio: pedidos/día, comercios activos,
  SKU, personas atendiendo, ticket promedio, zonas de reparto.

## Cosas que no hay que prometer

- **No des precio** hasta saber si es copiloto o autónomo y si hay API de WhatsApp.
- **No prometas que cotiza exacto** hasta resolver el tema del peso variable.
- **No prometas integración** con su sistema de facturación hasta ver si tiene API.
- **No prometas que atiende reclamos.** En perecedero, un reclamo mal contestado
  cuesta un cliente.
- **No prometas que reemplaza al vendedor.** Auxilia. Además destraba la
  resistencia interna.
- **No mezcles la web con el agente** en la misma cotización.

---

*Fuentes de la investigación: [milky.com.uy](http://www.milky.com.uy/) ·
[@lacteos.milky](https://www.instagram.com/lacteos.milky/) ·
[Facebook Lácteos Milky](https://www.facebook.com/MilkyLacteos/) ·
[La Casa del Queso — marca Milky](https://www.lacasadelqueso.com.uy/productos/productos_por_marca.php?id_representacion=132&secc=productos_marcas&id_menu=122)
· [Devoto — quesos](https://www.devoto.com.uy/frescos/quesos)*
