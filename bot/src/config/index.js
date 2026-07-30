require('dotenv').config();

const required = [
  'WA_PHONE_NUMBER_ID',
  'WA_ACCESS_TOKEN',
  'WA_VERIFY_TOKEN',
  'DATABASE_URL',
  'REDIS_URL',
];

for (const key of required) {
  if (!process.env[key]) {
    throw new Error(`Missing required environment variable: ${key}`);
  }
}

module.exports = {
  PORT: parseInt(process.env.PORT || '3000', 10),
  NODE_ENV: process.env.NODE_ENV || 'development',

  // WhatsApp
  WA_PHONE_NUMBER_ID: process.env.WA_PHONE_NUMBER_ID,
  WA_ACCESS_TOKEN: process.env.WA_ACCESS_TOKEN,
  WA_VERIFY_TOKEN: process.env.WA_VERIFY_TOKEN,
  WA_API_VERSION: process.env.WA_API_VERSION || 'v19.0',

  // Database
  DATABASE_URL: process.env.DATABASE_URL,

  // Redis
  REDIS_URL: process.env.REDIS_URL,

  // Calendly
  CALENDLY_WEBHOOK_SECRET: process.env.CALENDLY_WEBHOOK_SECRET || '',
  CALENDLY_LINK: process.env.CALENDLY_LINK || 'https://calendly.com/scalerics/diagnostico',

  // Anthropic
  ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY || '',

  // Admin
  ADMIN_TOKEN: process.env.ADMIN_TOKEN || '',

  // CRM sync (optional)
  CRM_API_URL: process.env.CRM_API_URL || '',
  CRM_ADMIN_TOKEN: process.env.CRM_ADMIN_TOKEN || '',
};
