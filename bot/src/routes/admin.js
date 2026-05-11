const express = require('express');
const router = express.Router();
const config = require('../config');
const leadsService = require('../services/leads');

function authMiddleware(req, res, next) {
  const token = req.headers['x-admin-token'] || req.query.token;
  if (!config.ADMIN_TOKEN || token !== config.ADMIN_TOKEN) {
    return res.status(401).json({ error: 'Unauthorized' });
  }
  next();
}

// GET /api/leads
router.get('/leads', authMiddleware, async (req, res) => {
  try {
    const { state, limit = 50, offset = 0 } = req.query;
    const leads = await leadsService.listLeads({
      state: state || undefined,
      limit: Math.min(parseInt(limit, 10), 200),
      offset: parseInt(offset, 10),
    });
    res.json({ leads, count: leads.length });
  } catch (err) {
    console.error('Admin leads error:', err.message);
    res.status(500).json({ error: 'Internal error' });
  }
});

// GET /api/leads/phone/:phone — lead + messages by phone (tries multiple formats)
router.get('/leads/phone/:phone', authMiddleware, async (req, res) => {
  try {
    const { query } = require('../db');
    const raw = req.params.phone.replace(/[^\d+]/g, '');
    const digits = raw.replace(/^\+/, '').replace(/^00/, '');
    const variants = new Set([raw, digits]);
    if (digits.length === 9 && digits.startsWith('0')) {
      const intl = '598' + digits.slice(1);
      variants.add(intl); variants.add('+' + intl);
    } else if (digits.length === 8) {
      const intl = '598' + digits;
      variants.add(intl); variants.add('+' + intl);
    } else if (digits.length === 11 && digits.startsWith('598')) {
      variants.add('+' + digits); variants.add('0' + digits.slice(3));
    }
    for (const v of [...variants]) { if (!v.startsWith('+')) variants.add('+' + v); }

    const ph = [...variants];
    const placeholders = ph.map((_, i) => `$${i + 1}`).join(', ');
    const leadRes = await query(
      `SELECT id, phone, name, state, score, business_type, main_problem,
              team_size, budget, urgency, business_name, created_at, last_message_at
       FROM leads WHERE phone IN (${placeholders})`,
      ph
    );
    if (!leadRes.rows.length) return res.status(404).json({ error: 'Lead no encontrado' });
    const lead = leadRes.rows[0];

    const msgRes = await query(
      'SELECT direction, content, sent_at FROM messages WHERE lead_id = $1 ORDER BY sent_at ASC LIMIT 120',
      [lead.id]
    );
    res.json({ lead, messages: msgRes.rows });
  } catch (err) {
    console.error('Admin lead-by-phone error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// POST /api/send — send a WhatsApp message from the dashboard
router.post('/send', authMiddleware, async (req, res) => {
  try {
    const { phone, text } = req.body;
    if (!phone || !text) return res.status(400).json({ error: 'phone and text required' });
    const wa = require('../services/whatsapp');
    const lead = await leadsService.findByPhone(phone);
    await wa.sendText(phone, text, lead ? lead.id : null);
    if (lead) await leadsService.update(lead.id, { human_requested: true, state: 'HUMAN_QUEUED' });
    res.json({ ok: true });
  } catch (err) {
    console.error('Admin send error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// GET /api/leads/search?name=... — find lead by partial name match
router.get('/leads/search', authMiddleware, async (req, res) => {
  try {
    const { query } = require('../db');
    const name = (req.query.name || '').trim();
    if (!name) return res.status(400).json({ error: 'name query param required' });
    const result = await query(
      `SELECT id, phone, name, state, score, business_type, main_problem,
              team_size, budget, urgency, business_name, created_at, last_message_at
       FROM leads WHERE name ILIKE $1 ORDER BY last_message_at DESC LIMIT 5`,
      [`%${name}%`]
    );
    if (!result.rows.length) return res.status(404).json({ error: 'Lead no encontrado' });
    const lead = result.rows[0];
    const msgRes = await query(
      'SELECT direction, content, sent_at FROM messages WHERE lead_id = $1 ORDER BY sent_at ASC LIMIT 120',
      [lead.id]
    );
    res.json({ lead, messages: msgRes.rows });
  } catch (err) {
    console.error('Admin leads/search error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// POST /api/leads/phone/:phone/release — clear human_requested flag
router.post('/leads/phone/:phone/release', authMiddleware, async (req, res) => {
  try {
    const { query } = require('../db');
    await query(
      "UPDATE leads SET human_requested = false, state = 'MENU' WHERE phone = $1",
      [req.params.phone]
    );
    res.json({ ok: true });
  } catch (err) {
    console.error('Admin release error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// GET /api/stats — basic funnel metrics
router.get('/stats', authMiddleware, async (req, res) => {
  try {
    const { query } = require('../db');
    const result = await query(`
      SELECT state, COUNT(*) AS count
      FROM leads
      WHERE opt_out = FALSE
      GROUP BY state
      ORDER BY count DESC
    `);

    const total = result.rows.reduce((sum, r) => sum + parseInt(r.count, 10), 0);
    res.json({ total, by_state: result.rows });
  } catch (err) {
    console.error('Admin stats error:', err.message);
    res.status(500).json({ error: 'Internal error' });
  }
});

module.exports = router;
