# Reuniones del CRM en Google Calendar, con invitaciones

Pedido de Juan (15/9): que el CRM vuelva a crear las reuniones en Google
Calendar y que Google mande las invitaciones solo.

## Qué hace

- **Crear** una reunión (con cliente u "otro asunto") la guarda en el CRM y
  crea el evento en el Google Calendar de la cuenta conectada, con
  `sendUpdates="all"`: Google les manda la invitación por mail a los invitados
  y, si es con un cliente que tiene mail cargado, también al cliente.
  - Hora de Montevideo (`timeZone: America/Montevideo`).
  - Link de Meet, como antes del 1/6, salvo que la reunión ya traiga un link.
  - Si se repite: **un solo evento recurrente** con RRULE
    (`RRULE:FREQ=WEEKLY;BYDAY=FR`, `INTERVAL=2`, `UNTIL`/`COUNT`). Es una sola
    invitación para toda la serie.
- **Editar**: "Todas" cambia el evento recurrente; "Solo esta" cambia esa
  instancia; "Esta y las siguientes" le pone fin a la serie (`UNTIL`) y crea una
  serie nueva desde esa fecha. Google les avisa a los invitados.
- **Borrar**: "Todas" borra el evento; "Solo esta" cancela la instancia; "Esta
  y las siguientes" le pone fin a la serie. Google les avisa a los invitados.
  Si Google no responde, **no se borra nada** y la pantalla lo dice: borrar
  solo en el CRM dejaría el evento vivo en Google.
- **Si Google falla al crear o editar**, la reunión queda en el CRM igual,
  marcada "No sincronizada con Google" (⚠), con un aviso arriba del calendario
  y un botón **Reintentar en Google**. No se reintenta solo.
- **Calendly** no se toca: esas reuniones ya viven en Google.
- **El import de Google** (lo que trae a Calendly y a las reuniones creadas a
  mano en Google) saltea los eventos que creó el CRM y sus instancias, por id.
  No los duplica ni les inventa un lead.

## Qué le llega a cada invitado

Un mail de invitación de Google Calendar enviado desde la cuenta conectada
(la de Scalerics), con título, fecha y hora (en la zona horaria del invitado),
descripción, el link de Meet y los botones Sí / No / Quizás. Si la reunión se
repite, la invitación es una sola y dice cada cuánto ("Semanalmente los
viernes"). Cada cambio o cancelación le llega como otro mail de Google.

## Credenciales y permisos, hoy

- **Tipo:** OAuth de una cuenta de Google (no es un service account). Por eso
  Google sí puede mandar invitaciones a gente de afuera: un service account
  sin delegación de dominio no podría, pero no es el caso.
- **Proyecto de Google Cloud:** `scrao-495517` (según `setup_calendar.py`).
- **Calendario:** `primary` de la cuenta que autorizó.
- **Scopes que pide `setup_calendar.py`:** `https://www.googleapis.com/auth/calendar`
  (lectura y escritura) y `https://www.googleapis.com/auth/drive.readonly`.
- **Secrets en Fly (solo nombres):** `GCAL_CLIENT_ID`, `GCAL_CLIENT_SECRET`,
  `GCAL_REFRESH_TOKEN`, `GCAL_TOKEN_RENEWED_AT`.
- **Estado de la app de OAuth:** "En producción" (`routes/tokens.py`). Con la
  app "En prueba" el refresh token vence a los 7 días; eso fue lo que el 1/6
  hizo sacar la creación en Google, porque el calendario entero se caía.

No se verificó contra Google desde esta rama (las pruebas no llaman a Google).
Mover una reunión importada de Google desde el CRM ya escribe en Google desde
el 8/9: **si eso anda en producción, el permiso de escritura ya está** y no hay
que hacer nada.

## Cómo comprobar y, si falta, habilitar la escritura

1. Entrar a <https://myaccount.google.com/permissions> con la **cuenta de
   Google de Scalerics que está conectada al CRM**.
2. Buscar la app del proyecto `scrao-495517`. Si dice que puede "ver, editar,
   compartir y borrar permanentemente todos los calendarios", el permiso está.
   Si solo dice "ver", falta.
3. Si falta:
   1. En <https://console.cloud.google.com/>, proyecto `scrao-495517` →
      **APIs y servicios → Biblioteca**: que **Google Calendar API** esté
      habilitada.
   2. **APIs y servicios → Pantalla de consentimiento de OAuth → Permisos
      (scopes)**: agregar `https://www.googleapis.com/auth/calendar` (alcanza
      también `.../auth/calendar.events`). Confirmar que el estado de
      publicación sea **En producción**.
   3. En una computadora con el `credentials.json` del proyecto, correr
      `python setup_calendar.py` y autorizar **con la cuenta de Scalerics**.
      Deja `GCAL_CLIENT_ID`, `GCAL_CLIENT_SECRET`, `GCAL_REFRESH_TOKEN` y
      `GCAL_TOKEN_RENEWED_AT` en el `.env`.
   4. Cargar esos cuatro valores como secrets de la app `scalerics-crm` en Fly
      (el script lo intenta solo; si falla, lo hace el coordinador a mano con
      `fly secrets set`). La máquina se reinicia sola.
4. Mientras falte el permiso, crear una reunión igual funciona: queda en el CRM
   con el aviso "No se pudo crear en Google: falta permiso de escritura en
   Google Calendar". Después de habilitarlo, **Reintentar en Google** la sube.

## Interruptor: `GCAL_CREAR_EVENTOS`

- `on` (o sin definir): el CRM crea los eventos nuevos en Google, **si hay
  credenciales** `GCAL_*` cargadas. Sin credenciales no intenta nada.
- `off` (también `0`, `false`, `no`): las reuniones nuevas quedan solo en el
  CRM, sin invitaciones. Las que ya están en Google se siguen actualizando y
  borrando en Google, para no dejar invitados con datos viejos.
- Se cambia sin tocar código: `fly secrets set GCAL_CREAR_EVENTOS=off --app
  scalerics-crm` (reinicia la máquina).

## Límites conocidos

- Google limita cuántas invitaciones puede mandar una cuenta por día; para el
  volumen del CRM no debería notarse.
- Si una serie se cambia con "Todas" después de haber movido o borrado alguna
  instancia suelta, Google puede descartar esas excepciones.
- No se crea el bot de Recall en las reuniones nuevas (antes del 1/6 sí).
