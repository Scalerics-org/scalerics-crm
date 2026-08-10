# Milky — reestructura de 3 puestos a 2 personas + agente de IA

**Situación:** se jubila Sandra. Quedan 2 personas. La idea es que la IA absorba
~80% de lo de Sandra y los otros 2 puestos absorban el 20% restante, además de
pasarle a la IA toda la tarea administrativa que se pueda.

**Este documento:** el mapa de las tareas, qué puede tomar la IA de verdad, qué
no, y en qué orden conviene hacerlo.

---

## Lo primero, porque tiene fecha de vencimiento

**Antes de escribir una línea de código: grabar a Sandra haciendo su trabajo.**

Cuando se va, se lleva el criterio: por qué ese proveedor va a esa cuenta, cómo
convierte unidades a kilos, qué informe mira la directora y para qué. Eso no
está escrito en ningún lado y no se recupera después.

Concretamente, mientras siga:
- [ ] Grabar la pantalla mientras hace cada tarea, una vez cada una. Sin
      preparar nada, como lo hace un día normal.
- [ ] Copia de **todos** sus Excel, tal cual están, sin ordenar.
- [ ] Que anote las excepciones: *"este proveedor siempre viene mal", "esto lo
      hago distinto en fin de mes"*.
- [ ] Lista de qué informe le manda a quién y cada cuánto.

Cuesta 3 o 4 horas de su tiempo y es la diferencia entre un proyecto y un
problema. **Esto va primero aunque el proyecto no se cierre.**

---

## Un dato que cambia el enfoque: dónde están las horas

120 facturas por mes son ~6 por día hábil. Tipearlas es del orden de **6 a 10
horas al mes**. Es poco.

**Las horas no están en cargar facturas. Están en:**

| Tarea | Frecuencia | Por qué come tiempo |
|---|---|---|
| Levantar saldo bancario a mano | **diaria** | Todos los días, sin excepción |
| Confeccionar pagos a proveedores | **diaria** | Hay que cruzar vencimientos con plata disponible |
| Conciliación bancaria y de cheques | recurrente | Buscar la diferencia es lo que duele, no el registro |
| Conciliación de proveedores y deudores | recurrente | Ídem |
| Conciliación de resguardos (3 fuentes) | mensual | Cruzar DGI ↔ nube ↔ contabilidad a mano |
| Tildar todo para DGI | mensual | Con vencimiento fiscal encima |
| Balance completo en Excel | mensual | Caja, bancos, deudores, cheques, DGI |
| Informes del sistema + estadísticas de ventas | recurrente | Armado manual repetido |
| Doble carga de compras (ver abajo) | diaria | **El más caro de todos** |

Si el proyecto se vende como "la IA tipea las facturas", se vende barato y
resuelve poco. **El valor está en las conciliaciones y en el trabajo diario.**

---

## El hallazgo estructural: la misma factura se carga dos veces

Miren estas tres tareas juntas:

- **Puesto administrativo, tarea 1:** registra las 120 facturas y las carga al
  sistema.
- **Sandra, tarea 1:** planilla Excel con los ingresos de compras — unidades,
  kilos — y los ingresa al sistema.
- **Sandra, tarea 3:** carga las entradas de las compras de productos.

**Es el mismo documento entrando dos veces por dos puertas distintas.** Una
persona lo mira como gasto y lo contabiliza; la otra lo mira como mercadería y
carga unidades y kilos. El papel es el mismo.

De acá sale el puesto, no del tipeo. Si la factura entra **una vez** y de ahí
salen las dos cosas — el asiento contable y el ingreso de stock en kilos — se
elimina un circuito completo.

**Es la primera cosa que hay que validar en la reunión.** Si por alguna razón
no son la misma factura (por ejemplo, la mercadería entra con remito y la
factura llega después), el ahorro es menor y hay que rehacer el número.

---

## El otro cambio importante: la factura no entra por PDF

**Lo que trajeron:** la IA lee el correo, abre los PDF y los pasa a un Excel.

**El problema, que ya detectaron ustedes mismos:** *"no todas llegan por
correo"*. Un sistema que depende del mail va a fallar justo en las que faltan, y
nadie se entera hasta el cierre.

**La alternativa:** en Uruguay las facturas de proveedores son CFE
electrónicos. El comprobante existe en formato XML — con RUT, fecha, moneda,
importes, IVA discriminado y el detalle de líneas — **antes** de que llegue
ningún PDF. Se puede ir a buscar a la fuente en vez de esperar que llegue:

1. **DGI**, que tiene consulta de comprobantes recibidos con descarga.
2. **El proveedor de facturación electrónica de Milky** — el mismo servicio "en
   la nube" donde hoy emiten los resguardos. Suele ser el camino más limpio y
   muchos tienen API.

Esto cambia tres cosas de fondo:

- **Deja de faltar información.** No importa si el proveedor mandó mail o no.
- **Deja de haber OCR en el camino crítico.** Un XML no se lee mal; un PDF sí.
  Sin dato inventado.
- **El "tildar para DGI" desaparece como tarea manual**, porque el dato ya viene
  de DGI.

El OCR / lectura con IA queda para el resto: facturas de exterior, gastos
sueltos, comprobantes no electrónicos. Es el 10–20%, no el 100%.

> ⚠️ **A verificar antes de prometer:** el alcance exacto de la consulta de
> recibidos para el RUT de Milky, y si el detalle de líneas (unidades y kilos)
> viene completo en los XML de sus proveedores. Si el detalle no viene o viene
> pobre, la carga de kilos necesita otra fuente y hay que decirlo antes, no
> después.

---

## Las mismas conciliaciones son un solo motor

Ustedes ya describieron la conciliación de resguardos perfecto: **DGI ↔ lo
emitido en la nube ↔ contabilidad**. Tres fuentes, se cruzan, aparecen las
diferencias.

Esa es exactamente la misma forma que:

- Facturas: **DGI ↔ sistema ↔ contabilidad**
- Banco: **extracto ↔ pagos registrados ↔ contabilidad**
- Cheques: **cartera ↔ depositado/acreditado ↔ contabilidad**
- Proveedores: **estado de cuenta del proveedor ↔ lo nuestro**
- Deudores: **cuenta corriente del comercio ↔ cobranzas**

**Se construye un motor de conciliación y se le enchufan fuentes distintas.** No
son cinco desarrollos. Eso es lo que hace que el proyecto entre en presupuesto.

Y lo importante del diseño: **el trabajo de la IA no es tildar lo que coincide
—eso lo hace cualquier planilla— es explicar lo que no coincide.** "Esta
diferencia de $4.312 es la factura 1187 de tal proveedor, contabilizada dos
veces el 12 y el 14." Eso es lo que hoy le come la tarde a una persona.

---

## Mapa de tareas: quién hace qué después

**IA** = lo hace el agente. **IA→P** = la IA lo prepara, una persona aprueba.
**P** = queda humano.

### Puesto administrativo / contable

| # | Tarea | Después | Notas |
|---|---|---|---|
| 1 | Registración de 120 facturas/mes | **IA** | Vía XML, no PDF |
| 1b | Asiento contable de cada factura | **IA→P** | Aprende del historial. Objetivo: 90% propuesto solo, la persona revisa excepciones |
| 1c | Tildar para DGI | **IA** | Se vuelve automático si el dato viene de DGI |
| 2 | Armar los pagos según vencimiento | **IA→P** | La IA arma la propuesta diaria; la persona decide a quién se le paga |
| 3 | Alta de pagos en el banco + transferencia | **P** | 🔴 **Nunca la IA.** Ver abajo |
| 3b | Asiento del comprobante de transferencia | **IA** | Hoy es a mano; se cruza contra el pago aprobado |
| 4 | Conciliación de proveedores | **IA→P** | La IA marca las diferencias |
| 4b | Conciliación de deudores y cobranzas | **IA→P** | Ídem |
| 4c | Cheques: cartera, depósito, acreditación | **IA→P** | Necesita definir de dónde sale el dato |
| 5 | Balance: caja, bancos, deudores, cheques, DGI | **IA→P** | La IA arma el borrador; el criterio contable es de la persona / el contador |
| 6 | Conciliación de resguardos (DGI ↔ nube ↔ contabilidad) | **IA→P** | Caso ideal: tres fuentes estructuradas |
| 7 | Levantar saldo bancario diario | **IA** | 🟢 Automatizable entero |
| 8 | Flujo de caja | **IA** | Sale solo del punto 7 + vencimientos + cheques a cobrar |

### Sandra (se jubila)

| # | Tarea | Después | Notas |
|---|---|---|---|
| 1 | Excel de ingresos de compras: unidades y kilos → sistema | **IA** | Es la misma factura del punto 1. **Acá está el puesto** |
| 3 | Carga de entradas de compras | **IA** | Ídem |
| 2 | Informes del sistema + estadísticas de ventas | **IA** | 🟡 Depende de si el sistema deja sacar los datos — ver riesgos |
| 4 | Cartas de promociones, cambios de precio y ofertas | **IA→P** | La directora sigue decidiendo la oferta; la IA redacta y arma la comunicación por comercio |

### El tercer puesto

**No tenemos sus tareas.** Tenemos el detalle del puesto contable y el de
Sandra. Falta el tercero, que es justamente uno de los dos que quedan y que va a
tener que absorber trabajo. **Sin eso el reparto final no se puede cerrar.**

---

## 🔴 Lo que la IA no va a hacer, y hay que decirlo en la reunión

**1. Liberar transferencias.** La IA arma la propuesta de pago; una persona
autoriza en el banco con su token. No es una limitación técnica, es la única
defensa contra el fraude más común del rubro: el mail que avisa que "el
proveedor cambió de cuenta bancaria". Si la IA pudiera pagar sola, ese mail
saldría caro.

> Al revés, acá la IA agrega seguridad: puede avisar *"esta cuenta bancaria no
> es la que usamos las últimas 14 veces con este proveedor"*. Eso hoy no lo
> chequea nadie.

**2. Decidir ofertas y precios.** Ustedes ya lo definieron bien: lo decide la
directora con la parte comercial. La IA redacta y distribuye, no decide.

**3. Firmar el balance y fijar criterio contable.** La responsabilidad es
personal y del contador. La IA deja el borrador armado y las diferencias
explicadas.

**4. Resolver diferencias con un proveedor o un cliente.** Las detecta, las
escala. No negocia.

---

## Etapa 1 del prototipo

Ustedes dijeron que la etapa 1 tiene que cubrir todo el trabajo. Se puede, con
una condición: **que cubra toda la cadena pero en modo "propone, no ejecuta", y
corriendo en paralelo al proceso actual durante un mes.**

Nadie apaga el Excel el día uno. Durante ese mes se compara: donde la IA
coincide, se deja de hacer a mano; donde falla, se corrige. Al final del mes
hay evidencia, no promesa — y ahí sí se apagan circuitos.

Orden sugerido, de menor riesgo a mayor:

**Semanas 1–2 — el primer resultado visible, sin tocar nada**
- Espejo de comprobantes recibidos (DGI / proveedor de e-factura).
- Saldo bancario diario y flujo de caja automático, en el mail a primera hora.
- *Por qué primero:* no escribe en ningún sistema, no puede romper nada, y
  reemplaza una tarea diaria desde el día uno. Es lo que hace que el equipo
  crea en el proyecto.

**Semanas 3–5 — el núcleo**
- Factura → asiento propuesto + ingreso de unidades y kilos (la doble carga).
- Tildado de tres puntas: DGI ↔ sistema ↔ contabilidad.

**Semanas 6–8 — el ciclo de pagos**
- Propuesta diaria de pagos por vencimiento.
- Comprobante de transferencia → asiento automático.
- Alerta de cuenta bancaria cambiada.

**Semanas 9–12 — las conciliaciones pesadas**
- Bancaria, cheques, proveedores, deudores.
- Resguardos.
- Borrador de balance.

**En paralelo, independiente de todo lo anterior**
- Informes del sistema y estadísticas de ventas.
- Cartas de ofertas y cambios de precio.
- *Se puede hacer en cualquier momento porque no toca contabilidad.* Y conecta
  directo con el agente de ventas del que hablamos antes.

---

## Riesgos, ordenados por lo que más puede doler

**1. 🔴 El sistema instalado.** Todo el plan asume que se le puede sacar y meter
datos. Si es un sistema cerrado, sin base de datos accesible, sin exportación ni
API, la mitad de esto se cae o se vuelve frágil. **Es la primera cosa a
confirmar, antes de cualquier presupuesto.**

**2. 🔴 El reparto es de 3 a 2 personas reales.** Si el 80% resulta ser 60%, las
dos que quedan trabajan de más y el proyecto queda pegado a eso. Por eso:
**registro de horas por tarea durante dos semanas, antes de definir el reparto.**
Sin ese registro el 80% es una impresión, no un número.

**3. 🟡 La fecha de Sandra manda.** Si se va en dos meses, no hay prototipo
maduro para entonces. Plan realista: la IA arranca por las tareas de ella que se
pueden mover ya (informes, cartas, ingreso de compras) y el resto se cubre a
mano un tiempo. Decirlo ahora es mejor que descubrirlo en octubre.

**4. 🟡 El contador.** Si es externo y no está de acuerdo con cómo se generan los
asientos, bloquea. Hay que sentarlo en la mesa temprano, no al final.

**5. 🟡 Los meses cerrados.** La IA aprende a contabilizar del historial. Si el
histórico está desprolijo, aprende mal. Hay que mirar 3 meses reales antes de
prometer el 90% de asientos automáticos.

---

## Para confirmar en la reunión

**Sistema y datos** *(lo más urgente)*
- ¿Cuál es exactamente el sistema instalado? ¿Quién lo soporta?
- ¿Tiene base de datos accesible, exportación o API? ¿Alguien lo integró alguna vez?
- ¿Los informes de ventas salen exportables o solo se ven en pantalla?
- ¿Quién es el proveedor de facturación electrónica? *(donde emiten los resguardos)*
- ¿Desde cuándo son emisores electrónicos?

**Circuito**
- **¿La factura de compra que carga el área contable es la misma que carga
  Sandra con unidades y kilos?** *(la pregunta que más plata define)*
- ¿La mercadería entra con remito y la factura llega después?
- ¿El detalle de kilos viene en la factura o se calcula acá?
- ¿Cuántos proveedores activos? ¿Cuántos comercios en cuenta corriente?
- ¿Qué banco o bancos? ¿Tienen banca empresas con descarga de extractos?
- ¿Los cheques son físicos o ya hay electrónicos?

**Personas**
- **¿Cuáles son las tareas del tercer puesto?** *(falta ese mapa)*
- ¿Cuándo se va Sandra exactamente?
- ¿Las 2 que quedan saben del proyecto? ¿Qué opinan?
- ¿Quién va a ser el referente interno que revisa lo que hace la IA?
- ¿El contador es interno o externo? ¿Firma el balance?

**Decisión**
- ¿Quién aprueba esto? ¿La directora sola?
- ¿Hay fecha límite atada a la jubilación?
- ¿Entienden que hay costo mensual? *(modelo + infraestructura + mantenimiento)*

---

## Cómo contarlo en una frase

> No se trata de reemplazar a Sandra. Se trata de que la factura entre una sola
> vez en vez de dos, y de que las conciliaciones las arme la máquina y las
> resuelva una persona. Con eso el trabajo de tres entra en dos.
