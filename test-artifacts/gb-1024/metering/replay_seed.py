"""复演 seed_metering 的完整数据流（无数据库）：核对关窗、估数窗、倒挂窗与挂账量级，
并算出 README 演示用的两条补录值与更正目标值。口径必须与 scripts/seed_demo.py 一致。"""
import sys,random,math
from datetime import datetime,timedelta
sys.path.insert(0,'/workspace/backend/src')
import generation_core as core

class R:
    def __init__(self,id,kwh,taken_at):self.id=id;self.reading_kwh=kwh;self.taken_at=taken_at

base=datetime(2026,11,20,18);starts=[base-timedelta(days=26-i) for i in range(27)]
blocks={'SL-A01':(1,150),'SL-A02':(2,120),'SL-B01':(3,100),'SL-C01':(4,80)}
plan={'MG-SOUTH':(1,['SL-A01','SL-A02']),'MG-WEST':(2,['SL-B01','SL-C01'])}
miss={'SL-A02':{'2026-11-13','2026-11-14','2026-11-18'},'SL-C01':{'2026-11-08'}}
dip={'MG-WEST':23}
for gcode,(gid,codes) in plan.items():
    rng=random.Random(20261120+gid);daily={}
    for c in codes:
        bid,cap=blocks[c];brng=random.Random(91000+bid)
        daily[bid]=[round(cap*(3.8+0.6*math.sin(2*math.pi*(starts[j+1].timetuple().tm_yday-80)/365)+brng.uniform(-0.35,0.35)),1) for j in range(26)]
    mdaily=[round(sum(daily[blocks[c][0]][j] for c in codes)*1.003+rng.uniform(-1.5,1.5),1) for j in range(26)]
    readings={};rid=1
    for c in codes:
        bid,_=blocks[c];cum=5000.0+bid*137.0;rows=[]
        for i,t in enumerate(starts):
            if i>0:cum=round(cum+daily[bid][i-1],1)
            if c in miss and t.date().isoformat() in miss[c]:continue
            rows.append(R(rid,cum,t));rid+=1
        readings[bid]=rows
    mcum=20000.0+gid*733.0;mrows=[]
    for i,t in enumerate(starts):
        if i>0:mcum=round(mcum+mdaily[i-1],1)
        mrows.append(R(rid,mcum,t));rid+=1
    if gcode in dip:mrows[dip[gcode]].reading_kwh=round(mrows[dip[gcode]-1].reading_kwh-45.0,1)
    print(f'== {gcode} ==')
    est_windows=[];susp=[]
    for j in range(26):
        master=round(mrows[j+1].reading_kwh-mrows[j].reading_kwh,3)
        actuals={}
        for c in codes:
            bid,_=blocks[c]
            kwh=core.array_actual(readings[bid],starts[j],starts[j+1])
            if kwh is not None:actuals[bid]=kwh
        missing=[blocks[c][0] for c in codes if blocks[c][0] not in actuals]
        preds={b:1.0 for b in missing}  # 纯逻辑复演：均分即可核对账平
        entries,inv=core.plan_allocation(master,actuals,preds)
        kinds=[k for k,_,_ in entries]
        if inv:print(f'  窗{j} {starts[j]:%m-%d}→{starts[j+1]:%m-%d} 倒挂撤账 master={master}')
        if 'estimate' in kinds:est_windows.append(j)
        s=[v for k,_,v in entries if k=='suspense']
        if s:susp.append((j,round(s[0],3)))
    print('  估数窗:',est_windows)
    print('  挂账窗:',susp)
    if gcode=='MG-WEST':
        print('  11-17错抄表底 =',mrows[23].reading_kwh,'→ 应更正为',round(mrows[22].reading_kwh+mdaily[22],1))
