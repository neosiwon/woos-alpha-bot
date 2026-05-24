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
    const sellKrw = parseFloat(f[4]);
    const tradeValue = parseFloat(f[5]);
    const trades = parseInt(f[6], 10);
    if (![execStrength, sellKrw, tradeValue, trades].every(Number.isFinite)) continue;
    rows.push({ ts: f[0], symbol: f[1], execStrength, sellKrw, tradeValue, trades });
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
    block[r.symbol] = { execStrength: r.execStrength, tradeValue: r.tradeValue, trades: r.trades, sellKrw: r.sellKrw };
  }
  return { ts: latestTs, data: Object.keys(block).length ? block : null };
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

module.exports = { getLatestExecBlock, getRecentSeries, _csvPath };
