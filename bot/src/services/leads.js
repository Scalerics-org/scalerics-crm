const db = require('../db');

async function findOrCreate(phone) {
  const existing = await db.query(
    'SELECT * FROM leads WHERE phone = $1',
    [phone]
  );
  if (existing.rows.length > 0) return existing.rows[0];

  const result = await db.query(
    `INSERT INTO leads (phone, state, last_message_at)
     VALUES ($1, 'NEW', NOW())
     RETURNING *`,
    [phone]
  );
  return result.rows[0];
}

async function findByPhone(phone) {
  const result = await db.query('SELECT * FROM leads WHERE phone = $1', [phone]);
  return result.rows[0] || null;
}

async function update(id, fields) {
  const keys = Object.keys(fields);
  if (keys.length === 0) return;

  // Always touch last_message_at unless caller already includes it
  const allFields = fields.last_message_at ? fields : { ...fields, last_message_at: new Date() };
  const allKeys = Object.keys(allFields);
  const setClauses = allKeys.map((k, i) => `${k} = $${i + 2}`).join(', ');
  const values = [id, ...allKeys.map((k) => allFields[k])];

  await db.query(
    `UPDATE leads SET ${setClauses} WHERE id = $1`,
    values
  );
}

async function saveMessage(leadId, direction, content, waMsgId = null) {
  await db.query(
    `INSERT INTO messages (lead_id, direction, content, wa_msg_id)
     VALUES ($1, $2, $3, $4)`,
    [leadId, direction, content, waMsgId]
  );
}

// Returns leads that haven't messaged in `minutes` and are in given states
async function getStaleLeads(states, minutesAgo) {
  const stateList = states.map((_, i) => `$${i + 2}`).join(', ');
  const result = await db.query(
    `SELECT * FROM leads
     WHERE state IN (${stateList})
       AND opt_out = FALSE
       AND last_message_at < NOW() - INTERVAL '${minutesAgo} minutes'
     LIMIT 100`,
    [null, ...states]
  );
  // Fix: params don't support INTERVAL injection, use explicit query
  return result.rows;
}

async function getStaleLeadsRaw(states, cutoffISO) {
  if (states.length === 0) return [];
  const stateList = states.map((_, i) => `$${i + 2}`).join(', ');
  const result = await db.query(
    `SELECT * FROM leads
     WHERE state IN (${stateList})
       AND opt_out = FALSE
       AND (last_message_at IS NULL OR last_message_at < $1)
     LIMIT 100`,
    [cutoffISO, ...states]
  );
  return result.rows;
}

async function getMeetingReminders(hoursAhead, reminderFlag) {
  const result = await db.query(
    `SELECT * FROM leads
     WHERE state = 'SCHEDULED'
       AND opt_out = FALSE
       AND ${reminderFlag} = FALSE
       AND meeting_time BETWEEN NOW() AND NOW() + INTERVAL '${hoursAhead} hours'`,
    []
  );
  return result.rows;
}

async function listLeads({ limit = 50, offset = 0, state } = {}) {
  const conditions = ['TRUE'];
  const params = [];

  if (state) {
    params.push(state);
    conditions.push(`state = $${params.length}`);
  }

  params.push(limit, offset);
  const result = await db.query(
    `SELECT id, phone, name, business_name, business_type, main_problem, team_size,
            budget, urgency, colors, instagram_web, score, priority, state, source,
            meeting_time, created_at, last_message_at
     FROM leads
     WHERE ${conditions.join(' AND ')}
     ORDER BY created_at DESC
     LIMIT $${params.length - 1} OFFSET $${params.length}`,
    params
  );
  return result.rows;
}

module.exports = {
  findOrCreate,
  findByPhone,
  update,
  saveMessage,
  getStaleLeadsRaw,
  getMeetingReminders,
  listLeads,
};
