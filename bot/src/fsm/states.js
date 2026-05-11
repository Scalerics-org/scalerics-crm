const S = {
  NEW: 'NEW',
  MENU: 'MENU',
  MENU_INFO: 'MENU_INFO',
  QUAL_0: 'QUAL_0',
  QUAL_1: 'QUAL_1',
  QUAL_2: 'QUAL_2',
  QUAL_3: 'QUAL_3',
  QUAL_4: 'QUAL_4',
  QUAL_5: 'QUAL_5',
  QUAL_6: 'QUAL_6',
  QUAL_7: 'QUAL_7',
  SCORED: 'SCORED',
  MEETING_SENT: 'MEETING_SENT',
  SCHEDULED: 'SCHEDULED',
  NURTURE: 'NURTURE',
  DISQUALIFIED: 'DISQUALIFIED',
  HUMAN_QUEUED: 'HUMAN_QUEUED',
  OPT_OUT: 'OPT_OUT',
};

// States where numbered options are expected and invalid input should be retried
const QUALIFYING_STATES = new Set([
  S.QUAL_1, S.QUAL_2, S.QUAL_3, S.QUAL_4, S.QUAL_5,
]);
// QUAL_6 and QUAL_7 are free-text — any input advances, no retry

// Global keyword map: input → target state (takes priority over FSM table)
// "ayuda" removed: too ambiguous (users say "ayuda" meaning their real problem, not "get me a human")
const GLOBAL_KEYWORDS = {
  humano: S.HUMAN_QUEUED,
  asesor: S.HUMAN_QUEUED,
  persona: S.HUMAN_QUEUED,
  stop: S.OPT_OUT,
  baja: S.OPT_OUT,
  cancelar: S.OPT_OUT,
  listo: S.MENU,   // re-activates nurture leads
  menu: S.MENU,
};

module.exports = { S, QUALIFYING_STATES, GLOBAL_KEYWORDS };
