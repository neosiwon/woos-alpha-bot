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

// ATR (Average True Range) — 변동성. candles: [{high,low,close}], period 기본14
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

// 한글명 캐시가 비어있으면 market/all 한 번 받아 채움 (fetchUniverse 미호출 대비).
// 알람 직전 notify에서 호출 → 봇이 universe 안 받아도 한글명 보장.
let _krFetched = false;
async function ensureKoreanNames() {
  if (_krFetched && Object.keys(koreanNames).length) return;
  const all = await _get(`${BASE}/market/all?isDetails=false`);
  if (Array.isArray(all)) {
    all.forEach(m => { if (m.market && m.market.startsWith('KRW-')) koreanNames[m.market.slice(4)] = m.korean_name; });
    _krFetched = true;
  }
}
module.exports = { fetchUniverse, fetchCandlesM5, calcBoxPct, _batchMap, getKoreanName, ensureKoreanNames, calcATR };
