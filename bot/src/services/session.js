const Redis = require('ioredis');
const config = require('../config');

const redis = new Redis(config.REDIS_URL, {
  maxRetriesPerRequest: 3,
});

redis.on('error', (err) => console.error('Redis error:', err.message));

const SESSION_TTL = 86400; // 24 horas en segundos
const SESSION_PREFIX = 'session:';
const DEDUP_PREFIX = 'msg:';

async function getSession(phone) {
  const data = await redis.hgetall(`${SESSION_PREFIX}${phone}`);
  if (!data || !data.state) return null;
  return {
    state: data.state,
    retries: parseInt(data.retries || '0', 10),
    leadId: data.leadId || null,
  };
}

async function setSession(phone, { state, leadId, retries = 0 }) {
  const key = `${SESSION_PREFIX}${phone}`;
  await redis.hmset(key, { state, leadId: leadId || '', retries: String(retries) });
  await redis.expire(key, SESSION_TTL);
}

async function incrementRetry(phone) {
  const key = `${SESSION_PREFIX}${phone}`;
  const val = await redis.hincrby(key, 'retries', 1);
  await redis.expire(key, SESSION_TTL);
  return val;
}

async function clearRetry(phone) {
  await redis.hset(`${SESSION_PREFIX}${phone}`, 'retries', '0');
}

// Anti-duplicate: returns true if the message was already processed
async function isDuplicate(msgId) {
  const key = `${DEDUP_PREFIX}${msgId}`;
  const result = await redis.set(key, '1', 'EX', 3600, 'NX');
  return result === null; // null means key already existed
}

module.exports = { getSession, setSession, incrementRetry, clearRetry, isDuplicate, redis };
