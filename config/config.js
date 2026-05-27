// woos-alpha-bot config v3 — 흡수+VWAP+저항 트리거 (2026-05-27 검증 기반)
// [검증대기] 표시 = 실측 안 된 값. 기존 표기 유지.
module.exports = {

  // ── 1차: 흡수 (매집 후보) ──────────────────────────────────────────
  // 거래량 ≥ 평소(하위25%) × N배 + 같은 윈도우 가격폭 ≤ X% = 흡수.
  // 5/27 백테스트: 거래3배+ AND 가격폭4%↓ → 후보 81개, 원본 5/6 포함.
  ABSORPTION: {
    LOOKBACK_HOURS: 3,            // 3시간 슬라이딩 윈도우
    VOLUME_SURGE_MIN: 3.0,
    PRICE_RANGE_MAX: 4.0,
    BASELINE_PERCENTILE: 25,      // 평소 = 하위 25% (발사시간 오염 방지)
    DEAD_COIN_MIN_24H_KRW: 1.0e9, // 거래대금 10억 미만 제외
  },

  // ── 2차: 저항 (발사 잠재력) ───────────────────────────────────────
  // 박스 상단(24h 고점)까지 ≥ N% 여유. 위가 비어야 큰 발사 가능.
  // 5/27: 81개 → 15개로 좁힘, 큰 발사(+10%↑) 모두 포함.
  // 매도벽 비율은 표시만 — 흡수시점 거래량 부족해 필터로는 못 씀.
  RESISTANCE: {
    BOX_TOP_MIN_PCT: 7.0,
    BOX_LOOKBACK_HOURS: 24,
    SHOW_WALL_RATIO: true,        // 알람에 표시만 (필터 X)
  },

  // ── 3차: 점화 (VWAP 진입) ─────────────────────────────────────────
  // 5분봉 종가가 24h VWAP ±1% 안 = 발사 점화.
  // 5/27 검증: 원본 4/5 잡힘 (PROS는 흡수~발사 갭 너무 커서 제외).
  // 쿨다운 30분으로 중복 방지.
  VWAP_ENTRY: {
    VWAP_HOURS: 24,
    ENTRY_BAND_PCT: 1.0,
    CANDLE_INTERVAL_MIN: 5,
  },

  // ── 익절/손절 (구버전 호환 위해 EXIT_PARAMS도 유지) ────────────────
  // 새 알람은 EXIT 사용. STOP -8%, TP1/2/3 = +5/10/15% (매수가 기준).
  // 5/27 6코인 비교 결과 평단×1.10 방식과 차이 미미 → 단순한 A 채택.
  EXIT: {
    STOP_PCT: -8.0,
    TP1_PCT: 5.0,  TP1_WEIGHT: 0.50,
    TP2_PCT: 10.0, TP2_WEIGHT: 0.30,
    TP3_PCT: 15.0, TP3_WEIGHT: 0.20,
    HOLD_HOURS: 4,
    TP1_TO_BREAKEVEN: true,
  },

  // ── 메이저 제외 ─────────────────────────────────────────────────
  // 시총 ADA 이상 + 스테이블 + XLM. 단타 펌핑 구조 불가.
  MAJORS: ['USDT','USDC','DAI','BTC','ETH','XRP','SOL','DOGE','ADA','BNB','TRX','XLM'],

  // ── 시장 국면 (BTC.D/USDT.D) ─────────────────────────────────────
  REGIME_LOOKBACK_HOURS: 4,
  REGIME_CHANGE_THRESHOLD: 0.3,
  DOMINANCE_FILE: process.env.WOOS_DOM_FILE || '/home/neosiwon/woos-alpha-bot/dominance.json',

  // ── 검증/리포트 ─────────────────────────────────────────────────
  VERIFY_HOURS: 4,
  VERIFY_LOG_FILE: process.env.WOOS_VERIFY_FILE || '/home/neosiwon/woos-alpha-bot/signals_log.csv',
  VERIFY_TRACK_FILE: process.env.WOOS_TRACK_FILE || '/home/neosiwon/woos-alpha-bot/tracking.json',
  DAILY_REPORT_HOUR: 9,
  REPORT_STATE_FILE: process.env.WOOS_REPORT_FILE || '/home/neosiwon/woos-alpha-bot/report_state.json',

  // ── 알람 채널 분리 ───────────────────────────────────────────────
  // PRIVATE: 운영자 본인 채팅 (2차/3차/리포트 모두)
  // GROUP  : 단톡방 (3차 점화 알람만, 안 설정되면 PRIVATE으로 fallback)
  TELEGRAM_BOT_TOKEN: process.env.TELEGRAM_BOT_TOKEN || null,
  TELEGRAM_CHAT_ID: process.env.TELEGRAM_CHAT_ID || null,                       // 기존 호환
  TELEGRAM_CHAT_ID_PRIVATE: process.env.TELEGRAM_CHAT_ID_PRIVATE || null,
  TELEGRAM_CHAT_ID_GROUP: process.env.TELEGRAM_CHAT_ID_GROUP || null,
  TELEGRAM_CHAT_ID_MONITOR: process.env.TELEGRAM_CHAT_ID_MONITOR || null,

  // ── 신호 쿨다운 ──────────────────────────────────────────────────
  SIGNAL_COOLDOWN_MIN: 30,        // 3차 발사 알람 쿨다운
  STAGE2_COOLDOWN_MIN: 60,        // 2차 감시 알람 쿨다운 (더 길게)

  // ── 운영 ────────────────────────────────────────────────────────
  LOOP_INTERVAL_SEC: 60,
  EXCHANGE: 'upbit',
  UPBIT_BATCH_SIZE: 5,
  UPBIT_BATCH_DELAY_MS: 1000,
  COLLECTOR_CSV_DIR: process.env.WOOS_CSV_DIR || '/home/neosiwon/woos_logs',
  COLLECTOR_CSV_PREFIX: 'woos_',
  ORDERBOOK_CSV_PREFIX: 'orderbook_',
  STATE_FILE: process.env.WOOS_STATE_FILE || '/home/neosiwon/woos-alpha-bot/state.json',

  // ── 구버전 호환 (기존 코드에서 참조하는 키들 — 삭제 X) ───────────
  ALPHA_TRIGGER: {
    SIGNAL_COOLDOWN_MIN: 30,
    EXEC_STRENGTH_MIN: 150,
    EXEC_USE_DYNAMIC: true,
  },
  SQUEEZE: { BOX_PCT_MAX: 5.0, LOOKBACK_MIN: 60, SWEET_MIN: 1.5, SWEET_MAX: 4.0 },
  SPIKE: { WINDOW_MIN: 1, TOP_PCT: 2.5, TOP_MIN: 3, TOP_MAX: 10, MAX_AGE_HOURS: 4, EARLY_HOUR_START: 7, EARLY_HOUR_END: 11 },
  EXIT_PARAMS: {
    COMMON: { STOP_PCT: -8, HOLD_HOURS: 4 },
    STRONG: { TP1: 5, TP2: 10, TP3: 15, W1: 0.50, W2: 0.30, W3: 0.20 },
    WEAK:   { TP1: 5, TP2: 10, TP3: 15, W1: 0.50, W2: 0.30, W3: 0.20 },
  },
  DEAD_COIN_MIN_24H_KRW: null,
};
