const cfg = require('../config/config');
const upbit = require('./exchange/upbit');

// 업비트 전체 스캔 -> 수축(박스폭 <= BOX_PCT_MAX) 통과분만 후보
async function findCandidates() {
  const universe = await upbit.fetchUniverse();
  if (!universe) { console.error('[scan] universe null -> 스킵'); return null; }
  console.log('[scan] universe ' + universe.length + '종 스캔 시작');

  const need = Math.ceil(cfg.SQUEEZE.LOOKBACK_MIN / 5); // 60분 / 5분 = 12개
  const candidates = [];

  const results = await upbit._batchMap(universe, async (sym) => {
    const candles = await upbit.fetchCandlesM5(sym, need);
    if (!candles) return null;                       // 0-0: 캔들 부족 제외
    const boxPct = upbit.calcBoxPct(candles);
    if (boxPct === null) return null;                // 0-0: 계산 불가 제외
    if (boxPct > cfg.SQUEEZE.BOX_PCT_MAX) return null; // 수축 아님 제외
    return { symbol: sym, boxPct, referencePrice: candles[candles.length - 1].close };
  });

  for (const r of results) if (r) candidates.push(r);
  console.log('[scan] 수축 통과 후보 ' + candidates.length + '종');
  return candidates;
}

module.exports = { findCandidates };
