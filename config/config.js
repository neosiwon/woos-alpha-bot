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
  // 매집 스파이크 — 5/25 전종목(259) 검증: 5분 순매수 스파이크 상위 = 세력 매집 종목.
  // 매집+수축 그룹 +5%상승 47% vs 죽은수축 7% (7배). 누적/비율/배율은 변별력 없어 기각.
  SPIKE: {
    WINDOW_MIN: 5,        // 5분 슬라이딩 (1틱은 노이즈, 5분이 매집펄스 단위 — 검증)
    TOP_N: 15             // 제외 후 스파이크 상위 N개를 후보로 (알파 5/25 상위 6위 내, 여유 두고 15)
    // [검증대기] TOP_N 적정값 — 상위N개(순위) vs 상위N%(동적) 며칠 데이터 후 조정
  },
  EXIT_PARAMS: {
    COMMON: { STOP_PCT: -5, HOLD_HOURS: 4 },
    STRONG: { TP1: 7, TP2: 10, TP3: 12, W1: 0.50, W2: 0.30, W3: 0.20 },
    WEAK:   { TP1: 7, TP2: 8,  TP3: 12, W1: 0.50, W2: 0.30, W3: 0.20 }
  },
  MARKET_REGIME: {
    STRONG: 'BTC.D down + USDT.D down',
    WEAK: 'USDT.D up'
  },
  REGIME_LOOKBACK_HOURS: 4,
  REGIME_CHANGE_THRESHOLD: 0.3,
  DOMINANCE_FILE: process.env.WOOS_DOM_FILE || '/home/neosiwon/woos-alpha-bot/dominance.json',
  VERIFY_HOURS: 4,
  VERIFY_LOG_FILE: process.env.WOOS_VERIFY_FILE || '/home/neosiwon/woos-alpha-bot/signals_log.csv',
  VERIFY_TRACK_FILE: process.env.WOOS_TRACK_FILE || '/home/neosiwon/woos-alpha-bot/tracking.json',
  EXCHANGE: 'upbit',
  // 제외 명단 (스캔/신호 제외) — 시총 ADA 이상 대형 + 수동 추가. 단타 펌핑 구조 불가.
  // 스테이블3 / 대형8(시총 ADA 12.5조 이상) / XLM(7.1조, 5/25 노이즈로 상위 떠서 수동 추가)
  // 가감은 이 배열에서 직접. ONDO/SUI/NEAR 등 ADA 미만은 후보 유지(알파 이력 있음).
  MAJORS: ['USDT','USDC','DAI','BTC','ETH','XRP','SOL','DOGE','ADA','BNB','TRX','XLM'],
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
