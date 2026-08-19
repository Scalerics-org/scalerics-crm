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
      INSERT INTO messages (lead_id, direction, kind, body, provider, provider_msg_id, status, error)
      VALUES (@lead_id, @direction, @kind, @body, @provider, @provider_msg_id, @status, @error)
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
        'am_notified_at', 'rubro_norm', 'nombre', 'necesidad',
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
        'business_name', 'business_type', 'budget', 'team_size',
        'colors', 'instagram_web', 'needs', 'rubro', 'rubro_norm',
        'score', 'priority', 'score_reason', 'meeting_url', 'meeting_time',
        'consultas_precio', 'motivo_derivacion',
        'horarios_ofrecidos', 'meeting_event_id',
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
      });
      return info.lastInsertRowid;
    },

    mensajesDeLead: (leadId) => stmt.mensajesDeLead.all(leadId),

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
        business_name = NULL, business_type = NULL, budget = NULL, team_size = NULL,
        colors = NULL, instagram_web = NULL, needs = NULL,
        rubro = NULL, rubro_norm = NULL,
        score = NULL, priority = NULL, score_reason = NULL,
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
