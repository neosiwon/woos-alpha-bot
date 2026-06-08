#!/usr/bin/env python3
# alpha_smc.py — SMC 시퀀스/구조 모듈 (LuxAlgo SMC 로직 Python 포팅)
# ============================================================
# 용도: 시퀀스 경계(펌핑·덤핑 추세전환) + 매물대 + 직전고저 — 보조 참고
#       ※발사 변별 아님(차트구조는 발사·비발사 비슷). 봇 신호가 주 변별자.
#       ※시퀀스를 절대값(-3%) 대신 CHoCH(구조전환)로 끊음 = 코인 정규화
#
# 제공:
#   cycle      : 현재 사이클 (bull=상승/흡수 가능, bear=덤핑 진행)
#   last_choch : 마지막 추세전환 시각 = 현재 시퀀스 시작점
#   zone       : Premium(고점,추격금지) / Discount(저점,흡수자리) / Equil
#   swing_hi/lo: 직전 고가/저가 (지지·저항)
#   order_blocks: 매물대 (수요OB=지지 / 공급OB=저항)
#
# 검증(1시간봉): OSMO bull CHoCH=발사시작 / GNO bear CHoCH+BOS5연속=덤핑진행(제외)
#   / HOME bear CHoCH=발사후꺾임
#
# 후행성: 1시간봉이라 최대 1h 지연. 발사 트리거론 부적합(Phase2 틱/호가).
#   시퀀스 경계 용도엔 적합(큰 흐름 전환만 보면 됨).
# ============================================================

import urllib.request, json

UA={"User-Agent":"Mozilla/5.0"}

def fetch_candles(sym, unit=60, count=300):
    url=f"https://api.bithumb.com/v1/candles/minutes/{unit}?market=KRW-{sym}&count={count}"
    try:
        d=json.loads(urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=12).read().decode())
        return list(reversed(d))  # 오래된→최근
    except: return None

def analyze(candles, sw=5):
    if not candles or len(candles)<sw*3: return None
    n=len(candles)
    H=[c['high_price'] for c in candles]; L=[c['low_price'] for c in candles]
    C=[c['trade_price'] for c in candles]; O=[c['opening_price'] for c in candles]
    T=[c['candle_date_time_kst'][5:16] for c in candles]

    # 스윙 고/저 (좌우 sw봉 내 최고/최저)
    sh=[]; sl=[]
    for i in range(sw, n-sw):
        if H[i]==max(H[i-sw:i+sw+1]): sh.append((i,H[i]))
        if L[i]==min(L[i-sw:i+sw+1]): sl.append((i,L[i]))

    # BOS/CHoCH (종가가 직전 스윙고 돌파=bull / 스윙저 이탈=bear)
    events=[]; trend=0; last_sh=None; last_sl=None; shi=sli=0
    for i in range(sw,n):
        while shi<len(sh) and sh[shi][0]<i: last_sh=sh[shi][1]; shi+=1
        while sli<len(sl) and sl[sli][0]<i: last_sl=sl[sli][1]; sli+=1
        if last_sh and C[i]>last_sh:
            events.append((i,"bull","CHoCH" if trend==-1 else "BOS")); trend=1; last_sh=None
        elif last_sl and C[i]<last_sl:
            events.append((i,"bear","CHoCH" if trend==1 else "BOS")); trend=-1; last_sl=None

    # 마지막 CHoCH = 현재 사이클 시작
    last_choch=None
    for i,b,t in reversed(events):
        if t=="CHoCH": last_choch=(T[i],b,i); break

    # 현재 사이클 판정: 마지막 CHoCH 방향 + 이후 BOS 흐름
    # bull = 상승/흡수 가능 / bear = 덤핑 진행 (흡수 후보 아님)
    cycle = "bull" if trend==1 else ("bear" if trend==-1 else "neutral")
    # 마지막 CHoCH 이후 같은방향 BOS 연속 = 추세 강함
    bos_after=0
    if last_choch:
        ci=last_choch[2]
        for i,b,t in events:
            if i>ci and t=="BOS" and b==last_choch[1]: bos_after+=1

    # Premium/Discount (최근 스윙 고저 사이 위치)
    if sh and sl:
        top=max(x[1] for x in sh[-3:]); bot=min(x[1] for x in sl[-3:]); cur=C[-1]
        pos=(cur-bot)/(top-bot)*100 if top>bot else 50
    else: pos=50; top=bot=C[-1]
    zone="Premium" if pos>62 else ("Discount" if pos<38 else "Equil")

    # Order Block (CHoCH 직전 반대캔들 = 매물대)
    obs=[]
    for i,b,t in events:
        if t!="CHoCH": continue
        for j in range(i-1,max(0,i-10),-1):
            if b=="bull" and C[j]<O[j]: obs.append((T[j],"demand",L[j],H[j])); break
            if b=="bear" and C[j]>O[j]: obs.append((T[j],"supply",L[j],H[j])); break

    return dict(
        cycle=cycle, last_choch=last_choch[:2] if last_choch else None,
        bos_after=bos_after, zone=zone, pos=pos,
        swing_hi=(T[sh[-1][0]],sh[-1][1]) if sh else None,
        swing_lo=(T[sl[-1][0]],sl[-1][1]) if sl else None,
        order_blocks=obs[-3:], cur=C[-1], top=top, bot=bot,
        events=[(e[1],e[2]) for e in events[-5:]],
    )

def smc(sym, unit=60):
    """종목 SMC 분석 (1시간봉 기본)"""
    c=fetch_candles(sym, unit)
    return analyze(c)

if __name__=="__main__":
    import sys
    syms=sys.argv[1:] or ["OSMO","HOME","GNO","ALLO"]
    for s in syms:
        r=smc(s)
        if not r: print(f"{s}: 데이터 없음"); continue
        ch=f"{r['last_choch'][1]}@{r['last_choch'][0]}" if r['last_choch'] else "-"
        print(f"{s}: 사이클={r['cycle']}(BOS후{r['bos_after']}) 마지막CHoCH={ch} {r['zone']}({r['pos']:.0f}%)")
        print(f"   직전고{r['swing_hi']} 직전저{r['swing_lo']}")
