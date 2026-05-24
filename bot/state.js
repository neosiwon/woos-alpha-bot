const fs = require('fs');
const cfg = require('../config/config');

function _load() {
  try {
    if (fs.existsSync(cfg.STATE_FILE)) return JSON.parse(fs.readFileSync(cfg.STATE_FILE, 'utf8'));
  } catch (e) { console.error('[state] load fail: ' + e.message); }
  return { cooldowns: {} };
}

function _save(s) {
  try { fs.writeFileSync(cfg.STATE_FILE, JSON.stringify(s)); }
  catch (e) { console.error('[state] save fail: ' + e.message); }
}

// 쿨다운 중이면 true (최근 SIGNAL_COOLDOWN_MIN 내 알림 보냄)
function inCooldown(symbol) {
  const s = _load();
  const last = s.cooldowns[symbol];
  if (!last) return false;
  const elapsedMin = (Date.now() - last) / 60000;
  return elapsedMin < cfg.ALPHA_TRIGGER.SIGNAL_COOLDOWN_MIN;
}

// 알림 보낸 시각 기록
function markNotified(symbol) {
  const s = _load();
  s.cooldowns[symbol] = Date.now();
  _save(s);
}

module.exports = { inCooldown, markNotified };
