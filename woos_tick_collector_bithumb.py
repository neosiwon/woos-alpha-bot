#!/usr/bin/env python3
# woos_tick_collector_bithumb.py — 빗썸 '단독 상장' 전종목 틱 상시저장
# 업비트 수집기와 동일 구조. 빗썸 WS 필드명도 업비트와 동일(trade_price/volume/ask_bid/sequential_id).
# 단독 = 빗썸 KRW 중 업비트 KRW에 없는 종목만 (업비트는 기존 수집기가 담당).
#
# 저장: ~/woos_logs/tick_bithumb_YYYYMMDD.csv.gz (업비트와 별도 파일)
# 컬럼: 시각KST,종목,체결가,체결량,체결액,매수매도,체결ID,활성(1/0)

import json, gzip, os, time, threading, collections
from datetime import datetime, timedelta, timezone
import websocket

KST = timezone(timedelta(hours=9))
LOG_DIR = os.path.expanduser("~/woos_logs")
os.makedirs(LOG_DIR, exist_ok=True)

STABLE = {"USDT","USDC","DAI","TUSD","BUSD"}
# 빗썸 단독이라도 메이저(신호 안 옴)는 제외 — 업비트 기준과 동일 + 빗썸 메이저(BNB 등)
MAJORS = {"BTC","ETH","XRP","SOL","DOGE","ADA","TRX","LINK","AVAX","DOT","BCH","SUI","BNB"}

ACTIVE_SURGE=4.0; ACTIVE_MIN_COUNT=20; ACTIVE_HOLD_MIN=15
REFRESH_SEC=3600

_min_count=collections.defaultdict(lambda: collections.defaultdict(int))
_active_until={}
_writer_lock=threading.Lock()
_last_minute_check=time.time()

def upbit_krw():
    import urllib.request
    with urllib.request.urlopen("https://api.upbit.com/v1/market/all?isDetails=false",timeout=10) as r:
        return set(x["market"][4:] for x in json.loads(r.read().decode()) if x.get("market","").startswith("KRW-"))

def bithumb_krw():
    import urllib.request
    with urllib.request.urlopen("https://api.bithumb.com/v1/market/all?isDetails=false",timeout=10) as r:
        return [x["market"] for x in json.loads(r.read().decode()) if x.get("market","").startswith("KRW-")]

def get_markets():
    up=upbit_krw()
    out=[]
    for mk in bithumb_krw():
        sym=mk[4:]
        if sym in STABLE or sym in MAJORS: continue
        if sym in up: continue   # 업비트에도 있으면 제외 (단독만)
        out.append(mk)
    return out

def _logpath():
    return os.path.join(LOG_DIR, f"tick_bithumb_{datetime.now(KST).strftime('%Y%m%d')}.csv.gz")

def _write_header_if_new(path):
    if not os.path.exists(path):
        with gzip.open(path,'wt') as f:
            f.write("시각KST,종목,체결가,체결량,체결액,매수매도,체결ID,활성\n")

def write_ticks(rows):
    path=_logpath()
    with _writer_lock:
        _write_header_if_new(path)
        with gzip.open(path,'at') as f:
            for r in rows:
                f.write(",".join(str(x) for x in r)+"\n")

def mark_active(sym, now):
    global _last_minute_check
    mn=now.strftime("%Y%m%d%H%M")
    _min_count[sym][mn]+=1
    if time.time()-_last_minute_check>=60:
        _last_minute_check=time.time()
        for s in list(_min_count.keys()):
            mins=_min_count[s]
            if len(mins)>=3:
                counts=sorted(mins.values())
                med=counts[len(counts)//2]
                cnt=mins.get(mn,0)
                if med>0 and cnt>=ACTIVE_MIN_COUNT and cnt>=med*ACTIVE_SURGE:
                    _active_until[s]=now+timedelta(minutes=ACTIVE_HOLD_MIN)
            if len(mins)>10:
                for k in sorted(mins)[:-10]: del mins[k]
        for s in list(_active_until.keys()):
            if _active_until[s]<now: del _active_until[s]

def is_active(sym, now):
    u=_active_until.get(sym)
    return bool(u and u>=now)

def on_message(ws, msg):
    try:
        d=json.loads(msg)
        if d.get("type")!="trade": return
        sym=d.get("code","")[4:]
        now=datetime.now(KST)
        mark_active(sym, now)
        price=d.get("trade_price",0); vol=d.get("trade_volume",0)
        ts=d.get("trade_timestamp",0); ab=d.get("ask_bid","")
        seq=d.get("sequential_id","")
        amount=round(price*vol,2)
        act=1 if is_active(sym,now) else 0
        tstr=datetime.fromtimestamp(ts/1000,KST).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if ts else now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        write_ticks([[tstr,sym,price,vol,amount,ab,seq,act]])
    except: pass

def make_on_open(markets):
    def on_open(ws):
        req=[{"ticket":"woos-bithumb"},{"type":"trade","codes":markets,"isOnlyRealtime":True},{"format":"DEFAULT"}]
        ws.send(json.dumps(req))
        print(f"[ws] subscribed {len(markets)} bithumb-only markets (trade)", flush=True)
    return on_open

def main():
    markets=get_markets()
    print(f"[tick-bithumb] start — {len(markets)} 빗썸단독 markets, log {LOG_DIR}", flush=True)
    last_refresh=time.time()
    while True:
        try:
            ws=websocket.WebSocketApp("wss://ws-api.bithumb.com/websocket/v1",
                on_open=make_on_open(markets), on_message=on_message)
            ws.run_forever(ping_interval=60, ping_timeout=10)
        except Exception as e:
            print(f"[ws] error: {e}, 재연결 5초후", flush=True)
        time.sleep(5)
        if time.time()-last_refresh>REFRESH_SEC:
            markets=get_markets(); last_refresh=time.time()
            print(f"[tick-bithumb] markets refreshed: {len(markets)}", flush=True)

if __name__=="__main__":
    main()
