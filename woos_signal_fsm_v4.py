# woos_signal_fsm.py  (v4 — 피보 TP: SMC 매물대 + 단기/장기 고저, 손절 -4%)
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
try:
    from alpha_smc import fetch_candles, analyze
except Exception:
    fetch_candles = analyze = None

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
def snap_dates(start_date, end_date):
    # 스냅샷 파일 있는 날짜 리스트 (오래된→최신)
    out = []
    d = start_date
    while d <= end_date:
        ds = d.strftime("%Y%m%d")
        path = os.path.join(LOG_DIR, f"fsm_snap_{ds}.json")
        if os.path.exists(path):
            out.append((ds, path))
        d += timedelta(days=1)
    return out

def load_snapshots(dates):
    # 날짜별 스냅샷(fsm_snap_*.json)을 읽어 종목별 per 구조로 합침.
    # 시퀀스 소급 = 여러 날 스냅샷 누적. 수집기가 빗썸 전종목이므로 exch=BT.
    # 스냅샷은 '현재 누적 스냅' (그날 0시~저장시점). 같은 날은 최신 스냅이 누적이라 덮어쓰기,
    # 다른 날은 vol5/누적을 합산.
    per = defaultdict(lambda: {
        "bots":  defaultdict(lambda: [0, 0.0, 0.0]),
        "abots": defaultdict(lambda: [0, 0.0, 0.0]),
        "first_ts": None, "last_ts": None,
        "sum_pxqty": 0.0, "sum_qty": 0.0, "hi": 0.0, "last_px": 0.0, "open_px": 0.0,
        "vol5": defaultdict(float),
    })
    for ds, path in dates:
        try:
            with open(path) as f:
                snap = json.load(f)
        except Exception:
            continue
        for sym, s in snap.items():
            if sym in SKIP:
                continue
            key = ("BT", sym)
            p = per[key]
            # 봇 카운트/금액 (그날 누적) — 날짜별 합산
            for q_str, v in s.get("bots", {}).items():
                try: q = round(float(q_str), 4)
                except Exception: continue
                b = p["bots"][q]
                b[0] += v[0]; b[1] += v[1]; b[2] += v[2]
            for k_str, v in s.get("abots", {}).items():
                ab = p["abots"][k_str]   # 버킷 키 그대로 (문자열)
                ab[0] += v[0]; ab[1] += v[1]; ab[2] += v[2]
            # 거래량 버킷 (날짜+버킷 키로 시퀀스 연속)
            for bk_str, vol in s.get("vol5", {}).items():
                p["vol5"][(ds, bk_str)] += vol
            # VWAP 누적 / hi (날짜별 합산)
            p["sum_pxqty"] += s.get("sum_pxqty", 0.0)
            p["sum_qty"]   += s.get("sum_qty", 0.0)
            if s.get("hi", 0) > p["hi"]:
                p["hi"] = s["hi"]
            # ts: 가장 이른 first / 가장 늦은 last
            ft = s.get("first_ts")
            if ft and (p["first_ts"] is None or ft < p["first_ts"]):
                p["first_ts"] = ft
                p["open_px"] = s.get("open_px", 0.0)
            lt = s.get("last_ts")
            if lt and (p["last_ts"] is None or lt > p["last_ts"]):
                p["last_ts"] = lt
                p["last_px"] = s.get("last_px", 0.0)
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
    # vol5 키 = (ds, bk_str). 시계열 정렬: 날짜 + 버킷번호(int)
    def sort_key(k):
        ds, bk = k
        try: return (ds, int(bk))
        except Exception: return (ds, 0)
    keys = sorted((k for k in vol5 if k[0] >= seq_start_ds), key=sort_key)
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
def fib_tp(sym, entry_px):
    # SMC 매물대(bot~top) + 단기/장기 고저로 zone 적응형 피보 TP.
    # 반환: dict(tp=[(라벨,가격,%), ...], box_top_pct, zone, cycle, resist) or None
    if fetch_candles is None:
        return None
    c = fetch_candles(sym, unit=60, count=300)
    if not c:
        return None
    r = analyze(c)
    if not r or r["top"] <= r["bot"]:
        return None
    bot, top, cur = r["bot"], r["top"], entry_px
    cyc, zone = r["cycle"], r["zone"]
    sh = r["swing_hi"][1] if r["swing_hi"] else None
    # 박스 피보 레벨
    f618 = bot + (top - bot) * 0.618
    f786 = bot + (top - bot) * 0.786
    e1272 = top + (top - bot) * 0.272
    # 현재가 위 후보 (가까운 순)
    cand = []
    for name, v in [("fib0.618", f618), ("fib0.786", f786), ("단기고", sh),
                    ("매물대상단", top), ("확장1.272", e1272)]:
        if v and v > cur * 1.01:                    # 진입가보다 1%+ 위
            cand.append((name, v, (v/cur - 1) * 100))
    cand.sort(key=lambda x: x[2])
    box_top_pct = (top / cur - 1) * 100
    # TP1 = +5% 고정. TP2 = 위 첫 피보, TP3 = 매물대상단(top) 또는 확장
    tp = [("TP1", cur * 1.05, 5.0)]
    mids = [x for x in cand if 5 < x[2]]            # +5% 위 후보
    if mids:
        tp.append(("TP2", mids[0][1], mids[0][2]))
        # TP3 = 매물대상단(top) 우선, 없으면 더 먼 후보
        top_pct = (top/cur - 1) * 100
        if top_pct > mids[0][2]:
            tp.append(("TP3", top, top_pct))
        elif len(mids) > 1:
            tp.append(("TP3", mids[-1][1], mids[-1][2]))
    return dict(tp=tp, box_top_pct=box_top_pct, zone=zone, cycle=cyc,
                resist=top, n_up=len(cand))

def build_signal(exch, sym, names, bs, grip, cusum, vwap, last_px, gap_min, seq, ftp=None):
    exname = "업비트" if exch == "UP" else "빗썸"
    band = (last_px / vwap - 1) * 100 if vwap > 0 else 0
    net = bs["net_man"]
    acc_label = "적극매집" if net >= 0 else "흡수매집"
    bot_kind = []
    if bs["has_qbot"]: bot_kind.append("수량봇")
    if bs["has_abot"]: bot_kind.append("금액봇")
    sl = vwap * 0.96   # A형 손절 -4% (메모리 #25)
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
    if ftp:
        thin = "저항 얇음" if ftp["box_top_pct"] >= 12 else "저항 근접"
        lines.append(f"- 박스 상단 +{ftp['box_top_pct']:.1f}% ({thin})")
    if cusum and cusum > 0:
        lines.append(f"- 거래량 변곡 CUSUM {cusum:.0f}")
    lines.append(f"- 봇 활동 {dur_str(gap_min if gap_min else 0)}")
    if seq > 1:
        lines.append("- 이전 시퀀스 발사 후 재매집")
    lines.append("-------------")
    lines.append(f"손절 {sl:.4g}원 (-4%)")
    # 피보 TP (없으면 고정 fallback)
    if ftp and len(ftp["tp"]) >= 2:
        ratios = ["50%", "30%", "20%"]
        for i, (name, px, pct) in enumerate(ftp["tp"]):
            r = ratios[i] if i < len(ratios) else ""
            lines.append(f"{name} {px:.4g}원 (+{pct:.1f}%) -> {r}")
    else:
        lines.append(f"TP1 {last_px*1.05:.4g}원 (+5%)->50% / TP2 (+10%)->30% / TP3 (+15%)->20%")
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
    dates = snap_dates(
        datetime.combine(start_date, datetime.min.time()),
        datetime.combine(end_date, datetime.min.time()))
    if not dates:
        print("[fsm] no snapshot files (수집기 fsm_agg 가동 확인)")
        return
    per = load_snapshots(dates)
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
            ftp = fib_tp(sym, last_px) if exch == "BT" else None
            text = build_signal(exch, sym, names, bs, grip, cusum, vwap, last_px, gap, r["seq"], ftp)
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
