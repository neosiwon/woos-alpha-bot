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

    def build_and_send(exch_filter, exch_name, emoji_head):
        # exch_filter: '업비트' or '빗썸' / 해당 거래소 종목만
        sd={k:v for k,v in sym_data.items() if v.get('거래소','')==exch_filter}
        매집={s:d for s,d in sd.items() if d['라벨']=='매집'}
        분배={s:d for s,d in sd.items() if d['라벨']=='분배'}
        중립={s:d for s,d in sd.items() if d['라벨']=='중립'}
        종료={s:d for s,d in sd.items() if d['종료']>0}
        매집_s=sorted(매집.items(), key=lambda x:-x[1]['순매수'])
        분배_s=sorted(분배.items(), key=lambda x:x[1]['순매수'])
        종료_s=sorted(종료.items(), key=lambda x:-abs(x[1]['순매수']))

        # 순매수 만원 → 보기 좋게 (억/만)
        def fmt_won(man):
            v=man*10000
            sign='+' if v>=0 else '−'; av=abs(v)
            if av>=1e8: return f"{sign}{av/1e8:.1f}억"
            return f"{sign}{av/1e4:,.0f}만"
        # 수량 콤마
        def fmt_qty(q):
            try:
                qf=float(q)
                return f"{qf:,.0f}" if qf>=1000 else f"{qf:g}"
            except: return str(q)
        CIRC="①②③④⑤⑥⑦⑧⑨⑩"
        def num(i): return CIRC[i-1] if 1<=i<=10 else f"{i}."
        BAR="━━━━━━━━━━━━━━"

        lines=[f"{emoji_head} <b>{exch_name} · {title_sub}</b>", BAR, ""]
        if 종료_s:
            lines.append(f"🎯 <b>매집종료 {len(종료_s)}</b> · 표류=신호후보")
            lines.append("")
            for i,(sym,d) in enumerate(종료_s,1):
                lines.append(f"　{num(i)} {kn(sym)}")
                lines.append(f"　　└ {d['마지막']} 표류 시작")
                lines.append("")
            lines.append(BAR); lines.append("")
        if 매집_s:
            lines.append(f"🟢 <b>매집 {len(매집_s)}</b> · 순매수순")
            lines.append("")
            for i,(sym,d) in enumerate(매집_s[:10],1):
                lines.append(f"　{num(i)} <b>{kn(sym)}</b>")
                lines.append(f"　　{fmt_qty(d['수량'])}개 × {d['횟수']}회")
                lines.append(f"　　건당 {d['평균금액']/10000:.0f}만 ┃ 순 {fmt_won(d['순매수'])}")
                lines.append("")
            lines.append(BAR); lines.append("")
        if 분배_s:
            lines.append(f"🔴 <b>분배 {len(분배_s)}</b> · 순매도순")
            lines.append("")
            for i,(sym,d) in enumerate(분배_s[:8],1):
                lines.append(f"　{num(i)} <b>{kn(sym)}</b>")
                lines.append(f"　　{fmt_qty(d['수량'])}개 × {d['횟수']}회")
                lines.append(f"　　건당 {d['평균금액']/10000:.0f}만 ┃ 순 {fmt_won(d['순매수'])}")
                lines.append("")
            lines.append(BAR); lines.append("")
        if 중립:
            lines.append(f"⚪ <b>중립</b> · "+", ".join(kn(x) for x in list(중립)[:8]))
        if not sd:
            lines.append("(이 시간대 봇 활동 없음)")
        # 끝 빈 줄 정리
        while lines and lines[-1]=="": lines.pop()
        msg="\n".join(lines)
        ok=send_telegram(token,chat,msg)
        print(msg); print(f"[{exch_name}] 발송{'OK' if ok else 'X'} 매집{len(매집_s)} 분배{len(분배_s)} 종료{len(종료_s)}\n")

    # title에서 기간 부분만 추출
    title_sub = title.replace("📊 <b>","").replace("</b>","").replace("오늘 봇 요약","하루 요약").replace("4시간 봇 요약","4시간 요약")

    # 업비트 / 빗썸 따로 발송
    build_and_send('업비트', '업비트', '📊')
    build_and_send('빗썸', '빗썸', '📊')

if __name__=="__main__":
    main()
