# Milky — notas de la reunión

## El planteo

La tarea de 3 personas se quiere reestructurar para que la puedan asumir las 2
personas que quedan más el agente de IA. La persona que se va es por jubilación.
La idea es reestructurar los 3 puestos.

El criterio: la IA va a hacer el **80% del trabajo de la persona que se va**, y
los otros 2 puestos van a absorber el **20% restante**.

En tareas administrativas, pasarle a la IA todo lo que se pueda, ya que se va
una persona y las tareas son muchas.

La idea es ver las tareas de los 3 puestos y analizar todo el conjunto. **La
primera etapa del prototipo cubre todo el trabajo.**

---

## Situación actual

**Facturas**
- Se registran todas las facturas que llegan a la empresa: **120 por mes**.
- Después se cargan en el sistema. Tienen instalado **Psyco / Mercado**.
- Todo eso hay que tildarlo para pasarlo a DGI.
- Hay que ver cómo se pueden contabilizar solas.
- Llegan facturas todos los días, y **no todas llegan por correo**.

**Idea inicial que trajeron**
- Que la IA tome desde el correo, lea los PDF de cada factura y los pase a un
  Excel.
- Es lo que vieron hasta ahora; capaz se puede buscar una mejor solución.

**Contabilidad**
- Conciliaciones bancarias y de cheques.
- Toda la parte contable.
- **Todo se hace en Excel.**

---

## Tareas del puesto administrativo / contable

1. **Registración de facturas.**

2. **Confección de los pagos a proveedores.** Se hace sobre la fecha de
   vencimiento de cada factura. **Los pagos a proveedores se hacen a diario.**

3. **Alta de los pagos en el banco.** Cuando pagan, se hace vía transferencia.
   Ahí tienen el comprobante de la transferencia del banco, con el que se
   registra el asiento **a mano**.

4. **Conciliación de proveedores y de deudores.** El punto uno es la cobranza de
   la empresa: se cobra todo con cheque y se ingresa, o el cliente transfiere
   dentro del plazo de pago.

5. **Balance completo:** caja, bancos, deudores, cheques y DGI.

**Conciliación de resguardos:** los resguardos se concilian contra tres fuentes
—contra DGI, contra lo que está emitido en la nube y contra lo que está en
contabilidad.

**Saldo bancario:** se levanta a diario y se hace todo a mano. Se usa para el
flujo de caja.

---

## Tareas de Sandra (la persona que se jubila)

1. **Planillas Excel.** Maneja todos los ingresos de las compras —las unidades y
   los kilos— y los ingresa al sistema.

2. **Informes.** Emite los informes del programa Mercado y elabora informes de
   estadísticas de ventas.

3. **Carga las entradas de las compras** de los productos.

4. **Cartas comerciales.** Las hace cuando hay alguna promoción, cambio de
   precio u oferta. Las ofertas las decide la directora de la empresa junto con
   la parte comercial.
