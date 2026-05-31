#!/usr/bin/env python3
# woos_wall_detector.py  (v3 — 수량고정 봇 탐지 + 매집포착/매집종료 2단 알람)
#
# ★ 봇 식별 기준 변경: '한 방향'이 아니라 '수량 고정'.
#   세력 봇은 정확히 N개씩(소수점까지 동일) 반복 주문 → 수량 최빈값이 압도적이면 봇.
#   방향은 라벨로만: 매수우위=매집 / 매도우위=분배 / 섞임=중립(양방향).
#   (ID 사례: 양방향 봇이었음 → 한방향 기준이면 놓침)
#
# 알람 2종 (모니터방):
#  [1] 매집 포착: 수량고정 봇이 새로 감지 (이전에 없던 봇)
#  [2] 매집 종료: 진행중이던 봇이 멈춤(이번 회차에 안 보임) + 거래 급감
#                → ID 패턴(매집끝→표류→1h후 신호)상 '신호 선행' 후보
#
# state(wall_state.json): 직전 회차에 활성이던 봇을 기록 → 이번에 사라졌으면 '종료' 판정.

import gzip, csv, collections, os, sys, json, urllib.request, urllib.parse
from datetime import datetime, timedelta

LOG_DIR = os.path.expanduser("~/woos_logs")
ENV_PATH = os.path.expanduser("~/woos-alpha-bot/.env")
STATE_PATH = os.path.join(LOG_DIR, "wall_state.json")

# ── 수량고정 봇 판정 ──
QTY_REP_MIN   = 4       # 소수점까지 동일수량이면 4회로도 봇 확정(우연 불가)      # 최빈 수량 최소 반복 횟수
QTY_RATIO_MIN = 0.10    # 집중도 10%+ (잡스런 저집중 제거)
MIN_AVG_AMT   = 40000   # 봇 평균 체결액 4만원+ (KAT 49000 통과, 6천원 잔챙이 제거)
GAP_MAX       = 30      # 봇 체결 중앙 간격 30초 이내 (연속성)
MIN_TICKS     = 15
DIR_LABEL_TH  = 0.70    # 70%+ 한쪽이면 매집/분배, 아니면 중립
# ── 분석 윈도우 ──
WINDOW_MIN    = 15      # 최근 15분 (5분마다 실행, 방금 봇 포착/종료)
# ── 매집종료 판정 ──
DEAD_DROP     = 0.3     # 직전 활성봇 종목의 최근 체결수가 이전 대비 30%↓면 거래급감
STABLE = {'USDT','USDC','DAI','TUSD','BUSD'}
SKIP_MAJOR = {'BTC','ETH','XRP','SOL','DOGE','ADA','TRX','LINK','AVAX','DOT','BCH','SUI'}

def load_env():
    env = {}
    try:
        for line in open(ENV_PATH):
            line=line.strip()
            if '=' in line and not line.startswith('#'):
                k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
    except: pass
    return env

def load_korean_names():
    """업비트 KRW 종목 한글명 매핑 {티커: 한글명}. 실패시 빈 dict."""
    try:
        url="https://api.upbit.com/v1/market/all?isDetails=false"
        with urllib.request.urlopen(url, timeout=10) as r:
            data=json.loads(r.read().decode())
        m={}
        for x in data:
            mk=x.get("market","")
            if mk.startswith("KRW-"):
                m[mk[4:]]=x.get("korean_name","")
        return m
    except Exception as e:
        print(f"[krname] 실패: {e}")
        return {}

KOR_NAMES = {}  # main에서 채움

def kname(sym):
    kn=KOR_NAMES.get(sym)
    return f"{kn}({sym})" if kn else sym

def send_telegram(token, chat_id, text):
    if not token or not chat_id: return False
    try:
        url=f"https://api.telegram.org/bot{token}/sendMessage"
        data=urllib.parse.urlencode({'chat_id':chat_id,'text':text,'parse_mode':'HTML'}).encode()
        with urllib.request.urlopen(urllib.request.Request(url,data=data),timeout=10) as r:
            return r.status==200
    except Exception as e:
        print(f"[tg] 실패: {e}"); return False

def load_state():
    try: return json.load(open(STATE_PATH))
    except: return {}
def save_state(st):
    try: json.dump(st, open(STATE_PATH,'w'), ensure_ascii=False)
    except Exception as e: print(f"[state] {e}")

def load_recent_ticks(path, window_min):
    """최근 window_min 분 틱만 로드 (8컬럼: 시각,종목,가,량,액,매수매도,ID,활성)"""
    rows=collections.defaultdict(list)
    cutoff=None
    all_lines=[]
    with gzip.open(path,'rt') as f:
        r=csv.reader(f); next(r,None)
        for x in r:
            if len(x)<7: continue
            all_lines.append(x)
    if not all_lines: return rows, None
    # 마지막 시각 기준 window
    try:
        last_t=datetime.strptime(all_lines[-1][0][:19], "%Y-%m-%d %H:%M:%S")
    except:
        last_t=datetime.now()
    cutoff=last_t - timedelta(minutes=window_min)
    for x in all_lines:
        try:
            t=datetime.strptime(x[0][:19], "%Y-%m-%d %H:%M:%S")
            if t<cutoff: continue
            rows[x[1]].append((t, float(x[2]), float(x[3]), float(x[4]), x[5]))
        except: pass
    return rows, last_t

def detect_qty_bot(d):
    """수량 고정 봇 판정. 반환 dict or None"""
    n=len(d)
    if n<MIN_TICKS: return None
    # 수량을 문자열 그대로 묶기 (소수점까지 동일해야 봇)
    qtys=collections.Counter(round(x[2],4) for x in d if x[2]>0)
    if not qtys: return None
    # 봇 = 긴 소수점 수량(금액/비율 기준 주문이라 어중간하게 떨어짐).
    # 사람 = 딱 떨어지는 수량(5,100,135개 등). 소수 셋째자리 이상 있어야 봇 인정.
    def is_bot_qty(q):
        # 소수점 3자리 이상 유효숫자가 있으면 봇 (예: 297.0297 O, 135.0 X, 41.675 O)
        frac = abs(q - round(q,2))  # 소수 3자리 이하 값
        return frac > 1e-6  # 소수 3자리 이상이 살아있으면 봇
    ranked=qtys.most_common()
    top_qty, top_cnt = None, 0
    for q,c in ranked:
        if c<QTY_REP_MIN: break
        if is_bot_qty(q):
            top_qty, top_cnt = q, c; break
    if top_qty is None: return None
    if top_cnt<QTY_REP_MIN or top_cnt/n<QTY_RATIO_MIN: return None
    same=[x for x in d if round(x[2],4)==top_qty]
    # 시간 연속성
    times=sorted(x[0] for x in same)
    gaps=sorted((times[i+1]-times[i]).total_seconds() for i in range(len(times)-1))
    if not gaps: return None
    med_gap=gaps[len(gaps)//2]
    if med_gap>GAP_MAX: return None
    # 방향
    bid=sum(1 for x in same if x[4]=='BID'); ask=len(same)-bid
    if bid/len(same)>=DIR_LABEL_TH: label,dirpct='매집',bid/len(same)
    elif ask/len(same)>=DIR_LABEL_TH: label,dirpct='분배',ask/len(same)
    else: label,dirpct='중립',max(bid,ask)/len(same)
    avg_amt=sum(x[3] for x in same)/len(same)
    if avg_amt < MIN_AVG_AMT: return None   # 평균 체결액 작으면 잔챙이 봇 → 제외
    net_buy=sum(x[3] for x in d if x[4]=='BID')-sum(x[3] for x in d if x[4]=='ASK')
    px=[x[1] for x in same]; px_chg=(px[-1]/px[0]-1)*100 if px[0]>0 else 0
    return dict(qty=top_qty, cnt=top_cnt, ratio=top_cnt/n, label=label, dirpct=dirpct,
                avg_amt=avg_amt, med_gap=med_gap, n_recent=n, px_chg=px_chg, net_buy=net_buy,
                last_seen=times[-1].strftime("%H:%M"))

def main():
    global KOR_NAMES
    KOR_NAMES=load_korean_names()
    env=load_env()
    token=env.get('TELEGRAM_BOT_TOKEN'); chat=env.get('TELEGRAM_CHAT_ID_MONITOR')
    state=load_state()
    prev_bots=state.get('active_bots',{})  # {sym: {qty,cnt,...}} 직전 회차 활성봇
    notified=set(state.get('notified_today',[]))  # 오늘 이미 포착알림 보낸 종목
    today=datetime.now().strftime('%Y%m%d')
    if state.get('notified_date')!=today: notified=set()  # 날짜 바뀌면 초기화

    # 업비트 + 빗썸 단독, 둘 다 (오늘 파일)
    today_str=datetime.now().strftime("%Y%m%d")
    files=[]
    upf=os.path.join(LOG_DIR,f"tick_{today_str}.csv.gz")
    btf=os.path.join(LOG_DIR,f"tick_bithumb_{today_str}.csv.gz")
    if os.path.exists(upf): files.append(("업비트",upf))
    if os.path.exists(btf): files.append(("빗썸",btf))

    cur_bots={}      # 이번 회차 활성봇 {거래소|종목: res}
    recent_count={}  # {거래소|종목: 체결수}
    for exch,path in files:
        if not os.path.exists(path): continue
        rows,last_t=load_recent_ticks(path, WINDOW_MIN)
        for sym,d in rows.items():
            if sym in STABLE or sym in SKIP_MAJOR: continue
            key=f"{exch}|{sym}"
            recent_count[key]=len(d)
            res=detect_qty_bot(d)
            if res:
                res['exch']=exch; res['sym']=sym
                cur_bots[key]=res

    msgs=[]
    # [알람1] 매집 포착 — 이번에 새로 생긴 봇 (이전에 없던 종목)
    for key,b in cur_bots.items():
        exch=b.get('exch','업비트'); sym=b.get('sym',key.split('|')[-1])
        nkey=f"{exch}|{sym}|{b['label']}"  # 거래소+종목+방향
        if nkey not in notified:
            notified.add(nkey)
            emoji='🟢' if b['label']=='매집' else ('🔴' if b['label']=='분배' else '⚪')
            msg=(f"{emoji} <b>[{exch}] {b['label']} 봇 포착</b>\n"
                 f"종목: <b>{kname(sym)}</b>\n"
                 f"수량고정: {b['qty']:g}개 × {b['cnt']}회 ({b['ratio']*100:.0f}%)\n"
                 f"방향: {b['label']} ({b['dirpct']*100:.0f}%) / 평균 {b['avg_amt']:,.0f}원\n"
                 f"간격: {b['med_gap']:.0f}초 / 최근 {b['last_seen']}")
            msgs.append(('포착',sym,msg,b))

    # [알람2] 매집 종료 — 직전엔 있었는데 이번에 사라진 봇 + 거래급감
    for key,pb in prev_bots.items():
        if key not in cur_bots:
            exch=pb.get('exch','업비트'); sym=pb.get('sym',key.split('|')[-1])
            prev_n=pb.get('n_recent',0); now_n=recent_count.get(key,0)
            dead = (prev_n>0 and now_n < prev_n*DEAD_DROP)
            if pb.get('label')=='매집' and dead:
                msg=(f"⚠️ <b>[{exch}] 매집 종료 — 표류 시작</b>\n"
                     f"종목: <b>{kname(sym)}</b>\n"
                     f"매집봇 멈춤 (직전 {pb['qty']:g}개×{pb['cnt']}회 → 사라짐)\n"
                     f"거래 급감: {prev_n} → {now_n}건 ({now_n/max(1,prev_n)*100:.0f}%)\n"
                     f"★ID패턴상 신호 선행 자리 (매집끝→표류→신호)")
                msgs.append(('종료',sym,msg,pb))

    # 발송 + state 갱신
    # pattern.csv 기록 (요약이 읽음) — 알람과 별개로 '잡힌 봇 전체' + '종료' 다 기록
    pat_path=os.path.join(LOG_DIR, f"pattern_{datetime.now().strftime('%Y%m%d')}.csv")
    write_hdr = not os.path.exists(pat_path)
    pf=open(pat_path,'a')
    if write_hdr:
        pf.write("시각,종목,종류,라벨,수량,횟수,비율,방향쏠림,평균금액,간격초,순매수만,거래소\n")
    now_hm=datetime.now().strftime("%H:%M")

    # (1) 이번 회차 잡힌 봇 전체 기록 (알람 여부 무관 — 요약용)
    for key,b in cur_bots.items():
        nb=b.get('net_buy',0); exch=b.get('exch','')
        pf.write("%s,%s,%s,%s,%g,%d,%.2f,%.2f,%.0f,%.0f,%.0f,%s\n" % (
            now_hm, b.get('sym',key), '활동', b.get('label',''), b.get('qty',0), b.get('cnt',0),
            b.get('ratio',0), b.get('dirpct',0), b.get('avg_amt',0), b.get('med_gap',0), nb/10000, exch))
    # (2) 알람(포착/종료) — dedup 적용된 것만, 텔레그램 발송 + 기록
    for kind,sym,msg,b in msgs:
        ok=send_telegram(token,chat,msg)
        nb=b.get('net_buy',0); exch=b.get('exch','')
        pf.write("%s,%s,%s,%s,%g,%d,%.2f,%.2f,%.0f,%.0f,%.0f,%s\n" % (
            now_hm, b.get('sym',sym), kind, b.get('label',''), b.get('qty',0), b.get('cnt',0),
            b.get('ratio',0), b.get('dirpct',0), b.get('avg_amt',0), b.get('med_gap',0), nb/10000, exch))
        print(f"[{kind}] {sym} {b.get('label','')} qty{b.get('qty')} 발송{'OK' if ok else 'X'}")
    pf.close()
    state['active_bots']=cur_bots
    state['notified_today']=list(notified)
    state['notified_date']=today
    state['last_run']=datetime.now().strftime("%Y-%m-%d %H:%M")
    save_state(state)

    print(f"\n수량고정봇 {len(cur_bots)}개 활성 / 알람 {len(msgs)}건 (chat={chat}, token={'O' if token else 'X'})")
    for key,b in sorted(cur_bots.items(), key=lambda x:-x[1]['ratio']):
        print(f"  [{b.get('exch','')}] {b.get('sym',key):8} {b['label']} {b['qty']:g}개×{b['cnt']}회 ({b['ratio']*100:.0f}%) {b['dirpct']*100:.0f}% 간격{b['med_gap']:.0f}s")

if __name__=="__main__":
    main()
