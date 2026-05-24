const cfg = require('./config/config');
const upbit = require('./bot/exchange/upbit');
const collector = require('./bot/source/collector');

async function main() {
  console.log('=== woos-alpha-bot 1단계 데이터 검증 ===\n');

  console.log('[1] 업비트 유니버스');
  const universe = await upbit.fetchUniverse();
  if (!universe) console.log('  X 실패 (업비트 차단? VM 리전 확인)');
  else console.log(`  O ${universe.length}종 (예: ${universe.slice(0,5).join(', ')})`);

  console.log('\n[2] 업비트 m5 캔들 + 박스폭');
  const sample = (universe && universe.length) ? universe[0] : 'IN';
  const candles = await upbit.fetchCandlesM5(sample, 12);
  if (!candles) console.log(`  X ${sample} 캔들 실패`);
  else { const b = upbit.calcBoxPct(candles); console.log(`  O ${sample}: ${candles.length}개, 종가 ${candles[candles.length-1].close}, 박스폭 ${b===null?'null':b.toFixed(2)+'%'}`); }

  console.log('\n[3] 수집기 CSV 최신 블록');
  console.log(`  경로: ${collector._csvPath()}`);
  const block = collector.getLatestExecBlock();
  if (!block || !block.data) console.log('  X CSV 못읽음 (경로/수집기 확인)');
  else {
    const syms = Object.keys(block.data);
    const top = syms.map(s => ({s, g: block.data[s].execStrength})).sort((a,b)=>b.g-a.g).slice(0,5);
    console.log(`  O 시각 ${block.ts} / ${syms.length}종`);
    console.log(`  체결강도 상위5: ${top.map(t=>t.s+'('+t.g+')').join(', ')}`);
  }

  console.log('\n[4] 지속성 시퀀스');
  const psym = (block && block.data) ? Object.keys(block.data)[0] : sample;
  const series = collector.getRecentSeries(psym, cfg.ALPHA_TRIGGER.PERSISTENCE_WINDOW);
  if (!series) console.log(`  ! ${psym} 시퀀스 없음 (누적 전이면 정상)`);
  else console.log(`  O ${psym}: ${series.length}틱 [${series.map(x=>x.execStrength).join(', ')}]`);

  console.log('\n=== 종합 ===');
  const ok = !!universe && !!candles && !!(block && block.data);
  console.log(ok ? ' O 1단계 통과 — trigger/notify 준비됨' : ' X 미통과 — 위 로그 확인');
}
main().catch(e => { console.error('verify 오류:', e); process.exit(1); });
