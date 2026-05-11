const express = require('express');
const router = express.Router();
const crypto = require('crypto');
const config = require('../config');
const leadsService = require('../services/leads');
const session = require('../services/session');
const wa = require('../services/whatsapp');
const T = require('../messages/templates');
const { S } = require('../fsm/states');
const { reminderQueue } = require('../services/scheduler');
const { notifyCRM } = require('../services/crm');

function verifyCalendlySignature(req) {
  if (!config.CALENDLY_WEBHOOK_SECRET) return true; // Skip if not configured
  const signature = req.headers['calendly-webhook-signature'] || '';
  const [t, v1] = signature.split(',').reduce((acc, part) => {
    const [k, val] = part.split('=');
    if (k === 't') acc[0] = val;
    if (k === 'v1') acc[1] = val;
    return acc;
  }, ['', '']);

  const payload = `${t}.${JSON.stringify(req.body)}`;
  const expected = crypto
    .createHmac('sha256', config.CALENDLY_WEBHOOK_SECRET)
    .update(payload)
    .digest('hex');

  return crypto.timingSafeEqual(Buffer.from(v1 || ''), Buffer.from(expected));
}

// POST /api/booking-confirmed — Calendly webhook
router.post('/booking-confirmed', express.json(), async (req, res) => {
  if (!verifyCalendlySignature(req)) {
    return res.status(401).json({ error: 'Invalid signature' });
  }

  res.sendStatus(200);

  try {
    const event = req.body;
    if (event.event !== 'invitee.created') return;

    const invitee = event.payload?.invitee;
    const scheduled = event.payload?.event;

    if (!invitee || !scheduled) return;

    // Extract phone from questions_and_answers or name
    const phoneAnswer = invitee.questions_and_answers?.find(
      (q) => q.question.toLowerCase().includes('whatsapp') ||
             q.question.toLowerCase().includes('teléfono') ||
             q.question.toLowerCase().includes('celular')
    );

    // Normalize phone: strip spaces, dashes, add + if missing
    let phone = phoneAnswer?.answer || '';
    phone = phone.replace(/[\s\-\(\)]/g, '');
    if (phone && !phone.startsWith('+')) phone = `+${phone}`;

    if (!phone) {
      console.warn('Calendly booking: no phone found in invitee answers', invitee.email);
      return;
    }

    const meetingTime = scheduled.start_time;
    const meetingUrl = scheduled.location?.join_url || scheduled.location?.location || '';

    const lead = await leadsService.findByPhone(phone);
    if (!lead) {
      console.warn('Calendly booking: lead not found for phone', phone);
      return;
    }

    await leadsService.update(lead.id, {
      state: S.SCHEDULED,
      meeting_time: meetingTime,
      meeting_url: meetingUrl,
    });
    await session.setSession(phone, { state: S.SCHEDULED, leadId: lead.id });
    notifyCRM(lead).catch(err => console.error('[CRM sync] booking failed:', err.message));

    // Format date/time for message
    const mt = new Date(meetingTime);
    const timeStr = mt.toLocaleTimeString('es-UY', { hour: '2-digit', minute: '2-digit', timeZone: 'America/Montevideo' });
    const dayStr = mt.toLocaleDateString('es-UY', {
      weekday: 'long', day: 'numeric', month: 'long', timeZone: 'America/Montevideo',
    });

    await wa.sendText(phone, T.MEETING_CONFIRMED(dayStr, timeStr, meetingUrl));

    // Schedule reminders
    const reminderTime24h = new Date(mt.getTime() - 24 * 60 * 60 * 1000);
    const reminderTime1h = new Date(mt.getTime() - 60 * 60 * 1000);
    const nowMs = Date.now();

    if (reminderTime24h.getTime() > nowMs) {
      await reminderQueue.add('reminder-24h',
        { leadId: lead.id, phone, meetingTime, meetingUrl },
        { jobId: `reminder-24h-${lead.id}`, delay: reminderTime24h.getTime() - nowMs, removeOnComplete: true }
      );
    }

    if (reminderTime1h.getTime() > nowMs) {
      await reminderQueue.add('reminder-1h',
        { leadId: lead.id, phone, meetingTime, meetingUrl },
        { jobId: `reminder-1h-${lead.id}`, delay: reminderTime1h.getTime() - nowMs, removeOnComplete: true }
      );
    }
  } catch (err) {
    console.error('Booking webhook error:', err.message);
  }
});

module.exports = router;
