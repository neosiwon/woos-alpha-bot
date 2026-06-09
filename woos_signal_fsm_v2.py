# woos_signal_fsm.py  (v2 — 시퀀스 FSM + 금액봇 + 장악률 + 거래량CUSUM)
# 매집 사이클 상태머신 (독립 실행, woos_accum_rank.py import 안 함)
#
# 모델 (검증 SIGNAL_MODEL_VALIDATION + 2026-06-09 재검증):
#   ACCUM      후보  : 장악률 >= GRIP_MIN (봇net/빗썸유통가치), 봇 도는 중
#   PRELAUNCH  발사후보: 봇 STALL_MIN+ 안 돎 = 매집완료, 발사 임박
#   LAUNCHED   발사  : 시퀀스 MFE +LAUNCH_PCT 달성
#   DUMPING    덤핑  : LAUNCHED 후 가격 < VWAP*(1-DUMP_DROP_PCT)
#   새 시퀀스       : DUMPING에서 가격 VWAP 회복 + 봇 재가동 -> seq+1
#   연장            : PRELAUNCH에서 +LAUNCH_PCT 전 봇 재가동 -> 같은 시퀀스 유지
#
# ★ v2 변경점 (2026-06-09 검증):
#   1) 봇 = 수량고정 OR 금액고정 (IO 1만·GWEI 2만원 = 금액봇, 수량은 가격÷결과)
#   2) 후보 컷 = 5억 절대값 -> 장악률(유통량 대비). 절대값은 대형코인 지배(WLD·ONDO)
#      검증: MOVE23%·SLX18%·GWEI15%(발사) vs ONDO·NEAR0.4%(비발사) 35배 분리
#   3) B형 거래량 CUSUM (시퀀스 baseline) = 발사 변곡 보조. 하루리셋 아닌 시퀀스누적
#   4) 분배 제외(-5%), 밈 제외(PEPE·SHIB·BONK)
#
# 속도: 날짜별 파일을 딱 1번씩만 통째로 읽고 전 종목 동시 누적 (scan_all 1패스).
# 총누적: 시퀀스 시작일~오늘 전 기간 합산 (최대 소급 MAX_SEQ_DAYS).
# 장악률: ACCUM 후보(봇 도는 종목)만 빗썸 유통량 API 호출 (전종목 호출 안함).
#
# 발송: FSM_NOTIFY=on 일 때만 단톡방 발송. 아니면 [fsm muted] 콘솔 미리보기.

import os, sys, gzip, csv, json, time, math
import urllib.request, urllib.parse
from collections import defaultdict
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
HOME = os.path.expanduser("~")
LOG_DIR = os.path.join(HOME, "woos_logs")
STATE_PATH = os.path.join(LOG_DIR, "fsm_state.json")
ENV_PATH = os.path.join(HOME, "woos-alpha-bot", ".env")

# ---- config ----
GRIP_MIN        = 10.0      # A-type: 장악률(봇net/유통가치) 컷 % (검증 발사 14~23 vs 비발사 0.4)
LAUNCH_PCT      = 5.0       # 시퀀스 MFE 이만큼이면 LAUNCHED (%)
DUMP_DROP_PCT   = 3.0       # LAUNCHED 후 VWAP 대비 이만큼 빠지면 DUMPING (%)
STALL_MIN       = 60        # 봇 이만큼(분) 안 돌면 PRELAUNCH(매집완료)
MAX_SEQ_DAYS    = 14        # 시퀀스 소급 최대 일수
ENTRY_BAND_PCT  = 0.6       # 평단(VWAP) +-이 밴드 안이면 진입 신호
DUMP_CUT_PCT    = -5.0      # 분배 제외: 시퀀스 시가 대비 이만큼 빠지면 후보 제외
# 봇 식별
DEC_MIN_CNT     = 4         # 소수점 수량 최소 반복 (수량봇)
INT_MIN_CNT     = 40        # 정수 수량 최소 반복 (수량봇)
AMT_REPEAT_MIN  = 20        # 금액봇: 같은 체결액(±0.5%버킷) 최소 반복
# B형 CUSUM
BUCKET_SEC      = 300       # 5분 버킷
CUSUM_K         = 1.0       # CUSUM slack
CUSUM_BASE_N    = 12        # baseline 버킷 수 (시퀀스 시작 첫 1시간)
STABLE = {"USDT","USDC","DAI","TUSD","BUSD"}
SKIP   = STABLE | {"BTC","ETH","XRP","SOL","DOGE","ADA","TRX","LINK","AVAX","DOT","BCH","SUI","BNB",
                   "PEPE","SHIB","BONK"}   # 밈 제외 추가

# ---- helpers ----
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
    # 수량봇: 소수점 수량 4회+ 또는 정수 수량 40회+
    if is_dec(qty):
        return cnt >= DEC_MIN_CNT
    return cnt >= INT_MIN_CNT

def amt_bucket(amt):
    # 금액봇 버킷: 0.5% 상대폭 (10,000 / 9,991 수수료차 흡수)
    if amt <= 0:
        return 0
    return int(math.log(amt) / math.log(1.005))

def fmt_man(man):
    if man >= 10000:
        return f"{man/10000:.1f}억"
    return f"{man:.0f}만"

def kn(sym, names):
    return names.get(sym, sym)

def load_korean_names():
    names = {}
    try:
        for base in ["https://api.upbit.com", "https://api.bithumb.com"]:
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

# ---- 빗썸 내부 유통량 (장악률 분모) ----
_SUPPLY_CACHE = {}
_COINTYPE = {}
def _load_cointypes():
    global _COINTYPE
    if _COINTYPE:
        return
    try:
        req = urllib.request.Request(
            "https://gw.bithumb.com/exchange/v1/comn/intro?coinType=C0101&crncCd=C0100",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bithumb.com/"})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        _COINTYPE = {c['coinSymbol']: c['coinType'] for c in data['data']['coinList']}
    except Exception:
        _COINTYPE = {}

def get_supply(sym):
    # 빗썸 내부 유통량(개수). 캐시. 업비트/없으면 None.
    if sym in _SUPPLY_CACHE:
        return _SUPPLY_CACHE[sym]
    _load_cointypes()
    ct = _COINTYPE.get(sym)
    if not ct:
        _SUPPLY_CACHE[sym] = None
        return None
    try:
        req = urllib.request.Request(
            f"https://gw.bithumb.com/exchange/v1/trade/accumulation/deposit/{ct}-C0100",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bithumb.com/"})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        s = float(data['data']['accumulationDepositAmt'])
    except Exception:
        s = None
    _SUPPLY_CACHE[sym] = s
    return s

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
    out = []
    d = start_date
    while d <= end_date:
        ds = d.strftime("%Y%m%d")
        up  = os.path.join(LOG_DIR, f"tick_{ds}.csv.gz")
        bt  = os.path.join(LOG_DIR, f"tick_bithumb_{ds}.csv.gz")
        up7 = os.path.join(LOG_DIR, f"tick_{ds}_7col.csv.gz")
        if os.path.exists(up7): out.append(("UP", ds, up7))
        if os.path.exists(up):  out.append(("UP", ds, up))
        if os.path.exists(bt):  out.append(("BT", ds, bt))
        d += timedelta(days=1)
    return out

def scan_all(files):
    # 1패스: 파일당 1회 읽기, 전 종목 동시 누적
    per = defaultdict(lambda: {
        "bots":  defaultdict(lambda: [0, 0.0, 0.0]),   # qty -> [cnt, buy_amt, sell_amt]
        "abots": defaultdict(lambda: [0, 0.0, 0.0]),   # amt_bucket -> [cnt, buy_amt, sell_amt]
        "first_ts": None, "last_ts": None,
        "sum_pxqty": 0.0, "sum_qty": 0.0, "hi": 0.0, "last_px": 0.0, "open_px": 0.0,
        "vol5": defaultdict(float),                     # (ds,bk) -> 거래대금 (CUSUM용)
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
                    ts = x[0][:16]
                    key = (exch, sym)
                    p = per[key]
                    # 수량봇 누적
                    q = round(qv, 4)
                    b = p["bots"][q]
                    b[0] += 1
                    # 금액봇 누적
                    ab = p["abots"][amt_bucket(amt)]
                    ab[0] += 1
                    if side == "BID":
                        b[1] += amt; ab[1] += amt
                    else:
                        b[2] += amt; ab[2] += amt
                    if p["first_ts"] is None:
                        p["first_ts"] = ts
                        p["open_px"] = px
                    p["last_ts"] = ts
                    p["sum_pxqty"] += px * qv
                    p["sum_qty"]   += qv
                    if px > p["hi"]:
                        p["hi"] = px
                    p["last_px"] = px
                    # 5분 거래량 버킷 (CUSUM용) — 날짜+버킷 키로 시퀀스 연속 누적
                    try:
                        hhmmss = x[0][11:19]
                        sec = int(hhmmss[:2])*3600 + int(hhmmss[3:5])*60 + int(hhmmss[6:8])
                        p["vol5"][(ds, sec // BUCKET_SEC)] += amt
                    except Exception:
                        pass
        except (FileNotFoundError, OSError):
            continue
    return per

def bot_summary(p):
    # 수량봇 + 금액봇 합산 (max로 이중계산 방지), net/대표봇/장악용 net
    # 수량봇
    qbots = []
    for q, (cnt, buy, sell) in p["bots"].items():
        if not is_bot_qty(q, cnt):
            continue
        qbots.append((q, cnt, buy, sell))
    # 금액봇
    abots = []
    for k, (cnt, buy, sell) in p["abots"].items():
        if cnt < AMT_REPEAT_MIN:
            continue
        abots.append((k, cnt, buy, sell))
    if not qbots and not abots:
        return None
    # net (매수-매도) — 수량봇/금액봇 각각 합산 후 max (이중계산 방지)
    def net_of(bots):
        return sum(b[2] for b in bots) - sum(b[3] for b in bots)
    def buy_of(bots):
        return sum(b[2] for b in bots)
    q_net = net_of(qbots); a_net = net_of(abots)
    q_buy = buy_of(qbots); a_buy = buy_of(abots)
    # 장악률 분자 = net (흡수/적극 다 세력). 더 큰 트랙 채택
    use_q = q_buy >= a_buy
    net_won = q_net if use_q else a_net
    buy_won = q_buy if use_q else a_buy
    # 대표봇 (수량봇 우선, 없으면 금액봇)
    if qbots:
        qbots.sort(key=lambda t: -(t[2] + t[3]))
        rep_q, rep_cnt = qbots[0][0], qbots[0][1]
        n_types = len(qbots) + len(abots)
        all_cnt = sum(b[1] for b in qbots)
        per_man = (q_buy / all_cnt) / 10000.0 if all_cnt else 0
    else:
        abots.sort(key=lambda t: -(t[2] + t[3]))
        rep_q, rep_cnt = 0, abots[0][1]
        n_types = len(abots)
        all_cnt = sum(b[1] for b in abots)
        per_man = (a_buy / all_cnt) / 10000.0 if all_cnt else 0
    return {
        "net_won": net_won, "buy_won": buy_won,
        "net_man": net_won / 10000.0,
        "rep_q": rep_q, "rep_cnt": rep_cnt, "n_types": n_types,
        "per_man": per_man,
        "has_qbot": bool(qbots), "has_abot": bool(abots),
    }

def cusum_peak(vol5, seq_start_ds):
    # 시퀀스 시작일~ 거래량 5분버킷 시계열 CUSUM (시퀀스 baseline)
    keys = sorted(k for k in vol5 if k[0] >= seq_start_ds)
    if len(keys) < CUSUM_BASE_N + 3:
        return 0.0
    series = [vol5[k] for k in keys]
    base = [v for v in series[:CUSUM_BASE_N] if v > 0]
    if len(base) < 3:
        return 0.0
    base.sort()
    med = base[len(base)//2]
    devs = sorted(abs(v - med) for v in base)
    mad = devs[len(devs)//2] or 0.1
    c = peak = 0.0
    for v in series[CUSUM_BASE_N:]:
        z = (v - med) / (1.4826 * mad)
        c = max(0.0, c + (z - CUSUM_K))
        peak = max(peak, c)
    return peak

def grip_rate(net_won, supply, last_px):
    # 장악률 = 봇net(원) / (유통량 × 현재가) × 100
    if not supply or last_px <= 0:
        return None
    sv = supply * last_px
    if sv <= 0:
        return None
    return net_won / sv * 100

# ---- FSM ----
def step_fsm(key, p, bs, grip, st, now_ts):
    exch, sym = key
    skey = f"{exch}|{sym}"
    rec = st.get(skey, {"state": "NONE", "seq": 1, "seq_start_ts": p["first_ts"],
                        "last_ts": None, "notified_seq": 0})
    vwap = p["sum_pxqty"] / p["sum_qty"] if p["sum_qty"] > 0 else 0
    last_px = p["last_px"]
    mfe_pct = (p["hi"] / vwap - 1) * 100 if vwap > 0 else 0
    try:
        last_dt = datetime.strptime(p["last_ts"], "%Y-%m-%d %H:%M")
        gap_min = (now_ts - last_dt).total_seconds() / 60.0
    except Exception:
        gap_min = 0
    state = rec["state"]
    seq = rec["seq"]
    # A형 후보 자격 = 장악률 >= GRIP_MIN (봇 있고 유통량 잡힐 때)
    grip_ok = (grip is not None and grip >= GRIP_MIN)
    # 상태 전이
    if state in ("NONE", "ACCUM", "PRELAUNCH"):
        if bs and grip_ok:
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
        if vwap > 0 and last_px >= vwap and gap_min < STALL_MIN:
            seq += 1
            rec["seq_start_ts"] = p["last_ts"]
            rec["notified_seq"] = 0
            state = "ACCUM"
    rec["state"] = state
    rec["seq"] = seq
    rec["last_ts"] = p["last_ts"]
    st[skey] = rec
    return rec, vwap, last_px, mfe_pct, gap_min

# ---- 신호 양식 ----
def build_signal(exch, sym, names, bs, grip, cusum, vwap, last_px, gap_min, seq):
    exname = "업비트" if exch == "UP" else "빗썸"
    band = (last_px / vwap - 1) * 100 if vwap > 0 else 0
    net = bs["net_man"]
    acc_label = "적극매집" if net >= 0 else "흡수매집"
    bot_kind = []
    if bs["has_qbot"]: bot_kind.append("수량봇")
    if bs["has_abot"]: bot_kind.append("금액봇")
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
    lines.append(f"- 장악률 {grip:.1f}% ({'+'.join(bot_kind)})")
    if bs["rep_q"]:
        lines.append(f"- 매집 봇 {bs['rep_q']:g}개 x{bs['rep_cnt']}회 외 {bs['n_types']-1}종")
    else:
        lines.append(f"- 금액 고정봇 x{bs['rep_cnt']}회 외 {bs['n_types']-1}종")
    lines.append(f"- {acc_label} (순 {net:+.0f}만)")
    if cusum and cusum > 0:
        lines.append(f"- 거래량 변곡 CUSUM {cusum:.0f}")
    lines.append(f"- 봇 활동 {dur_str(gap_min if gap_min else 0)}")
    if seq > 1:
        lines.append("- 이전 시퀀스 발사 후 재매집")
    lines.append("-------------")
    lines.append(f"손절 {sl:.4g}원 (-8%)")
    lines.append("TP1 (+5%)->50% / TP2 (+10%)->30% / TP3 (+15%)->20%")
    lines.append("- 보유한계 4H (TP1 도달 시 본절)")
    return "\n".join(lines)

def send_telegram(env, chat, text):
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
    chat = env.get("CHAT_ID_MONITOR") or env.get("TELEGRAM_CHAT_ID")
    now = datetime.now(KST)
    now_ts = now.replace(tzinfo=None)
    end_date = now.date()
    start_date = end_date - timedelta(days=MAX_SEQ_DAYS)
    files = day_files(
        datetime.combine(start_date, datetime.min.time()),
        datetime.combine(end_date, datetime.min.time()))
    if not files:
        print("[fsm] no tick files")
        return
    per = scan_all(files)
    st = load_state()
    names = load_korean_names()

    # 1차: 봇 있는 후보만 추림 (전종목 유통량 호출 방지)
    cands = []
    for key, p in per.items():
        bs = bot_summary(p)
        if not bs:
            continue
        # 분배 제외 (시퀀스 시가 대비)
        if p["open_px"] > 0:
            chg = (p["last_px"] / p["open_px"] - 1) * 100
            if chg <= DUMP_CUT_PCT:
                continue
        cands.append((key, p, bs))

    # 2차: 후보만 장악률(유통량) + CUSUM 계산, FSM 전이
    fired = []
    for key, p, bs in cands:
        exch, sym = key
        supply = get_supply(sym) if exch == "BT" else None
        grip = grip_rate(bs["net_won"], supply, p["last_px"]) if supply else None
        rec = st.get(f"{exch}|{sym}", {})
        seq_start_ds = (rec.get("seq_start_ts") or p["first_ts"] or "")[:10].replace("-", "")
        cusum = cusum_peak(p["vol5"], seq_start_ds) if seq_start_ds else 0.0
        r, vwap, last_px, mfe, gap = step_fsm(key, p, bs, grip, st, now_ts)
        # 신호 조건: ACCUM/PRELAUNCH 상태 + 평단 밴드 안 + 아직 미발송 시퀀스
        band = abs((last_px / vwap - 1) * 100) if vwap > 0 else 999
        if r["state"] in ("ACCUM", "PRELAUNCH") and grip is not None and grip >= GRIP_MIN \
           and band <= ENTRY_BAND_PCT and r["notified_seq"] != r["seq"]:
            text = build_signal(exch, sym, names, bs, grip, cusum, vwap, last_px, gap, r["seq"])
            fired.append((key, text))
            r["notified_seq"] = r["seq"]
            st[f"{exch}|{sym}"] = r

    save_state(st)
    # 발송
    if not fired:
        print(f"[fsm] {now.strftime('%H:%M')} 후보 {len(cands)} / 신호 0")
        return
    for key, text in fired:
        if notify_on:
            send_telegram(env, chat, text)
            print(f"[fsm sent] {key[1]}")
        else:
            print(f"[fsm muted] {key[1]}\n{text}\n")

if __name__ == "__main__":
    main()
