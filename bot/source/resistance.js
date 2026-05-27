// bot/source/resistance.js — 2차 트리거: 박스 상단 여유 (저항)
// 위로 갈 공간 ≥ N% 인 종목만 통과. 매도벽 비율은 표시용.
// 5/27 백테스트: 박스상단 ≥7% 컷 → 큰 발사 모두 포함, 헛걸림은 +0~7% 작은 움직임.

const cfg = require('../../config/config');
const upbit = require('../exchange/upbit');
const collector = require('./collector');

// 한 종목의 저항 평가
// 입력: symbol, currentPrice (선택, 없으면 ticker 조회)
// 출력: { passed, toTopPct, high24, wallRatioPct(표시용), askWallKrw, dayVolKrw }
async function evaluate(symbol, currentPrice) {
  const R = cfg.RESISTANCE;

  // 1) 박스 상단 = 직전 N시간 고점
  const candles = await upbit.fetchCandlesM60(symbol, R.BOX_LOOKBACK_HOURS);
  if (!candles || candles.length < 12) {
    return { passed: false, reason: 'no_candles' };
  }
  const high24 = Math.max(...candles.map(c => c.high));

  // 현재가 (없으면 ticker)
  let price = currentPrice;
  if (!price) {
    const tk = await upbit.fetchTicker(symbol);
    price = tk && tk.trade_price;
  }
  if (!price) return { passed: false, reason: 'no_price' };

  const toTopPct = (high24 / price - 1) * 100;

  // 2) 매도벽 비율 (표시용 — 필터 X)
  let wallRatioPct = null, askWallKrw = null, dayVolKrw = 0;
  if (R.SHOW_WALL_RATIO) {
    const ob = collector.getRecentOrderbookForWall(symbol);
    if (ob) {
      askWallKrw = ob.top5Ask * ob.ask1Price;
      // 그날 거래대금은 detectAbsorption 결과에 dayValue로 있어 거기서 가져옴 (scan에서 주입)
      // 여기선 alone일 때 호가 raw에 의존 — wallRatioPct는 scan에서 계산
    }
  }

  const passed = toTopPct >= R.BOX_TOP_MIN_PCT;
  return { passed, toTopPct, high24, currentPrice: price, askWallKrw, wallRatioPct, dayVolKrw };
}

module.exports = { evaluate };

