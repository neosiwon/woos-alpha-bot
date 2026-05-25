const cfg = require('../config/config');
const scan = require('./scan');
const trigger = require('./trigger');
const notify = require('./notify');
const state = require('./state');
const collector = require('./source/collector');
const dominance = require('./dominance');
const verifier = require('./verifier');

async function tick() {
  const t = new Date().toISOString();
  console.log('\n[' + t + '] tick 시작');

  await dominance.record();
  const regime = dominance.judge();
  await verifier.update();
  console.log('[tick] 국면: ' + regime);

  const candidates = await scan.findCandidates();
  if (!candidates || !candidates.length) { console.log('[tick] 후보 없음 -> 다음'); return; }

  // 체결강도 블록 = 매도소진 표시용 (없어도 신호는 발생 — 트리거 아님)
  const block = collector.getLatestExecBlock();

  const signals = trigger.evaluate(candidates, block);
  if (!signals.length) { console.log('[tick] 신호 없음'); return; }

  signals.forEach(s => { s.regime = regime; });
  const fresh = signals.filter(s => !state.inCooldown(s.symbol));
  if (!fresh.length) { console.log('[tick] 신호 ' + signals.length + ' 전부 쿨다운'); return; }

  await notify.sendTelegram(fresh);
  for (const s of fresh) state.markNotified(s.symbol);
  verifier.register(fresh);
  console.log('[tick] 알림 ' + fresh.length + '건 발송');
}

async function main() {
  console.log('=== woos-alpha-bot 시작 (주기 ' + cfg.LOOP_INTERVAL_SEC + 's) ===');
  await tick();
  setInterval(tick, cfg.LOOP_INTERVAL_SEC * 1000);
}

main().catch(e => { console.error('main 오류:', e); process.exit(1); });
