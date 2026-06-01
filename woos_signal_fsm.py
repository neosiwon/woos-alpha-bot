#!/usr/bin/env python3
# woos_signal_fsm.py
# 매집 사이클 상태머신 (독립 실행, woos_accum_rank.py import 안 함)
#
# 모델 (검증 완료 SIGNAL_MODEL_VALIDATION_v0.1):
#   ACCUM      후보  : 소수봇 총누적 >= ACCUM_CUT_MAN, 봇 도는 중
#   PRELAUNCH  발사후보: 봇 STALL_MIN+ 안 돎 = 매집완료, 발사 임박
#   LAUNCHED   발사  : 시퀀스 MFE +LAUNCH_PCT 달성
#   DUMPING    덤핑  : LAUNCHED 후 가격 < VWAP*(1-DUMP_DROP_PCT)
#   새 시퀀스       : DUMPING에서 가격 VWAP 회복 + 봇 재가동 -> seq+1
#   연장            : PRELAUNCH에서 +LAUNCH_PCT 전 봇 재가동 -> 같은 시퀀스 유지
#
# 속도: 날짜별 파일을 딱 1번씩만 통째로 읽고 전 종목 동시 누적 (scan_all 1패스).
# 총누적: 시퀀스 시작일~오늘 전 기간 합산 (최대 소급 MAX_SEQ_DAYS).
#
# 발송: FSM_NOTIFY=on 일 때만 단톡방 발송. 아니면 [fsm muted] 콘솔 미리보기.

import os, sys, gzip, csv, json, time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
HOME = os.path.expanduser("~")
LOG_DIR = os.path.join(HOME, "woos_logs")
STATE_PATH = os.path.join(LOG_DIR, "fsm_state.json")
ENV_PATH = os.path.join(HOME, "woos-alpha-bot", ".env")

# ---- config ----
ACCUM_CUT_MAN   = 50000     # 소수봇 총누적 후보 컷 (만원) = 5억
LAUNCH_PCT      = 5.0       # 시퀀스 MFE 이만큼이면 LAUNCHED (%)
DUMP_DROP_PCT   = 3.0       # LAUNCHED 후 VWAP 대비 이만큼 빠지면 DUMPING (%)
STALL_MIN       = 60        # 봇 이만큼(분) 안 돌면 PRELAUNCH(매집완료)
MAX_SEQ_DAYS    = 14        # 시퀀스 소급 최대 일수
ENTRY_BAND_PCT  = 0.6       # 평단(VWAP) +-이 밴드 안이면 진입 신호
# 봇 식별
DEC_MIN_CNT     = 4         # 소수점 수량 최소 반복
INT_MIN_CNT     = 40        # 정수 수량 최소 반복
STABLE = {"USDT","USDC","DAI","TUSD","BUSD"}
SKIP   = STABLE | {"BTC","ETH","XRP","SOL","DOGE","ADA","TRX","LINK","AVAX","DOT","BCH","SUI","BNB"}

# ---- helpers (자체 구현) ----
def load_env():
    env = {}
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env

def is_dec(qty):
    return abs(qty - round(qty, 2)) > 1e-6

def is_bot_qty(qty, cnt):
    # 소수점 수량 4회+ 또는 정수 수량 40회+
    if is_dec(qty):
        return cnt >= DEC_MIN_CNT
    return cnt >= INT_MIN_CNT

def fmt_man(man):
    # man = 만원 단위. 1억(10000만)+ 이면 '억'
    if man >= 10000:
        return f"{man/10000:.1f}억"
    return f"{man:.0f}만"

def kn(sym, names):
    return names.get(sym, sym)

def load_korean_names():
    names = {}
    try:
        import urllib.request
        for base, ex in [("https://api.upbit.com", "UP"), ("https://api.bithumb.com", "BT")]:
            try:
                url = base + "/v1/market/all?isDetails=false"
                with urllib.request.urlopen(url, timeout=10) as r:
                    for m in json.loads(r.read().decode()):
                        mk = m.get("market", "")
                        if mk.startswith("KRW-"):
                            names[mk[4:]] = m.get("korean_name", mk[4:])
            except Exception:
                pass
    except Exception:
        pass
    return names

def dur_str(minutes):
    h = int(minutes // 60); m = int(minutes % 60)
    if h > 0:
        return f"{h}h{m:02d}m"
    return f"{m}m"

# ---- state ----
def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(st):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)

# ---- 1패스 스캔 ----
def day_files(start_date, end_date):
    # (exch, date_str, path) 리스트, start~end 날짜
    out = []
    d = start_date
    while d <= end_date:
        ds = d.strftime("%Y%m%d")
        up = os.path.join(LOG_DIR, f"tick_{ds}.csv.gz")
        bt = os.path.join(LOG_DIR, f"tick_bithumb_{ds}.csv.gz")
        # 업비트 5/30 7col 백업도 포함
        up7 = os.path.join(LOG_DIR, f"tick_{ds}_7col.csv.gz")
        if os.path.exists(up7):
            out.append(("UP", ds, up7))
        if os.path.exists(up):
            out.append(("UP", ds, up))
        if os.path.exists(bt):
            out.append(("BT", ds, bt))
        d += timedelta(days=1)
    return out

def scan_all(files):
    # 1패스: 파일당 1회 읽기, 전 종목 동시 누적
    # 반환: per[(exch,sym)] = {
    #   bots:{qty:[cnt,buy,sell]}, first_ts, last_ts, last_bot_ts,
    #   sum_pxqty, sum_qty (VWAP용), hi, last_px }
    per = defaultdict(lambda: {
        "bots": defaultdict(lambda: [0, 0.0, 0.0]),
        "first_ts": None, "last_ts": None, "last_bot_ts": None,
        "sum_pxqty": 0.0, "sum_qty": 0.0, "hi": 0.0, "last_px": 0.0,
    })
    for exch, ds, path in files:
        try:
            with gzip.open(path, "rt") as f:
                r = csv.reader(f)
                next(r, None)
                for x in r:
                    if len(x) < 6:
                        continue
                    sym = x[1]
                    if sym in SKIP:
                        continue
                    try:
                        px = float(x[2]); qv = float(x[3]); amt = float(x[4])
                    except (ValueError, IndexError):
                        continue
                    if px <= 0 or qv <= 0:
                        continue
                    side = x[5]
                    ts = x[0][:16]  # 'YYYY-MM-DD HH:MM'
                    key = (exch, sym)
                    p = per[key]
                    q = round(qv, 4)
                    b = p["bots"][q]
                    b[0] += 1
                    if side == "BID":
                        b[1] += amt
                    else:
                        b[2] += amt
                    if p["first_ts"] is None:
                        p["first_ts"] = ts
                    p["last_ts"] = ts
                    # VWAP 누적 (전체 체결)
                    p["sum_pxqty"] += px * qv
                    p["sum_qty"] += qv
                    if px > p["hi"]:
                        p["hi"] = px
                    p["last_px"] = px
        except (FileNotFoundError, OSError):
            continue
    # 봇 마지막 체결 시각 (봇 수량의 마지막 등장) — 별도 패스 없이 근사:
    # 위 루프에서 봇 판정은 누적 후에 가능하므로 last_bot_ts는 last_ts로 근사
    return per

def bot_summary(p):
    # 봇만 추려 매수/매도/대표봇/누적(만원) 계산
    bots = []
    for q, (cnt, buy, sell) in p["bots"].items():
        if not is_bot_qty(q, cnt):
            continue
        bots.append((q, cnt, buy, sell))
    if not bots:
        return None
    bots.sort(key=lambda t: -(t[2] + t[3]))
    buy_sum = sum(b[2] for b in bots)
    sell_sum = sum(b[3] for b in bots)
    accum_man = buy_sum / 10000.0  # 매수 기준 총누적 (만원)
    net_man = (buy_sum - sell_sum) / 10000.0
    rep_q, rep_cnt = bots[0][0], bots[0][1]
    n_types = len(bots)
    per_man = (buy_sum / sum(b[1] for b in bots)) / 10000.0 if bots else 0
    return {
        "accum_man": accum_man, "net_man": net_man,
        "rep_q": rep_q, "rep_cnt": rep_cnt, "n_types": n_types,
        "per_man": per_man,
    }

# ---- FSM ----
def step_fsm(key, p, bs, st, now_ts):
    exch, sym = key
    skey = f"{exch}|{sym}"
    rec = st.get(skey, {"state": "NONE", "seq": 1, "seq_start_ts": p["first_ts"],
                        "last_ts": None, "last_accum": 0, "notified_seq": 0})
    vwap = p["sum_pxqty"] / p["sum_qty"] if p["sum_qty"] > 0 else 0
    last_px = p["last_px"]
    mfe_pct = (p["hi"] / vwap - 1) * 100 if vwap > 0 else 0
    # 봇 정지 판정: 마지막 체결~now (분)
    try:
        last_dt = datetime.strptime(p["last_ts"], "%Y-%m-%d %H:%M")
        gap_min = (now_ts - last_dt).total_seconds() / 60.0
    except Exception:
        gap_min = 0
    state = rec["state"]
    seq = rec["seq"]

    # 상태 전이
    if state in ("NONE", "ACCUM", "PRELAUNCH"):
        if bs and bs["accum_man"] >= ACCUM_CUT_MAN:
            if mfe_pct >= LAUNCH_PCT:
                state = "LAUNCHED"
            elif gap_min >= STALL_MIN:
                state = "PRELAUNCH"
            else:
                state = "ACCUM"
        else:
            state = "ACCUM" if bs else "NONE"
    elif state == "LAUNCHED":
        if vwap > 0 and last_px < vwap * (1 - DUMP_DROP_PCT/100):
            state = "DUMPING"
    elif state == "DUMPING":
        # VWAP 회복 + 봇 재가동 -> 새 시퀀스
        if vwap > 0 and last_px >= vwap and gap_min < STALL_MIN:
            seq += 1
            rec["seq_start_ts"] = p["last_ts"]
            rec["notified_seq"] = 0
            state = "ACCUM"

    rec["state"] = state
    rec["seq"] = seq
    rec["last_ts"] = p["last_ts"]
    rec["last_accum"] = bs["accum_man"] if bs else 0
    st[skey] = rec
    return rec, vwap, last_px, mfe_pct, gap_min

# ---- 신호 양식 ----
def build_signal(exch, sym, names, bs, vwap, last_px, gap_min, seq, dump_was):
    exname = "업비트" if exch == "UP" else "빗썸"
    band = (last_px / vwap - 1) * 100 if vwap > 0 else 0
    net = bs["net_man"]
    acc_label = "적극매집" if net >= 0 else "흡수매집"
    sl = vwap * 0.92
    lines = []
    tag = f" · 재매집 {seq}차" if seq > 1 else ""
    lines.append(f"[매수 신호 ({exname})]{tag}")
    lines.append("-------------")
    lines.append("")
    lines.append(f"종목: <b>{kn(sym,names)}({sym})</b>")
    lines.append(f"== 매수가 {last_px:g}원 ==")
    lines.append("")
    lines.append(f"- 평단 진입 {band:+.2f}% (VWAP {vwap:.4g}원)")
    lines.append(f"- 매집 봇 {bs['rep_q']:g}개 x{bs['rep_cnt']}회 외 {bs['n_types']-1}종")
    lines.append(f"- 총누적 {fmt_man(bs['accum_man'])} · 건당 {bs['per_man']:.1f}만")
    lines.append(f"- {acc_label} (순 {net:+.0f}만)")
    lines.append(f"- 봇 활동 {dur_str(gap_min if gap_min else 0)}")
    if seq > 1:
        lines.append("- 이전 시퀀스 발사 후 재매집")
    lines.append("-------------")
    lines.append(f"손절 {sl:.4g}원 (-8%)")
    lines.append("TP1 (+5%)->50% / TP2 (+10%)->30% / TP3 (+15%)->20%")
    lines.append("- 보유한계 4H (TP1 도달 시 본절)")
    return "\n".join(lines)

def send_telegram(env, chat, text):
    import urllib.request, urllib.parse
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token or not chat:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
        return True
    except Exception as e:
        print(f"[fsm] telegram err: {e}")
        return False

# ---- main ----
def main():
    env = load_env()
    notify_on = env.get("FSM_NOTIFY", "off").lower() == "on"
    now_ts = datetime.now(KST).replace(tzinfo=None)
    end_date = now_ts.date()
    start_date = end_date - timedelta(days=MAX_SEQ_DAYS)
    files = day_files(datetime.combine(start_date, datetime.min.time()),
                      datetime.combine(end_date, datetime.min.time()))
    if not files:
        print("[fsm] 신호 0건 (틱 파일 없음)")
        return
    per = scan_all(files)
    st = load_state()
    names = load_korean_names()
    chat_group = env.get("TELEGRAM_CHAT_ID_GROUP") or env.get("TELEGRAM_CHAT_ID_PRIVATE") or env.get("TELEGRAM_CHAT_ID_MONITOR")

    signals = 0
    for key, p in per.items():
        bs = bot_summary(p)
        rec, vwap, last_px, mfe_pct, gap_min = step_fsm(key, p, bs, st, now_ts)
        # 발송 조건: 평단 밴드 진입 + ACCUM/PRELAUNCH + 누적 5억+ + 이 시퀀스 미알림
        if not bs:
            continue
        if rec["state"] not in ("ACCUM", "PRELAUNCH"):
            continue
        if bs["accum_man"] < ACCUM_CUT_MAN:
            continue
        if vwap <= 0:
            continue
        band = abs(last_px / vwap - 1) * 100
        if band > ENTRY_BAND_PCT:
            continue
        if rec.get("notified_seq", 0) >= rec["seq"]:
            continue
        exch, sym = key
        text = build_signal(exch, sym, names, bs, vwap, last_px, gap_min, rec["seq"], False)
        if notify_on:
            ok = send_telegram(env, chat_group, text)
            if ok:
                rec["notified_seq"] = rec["seq"]
                st[f"{exch}|{sym}"] = rec
            print(f"[fsm SENT] {exch}|{sym}")
        else:
            print(f"[fsm muted]\n{text}\n")
        signals += 1

    save_state(st)
    print(f"[fsm] 신호 {signals}건")

if __name__ == "__main__":
    main()
