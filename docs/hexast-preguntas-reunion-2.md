# Hexast — preguntas para la segunda reunión

**Estado del deal:** discovery hecho. Dante validó el dolor. Vamos a mostrarle
el prototipo del sistema de stock.

**Objetivo de esta reunión:** salir con (a) los datos reales para cargar el
sistema, (b) las tres definiciones que cambian el precio, y (c) fecha y
decisor para cerrar.

**Lo que ya está respondido de la primera reunión — no lo vuelvas a preguntar:**
dos depósitos (BA y Tucumán), hoy todo en Excel con un archivo por depósito,
quiere stock consolidado de los dos, las cajas salen para cirugía y vuelven
parcialmente (22 → 19), también presta a ortopedias, quiere saber dónde está
cada caja y hace cuánto salió, las cirugías suspendidas le demoran las
devoluciones, y tiene fotos de productos que hoy manda a mano por WhatsApp.

---

## ⭐ Las 5 que no podés no hacer

Si la reunión se corta, hacé solo estas.

1. **"En Tucumán, ¿quién recibe y despacha la mercadería?"**
   → En la primera reunión dijo que el sistema lo usaría **solo él**. Pero
   tiene un depósito a 1.200 km. Si hay una persona en Tucumán cargando cosas,
   el sistema deja de ser de un usuario y necesita cuentas, permisos y
   auditoría de quién hizo qué. Es la pregunta que más puede mover el alcance
   y nadie la hizo.

2. **"¿ANMAT les exige reportar trazabilidad por lote y número de serie? ¿Lo
   hacen hoy?"**
   → Si la respuesta es sí, el sistema pasa de "lindo tener" a obligatorio y
   es tu mejor palanca de venta. También multiplica el trabajo: cada unidad
   pasa a tener identidad propia en vez de ser "3 arpones de 3,0".

3. **"¿Cómo entra la mercadería? ¿Importás, comprás acá, te fabrican?"**
   → De esto **no se habló nada** en la primera reunión, y es literalmente la
   mitad de lo que pidió ("manejar la entrada y salida"). Sin esto el sistema
   sabe restar pero no sumar.

4. **"Cuando la caja está en Meditec, ¿esa mercadería sigue siendo tuya o ya
   se la vendiste?"**
   → Define qué significa "stock". Si es consignación, el material afuera
   sigue siendo suyo y el stock consolidado tiene que sumarlo. Si es venta,
   sale del inventario al entregar. Son dos sistemas distintos.

5. **"¿Quién decide contratar y para cuándo lo necesitás andando?"**
   → Sin esto la reunión no cierra. Preguntala aunque el resto haya salido mal.

---

## 1. Datos que necesito para cargar el prototipo

Estas no son preguntas, son pedidos. Salí de la reunión con el material o con
fecha de cuándo te lo manda.

- [ ] **Los dos Excel**, uno por depósito. Tal como están, sin limpiar.
- [ ] **Las fotos de los productos**, con el nombre de cada una.
- [ ] **Qué hay adentro de cada caja**: producto y cantidad. Una por tipo de
      caja alcanza.
- [ ] **Cuántas cajas tiene en total** y cómo las llama.
- [ ] **Con qué ortopedias y sanatorios trabaja** — la lista completa.

Preguntas de apoyo mientras te lo pasa:

- ¿Cuántos códigos distintos maneja? ¿30, 100, 500?
- ¿Cómo identifica un producto: código propio, código del fabricante, GTIN?
- ¿Las cajas siempre tienen el mismo contenido o lo arma según la cirugía?
- ¿El instrumental (lo que no se consume) también hay que controlarlo, o solo
  los implantes?
- ¿Vende unidades sueltas, sin caja? *(Hoy el sistema asume que todo se mueve
  en cajas. Si vende 3 tornillos sueltos a una ortopedia, falta un flujo.)*

---

## 2. Entradas de mercadería

Tema nuevo. No se tocó en la primera reunión.

- ¿Cada cuánto entra mercadería? ¿Semanal, mensual, cuando se acaba?
- ¿Entra a un solo depósito y de ahí reparte, o a los dos por separado?
- ¿Viene con remito o factura del proveedor? ¿Lo guarda?
- ¿Le importa registrar **cuánto le costó** cada cosa?
  → Si dice que sí, se abre margen por producto y valorización de inventario:
  es otra funcionalidad y otro precio.
- ¿Hoy cómo se entera de que tiene que reponer? ¿Mira el Excel, se acuerda, se
  queda sin stock y ahí compra?
- ¿Quiere que el sistema le marque qué reponer, o prefiere decidirlo él?

---

## 3. Transferencias entre depósitos

- ¿Se manda mercadería de Buenos Aires a Tucumán? ¿Cada cuánto?
- **¿Cuánto tarda en llegar?**
  → Si tarda días, hace falta un estado "en tránsito" y alguien que confirme
  la recepción del otro lado. Si es rápido, la transferencia es instantánea y
  es una pantalla mucho más simple.
- ¿Alguna vez llegó menos de lo que salió?
- ¿Hoy cómo registra esa transferencia?

---

## 4. Qué significa "stock" exactamente

Esto define el modelo de datos. Vale la pena insistir hasta que quede claro.

- Las unidades que están **adentro de una caja en el depósito**, ¿son stock
  disponible para vender, o están comprometidas?
- La caja que está en Meditec hace 30 días, ¿su contenido cuenta como stock?
- ¿En qué momento factura: cuando entrega la caja o cuando le confirman que se
  usó el implante?
- Cuando repone una caja para dejarla completa, ¿saca del mismo depósito o
  compra específicamente para eso?
  → **Es la que más me sirve a mí.** Define si "completar una caja" mueve
  stock o solo cambia el estado de la caja.

---

## 5. Trazabilidad y regulatorio

Además de la pregunta ⭐2:

- ¿Los productos vienen con código de barras GS1 o DataMatrix que se pueda
  escanear?
  → Si escanean, el sistema vuela. Si hay que tipear serie por serie, no lo
  van a usar y hay que diseñar la carga de otra manera.
- ¿Los implantes tienen vencimiento? ¿Alguna vez se le venció material adentro
  de una caja que estaba afuera?
- Si mañana un cirujano reclama por un implante, ¿tiene que poder decir de qué
  lote salió?

---

## 6. Catálogo y WhatsApp

Su segundo dolor. Dijo textual que lo más práctico es *responder rápido por
WhatsApp con la foto exacta*.

- Cuando le preguntan por un producto, ¿manda **una** foto o varias?
- ¿Manda precio junto con la foto, o el precio va aparte?
  → Define si el catálogo puede ser una web pública o tiene que ser privado.
- ¿Quién le escribe: ortopedias, instrumentadores, médicos? ¿Cuántas consultas
  por día?
- **Mostrale el prototipo compartiendo una foto por WhatsApp desde el celular.**
  Preguntale si eso le alcanza o si quiere que se mande solo.
  → Compartir desde el celular es gratis. Automático es WhatsApp Business API:
  costo mensual, aprobación de Meta, y es un proyecto aparte. Que la diferencia
  la elija él viéndola, no explicada.

---

## 7. Operación real, para que no se caiga después

- En el depósito, ¿tiene señal y wifi? ¿Y en el sanatorio?
  → Si carga cosas sin internet, hace falta que funcione offline y sincronice.
  No es gratis.
- ¿Lo va a usar del celular, de la computadora, o los dos?
- **¿Quiere que el sistema le avise** cuando una caja lleva mucho tiempo
  afuera, o le alcanza con abrirlo y verlo?
  → Notificaciones implican servidor corriendo y la app instalada en el
  teléfono. Es un salto de alcance, no un detalle.
- Si mañana deja de usar el sistema una semana, ¿qué se rompe?

---

## 8. La web

Ojo con esto: **`hexast.com.ar` no resuelve DNS**. No es que la web esté
"caída": el dominio no está apuntando a ningún lado. Puede estar vencido, o la
web puede haber vivido en otra dirección.

- ¿Cuál era la dirección exacta de la web?
- ¿Tiene los accesos: dominio, hosting, casilla de correo asociada?
- ¿Quién se la hizo y por qué dejó de funcionar?
- ¿Para qué la quiere: que lo encuentren, mostrar el catálogo, vender?
- ¿Tiene mails con el dominio (@hexast.com.ar)? ¿Le siguen llegando?
  → Si el dominio venció, puede estar perdiendo mails y no saberlo. Es un
  hallazgo fuerte para llevarle.

---

## 9. Cierre comercial

- ¿Quién decide? ¿Alguien más opina? *(Confirmá que es él solo.)*
- ¿Para cuándo lo necesita funcionando? ¿Hay algo que lo apure?
- Si sigue como está seis meses más, ¿qué le pasa?
  → Acá aparece el número que justifica el precio. Si alguna vez perdió una
  caja o un implante, preguntale cuánto salía.
- ¿Prefiere pago único o mensual con soporte?
- Acordá la tercera instancia **con fecha** antes de cortar.

---

## Cosas que no hay que prometer en esta reunión

- **No des precio.** Primero valida el prototipo, después se presupuesta.
- **No prometas WhatsApp automático** hasta confirmar si compartir desde el
  celular le alcanza.
- **No prometas lote y serie** hasta saber si escanean código de barras.
- **No prometas la web** en el mismo paquete que el sistema. Son dos ventas.
- Aclarale que el prototipo **se resetea al refrescar**: es una maqueta
  funcional para decidir, no el sistema instalado.
