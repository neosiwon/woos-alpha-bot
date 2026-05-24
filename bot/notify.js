const cfg = require('../config/config');
const upbit = require('./exchange/upbit');

function fmtBox(boxPct) {
  const s = cfg.SQUEEZE;
  const focus = (boxPct >= s.SWEET_MIN && boxPct <= s.SWEET_MAX) ? ' ✅(집중)' : '';
  return boxPct.toFixed(2) + '%' + focus;
}

function fmtPrice(p) {
  if (p == null) return '-';
  if (p >= 100) return Math.round(p).toLocaleString() + '원';
  if (p >= 1) return p.toFixed(1) + '원';
  return p.toFixed(2) + '원';
}

function fmtStrategy(s) {
  const ep = cfg.EXIT_PARAMS;
  const p = (s.regime === 'STRONG') ? ep.STRONG : ep.WEAK; // UNKNOWN=약세 보수
  const w = (x) => Math.round(x * 100);
  return '\n📋 손절: ' + ep.COMMON.STOP_PCT + '% (1차 익절 후 본절)'
    + '\n익절: +' + p.TP1 + '%(' + w(p.W1) + '%)→+' + p.TP2 + '%(' + w(p.W2) + '%)→+' + p.TP3 + '%(' + w(p.W3) + '%)'
    + '\n보유한계: ' + ep.COMMON.HOLD_HOURS + '시간';
}

function buildMsg(s) {
  return '🚨 알파 신호감지\n'
    + '종목: ' + (upbit.getKoreanName(s.symbol) ? upbit.getKoreanName(s.symbol) + '(' + s.symbol + ')' : s.symbol) + '\n'
    + '거래소: 업비트\n'
    + '국면: ' + (s.regime === 'STRONG' ? '강세' : s.regime === 'WEAK' ? '약세' : '판정중') + '\n'
    + '현재가: ' + fmtPrice(s.referencePrice) + '\n'
    + '수축: ' + fmtBox(s.boxPct) + '\n'
    + '체결강도: ' + s.execStrength.toFixed(1) + '%\n'
    + '중복: ' + (s.persistHits || 0) + '회 (10분 내)\n'
    + '거래대금: ' + Math.round(s.tradeValue / 10000).toLocaleString() + '만'
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
