// bot/source/vwap_entry.js — 3차 트리거: VWAP ±1% 진입 (점화)
// 현재 5분봉 종가가 24h VWAP에서 ±1% 안 = 발사 점화.
// 5/27 검증: PRL/TRAC/ICP/ALT 모두 발사 직전 ±1% 안 진입.

const cfg = require('../../config/config');
const upbit = require('../exchange/upbit');

// 5분봉 N개의 VWAP (거래량 가중 평균가)
// candles: {value, volume} 필드 보유. upbit.fetchCandlesM5는 둘 다 채워줌.
function _calcVwap(candles) {
  let num = 0, den = 0;
  for (const c of candles) {
    // value = candle_acc_trade_price = sum(price × volume), volume = 거래량
    num += c.value;
    den += c.volume;
  }
  return den > 0 ? num / den : 0;
}

// 입력: symbol
// 출력: { triggered, vwap, distNow, currentPrice, candleTime }
async function evaluate(symbol) {
  const V = cfg.VWAP_ENTRY;
  const NEED = V.VWAP_HOURS * (60 / V.CANDLE_INTERVAL_MIN); // 288봉

  // 5분봉 NEED+1개 (upbit.fetchCandlesM5가 자동 페이징)
  const candles = await upbit.fetchCandlesM5(symbol, NEED + 1);
  if (!candles || candles.length < NEED + 1) {
    return { triggered: false, reason: 'short_candles', got: candles ? candles.length : 0 };
  }

  // 마지막 봉 = 현재 시점
  const current = candles[candles.length - 1];
  const window = candles.slice(-1 - NEED, -1); // 현재 봉 직전 288봉
  const vwap = _calcVwap(window);
  if (vwap <= 0) return { triggered: false, reason: 'no_vwap' };

  const distNow = (current.close / vwap - 1) * 100;

  // 트리거: 현재 ±N% 안 (전환 요구 X — 쿨다운으로 중복 방지)
  const triggered = Math.abs(distNow) <= V.ENTRY_BAND_PCT;

  return {
    triggered,
    vwap,
    distNow,
    currentPrice: current.close,
    candleTime: current.ts,
  };
}

module.exports = { evaluate };

