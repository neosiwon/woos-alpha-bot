// bot/scan.js — 후보 선정 (v3)
// 흐름: 흡수(1단) → 박스 상단 통과(2단). 통과한 종목만 candidates로 반환.
// candidates는 index.js → trigger.evaluate로 넘어가서 VWAP 진입(3단) 평가.
//
// 5/27 백테스트:
//   - 1단 흡수: 81개
//   - 2단 박스: 15개 (큰 발사 +10%↑ 모두 포함)

const cfg = require('../config/config');
const upbit = require('./exchange/upbit');
const collector = require('./source/collector');
const resistance = require('./source/resistance');

async function findCandidates() {
  // 1단: 흡수
  const absorption = collector.detectAbsorption();
  if (!absorption) { console.warn('[scan] 흡수 후보 없음 -> 스킵'); return null; }
  const syms = Object.keys(absorption);
  console.log(`[scan] 1단 흡수 후보 ${syms.length}개`);

  // 2단: 박스 상단 검증 (배치)
  const results = await upbit._batchMap(syms, async (sym) => {
    const abs = absorption[sym];
    const res = await resistance.evaluate(sym);
    if (!res.passed) return null;

    // 매도벽 비율 = 매도벽(KRW) / 그날 거래대금(KRW). 표시용.
    let wallRatioPct = null;
    if (res.askWallKrw && abs.dayValue > 0) {
      wallRatioPct = res.askWallKrw / abs.dayValue * 100;
    }

    return {
      symbol: sym,
      // 흡수 정보
      surge: abs.surge,
      range: abs.range,
      absorbTime: abs.time,
      dayValue: abs.dayValue,
      // 저항 정보
      toTopPct: res.toTopPct,
      high24: res.high24,
      referencePrice: res.currentPrice,
      wallRatioPct,        // 표시용 (null 가능)
      askWallKrw: res.askWallKrw,
    };
  });

  const candidates = results.filter(x => x);
  // 박스 여유 큰 순 (= 발사 잠재력 큰 순) 정렬
  candidates.sort((a, b) => b.toTopPct - a.toTopPct);
  console.log(`[scan] 2단 박스 통과 ${candidates.length}개`);
  return candidates;
}

// 9시 일일 리포트용 — 1단 흡수 후보 요약 텍스트
function buildAbsorptionSummary() {
  const absorption = collector.detectAbsorption();
  if (!absorption) return '🌅 오늘의 매집 후보: 없음';
  const list = Object.entries(absorption)
    .map(([s, a]) => ({ sym: s, ...a, score: a.surge / (a.range + 0.5) }))
    .sort((a, b) => b.score - a.score)
    .slice(0, 10);
  if (!list.length) return '🌅 오늘의 매집 후보: 없음';
  const lines = list.map((x, i) => {
    const ko = upbit.getKoreanName(x.sym);
    const name = ko ? `${ko}(${x.sym})` : x.sym;
    return `${i + 1}. ${name} 거래 ${x.surge.toFixed(1)}배↑ 가격 ${x.range.toFixed(1)}% @${x.time}`;
  });
  return `🌅 오늘의 매집 후보 ${list.length}종 (TOP 10)\n` + lines.join('\n');
}

module.exports = { findCandidates, buildAbsorptionSummary };

