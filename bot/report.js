const fs = require('fs');
const cfg = require('../config/config');

// signals_log.csv 컬럼: 신호시각,종목,진입가,MFE%,MAE%,종료가,실현수익%,5%달성,국면,진입체결강도,청산사유
function _readLog() {
  try {
    let txt = fs.readFileSync(cfg.VERIFY_LOG_FILE, 'utf8');
    if (txt.charCodeAt(0) === 0xFEFF) txt = txt.slice(1);
    const lines = txt.trim().split('\n');
    const rows = [];
    for (let i = 1; i < lines.length; i++) {
      const f = lines[i].split(',');
      if (f.length < 11) continue;
      rows.push({
        time: f[0], symbol: f[1], entry: +f[2], mfe: +f[3], mae: +f[4],
        endPrice: +f[5], realized: +f[6], hit5: f[7], regime: f[8],
        execStrength: +f[9], reason: f[10],
      });
    }
    return rows;
  } catch (e) { return []; }
}

// 최근 N시간 신호만 (신호시각 KST 기준)
function _recent(rows, hours) {
  const now = Date.now();
  return rows.filter(r => {
    const t = new Date(r.time.replace(' ', 'T') + '+09:00').getTime();
    return Number.isFinite(t) && (now - t) <= hours * 3600000;
  });
}

function _isEarly(timeStr) {
  const hh = parseInt(String(timeStr).slice(11, 13), 10);
  const sp = cfg.SPIKE || {};
  if (sp.EARLY_HOUR_START == null || sp.EARLY_HOUR_END == null) return false;
  return hh >= sp.EARLY_HOUR_START && hh <= sp.EARLY_HOUR_END;
}

function buildReport(rows) {
  const n = rows.length;
  if (!n) return '📊 일일 성적표\n─────────────\n최근 24시간 확정 신호 없음.';

  const hit5 = rows.filter(r => r.hit5 === 'O').length;
  const avgMfe = rows.reduce((s, r) => s + (r.mfe || 0), 0) / n;
  const avgReal = rows.reduce((s, r) => s + (r.realized || 0), 0) / n;

  // 상위 3개 종목 (MFE순, 종목 중복 제거 — 종목당 최고 MFE)
  const best = {};
  for (const r of rows) { if (best[r.symbol] == null || r.mfe > best[r.symbol]) best[r.symbol] = r.mfe; }
  const top3 = Object.keys(best).sort((a, b) => best[b] - best[a]).slice(0, 3)
    .map(s => `${s} ${best[s] >= 0 ? '+' : ''}${best[s].toFixed(1)}%`).join(', ');

  // 청산사유 분포
  const cnt = (key) => rows.filter(r => r.reason === key).length;
  const tp3 = cnt('tp3'), tp2 = cnt('time_tp2'), tp1 = cnt('time_tp1');
  const stop = cnt('stop'), time = cnt('time');
  // TP 개수는 키캡 이모지 (0~10), 손절/만료는 숫자
  const KC = ['0️⃣','1️⃣','2️⃣','3️⃣','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟'];
  const kc = (x) => (x >= 0 && x <= 10) ? KC[x] : String(x);

  // 국면별
  const strong = rows.filter(r => r.regime === 'STRONG');
  const weak = rows.filter(r => r.regime === 'WEAK' || r.regime === 'UNKNOWN');
  const grp = (arr) => arr.length ? `${arr.length}건 +5% ${arr.filter(r => r.hit5 === 'O').length}건` : '0건';

  // 장초반
  const early = rows.filter(r => _isEarly(r.time));
  const earlyHit = early.filter(r => r.hit5 === 'O').length;

  const pct = (a, b) => b ? Math.round(a / b * 100) : 0;
  const today = new Date(Date.now() + 9 * 3600000).toISOString().slice(5, 10).replace('-', '/');

  return [
    `📊 일일 성적표 (${today})`,
    '─────────────',
    `신호 ${n}건 | +5% 도달 ${hit5}건 (${pct(hit5, n)}%)`,
    `🏆 상위: ${top3}`,
    `평균 MFE +${avgMfe.toFixed(1)}% | 평균 실현 ${avgReal >= 0 ? '+' : ''}${avgReal.toFixed(1)}%`,
    '─────────────',
    `🎯 tp3 ${kc(tp3)} | tp2 ${kc(tp2)} | tp1 ${kc(tp1)} | 🛑손절 ${stop} | ⏳만료 ${time}`,
    `⏰ 장초반 ${early.length}건` + (early.length ? `, +5% ${earlyHit}건 (${pct(earlyHit, early.length)}%)` : ''),
    `📈 강세 ${grp(strong)} / 📉 약세 ${grp(weak)}`,
  ].join('\n');
}

async function _send(text) {
  const token = cfg.TELEGRAM_BOT_TOKEN, chatId = cfg.TELEGRAM_CHAT_ID;
  if (!token || !chatId) { console.log('[report] telegram not set\n' + text); return; }
  try {
    await fetch('https://api.telegram.org/bot' + token + '/sendMessage', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text }),
    });
  } catch (e) { console.error('[report] send fail: ' + e.message); }
}

// 하루 1회 중복방지 — 마지막 발송 날짜(KST) 저장
function _lastSent() {
  try { return JSON.parse(fs.readFileSync(cfg.REPORT_STATE_FILE, 'utf8')).lastDate || null; }
  catch (e) { return null; }
}
function _markSent(dateStr) {
  try { fs.writeFileSync(cfg.REPORT_STATE_FILE, JSON.stringify({ lastDate: dateStr })); } catch (e) {}
}

// tick()에서 호출 — 발송 시각대(KST)이고 오늘 아직 안 보냈으면 발송
async function maybeSendDaily() {
  if (cfg.DAILY_REPORT_HOUR == null) return;
  const kst = new Date(Date.now() + 9 * 3600000);
  const hour = kst.getUTCHours();
  const dateStr = kst.toISOString().slice(0, 10);
  if (hour !== cfg.DAILY_REPORT_HOUR) return;        // 지정 시각대 아님
  if (_lastSent() === dateStr) return;               // 오늘 이미 발송
  const rows = _recent(_readLog(), 24);
  await _send(buildReport(rows));
  _markSent(dateStr);
  console.log('[report] 일일 성적표 발송 (' + dateStr + ', ' + rows.length + '건)');
}

module.exports = { maybeSendDaily, buildReport, _readLog, _recent };
