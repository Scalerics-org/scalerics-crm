/**
 * Manda los colores del semáforo de la planilla al CRM.
 *
 * Vive pegado a `Scalerics - Leads - 2026` (Extensiones → Apps Script) y corre
 * solo cada 30 minutos. El CRM no puede tirar de esto por su cuenta: leer el
 * color de fondo de una celda exige la API de Sheets y una credencial nueva de
 * Google, mientras que acá adentro `getBackgrounds()` lo da gratis y el script
 * corre como el dueño de la planilla.
 *
 * POR QUÉ CONSULTA EN VEZ DE REACCIONAR
 *   Lo natural sería que pintar una fila dispare el sync al instante, con un
 *   trigger `onEdit`. No se puede: Google documenta que `onEdit` corre «cuando
 *   un usuario cambia el VALOR de una celda», y el color de fondo no es un
 *   valor. Pintar no dispara nada. Por eso se consulta cada tanto, y ese «cada
 *   tanto» se hizo corto.
 *
 * Del otro lado atiende POST /api/meta/sync-planilla, que traduce color a
 * estado, no retrocede sobre lo que el CRM ya sabe y es idempotente. La lógica
 * y sus reglas están en services/planilla_semaforo.py.
 *
 * INSTALACIÓN (una vez)
 *   1. Planilla → Extensiones → Apps Script, y pegar este archivo.
 *   2. Configuración del proyecto → Propiedades del script → agregar
 *      ADMIN_TOKEN con el mismo valor que tiene el secret en Fly.
 *   3. Ejecutar `probar()` una vez: pide permisos, no escribe nada en el CRM y
 *      deja en el log lo que haría. Si se ve bien, ejecutar `instalarTrigger()`.
 */

var CRM = 'https://scalerics-crm.fly.dev';

// Cada cuánto se consulta la planilla. Apps Script admite 1, 5, 10, 15 o 30
// minutos, o multiplos de una hora. A 30 minutos son 48 corridas por dia, que
// contra la cuota diaria de Apps Script no es nada: cada corrida lee unas pocas
// pestañas y postea una vez. El endpoint del CRM es idempotente, asi que
// consultar de mas no escribe de mas.
var CADA_MINUTOS = 30;

// Las pestañas que no son meses. Todo lo demás se lee, así que un mes nuevo
// entra solo sin tocar el script.
var NO_SON_MESES = ['Analisis', 'Análisis', 'Semaforo', 'Semáforo', 'Cuenta Corriente'];

// Columnas de la planilla (1 = A).
var COL_TELEFONO = 2;   // B
var COL_MAIL = 3;       // C
var COL_COLOR_HASTA = 4; // se busca el color de la fila entre A y D

var BLANCO = ['#ffffff', 'white', ''];


/** Junta las filas de todas las pestañas de mes. */
function leerFilas() {
  var libro = SpreadsheetApp.getActiveSpreadsheet();
  var filas = [];

  libro.getSheets().forEach(function (hoja) {
    var nombre = hoja.getName().trim();
    if (NO_SON_MESES.indexOf(nombre) !== -1) return;

    var ultima = hoja.getLastRow();
    if (ultima < 2) return;

    // Una sola lectura por hoja: pedir celda por celda hace que el script tarde
    // minutos y Apps Script lo corta a los 6.
    var ancho = Math.max(COL_MAIL, COL_COLOR_HASTA);
    var valores = hoja.getRange(2, 1, ultima - 1, ancho).getValues();
    var colores = hoja.getRange(2, 1, ultima - 1, ancho).getBackgrounds();

    for (var i = 0; i < valores.length; i++) {
      var tel = String(valores[i][COL_TELEFONO - 1] || '').trim();
      var mail = String(valores[i][COL_MAIL - 1] || '').trim();
      if (!tel && !mail) continue;

      // El color de la fila es el primero que no sea blanco entre A y D: la
      // planilla no siempre tiene pintada la columna A.
      var color = '';
      for (var c = 0; c < COL_COLOR_HASTA; c++) {
        var actual = String(colores[i][c] || '').toLowerCase();
        if (BLANCO.indexOf(actual) === -1) { color = actual; break; }
      }
      if (!color) continue;  // fila sin marcar: no dice nada, no se manda

      filas.push({ tel: tel, mail: mail, color: color, mes: nombre });
    }
  });

  return filas;
}


function _postear(filas, dry) {
  var token = PropertiesService.getScriptProperties().getProperty('ADMIN_TOKEN');
  if (!token) throw new Error('Falta ADMIN_TOKEN en las propiedades del script');

  var res = UrlFetchApp.fetch(CRM + '/api/meta/sync-planilla' + (dry ? '?dry=1' : ''), {
    method: 'post',
    contentType: 'application/json',
    headers: { 'x-admin-token': token },
    payload: JSON.stringify({ filas: filas }),
    muteHttpExceptions: true,
  });

  var codigo = res.getResponseCode();
  var cuerpo = res.getContentText();
  if (codigo !== 200) {
    // Que falle ruidoso: si el token vence o el CRM está caído, el silencio
    // haría que la planilla y el CRM se separen de nuevo sin que nadie note.
    throw new Error('El CRM respondió ' + codigo + ': ' + cuerpo);
  }
  Logger.log(cuerpo);
  return JSON.parse(cuerpo);
}


/** Lo que corre el trigger. */
function sincronizar() {
  var filas = leerFilas();
  Logger.log('Filas marcadas: ' + filas.length);
  return _postear(filas, false);
}


/** Igual que sincronizar() pero sin escribir. Para probar antes de prender. */
function probar() {
  var filas = leerFilas();
  Logger.log('Filas marcadas: ' + filas.length);
  return _postear(filas, true);
}


/** Deja el sync corriendo. Borra el trigger anterior si existía. */
function instalarTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'sincronizar') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('sincronizar').timeBased()
    .everyMinutes(CADA_MINUTOS).create();
  Logger.log('Trigger instalado: sincronizar() cada ' + CADA_MINUTOS + ' minutos');
}
