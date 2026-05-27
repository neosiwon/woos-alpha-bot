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

