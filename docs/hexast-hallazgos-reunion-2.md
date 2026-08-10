# Hexast — lo que salió de la reunión 2

**Fecha:** 04/08/2026
**Resumen en una línea:** el modelo que construimos está equivocado en su base, y apareció un segundo sistema adentro del primero (trazabilidad unitaria + remito de cirugía).

---

## 1. Lo que rompe el modelo actual

> **Estado: corregido en el prototipo (06/08/2026).** El producto ahora tiene
> tipo (implante / instrumental), la plantilla sólo admite instrumental, y los
> implantes cuelgan del movimiento de salida. La rendición cuenta contra lo
> que salió, no contra la plantilla. El resto de este documento sigue
> pendiente.

### La caja no lleva implantes

Nosotros modelamos la caja como un contenedor de implantes con una plantilla
("caja de hombro = 6 arpones 3,0 + 5 de 4,5 + …"). **Es incorrecto.**

| Cómo lo modelamos | Cómo es en realidad |
|---|---|
| La caja de hombro contiene 22 implantes | La caja de hombro contiene **instrumental**, y "en promedio siempre tiene lo mismo" |
| Los arpones están adentro de la caja | **Los arpones siempre van separados de la caja** |
| Se rinde contando implantes de la caja | Se rinde contando **los implantes que viajaron aparte** |

Son **dos cosas distintas que viajan juntas**:

- **Instrumental** — motor canulado, baterías, la caja en sí. No se consume,
  vuelve siempre, contenido estable.
- **Implantes** — arpones, tornillos. Se consumen, viajan sueltos, y cada
  unidad tiene identidad propia.

**Lo que sí se salvó:** la rendición por diferencia sigue siendo correcta,
pero aplicada a los implantes. Dante lo confirmó con otro ejemplo: *"caja de
rodilla con 22 tornillos diferentes, se usan 2 y vuelven 20"*. El mecanismo
es el bueno; estaba colgado del objeto equivocado.

---

## 2. Trazabilidad unitaria — el sistema nuevo adentro del sistema

**Todos los implantes** tienen, según Dante:

- Número de lote
- Número de serie
- Fecha de vencimiento
- Fecha de fabricación
- Número de ANMAT y **PM** (registro de producto médico)
- Material
- Fecha de esterilizado y **vencimiento del esterilizado**
- Código de barras **y** código QR

Esto significa que un producto deja de ser "37 arpones de 3,0" y pasa a ser
**37 unidades, cada una con su identidad**. Cambia el modelo de datos entero
y todas las pantallas de conteo.

### ANMAT es selectivo

*"ANMAT lleva trazabilidad en ciertos productos, rodilla y cadera, no todos
los productos."*

O sea: la obligación regulatoria alcanza a una parte del catálogo, pero el
dato existe en todos. El sistema debería capturarlo siempre y **reportar**
solo donde corresponde.

> Ojo: aparece **cadera**, que no estaba en el brief original (hombro,
> rodilla, tenodesis, pie y tobillo). Hay que confirmar el catálogo real.

### El escaneo no es un lujo, es la condición

*"Quiere escanear esta info para no estar cargando producto por producto."*

Con 37 unidades por producto y ocho campos por unidad, **cargar a mano es
imposible**. Si no se escanea, el sistema no se usa. Pasa de "estaría bueno"
a requisito.

---

## 3. Vencimientos — el dolor más fresco

> **Estado: hecho en el prototipo (06/08/2026).** Existe el lote con su
> vencimiento, la entrada lo pide, la rendición y la transferencia consumen
> primero lo que antes vence, y hay una pantalla propia ordenada por fecha con
> filtro "Este año". Falta la unidad con número de serie: eso depende de ver
> una etiqueta real.


*"Ayer preparó una mercadería que tenía que entregar en Jujuy y se encontró
con tornillos que vencen en diciembre."*

Es un caso concreto, de ayer, con nombre de destino. Vale tanto como el de la
caja del 3 de julio.

Lo que pidió explícitamente:

- **Tener la información a mano** al preparar una entrega
- **Saber qué tornillos se vencen este año**
- Ver el vencimiento de cada implante

Esto implica: vencimiento por unidad, una vista de "qué se vence", y —lo más
valioso— **un aviso al armar la salida** cuando lo que estás por mandar vence
pronto. Eso es lo que le hubiera evitado el problema de Jujuy.

---

## 4. El remito de cirugía — otra pieza entera

*"Que en la salida de mercadería se genere un remito que figure: caja de LCA,
motor canulado con 2 baterías, y el detalle de cada implante. Y después el
nombre del paciente, fecha de cirugía, nombre del doctor y lugar de cirugía.
Y el remito sale a nombre del cliente que compra esa cirugía."*

Un documento que hoy no existe en el sistema, y que arrastra entidades nuevas:

| Pieza | Qué es |
|---|---|
| **Cirugía** | Paciente, fecha, doctor, lugar |
| **Cliente** | A nombre de quién sale el remito — el que compra la cirugía |
| **Instrumental en el remito** | "Caja de LCA, motor canulado con 2 baterías" |
| **Implantes en el remito** | El detalle unidad por unidad, con lote y serie |

Y con esto la salida deja de ser "sacar una caja": es **preparar una cirugía**.
Ese es el verdadero flujo del negocio.

---

## 5. Qué queda en pie del prototipo

**Se salva:**
- La rendición por diferencia (el mecanismo, aplicado a implantes)
- El tablero con semáforo de días afuera
- El stock consolidado de los dos depósitos
- El catálogo con buscador y envío por WhatsApp
- Todo el sistema de diseño y las pantallas como lenguaje visual
- La arquitectura: interfaz de datos única, tokens, despliegue

**Hay que rehacer:**
- El modelo: separar instrumental de implantes
- La plantilla de caja: pasa a ser contenido de instrumental
- La rendición: pasa a operar sobre implantes con identidad unitaria
- El stock: de "cantidad por depósito" a "unidades con lote, serie y vencimiento"

**Hay que construir de cero:**
- Escaneo de código de barras / QR
- Vencimientos: vista, alertas y control al preparar la salida
- Cirugía como entidad (paciente, fecha, doctor, lugar)
- Cliente
- Remito

---

## 6. Preguntas que quedaron abiertas

Estas hay que contestarlas antes de cotizar en firme.

### Sobre los códigos — **la más importante de todas**

**¿El código de barras trae los datos adentro o es solo un identificador?**

Los implantes suelen venir con **GS1 DataMatrix**, que codifica en el mismo
símbolo el producto, el lote, el vencimiento y la serie. Si es así, un escaneo
llena todos los campos solos y el sistema vuela. Si el código es solo un
número que hay que cruzar contra una tabla, el trabajo es otro y bastante
mayor.

*Cómo averiguarlo sin tecnicismos: pedirle una foto de la etiqueta de un
implante. Con eso se ve en dos minutos.*

### Sobre el instrumental
- ¿El instrumental también se controla por unidad, o alcanza con la caja como un todo?
- ¿El motor canulado y las baterías son de Hexast o del sanatorio?
- ¿La caja de instrumental vuelve siempre completa, o también hay faltantes?

### Sobre la operación
- ¿Los implantes viajan en algún contenedor propio o sueltos?
- ¿Quién es "el cliente que compra la cirugía": la ortopedia, el sanatorio, la obra social?
- ¿El remito es un documento fiscal o un papel interno de control?
- ¿Necesita numeración correlativa de remitos?
- ¿Qué pasa con el implante que sale y no se usa: vuelve al stock con su lote y serie?

### Sobre el alcance regulatorio
- ¿Qué productos exactamente entran en la trazabilidad de ANMAT?
- ¿Hoy reporta a ANMAT? ¿Cómo, en qué formato?
- ¿Aparece cadera en el catálogo? El brief original no la incluía.

---

## 7. Qué significa para el presupuesto

El presupuesto de **U$S 2.290** cubre el sistema tal como está construido:
cajas, rendición por cantidad, stock consolidado y catálogo. **Con esta
información, queda corto** — y además describe un modelo que Dante acaba de
corregir.

Lo que se agrega no son ajustes, son módulos enteros:

| Módulo | Por qué es grande |
|---|---|
| Separar instrumental de implantes | Toca el modelo, la semilla y cuatro pantallas |
| Trazabilidad unitaria | Cada unidad pasa a ser una fila con ocho campos |
| Escaneo de códigos | Cámara, lectura y parseo del formato |
| Vencimientos | Vista, alertas y control en la salida |
| Cirugía + cliente + remito | Tres entidades y un documento imprimible |

**No cotizar de nuevo hasta responder la pregunta del código de barras.** Es
la que más mueve el número.

---

## 8. Lo bueno de todo esto

Dante no está pidiendo cosas de más: está describiendo su operación real, y
la describió con dos casos concretos y recientes (la caja del 3 de julio, los
tornillos de Jujuy). Eso es un cliente que se está comprando el proyecto.

Y el corazón del sistema —la rendición por diferencia— sigue siendo correcto.
Con escaneo se vuelve incluso **mejor**: escaneás lo que volvió, y lo que no
aparece es lo que se usó, identificado por lote y serie. El mismo modelo
mental que ya le mostramos, ahora al nivel de unidad y sirviendo para ANMAT.
