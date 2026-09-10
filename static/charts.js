/* Graficos del panel de Marketing.
 *
 * SVG a mano, sin librerias. Se eligio asi sobre Chart.js o ECharts por tres
 * motivos: el CRM no tiene ninguna dependencia de frontend y no se le queria
 * sumar una; los dos temas y la paleta de Scalerics hay que respetarlos
 * exactamente y a las librerias hay que pelearles los defaults; y el embudo de
 * nueve pasos, que es el grafico que justifica el modulo, igual habria que
 * dibujarlo a mano.
 *
 * Las funciones de dibujo son puras: reciben datos, devuelven un string.
 * Ninguna calcula nada — todo numero sale del dossier, que ya trae numerador,
 * denominador, n e intervalo de confianza.
 */

(function (raiz) {
  'use strict';

  var SC = raiz.SC || (raiz.SC = {});

  // ── Paleta ────────────────────────────────────────────────────────────────
  //
  // Validada con el script de la skill de dataviz contra las dos superficies
  // reales del CRM: #f8fafc en claro y #111827 en oscuro. No cambiar un color
  // sin volver a correrlo.
  //
  // Dos resultados de esa validacion que no son negociables:
  //
  //   1. El indigo lleva un paso distinto por tema. #4F46E5 sobre #111827 da
  //      2,82:1 de contraste, por debajo del piso de 3:1.
  //   2. Queda un aviso de daltonismo entre el azul y el violeta (delta-E 6,7
  //      deutan). Eso es legal SOLO con codificacion secundaria: por eso las
  //      etiquetas directas y la leyenda no son opcionales en este panel.
  SC.PALETA = {
    claro:  ['#0088CC', '#A855F7', '#0D9488', '#EA580C', '#4F46E5'],
    oscuro: ['#0088CC', '#A855F7', '#0D9488', '#EA580C', '#6366F1'],
    neutro: { claro: '#94A3B8', oscuro: '#64748B' },
    // Texto, ejes y grilla: recesivos a proposito. El color lo lleva la marca.
    tinta:  { claro: '#0f172a', oscuro: '#e2e8f0' },
    mudo:   { claro: '#64748b', oscuro: '#64748b' },
    grilla: { claro: '#e2e8f0', oscuro: '#1e293b' },
    fondo:  { claro: '#ffffff', oscuro: '#111827' },
    // Estado, reservados: nunca se usan como "serie 4".
    bien:   { claro: '#15803d', oscuro: '#22c55e' },
    mal:    { claro: '#b91c1c', oscuro: '#f87171' }
  };

  SC.SIN_CAMPANA = '(sin campaña)';
  SC.OTROS = 'otros';

  // El color sigue a la campana, no a su posicion: filtrar no puede repintar a
  // las que quedan. El orden de primera aparicion decide el slot y se recuerda.
  var _asignados = {};

  SC._resetColores = function () { _asignados = {}; };

  SC.colorDeCampana = function (nombre, indice, tema) {
    var hues = SC.PALETA[tema] || SC.PALETA.oscuro;
    var gris = SC.PALETA.neutro[tema] || SC.PALETA.neutro.oscuro;

    if (nombre === SC.SIN_CAMPANA || nombre === SC.OTROS) return gris;

    if (!(nombre in _asignados)) {
      var usados = 0;
      for (var k in _asignados) {
        if (_asignados[k] >= 0) usados++;
      }
      // Una septima campana no genera un hue nuevo: cae en el gris.
      _asignados[nombre] = usados < hues.length ? usados : -1;
    }
    var slot = _asignados[nombre];
    return slot < 0 ? gris : hues[slot];
  };

  // ── Escalas y ejes ────────────────────────────────────────────────────────

  SC.escalaLineal = function (dominio, rango) {
    var d0 = dominio[0], d1 = dominio[1], r0 = rango[0], r1 = rango[1];
    var ancho = d1 - d0;
    return function (v) {
      if (!ancho) return r0;          // dominio degenerado: no dividir por cero
      return r0 + (v - d0) / ancho * (r1 - r0);
    };
  };

  SC.ticks = function (min, max, cantidad) {
    if (!(max > min)) return [min];
    var crudo = (max - min) / (cantidad || 5);
    var mag = Math.pow(10, Math.floor(Math.log10(crudo)));
    var norm = crudo / mag;
    var paso = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
    // El eje tiene que CUBRIR el dato, no quedarse corto: con ticks(0, 97) y
    // paso 20, cortar en 80 dejaria una barra de 97 saliendose del area.
    var salida = [], v = Math.floor(min / paso) * paso;
    var tope = Math.ceil(max / paso) * paso + paso * 0.001;
    while (v <= tope && salida.length < 100) {
      salida.push(Math.round(v * 1e6) / 1e6);
      v += paso;
    }
    return salida;
  };

  // ── Formato ───────────────────────────────────────────────────────────────
  //
  // es-UY: miles con punto, decimal con coma. A mano y no con toLocaleString
  // porque el resultado tiene que ser identico corriendo en node (los tests) y
  // en el navegador, y toLocaleString depende del ICU que tenga cada uno.

  function _miles(entero) {
    return String(entero).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  }

  SC.fmt = function (valor, formato) {
    if (valor === null || valor === undefined || (typeof valor === 'number' && !isFinite(valor))) {
      return 'sin datos';
    }
    if (formato === 'porcentaje') {
      return (valor * 100).toFixed(1).replace('.', ',') + '%';
    }
    if (formato === 'moneda') {
      var fijo = Math.abs(valor).toFixed(2);
      var partes = fijo.split('.');
      // Primero los miles sobre la parte entera, despues la coma decimal. Al
      // reves, el separador de miles pisaria la coma.
      var texto = _miles(partes[0]) + ',' + partes[1];
      return (valor < 0 ? '−' : '') + texto;
    }
    var redondo = Math.round(valor);
    return (redondo < 0 ? '−' : '') + _miles(Math.abs(redondo));
  };

  SC.fmtDelta = function (valor, formato) {
    if (valor === null || valor === undefined) {
      return { texto: 'sin período anterior', signo: 'sin_comparacion' };
    }
    if (!valor) return { texto: SC.fmt(0, formato), signo: 'igual' };
    return {
      texto: SC.fmt(Math.abs(valor), formato),
      signo: valor > 0 ? 'sube' : 'baja'
    };
  };

  SC.esc = function (t) {
    return String(t === null || t === undefined ? '' : t)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  };

}(typeof globalThis !== 'undefined' ? globalThis : this));
