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

  // 0. 도미넌스 기록 (자가축적, 4h 후 강세/약세 판정)
  await dominance.record();
  const regime = dominance.judge();
  await verifier.update();
  console.log('[tick] 국면: ' + regime);

  // 1. 수축 후보 스캔
  const candidates = await scan.findCandidates();
  if (!candidates || !candidates.length) { console.log('[tick] 후보 없음 -> 다음'); return; }

  // 2. 체결강도 블록
  const block = collector.getLatestExecBlock();
  if (!block || !block.data) { console.log('[tick] 체결강도 없음 -> 다음'); return; }

  // 3. 트리거 판정
  const signals = trigger.evaluate(candidates, block, collector.getRecentSeries);
  if (!signals.length) { console.log('[tick] 신호 없음'); return; }

  // 4. 쿨다운 거르고 알림
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
  await tick();                                  // 즉시 1회
  setInterval(tick, cfg.LOOP_INTERVAL_SEC * 1000); // 이후 주기
}

main().catch(e => { console.error('main 오류:', e); process.exit(1); });
