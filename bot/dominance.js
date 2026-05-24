const fs = require('fs');
const cfg = require('../config/config');

// CoinGecko에서 현재 BTC.D / USDT.D 받기. 실패 시 null (0-0)
async function fetchDominance() {
  try {
    const r = await fetch('https://api.coingecko.com/api/v3/global');
    if (!r.ok) { console.error('[dom] HTTP ' + r.status); return null; }
    const d = await r.json();
    const m = d.data && d.data.market_cap_percentage;
    if (!m || typeof m.btc !== 'number' || typeof m.usdt !== 'number') return null;
    return { btcD: m.btc, usdtD: m.usdt, ts: Date.now() };
  } catch (e) { console.error('[dom] fetch fail: ' + e.message); return null; }
}

// 기록 로드/저장 (자가축적)
function _load() {
  try { if (fs.existsSync(cfg.DOMINANCE_FILE)) return JSON.parse(fs.readFileSync(cfg.DOMINANCE_FILE, 'utf8')); }
  catch (e) { console.error('[dom] load fail: ' + e.message); }
  return { history: [] };
}
function _save(s) {
  try { fs.writeFileSync(cfg.DOMINANCE_FILE, JSON.stringify(s)); }
  catch (e) { console.error('[dom] save fail: ' + e.message); }
}

// 현재값 받아서 기록에 추가 (매 틱 호출). 24h 넘은 기록은 정리.
async function record() {
  const now = await fetchDominance();
  if (!now) return null;                  // 0-0: 못 받으면 기록 안 함
  const s = _load();
  s.history.push(now);
  const cutoff = Date.now() - 24 * 3600 * 1000;
  s.history = s.history.filter(h => h.ts >= cutoff);
  _save(s);
  return now;
}

// 강세/약세 판정. 4시간 전 기록 있어야 판정 가능.
// 반환: 'STRONG' | 'WEAK' | 'UNKNOWN'(기록부족)
function judge() {
  const s = _load();
  if (!s.history.length) return 'UNKNOWN';
  const now = s.history[s.history.length - 1];
  const targetTs = Date.now() - cfg.REGIME_LOOKBACK_HOURS * 3600 * 1000;
  // 4시간 전에 가장 가까운 기록 찾기
  let past = null, bestDiff = Infinity;
  for (const h of s.history) {
    const diff = Math.abs(h.ts - targetTs);
    if (diff < bestDiff) { bestDiff = diff; past = h; }
  }
  // 4시간 전 기록이 충분히 오래된 게 없으면 (축적 부족) UNKNOWN
  if (!past || (now.ts - past.ts) < (cfg.REGIME_LOOKBACK_HOURS * 3600 * 1000 * 0.7)) return 'UNKNOWN';

  const th = cfg.REGIME_CHANGE_THRESHOLD;
  const btcChange = now.btcD - past.btcD;
  const usdtChange = now.usdtD - past.usdtD;
  // 강세: BTC.D 하락 AND USDT.D 하락 (둘 다 -th 이상)
  if (btcChange <= -th && usdtChange <= -th) return 'STRONG';
  // 약세: USDT.D 상승 (+th 이상)
  if (usdtChange >= th) return 'WEAK';
  // 그 외(횡보/애매): 보수적으로 약세 취급
  return 'WEAK';
}

module.exports = { fetchDominance, record, judge };
