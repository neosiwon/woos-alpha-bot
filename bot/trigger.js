// bot/trigger.js — 3차 점화 트리거: 24h VWAP ±1% 진입 (v3)
// scan.findCandidates로 받은 2단 통과 후보들에 대해 VWAP 진입 여부 확인.
// 통과한 종목만 signals로 반환 → index.js에서 notify.sendTelegram.
//
// 인자 호환: 기존 index.js가 (candidates, execBlock) 시그니처로 호출.
// execBlock은 사용 안 함 (구버전 호환 위해 받기만).

const cfg = require('../config/config');
const upbit = require('./exchange/upbit');
const vwapEntry = require('./source/vwap_entry');

async function evaluate(candidates, _execBlock) {
  if (!candidates || !candidates.length) {
    console.warn('[trigger] 후보 없음');
    return [];
  }

  // 각 후보의 VWAP 진입 여부 (배치 호출)
  const results = await upbit._batchMap(candidates, async (c) => {
    const v = await vwapEntry.evaluate(c.symbol);
    if (!v.triggered) return null;

    // 매수가 = VWAP 진입 시점 현재가 (5분봉 종가)
    return {
      ...c,
      vwap: v.vwap,
      distNow: v.distNow,
      // referencePrice는 scan에서 ticker 가격, vwap_entry는 5분봉 종가. 후자가 더 정확.
      referencePrice: v.currentPrice,
      candleTime: v.candleTime,
    };
  });

  const signals = results.filter(x => x);
  // 박스 여유(큰 발사 잠재력) 큰 순으로 정렬
  signals.sort((a, b) => b.toTopPct - a.toTopPct);
  console.log(`[trigger] 3단 VWAP 진입 ${signals.length}건 (후보 ${candidates.length})`);
  return signals;
}

module.exports = { evaluate };

