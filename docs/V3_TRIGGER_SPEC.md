# woos-alpha-bot V3 트리거 명세서

**버전:** v3.0 (2026-05-28)
**대상:** 업비트 KRW 마켓 (스테이블 제외 전 종목)

---

## 1. 3단 트리거 개요

| 단계 | 이름 | 역할 | 알람 채널 |
|------|------|------|-----------|
| 1차 | **흡수** | 매집 신호 후보 발굴 | (알람 없음, 9시 리포트에 TOP10 요약) |
| 2차 | **박스 상단** | 발사 잠재력 충분한 후보 압축 | MONITOR |
| 3차 | **VWAP 점화** | 평단 진입 → 매수 신호 발생 | MONITOR + GROUP |

각 단계는 직전 단계를 통과한 종목만 평가. 캐스케이드 구조.

---

## 2. 단계별 명세

### 2.1 1차 — 흡수 감지 (collector.js : `detectAbsorption`)

**의미:** 큰 거래가 들어왔는데 가격이 거의 안 움직이는 구간 = 매집(흡수)

**조건 (AND):**
- 거래량 ≥ `baseline × ABSORPTION.VOL_MULT` (기본 3배)
- 가격폭 ≤ `ABSORPTION.PRICE_RANGE_PCT` (기본 4%)
- 거래대금 ≥ `ABSORPTION.MIN_TURNOVER` (기본 10억원)

**baseline 계산:**
- 직전 60분 1분봉 거래량 60개의 **하위 25% 평균** 사용
- 중앙값/평균 대신 하위 25%인 이유: 발사 직전 거래량 증가가 baseline을 끌어올리면 흡수가 안 감지됨

**입력:** Upbit 1분봉 캔들 (직전 60분)
**출력:** `{symbol, surge, range, absorbTime, turnover, ...}` 또는 null

---

### 2.2 2차 — 박스 상단 (resistance.js)

**의미:** 24시간 고점까지 충분한 여유 (저항 얇음) → 발사 시 잠재 폭 큼

**조건:**
- `toTopPct ≥ RESISTANCE.MIN_TOP_PCT` (기본 7%)
- 즉, 현재가에서 24h 고점까지 7% 이상 여유

**참고만 표시 (필터 X):**
- 매도벽 비율: 호가창에서 위 5호가 매도잔량 / 평균 거래량
- 흡수 시점엔 거래량 부족해서 비율이 부풀려져 필터로는 부적합. 표시만 함.

---

### 2.3 3차 — VWAP 점화 (vwap_entry.js)

**의미:** 24시간 평단(VWAP) 근처에서 매수 = 평단 진입자가 본전권

**조건:**
- 현재가가 24h VWAP 의 ±`VWAP_ENTRY.MAX_DEVIATION_PCT` 안 (기본 1%)
- 동일 종목 30분 쿨다운

**VWAP 계산:**
```
VWAP = Σ(close_i × volume_i) / Σ(volume_i)
i ∈ 직전 24시간 5분봉 (288개)
```

---

## 3. 핵심 계산식 모음

| 지표 | 공식 | 소스 |
|------|------|------|
| 체결강도 | 매수체결량 / (매수체결량 + 매도체결량) × 100 | 매도0 → 9999 |
| 박스폭 | (high - low) / low × 100 | 60분 1분봉 |
| 거래대금 | price × volume (원) | 1분봉당 |
| baseline (volume) | 60개 중 하위 25% 평균 | 흡수 감지용 |
| MFE | (max_high - entry) / entry × 100 | 사후 검증 |
| MAE | (min_low - entry) / entry × 100 | 사후 검증 |
| VWAP | Σ(p×v) / Σ(v), 288개 5분봉 | 24h |
| toTopPct | (high24h - now) / now × 100 | 박스 상단 여유 |

---

## 4. 알람 채널 라우팅

| 채널 | 환경변수 | 받는 알람 |
|------|----------|-----------|
| MONITOR | `TELEGRAM_CHAT_ID_MONITOR` | 2차 매집 후보 + 3차 매수 + 9시 일일리포트 |
| GROUP | `TELEGRAM_CHAT_ID_GROUP` | 3차 매수 신호만 |
| PRIVATE (옵션) | `TELEGRAM_CHAT_ID_PRIVATE` | 9시 일일리포트 (있으면) |

**fallback 체인:**
- MONITOR 없으면 → PRIVATE → CHAT_ID (legacy)
- GROUP 없으면 → 단톡 발송 안 함

---

## 5. 3차 매수 알람 포맷 (v3 새 디자인)

```
🚨 매수 신호 (업비트)
─────────────
▶ 코인명 : 파로스 [PROS]              ← <b> 굵게

━━ 🔥 매수가 977원 🔥 ━━              ← <b> 굵게, 강조

• 평단 진입 -0.20% (점화)
• 매집진행 거래 10.3배↑ / 가격 2.2%
• 박스 상단 +11.0% (저항 얇음)
• 매도벽 2.01% (참고)
• BTC 📉 약세
─────────────
🛑 손절가 899원 (-8%)

🎯 TP1 1,026원 (+5%) → 50%
🎯 TP2 1,075원 (+10%) → 30%
🎯 TP3 1,124원 (+15%) → 20%
• 보유한계 4H (TP1 도달 시 본절)
```

`parse_mode: 'HTML'` 사용. `<b>` 태그로 핵심값 굵게.

---

## 6. 9시 일일 리포트

**KST 09:00 자동 발동** (`maybeSendDaily`)

**구성:**
1. **어제 성적표** (24h 확정된 신호들)
   - 신호 수, +5% 도달률
   - 상위 3종목 MFE
   - tp3/tp2/tp1/손절/만료 분포
   - BTC 강세/약세별 성적
2. **오늘 매집 후보 TOP10** (`scan.buildAbsorptionSummary`)
   - 1차 흡수 통과 종목들, 거래대금 순

**발송 + 저장:**
- 텔레그램 → MONITOR + (옵션) PRIVATE
- GitHub → `reports/YYYY-MM-DD.md` 자동 commit/push

**중복 방지:** `state.json`에 `lastDate` 기록, 같은 날 재발송 안 함

---

## 7. 파일 인벤토리

| 파일 | 역할 |
|------|------|
| `bot/index.js` | 1분 tick 진입점. scan → trigger → notify 호출 |
| `bot/scan.js` | `findCandidates` (1단+2단 합침), `buildAbsorptionSummary` |
| `bot/trigger.js` | `evaluate(candidates)` — 3단 VWAP 진입 평가 |
| `bot/notify.js` | `sendStage2`, `sendTelegram` — 텔레그램 발송 |
| `bot/report.js` | `maybeSendDaily` — 9시 리포트 + GitHub push |
| `bot/source/collector.js` | CSV reader + `detectAbsorption` (1단) |
| `bot/source/resistance.js` | 박스 상단 필터 (2단) |
| `bot/source/vwap_entry.js` | VWAP 진입 평가 (3단) |
| `bot/exchange/upbit.js` | Upbit REST/WS 래퍼, 5분/60분봉, ticker, 한글명 |
| `bot/dominance.js` | CoinGecko BTC.D / USDT.D 4시간 변동 → 국면 판정 |
| `bot/state.js` | 런타임 state.json 읽기/쓰기 |
| `bot/verifier.js` | 신호 사후 추적 (MFE/MAE/실현수익) |
| `config/config.js` | 환경변수 + 트리거 임계값 |

---

## 8. 백테스트 검증 (2026-05-27 데이터)

| 단계 | 통과 수 | 정확도 |
|------|---------|--------|
| 1차 흡수 | 81개 후보 | 원본 5/6 포함 (ONG는 거래대금 3억 미달) |
| 2차 박스상단 | 15개 | 큰 발사(+10%↑) 모두 포함 |
| 3차 VWAP 진입 | 14개 | 원본 4/5 (PROS는 흡수→발사 19시간 갭) |

**헛걸림 10개 모두 +0~7% 작은 움직임** → 분산 진입 시 손실 미미

**실시간 검증 (2026-05-28 03:09):**
- PROS 백테스트에선 19시간 갭으로 빠졌으나, 실시간에선 정확히 잡힘 (-0.20% VWAP 진입)
- 흡수 10.3배, 박스 +11.0%, 매도벽 2.01% 모두 표시 정상

---

## 9. 2026-05-28 패치 요약

### v3 트리거 도입 (10개 파일 신규/교체)
- 기존 단순 체결강도 트리거에서 → **3단 캐스케이드** 전환
- 거래량 상위 후보 폐기 → **수축 박스 + 흡수** 으로 선행 지표 강화

### 알람 포맷 새 디자인
- HTML `<b>` 굵게 (텔레그램 `parse_mode: 'HTML'`)
- 매수가를 `━━ 🔥 매수가 X원 🔥 ━━` 별도 줄로 강조
- 각 지표 앞 `•` bullet, "흡수" → "매집진행" 용어 변경
- `24h 평단` 줄 제거 (매수가와 중복)

### 채널 분리 (3개)
- MONITOR 추가 (2차 + 3차 + 9시 리포트)
- GROUP 3차만
- PRIVATE fallback 유지

### 9시 리포트 개선
- HTTP 응답 본문 로깅 (이전 silent fail 진단용)
- `reports/YYYY-MM-DD.md` 자동 git push

---

## 10. 운영 명령어

```bash
# 서비스 상태
sudo systemctl status woos-alpha-bot

# 로그 실시간
journalctl -u woos-alpha-bot -f

# 리포트 즉시 발송 + push (9시 안 기다리고)
cd ~/woos-alpha-bot && set -a && source .env && set +a && node -e "..."

# .env 변수 확인
cd ~/woos-alpha-bot && set -a && source .env && set +a && \
  node -e "const c=require('./config/config'); ['TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID_MONITOR','TELEGRAM_CHAT_ID_GROUP'].forEach(k=>console.log(k+':', c[k]?'set':'MISSING'))"

# 재시작
sudo systemctl restart woos-alpha-bot
```

---

## 11. 핵심 원칙 (지속)

- **거래량 상위로 후보를 좁히면 선점 실패** → 수축이 진짜 선행 지표
- **체결강도 절대값은 시간대마다 의미가 다름** → 반드시 동적 상위 N% 방식
- **매도벽은 흡수 시점엔 거래량 부족으로 비율 부풀려짐** → 필터 X, 표시만
- **baseline = 하위 25%** → 발사 시간대 거래량이 baseline 끌어올리는 것 방지
- **VWAP 진입 = "안에 있다"가 아닌 ±1% 진입 + 30분 쿨다운**

---

*Maintained by neosiwon@instance-20260524-081536*
