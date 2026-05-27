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
      body: JSON.stringify({ chat_id: chatId, text, disable_web_page_preview: true }),
    });
    if (!r.ok) console.error('[notify] telegram fail ' + r.status);
  } catch (e) { console.error('[notify] send error: ' + e.message); }
}

// 두 채널 ID 결정 (fallback)
function _privateChat() {
  return cfg.TELEGRAM_CHAT_ID_PRIVATE || cfg.TELEGRAM_CHAT_ID;
}
function _groupChat() {
  return cfg.TELEGRAM_CHAT_ID_GROUP || _privateChat();
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
  const chatId = _privateChat();
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
  return [
    '🚨매수 신호 (업비트)',
    '─────────────',
    '▶' + _name(s.symbol),
    `🔥 평단 진입 ${sign(s.distNow)}${s.distNow.toFixed(2)}% (점화)`,
    `📢매수추천가 >${fmtPrice(buy)}<`,
    '',
    `흡수 거래 ${s.surge.toFixed(1)}배↑ / 가격 ${s.range.toFixed(1)}%`,
    `박스 상단 +${s.toTopPct.toFixed(1)}% (저항 얇음)`,
    _wallStr(s.wallRatioPct).trimEnd(),
    `24h 평단 ${fmtPrice(s.vwap)}`,
    _regimeStr(s.regime),
    '─────────────',
    `📋 매수가 ${fmtPrice(buy)} 부근`,
    `🛑 손절 ${fmtPrice(stop)} (${E.STOP_PCT}%)`,
    `🎯 TP1 ${fmtPrice(tp1)} (+${E.TP1_PCT}%) → ${E.TP1_WEIGHT * 100}%`,
    `🎯 TP2 ${fmtPrice(tp2)} (+${E.TP2_PCT}%) → ${E.TP2_WEIGHT * 100}%`,
    `🎯 TP3 ${fmtPrice(tp3)} (+${E.TP3_PCT}%) → ${E.TP3_WEIGHT * 100}%`,
    `보유한계 ${E.HOLD_HOURS}H (TP1 도달 시 본절)`,
  ].filter(l => l !== '').join('\n');
}

// 기존 호환: sendTelegram(signals) — 3차 알람 (PRIVATE + GROUP)
async function sendTelegram(signals) {
  if (!signals || !signals.length) return;
  await upbit.ensureKoreanNames();
  const privateChat = _privateChat();
  const groupChat = _groupChat();
  const groupSame = (groupChat === privateChat);
  for (const s of signals) {
    const text = _build3(s);
    await _send(privateChat, text);
    if (!groupSame) await _send(groupChat, text);
  }
}

module.exports = { sendTelegram, sendStage2 };

