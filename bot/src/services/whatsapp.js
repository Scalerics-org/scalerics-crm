const axios = require('axios');
const config = require('../config');
const leadsService = require('./leads');

const BASE_URL = `https://graph.facebook.com/${config.WA_API_VERSION}/${config.WA_PHONE_NUMBER_ID}/messages`;

const headers = {
  Authorization: `Bearer ${config.WA_ACCESS_TOKEN}`,
  'Content-Type': 'application/json',
};

async function sendText(phone, text, leadId = null) {
  const payload = {
    messaging_product: 'whatsapp',
    recipient_type: 'individual',
    to: phone,
    type: 'text',
    text: { body: text, preview_url: false },
  };
  const result = await _send(payload);
  if (leadId) {
    leadsService.saveMessage(leadId, 'out', text).catch(() => {});
  }
  return result;
}

async function sendInteractiveList(phone, body, buttonLabel, sections) {
  const payload = {
    messaging_product: 'whatsapp',
    recipient_type: 'individual',
    to: phone,
    type: 'interactive',
    interactive: {
      type: 'list',
      body: { text: body },
      action: { button: buttonLabel, sections },
    },
  };
  return _send(payload);
}

async function markRead(msgId) {
  const payload = {
    messaging_product: 'whatsapp',
    status: 'read',
    message_id: msgId,
  };
  return _send(payload);
}

async function _send(payload) {
  try {
    const { data } = await axios.post(BASE_URL, payload, { headers });
    return data;
  } catch (err) {
    // markRead failures are non-critical (e.g. test token permissions)
    if (payload.status === 'read') return;
    const detail = err.response?.data || err.message;
    console.error('WhatsApp API error:', JSON.stringify(detail));
    throw err;
  }
}

module.exports = { sendText, sendInteractiveList, markRead };
