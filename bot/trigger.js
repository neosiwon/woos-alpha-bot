const cfg = require('../config/config');

// 매도 소진 상태 판정 (추천안 — 트리거 아님, 알림 표시/검증용).
// 5/25 알파 3건 신호 직전 공통: 매도만(강도0) 연속 → 매도마름(9999) 전환.
// 세력 발사 직전 "마지막 개미 털기 → 매도 소진" 가설. 표본 3건이라 박제 않고 measure.
// 반환 라벨: 'DRY'(매도마름 9999) / 'SELL_ONLY'(매도만, 강도0) / 'WEAK'(매도우위 <100) / 'NORMAL' / 'NONE'
function sellExhaustion(e) {
  if (!e) return { label: 'NONE', execStrength: null };
  const s = e.execStrength;
  let label = 'NORMAL';
  if (s >= 9999) label = 'DRY';                       // 매도 0 = 마름
  else if (s === 0 && e.sellKrw > 0) label = 'SELL_ONLY'; // 매수0, 매도만
  else if (s > 0 && s < 100) label = 'WEAK';          // 매도 우위
  return { label, execStrength: s };
}

// 신호 = scan 후보(매집 스파이크 상위 + 수축) 그 자체.
// 체결강도 150 게이트 제거 — 5/25 검증: 알파 신호 시점 노이즈필터 통과 0건, 강도 0/9999 극단.
// 150 트리거는 알파를 100% 막음 → 제거. 매집+수축이 곧 발사 대기 신호 (+5%상승 47%).
// candidates: scan.findCandidates() 결과 [{symbol, boxPct, referencePrice, spike, spikeTs}]
// execBlock: collector.getLatestExecBlock() (매도소진 표시용, 없어도 신호는 발생)
function evaluate(candidates, execBlock) {
  if (!candidates || !candidates.length) { console.warn('[trigger] 후보 없음'); return []; }

  const signals = candidates.map(c => {
    const e = execBlock && execBlock.data ? execBlock.data[c.symbol] : null;
    const ex = sellExhaustion(e);
    return {
      ...c,
      execStrength: ex.execStrength,   // 현재 체결강도 (표시용)
      sellState: ex.label,             // 매도소진 라벨 (추천안 — 알림 별도 표시)
    };
  });

  // 매집 강도(spike) 순 — scan에서 이미 정렬됐지만 명시
  signals.sort((a, b) => (b.spike || 0) - (a.spike || 0));
  const dry = signals.filter(s => s.sellState === 'DRY' || s.sellState === 'SELL_ONLY').length;
  console.log(`[trigger] 신호 ${signals.length} (매도소진상태 ${dry})`);
  return signals;
}

module.exports = { evaluate, sellExhaustion };
