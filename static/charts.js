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

  // ── Iconos ────────────────────────────────────────────────────────────────
  //
  // SVG inline, nunca emojis: es la regla del CRM. Y el delta lleva flecha
  // ademas de color porque un signo que solo se distingue por color no se
  // distingue.

  var _FLECHAS = {
    sube: '<path d="M8 3.5 L8 12.5 M8 3.5 L4.5 7 M8 3.5 L11.5 7"/>',
    baja: '<path d="M8 12.5 L8 3.5 M8 12.5 L4.5 9 M8 12.5 L11.5 9"/>',
    igual: '<path d="M3.5 8 L12.5 8"/>'
  };

  function _icono(signo) {
    if (!_FLECHAS[signo]) return '';
    return '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" ' +
           'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" ' +
           'stroke-linejoin="round" aria-hidden="true">' +
           _FLECHAS[signo] + '</svg>';
  }

  // ── Tiles ─────────────────────────────────────────────────────────────────
  //
  // Sin grafico: son numeros. La guia de visualizacion es explicita en que a
  // veces la respuesta correcta no es un grafico.
  //
  // `mejor` dice para que lado es bueno. No se puede adivinar del signo: en
  // `cpl`, `costo_demo` y `costo_presupuesto` subir es una mala noticia, y en
  // `leads` es una buena. Sin ese dato, el color mentiria la mitad de las veces.

  SC.tiles = function (metricas, tema) {
    if (!metricas || !metricas.length) {
      return '<div class="sc-vacio">Sin datos en el período</div>';
    }
    var tinta = SC.PALETA.tinta[tema];
    var mudo = SC.PALETA.mudo[tema];

    return '<div class="sc-tiles">' + metricas.map(function (m) {
      var d = SC.fmtDelta(m.delta_periodo_anterior, m.formato);
      var animo = 'neutro';
      if (d.signo === 'sube') animo = m.mejor === 'bajo' ? 'malo' : 'bueno';
      if (d.signo === 'baja') animo = m.mejor === 'bajo' ? 'bueno' : 'malo';
      if (!m.mejor || d.signo === 'igual' || d.signo === 'sin_comparacion') {
        animo = 'neutro';
      }

      var pie = d.signo === 'sin_comparacion'
        ? '<span class="sc-tile-delta" data-signo="sin_comparacion" ' +
          'data-animo="neutro" style="color:' + mudo + '">' +
          SC.esc(d.texto) + '</span>'
        : '<span class="sc-tile-delta" data-signo="' + d.signo + '" ' +
          'data-animo="' + animo + '">' + _icono(d.signo) + ' ' +
          SC.esc(d.texto) + '</span>';

      return '<div class="sc-tile" data-id="' + SC.esc(m.id) + '">' +
             '<div class="sc-tile-label" style="color:' + mudo + '">' +
             SC.esc(m.etiqueta) + '</div>' +
             '<div class="sc-tile-valor" style="color:' + tinta + '">' +
             SC.esc(SC.fmt(m.valor, m.formato)) + '</div>' + pie + '</div>';
    }).join('') + '</div>';
  };

  // ── Embudo ────────────────────────────────────────────────────────────────
  //
  // El grafico que justifica el modulo. Las etapas de `meta_insights` las tiene
  // cualquier reporte de ads y ahi se termina; las de `crm` son lo que solo
  // tenemos nosotros, porque sabemos que paso despues del clic. El corte entre
  // unas y otras se dibuja.

  var _ALTO_FILA = 34;
  var _AIRE_CORTE = 16;   // el rotulo del corte necesita su propia franja
  var _GAP = 2;               // el spacer de 2px que pide la guia
  var _ANCHO_ETIQUETA = 210;
  var _ANCHO_VALOR = 130;

  SC.embudo = function (etapas, tema) {
    if (!etapas || !etapas.length) {
      return '<div class="sc-vacio">Sin datos para el embudo</div>';
    }
    var tinta = SC.PALETA.tinta[tema];
    var mudo = SC.PALETA.mudo[tema];
    var azul = SC.PALETA[tema][0];
    var gris = SC.PALETA.neutro[tema];

    // Cada bloque —lo que reporta Meta y lo que sabe el CRM— se escala contra
    // su propio maximo. Con una sola escala lineal, 1.032.881 impresiones
    // contra 238 leads aplastan las seis etapas del CRM a una linea de 2px: el
    // grafico que justifica el modulo quedaba ilegible.
    //
    // El costo de esto es que el largo NO se puede comparar cruzando la linea
    // punteada. Por eso el numero absoluto va escrito al lado de cada barra y
    // el porcentaje de caida se calcula siempre contra la etapa anterior real,
    // no contra el maximo del bloque.
    function _maxDe(fuente) {
      var vs = etapas.filter(function (e) {
        return e.fuente === fuente && e.valor !== null && e.valor !== undefined;
      }).map(function (e) { return e.valor; });
      return vs.length ? Math.max.apply(null, vs) : 0;
    }
    var maxPorFuente = { meta_insights: _maxDe('meta_insights'), crm: _maxDe('crm') };

    var anchoBarra = 640;
    var ancho = _ANCHO_ETIQUETA + anchoBarra + _ANCHO_VALOR;
    var alto = etapas.length * (_ALTO_FILA + _GAP) + 24;

    var piezas = [], corteDibujado = false, anterior = null;

    // Donde cae el corte, para reservarle su franja y que nada se monte.
    var iCorte = -1;
    etapas.forEach(function (e, i) {
      if (iCorte < 0 && i > 0 && etapas[i - 1].fuente === 'meta_insights'
          && e.fuente !== 'meta_insights') iCorte = i;
    });
    alto += iCorte >= 0 ? _AIRE_CORTE : 0;

    etapas.forEach(function (e, i) {
      var y = i * (_ALTO_FILA + _GAP) + 12 +
              (iCorte >= 0 && i >= iCorte ? _AIRE_CORTE : 0);
      var esMeta = e.fuente === 'meta_insights';
      var color = esMeta ? azul : SC.PALETA[tema][2];

      // El corte: hasta aca llega cualquier reporte de ads.
      if (!corteDibujado && anterior && anterior.fuente === 'meta_insights'
          && !esMeta) {
        piezas.push(
          '<line x1="0" y1="' + (y - _GAP - 3) + '" x2="' + ancho +
          '" y2="' + (y - _GAP - 3) + '" stroke="' + gris +
          '" stroke-width="1" stroke-dasharray="3 3"/>' +
          '<text x="' + _ANCHO_ETIQUETA + '" y="' + (y - _GAP - 7) +
          '" font-size="10" fill="' + mudo + '">' +
          'hasta acá llega un reporte de ads · abajo, otra escala</text>');
        corteDibujado = true;
      }

      piezas.push('<text x="0" y="' + (y + 21) + '" font-size="12" fill="' +
                  tinta + '">' + SC.esc(e.etiqueta) + '</text>');

      if (e.valor === null || e.valor === undefined) {
        // Sin credenciales de Insights no hay impresiones. Eso no es cero
        // impresiones: es que no lo sabemos, y son cosas distintas.
        piezas.push('<text x="' + (_ANCHO_ETIQUETA + 4) + '" y="' + (y + 21) +
                    '" font-size="11" fill="' + mudo + '">sin datos</text>');
      } else {
        var maxBloque = maxPorFuente[e.fuente] || 0;
        var w = maxBloque > 0 ? Math.max(3, e.valor / maxBloque * anchoBarra) : 3;
        piezas.push(
          '<rect data-fuente="' + SC.esc(e.fuente) + '" data-clave="' +
          SC.esc(e.clave) + '" x="' + _ANCHO_ETIQUETA + '" y="' + y +
          '" width="' + w.toFixed(1) + '" height="' + _ALTO_FILA +
          '" rx="4" fill="' + color + '"><title>' + SC.esc(e.etiqueta) +
          ': ' + SC.esc(SC.fmt(e.valor, 'numero')) + '</title></rect>' +
          '<text x="' + (_ANCHO_ETIQUETA + anchoBarra + 8) + '" y="' +
          (y + 21) + '" font-size="12" fill="' + tinta + '">' +
          SC.esc(SC.fmt(e.valor, 'numero')) + '</text>');

        // La caida contra la etapa anterior que si tenia valor.
        if (anterior && anterior.valor) {
          var pct = e.valor / anterior.valor;
          piezas.push(
            '<text x="' + (_ANCHO_ETIQUETA - 8) + '" y="' + (y + 6) +
            '" text-anchor="end" font-size="10" fill="' + mudo + '">' +
            SC.esc(SC.fmt(pct, 'porcentaje')) + '</text>');
        }
      }

      if (e.valor !== null && e.valor !== undefined) anterior = e;
      else if (!anterior) anterior = null;
    });

    return '<svg class="sc-embudo" viewBox="0 0 ' + ancho + ' ' + alto +
           '" style="width:100%;height:auto" role="img" ' +
           'aria-label="Embudo de campañas de Meta">' +
           piezas.join('') + '</svg>';
  };

  // ── Series temporales ─────────────────────────────────────────────────────
  //
  // Un solo eje Y, siempre. Superponer dos escalas deja elegir donde se cruzan
  // las lineas, o sea que se puede fabricar cualquier correlacion moviendo un
  // eje. Dos medidas de escalas distintas van como dos paneles apilados que
  // comparten el eje de tiempo: se lee igual de rapido y no engana.

  var _M = { arriba: 26, derecha: 12, abajo: 26, izquierda: 52 };

  SC.serie = function (puntos, opciones, tema) {
    opciones = opciones || {};
    if (!puntos || !puntos.length) {
      return '<div class="sc-vacio">Sin datos para «' +
             SC.esc(opciones.etiqueta || '') + '»</div>';
    }
    var tinta = SC.PALETA.tinta[tema];
    var mudo = SC.PALETA.mudo[tema];
    var grilla = SC.PALETA.grilla[tema];
    var color = opciones.color || SC.PALETA[tema][0];
    var fondo = SC.PALETA.fondo[tema];

    var ancho = opciones.ancho || 980;
    var alto = opciones.alto || 190;
    var x0 = _M.izquierda, x1 = ancho - _M.derecha;
    var y0 = _M.arriba, y1 = alto - _M.abajo;

    var valores = puntos.filter(function (p) {
      return p.y !== null && p.y !== undefined;
    }).map(function (p) { return p.y; });

    var max = valores.length ? Math.max.apply(null, valores) : 0;
    var cortes = SC.ticks(0, max || 1, 4);
    var ey = SC.escalaLineal([0, cortes[cortes.length - 1]], [y1, y0]);
    var paso = puntos.length > 1 ? (x1 - x0) / (puntos.length - 1) : 0;
    var ex = function (i) { return puntos.length > 1 ? x0 + i * paso : (x0 + x1) / 2; };

    var piezas = [];

    // Grilla y eje Y: recesivos. Un solo eje, y el test lo verifica.
    piezas.push('<g class="sc-eje-y">' + cortes.map(function (t) {
      var y = ey(t);
      return '<line x1="' + x0 + '" y1="' + y.toFixed(1) + '" x2="' + x1 +
             '" y2="' + y.toFixed(1) + '" stroke="' + grilla +
             '" stroke-width="1"/>' +
             '<text x="' + (x0 - 8) + '" y="' + (y + 4).toFixed(1) +
             '" text-anchor="end" font-size="10" fill="' + mudo + '">' +
             SC.esc(SC.fmt(t, opciones.formato)) + '</text>';
    }).join('') + '</g>');

    // Eje X: no todas las etiquetas, o se amontonan.
    var cada = Math.max(1, Math.ceil(puntos.length / 8));
    piezas.push('<g class="sc-eje-x">' + puntos.map(function (p, i) {
      if (i % cada) return '';
      return '<text x="' + ex(i).toFixed(1) + '" y="' + (alto - 8) +
             '" text-anchor="middle" font-size="10" fill="' + mudo + '">' +
             SC.esc(p.x) + '</text>';
    }).join('') + '</g>');

    // La linea, cortada en cada hueco. Unir por arriba de un null inventaria
    // un dato que no hay.
    var tramo = [];
    function cerrar() {
      if (tramo.length > 1) {
        piezas.push('<path class="sc-linea" d="M' + tramo.join(' L') +
                    '" fill="none" stroke="' + color +
                    '" stroke-width="2" stroke-linecap="round" ' +
                    'stroke-linejoin="round"/>');
      } else if (tramo.length === 1) {
        // Un punto suelto entre dos huecos: igual se ve.
        piezas.push('<path class="sc-linea" d="M' + tramo[0] + ' L' +
                    tramo[0] + '" fill="none" stroke="' + color +
                    '" stroke-width="2" stroke-linecap="round"/>');
      }
      tramo = [];
    }
    puntos.forEach(function (p, i) {
      if (p.y === null || p.y === undefined) { cerrar(); return; }
      tramo.push(ex(i).toFixed(1) + ',' + ey(p.y).toFixed(1));
    });
    cerrar();

    // Marcadores y franja de captura: el area sensible es toda la columna, no
    // el punto, porque un marcador de 8px es imposible de apuntar.
    puntos.forEach(function (p, i) {
      if (p.y === null || p.y === undefined) return;
      var cx = ex(i), cy = ey(p.y);
      piezas.push('<circle cx="' + cx.toFixed(1) + '" cy="' + cy.toFixed(1) +
                  '" r="4" fill="' + color + '" stroke="' + fondo +
                  '" stroke-width="2"/>');
    });
    puntos.forEach(function (p, i) {
      var cx = ex(i);
      var w = paso || (x1 - x0);
      piezas.push('<rect class="sc-hit" x="' + (cx - w / 2).toFixed(1) +
                  '" y="' + y0 + '" width="' + w.toFixed(1) + '" height="' +
                  (y1 - y0) + '" fill="transparent"><title>' + SC.esc(p.x) +
                  ' · ' + SC.esc(SC.fmt(p.y, opciones.formato)) +
                  '</title></rect>');
    });

    return '<div class="sc-panel-serie">' +
           '<div class="sc-titulo" style="color:' + tinta + '">' +
           SC.esc(opciones.etiqueta || '') + '</div>' +
           '<svg viewBox="0 0 ' + ancho + ' ' + alto +
           '" style="width:100%;height:auto" role="img" aria-label="' +
           SC.esc(opciones.etiqueta || '') + '">' + piezas.join('') +
           '</svg></div>';
  };

  SC.parApilado = function (a, b, tema) {
    return '<div class="sc-par">' +
           SC.serie(a.puntos, a, tema) +
           SC.serie(b.puntos, b, tema) + '</div>';
  };

  // ── Barras con intervalo de confianza ─────────────────────────────────────
  //
  // El grafico que impide decidir sobre ruido. El `n` va SIEMPRE como etiqueta
  // directa, no solo cuando la muestra es chica: es lo que deja comparar dos
  // barras sin que el largo mienta.

  SC.barrasConIC = function (filas, opciones, tema) {
    opciones = opciones || {};
    if (!filas || !filas.length) {
      return '<div class="sc-vacio">Sin datos</div>';
    }
    var tinta = SC.PALETA.tinta[tema];
    var mudo = SC.PALETA.mudo[tema];
    var fondo = SC.PALETA.fondo[tema];

    var conValor = filas.filter(function (f) {
      return f.metrica && f.metrica.valor !== null &&
             f.metrica.valor !== undefined;
    });
    var max = conValor.length ? Math.max.apply(null, conValor.map(function (f) {
      // El tope contempla el extremo del intervalo, no solo el valor: si no,
      // el bigote se sale del area.
      var ic = f.metrica.ic95 || [];
      return Math.max(f.metrica.valor, ic[1] || 0);
    })) : 1;

    var anchoEtiqueta = opciones.anchoEtiqueta || 250;
    var anchoBarra = 520;
    // El ancho de la derecha tiene que dar para el valor MAS la nota
    // 'n=5 · muestra chica', que antes se cortaba contra el borde del viewBox.
    var anchoValor = 260;
    var ancho = anchoEtiqueta + anchoBarra + anchoValor;
    var altoFila = 30, gap = 2;
    var alto = filas.length * (altoFila + gap) + 8;
    var ex = SC.escalaLineal([0, max], [0, anchoBarra]);

    var piezas = filas.map(function (f, i) {
      var y = i * (altoFila + gap) + 4;
      var m = f.metrica || {};
      var color = f.campana
        ? SC.colorDeCampana(f.campana, i, tema)
        : (SC.PALETA[tema][0]);

      // El tope sale del ancho de la columna de etiquetas a font-size 12:
      // ~7px por caracter.
      var etiqueta = SC.recortar(f.etiqueta, Math.floor(anchoEtiqueta / 7));
      var out = '<text x="0" y="' + (y + 19) + '" font-size="12" fill="' +
                tinta + '">' + SC.esc(etiqueta) + '</text>' +
                (etiqueta !== f.etiqueta
                  ? '<title>' + SC.esc(f.etiqueta) + '</title>' : '');

      if (m.valor === null || m.valor === undefined) {
        out += '<text x="' + (anchoEtiqueta + 4) + '" y="' + (y + 19) +
               '" font-size="11" fill="' + mudo + '">sin datos</text>';
        return out;
      }

      var w = Math.max(2, ex(m.valor));
      out += '<rect x="' + anchoEtiqueta + '" y="' + (y + 6) + '" width="' +
             w.toFixed(1) + '" height="' + (altoFila - 12) +
             '" rx="4" fill="' + color + '"><title>' + SC.esc(f.etiqueta) +
             ': ' + SC.esc(SC.fmt(m.valor, m.formato)) + '</title></rect>';

      // El intervalo, con anillo del color de la superficie para que se lea
      // por encima de la barra.
      if (m.ic95 && m.ic95.length === 2) {
        var xa = anchoEtiqueta + ex(m.ic95[0]);
        var xb = anchoEtiqueta + ex(m.ic95[1]);
        var cy = y + altoFila / 2;
        out += '<g class="sc-ic" stroke="' + tinta + '" stroke-width="1.5" ' +
               'opacity="0.75">' +
               '<line x1="' + xa.toFixed(1) + '" y1="' + cy + '" x2="' +
               xb.toFixed(1) + '" y2="' + cy + '" stroke="' + fondo +
               '" stroke-width="4"/>' +
               '<line x1="' + xa.toFixed(1) + '" y1="' + cy + '" x2="' +
               xb.toFixed(1) + '" y2="' + cy + '"/>' +
               '<line x1="' + xa.toFixed(1) + '" y1="' + (cy - 4) + '" x2="' +
               xa.toFixed(1) + '" y2="' + (cy + 4) + '"/>' +
               '<line x1="' + xb.toFixed(1) + '" y1="' + (cy - 4) + '" x2="' +
               xb.toFixed(1) + '" y2="' + (cy + 4) + '"/></g>';
      }

      // Un costo no tiene `n`: su denominador es plata, no una muestra. Poner
      // "n=?" ahi no informa nada y ensucia toda la columna.
      var nota = (m.n === null || m.n === undefined) ? '' : 'n=' + m.n;
      if (m.muestra_chica && m.n) nota += ' · muestra chica';
      out += '<text x="' + (anchoEtiqueta + anchoBarra + 8) + '" y="' +
             (y + 19) + '" font-size="12" fill="' + tinta + '">' +
             SC.esc(SC.fmt(m.valor, m.formato)) + '</text>' +
             (nota ? '<text x="' + (anchoEtiqueta + anchoBarra + 88) + '" y="' +
             (y + 19) + '" font-size="10" fill="' + mudo + '">' +
             SC.esc(nota) + '</text>' : '');
      return out;
    }).join('');

    return '<svg class="sc-barras" viewBox="0 0 ' + ancho + ' ' + alto +
           '" style="width:100%;height:auto" role="img" aria-label="' +
           SC.esc(opciones.etiqueta || 'Comparación') + '">' + piezas +
           '</svg>';
  };

  // Las respuestas de texto libre del formulario pueden ser larguisimas y en
  // SVG no hay `text-overflow`: una etiqueta de 86 caracteres se sale del area
  // y se monta sobre la barra. Se corta a mano, con elipsis de verdad (…).
  SC.recortar = function (texto, tope) {
    var t = String(texto === null || texto === undefined ? '' : texto);
    return t.length <= tope ? t : t.slice(0, tope - 1).trimEnd() + '…';
  };

  SC.esc = function (t) {
    return String(t === null || t === undefined ? '' : t)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  };

}(typeof globalThis !== 'undefined' ? globalThis : this));
