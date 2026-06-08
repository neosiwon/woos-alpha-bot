#!/usr/bin/env python3
# alpha_phase1_absorb.py — Phase 1: 흡수 매집 후보군 (일별집계 json 합산)
# 데이터: daily_agg_YYYYMMDD.json 합산 (통파일 안 읽음, 빠름, 뭉개짐 0 검증)
# 3조건 AND: ①순수매집장악≥1% ②매도우위≥48% ③폭락아님(등락≥-30%&흡수율≥0.25)

import os, json, collections, urllib.request, urllib.parse
from datetime import datetime, timedelta

LOG_DIR    = os.path.expanduser("~/woos_logs")
ENV_PATH   = os.path.expanduser("~/woos-alpha-bot/.env")
STATE_PATH = os.path.join(LOG_DIR, "alpha_watchlist.json")
CACHE_PATH = os.path.join(LOG_DIR, "alpha_cache.json")

PURE_GRIP_MIN=1.0; SELL_DOM_MIN=48.0; PRICE_CRASH=-30.0; ABSORB_MIN=0.25
ACCUM_CUT_EOK=1.5  # 유통량 API 전 1차 컷: 순수매집 금액(억). 검증: JTO2.7·OSMO6.3·DAO2.8 통과
DUMP_DROP_PCT=3.0; MAX_SEQ_DAYS=14; BIDIR_MIN=3; DEC_MIN,INT_MIN=4,40
UA={"User-Agent":"Mozilla/5.0","Referer":"https://www.bithumb.com/","Accept":"application/json"}

def load_env():
    env={}
    try:
        for line in open(ENV_PATH):
            line=line.strip()
            if '=' in line and not line.startswith('#'):
                k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
    except: pass
    return env

def send_telegram(token,chat,text):
    if not token or not chat: print("[no tg]\n"+text); return
    try:
        url=f"https://api.telegram.org/bot{token}/sendMessage"
        data=urllib.parse.urlencode({'chat_id':chat,'text':text,'parse_mode':'HTML','disable_web_page_preview':'true'}).encode()
        urllib.request.urlopen(urllib.request.Request(url,data=data),timeout=10).read()
    except Exception as e: print(f"[tg] {e}")

KOR={}
def load_korean_names():
    try:
        for x in json.loads(urllib.request.urlopen("https://api.bithumb.com/v1/market/all?isDetails=false",timeout=10).read().decode()):
            mk=x.get("market","")
            if mk.startswith("KRW-"): KOR[mk[4:]]=x.get("korean_name","")
    except: pass
def kn(s): return f"{KOR.get(s)}({s})" if KOR.get(s) else s

def load_cache():
    try:
        c=json.load(open(CACHE_PATH))
        if c.get("date")==datetime.now().strftime("%Y%m%d"): return c
    except: pass
    return {"date":datetime.now().strftime("%Y%m%d"),"supply":{}}
def save_cache(c):
    try: json.dump(c,open(CACHE_PATH,'w'),ensure_ascii=False)
    except Exception as e: print(f"[cache] {e}")

def internal_supply(sym,cache):
    if sym in cache["supply"]: return cache["supply"][sym]
    if "_ctmap" not in cache:
        ctmap={}
        try:
            d=json.loads(urllib.request.urlopen(urllib.request.Request(
                "https://gw.bithumb.com/exchange/v1/comn/intro?coinType=C0101&crncCd=C0100",headers=UA),timeout=10).read().decode())
            for c in d.get("data",{}).get("coinList",[]): ctmap[c.get("coinSymbol")]=c.get("coinType")
        except: pass
        cache["_ctmap"]=ctmap
    ct=cache["_ctmap"].get(sym); v=None
    if ct:
        try:
            d=json.loads(urllib.request.urlopen(urllib.request.Request(
                f"https://gw.bithumb.com/exchange/v1/trade/accumulation/deposit/{ct}-C0100",headers=UA),timeout=2.5).read().decode())
            v=float(d["data"]["accumulationDepositAmt"])
        except: pass
    cache["supply"][sym]=v
    return v

def is_bot_qty(v,cnt):
    frac=abs(v-round(v,2))
    return (frac>1e-6 and cnt>=DEC_MIN) or (frac<=1e-6 and cnt>=INT_MIN)

def load_and_merge():
    dates=[(datetime.now()-timedelta(days=i)).strftime("%Y%m%d") for i in range(MAX_SEQ_DAYS-1,-1,-1)]
    files=[(d,os.path.join(LOG_DIR,f"daily_agg_{d}.json")) for d in dates]
    files=[(d,p) for d,p in files if os.path.exists(p)]
    if not files: return None,None
    merged=collections.defaultdict(lambda: collections.defaultdict(lambda:[0,0,0.0,0.0]))
    cum=collections.defaultdict(lambda:[0.0,0.0,None,None])
    for d,p in files:
        try: day=json.load(open(p))
        except: continue
        for sym,s in day.get("symbols",{}).items():
            for vk,arr in s.get("bots",{}).items():
                m=merged[sym][float(vk)]; m[0]+=arr[0]; m[1]+=arr[1]; m[2]+=arr[2]; m[3]+=arr[3]
            c=cum[sym]; c[0]+=s.get("pv",0); c[1]+=s.get("vv",0)
            if c[2] is None: c[2]=s.get("first_px")
            c[3]=s.get("last_px")
    seq=f"{files[0][0][4:]}~{files[-1][0][4:]}"
    return (merged,cum), seq

def compute_local(sym,qtys,c):
    # API 없이 계산 (매도우위/가격/순수매집물량) — 유통량은 나중에
    pure_buy=0.0
    for v,d in qtys.items():
        if not is_bot_qty(v,d[0]+d[1]): continue
        hb=d[0]>=BIDIR_MIN; ha=d[1]>=BIDIR_MIN
        if hb and ha: pure_buy+=max(0.0,d[2]-d[3])
        elif hb and not ha: pure_buy+=d[2]
    pv,vv,firstpx,lastpx=c
    if not lastpx or vv<=0: return None
    tot_bid=sum(d[2] for d in qtys.values()); tot_ask=sum(d[3] for d in qtys.values())
    if (tot_bid+tot_ask)<=0: return None
    sell_dom=tot_ask/(tot_bid+tot_ask)*100
    net_sell_ratio=(tot_ask-tot_bid)/(tot_bid+tot_ask)*100
    chg=(lastpx/firstpx-1)*100 if firstpx else 0
    vwap=pv/vv; vwap_gap=(lastpx/vwap-1)*100
    absorb_rate=net_sell_ratio/abs(chg) if chg<0 and net_sell_ratio>0 else (99 if net_sell_ratio>0 else 0)
    return dict(pure_buy=pure_buy, pure_amt=pure_buy*lastpx, sell_dom=sell_dom, chg=chg, vwap=vwap,
                vwap_gap=vwap_gap, absorb_rate=absorb_rate, px=lastpx)

def load_state():
    try: return json.load(open(STATE_PATH))
    except: return {"watch":{}}
def save_state(st):
    try: json.dump(st,open(STATE_PATH,'w'),ensure_ascii=False)
    except Exception as e: print(f"[state] {e}")

def main():
    import time as _T, sys as _S
    def _log(msg):
        print(f"[{_T.time()%1000:.1f}] {msg}", flush=True)
    _log("main 시작")
    env=load_env(); token=env.get('TELEGRAM_BOT_TOKEN'); chat=env.get('TELEGRAM_CHAT_ID_MONITOR')
    _log("env 로드 완료")
    load_korean_names()
    _log("한국어이름 로드 완료")
    cache=load_cache(); state=load_state(); watch=state.get("watch",{})
    now_hm=datetime.now().strftime("%H:%M")
    _log("cache/state 로드 완료, merge 시작")
    data,seq=load_and_merge()
    _log("merge 완료")
    if data is None:
        print("[info] daily_agg json 없음 — alpha_daily_agg.py로 먼저 생성"); return
    merged,cum=data
    # 1단계: API 없이 거름
    _log(f"1단계 시작 (종목 {len(merged)})")
    locals_={}
    for sym in merged:
        m=compute_local(sym,merged[sym],cum[sym])
        if not m: continue
        # API 필요없는 조건 먼저
        if m["sell_dom"]<SELL_DOM_MIN: continue
        if m["chg"]<PRICE_CRASH or m["absorb_rate"]<ABSORB_MIN: continue
        if m["pure_amt"] < ACCUM_CUT_EOK*1e8: continue   # 순수매집 금액 1.5억+ (유통량 API 전 컷)
        locals_[sym]=m
    print(f"[1단계] API전 후보 {len(locals_)}종 (전체 {len(merged)}종에서 압축)")
    # 2단계: 통과한 소수만 유통량 API → 장악률
    new_signals=[]; cur={}
    import time as _t
    for i,(sym,m) in enumerate(locals_.items(),1):
        _t0=_t.time()
        camt=internal_supply(sym,cache)
        print(f"  [{i}/{len(locals_)}] {sym} 유통량 {('OK' if camt else 'None')} ({_t.time()-_t0:.1f}s)")
        if not camt: continue
        m["pure_grip"]=m["pure_buy"]/camt*100
        cur[sym]=m
        if m["pure_grip"]<PURE_GRIP_MIN: continue
        if sym in watch: continue
        watch[sym]={"since":now_hm,"seq":seq,"pure_grip":round(m["pure_grip"],1),
                    "vwap":round(m["vwap"],4),"entry_px":m["px"]}
        new_signals.append((sym,m))
    # 관찰 해제: 관찰중 종목만 유통량 확인 (이미 cur에 있거나 추가 호출)
    removed=[]
    for sym in list(watch.keys()):
        m=cur.get(sym)
        if m is None:
            ml=compute_local(sym,merged.get(sym,{}),cum.get(sym,[0,0,None,None]))
            if ml is None: continue
            camt=internal_supply(sym,cache)
            if not camt: continue
            ml["pure_grip"]=ml["pure_buy"]/camt*100; m=ml
        if m["vwap_gap"]<=-DUMP_DROP_PCT or m["pure_grip"]<PURE_GRIP_MIN:
            removed.append((sym,watch[sym].get("pure_grip",0),round(m["pure_grip"],1))); del watch[sym]
    if new_signals:
        lines=[f"🔍 <b>[흡수 매집 포착]</b> 빗썸  🕐{now_hm}", f"시퀀스 {seq} 누적 · 3조건 충족\n"]
        for sym,m in sorted(new_signals,key=lambda x:-x[1]["pure_grip"]):
            lines.append(f"▶️ <b>{kn(sym)}</b> | 순수매집 {m['pure_grip']:.1f}% (자전제외)")
            lines.append(f"   매도우위 {m['sell_dom']:.0f}% · 누적등락 {m['chg']:+.0f}% · 흡수율 {m['absorb_rate']:.2f} · {m['px']:g}원")
            lines.append(f"   VWAP(세력평단) {m['vwap']:.4g} (현재 {m['vwap_gap']:+.1f}%)")
        lines.append("\n→ Phase2(매수벽/변곡점) 발사 트리거 대기")
        send_telegram(token,chat,"\n".join(lines)); print("\n".join(lines),"\n")
    else:
        print(f"[info] {now_hm} 신규 흡수매집 없음 (관찰중 {len(watch)}종)")
    if removed: print("[info] 관찰 해제: "+", ".join(f"{s}({g0}→{g1}%)" for s,g0,g1 in removed))
    state["watch"]=watch; save_state(state); save_cache(cache)
    print(f"[완료] 시퀀스 {seq} · 관찰리스트 {len(watch)}종 · 신규 {len(new_signals)}종")

if __name__=="__main__":
    main()
