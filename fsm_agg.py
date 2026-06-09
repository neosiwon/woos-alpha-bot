# fsm_agg.py — 수집기용 집계 모듈 (틱 실시간 누적 + 5분 스냅샷 저장)
# 수집기는 이걸 import해서: from fsm_agg import agg_tick, start_snapshot
#   on_message 끝에  agg_tick(sym, price, vol, amount, ab, tstr)  한 줄
#   main 시작에       start_snapshot()                            한 줄
# write_ticks(틱 저장)는 안 건드림. 집계만 별도 누적.
import os, json, time, math, threading, collections
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
LOG_DIR = os.path.join(os.path.expanduser("~"), "woos_logs")
SNAP_SEC = 300   # 5분마다 스냅샷

_lock = threading.Lock()
_AGG = collections.defaultdict(lambda: {
    "bots":  collections.defaultdict(lambda: [0, 0.0, 0.0]),   # qty -> [cnt, buy, sell]
    "abots": collections.defaultdict(lambda: [0, 0.0, 0.0]),   # amt_bucket -> [cnt, buy, sell]
    "vol5":  collections.defaultdict(float),                    # bk5 -> 거래대금
    "sum_pxqty": 0.0, "sum_qty": 0.0, "hi": 0.0,
    "first_ts": None, "last_ts": None, "last_px": 0.0, "open_px": 0.0,
})
_cur_date = [None]

def _amt_bucket(a):
    return int(math.log(a) / math.log(1.005)) if a > 0 else 0

def agg_tick(sym, price, vol, amount, ab, tstr):
    # on_message에서 매 틱 호출. 가벼운 누적만 (lock 짧게).
    try:
        with _lock:
            d = _AGG[sym]
            q = round(vol, 4)
            b = d["bots"][q]; b[0] += 1
            abk = d["abots"][_amt_bucket(amount)]; abk[0] += 1
            if ab == "BID":
                b[1] += amount; abk[1] += amount
            else:
                b[2] += amount; abk[2] += amount
            if d["first_ts"] is None:
                d["first_ts"] = tstr[:16]; d["open_px"] = price
            d["last_ts"] = tstr[:16]
            d["sum_pxqty"] += price * vol; d["sum_qty"] += vol
            if price > d["hi"]: d["hi"] = price
            d["last_px"] = price
            hh = int(tstr[11:13]); mm = int(tstr[14:16]); ss = int(tstr[17:19])
            d["vol5"][(hh*3600 + mm*60 + ss) // 300] += amount
    except Exception:
        pass

def _save():
    ds = datetime.now(KST).strftime("%Y%m%d")
    path = os.path.join(LOG_DIR, f"fsm_snap_{ds}.json")
    with _lock:
        out = {}
        for sym, d in _AGG.items():
            # 봇 후보만 저장 (파일 작게): 소수4회+ / 정수40회+ / 금액20회+
            bots = {f"{q}": v for q, v in d["bots"].items()
                    if (abs(q-round(q,2)) > 1e-6 and v[0] >= 4)
                    or (abs(q-round(q,2)) <= 1e-6 and v[0] >= 40)}
            abots = {f"{k}": v for k, v in d["abots"].items() if v[0] >= 20}
            if not bots and not abots:
                continue
            out[sym] = {
                "bots": bots, "abots": abots,
                "vol5": {f"{k}": round(v, 1) for k, v in d["vol5"].items()},
                "sum_pxqty": d["sum_pxqty"], "sum_qty": d["sum_qty"], "hi": d["hi"],
                "first_ts": d["first_ts"], "last_ts": d["last_ts"],
                "last_px": d["last_px"], "open_px": d["open_px"],
            }
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, path)

def _loop():
    while True:
        time.sleep(SNAP_SEC)
        try:
            ds = datetime.now(KST).strftime("%Y%m%d")
            if _cur_date[0] is None:
                _cur_date[0] = ds
            if ds != _cur_date[0]:        # 자정: 새 날 시퀀스 시작
                with _lock:
                    _AGG.clear()
                _cur_date[0] = ds
            _save()
        except Exception as e:
            print(f"[snap] err: {e}", flush=True)

def start_snapshot():
    threading.Thread(target=_loop, daemon=True).start()
    print("[snap] snapshot thread started (5min)", flush=True)
