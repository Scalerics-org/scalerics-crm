'use strict';

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

    encolarJob: (leadId, tipo, runAtIso) => stmt.insertJob.run(leadId, tipo, runAtIso),
    jobsVencidos: (ahoraIso) => stmt.jobsVencidos.all(ahoraIso),
    marcarJob: (id, estado, error = null) => stmt.marcarJob.run(estado, error, id),
    cancelarJobs: (leadId, tipo) => stmt.cancelarJobs.run(leadId, tipo),

    registrarEnvio: (tel, esPrimero) => stmt.insertSendLog.run(tel, esPrimero ? 1 : 0),
    enviosDesde: (desdeIso) => stmt.contarEnviosDesde.get(desdeIso).n,
    nuevosDesde: (desdeIso) => stmt.contarNuevosDesde.get(desdeIso).n,
    yaFueContactado: (tel) => Boolean(stmt.yaContactado.get(tel)),
  };
}

module.exports = { crearRepo };
