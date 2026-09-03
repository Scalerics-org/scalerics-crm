'use strict';

/**
 * Del formato de JavaScript al de SQLite. TODA fecha que el codigo le pase a
 * una consulta tiene que pasar por aca.
 *
 * Las columnas guardan `datetime('now')` y quedan como "2026-08-19 17:24:03".
 * El codigo, en cambio, produce fechas con `toISOString()`, que quedan como
 * "2026-08-19T16:52:03.966Z". SQLite compara las fechas de texto CARACTER POR
 * CARACTER, y en la posicion 10 una tiene un espacio (32) y la otra una T (84).
 * El espacio va antes, asi que cualquier comparacion entre dos fechas del mismo
 * dia da falso, sin error y sin aviso.
 *
 * Costo dos cosas en produccion. La conversacion con el lead: el corte de
 * `conversacion_desde` no dejaba pasar ningun mensaje, la IA recibia cero
 * historial y contestaba cada mensaje como si fuera el primero —le pregunto el
 * nombre del negocio dos veces seguidas, palabra por palabra, despues de que se
 * lo dijeran—. Y el limite de envios por hora, que contaba siempre cero y por
 * lo tanto nunca frenaba nada.
 *
 * Los dos fallaban en silencio, que es lo peor de este bug: no rompe, miente.
 */
function aFechaSqlite(v) {
  if (v === null || v === undefined || v === '') return null;
  const s = String(v);
  // Ya viene en formato SQLite: se recorta por si trae fracciones de segundo.
  if (!s.includes('T')) return s.slice(0, 19);
  const d = new Date(s);
  // Algo que no es una fecha se deja pasar tal cual: que falle la consulta y se
  // vea, en vez de convertirlo en una fecha inventada.
  if (Number.isNaN(d.getTime())) return s;
  return d.toISOString().replace('T', ' ').slice(0, 19);
}

/**
 * Acceso a datos. Todas las consultas viven aca; ningun otro modulo escribe SQL.
 */
function crearRepo(db) {
  const stmt = {
    insertLead: db.prepare(`
      INSERT INTO leads (external_id, nombre, rubro, rubro_norm, telefono, necesidad, origen, status)
      VALUES (@external_id, @nombre, @rubro, @rubro_norm, @telefono, @necesidad, @origen, @status)
    `),
    leadPorExternalId: db.prepare('SELECT * FROM leads WHERE external_id = ?'),
    leadPorId: db.prepare('SELECT * FROM leads WHERE id = ?'),
    leadPorTelefono: db.prepare('SELECT * FROM leads WHERE telefono = ? ORDER BY id DESC LIMIT 1'),
    listarLeads: db.prepare('SELECT * FROM leads ORDER BY id DESC LIMIT ?'),
    buscarPorNombre: db.prepare("SELECT * FROM leads WHERE nombre LIKE ? ORDER BY id DESC LIMIT 20"),

    insertMensaje: db.prepare(`
      INSERT INTO messages (lead_id, direction, kind, body, provider, provider_msg_id, status, error, destino, media)
      VALUES (@lead_id, @direction, @kind, @body, @provider, @provider_msg_id, @status, @error, @destino, @media)
    `),
    mensajesDeLead: db.prepare('SELECT * FROM messages WHERE lead_id = ? ORDER BY id ASC'),

    insertJob: db.prepare(`
      INSERT INTO jobs (lead_id, type, run_at) VALUES (?, ?, ?)
      ON CONFLICT DO NOTHING
    `),
    jobsVencidos: db.prepare(`
      SELECT * FROM jobs WHERE status = 'pending' AND run_at <= ? ORDER BY run_at ASC
    `),
    marcarJob: db.prepare('UPDATE jobs SET status = ?, last_error = ? WHERE id = ?'),
    reprogramarJob: db.prepare("UPDATE jobs SET run_at = ?, attempts = attempts + 1 WHERE id = ?"),
    cancelarJobs: db.prepare(
      "UPDATE jobs SET status = 'cancelled' WHERE lead_id = ? AND type = ? AND status = 'pending'"
    ),

    insertSendLog: db.prepare('INSERT INTO send_log (telefono, first_contact) VALUES (?, ?)'),
    contarEnviosDesde: db.prepare('SELECT COUNT(*) AS n FROM send_log WHERE sent_at >= ?'),
    contarNuevosDesde: db.prepare(
      'SELECT COUNT(*) AS n FROM send_log WHERE sent_at >= ? AND first_contact = 1'
    ),
    yaContactado: db.prepare('SELECT 1 FROM send_log WHERE telefono = ? LIMIT 1'),
  };

  return {
    db,

    crearLead(datos) {
      const info = stmt.insertLead.run({
        external_id: datos.external_id ?? null,
        nombre: datos.nombre,
        rubro: datos.rubro ?? null,
        rubro_norm: datos.rubro_norm ?? null,
        telefono: datos.telefono,
        necesidad: datos.necesidad ?? null,
        origen: datos.origen ?? null,
        status: datos.status ?? 'new',
      });
      return stmt.leadPorId.get(info.lastInsertRowid);
    },

    leadPorExternalId: (id) => stmt.leadPorExternalId.get(id),
    leadPorId: (id) => stmt.leadPorId.get(id),
    leadPorTelefono: (tel) => stmt.leadPorTelefono.get(tel),
    listarLeads: (limite = 200) => stmt.listarLeads.all(limite),
    buscarPorNombre: (nombre) => stmt.buscarPorNombre.all(`%${nombre}%`),

    /** Solo se actualizan campos conocidos: evita que un payload arbitrario toque columnas. */
    actualizarLead(id, campos) {
      const permitidos = [
        'status', 'welcomed_at', 'replied_at', 'followup_sent_at',
        'am_notified_at', 'humano_avisado_at', 'rubro_norm', 'nombre', 'necesidad',
      ];
      const set = Object.keys(campos).filter((k) => permitidos.includes(k));
      if (!set.length) return stmt.leadPorId.get(id);
      const sql = `UPDATE leads SET ${set.map((k) => `${k} = ?`).join(', ')} WHERE id = ?`;
      db.prepare(sql).run(...set.map((k) => campos[k]), id);
      return stmt.leadPorId.get(id);
    },

    /** Campos del embudo. Lista blanca igual que actualizarLead. */
    actualizarFunnel(id, campos) {
      const permitidos = [
        'fsm_state', 'fsm_retries', 'opt_out', 'human_requested',
        'bot_enabled', 'bot_pausado_hasta',
        'business_name', 'business_name_por_audio', 'business_type', 'budget', 'team_size',
        'colors', 'instagram_web', 'needs', 'rubro', 'rubro_norm',
        'score', 'priority', 'score_reason', 'meeting_url', 'meeting_time',
        'consultas_precio', 'motivo_derivacion',
        'horarios_ofrecidos', 'meeting_event_id',
        'nurture_desde', 'nurture_motivo',
        'no_cliente_motivo', 'no_cliente_desde',
      ];
      const set = Object.keys(campos).filter((k) => permitidos.includes(k));
      if (!set.length) return stmt.leadPorId.get(id);
      const sql = `UPDATE leads SET ${set.map((k) => `${k} = ?`).join(', ')} WHERE id = ?`;
      db.prepare(sql).run(...set.map((k) => campos[k]), id);
      return stmt.leadPorId.get(id);
    },

    registrarMensaje(m) {
      const info = stmt.insertMensaje.run({
        lead_id: m.lead_id ?? null,
        direction: m.direction,
        kind: m.kind,
        body: m.body ?? null,
        provider: m.provider,
        provider_msg_id: m.provider_msg_id ?? null,
        status: m.status ?? 'queued',
        error: m.error ?? null,
        destino: m.destino ?? null,
        // Lista, no archivo suelto: el agrupador junta los mensajes que llegan
        // seguidos, asi que dos notas de voz de corrido son un solo turno y por
        // lo tanto una sola fila con los dos audios.
        media: m.media && m.media.length ? JSON.stringify(m.media) : null,
      });
      return info.lastInsertRowid;
    },

    mensajesDeLead: (leadId) => stmt.mensajesDeLead.all(leadId),
    mensajePorId: (id) => db.prepare('SELECT * FROM messages WHERE id = ?').get(id),

    /**
     * Si el lead escribio hace poco. Lo usa la cola para decidir si un saliente
     * es una RESPUESTA —y entonces no espera al horario de envio— o algo que
     * arranca el bot, que si espera.
     */
    escribioHaceMenos(leadId, minutos, ahora = new Date()) {
      if (!leadId) return false;
      const desde = new Date(ahora.getTime() - minutos * 60_000).toISOString();
      const f = db.prepare(`
        SELECT 1 FROM messages
        WHERE lead_id = ? AND direction = 'in' AND created_at >= ?
        LIMIT 1
      `).get(leadId, aFechaSqlite(desde));
      return Boolean(f);
    },

    /**
     * Ultimos mensajes de la conversacion.
     *
     * `desde` corta lo anterior a un reinicio. Los mensajes viejos no se
     * borran —el equipo los sigue viendo en el panel— pero la IA no los ve, o
     * retomaria una conversacion que para el lead ya termino.
     */
    ultimosMensajes: (leadId, n = 6, desde = null) => (desde
      ? db.prepare(
        'SELECT direction, body FROM messages WHERE lead_id = ? AND created_at > ? ORDER BY id DESC LIMIT ?'
      ).all(leadId, aFechaSqlite(desde), n).reverse()
      : db.prepare(
        'SELECT direction, body FROM messages WHERE lead_id = ? ORDER BY id DESC LIMIT ?'
      ).all(leadId, n).reverse()),

    /**
     * Vuelve el lead al principio: se le borra lo que el embudo habia
     * averiguado y se marca desde donde cuenta la conversacion nueva.
     */
    reiniciarLead(id, ahoraIso) {
      db.prepare(`UPDATE leads SET
        fsm_state = 'NEW', fsm_retries = 0, opt_out = 0, human_requested = 0,
        motivo_derivacion = NULL, consultas_precio = 0,
        -- Los dos frenos del bot tambien. Reiniciar es empezar de cero, y sin
        -- esto un lead que reiniciaste justo despues de escribirle desde el
        -- telefono queda en NEW pero mudo unas horas, sin nada que lo explique.
        bot_enabled = 1, bot_pausado_hasta = NULL,
        business_name = NULL, business_name_por_audio = 0,
        business_type = NULL, budget = NULL, team_size = NULL,
        colors = NULL, instagram_web = NULL, needs = NULL,
        rubro = NULL, rubro_norm = NULL,
        score = NULL, priority = NULL, score_reason = NULL,
        nurture_desde = NULL, nurture_motivo = NULL,
        no_cliente_motivo = NULL, no_cliente_desde = NULL,
        humano_avisado_at = NULL,
        -- La reunion tambien. Sin esto el lead quedaba en NEW pero con una
        -- reunion colgada, que es un estado que no significa nada: reiniciado
        -- en todo menos en lo unico que ya habia conseguido. Y confundia de
        -- verdad — al probar la derivacion por abandono, el lead reiniciado no
        -- se derivaba nunca porque seguia figurando como que ya habia agendado.
        meeting_time = NULL, meeting_url = NULL, meeting_booked_at = NULL,
        meeting_event_id = NULL,
        -- Y los horarios que se le habian mostrado. Sin esto el lead reiniciado
        -- arrastra la lista de otra conversacion, que ademas puede ser de antes
        -- de que se cambiara la franja de atencion: elegiria "las 12" de una
        -- lista que ya no existe. Mismo olvido que la reunion colgada de arriba.
        horarios_ofrecidos = NULL,
        status = 'new', replied_at = NULL, followup_sent_at = NULL,
        -- Tambien el saludo: reiniciar es empezar de cero, y sin esto el lead
        -- reiniciado nunca vuelve a recibir la presentacion.
        welcomed_at = NULL,
        conversacion_desde = ?
      WHERE id = ?`).run(aFechaSqlite(ahoraIso), id);
      db.prepare(
        "UPDATE jobs SET status='cancelled', last_error='lead reiniciado' WHERE lead_id = ? AND status='pending'"
      ).run(id);
      return stmt.leadPorId.get(id);
    },

    /**
     * Marca la entrega/lectura que reporta el proveedor. Solo avanza: un acuse
     * de entrega que llega tarde no puede pisar un "leido" ya registrado.
     */
    marcarEntrega(providerMsgId, estado) {
      const orden = { queued: 0, sent: 1, delivered: 2, read: 3 };
      const fila = db.prepare('SELECT id, status FROM messages WHERE provider_msg_id = ?')
        .get(providerMsgId);
      if (!fila) return false;
      if ((orden[estado] ?? 0) <= (orden[fila.status] ?? 0)) return false;
      db.prepare('UPDATE messages SET status = ? WHERE id = ?').run(estado, fila.id);
      return true;
    },

    /**
     * Cuerpo de un saliente por su id en el proveedor. Lo necesita Baileys para
     * reenviar un mensaje cuando el dispositivo del destinatario no lo pudo
     * descifrar y pide el reintento.
     */
    cuerpoPorProviderId(providerMsgId) {
      const f = db.prepare(
        "SELECT body FROM messages WHERE provider_msg_id = ? AND direction = 'out'"
      ).get(providerMsgId);
      return f ? f.body : null;
    },

    /** Salientes que el proveedor acepto pero nadie confirmo haber recibido. */
    sinConfirmar(desdeIso) {
      return db.prepare(
        "SELECT COUNT(*) AS n FROM messages WHERE direction = 'out' AND status = 'sent' AND created_at <= ?"
      ).get(aFechaSqlite(desdeIso)).n;
    },

    encolarJob: (leadId, tipo, runAtIso) => stmt.insertJob.run(leadId, tipo, runAtIso),

    /**
     * Como encolarJob pero corriendo la hora del que ya estaba.
     *
     * encolarJob es ON CONFLICT DO NOTHING, que sirve para los jobs que se
     * agendan una vez. Para el reloj del abandono no: hay que reiniciarlo en
     * cada turno, y con DO NOTHING quedaria clavado en la hora del primer
     * mensaje —el lead seguiria conversando y a la hora lo derivarian igual—.
     */
    programarJob(leadId, tipo, runAtIso) {
      const r = db.prepare(
        "UPDATE jobs SET run_at = ?, attempts = 0 WHERE lead_id = ? AND type = ? AND status = 'pending'"
      ).run(runAtIso, leadId, tipo);
      if (!r.changes) stmt.insertJob.run(leadId, tipo, runAtIso);
    },

    /**
     * Registra que el lead agendo en Calendly. Cancela el follow-up pendiente:
     * el que ya agendo no tiene que recibir un "¿seguís interesado?".
     */
    registrarReunion(leadId, { meetingTime, meetingUrl, ahoraIso }) {
      db.prepare(
        'UPDATE leads SET meeting_time = ?, meeting_url = ?, meeting_booked_at = ? WHERE id = ?'
      ).run(meetingTime, meetingUrl ?? null, ahoraIso, leadId);
      stmt.cancelarJobs.run(leadId, 'followup');
      return stmt.leadPorId.get(leadId);
    },
    jobsVencidos: (ahoraIso) => stmt.jobsVencidos.all(ahoraIso),
    marcarJob: (id, estado, error = null) => stmt.marcarJob.run(estado, error, id),
    /** Corre un job pendiente a mas adelante, sin perderlo. */
    reprogramarJob: (id, runAtIso) => stmt.reprogramarJob.run(runAtIso, id),
    cancelarJobs: (leadId, tipo) => stmt.cancelarJobs.run(leadId, tipo),

    /**
     * Marca un entrante como visto.
     *
     * @returns {boolean} true si es la primera vez. Con false hay que
     *   descartarlo: WhatsApp reenvia lo no confirmado cuando el bot reconecta,
     *   y contestar dos veces el mismo mensaje se ve del otro lado como dos
     *   respuestas desordenadas al mismo "hola".
     *
     * El INSERT es la operacion atomica que decide: si otra tanda entro primero,
     * este falla por clave duplicada y devuelve false. Preguntar y despues
     * insertar dejaria una ventana entre las dos.
     */
    entranteEsNuevo(providerMsgId) {
      if (!providerMsgId) return true;
      const r = db.prepare('INSERT OR IGNORE INTO inbound_seen (provider_msg_id) VALUES (?)')
        .run(String(providerMsgId));
      return r.changes > 0;
    },

    /**
     * Si el mensaje ANTERIOR del lead se quedo sin respuesta del bot.
     *
     * Repetir una pregunta no es insistir cuando nadie contesto la primera vez.
     * Paso en produccion: el lead pregunto el precio, el tope por hora freno la
     * respuesta, el lead volvio a preguntar a los ocho minutos y el bot lo
     * derivo a una persona por "insistir con el precio". Desde su lado habia
     * preguntado una sola vez y nunca le contestaron.
     *
     * Se miran solo los salientes que de verdad salieron: uno que quedo
     * atrapado en la cola no le llego a nadie.
     */
    quedoSinRespuesta(leadId) {
      const entrantes = db.prepare(
        "SELECT id FROM messages WHERE lead_id = ? AND direction = 'in' ORDER BY id DESC LIMIT 2"
      ).all(leadId);
      // Es el primero que manda: no hay nada anterior que pudiera quedar sin responder.
      if (entrantes.length < 2) return false;

      const anterior = entrantes[1].id;
      const contestado = db.prepare(`
        SELECT 1 FROM messages
        WHERE lead_id = ? AND direction = 'out' AND id > ?
          AND kind IN ('manual', 'welcome')
          AND status IN ('sent', 'delivered', 'read')
        LIMIT 1
      `).get(leadId, anterior);
      return !contestado;
    },

    /**
     * Cuando fue lo ultimo que el bot le dijo, en formato de SQLite.
     *
     * Para un lead que esta con una persona, eso es el mensaje de la
     * derivacion: aproxima hace cuanto lo estan esperando.
     */
    ultimoSalienteAl(leadId) {
      const f = db.prepare(`
        SELECT created_at FROM messages
        WHERE lead_id = ? AND direction = 'out' AND kind IN ('manual', 'welcome')
        ORDER BY id DESC LIMIT 1
      `).get(leadId);
      return f ? f.created_at : null;
    },

    /**
     * Marca una reserva del calendario como ya registrada.
     *
     * @returns {boolean} true si es la primera vez. Con false hay que saltearla:
     *   registrarla otra vez le avisa al equipo de una reunion que ya conocia.
     *
     * La clave lleva la hora de inicio ademas del id: si la reunion se movio,
     * es una reserva nueva y si hay que avisar.
     */
    reservaEsNueva(eventId, inicio) {
      if (!eventId) return false;
      const r = db.prepare('INSERT OR IGNORE INTO reservas_vistas (clave) VALUES (?)')
        .run(`${eventId}|${inicio || ''}`);
      return r.changes > 0;
    },

    /**
     * Si ya salio un mensaje interno con ESE MISMO texto hace poco.
     *
     * Un aviso al equipo se repite palabra por palabra solo cuando algo esta en
     * bucle: el texto lleva el nombre, la hora y lo que dijo el lead, asi que
     * dos identicos son el mismo aviso dos veces.
     */
    avisoIdenticoReciente(texto, horas) {
      return Boolean(db.prepare(`
        SELECT 1 FROM messages
        WHERE direction = 'out' AND kind = 'am_notice' AND body = ?
          AND created_at >= datetime('now', ?)
        LIMIT 1
      `).get(String(texto), `-${Number(horas)} hours`));
    },

    /**
     * Cuantos mensajes se le mandaron a UN numero desde tal momento.
     *
     * Es la pregunta que delata un bucle lento. El incidente de los 489 avisos
     * eran 24 mensajes por hora —un goteo que ninguna alarma de volumen
     * agarra— pero todos al mismo telefono.
     */
    enviadosA(destino, desdeIso) {
      return db.prepare(`
        SELECT COUNT(*) AS n FROM messages
        WHERE direction = 'out' AND destino = ? AND created_at >= ?
      `).get(destino, desdeIso).n;
    },

    /** Lo ultimo que se le mando a un numero. Para no repetirselo. */
    ultimoTextoA(destino) {
      const f = db.prepare(`
        SELECT body FROM messages
        WHERE direction = 'out' AND destino = ?
        ORDER BY id DESC LIMIT 1
      `).get(destino);
      return f ? f.body : null;
    },

    /** Si ya se aviso de la agenda caida desde esa fecha. Para no repetirlo. */
    huboAvisoDeAgenda(desdeIso) {
      const f = db.prepare(`
        SELECT 1 FROM messages
        WHERE direction = 'out' AND kind = 'am_notice'
          AND body LIKE '%agenda de Google no responde%' AND created_at >= ?
        LIMIT 1
      `).get(aFechaSqlite(desdeIso));
      return Boolean(f);
    },

    /** Cuantos avisos internos salieron en la ultima hora. */
    internosDesde(desdeIso) {
      return db.prepare(`
        SELECT COUNT(*) AS n FROM messages
        WHERE direction = 'out' AND kind = 'am_notice' AND created_at >= ?
      `).get(desdeIso).n;
    },

    /** Los ids viejos no sirven para nada: WhatsApp no reenvia de hace dias. */
    limpiarEntrantesVistos(dias = 3) {
      return db.prepare(`DELETE FROM inbound_seen WHERE seen_at < datetime('now', '-${Number(dias)} days')`).run().changes;
    },

    registrarEnvio: (tel, esPrimero) => stmt.insertSendLog.run(tel, esPrimero ? 1 : 0),
    enviosDesde: (desdeIso) => stmt.contarEnviosDesde.get(aFechaSqlite(desdeIso)).n,
    nuevosDesde: (desdeIso) => stmt.contarNuevosDesde.get(aFechaSqlite(desdeIso)).n,
    yaFueContactado: (tel) => Boolean(stmt.yaContactado.get(tel)),
  };
}

module.exports = { crearRepo, aFechaSqlite };
