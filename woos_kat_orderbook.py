#!/usr/bin/env python3
# woos_kat_orderbook.py — 단일 종목 오더북 감시 (발사 방아쇠 = 매도벽 소멸)
# 사용: python3 woos_kat_orderbook.py KAT   (기본 KAT)
# 표류 진입한 종목의 오더북을 30초마다 찍어서:
#  - 위쪽 매도벽(현재가 위 N단 총량)이 직전 대비 급감 → "매도벽 소멸 = 발사 임박" 알람
#  - 매수/매도 비율 추적
#  - 발사(가격 급등) 또는 N분 경과시 자동 종료

import sys, json, time, threading, os, urllib.request, urllib.parse
import websocket
from datetime import datetime, timezone, timedelta

SYM = sys.argv[1] if len(sys.argv)>1 else "KAT"
CODE = f"KRW-{SYM}"
KST = timezone(timedelta(hours=9))
ENV_PATH = os.path.expanduser("~/woos-alpha-bot/.env")

POLL_SEC = 30          # 30초마다 오더북
WALL_DROP = 0.5        # 매도벽이 직전의 50% 미만으로 줄면 '소멸'
BID_SURGE = 1.8        # 매수벽이 직전의 1.8배+ 되면 '급증'(받침강화)
BID_DROP  = 0.5        # 매수벽이 직전의 50% 미만 되면 '소멸'(받침빠짐)
PRICE_JUMP = 0.02      # 가격 2%+ 오르면 발사로 보고 종료
MAX_MIN = 120          # 최대 2시간 감시
ASK_LEVELS = 5         # 현재가 위 5단까지를 '매도벽'으로

def load_env():
    env={}
    try:
        for line in open(ENV_PATH):
            line=line.strip()
            if '=' in line and not line.startswith('#'):
                k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
    except: pass
    return env

def send_telegram(token, chat, text):
    if not token or not chat: return
    try:
        url=f"https://api.telegram.org/bot{token}/sendMessage"
        data=urllib.parse.urlencode({'chat_id':chat,'text':text,'parse_mode':'HTML'}).encode()
        urllib.request.urlopen(urllib.request.Request(url,data=data),timeout=10)
    except Exception as e: print(f"[tg] {e}")

def get_orderbook():
    """한 번 연결해서 오더북 스냅샷 1개 받기"""
    result={}
    def on_message(ws,msg):
        result['data']=json.loads(msg); ws.close()
    def on_open(ws):
        ws.send(json.dumps([{"ticket":"ob"},{"type":"orderbook","codes":[CODE]},{"format":"DEFAULT"}]))
    ws=websocket.WebSocketApp("wss://api.upbit.com/websocket/v1",on_open=on_open,on_message=on_message)
    t=threading.Thread(target=ws.run_forever); t.start()
    time.sleep(5); ws.close(); t.join(timeout=2)
    return result.get('data')

def main():
    env=load_env(); token=env.get('TELEGRAM_BOT_TOKEN'); chat=env.get('TELEGRAM_CHAT_ID_MONITOR')
    print(f"=== {SYM} 오더북 감시 시작 ({datetime.now(KST).strftime('%H:%M')}) ===", flush=True)
    send_telegram(token,chat,f"👁 <b>[{SYM}] 오더북 감시 시작</b>\n표류 진입 → 발사 방아쇠(매도벽 소멸) 추적")

    prev_ask_wall=None
    prev_bid_wall=None
    start=time.time()
    first_price=None
    while time.time()-start < MAX_MIN*60:
        ob=get_orderbook()
        if not ob:
            time.sleep(POLL_SEC); continue
        units=ob.get('orderbook_units',[])
        if not units: time.sleep(POLL_SEC); continue
        # 현재가 ~ 위 ASK_LEVELS단 매도 총량 = 매도벽
        ask_wall=sum(u['ask_size'] for u in units[:ASK_LEVELS])
        bid_wall=sum(u['bid_size'] for u in units[:ASK_LEVELS])
        cur_ask=units[0]['ask_price']; cur_bid=units[0]['bid_price']
        ratio=bid_wall/ask_wall if ask_wall>0 else 0
        now=datetime.now(KST).strftime('%H:%M:%S')
        if first_price is None: first_price=cur_ask
        print(f"[{now}] 매도벽 {ask_wall:,.0f} / 매수벽 {bid_wall:,.0f} (비율 {ratio:.2f}) 현재 {cur_bid}~{cur_ask}", flush=True)

        # 발사 방아쇠 — 매도벽 소멸 (위가 뚫림)
        if prev_ask_wall and ask_wall < prev_ask_wall*WALL_DROP:
            msg=(f"🚀 <b>[{SYM}] 매도벽 소멸 — 발사 방아쇠</b>\n"
                 f"─────────────\n"
                 f"• 매도벽 {prev_ask_wall:,.0f} → {ask_wall:,.0f} ({ask_wall/prev_ask_wall*100:.0f}%)\n"
                 f"• 현재가 {cur_ask}원\n"
                 f"🎯 세력이 누르던 벽 거둠 = 급등 임박")
            send_telegram(token,chat,msg); print(msg,flush=True)
        # 매수벽 급증 (받침 강화 = 발사 준비?)
        if prev_bid_wall and bid_wall > prev_bid_wall*BID_SURGE:
            msg=(f"🟢 <b>[{SYM}] 매수벽 급증 — 받침 강화</b>\n"
                 f"─────────────\n"
                 f"• 매수벽 {prev_bid_wall:,.0f} → {bid_wall:,.0f} ({bid_wall/prev_bid_wall:.1f}배)\n"
                 f"• 현재가 {cur_bid}원\n"
                 f"🎯 세력 받침 강화 = 발사 준비?")
            send_telegram(token,chat,msg); print(msg,flush=True)
        # 매수벽 소멸 (받침 빠짐 = 하락 위험)
        if prev_bid_wall and bid_wall < prev_bid_wall*BID_DROP:
            msg=(f"🔻 <b>[{SYM}] 매수벽 소멸 — 받침 빠짐</b>\n"
                 f"─────────────\n"
                 f"• 매수벽 {prev_bid_wall:,.0f} → {bid_wall:,.0f} ({bid_wall/prev_bid_wall*100:.0f}%)\n"
                 f"• 현재가 {cur_bid}원\n"
                 f"⚠️ 받침 빠짐 = 하락 위험")
            send_telegram(token,chat,msg); print(msg,flush=True)

        # 가격 급등 = 발사 확인 → 종료
        if cur_ask >= first_price*(1+PRICE_JUMP):
            msg=f"🔥 <b>[{SYM}] 발사 확인</b>\n{first_price}→{cur_ask}원 (+{(cur_ask/first_price-1)*100:.1f}%)\n감시 종료"
            send_telegram(token,chat,msg); print(msg,flush=True)
            break
        prev_ask_wall=ask_wall
        prev_bid_wall=bid_wall
        time.sleep(POLL_SEC)
    print(f"=== {SYM} 감시 종료 ===", flush=True)

if __name__=="__main__":
    main()
