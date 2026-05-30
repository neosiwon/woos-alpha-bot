#!/usr/bin/env python3
# woos_tick_collector.py
# 업비트 전종목 체결(trade) 틱 수집기 — 세력 매집 미시구조 포착용
# WebSocket으로 전 KRW 마켓 trade 구독. 분당 체결건수가 평소 대비 급증한
# '활성 종목'만 틱 단위로 ~/woos_logs/tick_YYYYMMDD.csv.gz 에 기록.
#
# 목적: 일봉/60초 집계에 묻히는 세력 매집 체결 흐름을, 체결 한 건 단위로 본다.
# 전 종목(스테이블/메이저 제외) 틱을 상시 저장. 활성(급증) 여부는 컬럼으로 마킹.
#
# 기록 컬럼: 시각KST, 종목, 체결가, 체결량(코인), 체결액(원), 매수매도(BID/ASK), 체결ID, 활성(1/0)
#
# 활성 판정: 종목별 직전 10분 분당 체결건수의 중앙값 대비 현재 분 체결건수가
#           ACTIVE_SURGE 배 이상이면 그 종목을 ACTIVE_HOLD_MIN 분간 '활성'으로 등록.
#           활성 여부는 틱의 마지막 컬럼(활성=1/0)으로 기록. 저장은 전 종목 상시.

import json, gzip, os, time, threading
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
import websocket   # pip install websocket-client --break-system-packages
import requests

KST = timezone(timedelta(hours=9))
LOG_DIR = os.path.expanduser("~/woos_logs")
WS_URL = "wss://api.upbit.com/websocket/v1"

ACTIVE_SURGE = 4.0       # 분당 체결건수가 평소(중앙값) 대비 N배 이상이면 활성
ACTIVE_MIN_COUNT = 20    # 그 분 최소 체결건수 (너무 적으면 노이즈)
ACTIVE_HOLD_MIN = 15     # 활성 등록 후 유지 시간(분)
BASELINE_WINDOW = 10     # 평소 기준선 = 직전 N분 분당건수 중앙값
RECONNECT_SEC = 5

STABLE = {"USDT","USDC","DAI","TUSD","BUSD"}
# 메이저(원본 신호 안 오는 시총 상위) — 거래량만 크고 매집 대상 아님, 상시저장에서 제외
MAJORS = {"BTC","ETH","XRP","SOL","DOGE","ADA","TRX","LINK","AVAX","DOT","BCH","SUI"}

# 종목별: 분(минуте)별 체결건수 누적, 평소 기준선용 deque, 활성 만료시각
_minute_counts = defaultdict(lambda: defaultdict(int))  # {sym: {minute_str: count}}
_baseline = defaultdict(lambda: deque(maxlen=BASELINE_WINDOW))  # {sym: [분당건수...]}
_active_until = {}   # {sym: datetime} 활성 만료
_last_minute = {None}
_lock = threading.Lock()
_writer_lock = threading.Lock()

def get_krw_markets():
    r = requests.get("https://api.upbit.com/v1/market/all",
                     params={"isDetails":"false"}, timeout=10)
    r.raise_for_status()
    out = []
    for m in r.json():
        mk = m["market"]
        if not mk.startswith("KRW-"): continue
        sym = mk.split("-")[1]
        if sym in STABLE: continue
        if sym in MAJORS: continue   # 메이저 제외 (신호 안 옴)
        out.append(mk)
    return out

def _tick_path():
    now = datetime.now(KST)
    return os.path.join(LOG_DIR, f"tick_{now.strftime('%Y%m%d')}.csv.gz")

def _write_header_if_new(path):
    if not os.path.exists(path):
        with gzip.open(path, "at", encoding="utf-8") as f:
            f.write("시각KST,종목,체결가,체결량,체결액,매수매도,체결ID,활성\n")

def write_ticks(rows):
    if not rows: return
    path = _tick_path()
    with _writer_lock:
        _write_header_if_new(path)
        with gzip.open(path, "at", encoding="utf-8") as f:
            for r in rows:
                f.write(",".join(str(x) for x in r) + "\n")

# 분 경계마다 활성 판정 갱신
def roll_minute(now):
    minute = now.strftime("%Y-%m-%d %H:%M")
    with _lock:
        for sym in list(_minute_counts.keys()):
            mc = _minute_counts[sym]
            # 직전(완료된) 분들 처리
            for m in list(mc.keys()):
                if m < minute:  # 완료된 분
                    cnt = mc.pop(m)
                    base = _baseline[sym]
                    med = sorted(base)[len(base)//2] if base else 0
                    # 활성 판정
                    if base and med > 0 and cnt >= ACTIVE_MIN_COUNT and cnt >= med*ACTIVE_SURGE:
                        _active_until[sym] = now + timedelta(minutes=ACTIVE_HOLD_MIN)
                        print(f"[active] {sym} 체결{cnt}건 (평소{med}, {cnt/max(1,med):.1f}배) → {ACTIVE_HOLD_MIN}분 활성", flush=True)
                    base.append(cnt)
        # 만료된 활성 제거
        for sym in list(_active_until.keys()):
            if _active_until[sym] < now:
                del _active_until[sym]

def is_active(sym, now):
    u = _active_until.get(sym)
    return u is not None and u >= now

def on_message(ws, message):
    try:
        d = json.loads(message)
    except Exception:
        return
    if d.get("type") != "trade":
        return
    code = d.get("code","")           # "KRW-BTC"
    sym = code.split("-")[1] if "-" in code else code
    now = datetime.now(KST)
    minute = now.strftime("%Y-%m-%d %H:%M")
    with _lock:
        _minute_counts[sym][minute] += 1
    # 전 종목 상시 저장 (메이저/스테이블은 구독단계에서 이미 제외됨)
    # 활성 여부는 컬럼으로 마킹 (급증 시점 지표용 — 나중에 분석)
    price = d.get("trade_price",0)
    vol = d.get("trade_volume",0)
    ts = d.get("trade_timestamp",0)
    ab = d.get("ask_bid","")       # ASK(매도체결) / BID(매수체결)
    seq = d.get("sequential_id","")
    amount = round(price*vol, 2)
    act = 1 if is_active(sym, now) else 0   # 활성(급증)구간이면 1
    tstr = datetime.fromtimestamp(ts/1000, KST).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if ts else now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    write_ticks([[tstr, sym, price, vol, amount, ab, seq, act]])

def on_error(ws, err):
    print(f"[ws] error: {err}", flush=True)

def on_close(ws, code, msg):
    print(f"[ws] closed: {code} {msg}", flush=True)

def make_on_open(markets):
    def on_open(ws):
        sub = [{"ticket":"woos-tick"},
               {"type":"trade","codes":markets,"isOnlyRealtime":True},
               {"format":"DEFAULT"}]
        ws.send(json.dumps(sub))
        print(f"[ws] subscribed {len(markets)} markets (trade)", flush=True)
    return on_open

# 분 경계 롤링 스레드
def minute_roller():
    while True:
        now = datetime.now(KST)
        # 다음 분 +1초까지 대기
        nxt = (now + timedelta(minutes=1)).replace(second=1, microsecond=0)
        time.sleep(max(1,(nxt-now).total_seconds()))
        try:
            roll_minute(datetime.now(KST))
        except Exception as e:
            print(f"[roll] error: {e}", flush=True)

def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    markets = get_krw_markets()
    print(f"[tick] start — {len(markets)} KRW markets, log {LOG_DIR}", flush=True)
    threading.Thread(target=minute_roller, daemon=True).start()
    last_refresh = time.time()
    while True:
        try:
            ws = websocket.WebSocketApp(
                WS_URL,
                on_open=make_on_open(markets),
                on_message=on_message,
                on_error=on_error,
                on_close=on_close)
            ws.run_forever(ping_interval=60, ping_timeout=10)
        except Exception as e:
            print(f"[ws] run_forever exception: {e}", flush=True)
        # 마켓 6시간마다 갱신
        if time.time() - last_refresh > 6*3600:
            try:
                markets = get_krw_markets(); last_refresh = time.time()
                print(f"[tick] markets refreshed: {len(markets)}", flush=True)
            except Exception as e:
                print(f"[tick] market refresh fail: {e}", flush=True)
        print(f"[ws] reconnecting in {RECONNECT_SEC}s...", flush=True)
        time.sleep(RECONNECT_SEC)

if __name__ == "__main__":
    main()
