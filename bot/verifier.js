const fs = require('fs');
const cfg = require('../config/config');
const upbit = require('./exchange/upbit');

function _load() {
  try { if (fs.existsSync(cfg.VERIFY_TRACK_FILE)) return JSON.parse(fs.readFileSync(cfg.VERIFY_TRACK_FILE, 'utf8')); }
  catch (e) { console.error('[verify] load fail: ' + e.message); }
  return { tracking: [] };
}
function _save(s) {
  try { fs.writeFileSync(cfg.VERIFY_TRACK_FILE, JSON.stringify(s)); }
  catch (e) { console.error('[verify] save fail: ' + e.message); }
}

// CSV 헤더 보장
function _ensureHeader() {
  if (!fs.existsSync(cfg.VERIFY_LOG_FILE)) {
    fs.writeFileSync(cfg.VERIFY_LOG_FILE, '\uFEFF신호시각,종목,진입가,MFE%,MAE%,종료가,종료수익%,5%달성,국면,진입체결강도,청산사유\n');
  }
}

// 신호 등록 (이미 추적중이면 스킵 = 쿨다운과 별개로 중복추적 방지)
function register(signals) {
  const s = _load();
  const now = Date.now();
  for (const sig of signals) {
    if (sig.referencePrice == null || !(sig.referencePrice > 0)) continue; // 0-0: 진입가 없으면 추적 안함
    if (s.tracking.find(t => t.symbol === sig.symbol)) continue; // 이미 추적중
    s.tracking.push({
      symbol: sig.symbol,
      entryPrice: sig.referencePrice,
      entryTs: now,
      mfe: sig.referencePrice,  // 최고가 (초기=진입가)
      mae: sig.referencePrice,  // 최저가
      regime: sig.regime || 'UNKNOWN',
      execStrength: sig.execStrength || 0,
      signalTime: new Date(now + 9 * 3600 * 1000).toISOString().slice(0, 19).replace('T', ' '),
      atr: sig.atr || null,
      peak: sig.referencePrice,
      trailActive: false,
      exitReason: null,
      exitPrice: null,
    });
  }
  _save(s);
}

// 매 틱: 추적종목 현재가 갱신 + 4시간 경과분 확정
async function update() {
  const s = _load();
  if (!s.tracking.length) return;
  const now = Date.now();
  const stillTracking = [];

  for (const t of s.tracking) {
    const candles = await upbit.fetchCandlesM5(t.symbol, 1);
    if (candles && candles.length) {
      const price = candles[candles.length - 1].close;
      if (price > t.mfe) t.mfe = price;
      if (price < t.mae) t.mae = price;
      t.lastPrice = price;
      _trailing(t, price);
    }
    // 4시간 경과 → 확정
    const elapsedH = (now - t.entryTs) / 3600000;
    if (t.exitReason || elapsedH >= cfg.VERIFY_HOURS) {
      _finalize(t);
    } else {
      stillTracking.push(t);
    }
  }
  s.tracking = stillTracking;
  _save(s);
}

// 트레일링 청산 판정 (완전체). regime별 익절선, ATR 트레일. UNKNOWN=약세 보수.
function _trailing(t, price) {
  if (t.exitReason) return; // 이미 청산
  const ep = cfg.EXIT_PARAMS;
  const ret = (price - t.entryPrice) / t.entryPrice * 100;
  // 손절
  if (ret <= ep.COMMON.STOP_PCT) { t.exitReason = 'stop'; t.exitPrice = price; return; }
  // regime별 파라미터 (UNKNOWN/WEAK=약세 보수)
  const strong = (t.regime === 'STRONG');
  const param = strong ? ep.STRONG : ep.WEAK;
  // 익절선 도달 → 트레일 활성화
  if (!t.trailActive && ret >= param.ACTIVATE_PCT) { t.trailActive = true; t.peak = price; }
  // 트레일 중: 고점 갱신 + 고점에서 ATR*배수 하락 시 익절
  if (t.trailActive) {
    if (price > t.peak) t.peak = price;
    if (t.atr && t.atr > 0) {
      const drop = t.peak - price;
      if (drop >= t.atr * param.ATR_MULT) { t.exitReason = 'trail'; t.exitPrice = price; return; }
    }
  }
}

function _finalize(t) {
  _ensureHeader();
  const mfePct = ((t.mfe - t.entryPrice) / t.entryPrice * 100).toFixed(2);
  const maePct = ((t.mae - t.entryPrice) / t.entryPrice * 100).toFixed(2);
  const reason = t.exitReason || 'time';
  const endPrice = t.exitPrice || t.lastPrice || t.entryPrice;
  const endPct = ((endPrice - t.entryPrice) / t.entryPrice * 100).toFixed(2);
  const hit5 = (t.mfe - t.entryPrice) / t.entryPrice >= 0.05 ? 'O' : 'X';
  const row = [t.signalTime, t.symbol, t.entryPrice, mfePct, maePct, endPrice, endPct, hit5, t.regime, t.execStrength.toFixed(1), reason].join(',') + '\n';
  fs.appendFileSync(cfg.VERIFY_LOG_FILE, row);
  console.log('[verify] 확정 ' + t.symbol + ' MFE' + mfePct + '% MAE' + maePct + '% 5%달성' + hit5);
}

module.exports = { register, update };
