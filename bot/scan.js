const cfg = require('../config/config');
const upbit = require('./exchange/upbit');
const collector = require('./source/collector');

// 후보 선정 (A 구조 — 5/25 259종목 검증):
//   1) 매집 감지: 5분 순매수 스파이크 상위 N (MAJORS 제외) — 세력 매집 종목
//   2) 수축 확인: 그 중 박스폭 <= BOX_PCT_MAX — 매집의 동반 + 안전장치 + 발사 대기
// 근거: 매집(스파이크상위)+수축 그룹 +5%상승 47% vs 죽은수축 7% (7배).
//       순서 중요 — 수축 먼저 걸면 죽은 코인 다수. 매집 먼저 걸러야 함.
async function findCandidates() {
  // 1단계 — 매집 스파이크 (collector, CSV 기반)
  const spikes = collector.getSpikes();
  if (!spikes) { console.warn('[scan] 스파이크 없음 (CSV 미수집?) -> 스킵'); return null; }

  const majors = new Set(cfg.MAJORS);
  // 매집(양) 종목만 추려 스파이크 내림차순
  const positives = Object.keys(spikes)
    .filter(sym => !majors.has(sym))            // MAJORS(시총 대형 + XLM) 제외
    .filter(sym => spikes[sym].spike > 0)       // 0-0: 순매수 스파이크 양(+)만 (매집)
    .sort((a, b) => spikes[b].spike - spikes[a].spike);

  // 동적 상위 N% 컷 (5/25 알파 역산: 상위 0.4~2.0% → TOP_PCT 2.5%). MIN/MAX로 보정.
  const sp = cfg.SPIKE;
  let cut = Math.round(positives.length * (sp.TOP_PCT / 100));
  if (sp.TOP_MIN != null) cut = Math.max(cut, sp.TOP_MIN);
  if (sp.TOP_MAX != null) cut = Math.min(cut, sp.TOP_MAX);
  cut = Math.min(cut, positives.length);
  const ranked = positives.slice(0, cut);

  if (!ranked.length) { console.warn('[scan] 매집 후보 0'); return null; }
  console.log('[scan] 매집 스파이크 상위 ' + ranked.length + '종 (전체 ' + positives.length + ' 중 ' + sp.TOP_PCT + '%): ' + ranked.join(', '));

  // 2단계 — 수축 확인 (업비트 캔들, 상위 소수만 조회 = 가벼움)
  const need = Math.ceil(cfg.SQUEEZE.LOOKBACK_MIN / 5); // 60분 / 5분 = 12개
  const candidates = [];

  const results = await upbit._batchMap(ranked, async (sym) => {
    const candles = await upbit.fetchCandlesM5(sym, need);
    if (!candles) return null;                          // 0-0: 캔들 부족 제외
    const boxPct = upbit.calcBoxPct(candles);
    if (boxPct === null) return null;                   // 0-0: 계산 불가 제외
    if (boxPct > cfg.SQUEEZE.BOX_PCT_MAX) return null;  // 수축 아님 제외
    return {
      symbol: sym,
      boxPct,
      referencePrice: candles[candles.length - 1].close,
      spike: spikes[sym].spike,                         // 매집 강도 (알림 표시용)
      spikeTs: spikes[sym].spikeTs,
      rank: positives.indexOf(sym) + 1,                 // 매집 스파이크 전체 순위 (#1=최대)
    };
  });

  for (const r of results) if (r) candidates.push(r);
  // 매집 강도순 정렬
  candidates.sort((a, b) => b.spike - a.spike);
  console.log('[scan] 매집+수축 통과 후보 ' + candidates.length + '종');
  return candidates;
}

module.exports = { findCandidates };
