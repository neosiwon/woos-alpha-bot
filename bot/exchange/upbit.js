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

async function fetchCandlesM5(symbol, count) {
  const raw = await _get(`${BASE}/candles/minutes/5?market=KRW-${symbol}&count=${count}`);
  if (!Array.isArray(raw)) return null;
  if (raw.length < count) { console.warn(`[upbit] ${symbol} candles short (${raw.length}/${count})`); return null; }
  return raw.slice().reverse().map(c => ({
    ts: c.candle_date_time_kst, open: c.opening_price, high: c.high_price,
    low: c.low_price, close: c.trade_price, value: c.candle_acc_trade_price,
  }));
}

function calcBoxPct(candles) {
  if (!Array.isArray(candles) || candles.length === 0) return null;
  const hi = Math.max(...candles.map(c => c.high));
  const lo = Math.min(...candles.map(c => c.low));
  if (!(lo > 0)) return null;
  return ((hi - lo) / lo) * 100;
}

function getKoreanName(sym) { return koreanNames[sym] || null; }
module.exports = { fetchUniverse, fetchCandlesM5, calcBoxPct, _batchMap, getKoreanName };
