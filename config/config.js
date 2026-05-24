// woos-alpha-bot config — 단일 진실원 (근거는 docs 명세서)
// [검증대기] = 실측 안 된 값, null 유지, 임의값 금지 (원칙 0-0)
module.exports = {
  ALPHA_TRIGGER: {
    EXEC_STRENGTH_MIN: 150,
    EXEC_DYNAMIC_TOP_PCT: 5,
    EXEC_USE_DYNAMIC: true,
    MIN_POPULATION: null,
    MIN_TRADES: 30,
    MIN_SELL_KRW: 500000,
    MIN_VALUE_KRW: 10000000,
    PERSISTENCE_WINDOW: 10,
    PERSISTENCE_MIN_HITS: 3,
    PERSISTENCE_STRONG_HITS: 4,
    SIGNAL_COOLDOWN_MIN: 30
  },
  SQUEEZE: {
    BOX_PCT_MAX: 5.0,
    LOOKBACK_MIN: 60,
    SWEET_MIN: 1.5,
    SWEET_MAX: 4.0
  },
  EXIT_PARAMS: {
    COMMON: { STOP_PCT: -5 },
    STRONG: { ACTIVATE_PCT: 3, ATR_MULT: 3, HOLD_HOURS: 12 },
    WEAK: { ACTIVATE_PCT: 8, ATR_MULT: 2, HOLD_HOURS: 9 }
  },
  MARKET_REGIME: {
    STRONG: 'BTC.D down + USDT.D down',
    WEAK: 'USDT.D up'
  },
  EXCHANGE: 'upbit',
  MAJORS: [],
  UPBIT_BATCH_SIZE: 5,
  UPBIT_BATCH_DELAY_MS: 1000,
  COLLECTOR_CSV_DIR: process.env.WOOS_CSV_DIR || '/home/neosiwon/woos_logs',
  COLLECTOR_CSV_PREFIX: 'woos_',
  DEAD_COIN_MIN_24H_KRW: null,
  TELEGRAM_BOT_TOKEN: process.env.TELEGRAM_BOT_TOKEN || null,
  TELEGRAM_CHAT_ID: process.env.TELEGRAM_CHAT_ID || null,
  STATE_FILE: process.env.WOOS_STATE_FILE || './state.json',
  LOOP_INTERVAL_SEC: 60
};
