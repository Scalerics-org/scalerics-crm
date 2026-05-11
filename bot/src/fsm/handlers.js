const wa = require('../services/whatsapp');
const leadsService = require('../services/leads');
const T = require('../messages/templates');
const { S } = require('./states');
const config = require('../config');

// Maps qualifying question input to the DB field name
const QUAL_FIELD = {
  [S.QUAL_1]: 'business_type',
  [S.QUAL_2]: 'main_problem',
  [S.QUAL_3]: 'team_size',
  [S.QUAL_4]: 'budget',
  [S.QUAL_5]: 'urgency',
};

async function handleMenu(lead) {
  await wa.sendText(lead.phone, T.WELCOME(lead.name), lead.id);
}

async function handleMenuInfo(lead) {
  await wa.sendText(lead.phone, T.MENU_INFO, lead.id);
}

async function handleQual0(lead) {
  await wa.sendText(lead.phone, T.QUAL_0, lead.id);
}

async function handleQual1(lead, input, fromState) {
  if (fromState === S.QUAL_0 && input) {
    leadsService.update(lead.id, { business_name: input }).catch(() => {});
  }
  await wa.sendText(lead.phone, T.QUAL_1, lead.id);
}

async function handleQual2(lead, input) {
  await leadsService.update(lead.id, { business_type: parseInt(input, 10) });
  await wa.sendText(lead.phone, T.QUAL_2, lead.id);
}

async function handleQual3(lead, input) {
  await leadsService.update(lead.id, { budget: parseInt(input, 10) });
  await wa.sendText(lead.phone, T.QUAL_3, lead.id);
}

async function handleQual4(lead, input) {
  await leadsService.update(lead.id, { team_size: parseInt(input, 10) });
  await wa.sendText(lead.phone, T.QUAL_4, lead.id);
}

async function handleQual5(lead, input) {
  // Save colors (answer to QUAL_4)
  if (input) await leadsService.update(lead.id, { colors: input });
  await wa.sendText(lead.phone, T.QUAL_5, lead.id);
}

async function handleQual6(lead, input) {
  // Save instagram_web (answer to QUAL_5)
  if (input) await leadsService.update(lead.id, { instagram_web: input });
  await wa.sendText(lead.phone, T.QUAL_6, lead.id);
}

async function handleScored(lead, input, fromState) {
  // Save needs (answer to QUAL_6)
  if (fromState === S.QUAL_6 && input) {
    await leadsService.update(lead.id, { needs: input });
  }

  // Score the lead (imported here to avoid circular deps)
  const { scoreLead } = require('../services/ai');
  const freshLead = await leadsService.findByPhone(lead.phone);
  const { score, priority, recommended_action, reason } = await scoreLead(freshLead);

  await leadsService.update(lead.id, { score, priority, score_reason: reason });

  if (recommended_action === 'meeting') {
    await leadsService.update(lead.id, { state: S.MEETING_SENT });
    await wa.sendText(lead.phone, T.MEETING_OFFER(lead.name), lead.id);
    return S.MEETING_SENT;
  } else if (recommended_action === 'nurture') {
    await leadsService.update(lead.id, { state: S.NURTURE });
    await wa.sendText(lead.phone, T.NURTURE, lead.id);
    return S.NURTURE;
  } else {
    await leadsService.update(lead.id, { state: S.DISQUALIFIED });
    await wa.sendText(lead.phone, T.DISQUALIFIED, lead.id);
    return S.DISQUALIFIED;
  }
}

async function handleMeetingSent(lead, input) {
  if (input === '1') {
    await wa.sendText(lead.phone, T.MEETING_LINK, lead.id);
  } else if (input === '2') {
    await wa.sendText(lead.phone, T.MORE_INFO, lead.id);
  } else {
    await wa.sendText(lead.phone, T.MEETING_LINK, lead.id);
  }
}

async function handleScheduled(lead) {
  await wa.sendText(lead.phone, '✅ Confirmado, ¡te esperamos!', lead.id);
}

async function handleHumanQueued(lead) {
  if (lead.human_requested) return; // already waiting, don't repeat the message
  await leadsService.update(lead.id, { human_requested: true });
  await wa.sendText(lead.phone, T.HUMAN_QUEUED, lead.id);
  console.log(`[HUMAN REQUESTED] Lead: ${lead.phone} | State was: ${lead.state}`);
}

async function handleOptOut(lead) {
  await leadsService.update(lead.id, { opt_out: true });
  await wa.sendText(lead.phone, T.OPT_OUT, lead.id);
}

async function handleNurture(lead) {
  await wa.sendText(lead.phone, T.WELCOME(lead.name), lead.id);
}

module.exports = {
  handleMenu,
  handleMenuInfo,
  handleQual0,
  handleQual1,
  handleQual2,
  handleQual3,
  handleQual4,
  handleQual5,
  handleQual6,
  handleScored,
  handleMeetingSent,
  handleScheduled,
  handleHumanQueued,
  handleOptOut,
  handleNurture,
  QUAL_FIELD,
};
