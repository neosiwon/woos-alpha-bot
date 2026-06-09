#!/usr/bin/env python3
"""
alpha_scanner.py — 세력 발사 선행 스캐너 (A형 선점 + B형 추종)

검증 (2026-06-09, 원본채널 실시간신호 + 전종목 대조):
  - B형 (추종): 거래량 CUSUM (자기 시퀀스 baseline) → 전체시장 MAD 이상치
    실증: LWA+28%·SLX+56%·MOVE+60%·GWEI+19%·CPOOL+19%·LAYER+24% 다 상위로 잡음
  - A형 (선점): 봇 순매집 (누적 장악) — PEPE/SHIB/MOVE 등
  - 매도우위는 거르지 않음 (흡수 클라이맥스 = 발사 직전 신호, 메모리 #17/20)
  - 신규상장 (SLX/SPURS): baseline 불안정 → 추후 패치

구조: scan_all — 통파일 1회 읽어 전 종목 동시 누적 (122MB 반복읽기 금지)
사용:
  python3 alpha_scanner.py            # 오늘 1회 스캔 → stdout + ~/woos_logs/alpha_signals.json
  python3 alpha_scanner.py 20260609   # 특정일 스캔
  python3 alpha_scanner.py --loop 300 # 300초마다 반복 스캔 (실시간)
"""
import gzip, statistics, math, json, os, sys, time
from collections import defaultdict
from datetime import datetime

# ===== Config (English comments) =====
TICK_DIR        = os.path.expanduser("~/woos_logs")
OUT_JSON        = os.path.join(TICK_DIR, "alpha_signals.json")
BUCKET_SEC      = 300          # 5-min bucket (verified resolution)
CUSUM_K         = 1.0          # CUSUM slack (z - k)
CUSUM_BASE_MIN  = 60           # baseline window after trade-start (min)
BOT_NET_MIN     = 1_000_000    # A-type: bot net accumulation threshold (KRW)
CUSUM_MAD_MIN   = 3.5          # B-type: market-wide MAD outlier cut
VOL_CUT_KRW     = 300_000_000  # daily volume cut (exclude dead coins)
DUMP_CUT_PCT    = -5.0         # exclude distribution: price down > 5% from open
                               # (verified: launch >=-3%, dump <=-7%, clean gap at -5%)
TOP_N           = 25           # report top-N by cusum MAD
# EXCLUDE: majors (no signals) + meme coins (bot-like volume but no real accumulation)
#   PEPE/SHIB/BONK: high bot_net but price flat (+0%) = false A-type, verified 2026-06-09
EXCLUDE = set("BTC ETH XRP SOL DOGE ADA TRX LINK AVAX DOT BCH USDT USDC DAI TUSD BUSD PEPE SHIB BONK".split())

def is_bot(qty, count):
    if qty <= 0.001:
        return False
    is_round = abs(qty - round(qty)) < 1e-6 or abs(qty*2 - round(qty*2)) < 1e-6
    return count >= 50 if is_round else count >= 5

def cusum_peak(series, base_vals):
    base = [v for v in base_vals if v > 0]
    if len(base) < 3:
        return 0.0
    med = statistics.median(base)
    mad = statistics.median([abs(v - med) for v in base]) or 0.1
    c = peak = 0.0
    for v in series:
        z = (v - med) / (1.4826 * mad)
        c = max(0.0, c + (z - CUSUM_K))
        peak = max(peak, c)
    return peak

def mad_scores(values):
    vals = list(values)
    if len(vals) < 3:
        return [0.0]*len(vals)
    med = statistics.median(vals)
    mad = statistics.median([abs(v - med) for v in vals]) or 0.1
    return [(v - med) / (1.4826 * mad) for v in vals]

def scan_all(date_str):
    path = os.path.join(TICK_DIR, f"tick_bithumb_{date_str}.csv.gz")
    if not os.path.exists(path):
        return None
    sym = defaultdict(lambda: {
        "vol5": defaultdict(float),
        "bots": defaultdict(lambda: [0, 0, 0.0, 0.0]),
        "start": None, "last_px": None, "open_px": None,
    })
    with gzip.open(path, "rt") as f:
        next(f, None)
        for line in f:
            p = line.split(",")
            if len(p) < 6:
                continue
            s = p[1]
            if s in EXCLUDE:
                continue
            try:
                t = p[0][11:19]
                sec = int(t[:2])*3600 + int(t[3:5])*60 + int(t[6:8])
                bk = sec // BUCKET_SEC
                if bk >= 288:
                    continue
                amt = float(p[4]); qv = float(p[3]); px = float(p[2])
                d = sym[s]
                d["vol5"][bk] += amt
                d["last_px"] = px
                if d["start"] is None:
                    d["start"] = bk
                    d["open_px"] = px
                b = d["bots"][round(qv, 4)]
                if p[5] == "BID":
                    b[0] += 1; b[2] += qv
                else:
                    b[1] += 1; b[3] += qv
            except Exception:
                continue
    return sym

def compute(d):
    vols = [d["vol5"].get(i, 0) for i in range(288)]
    daily = sum(vols)
    start = d["start"] or 0
    be = min(start + CUSUM_BASE_MIN // 5, 288)
    base = [vols[i] for i in range(start, be)]
    series = [vols[i] for i in range(be, 288)]
    cpeak = cusum_peak(series, base)
    net = 0.0
    for q, b in d["bots"].items():
        if not is_bot(q, b[0] + b[1]):
            continue
        if b[0] >= 2 and b[1] >= 2:
            net += max(0.0, b[2] - b[3])
        elif b[0] >= 2:
            net += b[2]
    return dict(bot_net=net, cusum_peak=cpeak, daily_vol=daily,
                last_px=d["last_px"], open_px=d["open_px"],
                chg_pct=((d["last_px"]/d["open_px"]-1)*100 if d["open_px"] else 0.0))

def run(date_str=None):
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    sym = scan_all(date_str)
    if sym is None:
        return None
    rows = []
    for s, d in sym.items():
        m = compute(d)
        if m["daily_vol"] < VOL_CUT_KRW:
            continue
        if m["chg_pct"] <= DUMP_CUT_PCT:   # exclude distribution (price dumped)
            continue
        rows.append((s, m))
    if not rows:
        return []
    zs = mad_scores([m["cusum_peak"] for _, m in rows])
    for i, (s, m) in enumerate(rows):
        m["cusum_mad"] = zs[i]
    out = []
    for s, m in rows:
        A = m["bot_net"] >= BOT_NET_MIN
        B = m["cusum_mad"] >= CUSUM_MAD_MIN
        if A or B:
            out.append((s, "A+B" if A and B else ("A" if A else "B"), m))
    out.sort(key=lambda x: -x[2]["cusum_mad"])
    return out

def report(results, date_str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n=== alpha_scanner {date_str} @ {ts} : {len(results)} signals ===")
    print(f"{'SYM':9s}{'TAG':5s}{'botNet(M)':>10s}{'cusumMAD':>9s}{'vol(억)':>9s}{'chg%':>7s}")
    for s, tag, m in results[:TOP_N]:
        print(f"{s:9s}{tag:5s}{m['bot_net']/1e6:>10.0f}{m['cusum_mad']:>9.1f}{m['daily_vol']/1e8:>9.0f}{m['chg_pct']:>+7.0f}")
    # save json
    try:
        payload = {"ts": ts, "date": date_str,
                   "signals": [{"sym": s, "tag": t, "bot_net": m["bot_net"],
                                "cusum_mad": round(m["cusum_mad"],2),
                                "vol": m["daily_vol"], "px": m["last_px"],
                                "chg": round(m["chg_pct"],1)}
                               for s, t, m in results[:TOP_N]]}
        with open(OUT_JSON, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"[warn] json save failed: {e}")

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--loop":
        interval = int(args[1]) if len(args) > 1 else 300
        print(f"[alpha] loop mode every {interval}s")
        while True:
            ds = datetime.now().strftime("%Y%m%d")
            r = run(ds)
            if r is None:
                print(f"[alpha] no tick file for {ds}")
            else:
                report(r, ds)
            time.sleep(interval)
    else:
        ds = args[0] if args else None
        r = run(ds)
        ds2 = ds or datetime.now().strftime("%Y%m%d")
        if r is None:
            print(f"[alpha] no tick file for {ds2}")
        else:
            report(r, ds2)
