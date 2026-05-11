const express = require('express');
const router = express.Router();
const config = require('../config');
const session = require('../services/session');
const leadsService = require('../services/leads');
const wa = require('../services/whatsapp');
const engine = require('../fsm/engine');

// GET /webhook — Meta webhook verification
router.get('/', (req, res) => {
  const mode = req.query['hub.mode'];
  const token = req.query['hub.verify_token'];
  const challenge = req.query['hub.challenge'];

  if (mode === 'subscribe' && token === config.WA_VERIFY_TOKEN) {
    console.log('Webhook verified by Meta');
    return res.status(200).send(challenge);
  }
  res.sendStatus(403);
});

// POST /webhook — Incoming messages from Meta
router.post('/', async (req, res) => {
  // Respond immediately to Meta (must be < 5 seconds)
  res.sendStatus(200);

  try {
    const body = req.body;
    if (body.object !== 'whatsapp_business_account') return;

    const entry = body.entry?.[0];
    const change = entry?.changes?.[0];
    const value = change?.value;

    if (!value?.messages) return;

    for (const msg of value.messages) {
      await processMessage(msg, value.contacts?.[0]);
    }
  } catch (err) {
    console.error('Webhook processing error:', err.message, err.stack);
  }
});

async function processMessage(msg, contact) {
  const msgId = msg.id;
  const phone = msg.from;
  const timestamp = msg.timestamp;

  // Anti-duplicate
  if (await session.isDuplicate(msgId)) return;

  // Extract text content
  let text = '';
  if (msg.type === 'text') {
    text = msg.text?.body || '';
  } else if (msg.type === 'interactive') {
    // Button reply or list reply
    text = msg.interactive?.button_reply?.id ||
           msg.interactive?.list_reply?.id ||
           '';
  } else {
    // Sticker, image, audio etc — treat as wildcard
    text = '*';
  }

  // Mark as read (non-blocking)
  wa.markRead(msgId).catch(() => {});

  // Find or create lead
  const lead = await leadsService.findOrCreate(phone);

  // Update name from contact if available and not set
  if (contact?.profile?.name && !lead.name) {
    await leadsService.update(lead.id, { name: contact.profile.name });
    lead.name = contact.profile.name;
  }

  // Persist incoming message (update() auto-touches last_message_at)
  await leadsService.saveMessage(lead.id, 'in', text, msgId);

  // Delegate to FSM
  await engine.process(lead, text, msgId);
}

module.exports = router;
