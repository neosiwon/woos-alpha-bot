// bot/index.js — 메인 진입점 (v3)
// v3 변경: candidates 받은 직후 notify.sendStage2 호출 (2차 감시 알람).
// 나머지는 기존 흐름과 동일 (scan → trigger → notify.sendTelegram).

const cfg = require('../config/config');
const scan = require('./scan');
const trigger = require('./trigger');
const notify = require('./notify');
const state = require('./state');
const collector = require('./source/collector');
const dominance = require('./dominance');
const verifier = require('./verifier');
const report = require('./report');

async function tick() {
  const t = new Date().toISOString();
  console.log('\n[' + t + '] tick 시작');

  await dominance.record();
  const regime = dominance.judge();
  await verifier.update();
  await report.maybeSendDaily();
  console.log('[tick] 국면: ' + regime);

  // 1+2단: 흡수 → 박스 통과 후보
  const candidates = await scan.findCandidates();
  if (!candidates || !candidates.length) { console.log('[tick] 후보 없음 -> 다음'); return; }

  // v3 신규: 2차 감시 알람 (PRIVATE only, in-memory 쿨다운)
  await notify.sendStage2(candidates, regime);

  // 3단: VWAP 진입 검증 → 매수 신호
  const block = collector.getLatestExecBlock();   // 구버전 호환 (trigger.evaluate가 무시)
  const signals = await trigger.evaluate(candidates, block);
  if (!signals.length) { console.log('[tick] 매수 신호 없음'); return; }

  signals.forEach(s => { s.regime = regime; });
  const fresh = signals.filter(s => !state.inCooldown(s.symbol));
  if (!fresh.length) { console.log('[tick] 신호 ' + signals.length + ' 전부 쿨다운'); return; }

  // 3차 알람 (PRIVATE + GROUP)
  await notify.sendTelegram(fresh);
  for (const s of fresh) state.markNotified(s.symbol);
  verifier.register(fresh);
  console.log('[tick] 매수 알림 ' + fresh.length + '건 발송');
}

async function main() {
  console.log('=== woos-alpha-bot v3 시작 (주기 ' + cfg.LOOP_INTERVAL_SEC + 's) ===');
  await tick();
  setInterval(tick, cfg.LOOP_INTERVAL_SEC * 1000);
}

main().catch(e => { console.error('main 오류:', e); process.exit(1); });

