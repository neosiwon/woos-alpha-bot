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
    + '\n보유한계 ' + ep.COMMON.HOLD_HOURS + 'H (TP1 도달 시 본절 STOP LOSS)';
}

// 매도상태 — 🔥 강한 마름(소진=발사임박 가설) / 🟠 보통 (검증중)
function fmtSellState(s) {
  const map = {
    DRY: '🔥 강한 마름 (소진 임박)',
    SELL_ONLY: '🔥 강한 마름 (매도만 출회)',
    WEAK: '🟠 보통 (매도 우위)',
    NORMAL: '🟠 보통',
    NONE: '🟠 보통',
  };
  return map[s] || '🟠 보통';
}

function fmtSpike(spike, spikeTs) {
  if (spike == null) return '-';
  const eok = spike / 1e8;
  const t = spikeTs ? ' @' + String(spikeTs).slice(11, 16) : '';
  return eok.toFixed(2) + '억' + t;
}

function buildMsg(s) {
  const kor = upbit.getKoreanName(s.symbol);
  const name = kor ? kor + ' [' + s.symbol + ']' : '[' + s.symbol + ']';
  const regimeStr = s.regime === 'STRONG' ? '📈 강세'
    : s.regime === 'WEAK' ? '📉 약세'
    : '⚪ 판정중(약세기준)';
  return '🚨 매집신호 감지 (업비트)\n'
    + '─────────────\n'
    + name + '\n'
    + '매집 ' + fmtSpike(s.spike, s.spikeTs) + ' (순매수 스파이크)\n'
    + '수축 ' + fmtBox(s.boxPct) + '\n'
    + '매도상태 ' + fmtSellState(s.sellState) + '\n'
    + 'BTC ' + regimeStr + '\n'
    + fmtStrategy(s);
}

async function sendTelegram(signals) {
  if (!signals || !signals.length) return;
  await upbit.ensureKoreanNames(); // 한글명 캐시 보장 (fetchUniverse 미호출 대비)
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
