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
  return '\n📋 진입가: ' + fmtPrice(e)
    + '\n손절: ' + fmtPrice(priceAt(e, ep.COMMON.STOP_PCT)) + ' (' + ep.COMMON.STOP_PCT + '%, 1차익절 후 본절)'
    + '\n익절1: ' + fmtPrice(priceAt(e, p.TP1)) + ' (+' + p.TP1 + '%, ' + w(p.W1) + '%)'
    + '\n익절2: ' + fmtPrice(priceAt(e, p.TP2)) + ' (+' + p.TP2 + '%, ' + w(p.W2) + '%)'
    + '\n익절3: ' + fmtPrice(priceAt(e, p.TP3)) + ' (+' + p.TP3 + '%, ' + w(p.W3) + '%)'
    + '\n보유한계: ' + ep.COMMON.HOLD_HOURS + '시간';
}

// 매도 소진 상태 라벨 (추천안 — 검증중, 발송조건 아님)
function fmtSellState(s) {
  const map = {
    DRY: '🔥 매도 소진(마름)',
    SELL_ONLY: '⚠️ 매도만 출회',
    WEAK: '매도 우위',
    NORMAL: '정상',
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
  return '🚨 알파 신호감지\n'
    + '종목: ' + (upbit.getKoreanName(s.symbol) ? upbit.getKoreanName(s.symbol) + '(' + s.symbol + ')' : s.symbol) + '\n'
    + '거래소: 업비트\n'
    + '국면: ' + (s.regime === 'STRONG' ? '강세' : s.regime === 'WEAK' ? '약세' : '판정중(약세기준)') + '\n'
    + '현재가: ' + fmtPrice(s.referencePrice) + '\n'
    + '수축: ' + fmtBox(s.boxPct) + '\n'
    + '매집: ' + fmtSpike(s.spike5m, s.spikeTs) + ' (5분 순매수 최대)\n'
    + '매도상태: ' + fmtSellState(s.sellState) + '\n'
    + '거래대금: ' + (s.tradeValue != null ? Math.round(s.tradeValue / 10000).toLocaleString() + '만' : '-')
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
