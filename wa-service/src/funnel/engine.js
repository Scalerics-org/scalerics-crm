'use strict';

const { S, palabraGlobal } = require('./states');
const {
  eligioEsaHora, revisarFranja, textoDeFranja, nombroAlgunDia,
  elegirDiaPorCodigo, elegirHoraPorCodigo,
} = require('../agenda/eleccion');
const { enZona, instanteLocal } = require('../agenda/gcal');
const { TRANSICIONES } = require('./transitions');
const plantillas = require('../templates');
const { detectar, paraUnaPersona, ETIQUETA } = require('./derivacion');
const { esAcuse } = require('./acuse');
const { cuandoVolver } = require('./nurture');
const { prometeAgendar } = require('../ia/promesas');

/**
 * "ya agende", "ya reserve". En primera persona y en pasado a proposito: con
 * "agendamos?" —que lo dice el que todavia NO reservo— seria justo al reves.
 * La entrada llega normalizada, sin acentos.
 */
const YA_AGENDO = /\b(ya\s+)?(agende|reserve|coordine|saque\s+(el\s+)?turno|lo\s+saque)\b/;

function normalizar(texto) {
  return String(texto || '')
    .trim()
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '');
}

/**
 * Estados donde la IA todavia esta averiguando. Al salir de aca se califica y
 * se ofrece —o no— la reunion, y esa decision es del score, no del modelo.
 */
const FASE_CALIFICACION = new Set([S.NEW, S.CONVERSANDO, S.NURTURE, S.DISQUALIFIED]);

/** Donde el bot ya le ofrecio una reunion: ahi hablar de moverla es del embudo. */
const OFRECIO_REUNION = new Set([S.MEETING_SENT, S.HORARIOS_OFRECIDOS, S.MEETING_LINK_SENT, S.SCHEDULED]);

/**
 * Que decirle a cada uno. Va como contexto al redactor: sin esto tendria que
 * deducir de la conversacion por que lo estan descartando, y ahi se inventa el
 * motivo — como cuando anuncio que la empresa no estaba buscando gente, que es
 * una politica que el bot no conoce.
 */
const MOTIVO = {
  trabajo: 'Mandó un CV o busca trabajo. Decile que le pasás el mensaje al equipo. NO digas si estamos buscando gente o no: eso no lo sabés.',
  vender_algo: 'Te está ofreciendo un producto o servicio a vos. Agradecele y decile que no es por acá.',
  numero_equivocado: 'Se equivocó de número, el mensaje no era para nosotros. Decíselo en una línea, sin darle importancia.',
  algo_que_no_hacemos: 'Pide algo que no hacemos. Decile qué sí hacemos —software, webs, tiendas online, automatizaciones— en media línea, sin ofrecerle una reunión.',
};

/**
 * De la oferta en adelante la IA sigue conversando pero ya no puede volver a
 * ofrecer: el salto a SCORED solo corre mientras califica. Conversar no es
 * decidir.
 *
 * MEETING_LINK_SENT quedaba afuera, y con el link saliendo apenas se sabe que
 * necesita el lead, ese es el estado donde transcurre casi toda la
 * conversacion. Al no estar en ninguna fase, la IA no lo miraba: el turno caia
 * en la tabla de transiciones y de ahi al contador de insistencias.
 *
 * Se veia asi en produccion. El lead decia "me interesa pero para el mes que
 * viene", el bot contestaba "¿pudiste agendar?" —porque nadie habia leido lo
 * que dijo— y al segundo mensaje lo derivaba a una persona por insistir. Justo
 * el momento en que mas gente dice que lo ve mas adelante, y justo el que la
 * pausa venia a resolver.
 */
/**
 * Los estados que el CRM tiene que conocer: alguien del equipo tiene algo que
 * hacer con ese lead.
 *
 * MEETING_LINK_SENT esta aca desde que el embudo cierra de un saque. Antes
 * paraba en MEETING_SENT y recien con un "dale" del lead pasaba al link; ahora
 * encadena los dos en el mismo turno, asi que el estado final es siempre el del
 * link. Sin agregarlo, la condicion no se cumplia nunca y el CRM dejo de
 * enterarse de los leads calificados sin que nadie lo notara.
 */
const AVISAR_AL_CRM = new Set([S.MEETING_SENT, S.MEETING_LINK_SENT, S.HUMAN_QUEUED]);

/**
 * Por que no se puede dar el horario que pidio. Va como contexto al redactor:
 * repetir la lista sin decir el motivo se lee como que el bot no escucha, que
 * es lo que paso el 2-9 con "las 5 de la mañana" y despues "las 8:30".
 */
const MOTIVO_FRANJA = {
  /**
   * La franja sale de la config real del dia que pidio, no de
   * AGENDA_DESDE/AGENDA_HASTA. El 3-9 el bot ofrecio "de 10:00 a 19:00" y dos
   * mensajes despues dijo "manejamos entre las 12 y las 16": esas dos
   * variables habian quedado en los valores viejos cuando la config paso a
   * horarios por dia, y el rechazo las citaba. El lead lo noto enseguida.
   */
  fuera_de_franja: (cfg, inicio) => {
    const franja = textoDeFranja(inicio, cfg);
    return franja
      ? `Pidió una hora fuera del horario en que agendamos ese día, que es de ${franja}. Decíselo en una línea, sin pedir disculpas de más.`
      : 'Pidió una hora en la que no agendamos. Decíselo en una línea, sin pedir disculpas de más.';
  },
  dia_no_habil: () => 'Pidió un día que no es hábil: solo agendamos de lunes a viernes. Decíselo en una línea.',
  muy_pronto: (cfg) => `Pidió algo demasiado pronto: hace falta al menos ${cfg.AGENDA_AVISO_MIN_HORAS} horas de aviso. Decíselo sin sonar burocrático.`,
  muy_lejos: (cfg) => `Pidió una fecha demasiado lejana: agendamos hasta ${cfg.AGENDA_DIAS_ADELANTE} días adelante. Decíselo y ofrecele lo que hay.`,
};

const FASE_CIERRE = new Set([
  S.MEETING_SENT, S.MEETING_INFO, S.MEETING_LINK_SENT, S.SCHEDULED,
]);

/**
 * Motor del embudo.
 *
 * Ya no decide QUE decir —eso lo escribe la IA— sino CUANDO decir algo y a
 * quien le toca: al modelo o a una persona. Las decisiones que quedaron en
 * codigo son las que no pueden depender de que un modelo obedezca una
 * instruccion: la baja, la derivacion por queja o facturacion, el limite de una
 * sola consulta de precio, y que la reunion se ofrezca por score.
 */
function crearEmbudo({
  repo, cola, textos, scorer, logger, cfg = { amPhones: [] },
  crmNotify = null, agente = null, redactor = null, agenda = null, ahora = () => new Date(),
  /**
   * Programa los recordatorios de una reunion. Llega como funcion y no como el
   * scheduler entero porque el scheduler se arma DESPUES del embudo —lo tiene
   * como dependencia— asi que aca solo puede haber algo que se resuelva cuando
   * se llama.
   */
  recordatorios = null,
  /** Jev en modo sombra (ia/sombra.js): mira y anota, nunca cambia lo que sale. */
  sombra = null,
}) {
  const CALENDLY = cfg.CALENDLY_LINK || '';

  /** Lo que se le suma al pedido cuando el primer intento invento algo. */
  const SIN_ATRIBUIR = '\n\nIMPORTANTE: no le atribuyas al lead ninguna necesidad, problema ni objetivo '
    + 'que no haya dicho él mismo. Si no te dijo para qué lo quiere, no lo supongas: hablá de lo que sí dijo, '
    + 'o no hables de su situación en absoluto.';

  function decir(lead, texto) {
    cola.encolar({ to: lead.telefono, texto, kind: 'manual', leadId: lead.id });
  }

  /**
   * Le pide a la IA el mensaje de una situacion y lo manda.
   *
   * @param {string} sufijoCodigo se pega al final del mensaje SIN pasar por
   *   el modelo ni por Jev: la lista de dias u horas de agendar dia-primero,
   *   que arma el codigo. Jev sigue revisando el pitch que escribe la IA -el
   *   sufijo no se le muestra- porque ahi es donde se inventan cosas; una
   *   lista de fechas que arma el codigo no tiene nada que revisar.
   * @returns {Promise<boolean>} false si no se pudo escribir.
   */
  async function decirIA(lead, situacion, extra = '', sufijoCodigo = '') {
    let texto = await redactor?.escribir(lead, situacion, extra);
    if (!texto) return false;

    /**
     * Con JEV_MODO=decide, la oferta pasa por Jev antes de salir: es el mensaje
     * donde el modelo se pone a suponer para que suene a medida. Un reintento y
     * nada mas — el lead esta esperando, y dos llamadas de mas ya se notan.
     *
     * En sombra y apagado esto devuelve null y no cambia nada.
     */
    const veredicto = await sombra?.revisarOferta?.(lead, situacion, texto);
    if (veredicto?.bloquear) {
      // Si la primera revision ya se comio el presupuesto de tiempo del turno,
      // no se reescribe nada: el lead esta esperando y el texto fijo es seguro.
      const hayTiempo = (veredicto.ms ?? 0) < (sombra.presupuestoMs ?? Infinity);
      const segundo = hayTiempo ? await redactor?.escribir(lead, situacion, extra + SIN_ATRIBUIR) : null;
      const otra = segundo
        ? await sombra.revisarOferta(lead, situacion, segundo, { intento: 2, gastadoMs: veredicto.ms })
        : null;
      if (segundo && otra && !otra.bloquear) {
        texto = segundo;
      } else {
        texto = textos.ofertaNeutra(situacion, { extra, calendly: CALENDLY });
        logger?.warn(
          { leadId: lead.id, situacion, probabilidad: veredicto.probabilidad },
          'la oferta le atribuia algo al lead y el reintento tambien: sale el texto fijo'
        );
      }
    }

    const final = sufijoCodigo ? `${texto}\n\n${sufijoCodigo}` : texto;
    decir(lead, final);
    sombra?.alMandarIA(lead, situacion, final);
    return true;
  }

  /**
   * Deriva a un humano y le manda el contexto: quien es, por que, y los ultimos
   * mensajes. Sin el historial, quien atiende arranca a ciegas.
   */
  function derivar(lead, motivo) {
    // Solo el motivo: el flag human_requested lo pone el handler de
    // HUMAN_QUEUED. Si se marcara aca, ese handler creeria que ya estaba
    // derivado y no le avisaria al lead que lo estan pasando con alguien.
    repo.actualizarFunnel(lead.id, { motivo_derivacion: motivo });
    for (const am of cfg.amPhones) {
      cola.encolar({
        to: am,
        texto: plantillas.avisoDerivacion(repo.leadPorId(lead.id), {
          motivo: ETIQUETA[motivo] || motivo,
          historial: repo.ultimosMensajes(lead.id, 6, lead.conversacion_desde),
        }),
        kind: 'am_notice',
        leadId: lead.id,
      });
    }
    logger?.info({ leadId: lead.id, motivo }, 'conversacion derivada a un humano');
  }

  /**
   * Avisa una sola vez cada tantas horas: si se aviso por cada lead, el equipo
   * recibiria una rafaga justo cuando algo ya esta roto.
   */
  function avisarAgendaCaida() {
    const desde = new Date(ahora().getTime() - cfg.AGENDA_AVISO_CAIDA_HORAS * 3600_000).toISOString();
    if (repo.huboAvisoDeAgenda(desde)) return;

    logger?.error('la agenda no contesta: se cae al camino del link de Calendly');
    for (const am of cfg.amPhones) {
      cola.encolar({
        to: am,
        texto: plantillas.AVISO_AGENDA_CAIDA,
        kind: 'am_notice',
        leadId: null,
      });
    }
  }

  /** El AM se entera de como termino el embudo, gane o pierda. */
  function avisarDesenlace(leadId, desenlace) {
    const fresco = repo.leadPorId(leadId);
    for (const am of cfg.amPhones) {
      cola.encolar({
        to: am, texto: plantillas.resumenEmbudo(fresco, desenlace),
        kind: 'am_notice', leadId,
      });
    }
  }

  /**
   * Persiste lo que extrajo la IA. El rubro se clasifica al guardarlo, igual
   * que cuando llega del formulario: rubro_norm es lo que elige el gancho.
   */
  function guardarCampos(leadId, campos, { porAudio = false } = {}) {
    const conNorm = campos.rubro
      ? { ...campos, rubro_norm: plantillas.clasificar(campos.rubro) }
      : campos;
    // Un nombre propio dicho por voz no se puede transcribir bien: no es una
    // palabra que exista. Se guarda igual —el equipo lo lee y ademas tiene el
    // audio— pero queda marcado para que el bot no lo escriba en sus mensajes.
    // Se limpia solo en cuanto el lead lo escribe.
    if (campos.business_name !== undefined) {
      conNorm.business_name_por_audio = porAudio ? 1 : 0;
    }

    /**
     * El modelo dijo business_type=6 ("todavia no sabe") pero el lead ya
     * tenia uno concreto (1 a 5) guardado. Le paso a Patricia el 22-9: el
     * formulario de Meta decia "Crear mi ecommerce" (2), y una respuesta vaga
     * de despues ("Quisiera vender de todo un poco") hizo que el modelo
     * volviera a elegir "todavia no sabe", pisando el dato bueno y avisandole
     * al equipo que no se sabia que necesitaba. Un "no se" tardio no borra un
     * "si se" anterior: se descarta ese campo y se guarda el resto igual.
     */
    if (conNorm.business_type === 6) {
      const actual = repo.leadPorId(leadId);
      if (actual?.business_type && actual.business_type !== 6) {
        const { business_type, ...resto } = conNorm;
        // Rastro para cuando el lead SI se retracto de verdad ("mejor no se,
        // dejame pensarlo"): sin este log, un business_type que dejo de
        // actualizarse es indistinguible de uno que nunca se toco.
        logger?.info(
          { leadId, teniaBusinessType: actual.business_type },
          'el modelo dijo "todavia no sabe" pero el lead ya tenia un business_type concreto: se descarta ese campo'
        );
        repo.actualizarFunnel(leadId, resto);
        return;
      }
    }

    repo.actualizarFunnel(leadId, conNorm);
  }

  /** Los horarios que se le mostraron, tal como quedaron guardados. */
  function leerHorarios(lead) {
    try {
      return JSON.parse(lead.horarios_ofrecidos || '[]').map((s) => new Date(s));
    } catch (e) {
      return [];
    }
  }

  /** "Miércoles 23", con mayuscula inicial y sin repetir el mes. */
  function nombreDia(fecha) {
    const crudo = new Intl.DateTimeFormat('es-UY', {
      timeZone: cfg.TZ, weekday: 'long', day: 'numeric',
    }).format(fecha);
    return crudo.charAt(0).toUpperCase() + crudo.slice(1);
  }

  /** "12:00", en formato 24 horas. */
  function nombreHora(fecha) {
    return new Intl.DateTimeFormat('es-UY', {
      timeZone: cfg.TZ, hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(fecha);
  }

  /**
   * Paso 1 de agendar (dia primero): la lista numerada de dias, que arma el
   * codigo y se pega abajo del pitch de la IA. El modelo no la escribe —no
   * sabe que dias hay libres de verdad— asi que si la escribiera, inventaria.
   */
  function listaDeDias(dias) {
    const lineas = dias.map((d, i) => `${i + 1}. ${nombreDia(d)}`);
    return `¿Qué día te queda mejor?\n\n${lineas.join('\n')}`;
  }

  /**
   * Paso 2: la lista numerada de horas de UN dia ya elegido. El pitch de la
   * IA (situacion disponibilidad_del_dia) ya cierra pidiendo que elija; esto
   * es solo la lista, sin pregunta propia.
   */
  function listaDeHoras(horas) {
    return horas.map((h, i) => `${i + 1}. ${nombreHora(h)}`).join('\n');
  }

  /**
   * Cual de los horarios ofrecidos eligio. Lo interpreta el modelo —entiende
   * "las 13", "la primera", "a la una y media"— pero solo puede devolver uno de
   * los que existen: la lista va como enum, asi que no puede inventar una hora.
   */
  async function elegirHorario(lead, entrada, ofrecidos) {
    if (!agente?.activo) return null;

    const opciones = ofrecidos.map((d) => d.toISOString());
    const etiquetas = ofrecidos.map((d) => new Intl.DateTimeFormat('es-UY', {
      timeZone: cfg.TZ, hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(d));

    const elegido = await agente.elegirDeLista({
      texto: entrada,
      opciones,
      etiquetas,
      instruccion: 'El lead esta eligiendo uno de los horarios que se le ofrecieron. Devolvé cual, o "ninguno" si no se entiende o si contesta otra cosa.',
    });
    if (!elegido) return null;

    const i = opciones.indexOf(elegido);
    if (i < 0) return null;

    /**
     * El modelo elige de un enum cerrado con los horarios ofrecidos, asi que
     * cuando el que el lead quiere NO esta en la lista no puede contestar
     * "ninguno": devuelve el que menos le disgusta.
     *
     * Paso el 2-9: se le ofrecio 12:00 a 14:30, pidio las 15:00 y le agendo las
     * 12:00 confirmandoselas como si fueran las que pidio. El codigo verificaba
     * que el horario existiera y siguiera libre —las dos cosas eran ciertas—
     * pero no que fuera el que el lead pidio.
     *
     * Si el lead no escribio ninguna hora ("la primera", "dale esa") no hay
     * nada que verificar y manda el modelo, que para eso esta.
     */
    if (!eligioEsaHora(entrada, ofrecidos[i], cfg.TZ)) {
      logger?.info(
        { leadId: lead.id, entrada, eligio: opciones[i] },
        'el modelo eligio un horario que no es el que pidio el lead'
      );
      return null;
    }

    return ofrecidos[i];
  }

  /**
   * Sentinela: este turno no habla de horarios y lo tiene que atender la
   * conversacion normal. Sin esto, cualquier cosa que el lead escriba mientras
   * elige horario recibe la lista de vuelta — el 3-9 corrigio "quiero página
   * web no ecommerce" y le contestaron con los horarios.
   */
  const CONVERSAR = '__conversar__';

  /**
   * QUE hacer con lo que el lead dijo sobre horarios. Una sola funcion decide.
   *
   * Antes esto estaba repartido en cuatro ramas y cada una elegia por su cuenta
   * que horarios mostrar despues. Por eso arreglar una no arreglaba las otras:
   * en un mismo dia hubo que tocar tres veces lo mismo, y cada vez quedaba una
   * salida sin cubrir que volvia a ofrecer los dias cercanos cuando el lead
   * estaba hablando de otro.
   *
   * Ahora esto decide y `responderHorarios` responde. El dia en foco se elige
   * UNA vez, no una por rama.
   */
  async function decidirSobreHorarios(lead, entrada, ofrecidos) {
    const elegido = await elegirHorario(lead, entrada, ofrecidos);
    if (elegido) return { accion: 'agendar', inicio: elegido };

    if (!agente?.activo || !agenda?.activo) return { accion: 'no_entendi' };

    const hoy = enZona(ahora(), cfg.TZ).dia;
    /**
     * El dia del que se viene hablando. Una hora suelta es de ese dia.
     *
     * Si el lead todavia no nombro ninguno, es el del primer horario que tiene
     * a la vista: acaba de ver esa lista y contesta sobre ella.
     */
    const enFoco = lead.dia_en_foco
      || (ofrecidos.length ? enZona(ofrecidos[0], cfg.TZ).dia : null);

    const pedido = await agente.proponerMomento({ texto: entrada, hoy, tz: cfg.TZ, enFoco });
    // No hablo de fechas: lo atiende la conversacion, no la lista.
    if (!pedido) return { accion: 'no_es_de_horarios' };

    // Queda como foco para el turno que viene: si despues tira una hora sola,
    // es de este dia. El 3-9 el lead pregunto por el viernes 11, dijo "a las
    // 10:23", y el bot le contesto sobre el jueves y volvio a la lista.
    if (pedido.dia !== lead.dia_en_foco) {
      repo.actualizarFunnel(lead.id, { dia_en_foco: pedido.dia });
    }

    const dia = instanteLocal(pedido.dia, 12, 0, cfg.TZ);
    if (pedido.consulta) return { accion: 'mostrar_dia', dia };

    const inicio = instanteLocal(pedido.dia, pedido.hora, pedido.minuto, cfg.TZ);

    const franja = revisarFranja(inicio, cfg, ahora());
    if (!franja.ok) return { accion: 'rechazar', motivo: franja.motivo, inicio, dia: inicio };

    const libre = await agenda.libreEn(inicio);
    // null es "no se pudo preguntar": ni se agenda a ciegas ni se le dice que
    // no a algo que capaz estaba libre.
    if (libre === null) return { accion: 'no_entendi', dia: inicio };
    if (libre === false) return { accion: 'ocupado', dia: inicio };

    return { accion: 'agendar', inicio };
  }

  /**
   * Lo que se le dice. Un solo lugar, y el dia que se le muestra sale de un
   * solo lado: el que el lead nombro, y si no nombro ninguno, el de los
   * horarios que ya tenia a la vista.
   */
  async function responderHorarios(lead, decision, ofrecidos) {
    const enFoco = decision.dia || (ofrecidos.length ? ofrecidos[0] : null);

    // Las horas de ESE dia, numeradas igual que en el resto del flujo nuevo.
    // Si el dia en foco ya no tiene nada libre (se lleno entre medio, o Google
    // no contesto), no hay lista que mostrar: se vuelve al paso de elegir dia.
    const horas = (enFoco && agenda?.activo) ? await agenda.slotsDelDia(enFoco, ahora()) : [];
    if (!horas.length) return ofrecerDias(lead, 'dia_no_ofrecido', '');

    repo.actualizarFunnel(lead.id, {
      horarios_ofrecidos: JSON.stringify(horas.map((d) => d.toISOString())),
      dia_en_foco: enZona(enFoco, cfg.TZ).dia,
    });

    const SITUACION = {
      mostrar_dia: 'disponibilidad_del_dia',
      rechazar: 'horario_fuera_de_franja',
      ocupado: 'horario_ocupado',
      no_entendi: 'horario_no_entendido',
    };
    const situacion = SITUACION[decision.accion];

    const extra = decision.accion === 'rechazar'
      ? MOTIVO_FRANJA[decision.motivo](cfg, decision.inicio)
      : `El día es ${nombreDia(enFoco)}.`;

    if (!await decirIA(lead, situacion, extra, listaDeHoras(horas))) return sinIA(lead, situacion);
    return S.HORARIOS_OFRECIDOS;
  }

  /**
   * Paso 1: le ofrece la lista de dias con hueco. La usa tanto la entrada a
   * MEETING_SENT (con el pitch de siempre) como cualquier "che, no te entendí
   * / ese día no es de los que hay" del paso de elegir dia (con un pitch mas
   * corto): las dos veces hay que ir a buscar los dias de nuevo y guardar el
   * estado igual.
   */
  async function ofrecerDias(lead, situacion, entrada) {
    const dias = await agenda.diasConHueco(ahora());

    // Que Google deje de contestar no puede pasar en silencio. El lead igual
    // puede agendar —cae al camino del link— pero el bot deja de hacer lo
    // unico que lo diferencia, y sin este aviso nadie se entera hasta que
    // alguien mira los logs. El token de Google es lo que sostiene todo esto:
    // si se revoca o vence, esto es lo que lo delata.
    if (!dias.length) {
      avisarAgendaCaida();
      return alEntrar(lead, S.MEETING_LINK_SENT, entrada);
    }

    const inicios = dias.map((d) => d.inicio);
    repo.actualizarFunnel(lead.id, {
      horarios_ofrecidos: JSON.stringify(inicios.map((d) => d.toISOString())),
      dia_en_foco: null,
    });

    if (!await decirIA(lead, situacion, '', listaDeDias(inicios))) return sinIA(lead, situacion);
    return S.HORARIOS_OFRECIDOS;
  }

  /** Paso 2: le muestra las horas de un dia YA elegido. */
  async function mostrarHorasDelDia(lead, diaElegido) {
    const horas = await agenda.slotsDelDia(diaElegido, ahora());
    // Se llenó justo entre que se ofreció la lista de días y que eligió uno:
    // no hay nada que mostrar de ESE día. Se vuelve a ofrecer, de nuevo.
    if (!horas.length) return ofrecerDias(lead, 'dia_no_ofrecido', '');

    repo.actualizarFunnel(lead.id, {
      horarios_ofrecidos: JSON.stringify(horas.map((d) => d.toISOString())),
      dia_en_foco: enZona(diaElegido, cfg.TZ).dia,
    });

    const extra = `El día es ${nombreDia(diaElegido)}.`;
    if (!await decirIA(lead, 'disponibilidad_del_dia', extra, listaDeHoras(horas))) {
      return sinIA(lead, 'disponibilidad_del_dia');
    }
    return S.HORARIOS_OFRECIDOS;
  }

  /**
   * Paso 1, el turno: elige un dia de la lista, en codigo (numero, nombre del
   * dia, o fecha). Si no se entiende nada, o si nombro un dia real que no es
   * ninguno de los ofrecidos, se lo dice y se vuelve a ofrecer.
   */
  async function decidirDia(lead, entrada, diasOfrecidos) {
    const elegido = elegirDiaPorCodigo(entrada, diasOfrecidos, cfg.TZ, ahora());
    if (elegido) return mostrarHorasDelDia(lead, elegido);

    if (nombroAlgunDia(entrada)) return ofrecerDias(lead, 'dia_no_ofrecido', entrada);

    // Un numero de lista que no matcheo ninguna opcion ("5" con solo dos
    // dias ofrecidos) tampoco es conversacion: es que no se entendio bien.
    if (/^(?:opcion\s*)?\d{1,2}\.?$/.test(entrada.trim())) {
      return ofrecerDias(lead, 'dia_no_entendido', entrada);
    }

    // No habla de dias: lo atiende la conversacion. "¿cuánto sale?", "no
    // puedo esos días" y similares caen aca, igual que en el flujo de horas.
    return CONVERSAR;
  }

  /**
   * Paso 2, el turno: elige una hora del dia ya elegido, en codigo (numero
   * de lista, o una hora que coincide con alguna ofrecida). Si nombra otro
   * dia, se cambia de dia. Si no, el modelo queda de respaldo —entiende "la
   * primera", "a la una y media", y frases que no estan en la lista— para lo
   * que el codigo no pudo resolver solo.
   */
  async function decidirHora(lead, entrada, horasOfrecidas) {
    const elegida = elegirHoraPorCodigo(entrada, horasOfrecidas, cfg.TZ);

    let decision;
    if (elegida) {
      decision = { accion: 'agendar', inicio: elegida };
    } else if (nombroAlgunDia(entrada)) {
      // Quiere otro dia. Se resuelve en codigo, igual que el paso 1: se
      // vuelve a buscar que dias hay y se elige de ahi, no del dia de hoy.
      const dias = await agenda.diasConHueco(ahora());
      const inicios = dias.map((d) => d.inicio);
      const otroDia = elegirDiaPorCodigo(entrada, inicios, cfg.TZ, ahora());
      if (otroDia) return mostrarHorasDelDia(lead, otroDia);
      return ofrecerDias(lead, 'dia_no_ofrecido', entrada);
    } else {
      decision = await decidirSobreHorarios(lead, entrada, horasOfrecidas);
    }

    // No habla de horarios: lo atiende la conversacion. El 3-9 el lead
    // corrigio "quiero página web no ecommerce" mientras elegia horario y
    // recibio la lista de horarios, con la correccion perdida.
    if (decision.accion === 'no_es_de_horarios') return CONVERSAR;

    if (decision.accion !== 'agendar') {
      return responderHorarios(lead, decision, horasOfrecidas);
    }

    const elegido = decision.inicio;

    const r = await agenda.reservar({
      inicio: elegido,
      nombre: lead.business_name || lead.nombre,
      telefono: lead.telefono,
      resumen: lead.needs || lead.necesidad || '',
    });

    if (r.motivo === 'ocupado') {
      // Se lo tomaron entre medio: se muestra lo que queda de ESE dia, no se
      // salta a otro — el lead ya eligio el dia, lo que cambio es la hora.
      const diaEnFoco = instanteLocal(lead.dia_en_foco, 12, 0, cfg.TZ);
      const horasDeVuelta = await agenda.slotsDelDia(diaEnFoco, ahora());
      if (horasDeVuelta.length) {
        repo.actualizarFunnel(lead.id, {
          horarios_ofrecidos: JSON.stringify(horasDeVuelta.map((d) => d.toISOString())),
        });
        if (!await decirIA(lead, 'horario_ocupado', '', listaDeHoras(horasDeVuelta))) {
          return sinIA(lead, 'horario_ocupado');
        }
        return S.HORARIOS_OFRECIDOS;
      }
      return ofrecerDias(lead, 'dia_no_ofrecido', entrada);
    }

    if (!r.ok) {
      // Falló la agenda. No se le promete nada: va a una persona.
      derivar(lead, 'agenda');
      return alEntrar(lead, S.HUMAN_QUEUED, entrada);
    }

    servicioReunion(lead, r);
    return alEntrar(repo.leadPorId(lead.id), S.SCHEDULED, entrada);
  }

  /** Deja la reunion registrada: recordatorios, aviso al AM y estado. */
  function servicioReunion(lead, r) {
    repo.registrarReunion(lead.id, {
      meetingTime: r.inicio.toISOString(),
      meetingUrl: r.meetUrl,
      ahoraIso: ahora().toISOString(),
    });
    repo.actualizarFunnel(lead.id, { meeting_event_id: r.eventId || null });

    /**
     * Los recordatorios los programaba solo servicioLeads.registrarReunion, que
     * es por donde entran las de Calendly y las que carga el CRM. Cuando el bot
     * agenda por su cuenta pasa por repo.registrarReunion —el de abajo— y
     * quedaban sin programar: el 3-9 en produccion, Juanchi tenia sus dos jobs
     * esperando y el lead que agendo el bot no tenia ninguno.
     */
    recordatorios?.(repo.leadPorId(lead.id));

    for (const am of cfg.amPhones) {
      cola.encolar({
        to: am,
        texto: plantillas.avisoReunionAgendada(repo.leadPorId(lead.id), {
          cuando: new Intl.DateTimeFormat('es-UY', {
            timeZone: cfg.TZ, weekday: 'long', day: 'numeric', month: 'long',
            hour: '2-digit', minute: '2-digit', hour12: false,
          }).format(r.inicio),
          link: r.meetUrl,
        }),
        kind: 'am_notice',
        leadId: lead.id,
      });
    }
    logger?.info({ leadId: lead.id, cuando: r.inicio.toISOString() }, 'reunion agendada por el bot');
  }

  /**
   * No es un cliente posible: se le agradece y se deja de programarle cosas.
   *
   * No se le arma nada nuevo —ni seguimiento, ni pausa, ni derivacion por
   * abandono, que ya lo excluye—. Descalificar es dejar de hacer, no hacer una
   * cosa mas.
   *
   * Se revierte solo: si vuelve a escribir, el bot lo atiende y sale de aca.
   * Es a proposito que sea tan facil salir — equivocarse para este lado cuesta
   * un cliente, y no se puede depender de que alguien mire el CRM.
   */
  function descartar(lead, motivo, ahoraIso = ahora().toISOString()) {
    repo.actualizarFunnel(lead.id, {
      fsm_state: S.DISQUALIFIED,
      fsm_retries: 0,
      no_cliente_motivo: motivo,
      no_cliente_desde: ahoraIso,
    });
    repo.cancelarJobs(lead.id, 'followup');
    repo.cancelarJobs(lead.id, 'nurture');
    repo.cancelarJobs(lead.id, 'abandono');
    logger?.info({ leadId: lead.id, motivo }, 'lead descalificado: no es un cliente posible');
    return S.DISQUALIFIED;
  }

  /**
   * El lead dijo que no es el momento: queda en pausa y se le escribe cuando
   * dijo, en vez de insistirle a las 72 horas.
   *
   * El mensaje que sale es el que el modelo YA escribio en este turno. No se le
   * pide otro: seria una segunda llamada en el mismo turno, y la latencia de la
   * respuesta es justo lo que se acaba de bajar de 15 segundos a 4.
   */
  function pausar(lead, texto, aplaza, frase) {
    decir(lead, texto);

    const cuando = cuandoVolver(aplaza, ahora(), cfg);
    repo.actualizarFunnel(lead.id, {
      fsm_state: S.NURTURE,
      fsm_retries: 0,
      nurture_desde: ahora().toISOString(),
      nurture_motivo: frase || aplaza,
    });
    repo.programarJob(lead.id, 'nurture', cuando.toISOString());
    // El seguimiento de las 72 horas ya no corresponde: dijo cuando volver.
    repo.cancelarJobs(lead.id, 'followup');

    logger?.info({ leadId: lead.id, aplaza, vuelve: cuando.toISOString() }, 'lead en pausa');
    return S.NURTURE;
  }

  /**
   * La IA no pudo escribir y hay alguien esperando. No se disimula con un texto
   * armado: se lo pasa a una persona. Es la unica salida honesta sin el modelo.
   */
  function sinIA(lead, motivo) {
    logger?.warn({ leadId: lead.id, motivo }, 'la IA no respondio, va a una persona');
    derivar(lead, 'sin_ia');
    repo.actualizarFunnel(lead.id, { human_requested: 1, fsm_state: S.HUMAN_QUEUED });
    decir(lead, textos.SIN_IA);
    return S.HUMAN_QUEUED;
  }

  async function alEntrar(lead, estado, entrada) {
    switch (estado) {
      /**
       * Ya se sabe quien es, a que se dedica y que necesita. Con eso alcanza:
       * no hay filtro de score.
       *
       * Antes se le pedian siete datos —presupuesto, cuanta gente trabaja,
       * colores— y un puntaje decidia si merecia una reunion. La calificacion
       * ahora pasa a la reunion misma, que es literalmente lo que es: un
       * diagnostico. Filtrar antes con datos que el lead da a desgano —el
       * presupuesto sobre todo, que casi nadie contesta bien por WhatsApp—
       * dejaba afuera gente que en una llamada de treinta minutos se resolvia
       * en dos preguntas.
       */
      case S.SCORED: {
        const fresco = repo.leadPorId(lead.id);
        avisarDesenlace(lead.id, 'meeting');
        sombra?.alCalificar(fresco);
        return alEntrar(fresco, S.MEETING_SENT, entrada);
      }

      case S.MEETING_SENT: {
        // Con AGENDA_OFRECE_HORARIOS se le muestra primero una lista de dias
        // y, cuando elige uno, las horas de ESE dia. Apagado —que es como
        // esta— se le manda el link de Calendly y se agenda solo: el
        // formulario le pregunta empresa, que necesita y telefono, y esos
        // datos despues sirven para preparar la reunion.
        //
        // La agenda sigue conectada igual: se la usa para leer el calendario y
        // enterarse de quien agendo.
        const conAgenda = Boolean(agenda?.activo && cfg.AGENDA_OFRECE_HORARIOS);

        if (conAgenda) return ofrecerDias(lead, 'oferta_con_horarios', entrada);

        // El camino del link. Se va derecho: MEETING_LINK_SENT ya manda el
        // link al entrar, y su mensaje explica el proceso entero. Preguntarle
        // antes "¿te sirve?" es un mensaje de mas para llegar a lo mismo.
        return alEntrar(lead, S.MEETING_LINK_SENT, entrada);
      }

      /**
       * Eligio —o no— un dia, y despues una hora de ese dia.
       *
       * `lead.dia_en_foco` distingue los dos pasos: sin el, `horarios_ofrecidos`
       * son los DIAS que se le ofrecieron (paso 1); con el, son las HORAS de
       * ese dia (paso 2). La lista y la eleccion las resuelve el codigo —un
       * numero, el nombre del dia o de la hora—; el modelo queda de respaldo
       * solo para lo que el codigo no entiende.
       */
      case S.HORARIOS_OFRECIDOS: {
        const ofrecidos = leerHorarios(lead);
        if (!ofrecidos.length) return alEntrar(lead, S.MEETING_SENT, entrada);

        if (!lead.dia_en_foco) return decidirDia(lead, entrada, ofrecidos);
        return decidirHora(lead, entrada, ofrecidos);
      }

      case S.MEETING_INFO:
        if (!await decirIA(lead, 'mas_info')) return sinIA(lead, 'mas_info');
        return estado;

      /**
       * Se llega aca la primera vez para mandar el link, y despues cada vez que
       * el lead escribe teniendolo. La diferencia importa: lead.fsm_state es
       * todavia el estado anterior, asi que se sabe si recien entro o si esta
       * insistiendo.
       */
      case S.MEETING_LINK_SENT: {
        if (lead.fsm_state !== S.MEETING_LINK_SENT) {
          if (!await decirIA(lead, 'link_reunion')) return sinIA(lead, 'link_reunion');
          repo.actualizarFunnel(lead.id, { fsm_retries: 0 });
          return estado;
        }

        if (YA_AGENDO.test(entrada)) {
          // No se cancela el follow-up: la verdad la trae el webhook de
          // Calendly. Si de veras reservo, ese lo cancela; si se confundio, el
          // follow-up es exactamente lo que hay que mandarle.
          if (!await decirIA(lead, 'ya_agendo')) return sinIA(lead, 'ya_agendo');
          repo.actualizarFunnel(lead.id, { fsm_retries: 0 });
          return estado;
        }

        // Ya tiene el link y sigue escribiendo: quiere otra cosa. Se le
        // pregunta una vez y despues va a una persona.
        const insistencias = (lead.fsm_retries || 0) + 1;
        if (insistencias >= 2) {
          derivar(lead, 'post_oferta');
          return alEntrar(lead, S.HUMAN_QUEUED, entrada);
        }
        repo.actualizarFunnel(lead.id, { fsm_retries: insistencias });
        if (!await decirIA(lead, 'ya_tiene_link')) return sinIA(lead, 'ya_tiene_link');
        return estado;
      }

      case S.SCHEDULED: {
        // Solo al confirmarse. Repetirlo en cada mensaje posterior es el loop
        // que tenia MEETING_SENT.
        if (lead.fsm_state === S.SCHEDULED) return estado;

        const cuando = lead.meeting_time
          ? new Intl.DateTimeFormat('es-UY', {
            timeZone: cfg.TZ, weekday: 'long', day: 'numeric', month: 'long',
            hour: '2-digit', minute: '2-digit', hour12: false,
          }).format(new Date(lead.meeting_time))
          : '';
        const extra = cuando
          ? `Quedó agendada para el ${cuando}. El link de la videollamada es ${lead.meeting_url || '(sin link)'}`
          : '';

        await decirIA(lead, lead.meeting_event_id ? 'reunion_agendada' : 'reunion_confirmada', extra);
        return estado;
      }

      case S.HUMAN_QUEUED:
        if (!lead.human_requested) {
          repo.actualizarFunnel(lead.id, { human_requested: 1 });
          if (!await decirIA(lead, 'derivacion')) decir(lead, textos.SIN_IA);
          logger?.info({ leadId: lead.id, estadoPrevio: lead.fsm_state }, 'lead pasa a una persona');
        }
        return estado;

      case S.OPT_OUT:
        repo.actualizarFunnel(lead.id, { opt_out: 1 });
        repo.actualizarLead(lead.id, { status: 'closed' });
        repo.cancelarJobs(lead.id, 'followup');
        // Este es fijo a proposito: tiene que salir aunque no haya IA, y no se
        // le da al modelo la chance de intentar retenerlo.
        decir(lead, textos.OPT_OUT);
        return estado;

      case S.NURTURE:
        // lead.fsm_state es todavia el estado anterior. Al que califico y dijo
        // "todavia no" no se le contesta que su caso "se va a mirar a ver si
        // encaja" — ya encajo, lo que falta es el momento.
        await decirIA(lead, lead.fsm_state === S.MEETING_INFO ? 'no_ahora' : 'nurture');
        return estado;

      case S.DISQUALIFIED:
        await decirIA(lead, 'descartado');
        return estado;

      default:
        return estado;
    }
  }

  return {
    /** Marcar a mano desde el CRM que no es un cliente posible. */
    descartar(leadId, motivo) {
      const lead = repo.leadPorId(leadId);
      if (!lead) return null;
      return descartar(lead, motivo || 'a mano');
    },

    /**
     * El lead se fue de la conversacion: lo levanta una persona.
     *
     * Lo dispara el scheduler, no un mensaje entrante — es justamente la
     * ausencia de mensaje lo que lo activa—, asi que entra por aca en vez de
     * por procesar().
     *
     * Le avisa al agente comercial con el contexto y los ultimos mensajes, y
     * al lead le dice que lo van a contactar. Lo segundo importa: sin eso, del
     * otro lado la conversacion simplemente se corta.
     *
     * @returns {Promise<boolean>} false si la IA no pudo escribirle al lead, y
     *   entonces conviene reintentar mas tarde en vez de dejarlo a medias.
     */
    async derivarPorAbandono(leadId) {
      const lead = repo.leadPorId(leadId);
      if (!lead) return true;

      if (!await decirIA(lead, 'derivado_por_abandono')) return false;

      derivar(lead, 'abandono');
      repo.actualizarFunnel(lead.id, { human_requested: 1, fsm_state: S.HUMAN_QUEUED });
      return true;
    },

    /**
     * Procesa un mensaje entrante del lead.
     * @returns {string|null} el estado en que quedo, o null si se ignoro.
     */
    async procesar(leadId, textoCrudo, { porAudio = false } = {}) {
      const lead = repo.leadPorId(leadId);
      if (!lead) return null;

      const entrada = normalizar(textoCrudo);

      // Dado de baja: silencio absoluto.
      if (lead.opt_out) return null;

      const global = palabraGlobal(entrada);

      // Con un humano a cargo, el bot no vuelve solo. Lo devuelve el CRM con
      // POST /api/leads/phone/:phone/release.
      if (lead.human_requested) return null;

      if (global) {
        if (global === S.HUMAN_QUEUED) derivar(lead, 'pedido');
        return this._transicionar(lead, entrada, global);
      }

      /**
       * Le escribio a una persona, no al bot: el chat pasa entero a esa
       * persona, sin presentarse, sin preguntar nada y sin prometer nada.
       *
       * "Una reunion que no tenemos" solo cuenta mientras el bot no le ofrecio
       * ninguna: al que esta eligiendo horario, "¿podemos pasar la llamada al
       * martes?" le contesta el embudo, que es quien tiene los horarios.
       */
      const paraAlguien = paraUnaPersona(textoCrudo, {
        nombres: cfg.nombresEquipo || [],
        tieneReunion: Boolean(lead.meeting_booked_at || lead.horarios_ofrecidos)
          || OFRECIO_REUNION.has(lead.fsm_state),
      });
      if (paraAlguien) {
        decir(lead, textos.paraUnaPersona(paraAlguien.nombre));
        derivar(lead, paraAlguien.motivo);
        repo.actualizarFunnel(lead.id, { human_requested: 1, fsm_state: S.HUMAN_QUEUED });
        logger?.info(
          { leadId: lead.id, motivo: paraAlguien.motivo, nombre: paraAlguien.nombre },
          'le escribio a una persona del equipo: el chat pasa a esa persona'
        );
        return S.HUMAN_QUEUED;
      }

      /**
       * Ya esta agendado y esto es solo un acuse: no hace falta contestar.
       *
       * Despues de confirmar, Patricia mando "Ok perfecto", "Ok", y al
       * recordatorio "Ok bien", "Perfecto si" — y el bot le repitio la fecha
       * de la reunion las cuatro veces. El mensaje ya quedo registrado (mas
       * arriba, en leads.js): no responder no es lo mismo que no escuchar.
       */
      if (lead.meeting_booked_at && lead.fsm_state === S.SCHEDULED && esAcuse(textoCrudo)) {
        return lead.fsm_state;
      }

      // Casos que el superprompt manda derivar sin excepcion. Van antes de la
      // IA: aplican en cualquier punto y no se delegan.
      const disparador = detectar(textoCrudo);
      if (disparador) {
        if (disparador.motivo === 'queja') {
          // Una queja no la escribe el modelo si puede evitarse, pero tampoco
          // se calla: si no hay IA, el texto de derivacion alcanza.
          derivar(lead, 'queja');
          return this._transicionar(lead, entrada, S.HUMAN_QUEUED);
        }

        if (disparador.motivo === 'facturacion') {
          if (!await decirIA(lead, 'facturacion')) decir(lead, textos.SIN_IA);
          derivar(lead, 'facturacion');
          return this._transicionar(lead, entrada, S.HUMAN_QUEUED);
        }

        // Precio: la primera vez se contesta el criterio, sin dar numeros. Si
        // vuelve a preguntar es que no se conformo, y ahi va a un humano.
        //
        // Salvo que la primera respuesta nunca le haya llegado. Ahi repetir la
        // pregunta no es insistir: es no haber recibido nada. Paso de verdad
        // —el tope por hora freno la respuesta y el lead pregunto de nuevo a
        // los ocho minutos— y el bot lo derivo por insistente cuando desde su
        // lado habia preguntado una sola vez.
        const sinContestar = repo.quedoSinRespuesta(lead.id);
        const consultas = sinContestar
          ? (lead.consultas_precio || 1)
          : (lead.consultas_precio || 0) + 1;
        repo.actualizarFunnel(lead.id, { consultas_precio: consultas });

        if (sinContestar) {
          logger?.info({ leadId: lead.id }, 'repitio la pregunta del precio porque no le contestamos: no cuenta como insistir');
        }

        if (consultas === 1 || sinContestar) {
          // Si la IA no puede, sale el texto del superprompt tal cual: es el
          // unico mensaje donde las palabras exactas estan dictadas.
          if (!await decirIA(lead, 'precio')) decir(lead, textos.PRECIO);
          return lead.fsm_state || S.NEW;
        }
        derivar(lead, 'precio');
        return this._transicionar(lead, entrada, S.HUMAN_QUEUED);
      }

      const actual = lead.fsm_state || S.NEW;
      const califica = FASE_CALIFICACION.has(actual);
      // Al que esta en pausa se le conversa, pero no se le cierra: sus datos ya
      // estan completos, asi que sin esto el primer mensaje que mande lo
      // llevaria derecho a recibir el link de nuevo. Justo al que pidio tiempo.
      const puedeCerrar = califica && actual !== S.NURTURE && actual !== S.DISQUALIFIED;

      if (agente?.activo && (califica || FASE_CIERRE.has(actual))) {
        const r = await agente.responder(
          lead, textoCrudo,
          repo.ultimosMensajes(lead.id, 20, lead.conversacion_desde),
          actual,
          { porAudio },
        );
        if (r) return this._conversar(lead, entrada, r, { actual, califica, puedeCerrar, porAudio });
        return sinIA(lead, 'conversacion');
      }

      // Sin IA no hay embudo que lo atienda: la unica salida es una persona.
      if (!agente?.activo) return sinIA(lead, 'sin_clave');

      const mapa = TRANSICIONES[actual] || {};
      const destino = await this._transicionar(lead, entrada, mapa['*'] || S.CONVERSANDO);

      /**
       * El turno no era de horarios. Lo atiende la conversacion, como
       * cualquier otro: el lead sigue eligiendo horario, pero lo que dijo
       * ahora es otra cosa —una correccion, una duda, un dato— y merece que se
       * lo escuche en vez de recibir la lista de nuevo.
       */
      if (destino === CONVERSAR) {
        if (!agente?.activo) return sinIA(lead, 'sin_clave');
        const r = await agente.responder(
          lead, textoCrudo,
          repo.ultimosMensajes(lead.id, 20, lead.conversacion_desde),
          actual,
          { porAudio },
        );
        if (!r) return sinIA(lead, 'conversacion');
        await this._conversar(lead, entrada, r, {
          actual, califica: false, puedeCerrar: false, porAudio,
        });
        // Sigue eligiendo horario: la conversacion no lo saca de ahi.
        repo.actualizarFunnel(lead.id, { fsm_state: S.HORARIOS_OFRECIDOS });
        return S.HORARIOS_OFRECIDOS;
      }

      return destino;
    },

    /**
     * Un turno conducido por la IA: guarda lo que extrajo y contesta. Cuando ya
     * no falta ningun dato, el cierre lo hace el codigo — ofrecer la reunion
     * sale del score, no de lo que le parezca al modelo.
     */
    async _conversar(lead, entrada, { texto, datos, aplaza, aplazaFrase, queQuiere }, { actual, califica, puedeCerrar, porAudio = false }) {
      if (Object.keys(datos).length) {
        guardarCampos(lead.id, datos, { porAudio });
        logger?.info({ leadId: lead.id, campos: Object.keys(datos) }, 'la IA extrajo datos');
      }

      // El modelo clasifica siempre; que decida o no lo dice la config. Con la
      // decision apagada igual queda anotado, y eso es lo que permite mirar si
      // acierta antes de dejarlo descalificar solo.
      /**
       * El modelo se puso a agendar por su cuenta. No puede.
       *
       * La reserva la hace el lead en Calendly; el bot no tiene con que tomar
       * un horario. Cuando el embudo no cierra —porque falta un dato y el lead
       * lo esquiva— el modelo improvisa y termina prometiendo una reunion que
       * no existe. Paso: le pidio dia y hora, dijo "agendo la videollamada para
       * el martes a las 10 de la noche" y despues se lo confirmo. No habia
       * nada, y esa persona iba a esperar sola.
       *
       * Se tira lo que escribio y se le manda el link, que es lo unico que de
       * verdad lleva a una reunion.
       */
      /**
       * Corre SIEMPRE, tenga o no prendidos los horarios reales.
       *
       * Estaba detras de `!cfg.AGENDA_OFRECE_HORARIOS`, con el razonamiento de
       * que con los horarios prendidos el embudo agenda bien y esto sobra. Vale
       * solo si el lead LLEGA a la etapa de horarios. El 3-9 no llego —dijo que
       * su negocio no tenia nombre todavia, business_name quedo vacio y el
       * descubrimiento no cerro nunca— y el modelo se puso a negociar fechas
       * por su cuenta: "lunes a las 12 de la noche anotado", fuera de la franja
       * y sin nada en el calendario.
       *
       * Prender los horarios reales habia apagado la unica proteccion contra
       * exactamente eso.
       */
      if (!lead.meeting_booked_at) {
        const promesa = prometeAgendar(texto);
        if (promesa) {
          logger?.warn(
            { leadId: lead.id, promesa, texto },
            'la IA se puso a agendar sola: se descarta y se manda el link'
          );
          // Por SCORED y no derecho al link: es el unico lugar donde se le
          // avisa al equipo que hay un lead con reunion ofrecida. Yendo
          // directo, el lead recibia el link y del lado de adentro no se
          // enteraba nadie.
          return this._transicionar(lead, entrada, S.SCORED);
        }
      }

      if (queQuiere) {
        repo.actualizarFunnel(lead.id, { no_cliente_motivo: queQuiere });

        if (cfg.descalificaSolo.includes(queQuiere)) {
          // El mensaje se pide de nuevo con la situacion 'descartado' en vez de
          // usar el que el modelo ya escribio. Es la unica vez que se paga una
          // segunda llamada, y vale: el texto que trae lo escribio siguiendo el
          // objetivo de la etapa, o sea pidiendole el nombre del negocio a
          // alguien que acaba de mandar un CV.
          if (!await decirIA(lead, 'descartado', MOTIVO[queQuiere] || '')) {
            return sinIA(lead, 'descartado');
          }
          return descartar(repo.leadPorId(lead.id), queQuiere);
        }

        logger?.info(
          { leadId: lead.id, queQuiere },
          'el modelo lo ve como no-cliente, pero ese motivo lo decide una persona'
        );
      }

      if (aplaza) {
        // Ya tenia reunion agendada: no se pausa, va a una persona.
        //
        // Ponerlo en pausa dejaria vivos el evento del calendario y sus dos
        // recordatorios, y al que acaba de decir que no puede le llegaria
        // "mañana tenes la videollamada". Mover una reunion de verdad —y
        // avisarle al que la iba a dar— es de una persona: el bot no tiene con
        // que cancelarla.
        if (lead.meeting_booked_at) {
          decir(lead, texto);
          derivar(lead, 'reprograma');
          repo.actualizarFunnel(lead.id, { human_requested: 1, fsm_state: S.HUMAN_QUEUED });
          return S.HUMAN_QUEUED;
        }
        return pausar(lead, texto, aplaza, aplazaFrase);
      }

      // Volvio por su cuenta antes de tiempo: el mensaje programado ya no va.
      if (actual === S.NURTURE) repo.cancelarJobs(lead.id, 'nurture');

      if (puedeCerrar) {
        const fresco = repo.leadPorId(lead.id);
        if (!agente.faltantes(fresco).length) {
          /**
           * El ultimo control antes de darlo por calificado, con
           * JEV_MODO=decide.
           *
           * El embudo cierra cuando los tres campos estan llenos, pero llenos
           * no es lo mismo que ciertos: el 11-9 "Webs" —lo que el lead HACE—
           * quedo guardado como lo que necesitaba, y el 12-9 un "Sii" a una
           * pregunta de cinco opciones tambien cerro. Jev vuelve a leer la
           * conversacion entera y dice que necesita.
           *
           * Null —Jev callado, o con menos confianza que el umbral— es seguir
           * como siempre. Jev nunca frena nada por su cuenta.
           */
          const veredicto = await sombra?.decidirNecesidad?.(fresco);

          if (veredicto?.pisar) {
            repo.actualizarFunnel(fresco.id, { business_type: veredicto.pisar });
            logger?.info(
              { leadId: fresco.id, bot: fresco.business_type ?? null, jev: veredicto.pisar },
              'jev corrigio la necesidad antes de calificar'
            );
            return this._transicionar(repo.leadPorId(fresco.id), entrada, S.SCORED);
          }

          /**
           * No se entiende que necesita: no se lo da por calificado.
           *
           * Se le pregunta de nuevo UNA vez —la marca en la base es lo que lo
           * hace una y no un loop— y si sigue sin quedar claro lo atiende una
           * persona. Es lo que tendria que haber pasado el 11 y el 12/9 en vez
           * de ofrecerle una reunion sin saber para que.
           */
          if (veredicto?.repreguntar) {
            if (fresco.necesidad_repreguntada) {
              logger?.info({ leadId: fresco.id }, 'sigue sin quedar claro que necesita: va a una persona');
              derivar(fresco, 'necesidad');
              return this._transicionar(fresco, entrada, S.HUMAN_QUEUED);
            }

            repo.actualizarFunnel(fresco.id, { business_type: null, necesidad_repreguntada: 1 });
            const conMarca = repo.leadPorId(fresco.id);
            if (!await decirIA(conMarca, 'necesidad_confusa')) return sinIA(conMarca, 'necesidad_confusa');
            repo.actualizarFunnel(fresco.id, { fsm_state: S.CONVERSANDO, fsm_retries: 0 });
            return S.CONVERSANDO;
          }

          return this._transicionar(fresco, entrada, S.SCORED);
        }
      }

      decir(lead, texto);

      // Quien decide mandar el link es la IA —lo tiene en las instrucciones de
      // su etapa— pero quien se entera de que salio tiene que ser el codigo.
      // Si no, nadie sabe que el lead ya lo tiene y se lo puede volver a
      // mandar indefinidamente, que es justo el loop que habia antes.
      const mandoElLink = CALENDLY && texto.includes(CALENDLY);
      // Con `califica` y no con `puedeCerrar`: son distintos desde que existe la
      // pausa. Al que esta en NURTURE no se le cierra —no se le vuelve a
      // empujar el link— pero si vuelve a escribir sale de la pausa, y con
      // puedeCerrar se quedaba adentro para siempre.
      const destino = mandoElLink ? S.MEETING_LINK_SENT : (califica ? S.CONVERSANDO : actual);
      repo.actualizarFunnel(lead.id, { fsm_state: destino, fsm_retries: 0 });
      return destino;
    },

    async _transicionar(lead, entrada, destino) {
      const final = await alEntrar(repo.leadPorId(lead.id), destino, entrada);
      // El sentinela no es un estado: dice que el turno lo atiende la
      // conversacion. Guardarlo dejaria al lead en un fsm_state inexistente.
      if (final === CONVERSAR) return CONVERSAR;
      repo.actualizarFunnel(lead.id, { fsm_state: final });

      // El CRM se entera cuando el lead califica o pide un humano — los dos
      // momentos en que alguien del equipo tiene que hacer algo.
      //
      // MEETING_LINK_SENT esta en la lista desde que el embudo cierra de un
      // saque: antes paraba en MEETING_SENT y despues, con un "dale" del lead,
      // pasaba al link. Ahora encadena los dos en el mismo turno, asi que el
      // estado final es siempre el del link y esta condicion no se cumplia
      // nunca. El CRM dejo de enterarse de los leads calificados sin que nadie
      // lo notara.
      if (crmNotify && AVISAR_AL_CRM.has(final)) {
        await crmNotify.leadCalifico(lead.id);
      }
      return final;
    },
  };
}

module.exports = { crearEmbudo, normalizar, FASE_CALIFICACION, FASE_CIERRE, AVISAR_AL_CRM };
