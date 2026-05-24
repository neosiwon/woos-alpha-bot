const cfg = require('../config/config');

function fmtBox(boxPct) {
  const s = cfg.SQUEEZE;
  const focus = (boxPct >= s.SWEET_MIN && boxPct <= s.SWEET_MAX) ? ' ✅(집중)' : '';
  return boxPct.toFixed(2) + '%' + focus;
}

function buildMsg(s) {
  return '🚨 알파 신호감지\n'
    + '종목: ' + s.symbol + '\n'
    + '거래소: 업비트\n'
    + '수축: ' + fmtBox(s.boxPct) + '\n'
    + '체결강도: ' + s.execStrength.toFixed(1) + '%\n'
    + '중복: ' + (s.persistHits || 0) + '회 (10분 내)\n'
    + '거래대금: ' + Math.round(s.tradeValue / 10000).toLocaleString() + '만';
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
