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

function _ensureHeader() {
  if (!fs.existsSync(cfg.VERIFY_LOG_FILE)) {
    fs.writeFileSync(cfg.VERIFY_LOG_FILE, '\uFEFF신호시각,종목,진입가,MFE%,MAE%,종료가,실현수익%,5%달성,국면,진입체결강도,청산사유\n');
  }
}

function register(signals) {
  const s = _load();
  const now = Date.now();
  for (const sig of signals) {
    if (sig.referencePrice == null || !(sig.referencePrice > 0)) continue;
    if (s.tracking.find(t => t.symbol === sig.symbol)) continue;
    s.tracking.push({
      symbol: sig.symbol,
      entryPrice: sig.referencePrice,
      entryTs: now,
      mfe: sig.referencePrice,
      mae: sig.referencePrice,
      regime: sig.regime || 'UNKNOWN',
      execStrength: sig.execStrength || 0,
      signalTime: new Date(now + 9 * 3600 * 1000).toISOString().slice(0, 19).replace('T', ' '),
      tp1Hit: false,
      tp2Hit: false,
      stopPct: cfg.EXIT_PARAMS.COMMON.STOP_PCT,
      realizedPct: 0,
      posLeft: 1.0,
      exitReason: null,
      exitPrice: null,
    });
  }
  _save(s);
}

async function update() {
  const s = _load();
  if (!s.tracking.length) return;
  const now = Date.now();
  const stillTracking = [];

  for (const t of s.tracking) {
    // 4시간 경과 시 TP 판정 없이 즉시 종료 (보유한계 초과 후 가격은 반영 안 함)
    const elapsedH = (now - t.entryTs) / 3600000;
    if (elapsedH >= cfg.VERIFY_HOURS) {
      // 마지막 현재가만 갱신(종료가 기록용), TP/손절 판정은 안 함
      const candles = await upbit.fetchCandlesM5(t.symbol, 1);
      if (candles && candles.length) {
        const c = candles[candles.length - 1];
        if (c.high > t.mfe) t.mfe = c.high;
        if (c.low < t.mae) t.mae = c.low;
        t.lastPrice = c.close;
      }
      _finalize(t);
      continue;
    }
    const candles = await upbit.fetchCandlesM5(t.symbol, 1);
    if (candles && candles.length) {
      const c = candles[candles.length - 1];
      if (c.high > t.mfe) t.mfe = c.high;   // 고가로 MFE (봉내 최고 포착)
      if (c.low < t.mae) t.mae = c.low;     // 저가로 MAE (봉내 최저 포착)
      t.lastPrice = c.close;
      _splitExit(t, c.high, c.low);          // TP는 고가, 손절은 저가로 판정
    }
    if (t.exitReason) {
      _finalize(t);
    } else {
      stillTracking.push(t);
    }
  }
  s.tracking = stillTracking;
  _save(s);
}

function _splitExit(t, high, low) {
  if (t.exitReason) return;
  const ep = cfg.EXIT_PARAMS;
  if (t.tp1Hit === undefined) { t.tp1Hit = false; t.tp2Hit = false; t.stopPct = ep.COMMON.STOP_PCT; t.realizedPct = 0; t.posLeft = 1.0; }
  const param = (t.regime === 'STRONG') ? ep.STRONG : ep.WEAK;
  // 손절 먼저 (저가 기준, 보수적 — 같은 봉에서 손절·TP 동시 터치 시 손절 우선)
  const retLow = (low - t.entryPrice) / t.entryPrice * 100;
  if (retLow <= t.stopPct) {
    t.realizedPct += t.posLeft * t.stopPct; t.posLeft = 0;
    t.exitReason = (t.stopPct < 0 ? 'stop' : 'breakeven');
    t.exitPrice = t.entryPrice * (1 + t.stopPct / 100); return;
  }
  // TP (고가 기준 — 봉내 도달 포착)
  const retHigh = (high - t.entryPrice) / t.entryPrice * 100;
  if (!t.tp1Hit && retHigh >= param.TP1) { t.realizedPct += param.W1 * param.TP1; t.posLeft -= param.W1; t.tp1Hit = true; t.stopPct = 0; }
  if (!t.tp2Hit && retHigh >= param.TP2) { t.realizedPct += param.W2 * param.TP2; t.posLeft -= param.W2; t.tp2Hit = true; }
  if (retHigh >= param.TP3) { t.realizedPct += t.posLeft * param.TP3; t.posLeft = 0; t.exitReason = 'tp3'; t.exitPrice = t.entryPrice * (1 + param.TP3 / 100); return; }
}

function _finalize(t) {
  _ensureHeader();
  const mfePct = ((t.mfe - t.entryPrice) / t.entryPrice * 100).toFixed(2);
  const maePct = ((t.mae - t.entryPrice) / t.entryPrice * 100).toFixed(2);
  // 청산사유: tp3/stop은 그대로, 4h 타임아웃이면 도달한 최고 TP 반영
  let reason = t.exitReason;
  if (!reason) {
    if (t.tp2Hit) reason = 'time_tp2';
    else if (t.tp1Hit) reason = 'time_tp1';
    else reason = 'time';
  }
  const endPrice = t.exitPrice || t.lastPrice || t.entryPrice;
  let realized = (t.realizedPct || 0);
  const posLeft = (t.posLeft != null) ? t.posLeft : 1.0;
  if (posLeft > 0) realized += posLeft * ((endPrice - t.entryPrice) / t.entryPrice * 100);
  const endPct = realized.toFixed(2);
  const hit5 = (t.mfe - t.entryPrice) / t.entryPrice >= 0.05 ? 'O' : 'X';
  const row = [t.signalTime, t.symbol, t.entryPrice, mfePct, maePct, endPrice, endPct, hit5, t.regime, t.execStrength.toFixed(1), reason].join(',') + '\n';
  fs.appendFileSync(cfg.VERIFY_LOG_FILE, row);
  console.log('[verify] 확정 ' + t.symbol + ' MFE' + mfePct + '% MAE' + maePct + '% 실현' + endPct + '% 5%달성' + hit5);
}

module.exports = { register, update };
