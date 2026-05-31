#!/usr/bin/env python3
# woos_accum_rank.py — 봇 누적 상위 알람 + 봇 멈춤 알람 (노이즈 최소화)
# - 종목의 봇 전체 합산 (대표봇 + 외 N종)
# - 누적 원화/횟수/건당/순매수/생애시간/전시간대비
# - 거래소별(업비트/빗썸) 상위 N
# - 봇 멈춤(1시간 매집하던 봇이 안 돎): 상위 아니어도 별도 알람
# 정기(1시간) 실행 권장.

import os, sys, csv, gzip, json, collections, urllib.request, urllib.parse
from datetime import datetime, timedelta

LOG_DIR = os.path.expanduser("~/woos_logs")
ENV_PATH = os.path.expanduser("~/woos-alpha-bot/.env")
STATE_PATH = os.path.join(LOG_DIR, "accum_state.json")

TOP_N = 15
MIN_CNT_DEC = 4
MIN_CNT_INT = 40
MIN_ACCUM_MAN = 100      # 잔챙이 컷 (누적 만원)
HOT_CNT = 300            # 🔥 횟수 기준 (대표봇 반복, 압도적만)
STALL_MIN = 60           # 봇 마지막 체결이 N분+ 전이면 '멈춤' 후보
STABLE = {"USDT","USDC","DAI","TUSD","BUSD"}
SKIP = {"BTC","ETH","XRP","SOL","DOGE","ADA","TRX","LINK","AVAX","DOT","BCH","SUI","BNB"}

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
    if not token or not chat: return False
    try:
        url=f"https://api.telegram.org/bot{token}/sendMessage"
        data=urllib.parse.urlencode({'chat_id':chat,'text':text,'parse_mode':'HTML'}).encode()
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

def load_state():
    try: return json.load(open(STATE_PATH))
    except: return {}
def save_state(st):
    try: json.dump(st, open(STATE_PATH,'w'), ensure_ascii=False)
    except Exception as e: print(f"[state] {e}")

def is_bot_qty(v, cnt):
    frac=abs(v-round(v,2))
    return (frac>1e-6 and cnt>=MIN_CNT_DEC) or (frac<=1e-6 and cnt>=MIN_CNT_INT)

def analyze(fn):
    """종목별 '봇 조건 충족 수량 전체' 합산"""
    # sym -> {qty -> [cnt, amt, buy, sell, t1, t2]}
    raw=collections.defaultdict(lambda: collections.defaultdict(lambda:[0,0.0,0.0,0.0,None,None]))
    sym_px=collections.defaultdict(lambda:[1e18,0.0,None,None])  # 저,고,현재,시작
    try:
        with gzip.open(fn,'rt') as f:
            r=csv.reader(f); next(r,None)
            for x in r:
                if len(x)<7: continue
                sym=x[1]
                if sym in STABLE or sym in SKIP: continue
                try:
                    v=round(float(x[3]),4); amt=float(x[4]); px=float(x[2]); t=x[0][11:16]
                except: continue
                if v<=0 or px<=0: continue
                d=raw[sym][v]
                d[0]+=1; d[1]+=amt
                if x[5]=='BID': d[2]+=amt
                else: d[3]+=amt
                if d[4] is None: d[4]=t
                d[5]=t
                p=sym_px[sym]
                if px<p[0]: p[0]=px
                if px>p[1]: p[1]=px
                if p[3] is None: p[3]=px   # 시작가(첫틱)
                p[2]=px                     # 현재가(마지막)
    except FileNotFoundError:
        return []
    out=[]
    for sym, qtys in raw.items():
        bots=[(v,d) for v,d in qtys.items() if is_bot_qty(v,d[0])]
        if not bots: continue
        # 합산
        tot_cnt=sum(d[0] for _,d in bots)
        tot_amt=sum(d[1] for _,d in bots)
        tot_buy=sum(d[2] for _,d in bots)
        tot_sell=sum(d[3] for _,d in bots)
        accum_man=tot_amt/10000
        if accum_man < MIN_ACCUM_MAN: continue
        # 대표봇 = 누적금액 최대
        bots.sort(key=lambda x:-x[1][1])
        rep_v, rep_d = bots[0]
        n_other = len(bots)-1
        # 시간: 전체 봇의 첫~끝
        t1=min(d[4] for _,d in bots if d[4]); t2=max(d[5] for _,d in bots if d[5])
        # 건당 (대표봇 기준)
        per = rep_d[1]/rep_d[0]/10000 if rep_d[0]>0 else 0
        p=sym_px[sym]
        rng=(p[1]/p[0]-1)*100 if p[0]>0 else 0
        dir_chg=(p[2]/p[3]-1)*100 if p[3] and p[3]>0 else 0   # 시작→현재 방향
        net=(tot_buy-tot_sell)/10000
        if rng>5: continue   # ★박제만 후보 (변동 5% 초과 제외)
        out.append(dict(sym=sym, rep_qty=rep_v, rep_cnt=rep_d[0], n_other=n_other,
                        tot_cnt=tot_cnt, accum=accum_man, per=per, net=net,
                        rng=rng, dir_chg=dir_chg, t1=t1, t2=t2, last=t2))
    out.sort(key=lambda x:-x['accum'])
    return out

def dur_str(t1,t2):
    try:
        a=datetime.strptime(t1,"%H:%M"); b=datetime.strptime(t2,"%H:%M")
        m=int((b-a).total_seconds()/60)
        if m<0: m+=1440
        return f"{m//60}h{m%60:02d}m"
    except: return ""

def fmt_man(man):
    sign='+' if man>=0 else '-'; a=abs(man)
    if a>=10000: return f"{sign}{a/10000:.1f}억"
    return f"{sign}{a:,.0f}만"

def fmt_block(d, rank, prev, hot_cnt=False, hot_amt=False):
    """한 종목 본문 (합의 형식, 깔끔 정리)"""
    dc=d.get('dir_chg',0)
    if dc>=0.5: dir_e=f"📈 +{dc:.1f}%"
    elif dc<=-0.5: dir_e=f"📉 {dc:.1f}%"
    else: dir_e=f"➡️ {dc:+.1f}%"
    head=f"{dir_e} · 변동적음 {d['rng']:.0f}%"
    cnt_hot=" 🔥" if hot_cnt else ""   # 누적 횟수 순위 1,2위
    amt_hot=" 🔥" if hot_amt else ""   # 누적 금액 순위 1,2위
    side_emoji="🟢" if d['net']>=0 else "🔴"
    side_word="적극매집" if d['net']>=0 else "흡수매집"
    other=f" 외 {d['n_other']}종" if d['n_other']>0 else ""
    dur=dur_str(d['t1'],d['t2'])
    rnk=f"{rank}. " if rank is not None else ""
    lines=[f"{rnk}▶️ <b>{kn(d['sym'])}</b>",
           "",
           f"     {head}",
           f"     • 봇 {d['rep_qty']:g}개 ×{d['rep_cnt']}회{other}{cnt_hot}",
           f"     • 누적 {d['accum']:,.0f}만{amt_hot}  ·  건당 {d['per']:.1f}만",
           f"     • {side_emoji} 순 {fmt_man(d['net'])}  ({side_word})",
           f"     • 🕐 {d['t1']}~{d['t2']}  ({dur})"]
    if prev is not None:
        diff=d['accum']-prev
        arrow="🔺" if diff>0 else ("🔻" if diff<0 else "➖")
        lines.append(f"     • ⏮ 전시간 {fmt_man(diff)} {arrow}")
    else:
        lines.append(f"     • ⏮ 전시간 — (첫 집계)")
    return "\n".join(lines)

def main():
    mode = sys.argv[1] if len(sys.argv)>1 else "hourly"  # hourly / day
    env=load_env(); token=env.get('TELEGRAM_BOT_TOKEN'); chat=env.get('TELEGRAM_CHAT_ID_MONITOR')
    load_korean_names()
    today=datetime.now().strftime("%Y%m%d")
    state=load_state()
    prev_accum=state.get('accum',{})    # {exch|sym: 누적만원} 전 회차
    prev_seen=state.get('seen',{})      # {exch|sym: 마지막시각} 전 회차
    now_hm=datetime.now().strftime("%H:%M")
    BAR="──────────────────"

    new_accum={}; new_seen={}
    stall_msgs=[]

    for exch, fn in [("업비트",f"tick_{today}.csv.gz"),("빗썸",f"tick_bithumb_{today}.csv.gz")]:
        rows=analyze(os.path.join(LOG_DIR,fn))
        flag="🇰🇷" if exch=="업비트" else "🟡"
        title = "봇 누적 상위" if mode=="hourly" else "📅 24시간 마감 정리"
        # 🔥 = 표시 대상(상위 TOP_N) 중 누적횟수 1,2위 / 누적금액 1,2위
        shown=rows[:TOP_N]
        by_cnt=sorted(shown, key=lambda x:-x['tot_cnt'])[:2]
        by_amt=sorted(shown, key=lambda x:-x['accum'])[:2]
        hot_cnt_syms={x['sym'] for x in by_cnt}
        hot_amt_syms={x['sym'] for x in by_amt}
        lines=[f"{flag} <b>[{exch}] {title}</b>",
               f"      🕐 {now_hm} 기준",
               BAR, ""]
        for i,d in enumerate(shown,1):
            key=f"{exch}|{d['sym']}"
            prev=prev_accum.get(key)
            lines.append(fmt_block(d, i, prev, d['sym'] in hot_cnt_syms, d['sym'] in hot_amt_syms))
            lines.append("")
            lines.append(BAR)
            lines.append("")
            new_accum[key]=d['accum']
            new_seen[key]=d['last']
        if not rows:
            lines.append("(누적 기준 충족 종목 없음)")
        while lines and lines[-1] in ("", BAR): lines.pop()
        msg="\n".join(lines)
        send_telegram(token,chat,msg)
        print(msg, "\n")

        # 봇 멈춤 감지 — 전 회차엔 있었는데 이번 last가 STALL_MIN+ 전이면
        for d in rows:
            new_accum.setdefault(f"{exch}|{d['sym']}", d['accum'])
            new_seen.setdefault(f"{exch}|{d['sym']}", d['last'])
        # 전 회차 활성 종목 중, 이번에 마지막 체결이 오래된 것
        for key, plast in prev_seen.items():
            if not key.startswith(exch+"|"): continue
            # 이번 회차 그 종목 찾기
            d=next((x for x in rows if f"{exch}|{x['sym']}"==key), None)
            if d is None: continue
            try:
                last_t=datetime.strptime(d['last'],"%H:%M")
                now_t=datetime.strptime(now_hm,"%H:%M")
                gap=(now_t-last_t).total_seconds()/60
                if gap<0: gap+=1440
            except: continue
            if gap>=STALL_MIN and plast!=d['last']:  # 멈췄고 전과 달라짐(새로 멈춤)
                flag="🇰🇷" if exch=="업비트" else "🟡"
                body=fmt_block(d, None, prev_accum.get(key))
                msg=("🚨🚨 <b>매집봇 멈춤</b> 🚨🚨\n"
                     f"{flag} <b>[{exch}]</b> · 표류 후보\n"
                     f"{BAR}\n\n"
                     f"{body}\n\n"
                     f"{BAR}\n"
                     f"📍 봇 {gap:.0f}분+ 안 돎 → 발사 신호일 수 있음")
                stall_msgs.append(msg)

    if mode=="hourly":
        for m in stall_msgs:
            send_telegram(token,chat,m); print(m,"\n")
        save_state({'accum':new_accum, 'seen':new_seen, 'ts':now_hm})
        print(f"[발송] hourly 상위+멈춤{len(stall_msgs)}건")
    else:
        print(f"[발송] day 마감 정리")

if __name__=="__main__":
    main()
