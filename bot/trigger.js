const cfg = require('../config/config');
const A = cfg.ALPHA_TRIGGER;

// 노이즈필터: 9999(매도0)는 매도액 조건에서 자동 탈락
function passNoise(e) {
  return e.trades >= A.MIN_TRADES
    && e.sellKrw >= A.MIN_SELL_KRW
    && e.tradeValue >= A.MIN_VALUE_KRW;
}

// 지속성: 최근 시퀀스 중 EXEC_STRENGTH_MIN 이상 횟수 → repeat/strong
function persistence(series) {
  if (!series) return { hits: 0, level: 'none' };
  const hits = series.filter(x => x.execStrength >= A.EXEC_STRENGTH_MIN && x.execStrength < 9999).length;
  let level = 'none';
  if (hits >= A.PERSISTENCE_STRONG_HITS) level = 'strong_repeat';
  else if (hits >= A.PERSISTENCE_MIN_HITS) level = 'repeat';
  return { hits, level };
}

// candidates: [{symbol, boxPct, referencePrice}], execBlock: {sym:{...}}, getSeries: fn
function evaluate(candidates, execBlock, getSeries) {
  if (!candidates || !candidates.length) { console.warn('[trigger] 후보 없음'); return []; }
  if (!execBlock || !execBlock.data) { console.warn('[trigger] 체결강도 블록 없음 → 스킵'); return []; }

  // 1. 후보 ∩ 체결강도 + 노이즈필터 통과 = 모집단
  const pop = [];
  for (const c of candidates) {
    const e = execBlock.data[c.symbol];
    if (!e) continue;                         // 0-0: 체결강도 없으면 스킵
    if (e.execStrength >= 9999) continue;     // 9999(매도0) 제외 (노이즈필터로도 걸리지만 명시)
    if (!passNoise(e)) continue;              // 노이즈필터 (얇은/죽은 종목 제외)
    pop.push({ ...c, execStrength: e.execStrength, tradeValue: e.tradeValue });
  }
  if (!pop.length) { console.warn('[trigger] 모집단 0 (노이즈 통과 후보 없음)'); return []; }

  // 2. 동적 상위 N% 임계 (모집단 충분할 때만)
  let dynThreshold = null;
  if (A.EXEC_USE_DYNAMIC && (A.MIN_POPULATION !== null && pop.length >= A.MIN_POPULATION)) {
    const sorted = pop.map(p => p.execStrength).sort((a, b) => b - a);
    const idx = Math.max(0, Math.floor(sorted.length * (A.EXEC_DYNAMIC_TOP_PCT / 100)) - 1);
    dynThreshold = sorted[idx];
  }
  // MIN_POPULATION이 null이고 모집단 작으면 동적 못 믿음 → 절대값 fallback (위 조건이 처리)

  // 3. 트리거: 절대값 AND (동적 있으면 동적도)
  const signals = [];
  for (const p of pop) {
    if (p.execStrength < A.EXEC_STRENGTH_MIN) continue;          // 절대값 하한
    if (dynThreshold !== null && p.execStrength < dynThreshold) continue; // 동적 상위%
    const series = getSeries ? getSeries(p.symbol, A.PERSISTENCE_WINDOW) : null;
    const pers = persistence(series);
    signals.push({ ...p, persistence: pers.level, persistHits: pers.hits });
  }

  // 4. 정렬: strong_repeat 우선 → sweet(1.5~4%) 근접 → 체결강도
  const sweetMid = (cfg.SQUEEZE.SWEET_MIN + cfg.SQUEEZE.SWEET_MAX) / 2;
  signals.sort((a, b) => {
    const lv = { strong_repeat: 2, repeat: 1, none: 0 };
    if (lv[b.persistence] !== lv[a.persistence]) return lv[b.persistence] - lv[a.persistence];
    const da = Math.abs(a.boxPct - sweetMid), db = Math.abs(b.boxPct - sweetMid);
    if (da !== db) return da - db;
    return b.execStrength - a.execStrength;
  });

  console.log(`[trigger] 모집단 ${pop.length} / 동적임계 ${dynThreshold ?? '없음(절대값만)'} / 신호 ${signals.length}`);
  return signals;
}

module.exports = { evaluate, passNoise, persistence };
