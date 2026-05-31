#!/usr/bin/env python3
# woos_wall_summary.py — 봇 매집/분배 요약 (pattern.csv 읽기 방식)
# 인자: "4h" (최근 4시간) / "day" (오늘 전체)
# 5분 탐지기가 기록한 pattern_YYYYMMDD.csv를 시간대별로 집계 → 텔레그램 모니터방

import os, sys, csv, json, collections, urllib.request, urllib.parse
from datetime import datetime, timedelta

LOG_DIR = os.path.expanduser("~/woos_logs")
ENV_PATH = os.path.expanduser("~/woos-alpha-bot/.env")

def load_env():
    env={}
    try:
        for line in open(ENV_PATH):
            line=line.strip()
            if '=' in line and not line.startswith('#'):
                k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
    except: pass
    return env

def send_telegram(token, chat_id, text):
    if not token or not chat_id: return False
    try:
        url=f"https://api.telegram.org/bot{token}/sendMessage"
        data=urllib.parse.urlencode({'chat_id':chat_id,'text':text,'parse_mode':'HTML'}).encode()
        with urllib.request.urlopen(urllib.request.Request(url,data=data),timeout=10) as r:
            return r.status==200
    except Exception as e:
        print(f"[tg] {e}"); return False

KOR={}
def load_korean_names():
    for url in ("https://api.upbit.com/v1/market/all?isDetails=false",
                "https://api.bithumb.com/v1/market/all?isDetails=false"):
        try:
            with urllib.request.urlopen(url,timeout=10) as r:
                for x in json.loads(r.read().decode()):
                    mk=x.get("market","")
                    if mk.startswith("KRW-") and mk[4:] not in KOR:
                        KOR[mk[4:]]=x.get("korean_name","")
        except: pass
def kn(s): return f"{KOR.get(s)}({s})" if KOR.get(s) else s

def main():
    mode = sys.argv[1] if len(sys.argv)>1 else "4h"
    env=load_env(); token=env.get('TELEGRAM_BOT_TOKEN'); chat=env.get('TELEGRAM_CHAT_ID_MONITOR')
    load_korean_names()
    now=datetime.now(); today=now.strftime("%Y%m%d")
    pat=os.path.join(LOG_DIR,f"pattern_{today}.csv")

    if mode=="4h":
        cutoff=(now-timedelta(hours=4)).strftime("%H:%M")
        title=f"📊 <b>4시간 봇 요약</b> ({cutoff}~{now.strftime('%H:%M')})"
    else:
        cutoff="00:00"
        title=f"📊 <b>오늘 봇 요약</b> ({today[4:6]}/{today[6:]})"

    # pattern.csv 읽기 (헤더: 시각,종목,종류,라벨,수량,횟수,비율,방향쏠림,평균금액,간격초,순매수만)
    rows=[]
    try:
        with open(pat) as f:
            r=csv.DictReader(f)
            for x in r:
                if x.get('시각','') >= cutoff:
                    rows.append(x)
    except FileNotFoundError:
        rows=[]

    # 종목별 최신 상태 집계 (같은 종목 여러번이면 최신 + 포착/종료 카운트)
    sym_data=collections.defaultdict(lambda: {'포착':0,'종료':0,'라벨':'','수량':'','횟수':0,'평균금액':0,'순매수':0,'마지막':'','거래소':''})
    for x in rows:
        sym=x['종목']; d=sym_data[sym]
        kind=x.get('종류',''); 
        if kind=='포착': d['포착']+=1
        elif kind=='종료': d['종료']+=1
        d['라벨']=x.get('라벨',d['라벨']); d['수량']=x.get('수량',''); 
        try: d['횟수']=int(float(x.get('횟수',0)))
        except: pass
        try: d['순매수']=float(x.get('순매수만',0))
        except: pass
        try: d['평균금액']=float(x.get('평균금액',0))
        except: pass
        d['마지막']=x.get('시각','')
        d['거래소']=x.get('거래소','')

    매집={s:d for s,d in sym_data.items() if d['라벨']=='매집'}
    분배={s:d for s,d in sym_data.items() if d['라벨']=='분배'}
    중립={s:d for s,d in sym_data.items() if d['라벨']=='중립'}
    종료된={s:d for s,d in sym_data.items() if d['종료']>0}

    # 순위: 매집=순매수 큰 순, 분배=순매도 큰 순 (절댓값)
    매집_s=sorted(매집.items(), key=lambda x:-x[1]['순매수'])
    분배_s=sorted(분배.items(), key=lambda x:x[1]['순매수'])
    종료_s=sorted(종료된.items(), key=lambda x:-abs(x[1]['순매수']))

    lines=[title,"─────────────"]
    if 종료_s:
        lines.append(f"🎯 <b>매집종료 {len(종료_s)}</b> (표류=신호후보)")
        for i,(sym,d) in enumerate(종료_s,1):
            ex=f"[{d['거래소']}]" if d['거래소'] else ""
            lines.append(f"{i}. {ex} <b>{kn(sym)}</b>  {d['마지막']}")
        lines.append("─────────────")
    if 매집_s:
        lines.append(f"🟢 <b>매집 {len(매집_s)}</b> (순매수순)")
        for i,(sym,d) in enumerate(매집_s[:10],1):
            ex=f"[{d['거래소']}]" if d['거래소'] else ""
            lines.append(f"{i}. {ex} <b>{kn(sym)}</b>")
            lines.append(f"   {d['수량']}개×{d['횟수']}회 건당{d['평균금액']/10000:.0f}만 순{d['순매수']:+.0f}만")
        lines.append("─────────────")
    if 분배_s:
        lines.append(f"🔴 <b>분배 {len(분배_s)}</b> (순매도순)")
        for i,(sym,d) in enumerate(분배_s[:8],1):
            ex=f"[{d['거래소']}]" if d['거래소'] else ""
            lines.append(f"{i}. {ex} <b>{kn(sym)}</b>")
            lines.append(f"   {d['수량']}개×{d['횟수']}회 건당{d['평균금액']/10000:.0f}만 순{d['순매수']:+.0f}만")
        lines.append("─────────────")
    if 중립:
        lines.append(f"⚪ 중립 {len(중립)}: "+", ".join(kn(s) for s in list(중립)[:8]))
    if not sym_data:
        lines.append("(이 시간대 봇 활동 없음)")
    msg="\n".join(lines)
    ok=send_telegram(token,chat,msg)
    print(msg)
    print(f"\n[{mode}] 발송{'OK' if ok else 'X'} 매집{len(매집)} 분배{len(분배)} 종료{len(종료된)}")

if __name__=="__main__":
    main()
