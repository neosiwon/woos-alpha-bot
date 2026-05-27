#!/bin/bash
# woos-alpha-bot v3 trigger installer
# 사용법: VM의 ~/woos-alpha-bot/ 디렉토리에서 실행
#   chmod +x install_v3.sh && ./install_v3.sh

set -e
cd ~/woos-alpha-bot

echo "=== Stop service ==="
sudo systemctl stop woos-alpha-bot 2>/dev/null || true

echo "=== Backup ==="
cp -r bot bot.bak_$(date +%s) 2>/dev/null || true
cp -r config config.bak_$(date +%s) 2>/dev/null || true

mkdir -p bot/source bot/exchange config

echo "=== Writing 10 files ==="


# === bot/index.js ===
cat > bot/index.js << 'FILE_EOF_MARKER'
// bot/index.js — 메인 진입점 (v3)
// v3 변경: candidates 받은 직후 notify.sendStage2 호출 (2차 감시 알람).
// 나머지는 기존 흐름과 동일 (scan → trigger → notify.sendTelegram).

const cfg = require('../config/config');
const scan = require('./scan');
const trigger = require('./trigger');
const notify = require('./notify');
const state = require('./state');
const collector = require('./source/collector');
const dominance = require('./dominance');
const verifier = require('./verifier');
const report = require('./report');

async function tick() {
  const t = new Date().toISOString();
  console.log('\n[' + t + '] tick 시작');

  await dominance.record();
  const regime = dominance.judge();
  await verifier.update();
  await report.maybeSendDaily();
  console.log('[tick] 국면: ' + regime);

  // 1+2단: 흡수 → 박스 통과 후보
  const candidates = await scan.findCandidates();
  if (!candidates || !candidates.length) { console.log('[tick] 후보 없음 -> 다음'); return; }

  // v3 신규: 2차 감시 알람 (PRIVATE only, in-memory 쿨다운)
  await notify.sendStage2(candidates, regime);

  // 3단: VWAP 진입 검증 → 매수 신호
  const block = collector.getLatestExecBlock();   // 구버전 호환 (trigger.evaluate가 무시)
  const signals = await trigger.evaluate(candidates, block);
  if (!signals.length) { console.log('[tick] 매수 신호 없음'); return; }

  signals.forEach(s => { s.regime = regime; });
  const fresh = signals.filter(s => !state.inCooldown(s.symbol));
  if (!fresh.length) { console.log('[tick] 신호 ' + signals.length + ' 전부 쿨다운'); return; }

  // 3차 알람 (PRIVATE + GROUP)
  await notify.sendTelegram(fresh);
  for (const s of fresh) state.markNotified(s.symbol);
  verifier.register(fresh);
  console.log('[tick] 매수 알림 ' + fresh.length + '건 발송');
}

async function main() {
  console.log('=== woos-alpha-bot v3 시작 (주기 ' + cfg.LOOP_INTERVAL_SEC + 's) ===');
  await tick();
  setInterval(tick, cfg.LOOP_INTERVAL_SEC * 1000);
}

main().catch(e => { console.error('main 오류:', e); process.exit(1); });

FILE_EOF_MARKER

# === bot/scan.js ===
cat > bot/scan.js << 'FILE_EOF_MARKER'
// bot/scan.js — 후보 선정 (v3)
// 흐름: 흡수(1단) → 박스 상단 통과(2단). 통과한 종목만 candidates로 반환.
// candidates는 index.js → trigger.evaluate로 넘어가서 VWAP 진입(3단) 평가.
//
// 5/27 백테스트:
//   - 1단 흡수: 81개
//   - 2단 박스: 15개 (큰 발사 +10%↑ 모두 포함)

const cfg = require('../config/config');
const upbit = require('./exchange/upbit');
const collector = require('./source/collector');
const resistance = require('./source/resistance');

async function findCandidates() {
  // 1단: 흡수
  const absorption = collector.detectAbsorption();
  if (!absorption) { console.warn('[scan] 흡수 후보 없음 -> 스킵'); return null; }
  const syms = Object.keys(absorption);
  console.log(`[scan] 1단 흡수 후보 ${syms.length}개`);

  // 2단: 박스 상단 검증 (배치)
  const results = await upbit._batchMap(syms, async (sym) => {
    const abs = absorption[sym];
    const res = await resistance.evaluate(sym);
    if (!res.passed) return null;

    // 매도벽 비율 = 매도벽(KRW) / 그날 거래대금(KRW). 표시용.
    let wallRatioPct = null;
    if (res.askWallKrw && abs.dayValue > 0) {
      wallRatioPct = res.askWallKrw / abs.dayValue * 100;
    }

    return {
      symbol: sym,
      // 흡수 정보
      surge: abs.surge,
      range: abs.range,
      absorbTime: abs.time,
      dayValue: abs.dayValue,
      // 저항 정보
      toTopPct: res.toTopPct,
      high24: res.high24,
      referencePrice: res.currentPrice,
      wallRatioPct,        // 표시용 (null 가능)
      askWallKrw: res.askWallKrw,
    };
  });

  const candidates = results.filter(x => x);
  // 박스 여유 큰 순 (= 발사 잠재력 큰 순) 정렬
  candidates.sort((a, b) => b.toTopPct - a.toTopPct);
  console.log(`[scan] 2단 박스 통과 ${candidates.length}개`);
  return candidates;
}

// 9시 일일 리포트용 — 1단 흡수 후보 요약 텍스트
function buildAbsorptionSummary() {
  const absorption = collector.detectAbsorption();
  if (!absorption) return '🌅 오늘의 매집 후보: 없음';
  const list = Object.entries(absorption)
    .map(([s, a]) => ({ sym: s, ...a, score: a.surge / (a.range + 0.5) }))
    .sort((a, b) => b.score - a.score)
    .slice(0, 10);
  if (!list.length) return '🌅 오늘의 매집 후보: 없음';
  const lines = list.map((x, i) => {
    const ko = upbit.getKoreanName(x.sym);
    const name = ko ? `${ko}(${x.sym})` : x.sym;
    return `${i + 1}. ${name} 거래 ${x.surge.toFixed(1)}배↑ 가격 ${x.range.toFixed(1)}% @${x.time}`;
  });
  return `🌅 오늘의 매집 후보 ${list.length}종 (TOP 10)\n` + lines.join('\n');
}

module.exports = { findCandidates, buildAbsorptionSummary };

FILE_EOF_MARKER

# === bot/trigger.js ===
cat > bot/trigger.js << 'FILE_EOF_MARKER'
// bot/trigger.js — 3차 점화 트리거: 24h VWAP ±1% 진입 (v3)
// scan.findCandidates로 받은 2단 통과 후보들에 대해 VWAP 진입 여부 확인.
// 통과한 종목만 signals로 반환 → index.js에서 notify.sendTelegram.
//
// 인자 호환: 기존 index.js가 (candidates, execBlock) 시그니처로 호출.
// execBlock은 사용 안 함 (구버전 호환 위해 받기만).

const cfg = require('../config/config');
const upbit = require('./exchange/upbit');
const vwapEntry = require('./source/vwap_entry');

async function evaluate(candidates, _execBlock) {
  if (!candidates || !candidates.length) {
    console.warn('[trigger] 후보 없음');
    return [];
  }

  // 각 후보의 VWAP 진입 여부 (배치 호출)
  const results = await upbit._batchMap(candidates, async (c) => {
    const v = await vwapEntry.evaluate(c.symbol);
    if (!v.triggered) return null;

    // 매수가 = VWAP 진입 시점 현재가 (5분봉 종가)
    return {
      ...c,
      vwap: v.vwap,
      distNow: v.distNow,
      // referencePrice는 scan에서 ticker 가격, vwap_entry는 5분봉 종가. 후자가 더 정확.
      referencePrice: v.currentPrice,
      candleTime: v.candleTime,
    };
  });

  const signals = results.filter(x => x);
  // 박스 여유(큰 발사 잠재력) 큰 순으로 정렬
  signals.sort((a, b) => b.toTopPct - a.toTopPct);
  console.log(`[trigger] 3단 VWAP 진입 ${signals.length}건 (후보 ${candidates.length})`);
  return signals;
}

module.exports = { evaluate };

FILE_EOF_MARKER

# === bot/notify.js ===
cat > bot/notify.js << 'FILE_EOF_MARKER'
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

FILE_EOF_MARKER

# === bot/report.js ===
cat > bot/report.js << 'FILE_EOF_MARKER'
// bot/report.js — 일일 성적표 + 매일 9시 매집 후보 요약 (v3)
// 매일 09시(KST)에:
//   1) 어제 신호 24시간 성적표 (기존 기능)
//   2) 오늘의 매집 후보 TOP 10 (v3 신규)
// 둘을 합친 한 메시지를 PRIVATE 채널에 발송.

const fs = require('fs');
const cfg = require('../config/config');

// 매집 후보 요약 — scan에서 함수 제공
let _scanModule = null;
function _getScan() {
  if (!_scanModule) { try { _scanModule = require('./scan'); } catch (e) {} }
  return _scanModule;
}

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

function _recent(rows, hours) {
  const now = Date.now();
  return rows.filter(r => {
    const t = new Date(r.time.replace(' ', 'T') + '+09:00').getTime();
    return Number.isFinite(t) && (now - t) <= hours * 3600000;
  });
}

function buildScoreReport(rows) {
  const n = rows.length;
  if (!n) return '📊 일일 성적표\n─────────────\n최근 24시간 확정 신호 없음.';

  const hit5 = rows.filter(r => r.hit5 === 'O').length;
  const avgMfe = rows.reduce((s, r) => s + (r.mfe || 0), 0) / n;
  const avgReal = rows.reduce((s, r) => s + (r.realized || 0), 0) / n;

  const best = {};
  for (const r of rows) { if (best[r.symbol] == null || r.mfe > best[r.symbol]) best[r.symbol] = r.mfe; }
  const top3 = Object.keys(best).sort((a, b) => best[b] - best[a]).slice(0, 3)
    .map(s => `${s} ${best[s] >= 0 ? '+' : ''}${best[s].toFixed(1)}%`).join(', ');

  const cnt = (key) => rows.filter(r => r.reason === key).length;
  const tp3 = cnt('tp3'), tp2 = cnt('time_tp2'), tp1 = cnt('time_tp1');
  const stop = cnt('stop'), time = cnt('time');
  const KC = ['0️⃣','1️⃣','2️⃣','3️⃣','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟'];
  const kc = (x) => (x >= 0 && x <= 10) ? KC[x] : String(x);

  const strong = rows.filter(r => r.regime === 'STRONG');
  const weak = rows.filter(r => r.regime === 'WEAK' || r.regime === 'UNKNOWN');
  const grp = (arr) => arr.length ? `${arr.length}건 +5% ${arr.filter(r => r.hit5 === 'O').length}건` : '0건';

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
    `📈 강세 ${grp(strong)} / 📉 약세 ${grp(weak)}`,
  ].join('\n');
}

// 9시 전체 리포트 = 성적표 + 매집 후보 요약
function buildReport(rows) {
  const score = buildScoreReport(rows);
  let absorb = '';
  const scan = _getScan();
  if (scan && typeof scan.buildAbsorptionSummary === 'function') {
    try { absorb = scan.buildAbsorptionSummary(); } catch (e) { absorb = ''; }
  }
  return absorb ? `${score}\n\n${absorb}` : score;
}

async function _send(text) {
  // 9시 리포트는 PRIVATE으로
  const token = cfg.TELEGRAM_BOT_TOKEN;
  const chatId = cfg.TELEGRAM_CHAT_ID_PRIVATE || cfg.TELEGRAM_CHAT_ID;
  if (!token || !chatId) { console.log('[report] telegram not set\n' + text); return; }
  try {
    const r = await fetch('https://api.telegram.org/bot' + token + '/sendMessage', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text }),
    });
    if (!r.ok) console.error('[report] HTTP ' + r.status);
  } catch (e) { console.error('[report] send fail: ' + e.message); }
}

function _lastSent() {
  try { return JSON.parse(fs.readFileSync(cfg.REPORT_STATE_FILE, 'utf8')).lastDate || null; }
  catch (e) { return null; }
}
function _markSent(dateStr) {
  try { fs.writeFileSync(cfg.REPORT_STATE_FILE, JSON.stringify({ lastDate: dateStr })); } catch (e) {}
}

async function maybeSendDaily() {
  if (cfg.DAILY_REPORT_HOUR == null) return;
  const kst = new Date(Date.now() + 9 * 3600000);
  const hour = kst.getUTCHours();
  const dateStr = kst.toISOString().slice(0, 10);
  if (hour !== cfg.DAILY_REPORT_HOUR) return;
  if (_lastSent() === dateStr) return;
  const rows = _recent(_readLog(), 24);
  await _send(buildReport(rows));
  _markSent(dateStr);
  console.log('[report] 일일 리포트 발송 (' + dateStr + ', 신호 ' + rows.length + '건)');
}

module.exports = { maybeSendDaily, buildReport, _readLog, _recent };

FILE_EOF_MARKER

# === bot/source/collector.js ===
cat > bot/source/collector.js << 'FILE_EOF_MARKER'
// bot/source/collector.js — CSV 수집기 (거래 + 호가)
// v3 변경: getSpikes 폐기 → detectAbsorption 추가. 기존 메서드는 호환을 위해 유지.

const fs = require('fs');
const path = require('path');
const cfg = require('../../config/config');

function _todayKST() {
  const now = new Date(Date.now() + 9 * 3600 * 1000);
  const y = now.getUTCFullYear();
  const m = String(now.getUTCMonth() + 1).padStart(2, '0');
  const d = String(now.getUTCDate()).padStart(2, '0');
  return `${y}${m}${d}`;
}

function _tradePath()    { return path.join(cfg.COLLECTOR_CSV_DIR, `${cfg.COLLECTOR_CSV_PREFIX}${_todayKST()}.csv`); }
function _orderbookPath(){ return path.join(cfg.COLLECTOR_CSV_DIR, `${cfg.ORDERBOOK_CSV_PREFIX}${_todayKST()}.csv`); }

// 거래 CSV 전체 행 파싱 — 기존 유지
function _readAllRows() {
  const p = _tradePath();
  if (!fs.existsSync(p)) { console.warn(`[collector] no CSV: ${p}`); return null; }
  let txt;
  try { txt = fs.readFileSync(p, 'utf8'); }
  catch (e) { console.error(`[collector] read fail ${p}: ${e.message}`); return null; }
  if (txt.charCodeAt(0) === 0xFEFF) txt = txt.slice(1);
  const lines = txt.split('\n').filter(l => l.trim() !== '');
  if (lines.length < 2) return null;
  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    const f = lines[i].split(',');
    if (f.length < 7) continue;
    const execStrength = parseFloat(f[2]);
    const buyKrw = parseFloat(f[3]);
    const sellKrw = parseFloat(f[4]);
    const tradeValue = parseFloat(f[5]);
    const trades = parseInt(f[6], 10);
    if (![execStrength, buyKrw, sellKrw, tradeValue, trades].every(Number.isFinite)) continue;
    rows.push({ ts: f[0], symbol: f[1], execStrength, buyKrw, sellKrw, tradeValue, trades });
  }
  return rows.length ? rows : null;
}

// 최근 한 틱(같은 ts)의 종목별 체결강도 블록 — 기존 유지 (index.js에서 호출)
function getLatestExecBlock() {
  const rows = _readAllRows();
  if (!rows) return null;
  let latestTs = null;
  for (const r of rows) if (latestTs === null || r.ts > latestTs) latestTs = r.ts;
  if (!latestTs) return null;
  const block = {};
  for (const r of rows) {
    if (r.ts !== latestTs) continue;
    block[r.symbol] = { execStrength: r.execStrength, tradeValue: r.tradeValue, trades: r.trades, buyKrw: r.buyKrw, sellKrw: r.sellKrw };
  }
  return { ts: latestTs, data: Object.keys(block).length ? block : null };
}

// 기존 호환 — verifier 등 다른 모듈에서 사용 가능
function getRecentSeries(symbol, windowTicks) {
  const rows = _readAllRows();
  if (!rows) return null;
  const series = rows
    .filter(r => r.symbol === symbol)
    .sort((a, b) => (a.ts < b.ts ? -1 : 1))
    .slice(-windowTicks)
    .map(r => ({ ts: r.ts, execStrength: r.execStrength }));
  return series.length ? series : null;
}

// ts ("2026-05-27 09:14:05") → 시간(0~23)
function _hourOf(ts) {
  const m = /(\d{2}):/.exec(ts);
  return m ? parseInt(m[1], 10) : null;
}

// 호가 CSV 시간별 최우선매도가 (가격 추적용)
// 캐시 (tick 단위 무효화 — 같은 분 안에 재호출 시 재사용)
const _obCache = { at: 0, data: null };
const _OB_TTL_MS = 50 * 1000;

function _readOrderbookHourly() {
  const now = Date.now();
  if (_obCache.data && (now - _obCache.at) < _OB_TTL_MS) return _obCache.data;
  const p = _orderbookPath();
  if (!fs.existsSync(p)) { _obCache.data = null; _obCache.at = now; return null; }
  let txt;
  try { txt = fs.readFileSync(p, 'utf8'); }
  catch (e) { console.error(`[collector] ob read fail: ${e.message}`); return null; }
  if (txt.charCodeAt(0) === 0xFEFF) txt = txt.slice(1);
  const lines = txt.split('\n').filter(l => l.trim() !== '');
  // {symbol: {hour: [prices]}}
  const map = {};
  for (let i = 1; i < lines.length; i++) {
    const f = lines[i].split(',');
    if (f.length < 10) continue;
    const sym = f[1];
    const h = _hourOf(f[0]);
    const p = parseFloat(f[7]); // 최우선매도가
    if (h === null || !Number.isFinite(p) || p <= 0) continue;
    if (!map[sym]) map[sym] = {};
    if (!map[sym][h]) map[sym][h] = [];
    map[sym][h].push(p);
  }
  _obCache.data = map;
  _obCache.at = now;
  return map;
}

// === v3 신규: 흡수 검출 ===
// 거래량 ≥ 평소(하위25%) × 3배 AND 같은 윈도우 가격폭 ≤ 4% = 흡수.
// 반환: { symbol: { surge, range, time, dayValue } }
function detectAbsorption() {
  const rows = _readAllRows();
  if (!rows) return null;
  const ob = _readOrderbookHourly();
  if (!ob) { console.warn('[collector] no orderbook CSV for absorption'); return null; }

  const A = cfg.ABSORPTION;
  const majors = new Set(cfg.MAJORS);

  // 종목별 시간별 거래대금 + 종목별 총 거래대금
  const hourlyVol = {}, dayVol = {};
  for (const r of rows) {
    if (majors.has(r.symbol)) continue;
    const h = _hourOf(r.ts);
    if (h === null) continue;
    if (!hourlyVol[r.symbol]) { hourlyVol[r.symbol] = {}; dayVol[r.symbol] = 0; }
    hourlyVol[r.symbol][h] = (hourlyVol[r.symbol][h] || 0) + r.tradeValue;
    dayVol[r.symbol] += r.tradeValue;
  }

  const out = {};
  for (const sym of Object.keys(hourlyVol)) {
    if (dayVol[sym] < A.DEAD_COIN_MIN_24H_KRW) continue;
    const prices = ob[sym] || {};
    const hours = Object.keys(hourlyVol[sym]).map(Number).sort((a, b) => a - b);
    if (hours.length < A.LOOKBACK_HOURS) continue;

    // baseline = 하위 N% 시간의 거래량
    const sortedVols = hours.map(h => hourlyVol[sym][h]).sort((a, b) => a - b);
    const idx = Math.floor(sortedVols.length * A.BASELINE_PERCENTILE / 100);
    const baseline = sortedVols[idx];
    if (!(baseline > 0)) continue;

    // N시간 슬라이딩 윈도우 중 최고 흡수 찾기
    let best = null;
    for (let i = 0; i <= hours.length - A.LOOKBACK_HOURS; i++) {
      const win = hours.slice(i, i + A.LOOKBACK_HOURS);
      const vols = win.map(h => hourlyVol[sym][h]);
      const winPrices = win.flatMap(h => prices[h] || []);
      if (winPrices.length < 3) continue;

      const surge = Math.max(...vols) / baseline;
      const hi = Math.max(...winPrices);
      const lo = Math.min(...winPrices);
      const range = (hi - lo) / lo * 100;

      if (surge >= A.VOLUME_SURGE_MIN && range <= A.PRICE_RANGE_MAX) {
        // 더 강한 흡수면 갱신 (점수 = surge / range — 정렬용)
        const score = surge / (range + 0.5);
        if (!best || score > best.score) {
          best = { surge, range, time: `${win[win.length - 1]}시`, score };
        }
      }
    }

    if (best) {
      out[sym] = {
        surge: best.surge,
        range: best.range,
        time: best.time,
        dayValue: dayVol[sym],
      };
    }
  }

  return Object.keys(out).length ? out : null;
}

// === v3 신규: 호가에서 현재 매도벽 정보 ===
// 반환: { top5Ask, ask1Price } — 표시용 (필터 아님)
function getRecentOrderbookForWall(symbol) {
  const p = _orderbookPath();
  if (!fs.existsSync(p)) return null;
  let txt;
  try { txt = fs.readFileSync(p, 'utf8'); } catch (e) { return null; }
  if (txt.charCodeAt(0) === 0xFEFF) txt = txt.slice(1);
  const lines = txt.split('\n').filter(l => l.trim() !== '');
  // 뒤에서 훑어 해당 종목 최근 10개 평균
  const recent = [];
  for (let i = lines.length - 1; i >= 1 && recent.length < 10; i--) {
    const f = lines[i].split(',');
    if (f.length < 10 || f[1] !== symbol) continue;
    const top5 = parseFloat(f[5]);
    const ask1 = parseFloat(f[7]);
    if (!Number.isFinite(top5) || !Number.isFinite(ask1) || ask1 <= 0) continue;
    recent.push({ top5Ask: top5, ask1Price: ask1 });
  }
  if (!recent.length) return null;
  const avgTop5 = recent.reduce((a, b) => a + b.top5Ask, 0) / recent.length;
  const avgPrice = recent.reduce((a, b) => a + b.ask1Price, 0) / recent.length;
  return { top5Ask: avgTop5, ask1Price: avgPrice };
}

module.exports = {
  // v3 신규
  detectAbsorption, getRecentOrderbookForWall,
  // 기존 호환
  getLatestExecBlock, getRecentSeries, _csvPath: _tradePath,
};

FILE_EOF_MARKER

# === bot/source/resistance.js ===
cat > bot/source/resistance.js << 'FILE_EOF_MARKER'
// bot/source/resistance.js — 2차 트리거: 박스 상단 여유 (저항)
// 위로 갈 공간 ≥ N% 인 종목만 통과. 매도벽 비율은 표시용.
// 5/27 백테스트: 박스상단 ≥7% 컷 → 큰 발사 모두 포함, 헛걸림은 +0~7% 작은 움직임.

const cfg = require('../../config/config');
const upbit = require('../exchange/upbit');
const collector = require('./collector');

// 한 종목의 저항 평가
// 입력: symbol, currentPrice (선택, 없으면 ticker 조회)
// 출력: { passed, toTopPct, high24, wallRatioPct(표시용), askWallKrw, dayVolKrw }
async function evaluate(symbol, currentPrice) {
  const R = cfg.RESISTANCE;

  // 1) 박스 상단 = 직전 N시간 고점
  const candles = await upbit.fetchCandlesM60(symbol, R.BOX_LOOKBACK_HOURS);
  if (!candles || candles.length < 12) {
    return { passed: false, reason: 'no_candles' };
  }
  const high24 = Math.max(...candles.map(c => c.high));

  // 현재가 (없으면 ticker)
  let price = currentPrice;
  if (!price) {
    const tk = await upbit.fetchTicker(symbol);
    price = tk && tk.trade_price;
  }
  if (!price) return { passed: false, reason: 'no_price' };

  const toTopPct = (high24 / price - 1) * 100;

  // 2) 매도벽 비율 (표시용 — 필터 X)
  let wallRatioPct = null, askWallKrw = null, dayVolKrw = 0;
  if (R.SHOW_WALL_RATIO) {
    const ob = collector.getRecentOrderbookForWall(symbol);
    if (ob) {
      askWallKrw = ob.top5Ask * ob.ask1Price;
      // 그날 거래대금은 detectAbsorption 결과에 dayValue로 있어 거기서 가져옴 (scan에서 주입)
      // 여기선 alone일 때 호가 raw에 의존 — wallRatioPct는 scan에서 계산
    }
  }

  const passed = toTopPct >= R.BOX_TOP_MIN_PCT;
  return { passed, toTopPct, high24, currentPrice: price, askWallKrw, wallRatioPct, dayVolKrw };
}

module.exports = { evaluate };

FILE_EOF_MARKER

# === bot/source/vwap_entry.js ===
cat > bot/source/vwap_entry.js << 'FILE_EOF_MARKER'
// bot/source/vwap_entry.js — 3차 트리거: VWAP ±1% 진입 (점화)
// 현재 5분봉 종가가 24h VWAP에서 ±1% 안 = 발사 점화.
// 5/27 검증: PRL/TRAC/ICP/ALT 모두 발사 직전 ±1% 안 진입.

const cfg = require('../../config/config');
const upbit = require('../exchange/upbit');

// 5분봉 N개의 VWAP (거래량 가중 평균가)
// candles: {value, volume} 필드 보유. upbit.fetchCandlesM5는 둘 다 채워줌.
function _calcVwap(candles) {
  let num = 0, den = 0;
  for (const c of candles) {
    // value = candle_acc_trade_price = sum(price × volume), volume = 거래량
    num += c.value;
    den += c.volume;
  }
  return den > 0 ? num / den : 0;
}

// 입력: symbol
// 출력: { triggered, vwap, distNow, currentPrice, candleTime }
async function evaluate(symbol) {
  const V = cfg.VWAP_ENTRY;
  const NEED = V.VWAP_HOURS * (60 / V.CANDLE_INTERVAL_MIN); // 288봉

  // 5분봉 NEED+1개 (upbit.fetchCandlesM5가 자동 페이징)
  const candles = await upbit.fetchCandlesM5(symbol, NEED + 1);
  if (!candles || candles.length < NEED + 1) {
    return { triggered: false, reason: 'short_candles', got: candles ? candles.length : 0 };
  }

  // 마지막 봉 = 현재 시점
  const current = candles[candles.length - 1];
  const window = candles.slice(-1 - NEED, -1); // 현재 봉 직전 288봉
  const vwap = _calcVwap(window);
  if (vwap <= 0) return { triggered: false, reason: 'no_vwap' };

  const distNow = (current.close / vwap - 1) * 100;

  // 트리거: 현재 ±N% 안 (전환 요구 X — 쿨다운으로 중복 방지)
  const triggered = Math.abs(distNow) <= V.ENTRY_BAND_PCT;

  return {
    triggered,
    vwap,
    distNow,
    currentPrice: current.close,
    candleTime: current.ts,
  };
}

module.exports = { evaluate };

FILE_EOF_MARKER

# === bot/exchange/upbit.js ===
cat > bot/exchange/upbit.js << 'FILE_EOF_MARKER'
// bot/exchange/upbit.js — 업비트 API 래퍼
// 기존 기능 모두 유지 + 새 메서드 추가 (v3 트리거용)
const cfg = require('../../config/config');
const BASE = 'https://api.upbit.com/v1';
const koreanNames = {}; // 심볼 -> 한글명

async function _get(url) {
  try {
    const r = await fetch(url, { headers: { Accept: 'application/json' } });
    if (!r.ok) { console.error(`[upbit] HTTP ${r.status} ${url}`); return null; }
    return await r.json();
  } catch (e) { console.error(`[upbit] fetch fail ${url}: ${e.message}`); return null; }
}

async function _batchMap(items, fn) {
  const size = cfg.UPBIT_BATCH_SIZE || 6;
  const delay = cfg.UPBIT_BATCH_DELAY_MS || 200;
  const out = [];
  for (let i = 0; i < items.length; i += size) {
    const chunk = items.slice(i, i + size);
    const res = await Promise.all(chunk.map(fn));
    out.push(...res);
    if (i + size < items.length && delay > 0) await new Promise(r => setTimeout(r, delay));
  }
  return out;
}

async function fetchUniverse() {
  const all = await _get(`${BASE}/market/all?isDetails=false`);
  if (!Array.isArray(all)) return null;
  const majors = new Set(cfg.MAJORS);
  all.forEach(m => { if (m.market && m.market.startsWith('KRW-')) koreanNames[m.market.slice(4)] = m.korean_name; });
  return all
    .filter(m => typeof m.market === 'string' && m.market.startsWith('KRW-'))
    .map(m => m.market.slice(4))
    .filter(sym => !majors.has(sym));
}

// === 5분봉 (v3: volume 포함 + 자동 페이징) ===
// count > 200이면 자동으로 두 페이지 받아 합침 (업비트 한 번 200개 제한).
// 반환: 시간 오름차순. {ts, open, high, low, close, value, volume}
async function fetchCandlesM5(symbol, count) {
  const need = Math.max(1, count);
  // 1페이지: 최신 200개 (또는 need <200이면 need개)
  const firstCount = Math.min(need, 200);
  const raw1 = await _get(`${BASE}/candles/minutes/5?market=KRW-${symbol}&count=${firstCount}`);
  if (!Array.isArray(raw1) || raw1.length === 0) {
    console.warn(`[upbit] ${symbol} M5 first page empty`);
    return null;
  }
  let all = raw1.slice();
  // 2페이지 이상 필요?
  if (need > 200) {
    const oldest = raw1.reduce((a, b) =>
      a.candle_date_time_kst < b.candle_date_time_kst ? a : b
    );
    const need2 = need - raw1.length;
    const raw2 = await _get(
      `${BASE}/candles/minutes/5?market=KRW-${symbol}&count=${need2}&to=${encodeURIComponent(oldest.candle_date_time_kst)}`
    );
    if (Array.isArray(raw2) && raw2.length) all = all.concat(raw2);
  }
  // 시간 오름차순으로 변환
  return all
    .sort((a, b) => a.candle_date_time_kst.localeCompare(b.candle_date_time_kst))
    .map(c => ({
      ts: c.candle_date_time_kst,
      open: c.opening_price,
      high: c.high_price,
      low: c.low_price,
      close: c.trade_price,
      value: c.candle_acc_trade_price,   // 거래대금 (KRW)
      volume: c.candle_acc_trade_volume, // 거래량 (코인 수량)
    }));
}

// === 1시간봉 (박스 상단 계산용, v3 신규) ===
async function fetchCandlesM60(symbol, count) {
  const raw = await _get(`${BASE}/candles/minutes/60?market=KRW-${symbol}&count=${count}`);
  if (!Array.isArray(raw)) return null;
  return raw.slice().reverse().map(c => ({
    ts: c.candle_date_time_kst, open: c.opening_price, high: c.high_price,
    low: c.low_price, close: c.trade_price, value: c.candle_acc_trade_price,
  }));
}

// === 현재가 (v3 신규) ===
// /ticker 한 번에 여러 종목 가능. 단일 종목용 헬퍼.
async function fetchTicker(symbol) {
  const arr = await _get(`${BASE}/ticker?markets=KRW-${symbol}`);
  if (!Array.isArray(arr) || !arr.length) return null;
  return { trade_price: arr[0].trade_price };
}

// === 기존 유틸 (그대로 유지) ===
function calcBoxPct(candles) {
  if (!Array.isArray(candles) || candles.length === 0) return null;
  const hi = Math.max(...candles.map(c => c.high));
  const lo = Math.min(...candles.map(c => c.low));
  if (!(lo > 0)) return null;
  return ((hi - lo) / lo) * 100;
}

function calcATR(candles, period) {
  period = period || 14;
  if (!Array.isArray(candles) || candles.length < period + 1) return null;
  const trs = [];
  for (let i = 1; i < candles.length; i++) {
    const h = candles[i].high, l = candles[i].low, pc = candles[i-1].close;
    const tr = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
    trs.push(tr);
  }
  const recent = trs.slice(-period);
  if (recent.length < period) return null;
  return recent.reduce((a,b) => a+b, 0) / period;
}

function getKoreanName(sym) { return koreanNames[sym] || null; }

let _krFetched = false;
async function ensureKoreanNames() {
  if (_krFetched && Object.keys(koreanNames).length) return;
  const all = await _get(`${BASE}/market/all?isDetails=false`);
  if (Array.isArray(all)) {
    all.forEach(m => { if (m.market && m.market.startsWith('KRW-')) koreanNames[m.market.slice(4)] = m.korean_name; });
    _krFetched = true;
  }
}

module.exports = {
  fetchUniverse, fetchCandlesM5, fetchCandlesM60, fetchTicker,
  calcBoxPct, _batchMap, getKoreanName, ensureKoreanNames, calcATR,
};

FILE_EOF_MARKER

# === config/config.js ===
cat > config/config.js << 'FILE_EOF_MARKER'
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

FILE_EOF_MARKER

echo "=== Syntax check ==="
for f in bot/index.js bot/scan.js bot/trigger.js bot/notify.js bot/report.js \
         bot/source/collector.js bot/source/resistance.js bot/source/vwap_entry.js \
         bot/exchange/upbit.js config/config.js; do
  node --check "$f" && echo "$f OK" || { echo "$f FAILED"; exit 1; }
done

echo "=== Git commit & push ==="
git add bot config
git commit -m "v3: absorption + box-top + VWAP entry trigger" || echo "(nothing to commit)"
git push || echo "(push failed - check manually)"

echo "=== Restart service ==="
sudo systemctl start woos-alpha-bot
sleep 3
sudo systemctl status woos-alpha-bot --no-pager | head -15

echo ""
echo "=== DONE ==="
