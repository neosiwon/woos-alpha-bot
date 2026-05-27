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

