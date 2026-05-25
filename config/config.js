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
  // 매집 스파이크 — 5/25 알파 3개(CPOOL/SOON/ERA) 역산. 1틱이 진짜/노이즈 분리 최고.
  // 알파 1틱 스파이크 상위 0.4~2.0% (상위5위 내), 노이즈는 9%대 → 윈도우 짧을수록 분리도↑.
  // 5분은 노이즈를 끌어올림(5분합이 죽은코인도 부풀림). 1틱 상위5 = 알파3 다포함/노이즈0.
  SPIKE: {
    WINDOW_MIN: 1,        // 1틱(1분) 순매수 스파이크. 수집간격 60초라 1=1틱. (5분은 노이즈 부풀림)
    TOP_PCT: 2.5,         // 제외 후 스파이크 상위 N% 동적 컷 (알파 상위0.4~2.0%, 250종목 기준 ~6개)
    TOP_MIN: 3,           // 종목수 적은 날 최소 후보 (상위% 너무 적게 나올 때 보정)
    TOP_MAX: 10,          // 너무 많아질 때 상한 (정신없음 방지)
    MAX_AGE_HOURS: 4,     // 매집 스파이크 유효시간 — 최근 N시간 이내 매집만 후보 (오래된 매집=김빠짐 제외)
    // 장초반 표시 — 업비트 9시 일초기화 직후 매집 쏠림 관찰됨(5/25 상위30 중 9~11시 27%, 기대치 3배).
    // 필터 아닌 표시만 (오후/저녁 알파도 많음 — IN 13:52/19:20, SOON 19:13). 시간대 수정 가능.
    EARLY_HOUR_START: 7,  // 장초반 시작 시각(KST, 포함)
    EARLY_HOUR_END: 11    // 장초반 종료 시각(KST, 이하). 매집 스파이크가 이 구간이면 ⏰ 라벨
    // MAX_AGE_HOURS 4 + 장초반 7~11시 → 15시 이후엔 장초반 신호 자연 소멸(4h 만료)
    // [검증대기] TOP_PCT 2.5%·윈도우1틱 — 며칠 관찰 후 조정. 모두 여기서 수정 가능.
  },
  // 익절/손절 — 국면별 (5/9~25 알파 26건 백테스트, 50/30/20 분할, TP1도달시 손절→본절).
  // STRONG(강세): TP 7/15/25 = 거래당 평균 +8.3%, 도달률 96/42/23%. 강세장 표본 검증.
  // WEAK(약세): TP 5/10/15 = 보수값. 약세장 표본 없어 [검증대기] — 강세보다 빨리 확보.
  // UNKNOWN(판정불가) = WEAK 적용 (보수). 손절 -5는 -3~-10 무차별이라 표준값.
  EXIT_PARAMS: {
    COMMON: { STOP_PCT: -5, HOLD_HOURS: 4 },
    STRONG: { TP1: 7, TP2: 15, TP3: 25, W1: 0.50, W2: 0.30, W3: 0.20 },
    WEAK:   { TP1: 5, TP2: 10, TP3: 15, W1: 0.50, W2: 0.30, W3: 0.20 } // [검증대기]
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
