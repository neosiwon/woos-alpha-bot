const cfg = require('../config/config');
const upbit = require('./exchange/upbit');

function fmtBox(boxPct) {
  const s = cfg.SQUEEZE;
  const focus = (boxPct >= s.SWEET_MIN && boxPct <= s.SWEET_MAX) ? ' ✅(집중)' : '';
  return boxPct.toFixed(2) + '%' + focus;
}

// 저가코인까지 유효숫자 보이게
function fmtPrice(p) {
  if (p == null) return '-';
  if (p >= 100) return Math.round(p).toLocaleString() + '원';
  if (p >= 1) return p.toFixed(1) + '원';
  if (p >= 0.1) return p.toFixed(3) + '원';
  if (p >= 0.01) return p.toFixed(4) + '원';
  if (p >= 0.001) return p.toFixed(5) + '원';
  return p.toPrecision(3) + '원';
}

function priceAt(e, pct) { return e * (1 + pct / 100); }

function fmtStrategy(s) {
  const ep = cfg.EXIT_PARAMS;
  const p = (s.regime === 'STRONG') ? ep.STRONG : ep.WEAK; // UNKNOWN=약세 보수
  const e = s.referencePrice;
  const w = (x) => Math.round(x * 100);
  return '─────────────'
    + '\n📋 현재가 ' + fmtPrice(e)
    + '\n🛑 손절 ' + fmtPrice(priceAt(e, ep.COMMON.STOP_PCT)) + ' (' + ep.COMMON.STOP_PCT + '%)'
    + '\n🎯 TP1 ' + fmtPrice(priceAt(e, p.TP1)) + ' → ' + w(p.W1) + '% 익절'
    + '\n🎯 TP2 ' + fmtPrice(priceAt(e, p.TP2)) + ' → ' + w(p.W2) + '% 익절'
    + '\n🎯 TP3 ' + fmtPrice(priceAt(e, p.TP3)) + ' → ' + w(p.W3) + '% 익절'
    + '\n▶ 보유한계 ' + ep.COMMON.HOLD_HOURS + 'h (TP1 도달 시 손절→본절)';
}

// 신호 강도 — 매도상태 기준 (소진=발사임박=강). 임의 임계 없이 검증된 가설로 등급.
function fmtGrade(sellState) {
  return (sellState === 'DRY' || sellState === 'SELL_ONLY') ? '🔥 강' : '🟠 중';
}

// 매도상태 라벨 (추천안 — 검증중)
function fmtSellState(s) {
  const map = {
    DRY: '🔥 소진 임박',
    SELL_ONLY: '🔥 소진 임박',
    WEAK: '⏳ 대기 (매도 우위)',
    NORMAL: '⏳ 대기',
    NONE: '-',
  };
  return map[s] || '-';
}

function fmtSpike(spike5m, spikeTs) {
  if (spike5m == null) return '-';
  const eok = spike5m / 1e8;
  const t = spikeTs ? ' @' + String(spikeTs).slice(11, 16) : '';
  return eok.toFixed(2) + '억' + t;
}

function buildMsg(s) {
  const name = upbit.getKoreanName(s.symbol)
    ? upbit.getKoreanName(s.symbol) + '(' + s.symbol + ')'
    : s.symbol;
  const regimeStr = s.regime === 'STRONG' ? '📈 강세'
    : s.regime === 'WEAK' ? '📉 약세'
    : '⚪ 판정중(약세기준)';
  return '🚨 매집신호 감지 (업비트)\n'
    + '[' + fmtGrade(s.sellState) + '] ' + name + '\n'
    + '▶ 매집 ' + fmtSpike(s.spike5m, s.spikeTs) + ' (5분 순매수)\n'
    + '▶ 수축 ' + fmtBox(s.boxPct) + '\n'
    + '▶ 매도상태: ' + fmtSellState(s.sellState) + '\n'
    + '▶ BTC: ' + regimeStr + '\n'
    + fmtStrategy(s);
}

async function sendTelegram(signals) {
  if (!signals || !signals.length) return;
  const token = cfg.TELEGRAM_BOT_TOKEN;
  const chatId = cfg.TELEGRAM_CHAT_ID;
  if (!token || !chatId) {
    console.log('[notify] telegram not set, console only');
    for (const s of signals) console.log(buildMsg(s));
    return;
  }
  for (const s of signals) {
    try {
      const r = await fetch('https://api.telegram.org/bot' + token + '/sendMessage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatId, text: buildMsg(s) }),
      });
      if (!r.ok) console.error('[notify] telegram fail ' + r.status);
    } catch (e) { console.error('[notify] send error: ' + e.message); }
  }
}

module.exports = { sendTelegram };
