#!/usr/bin/env python3
# woos_orderbook_collector.py
# 업비트 호가(orderbook) 수집기 — 세력 흡수/매도벽 소진 추적용
# 기존 체결 수집기(woos_collector.py)와 독립적으로 동작.
# 60초 주기로 전체 KRW 마켓 호가를 받아 ~/woos_logs/orderbook_YYYYMMDD.csv 에 기록.
#
# 기록 컬럼:
#   시각KST, 종목, 총매도잔량, 총매수잔량, 매수벽비율(bid/ask),
#   상위5매도잔량, 상위5매수잔량, 최우선매도가, 최우선매수가, 스프레드%
#
# 세력 모델 관점:
#   - 매수벽비율(bid/ask) > 1  → 세력이 매수벽으로 받치는 중(흡수)
#   - 총매도잔량 시간에 걸쳐 감소 → 매도벽 소진(물리적 마름) → 발사 임박
#   - 단, 호가는 스푸핑(가짜벽) 가능 → 체결 데이터와 반드시 교차확인

import requests
import time
import csv
import os
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
LOG_DIR = os.path.expanduser("~/woos_logs")
INTERVAL = 60          # 수집 주기(초)
BATCH = 100            # 호가 API 한 번에 요청할 종목 수 (업비트 허용 범위)
REQ_DELAY = 0.2        # 배치 간 딜레이(초) — 429 방지

# 스테이블코인만 제외 (체결 수집기와 동일 정책 — 후처리에서 MAJORS 거름)
STABLE = {"USDT", "USDC", "DAI", "TUSD", "BUSD"}


def get_krw_markets():
    """전체 KRW 마켓 목록 (스테이블 제외)."""
    r = requests.get("https://api.upbit.com/v1/market/all",
                     params={"isDetails": "false"}, timeout=10)
    r.raise_for_status()
    out = []
    for m in r.json():
        mk = m["market"]
        if not mk.startswith("KRW-"):
            continue
        sym = mk.split("-")[1]
        if sym in STABLE:
            continue
        out.append(mk)
    return out


def fetch_orderbooks(markets):
    """복수 종목 호가 조회 (배치)."""
    results = []
    for i in range(0, len(markets), BATCH):
        chunk = markets[i:i + BATCH]
        try:
            r = requests.get("https://api.upbit.com/v1/orderbook",
                             params={"markets": ",".join(chunk)}, timeout=10)
            if r.status_code == 429:
                time.sleep(1.0)
                r = requests.get("https://api.upbit.com/v1/orderbook",
                                 params={"markets": ",".join(chunk)}, timeout=10)
            r.raise_for_status()
            results.extend(r.json())
        except Exception as e:
            print(f"[ob] fetch fail chunk {i}: {e}")
        time.sleep(REQ_DELAY)
    return results


def row_from_orderbook(ob):
    """호가 1건 → 기록용 행."""
    sym = ob["market"].split("-")[1]
    units = ob.get("orderbook_units", [])
    total_ask = ob.get("total_ask_size", 0) or 0
    total_bid = ob.get("total_bid_size", 0) or 0
    # 매수벽 비율 (bid/ask) — >1이면 매수벽이 더 두꺼움(흡수 정황)
    bid_ask_ratio = round(total_bid / total_ask, 4) if total_ask > 0 else 9999
    # 상위 5호가 잔량 합
    top5_ask = sum(u["ask_size"] for u in units[:5]) if units else 0
    top5_bid = sum(u["bid_size"] for u in units[:5]) if units else 0
    # 최우선 호가 + 스프레드
    if units:
        best_ask = units[0]["ask_price"]
        best_bid = units[0]["bid_price"]
        spread = round((best_ask - best_bid) / best_bid * 100, 4) if best_bid > 0 else 0
    else:
        best_ask = best_bid = spread = 0
    return [
        sym,
        round(total_ask, 4),
        round(total_bid, 4),
        bid_ask_ratio,
        round(top5_ask, 4),
        round(top5_bid, 4),
        best_ask,
        best_bid,
        spread,
    ]


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    print(f"[ob] orderbook collector start — interval {INTERVAL}s, log {LOG_DIR}")
    markets = get_krw_markets()
    print(f"[ob] tracking {len(markets)} KRW markets")
    last_market_refresh = time.time()

    while True:
        loop_start = time.time()
        now = datetime.now(KST)
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        fname = os.path.join(LOG_DIR, f"orderbook_{now.strftime('%Y%m%d')}.csv")
        new_file = not os.path.exists(fname)

        try:
            obs = fetch_orderbooks(markets)
            with open(fname, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(["시각KST", "종목", "총매도잔량", "총매수잔량",
                                "매수벽비율", "상위5매도", "상위5매수",
                                "최우선매도가", "최우선매수가", "스프레드%"])
                for ob in obs:
                    w.writerow([ts] + row_from_orderbook(ob))
            print(f"[ob] {ts} wrote {len(obs)} books")
        except Exception as e:
            print(f"[ob] loop error: {e}")

        # 마켓 목록 6시간마다 갱신 (신규 상장 반영)
        if time.time() - last_market_refresh > 6 * 3600:
            try:
                markets = get_krw_markets()
                last_market_refresh = time.time()
                print(f"[ob] markets refreshed: {len(markets)}")
            except Exception as e:
                print(f"[ob] market refresh fail: {e}")

        elapsed = time.time() - loop_start
        time.sleep(max(1, INTERVAL - elapsed))


if __name__ == "__main__":
    main()
