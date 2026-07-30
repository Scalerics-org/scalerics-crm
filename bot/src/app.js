require('dotenv').config();
const express = require('express');
const fs = require('fs');
const path = require('path');
const config = require('./config');
const db = require('./db');
const webhookRouter = require('./routes/webhook');
const bookingRouter = require('./routes/booking');
const adminRouter = require('./routes/admin');
const { startWorkers, runScheduledJobs } = require('./services/scheduler');

const app = express();

// ── Middleware ────────────────────────────────────────────────────────────────

app.use(express.json());

// ── Health check ──────────────────────────────────────────────────────────────

app.get('/health', (req, res) => {
  res.json({ status: 'ok', ts: new Date().toISOString() });
});

// ── Routes ────────────────────────────────────────────────────────────────────

app.use('/webhook', webhookRouter);
app.use('/api', bookingRouter);
app.use('/api', adminRouter);

// ── Error handler ─────────────────────────────────────────────────────────────

app.use((err, req, res, _next) => {
  console.error('Unhandled error:', err.message);
  res.status(500).json({ error: 'Internal server error' });
});

// ── Startup ───────────────────────────────────────────────────────────────────

async function migrate() {
  const sql = fs.readFileSync(path.join(__dirname, 'db/schema.sql'), 'utf8');
  await db.query(sql);
  console.log('Database schema ready');
}

async function start() {
  // Apply schema (idempotent — uses IF NOT EXISTS)
  await migrate();

  // Start BullMQ workers
  startWorkers();

  // Run follow-up scheduler every 5 minutes (non-blocking)
  runScheduledJobs().catch((err) => console.error('Scheduler error:', err.message));
  setInterval(() => {
    runScheduledJobs().catch((err) => console.error('Scheduler error:', err.message));
  }, 5 * 60 * 1000);

  app.listen(config.PORT, () => {
    console.log(`Scalerics WA Bot running on port ${config.PORT} [${config.NODE_ENV}]`);
  });
}

start().catch((err) => {
  console.error('Startup failed:', err.message);
  process.exit(1);
});

module.exports = app;
