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

function _csvPath() {
  return path.join(cfg.COLLECTOR_CSV_DIR, `${cfg.COLLECTOR_CSV_PREFIX}${_todayKST()}.csv`);
}

function _readAllRows() {
  const p = _csvPath();
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

// ts("2026-05-25 08:50:05") → 그날 자정 기준 경과 분(минute). 파싱 실패 시 null.
function _tsToMinutes(ts) {
  const m = /(\d{2}):(\d{2}):/.exec(ts);
  if (!m) return null;
  return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
}

// 매집 스파이크 측정 — 5/25 알파 역산: 1틱(config.SPIKE.WINDOW_MIN) 순매수 스파이크가 핵심.
// (누적/비율/배율 기각. 5분은 노이즈 부풀림. 1틱이 진짜/노이즈 분리 최고.)
// 반환: { symbol: { spike, spikeTs, cumNet } }
//   spike = WINDOW_MIN 구간 최대 순매수 합 (매집 강도). spikeTs = 그 구간 끝 시각.
//   cumNet = 당일 누적 순매수 (참고용).
function getSpikes() {
  const rows = _readAllRows();
  if (!rows) return null;
  const winMin = (cfg.SPIKE && cfg.SPIKE.WINDOW_MIN) || 1; // config에서 윈도우 (1=1틱)
  // 종목별 시계열 (시간순)
  const series = {};
  for (const r of rows) {
    if (!Number.isFinite(r.buyKrw) || !Number.isFinite(r.sellKrw)) continue; // 0-0
    const min = _tsToMinutes(r.ts);
    if (min === null) continue;
    if (!series[r.symbol]) series[r.symbol] = [];
    series[r.symbol].push({ min, ts: r.ts, net: r.buyKrw - r.sellKrw });
  }
  const out = {};
  for (const sym of Object.keys(series)) {
    const arr = series[sym].sort((a, b) => a.min - b.min);
    let cumNet = 0;
    for (const x of arr) cumNet += x.net;
    // 윈도우 슬라이딩 최대 순매수 (winMin<=1이면 단일 틱)
    let spike = -Infinity, spikeTs = null;
    for (let i = 0; i < arr.length; i++) {
      let s = 0;
      for (let j = i; j >= 0 && (winMin <= 1 ? j === i : arr[i].min - arr[j].min < winMin); j--) s += arr[j].net;
      if (s > spike) { spike = s; spikeTs = arr[i].ts; }
    }
    if (spike === -Infinity) continue;
    out[sym] = { spike, spikeTs, cumNet };
  }
  return Object.keys(out).length ? out : null;
}

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

module.exports = { getLatestExecBlock, getRecentSeries, getSpikes, _csvPath };
