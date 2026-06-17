#!/usr/bin/env python3
"""
woos_pinning_scanner_v4.py — A-type (pinning) launch scanner, STAGE 1 + STAGE 2
Korean ticker + FSM build_signal format. Telegram via alpha-bot .env.

Validated (R2 backtest): STAGE 1 pinning 6/6, STAGE 2 release 4/4.
  STAGE 1: bid-wall suppression(<1.0) >=70% over 2h AND trades/min<1.0
  STAGE 2: registered candidate's wall breaks >=1.0 AND ask-wall drops <=50%
Grip ratio is shown as REFERENCE only (this session: grip has no pre-launch
discriminative power; it is context, not a launch basis). Volume-profile slot
prints only when data available. A-type ONLY.
"""
import urllib.request, urllib.parse, json, time, os, gzip
from collections import deque, defaultdict
from datetime import datetime, timedelta

POLL_SEC = 10
WINDOW_HOURS = 2
SUPP_FRAC_MIN = 0.70
WALL_SUPPRESS = 1.0
TRADE_DEAD_TPM = 1.0
RELEASE_WALL = 1.0
RELEASE_ASK_DROP = 0.5
CAND_TTL_HOURS = 12
MIN_SAMPLES = int(WINDOW_HOURS * 3600 / POLL_SEC * 0.5)

TICK_DIR = os.path.expanduser("~/woos_logs")
TICK_FMT = "tick_bithumb_{}.csv.gz"
TRADE_RECHECK_SEC = 60
ENV_PATH = "/home/neosiwon/woos-alpha-bot/.env"

BITHUMB_OB = "https://api.bithumb.com/v1/orderbook?markets={}"
BITHUMB_MARKETS = "https://api.bithumb.com/v1/market/all?isDetails=false"
GW_INTRO = "https://gw.bithumb.com/exchange/v1/comn/intro?coinType=C0101&crncCd=C0100"
GW_DEPOSIT = "https://gw.bithumb.com/exchange/v1/trade/accumulation/deposit/{}-C0100"
HEADERS = {"User-Agent": "Mozilla/5.0", "accept": "application/json"}
GW_HEADERS = {"User-Agent": "Mozilla/5.0", "accept": "application/json",
              "Referer": "https://www.bithumb.com/"}
EXCLUDE = set()

KST = None  # local time assumed KST on VM


def load_env():
    env = {}
    try:
        for line in open(ENV_PATH):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except Exception:
        pass
    return env

ENV = load_env()


def fetch_json(url, timeout=10, headers=None):
    req = urllib.request.Request(url, headers=headers or HEADERS)
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def load_korean_names():
    names = {}
    try:
        for m in fetch_json(BITHUMB_MARKETS):
            mk = m.get("market", "")
            if mk.startswith("KRW-"):
                names[mk[4:]] = m.get("korean_name", mk[4:])
    except Exception:
        pass
    return names

NAMES = load_korean_names()


def kn(sym):
    k = NAMES.get(sym, sym)
    return "%s(%s)" % (k, sym) if k != sym else sym


def get_all_markets():
    return [m["market"].split("-")[1] for m in fetch_json(BITHUMB_MARKETS)
            if m["market"].startswith("KRW-")]


def fetch_orderbooks(syms):
    out = {}
    for i in range(0, len(syms), 100):
        chunk = syms[i:i + 100]
        markets = ",".join("KRW-" + s for s in chunk)
        try:
            for d in fetch_json(BITHUMB_OB.format(markets)):
                sym = d["market"].split("-")[1]
                ta = d.get("total_ask_size", 0)
                tb = d.get("total_bid_size", 0)
                if ta > 0:
                    out[sym] = (tb / ta, ta)
        except Exception as e:
            print("[ob] chunk %d err: %s" % (i, str(e)[:40]), flush=True)
        time.sleep(0.1)
    return out

# --- supply (장악률 분모) cache: coinType map + deposit amount ---
_coinmap = {}
_supply_cache = {}


def _load_coinmap():
    global _coinmap
    try:
        data = fetch_json(GW_INTRO, headers=GW_HEADERS)
        # structure: data['data']['coinType'] mapping symbol->coinType code (best-effort)
        d = data.get("data", data)
        cm = d.get("coinType") or d.get("coinTypeList") or {}
        if isinstance(cm, dict):
            _coinmap = cm
    except Exception:
        _coinmap = {}


def get_supply(sym):
    """Bithumb internal circulating supply (accumulationDepositAmt). Best-effort."""
    if sym in _supply_cache:
        return _supply_cache[sym]
    if not _coinmap:
        _load_coinmap()
    ct = _coinmap.get(sym)
    val = 0.0
    if ct:
        try:
            data = fetch_json(GW_DEPOSIT.format(ct), headers=GW_HEADERS)
            d = data.get("data", data)
            val = float(d.get("accumulationDepositAmt", 0) or 0)
        except Exception:
            val = 0.0
    _supply_cache[sym] = val
    return val


def is_qbot(q, c):
    if q <= 0.001:
        return False
    return c >= 40 if abs(q - round(q, 2)) < 1e-6 else c >= 4


def grip_and_net(sym, now, last_px):
    """STAGE-2 only (few candidates): grip ratio + net direction from VM ticks.
    grip = (bot buy+sell)/2 / (supply*price)*100 ; net = bot buy-sell (man)."""
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    days = sorted({cutoff.strftime("%Y%m%d"), now.strftime("%Y%m%d")})
    qmap = {}
    for d in days:
        fp = os.path.join(TICK_DIR, TICK_FMT.format(d))
        if not os.path.exists(fp):
            continue
        try:
            with gzip.open(fp, "rt") as f:
                next(f, None)
                for line in f:
                    p = line.split(",")
                    if len(p) < 7 or p[1] != sym:
                        continue
                    try:
                        ts = p[0]
                        dt = datetime(int(ts[:4]), int(ts[5:7]), int(ts[8:10]),
                                      int(ts[11:13]), int(ts[14:16]))
                        if not (cutoff <= dt <= now):
                            continue
                        qv = round(float(p[3]), 4)
                        amt = float(p[4])
                        bid = (p[5] == "BID")
                        b = qmap.setdefault(qv, [0, 0.0, 0.0])
                        b[0] += 1
                        if bid:
                            b[1] += amt
                        else:
                            b[2] += amt
                    except Exception:
                        continue
        except Exception:
            continue
    buy = sum(v[1] for q, v in qmap.items() if is_qbot(q, v[0]))
    sell = sum(v[2] for q, v in qmap.items() if is_qbot(q, v[0]))
    intervene = buy + sell
    net_man = (buy - sell) / 1e4
    supply = get_supply(sym)
    grip = None
    if supply > 0 and last_px > 0:
        grip = (intervene / 2) / (supply * last_px) * 100
    return grip, net_man


wall_hist = defaultdict(deque)
ask_peak = defaultdict(float)
candidates = {}
alerted_release = set()
_tpm_cache = {}
_last_px = {}


def trades_per_minute_local(sym, now):
    c = _tpm_cache.get(sym)
    if c and (now - c[0]).total_seconds() < TRADE_RECHECK_SEC:
        return c[1]
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    days = {cutoff.strftime("%Y%m%d"), now.strftime("%Y%m%d")}
    n = 0
    px = 0.0
    for d in sorted(days):
        fp = os.path.join(TICK_DIR, TICK_FMT.format(d))
        if not os.path.exists(fp):
            continue
        try:
            with gzip.open(fp, "rt") as f:
                next(f, None)
                for line in f:
                    p = line.split(",")
                    if len(p) < 6 or p[1] != sym:
                        continue
                    try:
                        ts = p[0]
                        dt = datetime(int(ts[:4]), int(ts[5:7]), int(ts[8:10]),
                                      int(ts[11:13]), int(ts[14:16]))
                        if cutoff <= dt <= now:
                            n += 1
                            px = float(p[2])
                    except Exception:
                        continue
        except Exception:
            continue
    if px:
        _last_px[sym] = px
    tpm = n / (WINDOW_HOURS * 60.0)
    _tpm_cache[sym] = (now, tpm)
    return tpm


def update_window(sym, wall, ask, now):
    dq = wall_hist[sym]
    dq.append((now, wall, ask))
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    while dq and dq[0][0] < cutoff:
        dq.popleft()
    ask_peak[sym] = max(ask_peak[sym] * 0.999, ask)


def suppression_frac(sym):
    dq = wall_hist[sym]
    if len(dq) < MIN_SAMPLES:
        return None
    return sum(1 for _, w, _ in dq if w < WALL_SUPPRESS) / len(dq)


def _html_escape(msg):
    # escape &, <, > but keep our only intended tag <b>...</b>
    msg = msg.replace("&", "&amp;")
    msg = msg.replace("<b>", "\x00B\x00").replace("</b>", "\x00b\x00")
    msg = msg.replace("<", "&lt;").replace(">", "&gt;")
    msg = msg.replace("\x00B\x00", "<b>").replace("\x00b\x00", "</b>")
    return msg


def send_telegram(msg):
    token = ENV.get("TELEGRAM_BOT_TOKEN")
    chat = (ENV.get("TELEGRAM_CHAT_ID_GROUP") or ENV.get("TELEGRAM_CHAT_ID_PRIVATE")
            or ENV.get("TELEGRAM_CHAT_ID_MONITOR"))
    if not token or not chat:
        print("[tg-off] " + msg.replace("\n", " | "), flush=True)
        return
    try:
        url = "https://api.telegram.org/bot%s/sendMessage" % token
        data = urllib.parse.urlencode({"chat_id": chat, "text": _html_escape(msg),
                                       "parse_mode": "HTML"}).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception as e:
        print("[tg] err %s" % str(e)[:40], flush=True)


def msg_stage1(sym, sf, tpm, wall, ask):
    L = []
    L.append("\U0001F4CC [\ubc15\uc81c \ud3ec\ucc29] (\ube57\uc378)")
    L.append("-------------")
    L.append("")
    L.append("\uc885\ubaa9: <b>%s</b>" % kn(sym))
    L.append("== A\ud615 \ub9e4\uc9d1 \ud6c4\ubcf4 ==")
    L.append("")
    L.append("- \uc5b5\ub20c\ub9bc %.0f%% (2h \uc9c0\uc18d, \ub9e4\uc218\ubcbd 1.0\ubbf8\ub9cc)" % (sf * 100))
    L.append("- \uac70\ub798\uc8fd\uc74c %.2f\uac74/\ubd84 (\uc778\uc704\uc801 \uc815\uc801)" % tpm)
    L.append("- \ub9e4\uc218\ubcbd\ube44\uc728 %.2f | \ub9e4\ub3c4\ubcbd %s" % (wall, format(int(ask), ",")))
    L.append("-------------")
    L.append("\uc138\ub825 \ub9e4\uc9d1 \uc911 \u00b7 \uac00\ub450\ub9ac \ud480\ub9bc \uac10\uc2dc \uc2dc\uc791")
    return "\n".join(L)


def msg_stage2(sym, last_wall, wall, peak_ask, ask, last_px, grip, net_man):
    sl = last_px * 0.92 if last_px else 0
    L = []
    L.append("\U0001F680 [\ubc1c\uc0ac \uc784\ubc15] (\ube57\uc378)")
    L.append("-------------")
    L.append("")
    L.append("\uc885\ubaa9: <b>%s</b>" % kn(sym))
    if last_px:
        L.append("== \ub9e4\uc218\uac00 %g\uc6d0 ==" % last_px)
    L.append("")
    L.append("- \uac00\ub450\ub9ac \ud480\ub9bc: \ub9e4\uc218\ubcbd %.2f\u2192%.2f" % (last_wall, wall))
    L.append("- \ub9e4\ub3c4\ubcbd %s\u2192%s (-%.0f%%)"
             % (format(int(peak_ask), ","), format(int(ask), ","), (1 - ask / peak_ask) * 100))
    if net_man is not None:
        lbl = "\U0001F7E2\uc801\uadf9" if net_man >= 0 else "\U0001F534\ud761\uc218"
        L.append("- %s\ub9e4\uc9d1 (\uc21c %+.0f\ub9cc)" % (lbl, net_man))
    if grip is not None:
        L.append("- \uc7a5\uc545\ub960 %.1f%% (\ucc38\uace0)" % grip)
    L.append("-------------")
    if last_px:
        L.append("\uc190\uc808 %.4g\uc6d0 (-8%%)" % sl)
    L.append("TP1 (+5%)->50% / TP2 (+10%)->30% / TP3 (+15%)->20%")
    L.append("- \ubcf4\uc720\ud55c\uacc4 4H (TP1 \ub3c4\ub2ec \uc2dc \ubcf8\uc808)")
    return "\n".join(L)


def scan_once(syms, now):
    for sym, (wall, ask) in fetch_orderbooks(syms).items():
        if sym in EXCLUDE:
            continue
        update_window(sym, wall, ask, now)
        sf = suppression_frac(sym)
        if sf is None:
            continue
        # STAGE 1
        if sf >= SUPP_FRAC_MIN:
            tpm = trades_per_minute_local(sym, now)
            if tpm < TRADE_DEAD_TPM:
                if sym not in candidates:
                    candidates[sym] = {"since": now, "last_wall": wall, "last_ask": ask}
                    send_telegram(msg_stage1(sym, sf, tpm, wall, ask))
                else:
                    candidates[sym]["last_wall"] = wall
                    candidates[sym]["last_ask"] = ask
        # STAGE 2
        if sym in candidates:
            c = candidates[sym]
            if (now - c["since"]).total_seconds() > CAND_TTL_HOURS * 3600:
                candidates.pop(sym, None)
                alerted_release.discard(sym)
                continue
            peak_ask = max(ask_peak[sym], c.get("last_ask", ask))
            if wall >= RELEASE_WALL and ask <= peak_ask * RELEASE_ASK_DROP and sym not in alerted_release:
                alerted_release.add(sym)
                last_px = _last_px.get(sym, 0)
                grip, net_man = grip_and_net(sym, now, last_px)
                send_telegram(msg_stage2(sym, c["last_wall"], wall, peak_ask, ask,
                                         last_px, grip, net_man))
            c["last_wall"] = wall
            c["last_ask"] = ask


def main():
    syms = get_all_markets()
    tg = "on" if ENV.get("TELEGRAM_BOT_TOKEN") else "OFF"
    print("[start] %d KRW markets | poll %ds | window %dh | supp>=%.2f | tpm<%.1f | tg=%s | names=%d"
          % (len(syms), POLL_SEC, WINDOW_HOURS, SUPP_FRAC_MIN, TRADE_DEAD_TPM, tg, len(NAMES)),
          flush=True)
    while True:
        now = datetime.now()
        try:
            scan_once(syms, now)
        except Exception as e:
            print("[scan] err %s" % str(e)[:60], flush=True)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
