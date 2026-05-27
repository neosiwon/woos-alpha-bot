#!/bin/bash
# Patch: 3차 알람 포맷 + 새 MONITOR 채널 (2차+3차) 분기
# 사용법: VM의 ~/woos-alpha-bot/ 에서 실행
#
# ⚠️ 실행 전 ~/woos-alpha-bot/.env 에 아래 한 줄 추가 필요:
#   TELEGRAM_CHAT_ID_MONITOR=-5197822664

set -e
cd ~/woos-alpha-bot

# .env 체크 (안내만)
if [ -f .env ] && ! grep -q "TELEGRAM_CHAT_ID_MONITOR" .env; then
  echo ""
  echo "⚠️  .env 에 다음 줄을 추가하세요 (없으면 모니터 그룹으로 안 보냄):"
  echo "    TELEGRAM_CHAT_ID_MONITOR=-5197822664"
  echo ""
  read -p "지금 추가하고 진행할까요? [y/N] " yn
  if [[ "$yn" =~ ^[Yy]$ ]]; then
    echo "TELEGRAM_CHAT_ID_MONITOR=-5197822664" >> .env
    echo "추가 완료."
  else
    echo "직접 추가 후 다시 실행하세요."
    exit 1
  fi
fi

echo "=== Stop ==="
sudo systemctl stop woos-alpha-bot 2>/dev/null || true

echo "=== Backup ==="
cp bot/notify.js bot/notify.js.bak_$(date +%s)
cp config/config.js config/config.js.bak_$(date +%s)

echo "=== Write notify.js ==="
cat > bot/notify.js << 'FILE_EOF_NOTIFY'
// bot/notify.js — 텔레그램 알람 (v3 — 2차 감시 + 3차 매수신호)
//
// 채널 분리:
//   2차 (감시 등록)  → PRIVATE only
//   3차 (매수 신호)  → PRIVATE + GROUP (GROUP 미설정 시 PRIVATE만)
//
// 알람 내용:
//   - 한글 티커 (upbit.getKoreanName)
//   - BTC 강세/약세 (dominance.judge 외부 주입)
//   - 흡수/박스/매도벽(표시)/평단/TP/손절

const cfg = require('../config/config');
const upbit = require('./exchange/upbit');

// 가격 포맷 (저가코인까지 유효숫자 보이게)
function fmtPrice(p) {
  if (p == null) return '-';
  if (p >= 100) return Math.round(p).toLocaleString() + '원';
  if (p >= 1) return p.toFixed(1) + '원';
  if (p >= 0.1) return p.toFixed(3) + '원';
  if (p >= 0.01) return p.toFixed(4) + '원';
  if (p >= 0.001) return p.toFixed(5) + '원';
  return p.toPrecision(3) + '원';
}

function _name(sym) {
  const ko = upbit.getKoreanName(sym);
  return ko ? `${ko} [${sym}]` : `[${sym}]`;
}

function _regimeStr(regime) {
  if (regime === 'STRONG') return 'BTC 📈 강세';
  if (regime === 'WEAK')   return 'BTC 📉 약세';
  return 'BTC ⚪ 판정중';
}

function _wallStr(wallRatioPct) {
  if (wallRatioPct == null) return '';
  return `매도벽 ${wallRatioPct.toFixed(2)}% (참고)\n`;
}

// === 텔레그램 발송 헬퍼 ===
async function _send(chatId, text) {
  if (!cfg.TELEGRAM_BOT_TOKEN || !chatId) {
    console.log('[notify] (no token/chat)\n' + text);
    return;
  }
  try {
    const r = await fetch('https://api.telegram.org/bot' + cfg.TELEGRAM_BOT_TOKEN + '/sendMessage', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text, parse_mode: 'HTML', disable_web_page_preview: true }),
    });
    if (!r.ok) console.error('[notify] telegram fail ' + r.status);
  } catch (e) { console.error('[notify] send error: ' + e.message); }
}

// 채널 분기:
//   2차 + 3차 → MONITOR (없으면 PRIVATE/CHAT_ID fallback)
//   3차만   → GROUP (단톡, 없으면 MONITOR 와 통합)
function _monitorChat() {
  return cfg.TELEGRAM_CHAT_ID_MONITOR
      || cfg.TELEGRAM_CHAT_ID_PRIVATE
      || cfg.TELEGRAM_CHAT_ID;
}
function _groupChat() {
  return cfg.TELEGRAM_CHAT_ID_GROUP || null;
}

// === 2차 알람 (감시 등록 — PRIVATE only) ===
// In-memory 쿨다운 캐시 (재시작 시 초기화 — OK, 어차피 첫 시작 알람은 가치 있음)
const _stage2Cache = {}; // {symbol: lastNotifyMs}

function _build2(c, regime) {
  return [
    '📋 매집 후보 감지 (업비트)',
    '─────────────',
    '▶' + _name(c.symbol),
    `흡수 거래 ${c.surge.toFixed(1)}배↑ / 가격 ${c.range.toFixed(1)}% @${c.absorbTime}`,
    `박스 상단 +${c.toTopPct.toFixed(1)}% (저항 얇음 ✅)`,
    _wallStr(c.wallRatioPct).trimEnd(),
    _regimeStr(regime),
    '─────────────',
    `📋 현재가 ${fmtPrice(c.referencePrice)}`,
    `⏳ 평단 진입 대기 중`,
  ].filter(l => l !== '').join('\n');
}

// 2차 알람 발송 (index.js에서 candidates 받은 직후 호출)
async function sendStage2(candidates, regime) {
  if (!candidates || !candidates.length) return;
  await upbit.ensureKoreanNames();
  const now = Date.now();
  const cdMs = (cfg.STAGE2_COOLDOWN_MIN || 60) * 60 * 1000;
  const chatId = _monitorChat();
  let sent = 0;
  for (const c of candidates) {
    const last = _stage2Cache[c.symbol] || 0;
    if (now - last < cdMs) continue;
    _stage2Cache[c.symbol] = now;
    await _send(chatId, _build2(c, regime));
    sent++;
  }
  if (sent) console.log(`[notify] 2차 알람 ${sent}건 발송`);
}

// === 3차 알람 (매수 신호 — PRIVATE + GROUP) ===
function _build3(s) {
  const E = cfg.EXIT;
  const buy = s.referencePrice;
  const stop = buy * (1 + E.STOP_PCT / 100);
  const tp1  = buy * (1 + E.TP1_PCT / 100);
  const tp2  = buy * (1 + E.TP2_PCT / 100);
  const tp3  = buy * (1 + E.TP3_PCT / 100);
  const sign = (n) => n >= 0 ? '+' : '';
  const wallLine = s.wallRatioPct != null
    ? `• 매도벽 ${s.wallRatioPct.toFixed(2)}% (참고)`
    : null;
  return [
    '🚨 매수 신호 (업비트)',
    '─────────────',
    `▶ 코인명 : <b>${_name(s.symbol)}</b>`,
    '',
    `━━ 🔥 <b>매수가 ${fmtPrice(buy)}</b> 🔥 ━━`,
    '',
    `• 평단 진입 ${sign(s.distNow)}${s.distNow.toFixed(2)}% (점화)`,
    `• 매집진행 거래 ${s.surge.toFixed(1)}배↑ / 가격 ${s.range.toFixed(1)}%`,
    `• 박스 상단 +${s.toTopPct.toFixed(1)}% (저항 얇음)`,
    wallLine,
    `• ${_regimeStr(s.regime)}`,
    '─────────────',
    `🛑 손절가 ${fmtPrice(stop)} (${E.STOP_PCT}%)`,
    '',
    `🎯 TP1 ${fmtPrice(tp1)} (+${E.TP1_PCT}%) → ${E.TP1_WEIGHT * 100}%`,
    `🎯 TP2 ${fmtPrice(tp2)} (+${E.TP2_PCT}%) → ${E.TP2_WEIGHT * 100}%`,
    `🎯 TP3 ${fmtPrice(tp3)} (+${E.TP3_PCT}%) → ${E.TP3_WEIGHT * 100}%`,
    `• 보유한계 ${E.HOLD_HOURS}H (TP1 도달 시 본절)`,
  ].filter(l => l != null).join('\n');
}

// 기존 호환: sendTelegram(signals) — 3차 알람 (PRIVATE + GROUP)
async function sendTelegram(signals) {
  if (!signals || !signals.length) return;
  await upbit.ensureKoreanNames();
  const monitorChat = _monitorChat();
  const groupChat = _groupChat();
  for (const s of signals) {
    const text = _build3(s);
    await _send(monitorChat, text);
    // 단톡 GROUP 이 설정돼있고 MONITOR 와 다르면 추가 발송
    if (groupChat && groupChat !== monitorChat) {
      await _send(groupChat, text);
    }
  }
}

module.exports = { sendTelegram, sendStage2 };
FILE_EOF_NOTIFY

echo "=== Write config.js ==="
cat > config/config.js << 'FILE_EOF_CONFIG'
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
FILE_EOF_CONFIG

echo "=== Syntax check ==="
node --check bot/notify.js && echo "notify OK"
node --check config/config.js && echo "config OK"

echo "=== Git push ==="
git add bot/notify.js config/config.js
git commit -m "notify: 3rd alarm format + MONITOR channel (2nd+3rd)" || echo "(nothing to commit)"
git push || echo "(push manual)"

echo "=== Restart ==="
sudo systemctl start woos-alpha-bot
sleep 3
sudo systemctl status woos-alpha-bot --no-pager | head -10
echo ""
echo "=== DONE ==="
echo "채널 라우팅:"
echo "  - MONITOR (-5197822664): 2차 매집후보 + 3차 매수신호"
echo "  - GROUP   (기존 단톡):  3차 매수신호만"
