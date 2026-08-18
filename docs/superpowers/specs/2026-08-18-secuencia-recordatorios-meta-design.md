# Secuencia de recordatorios a los leads de Meta

**Fecha:** 2026-08-18
**Repo:** `Scalerics-org/scalerics-crm`
**Estado:** diseño pendiente de aprobación

## Qué cambia

Hoy cada lead de Meta recibe **un** mail y nunca más. Eso pasa a ser una
secuencia de **hasta 7 mails a lo largo de un año**, y después el lead queda
cerrado para siempre.

Días desde el primer envío: **0, 10, 25, 115, 205, 295, 365**. Los tres primeros
son el seguimiento inmediato; del cuarto en adelante es uno por trimestre.

## La garantía que se reemplaza

Esta es la parte delicada del cambio y conviene decirla en voz alta.

Hoy "un mail por lead, para siempre" no lo garantiza el código: lo garantiza un
**`UNIQUE` sobre `business_id`** en `meta_reminders`. Si algo intentara mandar
dos veces, el `INSERT` falla antes de que salga el mail.

La secuencia necesita varias filas por lead, así que ese `UNIQUE` se va. En su
lugar va **`UNIQUE(business_id, numero)`**: la misma protección, ahora por
contacto. Sigue siendo la base la que impide el duplicado, no una condición en
el código.

**Nada se apoya en que el código se acuerde de filtrar.** Todas las guardas
viven en el `WHERE`, como hasta ahora.

## Cuándo se manda

Un lead entra en la tanda de hoy si:

- `source='meta'`, `crm_status='sin_contactar'`, tiene mail;
- **no** tiene ninguna fila con `unsubscribed_at` (la baja aplica a toda la
  secuencia, no al contacto que la disparó);
- y además:
  - **no recibió nada** y entró hace 3 días o más → le toca el contacto 1;
  - o **su último envío** fue hace tanto o más que el salto que corresponde al
    siguiente número, y todavía no llegó a 7.

El tope diario de **15 mails sobre 24 horas rodantes** no cambia, y es sobre el
total: los contactos nuevos y los de seguimiento compiten por el mismo cupo.

**Prioridad dentro de la tanda:** primero los seguimientos (2 a 7), después los
contactos nuevos. Un seguimiento tarde pierde sentido; un primer contacto puede
esperar un día sin costo.

## Qué dice cada mail

Los siete no pueden ser el mismo texto: repetir el mismo párrafo comercial cada
tres meses es exactamente lo que hace que alguien marque spam.

| # | Día | Tono |
|---|-----|------|
| 1 | 0 | El actual: qué hacemos para su rubro, invitación a agendar |
| 2 | 10 | Corto. "¿Lo viste? Sigue en pie." Sin repetir el discurso |
| 3 | 25 | Cierre suave: "por ahora lo dejamos acá, cuando quieras avisá" |
| 4-6 | 115, 205, 295 | Trimestral, corto, sin discurso de venta |
| 7 | 365 | **Explícitamente el último.** "No te escribimos más" |

El 7 tiene que cumplir lo que dice: después de ese, el lead no vuelve a entrar
nunca, aunque siga en `sin_contactar`.

Todos conservan el membrete, la firma, el link de baja y la cabecera
`List-Unsubscribe`.

## Lo que corta la secuencia

- **La baja.** Cualquier fila con `unsubscribed_at` saca al lead para siempre.
- **El cambio de estado.** Salir de `sin_contactar` lo saca.
- **Llegar a 7.**

**Riesgo aceptado, y es el principal:** no detectamos respuestas. Si alguien
contesta el mail y nadie lo mueve de `sin_contactar` en el CRM, va a seguir
recibiendo la secuencia después de haber contestado. Con un mail por lead eso
era imposible; con siete es el modo de falla más probable. Juan se compromete a
mover a los que contesten. La alternativa —leer `contacto@` por IMAP— es un
subsistema aparte y queda fuera de alcance.

## Migración

`meta_reminders` ya tiene **15 filas en producción** (la primera tanda, del
18-8-2026). SQLite no permite quitar un `UNIQUE` declarado en la tabla, así que
hay que reconstruirla: crear la nueva con `UNIQUE(business_id, numero)`, copiar
las filas existentes con `numero=1`, borrar la vieja y renombrar.

La migración corre dentro de una transacción y **antes** hay que bajarse el
backup de producción. Si se pierden esas 15 filas, esas 15 personas reciben de
nuevo el contacto 1.

## Verificación

1. Las 15 filas de producción sobreviven a la migración, con `numero=1` y su
   token intacto.
2. Un lead que recibió el contacto 1 hace 9 días no entra; a los 10, sí.
3. Correr el job dos veces seguidas no manda dos veces el mismo número.
4. Un lead dado de baja en el contacto 2 no recibe el 3 ni ningún trimestral.
5. Un lead que llegó a 7 no vuelve a entrar nunca.
6. El tope de 15 diarios cuenta juntos los nuevos y los seguimientos.
7. Los seguimientos salen antes que los contactos nuevos cuando compiten.
8. Cada número manda su texto, no el del contacto 1.

## Riesgos

| Riesgo | Mitigación |
|---|---|
| Escribirle a alguien que ya contestó | Mover el lead en el CRM al responder |
| Quemar la reputación de `scalerics.com` | Final a los 365 días, baja visible, textos distintos |
| Perder las 15 filas en la migración | Backup antes, transacción, y verificar el conteo después |
| Que el trimestral se lea como spam | Textos cortos, sin discurso de venta, y el último avisa que es el último |
