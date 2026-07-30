const axios = require('axios');
const config = require('../config');
const leadsService = require('./leads');

async function notifyCRM(lead) {
  if (!config.CRM_API_URL || !config.CRM_ADMIN_TOKEN) return;

  const freshLead = await leadsService.findByPhone(lead.phone);
  if (!freshLead) return;

  await axios.post(
    `${config.CRM_API_URL}/api/bot/lead-qualified`,
    {
      phone: freshLead.phone,
      name: freshLead.name,
      business_name: freshLead.business_name,
      business_type: freshLead.business_type,
      budget: freshLead.budget,
      team_size: freshLead.team_size,
      colors: freshLead.colors,
      instagram_web: freshLead.instagram_web,
      needs: freshLead.needs,
      score: freshLead.score,
      priority: freshLead.priority,
      state: freshLead.state,
      meeting_time: freshLead.meeting_time,
      meeting_url: freshLead.meeting_url,
    },
    {
      headers: { 'x-admin-token': config.CRM_ADMIN_TOKEN },
      timeout: 5000,
    }
  );
}

async function notifyCRMDirect(data) {
  if (!config.CRM_API_URL || !config.CRM_ADMIN_TOKEN) return;
  await axios.post(
    `${config.CRM_API_URL}/api/bot/lead-qualified`,
    data,
    { headers: { 'x-admin-token': config.CRM_ADMIN_TOKEN }, timeout: 5000 }
  );
}

module.exports = { notifyCRM, notifyCRMDirect };
