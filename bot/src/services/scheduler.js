const { Queue, Worker } = require('bullmq');
const Redis = require('ioredis');
const config = require('../config');
const leadsService = require('./leads');
const wa = require('./whatsapp');
const T = require('../messages/templates');
const { S } = require('../fsm/states');

// BullMQ requires maxRetriesPerRequest: null on its Redis connection
const connection = new Redis(config.REDIS_URL, { maxRetriesPerRequest: null });

// ── Queues ────────────────────────────────────────────────────────────────────

const followUpQueue = new Queue('follow-ups', { connection });
const reminderQueue = new Queue('reminders', { connection });

// ── Schedule follow-ups ────────────────────────────────────────────────────────

async function scheduleFollowUps() {
  const now = new Date();

  // 4h follow-up: leads stuck in qualifying states
  const cutoff4h = new Date(now - 4 * 60 * 60 * 1000).toISOString();
  const staleQual = await leadsService.getStaleLeadsRaw(
    [S.QUAL_1, S.QUAL_2, S.QUAL_3, S.QUAL_4, S.QUAL_5],
    cutoff4h
  );
  for (const lead of staleQual) {
    await followUpQueue.add('followup-4h', { leadId: lead.id, phone: lead.phone, name: lead.name }, {
      jobId: `followup-4h-${lead.id}`,
      removeOnComplete: true,
    });
  }

  // 24h follow-up: leads in menu or qualifying with no activity
  const cutoff24h = new Date(now - 24 * 60 * 60 * 1000).toISOString();
  const stale24h = await leadsService.getStaleLeadsRaw(
    [S.MENU, S.MENU_INFO, S.QUAL_1, S.QUAL_2, S.QUAL_3, S.QUAL_4, S.QUAL_5],
    cutoff24h
  );
  for (const lead of stale24h) {
    await followUpQueue.add('followup-24h', { leadId: lead.id, phone: lead.phone, name: lead.name }, {
      jobId: `followup-24h-${lead.id}`,
      removeOnComplete: true,
    });
  }

  // 72h follow-up: move to NURTURE
  const cutoff72h = new Date(now - 72 * 60 * 60 * 1000).toISOString();
  const stale72h = await leadsService.getStaleLeadsRaw(
    [S.MENU, S.MENU_INFO, S.QUAL_1, S.QUAL_2, S.QUAL_3, S.QUAL_4, S.QUAL_5, S.MEETING_SENT],
    cutoff72h
  );
  for (const lead of stale72h) {
    await followUpQueue.add('followup-72h', { leadId: lead.id, phone: lead.phone, name: lead.name }, {
      jobId: `followup-72h-${lead.id}`,
      removeOnComplete: true,
    });
  }
}

async function scheduleMeetingReminders() {
  // 24h reminder
  const leads24h = await leadsService.getMeetingReminders(25, 'reminder_24h_sent');
  for (const lead of leads24h) {
    await reminderQueue.add('reminder-24h', {
      leadId: lead.id,
      phone: lead.phone,
      meetingTime: lead.meeting_time,
      meetingUrl: lead.meeting_url,
    }, {
      jobId: `reminder-24h-${lead.id}`,
      removeOnComplete: true,
    });
  }

  // 1h reminder
  const leads1h = await leadsService.getMeetingReminders(1.1, 'reminder_1h_sent');
  for (const lead of leads1h) {
    await reminderQueue.add('reminder-1h', {
      leadId: lead.id,
      phone: lead.phone,
      meetingTime: lead.meeting_time,
      meetingUrl: lead.meeting_url,
    }, {
      jobId: `reminder-1h-${lead.id}`,
      removeOnComplete: true,
    });
  }
}

// ── Workers ───────────────────────────────────────────────────────────────────

function startWorkers() {
  new Worker('follow-ups', async (job) => {
    const { phone, name, leadId } = job.data;

    if (job.name === 'followup-4h') {
      await wa.sendText(phone, T.FOLLOWUP_4H, leadId);
      await leadsService.update(leadId, { last_message_at: new Date().toISOString() });
    }

    if (job.name === 'followup-24h') {
      await wa.sendText(phone, T.FOLLOWUP_24H(name), leadId);
      await leadsService.update(leadId, { last_message_at: new Date().toISOString() });
    }

    if (job.name === 'followup-72h') {
      await wa.sendText(phone, T.FOLLOWUP_72H(name), leadId);
      await leadsService.update(leadId, { state: S.NURTURE });
    }
  }, { connection, concurrency: 5 });

  new Worker('reminders', async (job) => {
    const { phone, leadId, meetingTime, meetingUrl } = job.data;
    const mt = new Date(meetingTime);
    const timeStr = mt.toLocaleTimeString('es-UY', { hour: '2-digit', minute: '2-digit' });
    const dayStr = mt.toLocaleDateString('es-UY', { weekday: 'long', day: 'numeric', month: 'long' });

    if (job.name === 'reminder-24h') {
      await wa.sendText(phone, T.REMINDER_24H(dayStr, timeStr, meetingUrl), leadId);
      await leadsService.update(leadId, { reminder_24h_sent: true });
    }

    if (job.name === 'reminder-1h') {
      await wa.sendText(phone, T.REMINDER_1H(timeStr, meetingUrl), leadId);
      await leadsService.update(leadId, { reminder_1h_sent: true });
    }
  }, { connection, concurrency: 5 });

  console.log('Scheduler workers started');
}

// Called by cron-like setInterval in app.js
async function runScheduledJobs() {
  await scheduleFollowUps();
  await scheduleMeetingReminders();
}

module.exports = { startWorkers, runScheduledJobs, followUpQueue, reminderQueue };
