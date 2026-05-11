const { S } = require('./states');

// Transition table: { [fromState]: { [input]: nextState } }
// '*' is the fallback when no specific input matches
// Handlers are wired in engine.js based on nextState

const TRANSITIONS = {
  [S.NEW]: {
    '*': S.MENU,
  },
  [S.MENU]: {
    '1': S.MENU_INFO,
    '2': S.QUAL_0,
    '3': S.HUMAN_QUEUED,
    '*': S.MENU,
  },
  [S.QUAL_0]: {
    '*': S.QUAL_1,
  },
  [S.MENU_INFO]: {
    '1': S.QUAL_1,
    '2': S.MENU,
    '*': S.MENU_INFO,
  },
  [S.QUAL_1]: {
    '1': S.QUAL_2,
    '2': S.QUAL_2,
    '3': S.QUAL_2,
    '4': S.QUAL_2,
    '5': S.QUAL_2,
    '*': S.QUAL_1, // invalid → retry (engine handles retry counter)
  },
  [S.QUAL_2]: {
    '1': S.QUAL_3,
    '2': S.QUAL_3,
    '3': S.QUAL_3,
    '4': S.QUAL_3,
    '5': S.QUAL_3,
    '*': S.QUAL_2,
  },
  [S.QUAL_3]: {
    '1': S.QUAL_4,
    '2': S.QUAL_4,
    '3': S.QUAL_4,
    '4': S.QUAL_4,
    '*': S.QUAL_3,
  },
  [S.QUAL_4]: {
    '1': S.QUAL_5,
    '2': S.QUAL_5,
    '3': S.QUAL_5,
    '*': S.QUAL_4,
  },
  [S.QUAL_5]: {
    '1': S.QUAL_6,
    '2': S.QUAL_6,
    '3': S.QUAL_6,
    '*': S.QUAL_5,
  },
  [S.QUAL_6]: {
    '*': S.QUAL_7,
  },
  [S.QUAL_7]: {
    '*': S.SCORED,
  },
  [S.MEETING_SENT]: {
    '1': S.MEETING_SENT, // re-send link (handled in engine)
    '2': S.MEETING_SENT, // send more info
    '*': S.MEETING_SENT,
  },
  [S.SCHEDULED]: {
    '1': S.SCHEDULED, // confirmed reminder
    '*': S.SCHEDULED,
  },
  [S.NURTURE]: {
    '*': S.MENU, // any message re-activates (listo keyword already handled globally)
  },
  [S.DISQUALIFIED]: {
    '*': S.MENU,
  },
  [S.HUMAN_QUEUED]: {
    '*': S.HUMAN_QUEUED, // wait for human
  },
  [S.OPT_OUT]: {
    '*': S.OPT_OUT,
  },
};

// Valid numeric options per qualifying state (used for invalid input messages)
const QUAL_OPTIONS = {
  [S.QUAL_1]: '*1* Agencia/Consultora\n*2* E-commerce\n*3* Servicios profesionales\n*4* SaaS/Software\n*5* Otro',
  [S.QUAL_2]: '*1* Respuesta lenta\n*2* Tareas repetitivas\n*3* Sin métricas\n*4* No puedo escalar\n*5* Otro',
  [S.QUAL_3]: '*1* Solo yo\n*2* 2-5 personas\n*3* 6-20 personas\n*4* Más de 20',
  [S.QUAL_4]: '*1* Presupuesto disponible\n*2* Depende del ROI\n*3* Aún no',
  [S.QUAL_5]: '*1* Este mes\n*2* 2-3 meses\n*3* Evaluando',
};

module.exports = { TRANSITIONS, QUAL_OPTIONS };
