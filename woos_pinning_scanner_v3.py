#!/usr/bin/env python3
"""
woos_pinning_scanner_v3.py — A-type (pinning) launch scanner, STAGE 1 + STAGE 2
Telegram via existing alpha-bot .env (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID_GROUP).

Signature (backtest 6/6 + 4/4 on R2 history):
  Pre-launch 2h: bid-wall suppression (bid/ask<1.0) >=70% AND trades/min<1.0
STAGE 1: pinning detect -> register candidate + alert
STAGE 2: registered candidate's wall breaks >=1.0 AND ask-wall drops <=50% -> launch alert
A-type ONLY. B-type out of scope.
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
HEADERS = {"User-Agent": "Mozilla/5.0", "accept": "application/json"}
EXCLUDE = set()


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

wall_hist = defaultdict(deque)
ask_peak = defaultdict(float)
candidates = {}
alerted_release = set()
_tpm_cache = {}


def fetch_json(url, timeout=10):
    req = urllib.request.Request(url, headers=HEADERS)
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def get_all_markets():
    data = fetch_json(BITHUMB_MARKETS)
    return [m["market"].split("-")[1] for m in data if m["market"].startswith("KRW-")]


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


def trades_per_minute_local(sym, now):
    c = _tpm_cache.get(sym)
    if c and (now - c[0]).total_seconds() < TRADE_RECHECK_SEC:
        return c[1]
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    days = {cutoff.strftime("%Y%m%d"), now.strftime("%Y%m%d")}
    n = 0
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
                    except Exception:
                        continue
        except Exception:
            continue
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


def send_telegram(msg):
    token = ENV.get("TELEGRAM_BOT_TOKEN")
    chat = (ENV.get("TELEGRAM_CHAT_ID_GROUP") or ENV.get("TELEGRAM_CHAT_ID_PRIVATE")
            or ENV.get("TELEGRAM_CHAT_ID_MONITOR"))
    if not token or not chat:
        print("[tg-off] " + msg.replace("\n", " | "), flush=True)
        return
    try:
        url = "https://api.telegram.org/bot%s/sendMessage" % token
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg,
                                       "parse_mode": "HTML"}).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception as e:
        print("[tg] err %s" % str(e)[:40], flush=True)


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
                    send_telegram(
                        "\U0001F4CC [\ubc15\uc81c \ud3ec\ucc29] %s\n"
                        "\uc5b5\ub20c\ub9bc %.0f%% (2h) + \uac70\ub798\uc8fd\uc74c %.2f/\ubd84\n"
                        "\ub9e4\uc218\ubcbd\ube44\uc728 %.2f | \ucd1d\ub9e4\ub3c4\uc794\ub7c9 %.0f\n"
                        "\u2192 A\ud615 \ub9e4\uc9d1 \ud6c4\ubcf4. \ud480\ub9bc \uac10\uc2dc \uc2dc\uc791"
                        % (sym, sf * 100, tpm, wall, ask))
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
                send_telegram(
                    "\U0001F680 [\ubc1c\uc0ac \uc784\ubc15] %s\n"
                    "\uac00\ub450\ub9ac \ud480\ub9bc: \ub9e4\uc218\ubcbd %.2f\u2192%.2f, "
                    "\ub9e4\ub3c4\ubcbd %.0f\u2192%.0f (-%.0f%%)\n"
                    "\u2192 \uc5b5\ub20c\ub9bc \ud480\ub9b0 \uc9c1\ud6c4. \uc9c4\uc785 \uac80\ud1a0"
                    % (sym, c["last_wall"], wall, peak_ask, ask, (1 - ask / peak_ask) * 100))
            c["last_wall"] = wall
            c["last_ask"] = ask


def main():
    syms = get_all_markets()
    tg = "on" if ENV.get("TELEGRAM_BOT_TOKEN") else "OFF"
    print("[start] %d KRW markets | poll %ds | window %dh | supp>=%.2f | tpm<%.1f | tg=%s"
          % (len(syms), POLL_SEC, WINDOW_HOURS, SUPP_FRAC_MIN, TRADE_DEAD_TPM, tg), flush=True)
    while True:
        now = datetime.now()
        try:
            scan_once(syms, now)
        except Exception as e:
            print("[scan] err %s" % str(e)[:60], flush=True)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
