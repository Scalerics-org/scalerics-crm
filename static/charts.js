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
    // El fondo NO es decorativo: es la superficie contra la que se
    // validan los contrastes. Tiene que ser el mismo color que la
    // tarjeta que contiene al grafico (`--superficie`), o el panel se
    // ve con un recuadro mas oscuro adentro de cada bloque.
    // Revalidado el 11/9/2026 al pasar el panel a tokens: la paleta
    // oscura pasa las cinco pruebas contra #161b27 igual que contra el
    // #111827 anterior, los cinco tonos siguen arriba de 3:1.
    fondo:  { claro: '#ffffff', oscuro: '#161b27' },
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


  // Varias campanas en el mismo eje. `SC.serie` dibuja una sola linea, asi que
  // comparar campanas obligaba a mirar graficos separados y adivinar la escala.
  //
  // Un solo eje Y, siempre: dos medidas de escalas distintas van en dos
  // graficos, nunca en dos ejes. Acá todas las series son la MISMA medida
  // (costo por demo, o CPL) en campanas distintas, que es el caso donde
  // compartir eje es lo correcto.
  //
  // El color lo da `SC.colorDeCampana`, o sea que sigue a la campana y no a su
  // posicion: filtrar una campana no repinta a las que quedan.

  // Los colores del semaforo de la planilla, que es donde el equipo los pinta.
  // La idea es que la etapa se reconozca por el mismo color en los dos lados.
  //
  // NO son los hex crudos del Excel: `#ffff00` y `#00ff00` puros son ilegibles
  // sobre el panel oscuro y chillones sobre el claro. Se conserva el TONO —que
  // es lo que se reconoce— y se elige el paso que se lee en cada tema. Los seis
  // pasan 3:1 contra la superficie hundida, calculado, no estimado.
  //
  // Los dos verdes son a proposito: en la planilla "demo agendada" es verde
  // claro y "cerrado" verde oscuro, y quedan separados en el embudo por el cyan
  // y el magenta, asi que no se confunden.
  SC.COLOR_ETAPA = {
    leads:        { oscuro: '#64748b', claro: '#475569' },
    interesados:  { oscuro: '#eab308', claro: '#a16207' },
    agendadas:    { oscuro: '#22c55e', claro: '#15803d' },
    demos:        { oscuro: '#22d3ee', claro: '#0e7490' },
    presupuestos: { oscuro: '#e879f9', claro: '#a21caf' },
    cierres:      { oscuro: '#4ade80', claro: '#14532d' }
  };

  SC.colorDeEtapa = function (clave, tema) {
    var c = SC.COLOR_ETAPA[clave];
    return c ? (c[tema] || c.oscuro) : SC.PALETA.neutro[tema];
  };

  // Un embudo dibujado como un embudo: cada etapa mas angosta que la anterior,
  // y los lados en diagonal para que la bajada se VEA en vez de tener que
  // compararla leyendo numeros.
  //
  // Sin porcentajes adentro. El ancho ya dice la proporcion —para eso es un
  // embudo— y el numero absoluto esta al lado. Un porcentaje sobre cada tramo
  // convierte el dibujo en una tabla con forma rara.
  SC.embudoReal = function (etapas, opciones, tema) {
    opciones = opciones || {};
    var vivas = (etapas || []).filter(function (e) {
      return e.n !== null && e.n !== undefined;
    });
    if (!vivas.length) {
      return '<div class="sc-vacio">Sin datos para el embudo</div>';
    }

    var tinta = SC.PALETA.tinta[tema];
    var mudo = SC.PALETA.mudo[tema];
    var fondo = SC.PALETA.fondo[tema];

    var ancho = opciones.ancho || 560;
    var altoEtapa = opciones.altoEtapa || 46;
    var alto = vivas.length * altoEtapa + 12;
    var cx = ancho * 0.40;              // el embudo a la izquierda, textos a la derecha
    var maxAncho = ancho * 0.62;
    var tope = vivas[0].n || 1;

    // Un piso de ancho para que una etapa en 1 sobre 100 siga siendo visible y
    // clickeable. Sin esto, las etapas del fondo desaparecen justo cuando son
    // las que importan.
    var MINIMO = 26;
    function anchoDe(n) {
      if (!tope) return MINIMO;
      return Math.max(MINIMO, (n / tope) * maxAncho);
    }

    var piezas = [];
    vivas.forEach(function (e, i) {
      var y = 6 + i * altoEtapa;
      var w0 = anchoDe(e.n);
      // El ultimo tramo no se angosta contra nada: baja recto.
      var sig = (i + 1 < vivas.length) ? vivas[i + 1] : e;
      var w1 = anchoDe(sig.n);
      var h = altoEtapa - 6;
      var color = SC.colorDeEtapa(e.clave, tema);

      piezas.push(
        '<polygon points="' +
        [(cx - w0 / 2).toFixed(1) + ',' + y,
         (cx + w0 / 2).toFixed(1) + ',' + y,
         (cx + w1 / 2).toFixed(1) + ',' + (y + h),
         (cx - w1 / 2).toFixed(1) + ',' + (y + h)].join(' ') +
        '" fill="' + color + '" stroke="' + fondo + '" stroke-width="2">' +
        '<title>' + SC.esc(e.etiqueta) + ': ' +
        SC.esc(SC.fmt(e.n, 'numero')) + '</title></polygon>');

      // El numero y el nombre van afuera, a la derecha: adentro no entran
      // cuando el tramo se angosta, que es justo donde mas se quiere leerlos.
      piezas.push(
        '<text x="' + (cx + maxAncho / 2 + 16) + '" y="' + (y + h / 2 - 2) +
        '" font-size="12" font-weight="700" fill="' + tinta + '">' +
        SC.esc(SC.fmt(e.n, 'numero')) + '</text>' +
        '<text x="' + (cx + maxAncho / 2 + 16) + '" y="' + (y + h / 2 + 12) +
        '" font-size="10" fill="' + mudo + '">' +
        SC.esc(e.etiqueta) + '</text>');
    });

    return '<svg viewBox="0 0 ' + ancho + ' ' + alto +
           '" style="width:100%;height:auto" role="img" aria-label="' +
           SC.esc(opciones.etiqueta || 'Embudo') + '">' +
           piezas.join('') + '</svg>';
  };

  SC.serieMulti = function (series, opciones, tema) {
    opciones = opciones || {};
    var conDatos = (series || []).filter(function (s) {
      return (s.puntos || []).some(function (p) {
        return p.y !== null && p.y !== undefined;
      });
    });
    if (!conDatos.length) {
      return '<div class="sc-vacio">Sin datos para «' +
             SC.esc(opciones.etiqueta || '') + '»</div>';
    }

    var tinta = SC.PALETA.tinta[tema];
    var mudo = SC.PALETA.mudo[tema];
    var grilla = SC.PALETA.grilla[tema];
    var fondo = SC.PALETA.fondo[tema];

    var ancho = opciones.ancho || 980;
    var alto = opciones.alto || 230;
    var x0 = _M.izquierda, x1 = ancho - _M.derecha;
    var y0 = _M.arriba, y1 = alto - _M.abajo;

    // El eje X son todas las semanas que aparecen en cualquier serie, en orden.
    // Si cada serie usara su propio eje, dos campanas con semanas distintas
    // quedarian desalineadas y la comparacion mentiria.
    var equis = [];
    conDatos.forEach(function (s) {
      (s.puntos || []).forEach(function (p) {
        if (equis.indexOf(p.x) === -1) equis.push(p.x);
      });
    });
    equis.sort();

    var valores = [];
    conDatos.forEach(function (s) {
      (s.puntos || []).forEach(function (p) {
        if (p.y !== null && p.y !== undefined) valores.push(p.y);
      });
    });
    var max = valores.length ? Math.max.apply(null, valores) : 0;
    var cortes = SC.ticks(0, max || 1, 4);
    var ey = SC.escalaLineal([0, cortes[cortes.length - 1]], [y1, y0]);
    var paso = equis.length > 1 ? (x1 - x0) / (equis.length - 1) : 0;
    var ex = function (i) {
      return equis.length > 1 ? x0 + i * paso : (x0 + x1) / 2;
    };

    var piezas = [];

    piezas.push('<g class="sc-eje-y">' + cortes.map(function (t) {
      var y = ey(t);
      return '<line x1="' + x0 + '" y1="' + y.toFixed(1) + '" x2="' + x1 +
             '" y2="' + y.toFixed(1) + '" stroke="' + grilla +
             '" stroke-width="1"/>' +
             '<text x="' + (x0 - 8) + '" y="' + (y + 4).toFixed(1) +
             '" text-anchor="end" font-size="10" fill="' + mudo + '">' +
             SC.esc(SC.fmt(t, opciones.formato)) + '</text>';
    }).join('') + '</g>');

    var cada = Math.max(1, Math.ceil(equis.length / 8));
    piezas.push('<g class="sc-eje-x">' + equis.map(function (x, i) {
      if (i % cada) return '';
      return '<text x="' + ex(i).toFixed(1) + '" y="' + (alto - 8) +
             '" text-anchor="middle" font-size="10" fill="' + mudo + '">' +
             SC.esc(x) + '</text>';
    }).join('') + '</g>');

    conDatos.forEach(function (s, indice) {
      var color = SC.colorDeCampana(s.campana, indice, tema);
      var porX = {};
      (s.puntos || []).forEach(function (p) { porX[p.x] = p.y; });

      // Cada hueco corta la linea. Unir por arriba de una semana sin dato
      // inventa una tendencia que nadie midio.
      var tramo = [];
      function cerrar() {
        if (tramo.length > 1) {
          piezas.push('<path class="sc-linea" d="M' + tramo.join(' L') +
                      '" fill="none" stroke="' + color +
                      '" stroke-width="2" stroke-linecap="round" ' +
                      'stroke-linejoin="round"/>');
        } else if (tramo.length === 1) {
          piezas.push('<path class="sc-linea" d="M' + tramo[0] + ' L' +
                      tramo[0] + '" fill="none" stroke="' + color +
                      '" stroke-width="2" stroke-linecap="round"/>');
        }
        tramo = [];
      }
      equis.forEach(function (x, i) {
        var y = porX[x];
        if (y === null || y === undefined) { cerrar(); return; }
        tramo.push(ex(i).toFixed(1) + ',' + ey(y).toFixed(1));
      });
      cerrar();

      equis.forEach(function (x, i) {
        var y = porX[x];
        if (y === null || y === undefined) return;
        piezas.push('<circle cx="' + ex(i).toFixed(1) + '" cy="' +
                    ey(y).toFixed(1) + '" r="3.5" fill="' + color +
                    '" stroke="' + fondo + '" stroke-width="2"><title>' +
                    SC.esc(s.campana) + ' · ' + SC.esc(x) + ' · ' +
                    SC.esc(SC.fmt(y, opciones.formato)) +
                    '</title></circle>');
      });
    });

    // Leyenda: con dos series o mas, la identidad no puede depender solo del
    // color. El cuadrito lleva el color y el texto va en tinta, nunca coloreado.
    var leyenda = conDatos.map(function (s, indice) {
      return '<span class="sc-leyenda-item">' +
             '<span class="sc-leyenda-punto" style="background:' +
             SC.colorDeCampana(s.campana, indice, tema) + '"></span>' +
             SC.esc(SC.recortar(s.campana, 26)) + '</span>';
    }).join('');

    return '<div class="sc-panel-serie">' +
           '<div class="sc-titulo" style="color:' + tinta + '">' +
           SC.esc(opciones.etiqueta || '') + '</div>' +
           '<svg viewBox="0 0 ' + ancho + ' ' + alto +
           '" style="width:100%;height:auto" role="img" aria-label="' +
           SC.esc(opciones.etiqueta || '') + '">' + piezas.join('') +
           '</svg>' +
           '<div class="sc-leyenda">' + leyenda + '</div></div>';
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
  //
  // YA NO SE USA EN EL PANEL. El bigote es correcto y resulto ilegible para
  // quien no trabaja con intervalos todos los dias —"no los estoy logrando
  // interpretar"— y un grafico que no se entiende no informa. El panel usa
  // `barrasSimples`, que dice la incertidumbre con palabras: "sobre 12 ·
  // muestra chica". Queda disponible para un informe tecnico; si vuelve al
  // panel, vuelve el mismo problema.

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

  // ── Dispersión ───────────────────────────────────────────────────────────
  //
  // Dos medidas contra dos medidas es el unico caso donde una dispersion es la
  // forma correcta: una barra compara magnitudes, una linea muestra el tiempo,
  // pero la RELACION entre dos cosas necesita los dos ejes.
  //
  // El tamano de la burbuja lleva una tercera medida —el gasto— porque el area
  // se compara mal pero alcanza para "esta pesa mas que aquella", que es todo
  // lo que hace falta. El numero exacto va en el tooltip.
  SC.dispersion = function (puntos, opciones, tema) {
    opciones = opciones || {};
    var vivos = (puntos || []).filter(function (p) {
      return p.x !== null && p.x !== undefined && p.y !== null && p.y !== undefined;
    });
    if (!vivos.length) {
      return '<div class="sc-vacio">Sin datos para «' +
             SC.esc(opciones.etiqueta || '') + '»</div>';
    }

    var tinta = SC.PALETA.tinta[tema];
    var mudo = SC.PALETA.mudo[tema];
    var grilla = SC.PALETA.grilla[tema];
    var fondo = SC.PALETA.fondo[tema];

    var ancho = opciones.ancho || 980;
    var alto = opciones.alto || 300;
    var x0 = _M.izquierda, x1 = ancho - _M.derecha - 120;
    var y0 = _M.arriba, y1 = alto - _M.abajo - 10;

    var maxX = Math.max.apply(null, vivos.map(function (p) { return p.x; }));
    var maxY = Math.max.apply(null, vivos.map(function (p) { return p.y; }));
    var cortesX = SC.ticks(0, maxX || 1, 4);
    var cortesY = SC.ticks(0, maxY || 1, 4);
    var ex = SC.escalaLineal([0, cortesX[cortesX.length - 1]], [x0, x1]);
    var ey = SC.escalaLineal([0, cortesY[cortesY.length - 1]], [y1, y0]);

    var maxPeso = Math.max.apply(null, vivos.map(function (p) { return p.peso || 0; }));
    function radio(peso) {
      if (!maxPeso || !peso) return 6;
      // Raiz cuadrada: el AREA tiene que ser proporcional al valor, no el radio.
      // Con el radio proporcional, el doble de gasto se ve cuatro veces mas
      // grande y la lectura miente.
      return 6 + Math.sqrt(peso / maxPeso) * 16;
    }

    var piezas = [];

    piezas.push('<g class="sc-eje-y">' + cortesY.map(function (t) {
      var y = ey(t);
      return '<line x1="' + x0 + '" y1="' + y.toFixed(1) + '" x2="' + x1 +
             '" y2="' + y.toFixed(1) + '" stroke="' + grilla + '" stroke-width="1"/>' +
             '<text x="' + (x0 - 8) + '" y="' + (y + 4).toFixed(1) +
             '" text-anchor="end" font-size="10" fill="' + mudo + '">' +
             SC.esc(SC.fmt(t, opciones.formatoY)) + '</text>';
    }).join('') + '</g>');

    piezas.push('<g class="sc-eje-x">' + cortesX.map(function (t) {
      return '<text x="' + ex(t).toFixed(1) + '" y="' + (alto - 14) +
             '" text-anchor="middle" font-size="10" fill="' + mudo + '">' +
             SC.esc(SC.fmt(t, opciones.formatoX)) + '</text>';
    }).join('') + '</g>');

    // Los nombres de los ejes: sin ellos una dispersion es un dibujo de puntos.
    piezas.push('<text x="' + ((x0 + x1) / 2) + '" y="' + (alto - 1) +
                '" text-anchor="middle" font-size="10" fill="' + mudo + '">' +
                SC.esc(opciones.nombreX || '') + '</text>');
    piezas.push('<text x="12" y="' + ((y0 + y1) / 2) +
                '" text-anchor="middle" font-size="10" fill="' + mudo +
                '" transform="rotate(-90 12 ' + ((y0 + y1) / 2) + ')">' +
                SC.esc(opciones.nombreY || '') + '</text>');

    vivos.forEach(function (p, i) {
      var color = SC.colorDeCampana(p.etiqueta, i, tema);
      var cx = ex(p.x), cy = ey(p.y), r = radio(p.peso);
      piezas.push('<circle cx="' + cx.toFixed(1) + '" cy="' + cy.toFixed(1) +
                  '" r="' + r.toFixed(1) + '" fill="' + color +
                  '" fill-opacity="0.75" stroke="' + fondo + '" stroke-width="2">' +
                  '<title>' + SC.esc(p.etiqueta) + ' · ' +
                  SC.esc(opciones.nombreX || 'x') + ': ' +
                  SC.esc(SC.fmt(p.x, opciones.formatoX)) + ' · ' +
                  SC.esc(opciones.nombreY || 'y') + ': ' +
                  SC.esc(SC.fmt(p.y, opciones.formatoY)) +
                  (p.peso ? ' · ' + SC.esc(opciones.nombrePeso || 'peso') + ': ' +
                   SC.esc(SC.fmt(p.peso, opciones.formatoPeso)) : '') +
                  '</title></circle>');
      // Etiqueta directa al lado de cada burbuja: con pocas marcas es mejor que
      // una leyenda, porque no obliga a ir y volver.
      piezas.push('<text x="' + (cx + r + 6).toFixed(1) + '" y="' + (cy + 4).toFixed(1) +
                  '" font-size="10" fill="' + tinta + '">' +
                  SC.esc(SC.recortar(p.etiqueta, 20)) + '</text>');
    });

    return '<div class="sc-panel-serie">' +
           '<div class="sc-titulo" style="color:' + tinta + '">' +
           SC.esc(opciones.etiqueta || '') + '</div>' +
           '<svg viewBox="0 0 ' + ancho + ' ' + alto +
           '" style="width:100%;height:auto" role="img" aria-label="' +
           SC.esc(opciones.etiqueta || '') + '">' + piezas.join('') + '</svg></div>';
  };

  // ── Barras divergentes ───────────────────────────────────────────────────
  //
  // Para polaridad: falta o sobra, desde un cero central. Una barra comun
  // obliga a leer el signo en el numero; aca el lado del cero ya lo dice.
  //
  // Dos tonos y nada mas, nunca un arcoiris: el ojo lee "de un lado o del
  // otro", y un tercer color inventaria una tercera categoria.
  SC.barrasDivergentes = function (filas, opciones, tema) {
    opciones = opciones || {};
    var vivas = (filas || []).filter(function (f) {
      return f.valor !== null && f.valor !== undefined;
    });
    if (!vivas.length) {
      return '<div class="sc-vacio">Sin datos para «' +
             SC.esc(opciones.etiqueta || '') + '»</div>';
    }

    var tinta = SC.PALETA.tinta[tema];
    var mudo = SC.PALETA.mudo[tema];
    var grilla = SC.PALETA.grilla[tema];
    // Los colores de estado, que estan reservados justo para esto y nunca se
    // usan como "serie 4".
    var positivo = SC.PALETA.mal[tema];     // falta plata: es la mala noticia
    var negativo = SC.PALETA.bien[tema];

    var anchoEtiqueta = 92;
    var anchoValor = 96;
    var ancho = opciones.ancho || 900;
    var altoFila = 30;
    var alto = vivas.length * altoFila + 26;
    var x0 = anchoEtiqueta, x1 = ancho - anchoValor;
    var cx = (x0 + x1) / 2;

    var tope = Math.max.apply(null, vivas.map(function (f) {
      return Math.abs(f.valor);
    })) || 1;
    var media = (x1 - x0) / 2;

    var piezas = ['<line x1="' + cx + '" y1="6" x2="' + cx + '" y2="' +
                  (alto - 18) + '" stroke="' + grilla + '" stroke-width="1"/>'];

    vivas.forEach(function (f, i) {
      var y = 10 + i * altoFila;
      var largo = (Math.abs(f.valor) / tope) * media;
      var esPos = f.valor >= 0;
      var x = esPos ? cx : cx - largo;
      piezas.push('<rect x="' + x.toFixed(1) + '" y="' + y + '" width="' +
                  Math.max(2, largo).toFixed(1) + '" height="' + (altoFila - 12) +
                  '" rx="4" fill="' + (esPos ? positivo : negativo) + '">' +
                  '<title>' + SC.esc(f.etiqueta) + ': ' +
                  SC.esc(SC.fmt(f.valor, opciones.formato)) + '</title></rect>');
      piezas.push('<text x="' + (anchoEtiqueta - 10) + '" y="' + (y + 13) +
                  '" text-anchor="end" font-size="11" fill="' + tinta + '">' +
                  SC.esc(f.etiqueta) + '</text>');
      piezas.push('<text x="' + (x1 + 10) + '" y="' + (y + 13) +
                  '" font-size="11" fill="' + mudo + '">' +
                  SC.esc(SC.fmt(f.valor, opciones.formato)) + '</text>');
    });

    piezas.push('<text x="' + cx + '" y="' + (alto - 4) +
                '" text-anchor="middle" font-size="10" fill="' + mudo + '">' +
                SC.esc(opciones.cero || '0') + '</text>');

    return '<div class="sc-panel-serie">' +
           '<div class="sc-titulo" style="color:' + tinta + '">' +
           SC.esc(opciones.etiqueta || '') + '</div>' +
           '<svg viewBox="0 0 ' + ancho + ' ' + alto +
           '" style="width:100%;height:auto" role="img" aria-label="' +
           SC.esc(opciones.etiqueta || '') + '">' + piezas.join('') + '</svg></div>';
  };

  // ── Mapa de calor ────────────────────────────────────────────────────────
  //
  // Una sola rampa de un solo tono, de transparente a lleno. Nunca un arcoiris:
  // en un arcoiris el orden de los colores no es el orden de los numeros, asi
  // que hay que ir a la referencia por cada celda.
  SC.matriz = function (celdas, opciones, tema) {
    opciones = opciones || {};
    var filas = opciones.filas || [];
    var columnas = opciones.columnas || [];
    if (!celdas || !celdas.length || !filas.length || !columnas.length) {
      return '<div class="sc-vacio">Sin datos para «' +
             SC.esc(opciones.etiqueta || '') + '»</div>';
    }

    var tinta = SC.PALETA.tinta[tema];
    var mudo = SC.PALETA.mudo[tema];
    var grilla = SC.PALETA.grilla[tema];
    var base = SC.PALETA[tema][0];          // el azul de marca, como unico tono

    var anchoEtiqueta = 46;
    var lado = opciones.lado || 36;
    var ancho = anchoEtiqueta + columnas.length * lado + 8;
    var alto = 24 + filas.length * lado + 8;
    var tope = opciones.maximo;

    var piezas = [];

    columnas.forEach(function (c, j) {
      piezas.push('<text x="' + (anchoEtiqueta + j * lado + lado / 2) +
                  '" y="14" text-anchor="middle" font-size="9" fill="' + mudo +
                  '">' + SC.esc(c.etiqueta) + '</text>');
    });

    filas.forEach(function (f, i) {
      piezas.push('<text x="' + (anchoEtiqueta - 8) + '" y="' +
                  (24 + i * lado + lado / 2 + 4) + '" text-anchor="end" ' +
                  'font-size="10" fill="' + mudo + '">' +
                  SC.esc(f.etiqueta) + '</text>');
    });

    function indice(lista, clave) {
      for (var k = 0; k < lista.length; k++) {
        if (lista[k].clave === clave) return k;
      }
      return -1;
    }

    celdas.forEach(function (celda) {
      var i = indice(filas, celda.fila);
      var j = indice(columnas, celda.columna);
      if (i < 0 || j < 0) return;
      var x = anchoEtiqueta + j * lado;
      var y = 24 + i * lado;
      // El cero queda sin relleno, no como el paso mas claro de la rampa: "no
      // entro nadie" y "entro poca gente" son cosas distintas y el mapa tiene
      // que dejar verlas distinto.
      var intensidad = (!tope || !celda.n) ? 0 : Math.max(0.14, celda.n / tope);
      piezas.push('<rect x="' + (x + 2) + '" y="' + (y + 2) + '" width="' +
                  (lado - 4) + '" height="' + (lado - 4) + '" rx="4" fill="' +
                  base + '" fill-opacity="' + intensidad.toFixed(2) +
                  '" stroke="' + grilla + '" stroke-width="1">' +
                  '<title>' + SC.esc(celda.titulo || '') + ': ' + celda.n +
                  '</title></rect>');
      if (celda.n) {
        piezas.push('<text x="' + (x + lado / 2) + '" y="' + (y + lado / 2 + 4) +
                    '" text-anchor="middle" font-size="10" fill="' + tinta +
                    '">' + celda.n + '</text>');
      }
    });

    return '<div class="sc-panel-serie">' +
           '<div class="sc-titulo" style="color:' + tinta + '">' +
           SC.esc(opciones.etiqueta || '') + '</div>' +
           '<svg viewBox="0 0 ' + ancho + ' ' + alto +
           '" style="width:100%;height:auto;max-width:' + ancho + 'px" ' +
           'role="img" aria-label="' + SC.esc(opciones.etiqueta || '') + '">' +
           piezas.join('') + '</svg></div>';
  };


  // ── Barras agrupadas ─────────────────────────────────────────────────────
  //
  // Varias medidas para cada periodo, una al lado de la otra. Es la forma
  // correcta cuando lo que se compara son MAGNITUDES en categorias discretas
  // —doce meses, no un continuo— y ademas se quiere comparar las series entre
  // si dentro de cada periodo.
  //
  // Una linea serviria para la tendencia, pero con tres o cuatro puntos una
  // linea se lee como si faltara algo. Barras, en cambio, se leen bien desde
  // una sola barra.
  //
  // Un solo eje Y, y por eso las series tienen que ser de la misma naturaleza:
  // leads, demos y ventas son todas cuentas de personas. Meterle el gasto acá
  // seria mezclar dolares con personas en la misma escala.
  SC.barrasAgrupadas = function (periodos, series, opciones, tema) {
    opciones = opciones || {};
    if (!periodos || !periodos.length || !series || !series.length) {
      return '<div class="sc-vacio">Sin datos para «' +
             SC.esc(opciones.etiqueta || '') + '»</div>';
    }

    var tinta = SC.PALETA.tinta[tema];
    var mudo = SC.PALETA.mudo[tema];
    var grilla = SC.PALETA.grilla[tema];
    var fondo = SC.PALETA.fondo[tema];

    var ancho = opciones.ancho || 980;
    var alto = opciones.alto || 300;
    var y0 = _M.arriba, y1 = alto - _M.abajo - 6;

    var todos = [];
    series.forEach(function (s) {
      periodos.forEach(function (p) {
        var v = s.valores[p.clave];
        if (v !== null && v !== undefined) todos.push(v);
      });
    });
    var max = todos.length ? Math.max.apply(null, todos) : 0;
    var cortes = SC.ticks(0, max || 1, 4);

    // El margen izquierdo sale de la etiqueta mas larga del eje y no de una
    // constante: con un margen fijo, "1.234,56" se sale del viewBox por la
    // izquierda y el numero aparece cortado.
    var largoY = Math.max.apply(null, cortes.map(function (t) {
      return SC.fmt(t, opciones.formato).length;
    }));
    var x0 = Math.max(_M.izquierda, largoY * 6 + 14);
    var x1 = ancho - _M.derecha;
    var ey = SC.escalaLineal([0, cortes[cortes.length - 1]], [y1, y0]);

    var anchoGrupo = (x1 - x0) / periodos.length;
    // Un respiro de 2px entre barras vecinas: pegadas se leen como una sola
    // barra de otro color.
    var GAP = 2;
    // Y un tope: con una sola serie y tres periodos, el 78% del grupo son
    // barras de 250px de ancho. Una barra asi no se lee como un dato, se lee
    // como un bloque de color. La guia de visualizacion pide marcas finas.
    var TOPE = opciones.anchoBarra || 46;
    var anchoBarra = Math.min(TOPE, Math.max(
      3, (anchoGrupo * 0.78 - GAP * (series.length - 1)) / series.length));

    var piezas = [];

    piezas.push('<g class="sc-eje-y">' + cortes.map(function (t) {
      var y = ey(t);
      return '<line x1="' + x0 + '" y1="' + y.toFixed(1) + '" x2="' + x1 +
             '" y2="' + y.toFixed(1) + '" stroke="' + grilla + '" stroke-width="1"/>' +
             '<text x="' + (x0 - 8) + '" y="' + (y + 4).toFixed(1) +
             '" text-anchor="end" font-size="10" fill="' + mudo + '">' +
             SC.esc(SC.fmt(t, opciones.formato)) + '</text>';
    }).join('') + '</g>');

    periodos.forEach(function (p, i) {
      var centro = x0 + anchoGrupo * (i + 0.5);
      var anchoTotal = anchoBarra * series.length + GAP * (series.length - 1);
      var inicio = centro - anchoTotal / 2;

      series.forEach(function (s, j) {
        var v = s.valores[p.clave];
        if (v === null || v === undefined) return;
        var y = ey(v);
        var x = inicio + j * (anchoBarra + GAP);
        var h = Math.max(0, y1 - y);
        piezas.push('<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) +
                    '" width="' + anchoBarra.toFixed(1) + '" height="' +
                    h.toFixed(1) + '" rx="3" fill="' + s.color +
                    '" stroke="' + fondo + '" stroke-width="1"><title>' +
                    SC.esc(p.etiqueta) + ' · ' + SC.esc(s.etiqueta) + ': ' +
                    SC.esc(SC.fmt(v, opciones.formato)) + '</title></rect>');
        // El numero arriba de la barra: con pocas barras entra y evita tener
        // que estimar contra la grilla.
        if (opciones.numeros !== false && anchoBarra >= 14 && v) {
          piezas.push('<text x="' + (x + anchoBarra / 2).toFixed(1) + '" y="' +
                      (y - 4).toFixed(1) + '" text-anchor="middle" ' +
                      'font-size="9" fill="' + mudo + '">' +
                      SC.esc(SC.fmt(v, opciones.formato)) + '</text>');
        }
      });

      piezas.push('<text x="' + centro.toFixed(1) + '" y="' + (alto - 10) +
                  '" text-anchor="middle" font-size="10" fill="' + mudo + '">' +
                  SC.esc(p.etiqueta) + '</text>');
    });

    // Una sola serie no lleva leyenda: el titulo ya la nombra, y un recuadro
    // con un solo item repite el titulo y se lee como si faltaran los demas.
    var leyenda = series.length < 2 ? '' : series.map(function (s) {
      return '<span class="sc-leyenda-item">' +
             '<span class="sc-leyenda-punto" style="background:' + s.color +
             '"></span>' + SC.esc(s.etiqueta) + '</span>';
    }).join('');

    return '<div class="sc-panel-serie">' +
           '<div class="sc-titulo" style="color:' + tinta + '">' +
           SC.esc(opciones.etiqueta || '') + '</div>' +
           '<svg viewBox="0 0 ' + ancho + ' ' + alto +
           '" style="width:100%;height:auto" role="img" aria-label="' +
           SC.esc(opciones.etiqueta || '') + '">' + piezas.join('') + '</svg>' +
           (leyenda ? '<div class="sc-leyenda">' + leyenda + '</div>' : '') +
           '</div>';
  };

  // Barras simples de una sola serie, ordenadas de mayor a menor.
  //
  // Reemplaza a `barrasConIC` en el panel: el intervalo de confianza es
  // correcto y es ilegible para quien no lo usa todos los dias, y un grafico
  // que no se entiende no informa. La incertidumbre no se tira: se dice con
  // palabras —"n=12, muestra chica"— al lado del numero.
  SC.barrasSimples = function (filas, opciones, tema) {
    opciones = opciones || {};
    var vivas = (filas || []).filter(function (f) {
      return f.valor !== null && f.valor !== undefined;
    });
    if (!vivas.length) {
      return '<div class="sc-vacio">Sin datos para «' +
             SC.esc(opciones.etiqueta || '') + '»</div>';
    }
    vivas = vivas.slice().sort(function (a, b) { return b.valor - a.valor; });

    var tinta = SC.PALETA.tinta[tema];
    var mudo = SC.PALETA.mudo[tema];
    var pista = SC.PALETA.grilla[tema];

    var tope = Math.max.apply(null, vivas.map(function (f) { return f.valor; })) || 1;

    return '<div class="sc-panel-serie">' +
           '<div class="sc-titulo" style="color:' + tinta + '">' +
           SC.esc(opciones.etiqueta || '') + '</div>' +
           // La ayuda va DEBAJO del titulo: primero que grafico es, despues
           // como leerlo. Al reves se lee la explicacion de algo que todavia
           // no tiene nombre.
           (opciones.ayuda
             ? '<div class="sc-barras-ayuda" style="color:' + mudo + '">' +
               SC.esc(opciones.ayuda) + '</div>'
             : '') +
           '<div class="sc-barras">' + vivas.map(function (f, i) {
             var pct = (f.valor / tope) * 100;
             var color = f.color || SC.colorDeCampana(f.etiqueta, i, tema);
             return '<div class="sc-barra-fila">' +
                    '<span class="sc-barra-nom">' + SC.esc(f.etiqueta) + '</span>' +
                    '<span class="sc-barra-pista" style="background:' + pista + '">' +
                    '<span class="sc-barra-lleno" style="width:' + pct.toFixed(1) +
                    '%;background:' + color + '"></span></span>' +
                    '<span class="sc-barra-val">' +
                    SC.esc(SC.fmt(f.valor, opciones.formato)) +
                    (f.nota ? '<span class="sc-barra-nota">' + SC.esc(f.nota) +
                     '</span>' : '') + '</span></div>';
           }).join('') + '</div></div>';
  };

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
