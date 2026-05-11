const { S, QUALIFYING_STATES, GLOBAL_KEYWORDS } = require('./states');
const { TRANSITIONS, QUAL_OPTIONS } = require('./transitions');
const handlers = require('./handlers');
const session = require('../services/session');
const leadsService = require('../services/leads');
const wa = require('../services/whatsapp');
const T = require('../messages/templates');

const MAX_RETRIES = 4;

function normalizeInput(text) {
  if (!text) return '';
  return text.trim().toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
}

function resolveGlobalKeyword(input) {
  for (const [kw, target] of Object.entries(GLOBAL_KEYWORDS)) {
    if (input.includes(kw)) return target;
  }
  return null;
}

async function process(lead, rawInput, waMsgId) {
  const input = normalizeInput(rawInput);

  // --- OPT OUT: silently ignore ---
  if (lead.opt_out) return;

  // --- Human took over: only allow MENU keyword to escape ---
  if (lead.human_requested) {
    const globalTarget = resolveGlobalKeyword(input);
    if (globalTarget === S.MENU) {
      await leadsService.update(lead.id, { human_requested: false });
      await _executeTransition(lead, input, lead.state, S.MENU);
    }
    return;
  }

  // --- Global keywords take priority ---
  const globalTarget = resolveGlobalKeyword(input);
  if (globalTarget) {
    await _executeTransition(lead, input, lead.state, globalTarget);
    return;
  }

  // --- Get current session state (Redis) or fall back to DB state ---
  let sess = await session.getSession(lead.phone);
  const currentState = sess?.state || lead.state || S.NEW;

  // --- Lookup transition ---
  const stateMap = TRANSITIONS[currentState] || {};
  const nextState = stateMap[input] || stateMap['*'];

  if (!nextState) {
    // No transition defined at all — show menu
    await _executeTransition(lead, input, currentState, S.MENU);
    return;
  }

  // --- Invalid input handling for qualifying states ---
  const isInvalidInput = QUALIFYING_STATES.has(currentState) && nextState === currentState;

  if (isInvalidInput) {
    const retries = await session.incrementRetry(lead.phone);

    if (retries >= MAX_RETRIES) {
      await session.clearRetry(lead.phone);
      await _executeTransition(lead, input, currentState, S.HUMAN_QUEUED);
      return;
    }

    const options = QUAL_OPTIONS[currentState] || '';
    if (retries === 1) {
      await wa.sendText(lead.phone, T.INVALID_INPUT_1(options), lead.id);
    } else {
      await wa.sendText(lead.phone, T.INVALID_INPUT_2, lead.id);
    }
    return;
  }

  // Valid input — clear retries
  await session.clearRetry(lead.phone);
  await _executeTransition(lead, input, currentState, nextState);
}

async function _executeTransition(lead, input, fromState, toState) {
  // Run the appropriate handler; handler may override toState (e.g., scoring)
  let finalState = toState;

  switch (toState) {
    case S.MENU:
      await handlers.handleMenu(lead);
      break;
    case S.MENU_INFO:
      await handlers.handleMenuInfo(lead);
      break;
    case S.QUAL_0:
      await handlers.handleQual0(lead);
      break;
    case S.QUAL_1:
      await handlers.handleQual1(lead, input, fromState);
      break;
    case S.QUAL_2:
      await handlers.handleQual2(lead, input);
      break;
    case S.QUAL_3:
      await handlers.handleQual3(lead, input);
      break;
    case S.QUAL_4:
      await handlers.handleQual4(lead, input);
      break;
    case S.SCORED:
      finalState = await handlers.handleScored(lead, input) || toState;
      break;
    case S.MEETING_SENT:
      if (fromState === S.QUAL_4) {
        if (input) await leadsService.update(lead.id, { colors: input });
        await wa.sendText(lead.phone, T.MEETING_OFFER(lead.name), lead.id);
      } else {
        await handlers.handleMeetingSent(lead, input);
      }
      break;
    case S.SCHEDULED:
      await handlers.handleScheduled(lead);
      break;
    case S.HUMAN_QUEUED:
      await handlers.handleHumanQueued(lead);
      break;
    case S.OPT_OUT:
      await handlers.handleOptOut(lead);
      break;
    case S.NURTURE:
      // Handled by scoring, no extra message here
      break;
    case S.DISQUALIFIED:
      // Handled by scoring, no extra message here
      break;
    default:
      await handlers.handleMenu(lead);
      finalState = S.MENU;
  }

  // Persist state
  await session.setSession(lead.phone, { state: finalState, leadId: lead.id });
  await leadsService.update(lead.id, { state: finalState });
}

module.exports = { process };
